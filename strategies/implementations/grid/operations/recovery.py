"""
Recovery workflows for stale grid positions.
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import List, Optional

from ..config import GridConfig
from ..models import GridState, TrackedPosition
from ..position_manager import GridPositionManager
from .close_position import GridOrderCloser


class GridRecoveryOperator:
    """Detect and resolve stuck grid positions according to the configured mode."""

    def __init__(
        self,
        config: GridConfig,
        exchange_client,
        grid_state: GridState,
        logger,
        log_event,
        position_manager: GridPositionManager,
        order_closer: GridOrderCloser,
    ) -> None:
        self.config = config
        self.exchange_client = exchange_client
        self.grid_state = grid_state
        self.logger = logger
        self._log_event = log_event
        self.position_manager = position_manager
        self.order_closer = order_closer

    async def run_recovery_checks(
        self,
        current_price: Decimal,
        current_position: Optional[Decimal] = None,
    ) -> None:
        """Identify and recover positions that have been open longer than allowed."""
        if self.config.recovery_mode == "none":
            return

        if self.position_manager.count() == 0:
            return

        active_ids = {order.order_id for order in self.grid_state.active_close_orders}
        
        # Build a map of order_id to close price for exit logging
        close_order_prices = {
            order.order_id: order.price 
            for order in self.grid_state.active_close_orders
        }
        
        pruned_positions = self.position_manager.prune_by_active_orders(active_ids)

        def _signed_size(tp: TrackedPosition) -> Decimal:
            return tp.size if tp.side == "long" else -tp.size

        retracked_positions: List[TrackedPosition] = []
        actual_position = (
            current_position
            if current_position is not None
            else self.grid_state.last_known_position
        )
        zero = Decimal("0")

        if pruned_positions and actual_position is not None:
            remaining_signed = sum(_signed_size(pos) for pos in self.position_manager.all())
            delta = actual_position - remaining_signed

            if delta != zero:
                pending = list(pruned_positions)
                for position in list(pending):
                    signed = _signed_size(position)
                    if signed == zero:
                        continue
                    same_direction = (delta > zero and signed > zero) or (delta < zero and signed < zero)
                    if same_direction:
                        self.position_manager.track(position)
                        retracked_positions.append(position)
                        pruned_positions.remove(position)
                        remaining_signed += signed
                        delta = actual_position - remaining_signed
                        self._log_event(
                            "close_order_missing_retracked",
                            (
                                "Grid: Close order missing while exposure persists; "
                                "re-queueing for post-only retry."
                            ),
                            level="WARNING",
                            position_id=position.position_id,
                            side=position.side,
                            size=position.size,
                            remaining_position=actual_position,
                        )
                        if delta == zero:
                            break

        # Log completed positions with exit details
        for position in pruned_positions:
            # Find the exit price from the close order that was placed
            exit_price = None
            for close_order_id in position.close_order_ids or []:
                if close_order_id in close_order_prices:
                    exit_price = close_order_prices[close_order_id]
                    break
            
            # Log exit filled
            if exit_price:
                self.logger.info(
                    f"\n{'-'*80}\n🎯 Position {position.position_id} EXIT FILLED @ {exit_price}\n{'-'*80}"
                )
            
            # Log position complete
            exit_info = f" | Exit @ {exit_price}" if exit_price else ""
            self.logger.info(
                f"\n{'='*80}\n💰 POSITION {position.position_id} COMPLETE | Entry @ {position.entry_price} | Size: {position.size} | Side: {position.side.upper()}{exit_info}\n{'='*80}\n"
            )

        retry_ids = {pos.position_id for pos in retracked_positions}

        if self.position_manager.count() == 0:
            return

        threshold_seconds = int(self.config.position_timeout_minutes) * 60
        now = time.time()
        remaining: List[TrackedPosition] = []

        for tracked in self.position_manager.all():
            close_ids = tracked.close_order_ids or []
            still_active = any(order_id in active_ids for order_id in close_ids)
            if tracked.position_id in retry_ids:
                remaining.append(tracked)
                continue

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

        self.position_manager.replace(remaining)

    async def _recover_position(self, tracked: TrackedPosition, current_price: Decimal) -> bool:
        """Execute the configured recovery strategy for a stuck position."""
        mode = self.config.recovery_mode
        signed_position = tracked.size if tracked.side == "long" else -tracked.size
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
            success = await self.order_closer.market_close(
                signed_position,
                f"Aggressive recovery - {reason}",
                tracked_position=tracked,
            )
            return success

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
                tracked.hedged = True
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

    async def _place_hedge_order(self, tracked: TrackedPosition) -> bool:
        """Place an opposite market order to neutralize exposure."""
        contract_id = self.exchange_client.config.contract_id
        hedge_side = "sell" if tracked.side == "long" else "buy"
        try:
            result = await self.exchange_client.place_market_order(
                contract_id=contract_id,
                quantity=tracked.size,
                side=hedge_side,
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
