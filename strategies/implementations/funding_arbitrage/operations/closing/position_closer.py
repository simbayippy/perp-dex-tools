"""Main orchestrator for closing funding arbitrage positions."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from exchange_clients.events import LiquidationEvent
from exchange_clients.base_models import ExchangePositionSnapshot

from ..core.contract_preparer import ContractPreparer
from ..core.websocket_manager import WebSocketManager
from ..core.decimal_utils import to_decimal
from strategies.execution.core.spread_utils import SpreadCheckType, is_spread_acceptable
from ...risk_management import get_risk_manager
from .exit_evaluator import ExitEvaluator
from .pnl_calculator import PnLCalculator
from .close_executor import CloseExecutor
from .order_builder import OrderBuilder, WideSpreadException

if TYPE_CHECKING:
    from ...models import FundingArbPosition
    from ...strategy import FundingArbitrageStrategy


class PositionCloser:
    """Encapsulates exit-condition evaluation and close execution."""

    def __init__(self, strategy: "FundingArbitrageStrategy") -> None:
        self._strategy = strategy
        self._risk_manager = self._build_risk_manager()
        self._contract_preparer = ContractPreparer()
        self._ws_manager = WebSocketManager()
        self._exit_evaluator = ExitEvaluator(strategy, self._risk_manager)
        self._pnl_calculator = PnLCalculator(strategy)
        self._close_executor = CloseExecutor(strategy)
        self._order_builder = OrderBuilder(strategy)
        self._current_close_order_type: Optional[str] = None
        # Track positions currently being closed to prevent concurrent close operations
        self._positions_closing: set = set()

    # =========================================================================
    # 1. MAIN ORCHESTRATION
    # =========================================================================

    async def evaluateAndClosePositions(self) -> List[str]:
        """Evaluate all open positions and close those that meet exit conditions."""
        strategy = self._strategy
        actions: List[str] = []
        positions = await strategy.position_manager.get_open_positions()

        for position in positions:
            # Skip positions that are already being closed (prevents race conditions)
            if position.id in self._positions_closing:
                continue

            snapshots = await self._fetch_leg_snapshots(position)

            # --- A. CRITICAL CHECKS (Immediate Exit) ---
            # Liquidation risk takes priority over everything. No polling allowed.
            critical_reason = (
                self._exit_evaluator.check_liquidation_risk(position, snapshots) or
                self._exit_evaluator.detect_liquidation(position, snapshots) or
                self._exit_evaluator.detect_imbalance(position, snapshots)
            )

            if critical_reason:
                await self._cancel_exit_polling(position) # Stop polling if critical
                await self.close(position, critical_reason, live_snapshots=snapshots, order_type="limit")
                actions.append(f"Closed {position.symbol}: {critical_reason}")
                continue

            # --- B. PROFIT TAKING (Opportunistic) ---
            # Check for immediate cross-exchange basis opportunities
            profit_taker = getattr(strategy, 'profit_taker', None)
            if profit_taker:
                was_closed = await profit_taker.evaluate_and_execute(
                    position, snapshots, trigger_source="polling"
                )
                if was_closed:
                    actions.append(f"Closed {position.symbol} for profit")
                    continue

            # --- C. EXIT POLLING & STANDARD EVALUATION ---
            polling_state = self._get_polling_state(position)

            if polling_state:
                # C1. Position is ALREADY in polling state -> Process logic
                await self._handle_active_polling(position, polling_state, snapshots, actions)
            else:
                # C2. Position is normal -> Check if we should start exit sequence
                await self._evaluate_standard_exit(position, snapshots, actions)

        return actions

    # =========================================================================
    # 2. POLLING LOGIC (The "Brain")
    # =========================================================================

    async def _evaluate_standard_exit(
        self, 
        position: "FundingArbPosition", 
        snapshots: Dict, 
        actions: List[str]
    ) -> None:
        """Decides if a position should close immediately, start polling, or stay open."""
        enable_polling = getattr(self._strategy.config, "enable_exit_polling", True)

        should_close, reason = await self._exit_evaluator.should_close(
            position,
            snapshots,
            gather_current_rates=self._gather_current_rates,
            should_skip_erosion_exit=self._should_skip_erosion_exit,
        )

        if not should_close:
            return

        # Exit condition met. Can we get out at break-even right now?
        can_exit_now = self._exit_evaluator.can_exit_at_break_even(position, snapshots)

        if can_exit_now:
            # Optimal case: Exit immediately
            await self.close(position, reason or "UNKNOWN", live_snapshots=snapshots, order_type="limit")
            actions.append(f"Closed {position.symbol}: {reason} (Break-even hit)")
            return

        # Break-even not possible yet.
        if enable_polling:
            # Check spread before entering polling to avoid noise
            spread_acceptable = await self._check_spread_before_polling(position, snapshots)
            if not spread_acceptable:
                self._strategy.logger.debug(f"Deferring exit polling for {position.symbol} due to wide spread")
                return

            # Start Polling (Wait for better prices)
            await self._initiate_exit_polling(position, reason or "UNKNOWN")
        else:
            # Polling disabled, exit immediately despite loss
            await self.close(position, reason or "UNKNOWN", live_snapshots=snapshots)
            actions.append(f"Closed {position.symbol}: {reason}")

    async def _handle_active_polling(
        self,
        position: "FundingArbPosition",
        polling_state: Dict[str, Any],
        snapshots: Dict,
        actions: List[str],
    ) -> None:
        """Processes a position that is currently in the waiting/polling loop."""
        strategy = self._strategy
        
        # 1. Update Heartbeat
        polling_state["last_check_at"] = datetime.now(timezone.utc).isoformat()
        position.metadata["exit_polling_state"] = polling_state

        # 2. Check Timeout
        if self._is_polling_timeout_expired(polling_state):
            reason = polling_state.get("exit_reason", "TIMEOUT")
            strategy.logger.warning(f"⏰ Polling timeout for {position.symbol}. Forcing Exit.")
            await self._cancel_exit_polling(position)
            await self.close(position, f"{reason}_TIMEOUT", live_snapshots=snapshots, order_type="limit")
            actions.append(f"Closed {position.symbol}: Timeout")
            return

        # 3. Re-evaluate Conditions (Did funding rates improve?)
        should_still_close, current_reason = await self._exit_evaluator.should_close(
            position,
            snapshots,
            gather_current_rates=self._gather_current_rates,
            should_skip_erosion_exit=self._should_skip_erosion_exit,
        )

        if not should_still_close:
            strategy.logger.info(f"✅ Conditions improved for {position.symbol}. Cancelling polling.")
            await self._cancel_exit_polling(position)
            return

        # 4. Check Break-Even (The goal of polling)
        if self._exit_evaluator.can_exit_at_break_even(position, snapshots):
            # Check spread one last time before pulling the trigger
            if await self._check_spread_before_exit(position, snapshots):
                reason = polling_state.get("exit_reason", "UNKNOWN")
                strategy.logger.info(f"✅ Break-even reached for {position.symbol}. Exiting.")
                await self._cancel_exit_polling(position)
                await self.close(position, reason, live_snapshots=snapshots, order_type="limit")
                actions.append(f"Closed {position.symbol}: {reason} (Break-even)")
                return
            else:
                strategy.logger.debug(f"Break-even reached for {position.symbol} but spread too wide.")
        else:
            strategy.logger.debug(
                f"⏳ {position.symbol} waiting for break-even (Reason: {polling_state.get('exit_reason')})"
            )

        # 5. Persist Heartbeat
        # We update the position to save the "last_check_at" timestamp
        await strategy.position_manager.update(position)

    # =========================================================================
    # 3. EXECUTION & PNL (The "Hands")
    # =========================================================================

    async def handle_liquidation_event(self, event: LiquidationEvent) -> None:
        """React to liquidation notifications by immediately unwinding impacted positions."""
        strategy = self._strategy
        positions = await strategy.position_manager.get_open_positions()

        for position in positions:
            if not self._symbols_match(position.symbol, event.symbol):
                continue

            if event.exchange not in {position.long_dex, position.short_dex}:
                continue

            strategy.logger.error(
                f"🚨 Liquidation event detected on {event.exchange.upper()} for {event.symbol} "
                f"(side={event.side}, qty={event.quantity}, price={event.price})."
            )

            snapshots = await self._fetch_leg_snapshots(position)
            reason = f"LIQUIDATION_{event.exchange.upper()}"
            await self.close(position, reason, live_snapshots=snapshots)

    async def close(
        self,
        position: "FundingArbPosition",
        reason: str,
        *,
        live_snapshots: Optional[Dict[str, Optional["ExchangePositionSnapshot"]]] = None,
        order_type: Optional[str] = None,
    ) -> None:
        """Close a position and calculate PnL."""
        strategy = self._strategy
        self._current_close_order_type = order_type

        # Handle Manual Closes (Cancel polling if it exists)
        is_manual_close = reason.lower().startswith(("telegram_", "manual_"))
        if is_manual_close:
            polling_state = self._get_polling_state(position)
            if polling_state:
                strategy.logger.info(f"🛑 Manual close for {position.symbol}. Cancelling polling.")
                await self._cancel_exit_polling(position)

        # Prevent concurrent closes
        if position.id in self._positions_closing:
            strategy.logger.warning(f"Position {position.symbol} is already closing. Skipping.")
            return

        self._positions_closing.add(position.id)

        try:
            # Unregister profit listeners
            profit_taker = getattr(strategy, 'profit_taker', None)
            if profit_taker:
                try:
                    await profit_taker.unregister_position(position)
                except Exception:
                    pass

            pre_close_snapshots = live_snapshots or await self._fetch_leg_snapshots(position)
            
            # --- CALCULATE PRE-CLOSE METRICS ---
            total_unrealized_pnl = Decimal("0")
            total_funding_accrued = Decimal("0")
            missing_snapshot_data = False

            for dex in [position.long_dex, position.short_dex]:
                snapshot = pre_close_snapshots.get(dex) or pre_close_snapshots.get(dex.lower())
                if not snapshot:
                    missing_snapshot_data = True
                    continue
                
                if snapshot.unrealized_pnl is not None:
                    total_unrealized_pnl += to_decimal(snapshot.unrealized_pnl)
                else:
                    missing_snapshot_data = True
                
                if snapshot.funding_accrued is not None:
                    total_funding_accrued += to_decimal(snapshot.funding_accrued)

            # --- EXECUTE CLOSE ---
            try:
                await self._close_executor.close_exchange_positions(
                    position,
                    reason=reason,
                    live_snapshots=pre_close_snapshots,
                    order_type=order_type,
                    order_builder=self._order_builder,
                )
            except WideSpreadException as exc:
                strategy.logger.info(f"⏸️ Deferring close for {position.symbol} due to wide spread ({exc.spread_pct*100:.2f}%)")
                return

            await asyncio.sleep(1.0) # Allow fills to propagate

            # --- PNL CALCULATION ---
            closing_fees = Decimal("0")
            close_result = position.metadata.get("close_execution_result")
            if close_result and close_result.get("filled_orders"):
                closing_fees = self._pnl_calculator.calculate_closing_fees(
                    close_result, order_type=self._current_close_order_type
                )

            pnl = None
            pnl_method = None

            # Method 1: Trade History (Most accurate)
            try:
                import time
                trade_history_result = await self._pnl_calculator.calculate_pnl_from_trade_history(
                    position,
                    close_result,
                    position.opened_at.timestamp(),
                    time.time(),
                    store_trades_fn=self._pnl_calculator.store_trades_in_database,
                )
                if trade_history_result:
                    pnl, pnl_method = trade_history_result
            except Exception as e:
                strategy.logger.debug(f"Trade history PnL failed: {e}")

            # Method 2: Snapshots (Fallback)
            if pnl is None and not missing_snapshot_data:
                funding_to_add = total_funding_accrued
                if funding_to_add == 0:
                    cumulative = await strategy.position_manager.get_cumulative_funding(position.id)
                    funding_to_add = to_decimal(cumulative)
                
                total_fees = to_decimal(position.total_fees_paid) + to_decimal(closing_fees)
                pnl = total_unrealized_pnl + funding_to_add - total_fees
                pnl_method = "snapshots"

            # Method 3: Websocket Fills (Fallback)
            if pnl is None and close_result and close_result.get("filled_orders"):
                cumulative_funding = await strategy.position_manager.get_cumulative_funding(position.id)
                position.cumulative_funding = cumulative_funding
                
                price_pnl = Decimal("0")
                legs_metadata = position.metadata.get("legs", {})
                
                for fill in close_result["filled_orders"]:
                    dex = fill["dex"]
                    fill_price = to_decimal(fill.get("fill_price", 0))
                    filled_qty = to_decimal(fill.get("filled_quantity", 0))
                    entry_price = to_decimal(legs_metadata.get(dex, {}).get("entry_price", 0))
                    side = legs_metadata.get(dex, {}).get("side", "long")
                    
                    if entry_price > 0 and filled_qty > 0:
                        if side == "long":
                            price_pnl += (fill_price - entry_price) * filled_qty
                        else:
                            price_pnl += (entry_price - fill_price) * filled_qty

                total_fees = to_decimal(position.total_fees_paid) + to_decimal(closing_fees)
                pnl = price_pnl + to_decimal(cumulative_funding) - total_fees
                pnl_method = "websocket_fills"

            # Method 4: Database Logic (Last Resort)
            if pnl is None:
                cumulative_funding = await strategy.position_manager.get_cumulative_funding(position.id)
                position.cumulative_funding = cumulative_funding
                total_fees = to_decimal(position.total_fees_paid) + to_decimal(closing_fees)
                pnl = position.get_net_pnl() - to_decimal(closing_fees)
                pnl_method = "cumulative_funding"

            # --- FINALIZE ---
            pnl_pct = (pnl / position.size_usd) if (position.size_usd and position.size_usd > 0) else Decimal("0")

            await strategy.position_manager.close(
                position.id, exit_reason=reason, pnl_usd=pnl
            )

            # Refresh position object to get updated state
            refreshed = await strategy.position_manager.get(position.id)
            if refreshed: position = refreshed

            strategy.logger.info(
                f"✅ Closed {position.symbol} ({reason}): PnL=${pnl:.2f} ({pnl_pct*100:.2f}%) via {pnl_method}"
            )

            # Send Notification
            try:
                # Basic leg pnl for notification only
                leg_pnl_est = {}
                for dex, snap in pre_close_snapshots.items():
                    if snap and snap.unrealized_pnl is not None:
                        leg_pnl_est[dex] = to_decimal(snap.unrealized_pnl)

                await strategy.notification_service.notify_position_closed(
                    symbol=position.symbol,
                    reason=reason,
                    pnl_usd=pnl,
                    pnl_pct=pnl_pct,
                    age_hours=position.get_age_hours(),
                    size_usd=position.size_usd,
                    leg_pnl=leg_pnl_est
                )
            except Exception:
                pass

        except Exception as exc:
            strategy.logger.error(f"Error closing position {position.id}: {exc}")
            raise
        finally:
            self._positions_closing.discard(position.id)

    # =========================================================================
    # 4. STATE MANAGEMENT (The "Memory")
    # =========================================================================

    def _get_polling_state(self, position: "FundingArbPosition") -> Optional[Dict[str, Any]]:
        """Retrieve polling state safely."""
        state = (position.metadata or {}).get("exit_polling_state")
        return state if isinstance(state, dict) else None

    async def _initiate_exit_polling(self, position: "FundingArbPosition", reason: str) -> None:
        """Start polling and PERSIST state to database immediately."""
        strategy = self._strategy
        max_duration = getattr(strategy.config, "exit_polling_max_duration_minutes", 5)
        
        polling_state = {
            "started_at": datetime.now(timezone.utc).isoformat(),
            "exit_reason": reason,
            "max_duration_minutes": max_duration,
            "last_check_at": datetime.now(timezone.utc).isoformat(),
        }
        
        if position.metadata is None:
            position.metadata = {}
        position.metadata["exit_polling_state"] = polling_state
        
        # KEY FIX: Save to DB so next iteration knows we are polling
        await strategy.position_manager.update(position)
        
        strategy.logger.info(
            f"⏳ Started exit polling for {position.symbol} (reason: {reason}, max: {max_duration}m)"
        )

    async def _cancel_exit_polling(self, position: "FundingArbPosition") -> None:
        """Clear polling state and PERSIST to database immediately."""
        if position.metadata and "exit_polling_state" in position.metadata:
            state = position.metadata.pop("exit_polling_state")
            reason = state.get("exit_reason", "UNKNOWN")
            
            # KEY FIX: Save to DB
            await self._strategy.position_manager.update(position)
            
            self._strategy.logger.info(f"✅ Cancelled exit polling for {position.symbol} (reason: {reason})")

    def _is_polling_timeout_expired(self, polling_state: Dict[str, Any]) -> bool:
        started_at_str = polling_state.get("started_at")
        max_duration = polling_state.get("max_duration_minutes", 10)
        
        if not started_at_str: return True
        try:
            started = datetime.fromisoformat(started_at_str.replace("Z", "+00:00"))
            if started.tzinfo is None: started = started.replace(tzinfo=timezone.utc)
            elapsed = (datetime.now(timezone.utc) - started).total_seconds() / 60
            return elapsed >= max_duration
        except Exception:
            return True

    # =========================================================================
    # 5. DATA FETCHING & RISK HELPERS
    # =========================================================================

    async def _fetch_leg_snapshots(self, position: "FundingArbPosition") -> Dict[str, Optional["ExchangePositionSnapshot"]]:
        """Fetch up-to-date exchange snapshots for both legs."""
        snapshots = {}
        legs_metadata = (position.metadata or {}).get("legs", {})

        for dex in filter(None, [position.long_dex, position.short_dex]):
            client = self._strategy.exchange_clients.get(dex)
            if not client:
                snapshots[dex] = None
                continue

            leg_meta = legs_metadata.get(dex, {})
            # Prep context/ws
            await self._contract_preparer.prepare_contract_context(
                client, position.symbol, metadata=leg_meta,
                contract_hint=leg_meta.get("market_id"), logger=self._strategy.logger
            )
            await self._ws_manager.ensure_market_feed_once(client, position.symbol, self._strategy.logger)

            try:
                snapshots[dex] = await client.get_position_snapshot(position.symbol)
            except Exception as exc:
                self._strategy.logger.error(f"[{dex}] Snapshot failed for {position.symbol}: {exc}")
                snapshots[dex] = None

        return snapshots

    async def _gather_current_rates(self, position: "FundingArbPosition") -> Optional[Dict[str, Decimal]]:
        """Fetch latest funding rates for both legs."""
        repo = getattr(self._strategy, "funding_rate_repo", None)
        if not repo: return None

        try:
            long_rate_row = await repo.get_latest_specific(position.long_dex, position.symbol)
            short_rate_row = await repo.get_latest_specific(position.short_dex, position.symbol)
        except Exception:
            return None

        def _get_val(row, key):
            val = row.get(key) if isinstance(row, dict) else getattr(row, key, None)
            return Decimal(str(val)) if val is not None else None

        l_rate = _get_val(long_rate_row, "funding_rate")
        s_rate = _get_val(short_rate_row, "funding_rate")

        if l_rate is None or s_rate is None: return None

        divergence = s_rate - l_rate
        position.current_divergence = divergence
        return {
            "divergence": divergence,
            "long_rate": l_rate,
            "short_rate": s_rate,
            "long_oi_usd": _get_val(long_rate_row, "open_interest_usd") or Decimal("0"),
            "short_oi_usd": _get_val(short_rate_row, "open_interest_usd") or Decimal("0"),
        }

    async def _check_spread_before_polling(self, position: "FundingArbPosition", snapshots: Dict) -> bool:
        return await self._check_spread_generic(position, snapshots, "polling")

    async def _check_spread_before_exit(self, position: "FundingArbPosition", snapshots: Dict) -> bool:
        return await self._check_spread_generic(position, snapshots, "exit")

    async def _check_spread_generic(self, position: "FundingArbPosition", snapshots: Dict, context: str) -> bool:
        strategy = self._strategy
        if not getattr(strategy.config, "enable_wide_spread_protection", True):
            return True

        price_provider = getattr(strategy, "price_provider", None)
        if not price_provider: return True

        for dex in [position.long_dex, position.short_dex]:
            client = strategy.exchange_clients.get(dex)
            if not client: continue
            
            try:
                bid, ask = await price_provider.get_bbo_prices(client, position.symbol)
                acceptable, spread_pct, _ = is_spread_acceptable(bid, ask, SpreadCheckType.EXIT)
                
                if not acceptable:
                    strategy.logger.info(
                        f"⏸️ Wide spread on {dex.upper()} {position.symbol}: {spread_pct*100:.2f}%. "
                        f"Deferring {context}."
                    )
                    return False
            except Exception:
                continue
        return True

    async def _should_skip_erosion_exit(self, position: "FundingArbPosition", trigger_reason: Optional[str]) -> bool:
        return await self._exit_evaluator.should_skip_erosion_exit(
            position, trigger_reason, is_opportunity_tradeable=self._is_opportunity_tradeable
        )

    async def _is_opportunity_tradeable(self, opportunity: "ArbitrageOpportunity") -> bool:
        """Validate that an opportunity is actually tradeable."""
        # Note: This logic seems to rely on OpportunityFinder static methods
        try:
            from funding_rate_service.core.opportunity_finder import OpportunityFinder
        except Exception:
            return True
            
        sym = opportunity.symbol
        if not OpportunityFinder.is_symbol_tradeable(opportunity.long_dex, sym): return False
        if not OpportunityFinder.is_symbol_tradeable(opportunity.short_dex, sym): return False
        
        # Volume/OI Checks
        min_vol = getattr(self._strategy.config, "min_volume_24h", None)
        if min_vol and (opportunity.min_volume_24h or 0) < min_vol: return False
        
        min_oi = getattr(self._strategy.config, "min_oi_usd", None)
        if min_oi and (opportunity.min_oi_usd or 0) < min_oi: return False

        return True

    def _build_risk_manager(self):
        strategy = self._strategy
        risk_cfg = strategy.config.risk_config
        try:
            payload = {
                "min_erosion_ratio": float(risk_cfg.min_erosion_threshold),
                "severe_erosion_ratio": float(getattr(risk_cfg, "severe_erosion_ratio", Decimal("0.2"))),
                "max_position_age_hours": risk_cfg.max_position_age_hours,
                "flip_margin": float(getattr(risk_cfg, "flip_margin", Decimal("0"))),
            }
            return get_risk_manager(risk_cfg.strategy, payload)
        except Exception:
            return None

    @staticmethod
    def _symbols_match(pos_symbol: Optional[str], event_symbol: Optional[str]) -> bool:
        if not pos_symbol or not event_symbol: return False
        p, e = pos_symbol.upper(), event_symbol.upper()
        return p == e or e.endswith(p) or e.startswith(p) or p.endswith(e) or p.startswith(e)