"""
WebSocket connection management for Paradex.

Handles connection lifecycle, reconnection, and connection state management.
Note: Paradex SDK handles the actual WebSocket connection, but this module
provides reconnection logic and connection state tracking.
"""

import asyncio
from typing import Dict, Any, Optional, Callable


class ParadexWebSocketConnection:
    """Manages WebSocket connection lifecycle and reconnection."""

    RECONNECT_BACKOFF_INITIAL = 1.0
    RECONNECT_BACKOFF_MAX = 30.0
    RECONNECT_MAX_ATTEMPTS = 10

    def __init__(self, paradex_ws_client: Any, logger: Optional[Any] = None):
        """
        Initialize connection manager.
        
        Args:
            paradex_ws_client: Paradex SDK WebSocket client instance
            logger: Logger instance
        """
        self.paradex_ws_client = paradex_ws_client
        self.logger = logger
        self._connected = False
        self._reconnect_attempts = 0

    def set_logger(self, logger):
        """Set the logger instance."""
        self.logger = logger

    async def open_connection(self) -> bool:
        """
        Establish the websocket connection using Paradex SDK.
        
        Returns:
            True if connection successful, False otherwise
        """
        try:
            is_connected = await self.paradex_ws_client.connect()
            if is_connected:
                self._connected = True
                self._reconnect_attempts = 0
                if self.logger:
                    self.logger.info("[PARADEX] 🔗 Connected to websocket")
            return is_connected
        except Exception as exc:
            if self.logger:
                self.logger.error(f"Failed to connect to Paradex websocket: {exc}")
            self._connected = False
            return False

    async def cleanup_current_ws(self) -> None:
        """Close the active websocket connection if it exists."""
        if self._connected:
            try:
                if hasattr(self.paradex_ws_client, '_close_connection'):
                    await self.paradex_ws_client._close_connection()
                self._connected = False
                # Wait a brief moment for the close to complete
                await asyncio.sleep(0.1)
            except Exception as exc:
                # Ignore errors during cleanup (websocket might already be closed)
                if self.logger:
                    self.logger.debug(f"Error closing websocket during cleanup: {exc}")
                self._connected = False

    def is_connected(self) -> bool:
        """Check if websocket is currently connected."""
        return self._connected

    async def reconnect(
        self,
        reset_order_book_fn: Optional[Callable] = None,
        subscribe_channels_fn: Optional[Callable] = None,
        running: bool = True,
    ) -> bool:
        """
        Attempt to reconnect with exponential backoff.
        
        Args:
            reset_order_book_fn: Optional function to reset order book state
            subscribe_channels_fn: Optional function to subscribe to channels
            running: Whether the manager is still running
            
        Returns:
            True if reconnection successful, False otherwise
        """
        delay = self.RECONNECT_BACKOFF_INITIAL
        attempt = 1
        
        while running and attempt <= self.RECONNECT_MAX_ATTEMPTS:
            if self.logger:
                self.logger.warning(
                    f"[PARADEX] Reconnecting websocket (attempt {attempt}/{self.RECONNECT_MAX_ATTEMPTS})"
                )
            
            try:
                # Reset order book if function provided
                if reset_order_book_fn:
                    await reset_order_book_fn()
                
                # Attempt to reconnect
                success = await self.open_connection()
                
                if success:
                    # Resubscribe to channels if function provided
                    if subscribe_channels_fn:
                        await subscribe_channels_fn()
                    
                    if self.logger:
                        self.logger.info("[PARADEX] Websocket reconnect successful")
                    return True
                else:
                    raise Exception("Connection failed")
                    
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self.logger:
                    self.logger.error(
                        f"[PARADEX] Reconnect attempt {attempt} failed: {exc}. Retrying in {delay:.1f}s"
                    )
                await asyncio.sleep(delay)
                delay = min(delay * 2, self.RECONNECT_BACKOFF_MAX)
                attempt += 1
        
        if self.logger:
            self.logger.warning(
                f"[PARADEX] Reconnect aborted after {attempt-1} attempts"
            )
        return False

