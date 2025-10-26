"""
Close-position orchestration for the grid strategy.
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Any, Dict, Optional

from ..config import GridConfig
from ..models import GridCycleState, GridOrder, GridState, TrackedPosition
from ..position_manager import GridPositionManager


class GridOrderCloser:
    """Manage exit logic (close orders, cancellations, stop-loss market exits)."""

    def __init__(
        self,
        config: GridConfig,
        exchange_client,
        grid_state: GridState,
        logger,
        log_event,
        position_manager: GridPositionManager,
    ) -> None:
        self.config = config
        self.exchange_client = exchange_client
        self.grid_state = grid_state
        self.logger = logger
        self._log_event = log_event
        self.position_manager = position_manager

    async def market_close(self, current_position: Decimal, reason: str) -> bool:
        """Execute a market order to flatten the current position."""
        size = current_position.copy_abs()
        if size <= 0:
            return False

        close_side = "sell" if current_position > 0 else "buy"
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
                side=close_side,
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
            await self.cancel_all_orders()
            self.position_manager.clear()
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

    async def handle_filled_order(self) -> Dict[str, Any]:
        """Handle a filled open order by placing corresponding close order."""
        if self.grid_state.filled_price and self.grid_state.filled_quantity:
            try:
                close_side = "sell" if self.config.direction == "buy" else "buy"
                close_price = self._calculate_close_price(self.grid_state.filled_price)

                if self.config.boost_mode:
                    # Boost mode: use market order for faster execution
                    order_result = await self.exchange_client.place_market_order(
                        contract_id=self.exchange_client.config.contract_id,
                        quantity=self.grid_state.filled_quantity,
                        side=close_side,
                    )
                else:
                    # Normal mode: use limit order (reduce only so we do not add exposure)
                    order_result = await self.exchange_client.place_limit_order(
                        contract_id=self.exchange_client.config.contract_id,
                        quantity=self.grid_state.filled_quantity,
                        price=close_price,
                        side=close_side,
                        reduce_only=True,
                    )

                if order_result.success:
                    entry_price = self.grid_state.filled_price or order_result.price or close_price
                    position_size = self.grid_state.filled_quantity or order_result.size
                    side = "long" if self.config.direction == "buy" else "short"
                    if position_size and order_result.status != "FILLED":
                        tracked_position = TrackedPosition(
                            entry_price=Decimal(str(entry_price)),
                            size=Decimal(str(position_size)),
                            side=side,
                            open_time=time.time(),
                            close_order_ids=[order_result.order_id] if order_result.order_id else [],
                        )
                        self.position_manager.track(tracked_position)
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
                    self.grid_state.pending_open_order_id = None
                    self.grid_state.pending_open_quantity = None
                    self.grid_state.last_open_order_time = time.time()

                    self.logger.log(
                        f"Grid: Placed close order at {close_price}",
                        "INFO",
                    )

                    return {
                        "action": "order_placed",
                        "order_id": order_result.order_id,
                        "side": close_side,
                        "quantity": self.grid_state.filled_quantity,
                        "price": close_price,
                        "wait_time": 1 if self.exchange_client.get_exchange_name() == "lighter" else 0,
                    }

                self.logger.log(f"Grid: Failed to place close order: {order_result.error_message}", "ERROR")
                return {
                    "action": "error",
                    "message": order_result.error_message,
                    "wait_time": 5,
                }

            except Exception as exc:
                self.logger.log(f"Error placing close order: {exc}", "ERROR")
                return {
                    "action": "error",
                    "message": str(exc),
                    "wait_time": 5,
                }

        # Still waiting for fill notification
        return {
            "action": "wait",
            "message": "Grid: Waiting for open order to fill",
            "wait_time": 0.5,
        }

    def notify_order_filled(self, filled_price: Decimal, filled_quantity: Decimal) -> None:
        """
        Notify strategy that an order was filled.

        This is called by the trading bot after successful order execution.
        """
        self.grid_state.filled_price = filled_price
        self.grid_state.filled_quantity = filled_quantity
        self.grid_state.pending_open_order_id = None
        self.grid_state.pending_open_quantity = None
        self.logger.log(
            f"Grid: Order filled at {filled_price} for {filled_quantity}",
            "INFO",
        )

    def _calculate_close_price(self, filled_price: Decimal) -> Decimal:
        """Calculate the close price based on desired profit on position margin."""
        multiplier = self.get_take_profit_multiplier()
        return filled_price * multiplier

    def get_take_profit_multiplier(self) -> Decimal:
        """
        Resolve the price multiplier required to achieve the configured take-profit
        as a return on margin rather than as a raw price change.

        For longs (direction == "buy") the multiplier is > 1.
        For shorts (direction == "sell") the multiplier is < 1.
        """
        fraction = self._take_profit_fraction()
        one = Decimal("1")

        if self.config.direction == "buy":
            return one + fraction

        multiplier = one - fraction
        return multiplier if multiplier > Decimal("0") else Decimal("0")

    def _take_profit_fraction(self) -> Decimal:
        """
        Convert the configured take-profit percentage into a price delta fraction that
        incorporates leverage by scaling with the effective margin ratio.
        """
        margin_ratio = self._resolve_margin_ratio()
        take_profit_pct = self.config.take_profit
        fraction = (take_profit_pct / Decimal("100")) * margin_ratio
        return fraction if fraction > Decimal("0") else Decimal("0")

    def _resolve_margin_ratio(self) -> Decimal:
        """
        Determine the margin ratio (margin / notional) to use when converting between
        desired profit percentage and equivalent price movement.
        """
        margin_ratio = self.grid_state.margin_ratio
        if margin_ratio is not None and margin_ratio > Decimal("0"):
            return margin_ratio

        target_leverage = getattr(self.config, "target_leverage", None)
        if target_leverage:
            leverage_value = Decimal(str(target_leverage))
            if leverage_value > Decimal("0"):
                return Decimal("1") / leverage_value

        return Decimal("1")

    async def update_active_orders(self) -> None:
        """Update active close orders."""
        try:
            active_orders = await self.exchange_client.get_active_orders(
                self.exchange_client.config.contract_id
            )

            # Filter close orders (opposite side of direction)
            close_side = "sell" if self.config.direction == "buy" else "buy"

            self.grid_state.active_close_orders = [
                GridOrder(
                    order_id=order.order_id,
                    price=order.price,
                    size=order.size,
                    side=order.side,
                )
                for order in active_orders
                if order.side == close_side
            ]

        except Exception as exc:
            self.logger.log(f"Error updating active orders: {exc}", "ERROR")

    async def cancel_all_orders(self) -> None:
        """Cancel all active orders (used when stop price is triggered)."""
        try:
            self.logger.log(
                f"Canceling {len(self.grid_state.active_close_orders)} active orders...",
                "INFO",
            )

            for order in self.grid_state.active_close_orders:
                try:
                    await self.exchange_client.cancel_order(order.order_id)
                    self.logger.log(f"Canceled order {order.order_id}", "INFO")
                except Exception as exc:
                    self.logger.log(f"Error canceling order {order.order_id}: {exc}", "ERROR")

            # Clear active close orders from state
            self.grid_state.active_close_orders = []
            self.logger.log("All orders canceled", "INFO")

        except Exception as exc:
            self.logger.log(f"Error in cancel_all_orders: {exc}", "ERROR")
