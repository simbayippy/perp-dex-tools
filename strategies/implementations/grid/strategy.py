"""
Grid Trading Strategy

Single-exchange grid trading implementation.
Inherits directly from BaseStrategy and composes what it needs.
"""

import time
from decimal import Decimal
from typing import List, Dict, Any, Optional, Tuple

from strategies.base_strategy import BaseStrategy
from .config import GridConfig
from .models import GridState, GridOrder, GridCycleState, TrackedPosition
from exchange_clients.base_models import ExchangePositionSnapshot
from helpers.event_notifier import GridEventNotifier
from strategies.execution.core.leverage_validator import LeverageValidator


class GridStrategy(BaseStrategy):
    """
    Grid trading strategy implementation.
    
    This strategy:
    1. Places orders at regular price intervals (grid levels)
    2. Takes profit when price moves to the next grid level
    3. Maintains a maximum number of active orders
    4. Uses dynamic cooldown based on order density
    5. Supports optional safety features (stop/pause prices)
    """
    
    def __init__(
        self,
        config: GridConfig,
        exchange_client
    ):
        """
        Initialize grid strategy.
        
        Args:
            config: GridConfig instance with strategy parameters
            exchange_client: Exchange client for trading
        """
        super().__init__(
            config=config,
            exchange_client=exchange_client
        )
        
        # Initialize grid state
        self.grid_state = GridState()
        
        exchange_name = "unknown"
        if hasattr(exchange_client, "get_exchange_name"):
            try:
                exchange_name = exchange_client.get_exchange_name()
            except Exception:
                exchange_name = "unknown"
        
        ticker = "unknown"
        if hasattr(exchange_client, "config"):
            ticker = getattr(exchange_client.config, "ticker", ticker)
        elif hasattr(config, "ticker"):
            ticker = getattr(config, "ticker")
        
        self.event_notifier = GridEventNotifier(
            strategy="grid",
            exchange=str(exchange_name),
            ticker=str(ticker or "unknown"),
        )

        self.order_notional_usd: Optional[Decimal] = getattr(config, "order_notional_usd", None)
        self._requested_leverage: Optional[Decimal] = getattr(config, "target_leverage", None)
        self._leverage_validator = LeverageValidator()
        self._applied_leverage: Optional[Decimal] = None
        self._max_symbol_leverage: Optional[Decimal] = None
        self._fallback_margin_ratio: Optional[Decimal] = None
        self._last_order_notional: Optional[Decimal] = None
        
        self.logger.log("Grid strategy initialized with parameters:", "INFO")
        self.logger.log(f"  - Take Profit: {config.take_profit}%", "INFO")
        self.logger.log(f"  - Grid Step: {config.grid_step}%", "INFO")
        self.logger.log(f"  - Direction: {config.direction}", "INFO")
        if self.order_notional_usd is not None:
            self.logger.log(f"  - Order Notional (USD): {self.order_notional_usd}", "INFO")
        else:
            qty_display = getattr(self.exchange_client.config, "quantity", None)
            self.logger.log(f"  - Order Quantity (base): {qty_display}", "INFO")
        self.logger.log(f"  - Max Orders: {config.max_orders}", "INFO")
        self.logger.log(f"  - Wait Time: {config.wait_time}s", "INFO")
        self.logger.log(f"  - Max Margin (USD): {config.max_margin_usd}", "INFO")
        self.logger.log(
            f"  - Stop Loss: {'enabled' if config.stop_loss_enabled else 'disabled'} "
            f"(threshold {config.stop_loss_percentage}%)",
            "INFO",
        )
        if self._requested_leverage is not None:
            self.logger.log(f"  - Target Leverage: {self._requested_leverage}x", "INFO")
        self.logger.log(
            f"  - Position Timeout: {config.position_timeout_minutes} minutes",
            "INFO",
        )
        self.logger.log(f"  - Recovery Mode: {config.recovery_mode}", "INFO")
        
        # Log safety parameters if set
        if config.stop_price is not None:
            self.logger.log(
                f"  - Stop Price: {config.stop_price} "
                f"(will stop if {'below' if config.direction == 'buy' else 'above'})", 
                "WARNING"
            )
        
        if config.pause_price is not None:
            self.logger.log(
                f"  - Pause Price: {config.pause_price} "
                f"(will pause if {'above' if config.direction == 'buy' else 'below'})", 
                "INFO"
            )
    
    def _direction_multiplier(self) -> Decimal:
        """Return +1 for buy direction, -1 for sell direction."""
        return Decimal("1") if self.config.direction == 'buy' else Decimal("-1")
    
    def _serialize_value(self, value: Any) -> Any:
        """Serialize payload values for structured logging."""
        if isinstance(value, Decimal):
            return float(value)
        if isinstance(value, (list, tuple)):
            return [self._serialize_value(item) for item in value]
        if isinstance(value, dict):
            return {key: self._serialize_value(val) for key, val in value.items()}
        return value
    
    def _log_event(self, event_type: str, message: str, level: str = "INFO", **context: Any) -> None:
        """Emit structured grid strategy event."""
        payload = {
            "event_type": event_type,
            "grid_direction": self.config.direction,
            **context,
        }
        serialized_payload = {key: self._serialize_value(val) for key, val in payload.items()}
        self.logger.log(message, level.upper(), **serialized_payload)

        if self.event_notifier:
            self.event_notifier.notify(
                event_type=event_type,
                level=level.upper(),
                message=message,
                payload=serialized_payload,
            )
    
    async def _fetch_position_snapshot(self) -> Optional[ExchangePositionSnapshot]:
        """Fetch the latest position snapshot from the exchange, trying multiple symbol hints."""
        symbols: List[Optional[str]] = [
            getattr(self.config, "ticker", None),
            getattr(self.exchange_client.config, "contract_id", None)
        ]
        
        for symbol in symbols:
            if not symbol:
                continue
            try:
                snapshot = await self.exchange_client.get_position_snapshot(symbol)
                if snapshot:
                    return snapshot
            except TypeError:
                # Some clients may not support the symbol format (skip to next hint)
                continue
            except Exception as exc:
                self.logger.log(
                    f"Grid: Failed to fetch position snapshot for {symbol}: {exc}",
                    "DEBUG"
                )
        return None
    
    def _calculate_margin_usage(
        self,
        snapshot: Optional[ExchangePositionSnapshot],
        current_position: Decimal,
        reference_price: Decimal
    ) -> Tuple[Decimal, Optional[Decimal]]:
        """
        Estimate current margin usage and margin ratio using exchange snapshot data when available.
        """
        abs_position = current_position.copy_abs() if isinstance(current_position, Decimal) else Decimal(str(current_position)).copy_abs()
        margin_used = Decimal("0")
        margin_ratio: Optional[Decimal] = None
        
        if snapshot:
            if getattr(snapshot, "margin_reserved", None) is not None:
                margin_used = snapshot.margin_reserved.copy_abs()
            elif getattr(snapshot, "exposure_usd", None) is not None:
                margin_used = snapshot.exposure_usd.copy_abs()
            else:
                margin_used = abs_position * reference_price
            
            exposure = getattr(snapshot, "exposure_usd", None)
            reserved = getattr(snapshot, "margin_reserved", None)
            if exposure and reserved:
                exposure_abs = exposure.copy_abs()
                if exposure_abs > 0:
                    margin_ratio = reserved.copy_abs() / exposure_abs
        else:
            margin_used = abs_position * reference_price
        
        return margin_used, margin_ratio

    async def _prepare_leverage_settings(self) -> None:
        """Fetch leverage limits and apply requested leverage if provided."""
        if not self.exchange_client:
            return
        
        ticker = getattr(self.config, "ticker", None) or getattr(self.exchange_client.config, "ticker", None)
        if not ticker:
            return
        
        try:
            leverage_info = await self._leverage_validator.get_leverage_info(self.exchange_client, ticker)
            self._max_symbol_leverage = leverage_info.max_leverage
            
            if leverage_info.max_leverage is not None:
                self.logger.log(
                    f"  - Exchange max leverage for {ticker}: {leverage_info.max_leverage}x",
                    "INFO",
                )
            
            applied: Optional[Decimal] = None
            if self._requested_leverage is not None:
                applied = self._requested_leverage
                if leverage_info.max_leverage is not None and applied > leverage_info.max_leverage:
                    self.logger.log(
                        f"Requested leverage {applied}x exceeds exchange maximum {leverage_info.max_leverage}x. "
                        "Using the maximum allowed leverage instead.",
                        "WARNING",
                    )
                    applied = leverage_info.max_leverage
                
                if applied and applied > 0 and hasattr(self.exchange_client, "set_account_leverage"):
                    leverage_to_set = max(int(applied), 1)
                    try:
                        set_success = await self.exchange_client.set_account_leverage(ticker, leverage_to_set)
                        if set_success:
                            self.logger.log(
                                f"Applied leverage {leverage_to_set}x on {self.exchange_client.get_exchange_name().upper()}.",
                                "INFO",
                            )
                            applied = Decimal(leverage_to_set)
                        else:
                            self.logger.log(
                                f"Failed to set leverage to {leverage_to_set}x on "
                                f"{self.exchange_client.get_exchange_name().upper()}.",
                                "WARNING",
                            )
                    except Exception as exc:
                        self.logger.log(
                            f"Error setting leverage on {self.exchange_client.get_exchange_name().upper()}: {exc}",
                            "WARNING",
                        )
            
            self._applied_leverage = applied
            if self._applied_leverage and self._applied_leverage > 0:
                self._fallback_margin_ratio = Decimal("1") / self._applied_leverage
            else:
                self._fallback_margin_ratio = None
        
        except Exception as exc:
            self.logger.log(
                f"Grid: Failed to prepare leverage settings for {ticker}: {exc}",
                "WARNING",
            )
    
    async def _prepare_risk_snapshot(
        self,
        reference_price: Decimal
    ) -> Tuple[Decimal, Optional[ExchangePositionSnapshot]]:
        """
        Refresh cached risk metrics (position, margin usage, margin ratio).
        """
        try:
            current_position = await self.exchange_client.get_account_positions()
        except Exception as exc:
            self.logger.log(f"Grid: Failed to fetch account positions: {exc}", "ERROR")
            current_position = Decimal("0")
        
        snapshot = await self._fetch_position_snapshot()
        margin_used, margin_ratio = self._calculate_margin_usage(snapshot, current_position, reference_price)
        
        self.grid_state.last_known_position = current_position
        self.grid_state.last_known_margin = margin_used
        self.grid_state.margin_ratio = margin_ratio
        
        if current_position == 0:
            self.grid_state.last_stop_loss_trigger = 0.0
        
        return current_position, snapshot
    
    async def _enforce_stop_loss(
        self,
        snapshot: Optional[ExchangePositionSnapshot],
        current_price: Decimal,
        current_position: Decimal
    ) -> bool:
        """
        Trigger a stop-loss exit if price has moved against the open position beyond the configured threshold.
        """
        if not self.config.stop_loss_enabled:
            return False
        
        if current_position == 0:
            # Reset timer when flat
            self.grid_state.last_stop_loss_trigger = 0.0
            return False
        
        entry_price = getattr(snapshot, "entry_price", None) if snapshot else None
        if entry_price is None or entry_price <= 0:
            return False
        
        try:
            stop_fraction = Decimal(str(self.config.stop_loss_percentage)) / Decimal("100")
        except Exception:
            stop_fraction = Decimal("0")
        
        if stop_fraction <= 0:
            return False
        
        now = time.time()
        if now - self.grid_state.last_stop_loss_trigger < 3:
            # Avoid spamming market orders if the previous stop-loss just fired
            return True
        
        position_side = "long" if current_position > 0 else "short"
        triggered = False
        if position_side == "long":
            stop_price = entry_price * (Decimal("1") - stop_fraction)
            if current_price <= stop_price:
                message = (
                    f"Price {current_price} breached long stop {stop_price} "
                    f"(entry {entry_price}, threshold {self.config.stop_loss_percentage}%)"
                )
                triggered = await self._close_position_market(current_position, message)
        else:
            stop_price = entry_price * (Decimal("1") + stop_fraction)
            if current_price >= stop_price:
                message = (
                    f"Price {current_price} breached short stop {stop_price} "
                    f"(entry {entry_price}, threshold {self.config.stop_loss_percentage}%)"
                )
                triggered = await self._close_position_market(current_position, message)
        
        if triggered:
            self.grid_state.last_stop_loss_trigger = now
            self.grid_state.last_open_order_time = now
            return True
        
        return False
    
    async def _close_position_market(self, current_position: Decimal, reason: str) -> bool:
        """Execute a market order to flatten the current position."""
        size = current_position.copy_abs()
        if size <= 0:
            return False
        
        close_side = 'sell' if current_position > 0 else 'buy'
        contract_id = getattr(self.exchange_client.config, "contract_id", None)
        
        self._log_event(
            "stop_loss_initiated",
            f"⚠️ GRID STOP LOSS | {reason}",
            level="WARNING",
            close_side=close_side,
            quantity=size,
        )
        try:
            order_result = await self.exchange_client.place_market_order(
                contract_id=contract_id,
                quantity=size,
                side=close_side
            )
        except Exception as exc:
            self._log_event(
                "stop_loss_error",
                f"Grid: Stop loss execution error: {exc}",
                level="ERROR",
                close_side=close_side,
                quantity=size,
                error=str(exc),
            )
            return False
        
        if getattr(order_result, "success", False):
            self._log_event(
                "stop_loss_executed",
                f"Grid: Stop loss market {close_side} for {size} succeeded",
                level="WARNING",
                close_side=close_side,
                quantity=size,
                order_id=getattr(order_result, "order_id", None),
            )
            await self._cancel_all_orders()
            self.grid_state.tracked_positions = []
            self.grid_state.last_known_position = Decimal("0")
            self.grid_state.last_known_margin = Decimal("0")
            self.grid_state.margin_ratio = None
            return True
        
        error_message = getattr(order_result, "error_message", "unknown error")
        self._log_event(
            "stop_loss_failed",
            f"Grid: Stop loss order failed - {error_message}",
            level="ERROR",
            close_side=close_side,
            quantity=size,
            order_id=getattr(order_result, "order_id", None),
            error=error_message,
        )
        return False
    
    async def _check_risk_limits(
        self,
        reference_price: Decimal,
        order_quantity: Decimal
    ) -> Tuple[bool, str]:
        """
        Ensure the next order stays within configured margin and position limits.
        """
        current_position = self.grid_state.last_known_position
        if current_position is None:
            try:
                current_position = await self.exchange_client.get_account_positions()
            except Exception:
                current_position = Decimal("0")
            self.grid_state.last_known_position = current_position
        
        current_margin = self.grid_state.last_known_margin or Decimal("0")
        margin_ratio = self.grid_state.margin_ratio
        notional = order_quantity.copy_abs() * reference_price
        required_margin = notional
        if (margin_ratio is None or margin_ratio <= 0) and self._fallback_margin_ratio:
            margin_ratio = self._fallback_margin_ratio
        if margin_ratio and margin_ratio > 0:
            required_margin *= margin_ratio
        
        projected_margin = current_margin + required_margin
        if projected_margin > self.config.max_margin_usd:
            projected_val = float(projected_margin)
            limit_val = float(self.config.max_margin_usd)
            message = (
                f"Grid: Margin cap reached (projected ${projected_val:.2f} > "
                f"limit ${limit_val:.2f}). Skipping new order."
            )
            self._log_event(
                "margin_cap_hit",
                message,
                level="WARNING",
                projected_margin=projected_margin,
                margin_limit=self.config.max_margin_usd,
                required_margin=required_margin,
                order_notional_usd=notional,
            )
            return False, message
        
        return True, "OK"
    
    def _prune_tracked_positions(self, active_order_ids: set[str]) -> None:
        """Remove tracked positions whose close orders are no longer active."""
        remaining: List[TrackedPosition] = []
        for tracked in self.grid_state.tracked_positions:
            if tracked.hedged:
                continue
            if not tracked.close_order_ids:
                continue
            if any(order_id in active_order_ids for order_id in tracked.close_order_ids):
                remaining.append(tracked)
        self.grid_state.tracked_positions = remaining
    
    async def _run_recovery_checks(self, current_price: Decimal) -> None:
        """Identify and recover positions that have been open longer than allowed."""
        if self.config.recovery_mode == "none":
            return
        
        if not self.grid_state.tracked_positions:
            return
        
        active_ids = {order.order_id for order in self.grid_state.active_close_orders}
        self._prune_tracked_positions(active_ids)
        
        if not self.grid_state.tracked_positions:
            return
        
        threshold_seconds = int(self.config.position_timeout_minutes) * 60
        now = time.time()
        remaining: List[TrackedPosition] = []
        
        for tracked in self.grid_state.tracked_positions:
            still_active = any(order_id in active_ids for order_id in tracked.close_order_ids)
            if not still_active:
                continue
            
            time_open = now - tracked.open_time
            if time_open < threshold_seconds:
                remaining.append(tracked)
                continue
            
            if now - tracked.last_recovery_time < 5:
                self._log_event(
                    "recovery_cooldown_active",
                    "Grid: Recovery cooldown active; deferring recovery attempt",
                    level="DEBUG",
                    side=tracked.side,
                    size=tracked.size,
                    time_open_seconds=time_open,
                    recovery_mode=self.config.recovery_mode,
                )
                remaining.append(tracked)
                continue
            
            self._log_event(
                "recovery_detected",
                (
                    f"Grid: Detected stuck position ({tracked.side}, {tracked.size} @ {tracked.entry_price}) "
                    f"open for {time_open:.0f}s. Attempting {self.config.recovery_mode} recovery."
                ),
                level="WARNING",
                side=tracked.side,
                size=tracked.size,
                entry_price=tracked.entry_price,
                time_open_seconds=time_open,
                recovery_mode=self.config.recovery_mode,
                recovery_attempts=tracked.recovery_attempts,
            )
            
            resolved = await self._recover_position(tracked, current_price)
            if not resolved:
                tracked.last_recovery_time = now
                tracked.recovery_attempts += 1
                remaining.append(tracked)
        
        self.grid_state.tracked_positions = remaining
    
    async def _recover_position(self, tracked: TrackedPosition, current_price: Decimal) -> bool:
        """Execute the configured recovery strategy for a stuck position."""
        mode = self.config.recovery_mode
        signed_position = tracked.size if tracked.side == 'long' else -tracked.size
        reason = (
            f"stuck {tracked.side} position ({tracked.size}) older than "
            f"{self.config.position_timeout_minutes} min"
        )
        
        if mode == "aggressive":
            await self._cancel_orders(tracked.close_order_ids)
            self._log_event(
                "recovery_aggressive_start",
                "Grid: Executing aggressive recovery via market close",
                level="WARNING",
                side=tracked.side,
                size=tracked.size,
            )
            success = await self._close_position_market(signed_position, f"Aggressive recovery - {reason}")
            return success
        
        if mode == "ladder":
            await self._cancel_orders(tracked.close_order_ids)
            self._log_event(
                "recovery_ladder_start",
                "Grid: Executing ladder recovery with staggered limit orders",
                level="WARNING",
                side=tracked.side,
                size=tracked.size,
            )
            ladder_order_ids = await self._place_ladder_orders(tracked, current_price)
            if ladder_order_ids:
                tracked.close_order_ids = ladder_order_ids
            return False
        
        if mode == "hedge":
            await self._cancel_orders(tracked.close_order_ids)
            self._log_event(
                "recovery_hedge_start",
                "Grid: Executing hedge recovery to neutralize exposure",
                level="WARNING",
                side=tracked.side,
                size=tracked.size,
            )
            success = await self._place_hedge_order(tracked)
            if success:
                self.grid_state.last_known_position = Decimal("0")
                self.grid_state.last_known_margin = Decimal("0")
                self.grid_state.margin_ratio = None
            return success
        
        return False
    
    async def _cancel_orders(self, order_ids: List[str]) -> None:
        """Cancel a specific set of orders, ignoring failures."""
        for order_id in order_ids:
            if not order_id:
                continue
            try:
                await self.exchange_client.cancel_order(order_id)
            except Exception as exc:
                self._log_event(
                    "recovery_cancel_failed",
                    f"Grid: Failed to cancel order {order_id} during recovery: {exc}",
                    level="ERROR",
                    order_id=order_id,
                    error=str(exc),
                )
    
    async def _place_ladder_orders(
        self,
        tracked: TrackedPosition,
        current_price: Decimal
    ) -> List[str]:
        """Place multiple staggered limit orders to unwind a stuck position."""
        contract_id = self.exchange_client.config.contract_id
        increments = [
            Decimal("0.015"),
            Decimal("0.03"),
            Decimal("0.045"),
        ]
        
        order_side = 'sell' if tracked.side == 'long' else 'buy'
        ladder_order_ids: List[str] = []
        
        for pct in increments:
            if tracked.side == 'long':
                target_price = current_price * (Decimal("1") + pct)
            else:
                target_price = current_price * (Decimal("1") - pct)
            
            target_price = self.exchange_client.round_to_tick(target_price)
            try:
                result = await self.exchange_client.place_close_order(
                    contract_id=contract_id,
                    quantity=tracked.size,
                    price=target_price,
                    side=order_side
                )
            except Exception as exc:
                self._log_event(
                    "recovery_ladder_order_error",
                    f"Grid: Ladder order placement failed ({order_side} @ {target_price}): {exc}",
                    level="ERROR",
                    side=order_side,
                    price=target_price,
                    size=tracked.size,
                    error=str(exc),
                )
                continue
            
            if getattr(result, "success", False) and result.order_id:
                ladder_order_ids.append(result.order_id)
                self._log_event(
                    "recovery_ladder_order_submitted",
                    "Grid: Submitted ladder recovery order",
                    level="WARNING",
                    side=order_side,
                    price=target_price,
                    size=tracked.size,
                    order_id=result.order_id,
                )
            else:
                self._log_event(
                    "recovery_ladder_order_rejected",
                    "Grid: Ladder recovery order was rejected or missing order ID",
                    level="ERROR",
                    side=order_side,
                    price=target_price,
                    size=tracked.size,
                    order_id=getattr(result, "order_id", None),
                    error=getattr(result, "error_message", None),
                )
        
        if ladder_order_ids:
            self._log_event(
                "recovery_ladder_orders_active",
                f"Grid: Placed {len(ladder_order_ids)} ladder recovery orders ({order_side})",
                level="WARNING",
                side=order_side,
                order_ids=ladder_order_ids,
            )
        else:
            self._log_event(
                "recovery_ladder_failed",
                "Grid: Failed to place ladder recovery orders; will retry later.",
                level="ERROR",
                side=order_side,
                size=tracked.size,
            )
        
        return ladder_order_ids
    
    async def _place_hedge_order(self, tracked: TrackedPosition) -> bool:
        """Place an opposite market order to neutralize exposure."""
        contract_id = self.exchange_client.config.contract_id
        hedge_side = 'sell' if tracked.side == 'long' else 'buy'
        try:
            result = await self.exchange_client.place_market_order(
                contract_id=contract_id,
                quantity=tracked.size,
                side=hedge_side
            )
        except Exception as exc:
            self._log_event(
                "recovery_hedge_error",
                f"Grid: Hedge order failed ({hedge_side} {tracked.size}): {exc}",
                level="ERROR",
                side=hedge_side,
                size=tracked.size,
                error=str(exc),
            )
            return False
        
        if getattr(result, "success", False):
            self._log_event(
                "recovery_hedge_executed",
                f"Grid: Hedge order {hedge_side} {tracked.size} executed to neutralize stuck position",
                level="WARNING",
                side=hedge_side,
                size=tracked.size,
                order_id=getattr(result, "order_id", None),
            )
            return True
        
        error_message = getattr(result, "error_message", "unknown error")
        self._log_event(
            "recovery_hedge_rejected",
            f"Grid: Hedge order rejected - {error_message}",
            level="ERROR",
            side=hedge_side,
            size=tracked.size,
            order_id=getattr(result, "order_id", None),
            error=error_message,
        )
        return False
    
    async def _initialize_strategy(self):
        """Initialize strategy (called by base class)."""
        await self._prepare_leverage_settings()
    
    async def should_execute(self) -> bool:
        """Determine if grid strategy should execute."""
        try:
            # Get current market data
            best_bid, best_ask = await self.exchange_client.fetch_bbo_prices(
                self.exchange_client.config.contract_id
            )
            current_price = (best_bid + best_ask) / 2
            
            # Check stop price - critical safety check first
            if self.config.stop_price is not None:
                if (self.config.direction == 'buy' and current_price < self.config.stop_price) or \
                   (self.config.direction == 'sell' and current_price > self.config.stop_price):
                    message = (
                        f"⚠️ STOP PRICE TRIGGERED! Current: {current_price}, Stop: {self.config.stop_price}"
                    )
                    self._log_event(
                        "stop_price_triggered",
                        message,
                        level="WARNING",
                        current_price=current_price,
                        stop_price=self.config.stop_price,
                    )
                    self._log_event(
                        "stop_price_shutdown",
                        "Canceling all orders and stopping strategy...",
                        level="WARNING",
                    )
                    await self._cancel_all_orders()
                    return False
            
            # Check pause price - temporary pause
            if self.config.pause_price is not None:
                if (self.config.direction == 'buy' and current_price > self.config.pause_price) or \
                   (self.config.direction == 'sell' and current_price < self.config.pause_price):
                    self._log_event(
                        "pause_price_triggered",
                        f"⏸️ PAUSE PRICE REACHED! Current: {current_price}, Pause: {self.config.pause_price}",
                        level="INFO",
                        current_price=current_price,
                        pause_price=self.config.pause_price,
                    )
                    return False
            
            current_position, snapshot = await self._prepare_risk_snapshot(current_price)
            if await self._enforce_stop_loss(snapshot, current_price, current_position):
                return False
            
            # Update active orders
            await self._update_active_orders()
            await self._run_recovery_checks(current_price)
            
            # Check if we should wait based on cooldown
            wait_time = self._calculate_wait_time()
            if wait_time > 0:
                return False
            
            # Check grid step condition
            return await self._meet_grid_step_condition(best_bid, best_ask)
            
        except Exception as e:
            self._log_event(
                "should_execute_error",
                f"Error in should_execute: {e}",
                level="ERROR",
                error=str(e),
            )
            return False
    
    async def execute_strategy(self, market_data=None) -> Dict[str, Any]:
        """
        Execute grid strategy using state machine pattern.
        
        Grid cycle states:
        1. 'READY' → Place open order
        2. 'WAITING_FOR_FILL' → Check if filled, then place close order  
        3. 'COMPLETE' → Reset state
        
        Args:
            market_data: Optional market data (not used, grid fetches its own)
        
        Returns:
            Dictionary with execution result
        """
        try:
            # State 1: Ready to place open order
            if self.grid_state.cycle_state == GridCycleState.READY:
                return await self._place_open_order()
            
            # State 2: Waiting for fill, then place close order
            elif self.grid_state.cycle_state == GridCycleState.WAITING_FOR_FILL:
                return await self._handle_filled_order()
            
            else:
                # Unknown state, reset
                self.grid_state.cycle_state = GridCycleState.READY
                return {
                    'action': 'wait',
                    'message': 'Grid: Resetting cycle state',
                    'wait_time': 1
                }
                
        except Exception as e:
            self._log_event(
                "execute_strategy_error",
                f"Error executing grid strategy: {e}",
                level="ERROR",
                error=str(e),
            )
            # Reset state on error
            self.grid_state.cycle_state = GridCycleState.READY
            return {
                'action': 'error',
                'message': f'Grid strategy error: {e}',
                'wait_time': 5
            }

    def _determine_order_quantity(self, reference_price: Decimal) -> Tuple[Decimal, Decimal]:
        """
        Calculate the per-order quantity based on configured notional or fallback quantity.

        Returns:
            Tuple of (quantity, applied_notional_usd)
        """
        if reference_price <= 0:
            raise ValueError("Reference price must be positive to compute order quantity")
        
        if self.order_notional_usd is None:
            raw_quantity = getattr(self.exchange_client.config, "quantity", None)
            if raw_quantity is None:
                raise ValueError("Exchange config missing 'quantity' for grid strategy")
            quantity = Decimal(str(raw_quantity))
            if quantity <= 0:
                raise ValueError(f"Invalid order quantity: {quantity}")
            applied_notional = quantity * reference_price
            self._last_order_notional = applied_notional
            return quantity, applied_notional
        
        target_notional = Decimal(str(self.order_notional_usd))
        min_notional = getattr(self.exchange_client.config, "min_order_notional", None)
        adjusted_notional = target_notional
        
        if min_notional is not None:
            min_notional_dec = Decimal(str(min_notional))
            if target_notional < min_notional_dec:
                self.logger.log(
                    f"Requested order notional ${float(target_notional):.2f} is below "
                    f"exchange minimum ${float(min_notional_dec):.2f}. Using the minimum allowed.",
                    "WARNING",
                )
                adjusted_notional = min_notional_dec
        
        quantity = adjusted_notional / reference_price
        quantity = self.exchange_client.round_to_step(quantity)
        if quantity <= 0:
            raise ValueError(
                "Computed order quantity rounded to zero. Increase `order_notional_usd` to satisfy exchange step size."
            )
        
        applied_notional = quantity * reference_price
        setattr(self.exchange_client.config, "quantity", quantity)
        self._last_order_notional = applied_notional
        return quantity, applied_notional
    
    async def _place_open_order(self) -> Dict[str, Any]:
        """Place an open order to enter a new grid level."""
        try:
            contract_id = self.exchange_client.config.contract_id
            
            # Get aggressive price (act like market order but with price protection)
            best_bid, best_ask = await self.exchange_client.fetch_bbo_prices(contract_id)
            reference_price = (best_bid + best_ask) / 2
            
            try:
                quantity, applied_notional = self._determine_order_quantity(reference_price)
            except ValueError as exc:
                self.logger.log(f"Grid: Unable to determine order size - {exc}", "WARNING")
                return {
                    'action': 'wait',
                    'message': str(exc),
                    'wait_time': max(1, float(self.config.wait_time))
                }
            
            tick = self.exchange_client.config.tick_size
            offset_multiplier = getattr(self.config, "post_only_tick_multiplier", Decimal("2"))
            offset = tick * offset_multiplier  # configurable distance for post-only placement
            if self.config.direction == 'buy':
                # For buy, place a couple ticks below the ask to respect post-only
                order_price = best_ask - offset
                if order_price <= 0:
                    order_price = tick
            else:  # sell
                # For sell, place above the bid by a couple ticks
                order_price = best_bid + offset
            
            order_price = self.exchange_client.round_to_tick(order_price)
            
            risk_ok, risk_message = await self._check_risk_limits(reference_price, quantity)
            if not risk_ok:
                return {
                    'action': 'wait',
                    'message': risk_message,
                    'wait_time': max(1, float(self.config.wait_time))
                }
            
            order_result = await self.exchange_client.place_limit_order(
                contract_id=contract_id,
                quantity=quantity,
                price=order_price,
                side=self.config.direction
            )
            
            if order_result.success:
                # Update state to waiting for fill
                self.grid_state.cycle_state = GridCycleState.WAITING_FOR_FILL
                
                # Record filled price and quantity from result
                if order_result.status == 'FILLED':
                    self.grid_state.filled_price = order_result.price
                    self.grid_state.filled_quantity = order_result.size
                
                self.logger.log(
                    f"Grid: Placed {self.config.direction} order for {quantity} "
                    f"(@ ~${float(applied_notional):.2f}) at {order_result.price}",
                    "INFO"
                )
                
                return {
                    'action': 'order_placed',
                    'order_id': order_result.order_id,
                    'side': self.config.direction,
                    'quantity': quantity,
                    'price': order_result.price,
                    'notional_usd': applied_notional,
                    'status': order_result.status
                }
            else:
                self.logger.log(f"Grid: Failed to place open order: {order_result.error_message}", "ERROR")
                return {
                    'action': 'error',
                    'message': order_result.error_message,
                    'wait_time': 5
                }
                
        except Exception as e:
            self.logger.log(f"Error placing open order: {e}", "ERROR")
            return {
                'action': 'error',
                'message': str(e),
                'wait_time': 5
            }
    
    async def _handle_filled_order(self) -> Dict[str, Any]:
        """Handle a filled open order by placing corresponding close order."""
        # Check if we have filled price (may be set by notify_order_filled callback)
        if self.grid_state.filled_price and self.grid_state.filled_quantity:
            try:
                # Calculate close order parameters
                close_side = 'sell' if self.config.direction == 'buy' else 'buy'
                close_price = self._calculate_close_price(self.grid_state.filled_price)
                
                # Place close order
                if self.config.boost_mode:
                    # Boost mode: use market order for faster execution
                    order_result = await self.exchange_client.place_market_order(
                        contract_id=self.exchange_client.config.contract_id,
                        quantity=self.grid_state.filled_quantity,
                        side=close_side
                    )
                else:
                    # Normal mode: use limit order
                    order_result = await self.exchange_client.place_close_order(
                        contract_id=self.exchange_client.config.contract_id,
                        quantity=self.grid_state.filled_quantity,
                        price=close_price,
                        side=close_side
                    )
                
                if order_result.success:
                    entry_price = self.grid_state.filled_price or order_result.price or close_price
                    position_size = self.grid_state.filled_quantity or order_result.size
                    side = 'long' if self.config.direction == 'buy' else 'short'
                    tracked_position = None
                    if position_size and order_result.status != 'FILLED':
                        tracked_position = TrackedPosition(
                            entry_price=Decimal(str(entry_price)),
                            size=Decimal(str(position_size)),
                            side=side,
                            open_time=time.time(),
                            close_order_ids=[order_result.order_id] if order_result.order_id else [],
                        )
                        self.grid_state.tracked_positions.append(tracked_position)
                        self._log_event(
                            "position_tracked",
                            "Grid: Tracking position for recovery monitoring",
                            level="INFO",
                            side=side,
                            size=position_size,
                            entry_price=entry_price,
                            close_order_ids=tracked_position.close_order_ids,
                        )
                    
                    # Reset state for next cycle
                    self.grid_state.cycle_state = GridCycleState.READY
                    self.grid_state.filled_price = None
                    self.grid_state.filled_quantity = None
                    self.grid_state.last_open_order_time = time.time()
                    
                    self.logger.log(
                        f"Grid: Placed close order at {close_price}",
                        "INFO"
                    )
                    
                    return {
                        'action': 'order_placed',
                        'order_id': order_result.order_id,
                        'side': close_side,
                        'quantity': self.grid_state.filled_quantity,
                        'price': close_price,
                        'wait_time': 1 if self.exchange_client.get_exchange_name() == "lighter" else 0
                    }
                else:
                    self.logger.log(f"Grid: Failed to place close order: {order_result.error_message}", "ERROR")
                    return {
                        'action': 'error',
                        'message': order_result.error_message,
                        'wait_time': 5
                    }
                    
            except Exception as e:
                self.logger.log(f"Error placing close order: {e}", "ERROR")
                return {
                    'action': 'error',
                    'message': str(e),
                    'wait_time': 5
                }
        else:
            # Still waiting for fill notification
            return {
                'action': 'wait',
                'message': 'Grid: Waiting for open order to fill',
                'wait_time': 0.5
            }
    
    def notify_order_filled(self, filled_price: Decimal, filled_quantity: Decimal):
        """
        Notify strategy that an order was filled.
        
        This is called by the trading bot after successful order execution.
        """
        self.grid_state.filled_price = filled_price
        self.grid_state.filled_quantity = filled_quantity
        self.logger.log(
            f"Grid: Order filled at {filled_price} for {filled_quantity}",
            "INFO"
        )
    
    def _calculate_close_price(self, filled_price: Decimal) -> Decimal:
        """Calculate the close price based on take-profit settings."""
        take_profit_pct = self.config.take_profit
        
        # Calculate close price
        if self.config.direction == 'buy':
            # Long position: sell higher
            close_price = filled_price * (1 + take_profit_pct / 100)
        else:
            # Short position: buy back lower
            close_price = filled_price * (1 - take_profit_pct / 100)
        
        return close_price
    
    async def _update_active_orders(self):
        """Update active close orders."""
        try:
            active_orders = await self.exchange_client.get_active_orders(
                self.exchange_client.config.contract_id
            )
            
            # Filter close orders (opposite side of direction)
            close_side = 'sell' if self.config.direction == 'buy' else 'buy'
            
            self.grid_state.active_close_orders = [
                GridOrder(
                    order_id=order.order_id,
                    price=order.price,
                    size=order.size,
                    side=order.side
                )
                for order in active_orders
                if order.side == close_side
            ]
            
        except Exception as e:
            self.logger.log(f"Error updating active orders: {e}", "ERROR")
    
    def _calculate_wait_time(self) -> float:
        """Calculate wait time between orders based on order density."""
        active_count = len(self.grid_state.active_close_orders)
        last_count = self.grid_state.last_close_orders_count
        
        # If orders decreased (one filled), allow immediate new order
        if active_count < last_count:
            self.grid_state.last_close_orders_count = active_count
            return 0
        
        self.grid_state.last_close_orders_count = active_count
        
        # If at max capacity, wait
        if active_count >= self.config.max_orders:
            return 1
        
        # Dynamic cooldown based on order density
        order_ratio = active_count / self.config.max_orders
        if order_ratio >= 2/3:
            cool_down_time = 2 * self.config.wait_time
        elif order_ratio >= 1/3:
            cool_down_time = self.config.wait_time
        elif order_ratio >= 1/6:
            cool_down_time = self.config.wait_time / 2
        else:
            cool_down_time = self.config.wait_time / 4
        
        # Handle startup with existing orders
        if self.grid_state.last_open_order_time == 0 and active_count > 0:
            self.grid_state.last_open_order_time = time.time()
        
        # Check if cooldown period has passed
        current_time = time.time()
        if current_time - self.grid_state.last_open_order_time > cool_down_time:
            return 0
        else:
            return 1
    
    async def _meet_grid_step_condition(self, best_bid: Decimal, best_ask: Decimal) -> bool:
        """Check if grid step condition is met."""
        if not self.grid_state.active_close_orders:
            return True
        
        try:
            # Find the next close order price
            if self.config.direction == "buy":
                # Long: find lowest close (sell) order
                next_close_order = min(
                    self.grid_state.active_close_orders, 
                    key=lambda o: o.price
                )
                next_close_price = next_close_order.price
                
                # Calculate new order close price
                new_order_close_price = best_ask * (1 + self.config.take_profit / 100)
                
                # Check if there's enough gap
                return next_close_price / new_order_close_price > 1 + self.config.grid_step / 100
                
            else:  # sell direction
                # Short: find highest close (buy) order
                next_close_order = max(
                    self.grid_state.active_close_orders, 
                    key=lambda o: o.price
                )
                next_close_price = next_close_order.price
                
                # Calculate new order close price
                new_order_close_price = best_bid * (1 - self.config.take_profit / 100)
                
                # Check if there's enough gap
                return new_order_close_price / next_close_price > 1 + self.config.grid_step / 100
                
        except Exception as e:
            self.logger.log(f"Error in grid step condition: {e}", "ERROR")
            return False
    
    async def _cancel_all_orders(self):
        """Cancel all active orders (used when stop price is triggered)."""
        try:
            self.logger.log(
                f"Canceling {len(self.grid_state.active_close_orders)} active orders...", 
                "INFO"
            )
            
            for order in self.grid_state.active_close_orders:
                try:
                    await self.exchange_client.cancel_order(order.order_id)
                    self.logger.log(f"Canceled order {order.order_id}", "INFO")
                except Exception as e:
                    self.logger.log(f"Error canceling order {order.order_id}: {e}", "ERROR")
            
            # Clear active close orders from state
            self.grid_state.active_close_orders = []
            self.logger.log("All orders canceled", "INFO")
            
        except Exception as e:
            self.logger.log(f"Error in _cancel_all_orders: {e}", "ERROR")
    
    async def get_status(self) -> Dict[str, Any]:
        """Get current strategy status."""
        try:
            position = await self.exchange_client.get_account_positions()
            active_close_amount = sum(order.size for order in self.grid_state.active_close_orders)
            
            return {
                "strategy": "grid",
                "cycle_state": self.grid_state.cycle_state.value,
                "active_orders": len(self.grid_state.active_close_orders),
                "position": float(position),
                "margin_used": float(self.grid_state.last_known_margin),
                "margin_limit": float(self.config.max_margin_usd),
                "margin_ratio": float(self.grid_state.margin_ratio) if self.grid_state.margin_ratio is not None else None,
                "active_close_amount": float(active_close_amount),
                "last_order_time": self.grid_state.last_open_order_time,
                "parameters": {
                    "take_profit": float(self.config.take_profit),
                    "grid_step": float(self.config.grid_step),
                    "direction": self.config.direction,
                    "max_orders": self.config.max_orders
                }
            }
        except Exception as e:
            return {
                "strategy": "grid",
                "error": str(e)
            }
    
    def get_strategy_name(self) -> str:
        """Get the strategy name."""
        return "Grid Trading"
    
    def get_required_parameters(self) -> List[str]:
        """Get list of required strategy parameters."""
        return [
            "take_profit",
            "grid_step",
            "direction",
            "max_orders",
            "wait_time"
        ]
