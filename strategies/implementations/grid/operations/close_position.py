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
from ..utils import client_order_index_from_position

# Internal retry limits for post-only close order handling.
POST_ONLY_CLOSE_RETRY_LIMIT = 3
POST_ONLY_CLOSE_RETRY_BACKOFF_SECONDS = 0.25
POST_ONLY_CLOSE_MARKET_FALLBACK = True


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

    async def market_close(
        self,
        current_position: Decimal,
        reason: str,
        tracked_position: Optional[TrackedPosition] = None,
    ) -> bool:
        """Execute a market order to flatten the current (or specific) position."""
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
            if tracked_position is not None:
                await self._finalize_tracked_market_close(tracked_position)
            else:
                await self.cancel_all_orders()
                self.position_manager.clear()
                self.grid_state.last_known_position = Decimal("0")
                self.grid_state.last_known_margin = Decimal("0")
                self.grid_state.margin_ratio = None
                self.grid_state.pending_position_id = None
                self.grid_state.filled_position_id = None
                self.grid_state.pending_client_order_index = None
                self.grid_state.filled_client_order_index = None
                self.grid_state.order_index_to_position_id.clear()
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

    async def _finalize_tracked_market_close(self, tracked: TrackedPosition) -> None:
        """Cleanup bookkeeping after a targeted market close."""
        cancel_ids = [order_id for order_id in tracked.close_order_ids or [] if order_id]
        for order_id in cancel_ids:
            try:
                await self.exchange_client.cancel_order(order_id)
            except Exception as exc:  # pragma: no cover - defensive logging
                self.logger.log(
                    f"Grid: Failed to cancel close order {order_id} after market close: {exc}",
                    "DEBUG",
                )

        if cancel_ids:
            cancel_set = set(cancel_ids)
            self.grid_state.active_close_orders = [
                order for order in self.grid_state.active_close_orders if order.order_id not in cancel_set
            ]

        if tracked.entry_client_order_index is not None:
            self.grid_state.order_index_to_position_id.pop(tracked.entry_client_order_index, None)
            if self.grid_state.pending_client_order_index == tracked.entry_client_order_index:
                self.grid_state.pending_client_order_index = None
            if self.grid_state.filled_client_order_index == tracked.entry_client_order_index:
                self.grid_state.filled_client_order_index = None

        for idx in tracked.close_client_order_indices or []:
            self.grid_state.order_index_to_position_id.pop(idx, None)

        if self.grid_state.pending_position_id == tracked.position_id:
            self.grid_state.pending_position_id = None
        if self.grid_state.filled_position_id == tracked.position_id:
            self.grid_state.filled_position_id = None

        self.position_manager.remove(tracked.position_id)

    async def handle_filled_order(self) -> Dict[str, Any]:
        """Handle a filled open order by placing corresponding close order."""
        if self.grid_state.filled_price and self.grid_state.filled_quantity:
            try:
                position_id = (
                    self.grid_state.filled_position_id
                    or self.grid_state.pending_position_id
                )
                entry_client_index = (
                    self.grid_state.filled_client_order_index
                    or self.grid_state.pending_client_order_index
                )
                if entry_client_index is None:
                    for idx, mapped_id in self.grid_state.order_index_to_position_id.items():
                        if mapped_id == position_id:
                            entry_client_index = idx
                            break
                if position_id is None and entry_client_index is not None:
                    position_id = self.grid_state.order_index_to_position_id.get(entry_client_index)
                if position_id is None:
                    position_id = self.grid_state.allocate_position_id()
                if entry_client_index is None:
                    entry_client_index = client_order_index_from_position(position_id, "entry")
                self.grid_state.order_index_to_position_id.setdefault(entry_client_index, position_id)
                close_side = "sell" if self.config.direction == "buy" else "buy"
                close_price = self._calculate_close_price(self.grid_state.filled_price)
                close_client_index = client_order_index_from_position(position_id, "close")

                if self.config.boost_mode:
                    # Boost mode: use market order for faster execution
                    order_result = await self.exchange_client.place_market_order(
                        contract_id=self.exchange_client.config.contract_id,
                        quantity=self.grid_state.filled_quantity,
                        side=close_side,
                        client_order_id=close_client_index,
                    )
                else:
                    # Normal mode: use limit order (reduce only so we do not add exposure)
                    order_result = await self.exchange_client.place_limit_order(
                        contract_id=self.exchange_client.config.contract_id,
                        quantity=self.grid_state.filled_quantity,
                        price=close_price,
                        side=close_side,
                        reduce_only=True,
                        client_order_id=close_client_index,
                    )

                if order_result.success:
                    entry_price = self.grid_state.filled_price or order_result.price or close_price
                    position_size = self.grid_state.filled_quantity or order_result.size
                    side = "long" if self.config.direction == "buy" else "short"
                    if position_size and order_result.status != "FILLED":
                        tracked_position = TrackedPosition(
                            position_id=position_id,
                            entry_price=Decimal(str(entry_price)),
                            size=Decimal(str(position_size)),
                            side=side,
                            open_time=time.time(),
                            close_order_ids=[order_result.order_id] if order_result.order_id else [],
                            entry_client_order_index=entry_client_index,
                            close_client_order_indices=[close_client_index],
                            post_only_retry_count=0,
                            last_post_only_retry=0.0,
                        )
                        self.position_manager.track(tracked_position)
                        self._log_event(
                            "position_tracked",
                            "Grid: Tracking position for recovery monitoring",
                            level="INFO",
                            position_id=position_id,
                            side=side,
                            size=position_size,
                            entry_price=entry_price,
                            close_order_ids=tracked_position.close_order_ids,
                            close_client_order_indices=tracked_position.close_client_order_indices,
                        )

                    # Reset state for next cycle
                    self.grid_state.cycle_state = GridCycleState.READY
                    self.grid_state.filled_price = None
                    self.grid_state.filled_quantity = None
                    self.grid_state.filled_position_id = None
                    self.grid_state.filled_client_order_index = None
                    self.grid_state.pending_open_order_id = None
                    self.grid_state.pending_open_quantity = None
                    self.grid_state.pending_open_order_time = None
                    self.grid_state.pending_position_id = None
                    if entry_client_index is not None:
                        self.grid_state.order_index_to_position_id.pop(entry_client_index, None)
                    self.grid_state.pending_client_order_index = None
                    self.grid_state.last_open_order_time = time.time()

                    self.logger.log(
                        f"Grid: Placed close order at {close_price} for position {position_id}",
                        "INFO",
                    )
                    
                    # Log position entry completion with visual separator
                    self.logger.log(
                        f"\n{'-'*80}\n✅ Position {position_id} ENTRY COMPLETE | CLOSE ORDER PLACED | Waiting for exit @ {close_price}\n{'-'*80}",
                        "INFO",
                    )

                    return {
                        "action": "order_placed",
                        "order_id": order_result.order_id,
                        "position_id": position_id,
                        "side": close_side,
                        "quantity": position_size,
                        "price": close_price,
                        "wait_time": 1 if self.exchange_client.get_exchange_name() == "lighter" else 0,
                    }

                self.logger.log(
                    f"Grid: Failed to place close order for position {position_id}: {order_result.error_message}",
                    "ERROR",
                )
                return {
                    "action": "error",
                    "message": order_result.error_message,
                    "position_id": position_id,
                    "wait_time": 5,
                }

            except Exception as exc:
                self.logger.log(f"Error placing close order for position {position_id}: {exc}", "ERROR")
                return {
                    "action": "error",
                    "message": str(exc),
                    "position_id": position_id,
                    "wait_time": 5,
                }

        # Still waiting for fill notification
        return {
            "action": "wait",
            "message": "Grid: Waiting for open order to fill",
                    "wait_time": 0.5,
        }

    async def ensure_close_orders(
        self,
        current_position: Decimal,
        best_bid: Optional[Decimal],
        best_ask: Optional[Decimal],
    ) -> None:
        """Repost cancelled close orders when they violate the post-only rule."""
        if self.position_manager.count() == 0 or current_position == 0:
            return

        active_ids = {order.order_id for order in self.grid_state.active_close_orders}
        now = time.time()
        retry_limit = POST_ONLY_CLOSE_RETRY_LIMIT
        use_market_fallback = POST_ONLY_CLOSE_MARKET_FALLBACK
        backoff = POST_ONLY_CLOSE_RETRY_BACKOFF_SECONDS

        for tracked in self.position_manager.all():
            if tracked.hedged or tracked.size <= 0:
                continue

            if tracked.close_order_ids and any(order_id in active_ids for order_id in tracked.close_order_ids):
                tracked.post_only_retry_count = 0
                continue

            if now - tracked.last_post_only_retry < backoff:
                continue

            if tracked.post_only_retry_count >= retry_limit:
                if use_market_fallback:
                    signed_position = tracked.size if tracked.side == "long" else -tracked.size
                    self._log_event(
                        "close_order_retry_limit_exceeded",
                        (
                            f"Grid: Close order retries exhausted for {tracked.position_id}; "
                            "falling back to market exit."
                        ),
                        level="WARNING",
                        position_id=tracked.position_id,
                        retries=tracked.post_only_retry_count,
                    )
                    await self.market_close(
                        signed_position,
                        f"Post-only close retries exceeded for {tracked.position_id}",
                        tracked_position=tracked,
                    )
                    tracked.post_only_retry_count = retry_limit + 1
                else:
                    self._log_event(
                        "close_order_retry_limit_exceeded",
                        (
                            f"Grid: Close order retries exhausted for {tracked.position_id}; "
                            "manual intervention required."
                        ),
                        level="ERROR",
                        position_id=tracked.position_id,
                        retries=tracked.post_only_retry_count,
                    )
                    tracked.last_post_only_retry = now
                    tracked.post_only_retry_count = retry_limit + 1
                continue

            attempt = tracked.post_only_retry_count + 1
            close_side = "sell" if tracked.side == "long" else "buy"
            close_price = self._compute_retry_close_price(tracked, best_bid, best_ask, attempt)
            close_client_index = client_order_index_from_position(
                tracked.position_id,
                f"close-retry-{attempt}",
            )

            try:
                order_result = await self.exchange_client.place_limit_order(
                    contract_id=self.exchange_client.config.contract_id,
                    quantity=tracked.size,
                    price=close_price,
                    side=close_side,
                    reduce_only=True,
                    client_order_id=close_client_index,
                )
            except Exception as exc:  # pragma: no cover - defensive logging
                self._log_event(
                    "close_order_retry_error",
                    f"Grid: Failed to repost close order for {tracked.position_id}: {exc}",
                    level="ERROR",
                    position_id=tracked.position_id,
                    side=close_side,
                    price=close_price,
                    retries=attempt,
                    error=str(exc),
                )
                tracked.post_only_retry_count = attempt
                tracked.last_post_only_retry = now
                continue

            tracked.post_only_retry_count = attempt
            tracked.last_post_only_retry = now

            if order_result.success:
                new_order_id = order_result.order_id or str(close_client_index)
                prev_ids = set(tracked.close_order_ids or [])
                tracked.close_order_ids = [new_order_id]
                if close_client_index not in tracked.close_client_order_indices:
                    tracked.close_client_order_indices.append(close_client_index)
                self.grid_state.order_index_to_position_id[close_client_index] = tracked.position_id

                new_order = GridOrder(
                    order_id=new_order_id,
                    price=order_result.price or close_price,
                    size=tracked.size,
                    side=close_side,
                )
                self.grid_state.active_close_orders = [
                    order for order in self.grid_state.active_close_orders if order.order_id not in prev_ids
                ]
                self.grid_state.active_close_orders.append(new_order)

                self._log_event(
                    "close_order_reposted",
                    "Grid: Reposted close order after post-only cancel",
                    level="WARNING",
                    position_id=tracked.position_id,
                    side=close_side,
                    price=new_order.price,
                    size=tracked.size,
                    retries=attempt,
                )
            else:
                self._log_event(
                    "close_order_retry_rejected",
                    "Grid: Close order retry rejected by exchange",
                    level="ERROR",
                    position_id=tracked.position_id,
                    side=close_side,
                    price=close_price,
                    size=tracked.size,
                    retries=attempt,
                    error=order_result.error_message,
                )

    def _compute_retry_close_price(
        self,
        tracked: TrackedPosition,
        best_bid: Optional[Decimal],
        best_ask: Optional[Decimal],
        attempt: int,
    ) -> Decimal:
        """Adjust close limit price to remain post-only while tracking the market."""
        base_price = self._calculate_close_price(tracked.entry_price)
        tick = getattr(self.exchange_client.config, "tick_size", None)
        if tick is None:
            return base_price

        try:
            tick_size = Decimal(str(tick))
        except Exception:  # pragma: no cover - defensive guard
            tick_size = Decimal(tick)

        multiplier = getattr(self.config, "post_only_tick_multiplier", Decimal("2"))
        if multiplier <= 0:
            multiplier = Decimal("1")

        offset = tick_size * multiplier * Decimal(attempt)

        if tracked.side == "long":
            reference = best_bid if best_bid is not None else base_price
            price = max(base_price, reference + offset)
        else:
            reference = best_ask if best_ask is not None else base_price
            price = min(base_price, reference - offset)
            if price <= Decimal("0"):
                price = tick_size

        return self.exchange_client.round_to_tick(price)

    def notify_order_filled(
        self,
        filled_price: Decimal,
        filled_quantity: Decimal,
        order_id: Optional[str] = None,
    ) -> None:
        """
        Notify strategy that an order was filled.

        This is called by the trading bot after successful order execution.
        """
        client_index: Optional[int] = None
        if order_id is not None:
            try:
                client_index = int(str(order_id))
            except (TypeError, ValueError):
                client_index = None

        mapped_position_id: Optional[str] = None
        if client_index is not None:
            mapped_position_id = self.grid_state.order_index_to_position_id.get(client_index)

        expected_index = self.grid_state.pending_client_order_index
        if (
            expected_index is None
            and self.grid_state.pending_position_id is None
            and (
                client_index is None
                or client_index not in self.grid_state.order_index_to_position_id
            )
        ):
            self.logger.log(
                "Grid: Ignoring fill for order outside pending context",
                "DEBUG",
                order_id=order_id,
            )
            return

        if (
            expected_index is not None
            and client_index is not None
            and expected_index != client_index
        ):
            if mapped_position_id:
                self.logger.log(
                    "Grid: Fill for non-pending order detected; realigning state",
                    "WARNING",
                    expected_order_index=expected_index,
                    received_order_index=client_index,
                    position_id=mapped_position_id,
                )
                self.grid_state.pending_position_id = mapped_position_id
                self.grid_state.pending_client_order_index = client_index
            else:
                self.logger.log(
                    "Grid: Ignoring unexpected fill for unknown order index",
                    "WARNING",
                    order_id=order_id,
                    expected_order_index=expected_index,
                )
                return

        self.grid_state.filled_price = filled_price
        self.grid_state.filled_quantity = filled_quantity
        if self.grid_state.pending_position_id is None:
            self.grid_state.pending_position_id = self.grid_state.allocate_position_id()
        self.grid_state.filled_position_id = self.grid_state.pending_position_id
        if client_index is not None:
            self.grid_state.filled_client_order_index = client_index
            self.grid_state.pending_client_order_index = client_index
            if mapped_position_id is None and self.grid_state.pending_position_id:
                self.grid_state.order_index_to_position_id[client_index] = self.grid_state.pending_position_id
        elif self.grid_state.pending_client_order_index is not None:
            self.grid_state.filled_client_order_index = self.grid_state.pending_client_order_index
        self.grid_state.pending_open_order_id = None
        self.grid_state.pending_open_quantity = None
        self.logger.log(
            f"Grid: Order filled at {filled_price} for {filled_quantity} (position {self.grid_state.filled_position_id})",
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
