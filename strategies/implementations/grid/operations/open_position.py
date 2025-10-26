"""
Open-position orchestration for the grid strategy.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, Optional, Tuple

from exchange_clients.market_data import PriceStream
from ..config import GridConfig
from ..models import GridCycleState, GridState
from ..risk_controller import GridRiskController


class GridOpenPositionOperator:
    """Submit entry orders and handle order sizing for the grid strategy."""

    def __init__(
        self,
        config: GridConfig,
        exchange_client,
        grid_state: GridState,
        logger,
        risk_controller: GridRiskController,
        price_stream: PriceStream,
        order_notional_usd: Optional[Decimal],
    ) -> None:
        self.config = config
        self.exchange_client = exchange_client
        self.grid_state = grid_state
        self.logger = logger
        self.risk_controller = risk_controller
        self.price_stream = price_stream
        self.order_notional_usd = order_notional_usd

        self._last_order_notional: Optional[Decimal] = None

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

    async def place_open_order(self) -> Dict[str, Any]:
        """Place an open order to enter a new grid level."""
        try:
            contract_id = self.exchange_client.config.contract_id

            bbo = await self.price_stream.latest()
            best_bid = bbo.bid
            best_ask = bbo.ask
            reference_price = (best_bid + best_ask) / Decimal("2")

            try:
                quantity, applied_notional = self._determine_order_quantity(reference_price)
            except ValueError as exc:
                self.logger.log(f"Grid: Unable to determine order size - {exc}", "WARNING")
                return {
                    "action": "wait",
                    "message": str(exc),
                    "wait_time": max(1, float(self.config.wait_time)),
                }

            tick = self.exchange_client.config.tick_size
            offset_multiplier = getattr(self.config, "post_only_tick_multiplier", Decimal("2"))
            offset = tick * offset_multiplier  # configurable distance for post-only placement
            if self.config.direction == "buy":
                # For buy, place a couple ticks below the ask to respect post-only
                order_price = best_ask - offset
                if order_price <= 0:
                    order_price = tick
            else:  # sell
                # For sell, place above the bid by a couple ticks
                order_price = best_bid + offset

            order_price = self.exchange_client.round_to_tick(order_price)

            risk_ok, risk_message = await self.risk_controller.check_order_limits(reference_price, quantity)
            if not risk_ok:
                return {
                    "action": "wait",
                    "message": risk_message,
                    "wait_time": max(1, float(self.config.wait_time)),
                }

            order_result = await self.exchange_client.place_limit_order(
                contract_id=contract_id,
                quantity=quantity,
                price=order_price,
                side=self.config.direction,
            )

            if order_result.success:
                # Update state to waiting for fill
                self.grid_state.cycle_state = GridCycleState.WAITING_FOR_FILL

                # Record filled price and quantity from result
                if order_result.status == "FILLED":
                    self.grid_state.filled_price = order_result.price
                    self.grid_state.filled_quantity = order_result.size
                    self.grid_state.pending_open_order_id = None
                    self.grid_state.pending_open_quantity = None
                else:
                    self.grid_state.pending_open_order_id = order_result.order_id
                    self.grid_state.pending_open_quantity = quantity

                self.logger.log(
                    f"Grid: Placed {self.config.direction} order for {quantity} "
                    f"(@ ~${float(applied_notional):.2f}) at {order_result.price}",
                    "INFO",
                )

                return {
                    "action": "order_placed",
                    "order_id": order_result.order_id,
                    "side": self.config.direction,
                    "quantity": quantity,
                    "price": order_result.price,
                    "notional_usd": applied_notional,
                    "status": order_result.status,
                }

            self.logger.log(f"Grid: Failed to place open order: {order_result.error_message}", "ERROR")
            return {
                "action": "error",
                "message": order_result.error_message,
                "wait_time": 5,
            }

        except Exception as exc:
            self.logger.log(f"Error placing open order: {exc}", "ERROR")
            return {
                "action": "error",
                "message": str(exc),
                "wait_time": 5,
            }
