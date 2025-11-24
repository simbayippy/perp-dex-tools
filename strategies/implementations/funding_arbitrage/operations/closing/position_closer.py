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
    # 1. MAIN EVALUATION LOOP (Simplified)
    # =========================================================================

    async def evaluateAndClosePositions(self) -> List[str]:
        """Evaluate all open positions and close those that meet exit conditions."""
        strategy = self._strategy
        actions: List[str] = []
        positions = await strategy.position_manager.get_open_positions()

        for position in positions:
            # 1. Concurrency Check
            if position.id in self._positions_closing:
                continue

            snapshots = await self._fetch_leg_snapshots(position)

            # 2. CRITICAL CHECKS (Liquidation Risk)
            # Priority: HIGH. Action: Close IMMEDIATELY.
            # We do NOT check spread here. If we are liquidating, we must exit.
            critical_reason = (
                self._exit_evaluator.check_liquidation_risk(position, snapshots) or
                self._exit_evaluator.detect_liquidation(position, snapshots) or
                self._exit_evaluator.detect_imbalance(position, snapshots)
            )

            if critical_reason:
                # Use limit orders to try to avoid slippage, but execute immediately
                await self.close(position, critical_reason, live_snapshots=snapshots, order_type="limit")
                actions.append(f"Closed {position.symbol}: {critical_reason}")
                continue

            # 3. PROFIT TAKING (Optional - Keep if you want to take profits early)
            # Checks for cross-exchange basis spread opportunities
            profit_taker = getattr(strategy, 'profit_taker', None)
            if profit_taker:
                if await profit_taker.evaluate_and_execute(position, snapshots, trigger_source="loop"):
                    actions.append(f"Closed {position.symbol} for profit")
                    continue

            # 4. STANDARD STRATEGY CHECKS (Divergence, Funding Flip, Erosion)
            should_close, reason = await self._exit_evaluator.should_close(
                position,
                snapshots,
                gather_current_rates=self._gather_current_rates,
                should_skip_erosion_exit=self._should_skip_erosion_exit,
            )

            if should_close:
                # 5. SPREAD CHECK
                # Only close if the market is calm (tight spread).
                is_spread_safe = await self._check_spread_is_safe(position, snapshots)

                if is_spread_safe:
                    await self.close(position, reason or "UNKNOWN", live_snapshots=snapshots, order_type="limit")
                    actions.append(f"Closed {position.symbol}: {reason}")
                else:
                    # Spread is wide -> Log and Wait (do nothing, logic repeats next iteration)
                    strategy.logger.info(
                        f"⏳ Exit signal for {position.symbol} ({reason}), but spread is too wide. "
                        f"Waiting for next iteration."
                    )

        return actions

    # =========================================================================
    # 2. EXECUTION & PNL
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
                f"🚨 Liquidation event detected on {event.exchange.upper()} for {event.symbol}."
            )

            snapshots = await self._fetch_leg_snapshots(position)
            await self.close(position, f"LIQUIDATION_{event.exchange.upper()}", live_snapshots=snapshots)

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

        # Clean up polling metadata if it exists from old versions
        if position.metadata and "exit_polling_state" in position.metadata:
             position.metadata.pop("exit_polling_state")
             await strategy.position_manager.update(position)

        # Prevent concurrent closes
        if position.id in self._positions_closing:
            return
        self._positions_closing.add(position.id)

        try:
            # Unregister profit listeners if any
            profit_taker = getattr(strategy, 'profit_taker', None)
            if profit_taker:
                try:
                    await profit_taker.unregister_position(position)
                except Exception:
                    pass

            pre_close_snapshots = live_snapshots or await self._fetch_leg_snapshots(position)

            # --- PREPARE PNL DATA ---
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

            # --- EXECUTE ---
            try:
                await self._close_executor.close_exchange_positions(
                    position,
                    reason=reason,
                    live_snapshots=pre_close_snapshots,
                    order_type=order_type,
                    order_builder=self._order_builder,
                )
            except WideSpreadException as exc:
                # Double safety: if close_executor detects wide spread during execution
                strategy.logger.info(f"⏸️ Execution aborted due to wide spread: {exc.spread_pct*100:.2f}%")
                return

            await asyncio.sleep(1.0) 

            # --- CALCULATE FINAL PNL ---
            closing_fees = Decimal("0")
            close_result = position.metadata.get("close_execution_result")
            if close_result and close_result.get("filled_orders"):
                closing_fees = self._pnl_calculator.calculate_closing_fees(
                    close_result, order_type=self._current_close_order_type
                )

            pnl = None
            pnl_method = None

            # 1. Trade History Method
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
            except Exception:
                pass

            # 2. Snapshot Method
            if pnl is None and not missing_snapshot_data:
                funding_to_add = total_funding_accrued
                if funding_to_add == 0:
                    cumulative = await strategy.position_manager.get_cumulative_funding(position.id)
                    funding_to_add = to_decimal(cumulative)
                
                total_fees = to_decimal(position.total_fees_paid) + to_decimal(closing_fees)
                pnl = total_unrealized_pnl + funding_to_add - total_fees
                pnl_method = "snapshots"

            # 3. Websocket Fills Method
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
                        diff = (fill_price - entry_price) if side == "long" else (entry_price - fill_price)
                        price_pnl += diff * filled_qty

                total_fees = to_decimal(position.total_fees_paid) + to_decimal(closing_fees)
                pnl = price_pnl + to_decimal(cumulative_funding) - total_fees
                pnl_method = "websocket_fills"

            # 4. Fallback
            if pnl is None:
                pnl = position.get_net_pnl() - to_decimal(closing_fees)
                pnl_method = "fallback"

            # --- FINISH ---
            pnl_pct = (pnl / position.size_usd) if (position.size_usd and position.size_usd > 0) else Decimal("0")

            await strategy.position_manager.close(
                position.id, exit_reason=reason, pnl_usd=pnl
            )
            
            # Refresh to get updated object
            refreshed = await strategy.position_manager.get(position.id)
            if refreshed: position = refreshed

            strategy.logger.info(
                f"✅ Closed {position.symbol} ({reason}): PnL=${pnl:.2f} ({pnl_pct*100:.2f}%) via {pnl_method}"
            )

            # Remove position from live table
            try:
                live_table = getattr(strategy, 'live_table', None)
                if live_table:
                    await live_table.remove_position(position)
            except Exception as e:
                strategy.logger.debug(f"Failed to remove position from live table: {e}")

            try:
                # Create simple leg pnl dict for notification
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
    # 3. HELPERS
    # =========================================================================

    async def _check_spread_is_safe(self, position: "FundingArbPosition", snapshots: Dict) -> bool:
        """
        Check if the spread is acceptable for exiting.
        Returns True if safe to trade, False if spread is too wide.
        """
        strategy = self._strategy
        if not getattr(strategy.config, "enable_wide_spread_protection", True):
            return True

        price_provider = getattr(strategy, "price_provider", None)
        if not price_provider:
            return True

        for dex in [position.long_dex, position.short_dex]:
            client = strategy.exchange_clients.get(dex)
            if not client: continue
            
            try:
                bid, ask = await price_provider.get_bbo_prices(client, position.symbol)
                acceptable, spread_pct, _ = is_spread_acceptable(bid, ask, SpreadCheckType.EXIT)
                
                if not acceptable:
                    strategy.logger.info(
                        f"⚠️ Wide spread on {dex.upper()} {position.symbol}: {spread_pct*100:.2f}%. "
                        f"Close deferred."
                    )
                    return False
            except Exception:
                # If we can't check spread, we usually assume safe or log warning. 
                # Assuming safe to avoid stuck positions on API glitches, or return False to be safe.
                # Returning True allows retry mechanisms in close_executor to handle it.
                return True

        return True

    async def _verify_profit_opportunity_pre_execution(
        self,
        position: "FundingArbPosition",
        snapshots: Dict[str, Optional["ExchangePositionSnapshot"]],
    ) -> tuple[bool, Optional[str]]:
        """
        Verify profit opportunity with fresh BBO before execution.

        Re-checks profitability to prevent closing when opportunity disappeared
        between detection and execution. This is critical for profit-taking closes.

        Args:
            position: Position to verify
            snapshots: Current position snapshots

        Returns:
            (is_verified, reason) tuple
            - is_verified: True if still profitable, False if opportunity gone
            - reason: Description of verification result
        """
        strategy = self._strategy

        # Fetch fresh BBO prices for both legs
        bbo_prices = {}
        for dex in [position.long_dex, position.short_dex]:
            client = strategy.exchange_clients.get(dex)
            if not client:
                strategy.logger.warning(
                    f"Cannot verify profit for {position.symbol}: "
                    f"No client for {dex}"
                )
                return False, f"No client available for {dex}"

            if not strategy.price_provider:
                # No price provider - skip verification
                return True, "Price provider not available, skipping verification"

            try:
                bid, ask = await strategy.price_provider.get_bbo_prices(
                    client, position.symbol
                )
                bbo_prices[dex] = {"bid": bid, "ask": ask}
                strategy.logger.debug(
                    f"[{dex.upper()}] Fresh BBO for {position.symbol}: "
                    f"bid={bid}, ask={ask}"
                )
            except Exception as e:
                strategy.logger.warning(
                    f"Failed to fetch fresh BBO for {dex} {position.symbol}: {e}"
                )
                return False, f"BBO fetch failed for {dex}: {str(e)}"

        # Re-check profitability with fresh BBO prices
        profit_taker = getattr(strategy, 'profit_taker', None)
        if not profit_taker:
            return True, "Profit taker not available, skipping verification"

        evaluator = getattr(profit_taker, '_profit_evaluator', None)
        if not evaluator:
            return True, "Profit evaluator not available, skipping verification"

        try:
            should_close, reason = await evaluator.check_immediate_profit_opportunity(
                position, snapshots, bbo_prices=bbo_prices
            )

            if not should_close:
                strategy.logger.info(
                    f"❌ Profit verification failed for {position.symbol}: {reason}. "
                    f"Aborting close to prevent unprofitable execution."
                )
                return False, f"Profit opportunity disappeared: {reason}"

            strategy.logger.debug(
                f"✅ Profit verification passed for {position.symbol}: {reason}"
            )
            return True, reason

        except Exception as e:
            strategy.logger.error(
                f"Error during profit verification for {position.symbol}: {e}",
                exc_info=True
            )
            return False, f"Verification error: {str(e)}"

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

    async def _should_skip_erosion_exit(self, position: "FundingArbPosition", trigger_reason: Optional[str]) -> bool:
        return await self._exit_evaluator.should_skip_erosion_exit(
            position, trigger_reason, is_opportunity_tradeable=self._is_opportunity_tradeable
        )

    async def _is_opportunity_tradeable(self, opportunity: "ArbitrageOpportunity") -> bool:
        try:
            from funding_rate_service.core.opportunity_finder import OpportunityFinder
        except Exception:
            return True
        sym = opportunity.symbol
        if not OpportunityFinder.is_symbol_tradeable(opportunity.long_dex, sym): return False
        if not OpportunityFinder.is_symbol_tradeable(opportunity.short_dex, sym): return False
        
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