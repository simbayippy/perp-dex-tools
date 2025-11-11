"""
WebSocket handlers module for Backpack client.

Handles WebSocket order updates and liquidation notifications.
"""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Dict, Optional

from exchange_clients.base_models import CancelReason, OrderInfo
from exchange_clients.events import LiquidationEvent
from exchange_clients.backpack.client.utils.helpers import to_decimal, to_internal_symbol


class BackpackWebSocketHandlers:
    """
    WebSocket handlers for Backpack exchange.
    
    Handles:
    - Order update callbacks
    - Liquidation notifications
    """
    
    def __init__(
        self,
        config: Any,
        logger: Any,
        latest_orders: Dict[str, OrderInfo],
        order_update_handler: Optional[Callable] = None,
        order_fill_callback: Optional[Callable] = None,
        order_manager: Optional[Any] = None,
        emit_liquidation_event_fn: Optional[Callable] = None,
        get_exchange_name_fn: Optional[Callable] = None,
    ):
        """
        Initialize WebSocket handlers.
        
        Args:
            config: Trading configuration object
            logger: Logger instance
            latest_orders: Dictionary storing latest OrderInfo objects
            order_update_handler: Optional handler for order updates
            order_fill_callback: Optional callback for order fills
            order_manager: Optional order manager (for notifications)
            emit_liquidation_event_fn: Function to emit liquidation events
            get_exchange_name_fn: Function to get exchange name
        """
        self.config = config
        self.logger = logger
        self.latest_orders = latest_orders
        self.order_update_handler = order_update_handler
        self.order_fill_callback = order_fill_callback
        self.order_manager = order_manager
        self.emit_liquidation_event = emit_liquidation_event_fn
        self.get_exchange_name = get_exchange_name_fn or (lambda: "backpack")

    async def handle_order_update(self, order_data: Dict[str, Any]) -> None:
        """
        Normalize and forward order update events to the registered handler.
        
        Args:
            order_data: Raw order update data from WebSocket
        """
        try:
            symbol = order_data.get("s") or order_data.get("symbol")
            if symbol and getattr(self.config, "contract_id", None):
                expected_symbol = getattr(self.config, "contract_id")
                if expected_symbol and symbol != expected_symbol:
                    return

            order_id = str(order_data.get("i") or order_data.get("orderId") or "")
            if not order_id:
                return

            side_raw = (order_data.get("S") or order_data.get("side") or "").upper()
            if side_raw == "BID":
                side = "buy"
            elif side_raw == "ASK":
                side = "sell"
            else:
                side = side_raw.lower() or None

            quantity = to_decimal(order_data.get("q"), Decimal("0"))
            filled = to_decimal(order_data.get("z"), Decimal("0"))
            price = to_decimal(order_data.get("p"), Decimal("0"))
            remaining = None
            if quantity is not None and filled is not None:
                remaining = quantity - filled

            event = (order_data.get("e") or order_data.get("event") or "").lower()
            status = None
            if event == "orderfill":
                if quantity is not None and quantity > 0 and filled >= quantity:
                    status = "FILLED"
                elif filled and filled > 0:
                    status = "PARTIALLY_FILLED"
                else:
                    status = "OPEN"
            elif event in {"orderaccepted", "new"}:
                status = "OPEN"
            elif event in {"ordercancelled", "ordercanceled", "orderexpired", "canceled"}:
                status = "CANCELED"
            else:
                status = order_data.get("X") or order_data.get("status")
            status = (status or "").upper()

            # Parse cancellation reason if applicable
            cancel_reason = ""
            if status == "CANCELED":
                # Check for error code in order data (Backpack may include error codes)
                error_code = order_data.get("code") or order_data.get("errorCode") or order_data.get("error_code")
                error_msg = order_data.get("msg") or order_data.get("message") or order_data.get("error") or ""
                
                # Normalize Backpack error codes to standard CancelReason values
                # Note: Proactively adding -2021 based on error code format matching Aster
                # Can adjust if actual error format differs
                if error_code == -2021 or (isinstance(error_msg, str) and 'ORDER_WOULD_IMMEDIATELY_TRIGGER' in error_msg.upper()):
                    # Backpack uses -2021 for post-only orders that would immediately cross
                    cancel_reason = CancelReason.POST_ONLY_VIOLATION
                elif error_code:
                    # Other error codes - pass through as lowercase string
                    cancel_reason = str(error_code).lower()
                else:
                    cancel_reason = CancelReason.UNKNOWN

            previous = self.latest_orders.get(order_id)
            prev_filled = previous.filled_size if previous else Decimal("0")

            info = OrderInfo(
                order_id=order_id,
                side=side or "",
                size=quantity or Decimal("0"),
                price=price or Decimal("0"),
                status=status,
                filled_size=filled or Decimal("0"),
                remaining_size=remaining or Decimal("0"),
                cancel_reason=cancel_reason,
            )
            self.latest_orders[order_id] = info
            
            # Notify order manager that update was received (for await_order_update())
            if self.order_manager:
                self.order_manager.notify_order_update(order_id)

            if status == "FILLED":
                self.logger.info(
                    f"[WEBSOCKET] [BACKPACK] {status} "
                    f"{filled or quantity} @ {price or 'n/a'}"
                )
            else:
                self.logger.info(
                    f"[WEBSOCKET] [BACKPACK] {status} "
                    f"{filled or quantity} @ {price or 'n/a'}"
                )

            if self.order_update_handler:
                payload = {
                    "order_id": order_id,
                    "side": side,
                    "order_type": order_data.get("o") or order_data.get("type") or "UNKNOWN",
                    "status": status,
                    "size": quantity,
                    "price": price,
                    "contract_id": symbol,
                    "filled_size": filled,
                    "raw_event": order_data,
                }
                self.order_update_handler(payload)

            if self.order_fill_callback and filled is not None and price is not None:
                fill_increment = filled - prev_filled
                if fill_increment > Decimal("0"):
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(
                            self.order_fill_callback(
                                order_id,
                                price,
                                fill_increment,
                                order_data.get("u"),
                            )
                        )
                    except RuntimeError:
                        await self.order_fill_callback(
                            order_id,
                            price,
                            fill_increment,
                            order_data.get("u"),
                        )
        except Exception as exc:
            self.logger.error(f"Error handling Backpack order update: {exc}")

    async def handle_liquidation_notification(self, payload: Dict[str, Any]) -> None:
        """
        Convert Backpack order updates triggered by liquidations into LiquidationEvent objects.
        
        Args:
            payload: Raw liquidation payload from WebSocket
        """
        try:
            origin = (payload.get("O") or "").upper()
            if origin not in {
                "LIQUIDATION_AUTOCLOSE",
                "ADL_AUTOCLOSE",
                "BACKSTOP_LIQUIDITY_PROVIDER",
            }:
                return

            event_type = (payload.get("e") or "").lower()
            if event_type != "orderfill":
                return

            symbol_raw = payload.get("s")
            if not symbol_raw:
                return

            internal_symbol = to_internal_symbol(symbol_raw) or (self.config.ticker or symbol_raw)

            side_raw = (payload.get("S") or "").lower()
            side = "buy" if side_raw in {"bid", "buy"} else "sell" if side_raw in {"ask", "sell"} else side_raw or "buy"

            fill_qty = to_decimal(payload.get("l"), Decimal("0")) or Decimal("0")
            if fill_qty <= 0:
                fill_qty = to_decimal(payload.get("z"), Decimal("0")) or Decimal("0")
            if fill_qty <= 0:
                return
            fill_qty = fill_qty.copy_abs()

            price = to_decimal(payload.get("L"), Decimal("0")) or Decimal("0")

            timestamp_us = payload.get("E")
            timestamp = datetime.now(timezone.utc)
            if timestamp_us is not None:
                try:
                    timestamp = datetime.fromtimestamp(int(timestamp_us) / 1_000_000, tz=timezone.utc)
                except (ValueError, TypeError, OSError):
                    timestamp = datetime.now(timezone.utc)

            metadata: Dict[str, Any] = {
                "order_id": payload.get("i"),
                "trade_id": payload.get("t"),
                "origin": origin,
                "maker": payload.get("m"),
                "raw": payload,
            }

            event = LiquidationEvent(
                exchange=self.get_exchange_name(),
                symbol=internal_symbol,
                side=side,
                quantity=fill_qty,
                price=price,
                timestamp=timestamp,
                metadata=metadata,
            )
            await self.emit_liquidation_event(event)
        except Exception as exc:
            self.logger.error(f"Error handling Backpack liquidation notification: {exc}")

