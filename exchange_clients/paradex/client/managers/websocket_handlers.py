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
            order_fill_callback: Optional callback for order fills
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
        self.order_manager = order_manager
        self.position_manager = position_manager
        self.emit_liquidation_event = emit_liquidation_event_fn
        self.get_exchange_name = get_exchange_name_fn or (lambda: "paradex")
        self.normalize_symbol = normalize_symbol_fn or (lambda s: s.upper())
    
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
            
            # Log order update
            status = order_info.status.upper()
            if status in ('FILLED', 'CANCELED', 'CLOSED'):
                self.logger.info(
                    f"[WEBSOCKET] [PARADEX] {status} "
                    f"{order_info.filled_size} @ {order_info.price}"
                )
            elif status == 'PARTIALLY_FILLED':
                self.logger.info(
                    f"[WEBSOCKET] [PARADEX] {status} "
                    f"{order_info.filled_size}/{order_info.size} @ {order_info.price}"
                )
            else:
                self.logger.debug(
                    f"[WEBSOCKET] [PARADEX] {status} "
                    f"{order_info.size} @ {order_info.price}"
                )
            
            # Call order fill callback if fill occurred
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
            
        except Exception as e:
            self.logger.error(f"[PARADEX] Error handling WebSocket order update: {e}")
    
    async def handle_liquidation_notification(self, payload: Dict[str, Any]) -> None:
        """
        Handle liquidation notifications from Paradex WebSocket.
        
        Args:
            payload: Liquidation event payload from WebSocket
        """
        # TODO: Implement when Paradex liquidation stream is available
        # For now, this is a placeholder
        self.logger.debug(f"[PARADEX] Liquidation notification received: {payload}")

