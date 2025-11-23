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

    async def evaluateAndClosePositions(self) -> List[str]:
        """Evaluate all open positions and close those that meet exit conditions."""
        strategy = self._strategy
        actions: List[str] = []
        positions = await strategy.position_manager.get_open_positions()
        enable_polling = getattr(strategy.config, "enable_exit_polling", True)

        for position in positions:
            # Skip positions that are already being closed (prevents race conditions)
            if position.id in self._positions_closing:
                strategy.logger.debug(
                    f"Skipping {position.symbol} - position is already being closed"
                )
                continue
            
            snapshots = await self._fetch_leg_snapshots(position)

            # Check liquidation risk FIRST (highest priority - proactive prevention)
            # Critical exits bypass polling entirely
            liquidation_risk_reason = self._exit_evaluator.check_liquidation_risk(position, snapshots)
            if liquidation_risk_reason is not None:
                # Cancel any existing polling for critical exit
                self._cancel_exit_polling(position)
                # Use limit orders to prevent slippage (speed is less critical than avoiding slippage)
                await self.close(position, liquidation_risk_reason, live_snapshots=snapshots, order_type="limit")
                strategy.logger.warning(
                    f"Closed {position.symbol}: {liquidation_risk_reason}"
                )
                actions.append(f"Closed {position.symbol}: {liquidation_risk_reason}")
                continue

            # Check if already liquidated (reactive detection)
            # Critical exits bypass polling entirely
            liquidation_reason = self._exit_evaluator.detect_liquidation(position, snapshots)
            if liquidation_reason is not None:
                self._cancel_exit_polling(position)
                await self.close(position, liquidation_reason, live_snapshots=snapshots)
                strategy.logger.warning(
                    f"Closed {position.symbol}: {liquidation_reason}"
                )
                actions.append(f"Closed {position.symbol}: {liquidation_reason}")
                continue

            imbalance_reason = self._exit_evaluator.detect_imbalance(position, snapshots)
            if imbalance_reason is not None:
                self._cancel_exit_polling(position)
                await self.close(position, imbalance_reason, live_snapshots=snapshots)
                strategy.logger.warning(
                    f"Closed {position.symbol}: {imbalance_reason}"
                )
                actions.append(f"Closed {position.symbol}: {imbalance_reason}")
                continue

            # Check for IMMEDIATE PROFIT OPPORTUNITY (cross-exchange basis spread)
            # This check runs BEFORE normal exit conditions to capture favorable price divergence
            should_take_profit, profit_reason = await self._exit_evaluator.check_immediate_profit_opportunity(
                position, snapshots
            )
            if should_take_profit:
                # Verify profitability right before execution using fresh BBO prices
                is_profitable, verification_reason = await self._verify_profit_opportunity_pre_execution(
                    position, snapshots
                )

                if not is_profitable:
                    strategy.logger.warning(
                        f"❌ Profit opportunity disappeared before execution for {position.symbol}: "
                        f"{verification_reason}. Skipping close."
                    )
                    # Don't close if profit disappeared - continue to next position
                    continue

                # Execute profit-taking close with normal limit orders (hedge will use aggressive_limit)
                strategy.logger.info(
                    f"💰 Executing profit-taking close with limit orders for {position.symbol}"
                )
                self._cancel_exit_polling(position)
                await self.close(
                    position,
                    profit_reason,
                    live_snapshots=snapshots,
                    order_type="limit",  # Use normal limit orders initially, hedge will use aggressive_limit if one leg fills first
                )
                actions.append(f"Closed {position.symbol}: {profit_reason}")
                continue

            # Check if position is in exit polling state
            polling_state = self._check_exit_polling_state(position)
            
            if polling_state:
                # Position is in polling state - handle polling logic
                await self._handle_exit_polling(
                    position, polling_state, snapshots, actions
                )
                continue
            
            # Position is NOT in polling - evaluate exit conditions
            should_close, reason = await self._exit_evaluator.should_close(
                position,
                snapshots,
                gather_current_rates=self._gather_current_rates,
                should_skip_erosion_exit=self._should_skip_erosion_exit,
            )
            
            if should_close:
                if enable_polling:
                    # Check spread before entering polling
                    spread_acceptable = await self._check_spread_before_polling(
                        position, snapshots
                    )
                    
                    if not spread_acceptable:
                        # Spread too wide, defer (will check again next iteration)
                        strategy.logger.debug(
                            f"Deferring exit polling start for {position.symbol} due to wide spread"
                        )
                        continue
                    
                    # Check if break-even is available NOW
                    can_exit_now = self._exit_evaluator.can_exit_at_break_even(
                        position, snapshots
                    )
                    
                    if can_exit_now:
                        # Break-even available immediately - exit now (no polling delay)
                        # Use limit orders for better execution
                        await self.close(position, reason or "UNKNOWN", live_snapshots=snapshots, order_type="limit")
                        strategy.logger.info(
                            f"Closed {position.symbol}: {reason} (break-even available immediately)"
                        )
                        actions.append(f"Closed {position.symbol}: {reason}")
                    else:
                        # Break-even not available - start polling
                        self._start_exit_polling(position, reason or "UNKNOWN")
                        strategy.logger.info(
                            f"Started exit polling for {position.symbol}: {reason} "
                            f"(waiting for break-even exit opportunity)"
                        )
                else:
                    # Polling disabled - exit immediately
                    await self.close(position, reason or "UNKNOWN", live_snapshots=snapshots)
                    strategy.logger.info(f"Closed {position.symbol}: {reason}")
                    actions.append(f"Closed {position.symbol}: {reason}")
            else:
                strategy.logger.debug(
                    f"Position {position.symbol} not closing: {reason}"
                )

        return actions
    
    async def _handle_exit_polling(
        self,
        position: "FundingArbPosition",
        polling_state: Dict[str, Any],
        snapshots: Dict[str, Optional["ExchangePositionSnapshot"]],
        actions: List[str],
    ) -> None:
        """
        Handle exit polling logic for a position in polling state.
        
        Args:
            position: Position in polling state
            polling_state: Polling state dict
            snapshots: Current exchange snapshots
            actions: Actions list to append to
        """
        strategy = self._strategy
        
        # Update last check time
        polling_state["last_check_at"] = datetime.now(timezone.utc).isoformat()
        if position.metadata is None:
            position.metadata = {}
        position.metadata["exit_polling_state"] = polling_state
        
        # Check if timeout expired
        if self._is_polling_timeout_expired(polling_state):
            # Timeout expired - force exit with aggressive limit orders
            reason = polling_state.get("exit_reason", "TIMEOUT")
            strategy.logger.warning(
                f"⏰ Exit polling timeout expired for {position.symbol}. "
                f"Forcing exit with aggressive limit orders."
            )
            self._cancel_exit_polling(position)
            # Use limit order type to ensure aggressive_limit execution mode is used
            await self.close(position, f"{reason}_TIMEOUT", live_snapshots=snapshots, order_type="limit")
            actions.append(f"Closed {position.symbol}: {reason}_TIMEOUT (timeout)")
            return
        
        # Re-evaluate exit conditions (maybe funding improved?)
        should_close, current_reason = await self._exit_evaluator.should_close(
            position,
            snapshots,
            gather_current_rates=self._gather_current_rates,
            should_skip_erosion_exit=self._should_skip_erosion_exit,
        )
        
        if not should_close:
            # Exit conditions no longer met - cancel polling
            strategy.logger.info(
                f"✅ Exit conditions no longer met for {position.symbol} "
                f"(was: {polling_state.get('exit_reason')}, now: {current_reason}). "
                f"Cancelling exit polling."
            )
            self._cancel_exit_polling(position)
            await strategy.position_manager.update(position)
            return
        
        # Exit conditions still met - check if break-even reached
        can_exit = self._exit_evaluator.can_exit_at_break_even(position, snapshots)
        
        if can_exit:
            # Break-even reached - check spread before attempting exit
            spread_acceptable = await self._check_spread_before_exit(position, snapshots)
            
            if spread_acceptable:
                # Break-even reached and spread acceptable - attempt exit
                reason = polling_state.get("exit_reason", "UNKNOWN")
                strategy.logger.info(
                    f"✅ Break-even exit opportunity available for {position.symbol}. "
                    f"Attempting exit."
                )
                self._cancel_exit_polling(position)
                # Use limit orders for better execution
                await self.close(position, reason, live_snapshots=snapshots, order_type="limit")
                actions.append(f"Closed {position.symbol}: {reason} (break-even)")
            else:
                # Break-even reached but spread too wide - continue polling
                strategy.logger.debug(
                    f"Break-even reached for {position.symbol} but spread too wide. "
                    f"Continuing polling."
                )
        else:
            # Break-even not reached yet - continue polling
            strategy.logger.debug(
                f"⏳ Exit polling for {position.symbol}: waiting for break-even "
                f"(reason: {polling_state.get('exit_reason')})"
            )
        
        # Update position with polling state
        await strategy.position_manager.update(position)

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

        # Check if this is a manual close (from telegram bot or control API)
        # Manual closes should cancel any existing exit polling and proceed immediately
        manual_close_reasons = {
            "telegram_manual_close",
            "telegram_stop_with_close",
            "manual_close",
        }
        is_manual_close = reason.lower() in manual_close_reasons or reason.startswith("telegram_")
        
        if is_manual_close:
            # Cancel any existing exit polling for manual closes
            polling_state = self._check_exit_polling_state(position)
            if polling_state:
                strategy.logger.info(
                    f"🛑 Manual close requested for {position.symbol}. "
                    f"Cancelling exit polling (was waiting for: {polling_state.get('exit_reason')})"
                )
                self._cancel_exit_polling(position)
                await strategy.position_manager.update(position)

        # Check if position is already being closed (prevent concurrent closes)
        if position.id in self._positions_closing:
            strategy.logger.warning(
                f"Position {position.symbol} (id={position.id}) is already being closed. "
                f"Skipping duplicate close request (reason: {reason})"
            )
            return
        
        # Mark position as being closed
        self._positions_closing.add(position.id)
        strategy.logger.debug(f"Marking position {position.symbol} (id={position.id}) as closing")

        try:
            pre_close_snapshots = live_snapshots or await self._fetch_leg_snapshots(position)
            
            total_unrealized_pnl = Decimal("0")
            total_funding_accrued = Decimal("0")
            missing_snapshot_data = False
            
            for dex in [position.long_dex, position.short_dex]:
                snapshot = pre_close_snapshots.get(dex) or pre_close_snapshots.get(dex.lower())
                if not snapshot:
                    missing_snapshot_data = True
                    strategy.logger.debug(
                        f"[{dex}] Missing snapshot, will use fallback method for PnL"
                    )
                    continue
                
                if snapshot.unrealized_pnl is not None:
                    unrealized_pnl_decimal = to_decimal(snapshot.unrealized_pnl)
                    total_unrealized_pnl += unrealized_pnl_decimal
                    strategy.logger.debug(
                        f"[{dex}] Pre-close Unrealized PnL: ${unrealized_pnl_decimal:.2f}"
                    )
                else:
                    missing_snapshot_data = True
                    strategy.logger.debug(
                        f"[{dex}] Missing unrealized_pnl in snapshot, will use fallback method"
                    )
                
                if snapshot.funding_accrued is not None:
                    funding_decimal = to_decimal(snapshot.funding_accrued)
                    total_funding_accrued += funding_decimal
                    strategy.logger.debug(
                        f"[{dex}] Pre-close Funding Accrued: ${funding_decimal:.2f}"
                    )
                else:
                    strategy.logger.debug(
                        f"[{dex}] Missing funding_accrued in snapshot, will use cumulative_funding from DB"
                    )
            
            try:
                await self._close_executor.close_exchange_positions(
                    position,
                    reason=reason,
                    live_snapshots=pre_close_snapshots,
                    order_type=order_type,
                    order_builder=self._order_builder,
                )
            except WideSpreadException as exc:
                # Defer closing due to wide spread - position will be checked again next iteration
                strategy.logger.info(
                    f"⏸️  Deferring close for {position.symbol} due to wide spread "
                    f"({exc.spread_pct*100:.2f}%) on {exc.exchange.upper()}. "
                    f"Will retry next iteration."
                )
                return  # Exit early without closing position

            await asyncio.sleep(1.0)
            
            closing_fees = Decimal("0")
            close_result = position.metadata.get("close_execution_result")
            if close_result and close_result.get("filled_orders"):
                closing_fees = self._pnl_calculator.calculate_closing_fees(
                    close_result, 
                    order_type=self._current_close_order_type
                )
                strategy.logger.debug(
                    f"Closing fees calculated: ${closing_fees:.2f}"
                )
            
            pnl = None
            pnl_method = None
            
            try:
                import time
                start_time = position.opened_at.timestamp()
                end_time = time.time()
                trade_history_result = await self._pnl_calculator.calculate_pnl_from_trade_history(
                    position,
                    close_result,
                    start_time,
                    end_time,
                    store_trades_fn=self._pnl_calculator.store_trades_in_database,
                )
                if trade_history_result:
                    pnl, pnl_method = trade_history_result
            except Exception as e:
                strategy.logger.debug(
                    f"Trade history PnL calculation failed: {e}, falling back to snapshots"
                )
            
            if pnl is None and not missing_snapshot_data:
                if total_funding_accrued != 0:
                    funding_to_add = total_funding_accrued
                    funding_source = "snapshots"
                    strategy.logger.debug(
                        f"Using funding_accrued from snapshots: ${total_funding_accrued:.2f}"
                    )
                else:
                    cumulative_funding = await strategy.position_manager.get_cumulative_funding(position.id)
                    funding_to_add = to_decimal(cumulative_funding)
                    funding_source = "database"
                    strategy.logger.debug(
                        f"Using cumulative_funding from database: ${cumulative_funding:.2f}"
                    )
                
                entry_fees_decimal = to_decimal(position.total_fees_paid)
                closing_fees_decimal = to_decimal(closing_fees)
                total_fees_decimal = entry_fees_decimal + closing_fees_decimal
                pnl = total_unrealized_pnl + funding_to_add - total_fees_decimal
                pnl_method = "snapshots"
                
                strategy.logger.info(
                    f"PnL calculation ({pnl_method}): "
                    f"price_pnl=${total_unrealized_pnl:.2f}, "
                    f"funding=${funding_to_add:.2f} (from {funding_source}), "
                    f"entry_fees=${position.total_fees_paid:.2f}, "
                    f"closing_fees=${closing_fees:.2f}, "
                    f"total_fees=${total_fees_decimal:.2f}, "
                    f"net_pnl=${pnl:.2f}"
                )
            
            if pnl is None:
                if close_result and close_result.get("filled_orders"):
                    strategy.logger.debug(
                        f"Using close execution result (websocket fills) for PnL: {position.symbol}"
                    )
                    cumulative_funding = await strategy.position_manager.get_cumulative_funding(position.id)
                    position.cumulative_funding = cumulative_funding
                    
                    legs_metadata = position.metadata.get("legs", {})
                    price_pnl = Decimal("0")
                    
                    for fill_info in close_result["filled_orders"]:
                        dex = fill_info["dex"]
                        fill_price = fill_info.get("fill_price")
                        filled_qty = fill_info.get("filled_quantity")
                        
                        if fill_price and filled_qty:
                            leg_meta = legs_metadata.get(dex, {})
                            entry_price = leg_meta.get("entry_price")
                            
                            if entry_price:
                                fill_price_decimal = to_decimal(fill_price)
                                entry_price_decimal = to_decimal(entry_price)
                                filled_qty_decimal = to_decimal(filled_qty)
                                
                                side = leg_meta.get("side", "long")
                                if side == "long":
                                    leg_pnl = (fill_price_decimal - entry_price_decimal) * filled_qty_decimal
                                else:
                                    leg_pnl = (entry_price_decimal - fill_price_decimal) * filled_qty_decimal
                                price_pnl += leg_pnl
                                strategy.logger.debug(
                                    f"[{dex}] Price PnL from websocket fills: ${leg_pnl:.2f} "
                                    f"(entry=${entry_price_decimal:.6f}, exit=${fill_price_decimal:.6f}, qty={filled_qty_decimal})"
                                )
                    
                    cumulative_funding_decimal = to_decimal(cumulative_funding)
                    entry_fees_decimal = to_decimal(position.total_fees_paid)
                    closing_fees_decimal = to_decimal(closing_fees)
                    total_fees_decimal = entry_fees_decimal + closing_fees_decimal
                    pnl = price_pnl + cumulative_funding_decimal - total_fees_decimal
                    pnl_method = "websocket_fills"
                    
                    strategy.logger.info(
                        f"PnL calculation ({pnl_method}): "
                        f"price_pnl=${price_pnl:.2f}, "
                        f"funding=${cumulative_funding:.2f}, "
                        f"entry_fees=${position.total_fees_paid:.2f}, "
                        f"closing_fees=${closing_fees:.2f}, "
                        f"total_fees=${total_fees_decimal:.2f}, "
                        f"net_pnl=${pnl:.2f}"
                    )
            
            if pnl is None:
                strategy.logger.warning(
                    f"Could not get accurate PnL from exchanges for {position.symbol}, "
                    f"falling back to cumulative funding method"
                )
                cumulative_funding = await strategy.position_manager.get_cumulative_funding(position.id)
                position.cumulative_funding = cumulative_funding
                entry_fees_decimal = to_decimal(position.total_fees_paid)
                closing_fees_decimal = to_decimal(closing_fees)
                total_fees_decimal = entry_fees_decimal + closing_fees_decimal
                pnl = position.get_net_pnl() - closing_fees_decimal
                pnl_method = "cumulative_funding"
                
                strategy.logger.info(
                    f"PnL calculation ({pnl_method}): "
                    f"funding=${cumulative_funding:.2f}, "
                    f"entry_fees=${position.total_fees_paid:.2f}, "
                    f"closing_fees=${closing_fees:.2f}, "
                    f"total_fees=${total_fees_decimal:.2f}, "
                    f"net_pnl=${pnl:.2f}"
                )
            
            if position.size_usd and position.size_usd > Decimal("0"):
                try:
                    pnl_pct = pnl / position.size_usd
                except Exception:
                    pnl_pct = Decimal("0")
            else:
                pnl_pct = Decimal("0")

            await strategy.position_manager.close(
                position.id,
                exit_reason=reason,
                pnl_usd=pnl,
            )

            refreshed = await strategy.position_manager.get(position.id)
            if refreshed:
                position = refreshed

            strategy.logger.info(
                f"✅ Closed {position.symbol} ({reason}): "
                f"PnL=${pnl:.2f} ({pnl_pct*100:.2f}%), "
                f"Age={position.get_age_hours():.1f}h"
            )
            
            try:
                # Extract leg PnL from pre_close_snapshots for individual leg breakdown
                leg_pnl: Dict[str, Decimal] = {}
                for dex in [position.long_dex, position.short_dex]:
                    snapshot = pre_close_snapshots.get(dex) or pre_close_snapshots.get(dex.lower())
                    if snapshot and snapshot.unrealized_pnl is not None:
                        leg_pnl[dex] = to_decimal(snapshot.unrealized_pnl)
                
                await strategy.notification_service.notify_position_closed(
                    symbol=position.symbol,
                    reason=reason,
                    pnl_usd=pnl,
                    pnl_pct=pnl_pct,
                    age_hours=position.get_age_hours(),
                    size_usd=position.size_usd,
                    leg_pnl=leg_pnl if leg_pnl else None,
                )
            except Exception as exc:
                strategy.logger.warning(f"Failed to send position closed notification: {exc}")

        except Exception as exc:
            strategy.logger.error(
                f"Error closing position {position.id}: {exc}"
            )
            raise
        finally:
            # Always remove from closing set, even if close failed
            self._positions_closing.discard(position.id)
            strategy.logger.debug(f"Removed position {position.symbol} (id={position.id}) from closing set")

    def _build_risk_manager(self):
        """Build risk manager from strategy config."""
        strategy = self._strategy
        risk_cfg = strategy.config.risk_config

        try:
            config_payload = {
                "min_erosion_ratio": float(risk_cfg.min_erosion_threshold),
                "severe_erosion_ratio": float(
                    getattr(risk_cfg, "severe_erosion_ratio", Decimal("0.2"))
                ),
                "max_position_age_hours": risk_cfg.max_position_age_hours,
                "flip_margin": float(getattr(risk_cfg, "flip_margin", Decimal("0"))),
            }
            return get_risk_manager(risk_cfg.strategy, config_payload)
        except Exception as exc:
            strategy.logger.error(
                f"Failed to initialize risk manager '{risk_cfg.strategy}': {exc}"
            )
            return None

    async def _gather_current_rates(
        self, position: "FundingArbPosition"
    ) -> Optional[Dict[str, Decimal]]:
        """Fetch latest funding rates for both legs."""
        repo = getattr(self._strategy, "funding_rate_repo", None)
        if repo is None:
            return None

        try:
            long_rate_row = await repo.get_latest_specific(
                position.long_dex, position.symbol
            )
            short_rate_row = await repo.get_latest_specific(
                position.short_dex, position.symbol
            )
        except Exception as exc:
            self._strategy.logger.error(
                f"Failed to fetch funding rates for {position.symbol}: {exc}"
            )
            return None

        if not long_rate_row or not short_rate_row:
            return None

        def _extract(row, key: str) -> Optional[Decimal]:
            value = None
            if isinstance(row, dict):
                value = row.get(key)
            elif hasattr(row, "_mapping"):
                value = row._mapping.get(key)
            else:
                value = getattr(row, key, None)
            if value is None:
                return None
            try:
                return Decimal(str(value))
            except Exception:
                return None

        long_rate = _extract(long_rate_row, "funding_rate")
        short_rate = _extract(short_rate_row, "funding_rate")
        if long_rate is None or short_rate is None:
            self._strategy.logger.warning(
                f"Funding rate data missing for {position.symbol}: "
                f"long={long_rate_row}, short={short_rate_row}"
            )
            return None
        divergence = short_rate - long_rate
        position.current_divergence = divergence

        return {
            "divergence": divergence,
            "long_rate": long_rate,
            "short_rate": short_rate,
            "long_oi_usd": _extract(long_rate_row, "open_interest_usd") or Decimal("0"),
            "short_oi_usd": _extract(short_rate_row, "open_interest_usd") or Decimal("0"),
        }

    async def _fetch_leg_snapshots(
        self, position: "FundingArbPosition"
    ) -> Dict[str, Optional["ExchangePositionSnapshot"]]:
        """Fetch up-to-date exchange snapshots for both legs."""
        snapshots: Dict[str, Optional["ExchangePositionSnapshot"]] = {}

        legs_metadata = (position.metadata or {}).get("legs", {})

        for dex in filter(None, [position.long_dex, position.short_dex]):
            client = self._strategy.exchange_clients.get(dex)
            if client is None:
                self._strategy.logger.error(
                    f"No exchange client for {dex} while evaluating {position.symbol}"
                )
                snapshots[dex] = None
                continue

            leg_metadata = legs_metadata.get(dex, {}) if isinstance(legs_metadata, dict) else {}
            await self._contract_preparer.prepare_contract_context(
                client,
                position.symbol,
                metadata=leg_metadata,
                contract_hint=leg_metadata.get("market_id"),
                logger=self._strategy.logger,
            )
            await self._ws_manager.ensure_market_feed_once(client, position.symbol, self._strategy.logger)

            try:
                snapshots[dex] = await client.get_position_snapshot(position.symbol)
            except Exception as exc:
                self._strategy.logger.error(
                    f"[{dex}] Failed to fetch position snapshot for {position.symbol}: {exc}"
                )
                snapshots[dex] = None

        return snapshots

    async def _should_skip_erosion_exit(
        self,
        position: "FundingArbPosition",
        trigger_reason: Optional[str],
    ) -> bool:
        """Check if erosion exit should be skipped."""
        return await self._exit_evaluator.should_skip_erosion_exit(
            position,
            trigger_reason,
            is_opportunity_tradeable=self._is_opportunity_tradeable,
        )

    async def _is_opportunity_tradeable(
        self,
        opportunity: "ArbitrageOpportunity",
    ) -> bool:
        """Validate that an opportunity is actually tradeable."""
        strategy = self._strategy
        
        try:
            from funding_rate_service.core.opportunity_finder import OpportunityFinder
        except Exception:
            return True
        
        symbol = opportunity.symbol
        long_dex = opportunity.long_dex
        short_dex = opportunity.short_dex
        
        if not OpportunityFinder.is_symbol_tradeable(long_dex, symbol):
            strategy.logger.debug(
                f"⏭️  [{symbol}] Skipping opportunity - {long_dex} marked as untradeable"
            )
            return False
        
        if not OpportunityFinder.is_symbol_tradeable(short_dex, symbol):
            strategy.logger.debug(
                f"⏭️  [{symbol}] Skipping opportunity - {short_dex} marked as untradeable"
            )
            return False
        
        min_volume_24h = getattr(strategy.config, "min_volume_24h", None)
        min_oi_usd = getattr(strategy.config, "min_oi_usd", None)
        
        if min_volume_24h is not None:
            min_volume = opportunity.min_volume_24h
            if min_volume is None:
                strategy.logger.debug(
                    f"⏭️  [{symbol}] Skipping opportunity - missing volume data "
                    f"(required: ${min_volume_24h:.0f})"
                )
                return False
        
        if min_oi_usd is not None:
            min_oi = opportunity.min_oi_usd
            if min_oi is None:
                strategy.logger.debug(
                    f"⏭️  [{symbol}] Skipping opportunity - missing OI data "
                    f"(required: ${min_oi_usd:.0f})"
                )
                return False
        
        long_client = strategy.exchange_clients.get(long_dex)
        short_client = strategy.exchange_clients.get(short_dex)
        
        if long_client and short_client:
            try:
                leverage_validator = getattr(strategy, "leverage_validator", None)
                if leverage_validator:
                    long_leverage_info = await leverage_validator.get_leverage_info(long_client, symbol)
                    if long_leverage_info.error == "MARKET_NOT_FOUND":
                        strategy.logger.debug(
                            f"⏭️  [{symbol}] Skipping opportunity - MARKET_NOT_FOUND on {long_dex}"
                        )
                        OpportunityFinder.mark_symbol_untradeable(long_dex, symbol)
                        return False
                    
                    short_leverage_info = await leverage_validator.get_leverage_info(short_client, symbol)
                    if short_leverage_info.error == "MARKET_NOT_FOUND":
                        strategy.logger.debug(
                            f"⏭️  [{symbol}] Skipping opportunity - MARKET_NOT_FOUND on {short_dex}"
                        )
                        OpportunityFinder.mark_symbol_untradeable(short_dex, symbol)
                        return False
            except Exception as exc:
                strategy.logger.debug(
                    f"⚠️  [{symbol}] Leverage check failed during tradability validation: {exc}"
                )
        
        return True

    def _check_exit_polling_state(
        self, position: "FundingArbPosition"
    ) -> Optional[Dict[str, Any]]:
        """
        Check if position is in exit polling state.
        
        Returns:
            Polling state dict if in polling, None otherwise
        """
        polling_state = (position.metadata or {}).get("exit_polling_state")
        if polling_state and isinstance(polling_state, dict):
            return polling_state
        return None
    
    def _start_exit_polling(
        self, position: "FundingArbPosition", reason: str
    ) -> None:
        """
        Start exit polling state for position.
        
        Args:
            position: Position to start polling for
            reason: Exit reason that triggered polling
        """
        strategy = self._strategy
        max_duration_minutes = getattr(
            strategy.config, "exit_polling_max_duration_minutes", 10
        )
        
        polling_state = {
            "started_at": datetime.now(timezone.utc).isoformat(),
            "exit_reason": reason,
            "max_duration_minutes": max_duration_minutes,
            "last_check_at": datetime.now(timezone.utc).isoformat(),
        }
        
        if position.metadata is None:
            position.metadata = {}
        position.metadata["exit_polling_state"] = polling_state
        
        strategy.logger.info(
            f"⏳ Started exit polling for {position.symbol} (reason: {reason}, "
            f"max duration: {max_duration_minutes} minutes)"
        )
    
    def _cancel_exit_polling(self, position: "FundingArbPosition") -> None:
        """
        Cancel exit polling state for position.
        
        Args:
            position: Position to cancel polling for
        """
        strategy = self._strategy
        if position.metadata and "exit_polling_state" in position.metadata:
            polling_state = position.metadata.pop("exit_polling_state")
            reason = polling_state.get("exit_reason", "UNKNOWN")
            strategy.logger.info(
                f"✅ Cancelled exit polling for {position.symbol} (reason: {reason})"
            )
    
    def _is_polling_timeout_expired(
        self, polling_state: Dict[str, Any]
    ) -> bool:
        """
        Check if polling timeout has expired.
        
        Args:
            polling_state: Polling state dict
            
        Returns:
            True if timeout expired, False otherwise
        """
        started_at_str = polling_state.get("started_at")
        max_duration_minutes = polling_state.get("max_duration_minutes", 10)
        
        if not started_at_str:
            return True  # Invalid state, consider expired
        
        try:
            started_at = datetime.fromisoformat(started_at_str.replace("Z", "+00:00"))
            if started_at.tzinfo is None:
                started_at = started_at.replace(tzinfo=timezone.utc)
            
            elapsed_minutes = (datetime.now(timezone.utc) - started_at).total_seconds() / 60
            return elapsed_minutes >= max_duration_minutes
        except Exception:
            return True  # On error, consider expired
    
    async def _check_spread_before_polling(
        self,
        position: "FundingArbPosition",
        snapshots: Dict[str, Optional["ExchangePositionSnapshot"]],
    ) -> bool:
        """
        Check if spread is acceptable before entering polling.
        
        Returns:
            True if spread is acceptable, False if too wide (should defer)
        """
        strategy = self._strategy
        
        if not getattr(strategy.config, "enable_wide_spread_protection", True):
            return True  # Spread protection disabled, allow polling
        
        try:
            price_provider = getattr(strategy, "price_provider", None)
            if not price_provider:
                return True  # No price provider, allow polling
            
            # Check spread for both legs
            for dex in [position.long_dex, position.short_dex]:
                client = strategy.exchange_clients.get(dex)
                if not client:
                    continue
                
                snapshot = snapshots.get(dex) or snapshots.get(dex.lower())
                if not snapshot:
                    continue
                
                try:
                    bid, ask = await price_provider.get_bbo_prices(client, position.symbol)
                    acceptable, spread_pct, reason_msg = is_spread_acceptable(
                        bid, ask, SpreadCheckType.EXIT
                    )

                    if not acceptable:
                        strategy.logger.info(
                            f"⏸️  Wide spread detected on {dex.upper()} {position.symbol}: "
                            f"{spread_pct*100:.2f}% (bid={bid}, ask={ask}). "
                            f"Deferring exit polling."
                        )
                        return False
                except Exception as exc:
                    strategy.logger.warning(
                        f"Failed to check spread for {dex} {position.symbol}: {exc}. "
                        f"Allowing polling."
                    )
                    continue
            
            return True  # Spread acceptable
        except Exception as exc:
            strategy.logger.warning(
                f"Error checking spread before polling for {position.symbol}: {exc}. "
                f"Allowing polling."
            )
            return True
    
    async def _check_spread_before_exit(
        self,
        position: "FundingArbPosition",
        snapshots: Dict[str, Optional["ExchangePositionSnapshot"]],
    ) -> bool:
        """
        Check if spread is acceptable before attempting exit during polling.
        
        Returns:
            True if spread is acceptable, False if too wide (should continue polling)
        """
        return await self._check_spread_before_polling(position, snapshots)
    
    async def _verify_profit_opportunity_pre_execution(
        self,
        position: "FundingArbPosition",
        snapshots: Dict[str, Optional["ExchangePositionSnapshot"]],
    ) -> tuple[bool, str]:
        """
        Verify profitability RIGHT BEFORE execution using fresh BBO prices.

        This prevents executing closes when profit opportunity has disappeared.
        Fetches fresh orderbook data via websocket/API to ensure accuracy.

        Returns:
            Tuple[bool, str]: (is_profitable, reason)
        """
        strategy = self._strategy

        # Get price provider
        price_provider = getattr(strategy, "price_provider", None)
        if not price_provider:
            return False, "Price provider not available"

        # Get exchange clients
        long_client = strategy.exchange_clients.get(position.long_dex)
        short_client = strategy.exchange_clients.get(position.short_dex)

        if not long_client or not short_client:
            return False, "Missing exchange client"

        try:
            # Get fresh BBO for both legs (returns tuple of (bid, ask))
            long_bid, long_ask = await price_provider.get_bbo_prices(long_client, position.symbol)
            long_fresh_mark = (long_bid + long_ask) / Decimal("2")

            short_bid, short_ask = await price_provider.get_bbo_prices(short_client, position.symbol)
            short_fresh_mark = (short_bid + short_ask) / Decimal("2")

        except Exception as e:
            strategy.logger.error(f"Failed to fetch fresh BBO prices for {position.symbol}: {e}")
            return False, f"BBO fetch failed: {e}"

        # Get entry prices and quantities from metadata
        legs_metadata = position.metadata.get("legs", {})
        long_meta = legs_metadata.get(position.long_dex, {})
        short_meta = legs_metadata.get(position.short_dex, {})

        long_entry = long_meta.get("entry_price")
        short_entry = short_meta.get("entry_price")

        if not long_entry or not short_entry:
            return False, "Missing entry prices in metadata"

        # Get quantities from snapshots
        long_snapshot = snapshots.get(position.long_dex)
        short_snapshot = snapshots.get(position.short_dex)

        if not long_snapshot or not short_snapshot:
            return False, "Missing snapshots"

        long_qty = long_snapshot.quantity.copy_abs() if long_snapshot.quantity else Decimal("0")
        short_qty = short_snapshot.quantity.copy_abs() if short_snapshot.quantity else Decimal("0")

        # Validate quantities
        if long_qty <= 0 or short_qty <= 0:
            return False, f"Invalid quantities: long={long_qty}, short={short_qty}"

        # Calculate PnL with fresh prices (price-based PnL only, funding already accrued)
        long_entry_decimal = to_decimal(long_entry)
        short_entry_decimal = to_decimal(short_entry)

        long_price_pnl = (long_fresh_mark - long_entry_decimal) * long_qty
        short_price_pnl = (short_entry_decimal - short_fresh_mark) * short_qty  # Inverse for short

        # Add funding accrued from snapshots (same as initial check)
        long_funding = to_decimal(long_snapshot.funding_accrued) if long_snapshot.funding_accrued is not None else Decimal("0")
        short_funding = to_decimal(short_snapshot.funding_accrued) if short_snapshot.funding_accrued is not None else Decimal("0")

        long_pnl = long_price_pnl + long_funding
        short_pnl = short_price_pnl + short_funding
        net_pnl = long_pnl + short_pnl

        # Estimate closing fees (using maker fees since we'll use limit orders)
        estimated_fees = self._exit_evaluator._estimate_closing_fees_maker(position, snapshots)

        # Calculate net profit with fresh prices
        net_profit = net_pnl - estimated_fees

        # Use same threshold as initial check: 0.2% of position size
        min_profit_pct = getattr(strategy.config, "min_immediate_profit_taking_pct", Decimal("0.002"))  # 0.2%
        min_profit_threshold = position.size_usd * min_profit_pct

        if net_profit > min_profit_threshold:
            strategy.logger.info(
                f"✅ Profit verified pre-execution for {position.symbol}: ${net_profit:.2f} "
                f"(threshold: ${min_profit_threshold:.2f})",
                extra={
                    "long_fresh_mark": float(long_fresh_mark),
                    "short_fresh_mark": float(short_fresh_mark),
                    "long_pnl": float(long_pnl),
                    "short_pnl": float(short_pnl),
                    "net_pnl": float(net_pnl),
                    "estimated_fees": float(estimated_fees),
                    "net_profit": float(net_profit),
                    "min_profit_threshold": float(min_profit_threshold),
                }
            )
            return True, f"Verified: ${net_profit:.2f} profit"
        else:
            return False, f"Profit disappeared: ${net_profit:.2f} (threshold: ${min_profit_threshold:.2f})"

    @staticmethod
    def _symbols_match(position_symbol: Optional[str], event_symbol: Optional[str]) -> bool:
        """Check if two symbols match (handles variations like BTC vs BTCUSDT)."""
        pos_upper = (position_symbol or "").upper()
        event_upper = (event_symbol or "").upper()

        if not pos_upper or not event_upper:
            return False

        if pos_upper == event_upper:
            return True

        if event_upper.endswith(pos_upper) or event_upper.startswith(pos_upper):
            return True

        if pos_upper.endswith(event_upper) or pos_upper.startswith(event_upper):
            return True

        return False

