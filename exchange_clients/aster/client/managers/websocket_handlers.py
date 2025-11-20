"""
WebSocket handlers module for Aster client.

Handles WebSocket callback processing for orders and liquidations.
"""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Dict, Optional

from exchange_clients.base_models import CancelReason, OrderInfo
from exchange_clients.events import LiquidationEvent
from exchange_clients.aster.client.utils.helpers import to_decimal


class AsterWebSocketHandlers:
    """
    WebSocket handlers for Aster exchange.
    
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
        order_status_callback: Optional[Callable] = None,
        order_manager: Optional[Any] = None,
        position_manager: Optional[Any] = None,
        emit_liquidation_event_fn: Optional[Callable] = None,
        get_exchange_name_fn: Optional[Callable[[], str]] = None,
        normalize_symbol_fn: Optional[Callable[[str], str]] = None,
    ):
        """
        Initialize WebSocket handlers.
        
        Args:
            config: Trading configuration object
            logger: Logger instance
            latest_orders: Latest orders dictionary (client._latest_orders) - stores OrderInfo objects
            order_update_handler: Optional callback for order updates
            order_fill_callback: Optional callback for incremental order fills
            order_status_callback: Optional callback for order status changes (FILLED/CANCELED)
            order_manager: Optional order manager (for notifications)
            position_manager: Optional position manager (for position tracking)
            emit_liquidation_event_fn: Function to emit liquidation events
            get_exchange_name_fn: Function to get exchange name
            normalize_symbol_fn: Function to normalize symbols
        """
        self.config = config
        self.logger = logger
        self.latest_orders = latest_orders
        self.order_update_handler = order_update_handler
        self.order_fill_callback = order_fill_callback
        self.order_status_callback = order_status_callback
        self.order_manager = order_manager
        self.position_manager = position_manager
        self.emit_liquidation_event = emit_liquidation_event_fn
        self.get_exchange_name = get_exchange_name_fn or (lambda: "aster")
        self.normalize_symbol = normalize_symbol_fn or (lambda s: s.upper())
        # Track previous position quantities for zero detection
        self._previous_positions: Dict[str, Decimal] = {}
    
    async def handle_websocket_order_update(self, order_data: Dict[str, Any]):
        """Handle order updates from WebSocket."""
        try:
            order_id = str(order_data.get("order_id") or order_data.get("i") or order_data.get("orderId") or "")
            if not order_id:
                return

            symbol = order_data.get("contract_id") or order_data.get("symbol") or order_data.get("s")
            expected_symbol = getattr(self.config, "contract_id", None)
            if expected_symbol and symbol and symbol != expected_symbol:
                return

            side_raw = (order_data.get("side") or order_data.get("S") or "").lower()
            side = side_raw if side_raw in {"buy", "sell"} else side_raw.lower()

            quantity = to_decimal(order_data.get("size"), Decimal("0"))
            filled = to_decimal(order_data.get("filled_size"), Decimal("0"))
            price = to_decimal(order_data.get("price"), Decimal("0"))

            remaining = None
            if quantity is not None and filled is not None:
                remaining = quantity - filled
                if remaining < Decimal("0"):
                    remaining = Decimal("0")

            status = (order_data.get("status") or order_data.get("X") or "").upper()
            if status == "OPEN" and filled and filled > Decimal("0"):
                status = "PARTIALLY_FILLED"

            # Parse cancellation reason if applicable
            cancel_reason = ""
            if status in {"CANCELED", "CANCELLED", "REJECTED", "EXPIRED"}:
                # Check for error code in order data (Aster may include error codes)
                error_code = order_data.get("code") or order_data.get("errorCode") or order_data.get("error_code")
                error_msg = order_data.get("msg") or order_data.get("message") or order_data.get("error") or ""
                
                # Normalize Aster error codes to standard CancelReason values
                if error_code == -2021 or (isinstance(error_msg, str) and 'ORDER_WOULD_IMMEDIATELY_TRIGGER' in error_msg.upper()):
                    # Aster uses -2021 for GTX (post-only) orders that would immediately cross
                    cancel_reason = CancelReason.POST_ONLY_VIOLATION
                elif status == "EXPIRED":
                    cancel_reason = CancelReason.EXPIRED
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

            if status in {"FILLED", "CANCELED"}:
                self.logger.info(
                    f"[WEBSOCKET] [ASTER] {status} "
                    f"{filled or quantity} @ {price or 'n/a'}"
                )
            else:
                self.logger.info(
                    f"[WEBSOCKET] [ASTER] {status} "
                    f"{filled or quantity} @ {price or 'n/a'}"
                )

            # Check for position zeroed when order is FILLED
            if status == "FILLED" and symbol and self.position_manager:
                asyncio.create_task(
                    self._check_position_zeroed(symbol, filled or quantity)
                )

            if self.order_update_handler:
                payload = {
                    "order_id": order_id,
                    "side": side,
                    "order_type": order_data.get("order_type") or order_data.get("o") or order_data.get("type") or "UNKNOWN",
                    "status": status,
                    "size": quantity,
                    "price": price,
                    "contract_id": symbol,
                    "filled_size": filled,
                    "raw_event": order_data,
                }
                self.order_update_handler(payload)

            # Call incremental fill callback (for partial fills)
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
                        # No running loop; fallback to direct await
                        await self.order_fill_callback(
                            order_id,
                            price,
                            fill_increment,
                            order_data.get("u"),
                        )
            
            # Call status callback for final states (FILLED/CANCELED)
            # This fires even if fill_increment = 0 (instant fills) or for cancellations
            if self.order_status_callback and status in {"FILLED", "CANCELED", "CANCELLED"}:
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(
                        self.order_status_callback(
                            order_id,
                            status,
                            filled or Decimal("0"),
                            price,
                        )
                    )
                except RuntimeError:
                    # No running loop; fallback to direct await
                    await self.order_status_callback(
                        order_id,
                        status,
                        filled or Decimal("0"),
                        price,
                        )

        except Exception as e:
            self.logger.error(f"Error handling WebSocket order update: {e}")

    async def handle_liquidation_notification(self, payload: Dict[str, Any]) -> None:
        """Normalize forceOrder messages into LiquidationEvent instances."""
        order = payload.get("o", {})
        symbol = order.get("s")
        if not symbol:
            return

        quantity_raw = order.get("z") or order.get("q") or "0"
        price_raw = order.get("ap") or order.get("p") or "0"

        try:
            quantity = Decimal(str(quantity_raw)).copy_abs()
            price = Decimal(str(price_raw))
        except (InvalidOperation, TypeError):
            return

        if quantity <= 0:
            return

        side = (order.get("S") or "sell").lower()
        timestamp_ms = order.get("T")
        if timestamp_ms is not None:
            try:
                timestamp = datetime.fromtimestamp(int(timestamp_ms) / 1000, tz=timezone.utc)
            except (ValueError, OSError, OverflowError):
                timestamp = datetime.now(timezone.utc)
        else:
            timestamp = datetime.now(timezone.utc)

        # Normalize symbol (remove USDT suffix)
        internal_symbol = symbol
        if symbol.endswith("USDT"):
            internal_symbol = symbol[:-4]

        event = LiquidationEvent(
            exchange=self.get_exchange_name(),
            symbol=internal_symbol,
            side=side,
            quantity=quantity,
            price=price,
            timestamp=timestamp,
            metadata={"raw": payload},
        )

        if self.emit_liquidation_event:
            await self.emit_liquidation_event(event)

    async def _check_position_zeroed(self, symbol: str, filled_qty: Decimal) -> None:
        """
        Check if position went to zero after an order fill.
        
        Similar to Lighter's position zeroed detection, but triggered by order fills
        since Aster doesn't have a position stream.
        
        Args:
            symbol: Trading symbol
            filled_qty: Quantity that was filled
        """
        try:
            if not self.position_manager:
                return
            
            # Normalize symbol
            normalized_symbol = self.normalize_symbol(symbol)
            if normalized_symbol.endswith("USDT"):
                normalized_symbol = normalized_symbol[:-4]
            normalized_symbol = normalized_symbol.upper()
            
            # Get current position
            contract_id = getattr(self.config, "contract_id", None) or symbol
            current_qty = await self.position_manager.get_account_positions(contract_id)
            
            # Get previous position quantity
            prev_qty = self._previous_positions.get(normalized_symbol, Decimal("0"))
            
            # Update tracking
            self._previous_positions[normalized_symbol] = current_qty
            
            # Check if position went from non-zero to zero
            if prev_qty != 0 and current_qty == 0:
                self.logger.warning(
                    f"⚠️ [ASTER] Position suddenly zeroed: {normalized_symbol} | "
                    f"Previous qty: {prev_qty} | Filled qty: {filled_qty} | "
                    f"Possible causes: liquidation (no notification), rollback, or manual close"
                )
        except Exception as e:
            # Don't let position check errors break order processing
            self.logger.debug(f"[ASTER] Error checking position zeroed: {e}")

