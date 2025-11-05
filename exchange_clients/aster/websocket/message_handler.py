"""
Message parsing and routing for Aster WebSocket.

Handles incoming WebSocket message parsing, type detection, and routing to appropriate handlers.
"""

import json
import time
from typing import Dict, Any, Optional, Callable, Awaitable

import websockets

from exchange_clients.base_websocket import BBOData


class AsterMessageHandler:
    """Handles WebSocket message parsing and routing."""

    def __init__(
        self,
        config: Dict[str, Any],
        ws: Optional[websockets.WebSocketClientProtocol],
        order_update_callback: Optional[Callable] = None,
        liquidation_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
        notify_bbo_update_fn: Optional[Callable] = None,
        connection_manager: Optional[Any] = None,
        logger: Optional[Any] = None,
    ):
        """
        Initialize message handler.
        
        Args:
            config: Configuration object
            ws: WebSocket connection
            order_update_callback: Callback for order updates
            liquidation_callback: Callback for liquidations
            notify_bbo_update_fn: Function to notify BBO updates
            connection_manager: Connection manager instance (for ping time updates)
            logger: Logger instance
        """
        self.config = config
        self.ws = ws
        self.order_update_callback = order_update_callback
        self.liquidation_callback = liquidation_callback
        self.notify_bbo_update = notify_bbo_update_fn
        self.connection_manager = connection_manager
        self.logger = logger

    def set_logger(self, logger):
        """Set the logger instance."""
        self.logger = logger

    def set_ws(self, ws):
        """Set the WebSocket connection."""
        self.ws = ws

    def _log(self, message: str, level: str = "INFO"):
        """Log message using the logger if available."""
        if self.logger:
            if hasattr(self.logger, 'log'):
                self.logger.log(message, level)
            elif level == "ERROR" and hasattr(self.logger, 'error'):
                self.logger.error(message)
            elif level == "WARNING" and hasattr(self.logger, 'warning'):
                self.logger.warning(message)
            elif level == "DEBUG" and hasattr(self.logger, 'debug'):
                self.logger.debug(message)
            elif hasattr(self.logger, 'info'):
                self.logger.info(message)

    async def process_message(self, message: Any) -> None:
        """
        Process a WebSocket message and route to appropriate handler.
        
        Args:
            message: Raw WebSocket message (bytes or str)
        """
        # Check if this is a ping frame (websockets library handles pong automatically)
        if isinstance(message, bytes) and message == b'\x89\x00':  # Ping frame
            if self.connection_manager:
                self.connection_manager.update_ping_time()
            self._log("Received ping frame, sending pong", "DEBUG")
            return

        try:
            # Parse JSON message
            if isinstance(message, bytes):
                data = json.loads(message.decode('utf-8'))
            else:
                data = json.loads(message)
            
            await self._handle_message(data)
        except json.JSONDecodeError as e:
            self._log(f"Failed to parse WebSocket message: {e}", "ERROR")
        except Exception as e:
            self._log(f"Error handling WebSocket message: {e}", "ERROR")

    async def _handle_message(self, data: Dict[str, Any]):
        """Handle incoming WebSocket messages and route by event type."""
        try:
            event_type = data.get('e', '')

            if event_type == 'ORDER_TRADE_UPDATE':
                await self._handle_order_update(data)
            elif event_type == 'forceOrder':
                if self.liquidation_callback:
                    await self.liquidation_callback(data)
            elif event_type == 'listenKeyExpired':
                self._log("Listen key expired, reconnecting...", "WARNING")
                # Note: Reconnection should be handled by connection manager
            else:
                self._log(f"Unknown WebSocket message: {data}", "DEBUG")

        except Exception as e:
            self._log(f"Error handling WebSocket message: {e}", "ERROR")

    async def _handle_order_update(self, order_data: Dict[str, Any]):
        """Handle order update messages."""
        try:
            order_info = order_data.get('o', {})

            order_id = order_info.get('i', '')
            symbol = order_info.get('s', '')
            side = order_info.get('S', '')
            quantity = order_info.get('q', '0')
            price = order_info.get('p', '0')
            executed_qty = order_info.get('z', '0')
            status = order_info.get('X', '')

            # Map status
            status_map = {
                'NEW': 'OPEN',
                'PARTIALLY_FILLED': 'PARTIALLY_FILLED',
                'FILLED': 'FILLED',
                'CANCELED': 'CANCELED',
                'REJECTED': 'REJECTED',
                'EXPIRED': 'EXPIRED'
            }
            mapped_status = status_map.get(status, status)

            # Call the order update callback if it exists
            if self.order_update_callback:
                # Let strategy determine order type
                order_type = "ORDER"

                await self.order_update_callback({
                    'order_id': order_id,
                    'side': side.lower(),
                    'order_type': order_type,
                    'status': mapped_status,
                    'size': quantity,
                    'price': price,
                    'contract_id': symbol,
                    'filled_size': executed_qty
                })

        except Exception as e:
            self._log(f"Error handling order update: {e}", "ERROR")

