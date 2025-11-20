"""
WebSocket handlers module for Paradex client.

Handles WebSocket callback processing for orders and positions.
"""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional

from exchange_clients.base_models import CancelReason, OrderInfo
from exchange_clients.paradex.client.utils.converters import build_order_info_from_paradex
from exchange_clients.paradex.client.utils.helpers import to_decimal, normalize_order_side


class ParadexWebSocketHandlers:
    """
    WebSocket handlers for Paradex exchange.
    
    Handles:
    - Order update callbacks
    - Position update callbacks (if available)
    """
    
    def __init__(
        self,
        config: Any,
        logger: Any,
        latest_orders: Dict[str, OrderInfo],
        order_fill_callback: Optional[Any] = None,
        order_status_callback: Optional[Any] = None,
        order_manager: Optional[Any] = None,
        position_manager: Optional[Any] = None,
        emit_liquidation_event_fn: Optional[Any] = None,
        get_exchange_name_fn: Optional[Any] = None,
        normalize_symbol_fn: Optional[Any] = None,
    ):
        """
        Initialize WebSocket handlers.
        
        Args:
            config: Trading configuration object
            logger: Logger instance
            latest_orders: Latest orders dictionary (client._latest_orders) - stores OrderInfo objects
            order_fill_callback: Optional callback for incremental order fills
            order_status_callback: Optional callback for order status changes (FILLED/CANCELED)
            order_manager: Optional order manager (for notifications)
            position_manager: Optional position manager (for position updates)
            emit_liquidation_event_fn: Function to emit liquidation events
            get_exchange_name_fn: Function to get exchange name
            normalize_symbol_fn: Function to normalize symbols
        """
        self.config = config
        self.logger = logger
        self.latest_orders = latest_orders
        self.order_fill_callback = order_fill_callback
        self.order_status_callback = order_status_callback
        self.order_manager = order_manager
        self.position_manager = position_manager
        self.emit_liquidation_event = emit_liquidation_event_fn
        self.get_exchange_name = get_exchange_name_fn or (lambda: "paradex")
        self.normalize_symbol = normalize_symbol_fn or (lambda s: s.upper())
        # Track previous position quantities for zero detection
        self._previous_positions: Dict[str, Decimal] = {}
    
    async def handle_websocket_order_update(self, order_data: Dict[str, Any]) -> None:
        """
        Handle order updates from Paradex WebSocket.
        
        Paradex WebSocket sends order updates via ParadexWebsocketChannel.ORDERS.
        Message format matches the order response from REST API.
        
        Args:
            order_data: Order data dictionary from WebSocket
        """
        try:
            # Extract order ID
            order_id = str(order_data.get('id') or order_data.get('order_id') or '')
            if not order_id:
                return
            
            # Check if this order is for our market
            market = order_data.get('market') or order_data.get('symbol')
            expected_market = getattr(self.config, 'contract_id', None)
            if expected_market and market and market != expected_market:
                self.logger.debug(
                    f"[PARADEX] Ignoring order update for market {market} "
                    f"(expected {expected_market})"
                )
                return
            
            # Convert to OrderInfo
            order_info = build_order_info_from_paradex(order_data, order_id)
            if not order_info:
                return
            
            # Get previous order state for fill detection
            previous_order = self.latest_orders.get(order_id)
            prev_filled = previous_order.filled_size if previous_order else Decimal("0")
            
            # Update cache
            self.latest_orders[order_id] = order_info
            
            # Notify order manager that update was received (for await_order_update())
            if self.order_manager:
                self.order_manager.notify_order_update(order_id)
            
            # Log order update
            status = order_info.status.upper()
            if status == 'CANCELED':
                # For cancelled orders, show filled amount and remaining (cancelled) amount for clarity
                filled = order_info.filled_size or Decimal("0")
                remaining = getattr(order_info, 'remaining_size', None)
                if remaining is None or remaining == Decimal("0"):
                    # Calculate remaining if not available
                    remaining = order_info.size - filled if order_info.size and order_info.size > filled else Decimal("0")
                
                if filled > Decimal("0"):
                    # Had partial fills before cancellation
                    self.logger.info(
                        f"[WEBSOCKET] [PARADEX] {status} "
                        f"(filled: {filled}, cancelled: {remaining}) @ {order_info.price}"
                    )
                else:
                    # No fills, just cancelled
                    cancelled_qty = order_info.size if order_info.size else remaining
                    self.logger.info(
                        f"[WEBSOCKET] [PARADEX] {status} "
                        f"(cancelled: {cancelled_qty}) @ {order_info.price}"
                    )
            elif status in ('FILLED', 'CLOSED'):
                self.logger.info(
                    f"[WEBSOCKET] [PARADEX] {status} "
                    f"{order_info.filled_size} @ {order_info.price}"
                )
                # Check for position zeroed when order is FILLED
                if market and self.position_manager:
                    asyncio.create_task(
                        self._check_position_zeroed(market, order_info.filled_size)
                    )
            elif status == 'PARTIALLY_FILLED':
                self.logger.info(
                    f"[WEBSOCKET] [PARADEX] {status} "
                    f"{order_info.filled_size}/{order_info.size} @ {order_info.price}"
                )
            elif status == 'OPEN':
                # Log OPEN at INFO level (like Lighter) for visibility
                self.logger.info(
                    f"[WEBSOCKET] [PARADEX] {status} "
                    f"{order_info.size} @ {order_info.price}"
                )
            else:
                self.logger.debug(
                    f"[WEBSOCKET] [PARADEX] {status} "
                    f"{order_info.size} @ {order_info.price}"
                )
            
            # Call incremental fill callback (for partial fills)
            if self.order_fill_callback and order_info.filled_size > prev_filled:
                fill_increment = order_info.filled_size - prev_filled
                if fill_increment > Decimal("0"):
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(
                            self.order_fill_callback(
                                order_id,
                                order_info.price,
                                fill_increment,
                                None,  # Trade ID not available in order update
                            )
                        )
                    except RuntimeError:
                        # No running loop; fallback to direct await
                        await self.order_fill_callback(
                            order_id,
                            order_info.price,
                            fill_increment,
                            None,
                        )
            
            # Call status callback for final states (FILLED/CLOSED/CANCELED)
            # This fires even if fill_increment = 0 (instant fills) or for cancellations
            if self.order_status_callback and status in {'FILLED', 'CLOSED', 'CANCELED'}:
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(
                        self.order_status_callback(
                            order_id,
                            status,
                            order_info.filled_size or Decimal("0"),
                            order_info.price,
                        )
                    )
                except RuntimeError:
                    # No running loop; fallback to direct await
                    await self.order_status_callback(
                        order_id,
                        status,
                        order_info.filled_size or Decimal("0"),
                        order_info.price,
                        )
            
        except Exception as e:
            self.logger.error(f"[PARADEX] Error handling WebSocket order update: {e}")
    
    async def handle_liquidation_notification(self, payload: Dict[str, Any]) -> None:
        """
        Handle liquidation notifications from Paradex WebSocket FILLS channel.
        
        Liquidations come through FILLS channel with fill_type="LIQUIDATION".
        
        Args:
            payload: Fill data dictionary from WebSocket (with fill_type="LIQUIDATION")
        """
        try:
            from exchange_clients.events import LiquidationEvent
            from datetime import datetime, timezone
            
            # Extract liquidation details from fill data
            fill_type = payload.get('fill_type') or payload.get('trade_type')
            if fill_type != "LIQUIDATION":
                return
            
            market = payload.get('market')
            if not market:
                return
            
            # Normalize symbol
            normalized_symbol = self.normalize_symbol(market)
            
            # Extract quantity and price
            size_str = payload.get('size')
            price_str = payload.get('price')
            
            if not size_str or not price_str:
                return
            
            quantity = to_decimal(size_str)
            price = to_decimal(price_str)
            
            if quantity is None or price is None or quantity <= 0:
                return
            
            # Extract side (BUY/SELL)
            side_raw = payload.get('side', '').upper()
            side = "buy" if side_raw == "BUY" else "sell" if side_raw == "SELL" else "sell"
            
            # Extract timestamp
            created_at_ms = payload.get('created_at')
            if created_at_ms:
                try:
                    timestamp = datetime.fromtimestamp(int(created_at_ms) / 1000, tz=timezone.utc)
                except (ValueError, TypeError, OSError):
                    timestamp = datetime.now(timezone.utc)
            else:
                timestamp = datetime.now(timezone.utc)
            
            # Create liquidation event
            event = LiquidationEvent(
                exchange=self.get_exchange_name(),
                symbol=normalized_symbol,
                side=side,
                quantity=abs(quantity),  # Always positive
                price=price,
                timestamp=timestamp,
                metadata={
                    "fill_id": payload.get('id'),
                    "order_id": payload.get('order_id'),
                    "account": payload.get('account'),
                    "market": market,
                    "fill_type": fill_type,
                    "raw": payload,
                },
            )
            
            # Emit liquidation event if callback is available
            if self.emit_liquidation_event:
                await self.emit_liquidation_event(event)
                self.logger.info(
                    f"[PARADEX] Liquidation detected: {normalized_symbol} {side} "
                    f"{abs(quantity)} @ {price}"
                )
            else:
                self.logger.debug(
                    f"[PARADEX] Liquidation detected but no callback: {normalized_symbol} "
                    f"{side} {abs(quantity)} @ {price}"
                )
                
        except Exception as e:
            self.logger.error(f"[PARADEX] Error handling liquidation notification: {e}")

    async def _check_position_zeroed(self, market: str, filled_qty: Decimal) -> None:
        """
        Check if position went to zero after an order fill.
        
        Similar to Lighter's position zeroed detection, but triggered by order fills
        since Paradex doesn't have a position stream.
        
        Args:
            market: Trading market/symbol (e.g., "BTC-USD-PERP")
            filled_qty: Quantity that was filled
        """
        try:
            if not self.position_manager:
                return
            
            # Normalize symbol
            normalized_symbol = self.normalize_symbol(market)
            
            # Get current position
            current_qty = await self.position_manager.get_account_positions(market)
            
            # Get previous position quantity
            prev_qty = self._previous_positions.get(normalized_symbol, Decimal("0"))
            
            # Update tracking
            self._previous_positions[normalized_symbol] = current_qty
            
            # Check if position went from non-zero to zero
            if prev_qty != 0 and current_qty == 0:
                self.logger.warning(
                    f"⚠️ [PARADEX] Position suddenly zeroed: {normalized_symbol} | "
                    f"Previous qty: {prev_qty} | Filled qty: {filled_qty} | "
                    f"Possible causes: liquidation (no notification), rollback, or manual close"
                )
        except Exception as e:
            # Don't let position check errors break order processing
            self.logger.debug(f"[PARADEX] Error checking position zeroed: {e}")

