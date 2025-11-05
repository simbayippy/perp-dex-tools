"""
WebSocket connection management for Aster.

Handles connection lifecycle, listen key management, reconnection, and health monitoring.
"""

import asyncio
import hmac
import hashlib
import time
from typing import Dict, Any, Optional, Callable, Awaitable
from urllib.parse import urlencode

import aiohttp
import websockets

from exchange_clients.base_websocket import BaseWebSocketManager


class AsterWebSocketConnection:
    """Manages WebSocket connection lifecycle, listen keys, and health monitoring."""

    RECONNECT_BACKOFF_INITIAL = 1.0
    RECONNECT_BACKOFF_MAX = 30.0
    RECEIVE_TIMEOUT = 45.0  # seconds - timeout for receiving messages

    def __init__(
        self,
        config: Dict[str, Any],
        api_key: str,
        secret_key: str,
        ws_url: str,
        base_url: str,
        logger: Optional[Any] = None,
    ):
        """
        Initialize connection manager.
        
        Args:
            config: Configuration object
            api_key: API key for authentication
            secret_key: Secret key for signature generation
            ws_url: WebSocket base URL
            base_url: REST API base URL
            logger: Logger instance
        """
        self.config = config
        self.api_key = api_key
        self.secret_key = secret_key
        self.ws_url = ws_url
        self.base_url = base_url
        self.logger = logger
        
        # Connection state
        self.websocket: Optional[websockets.WebSocketClientProtocol] = None
        self.listen_key: Optional[str] = None
        self._last_ping_time: Optional[float] = None
        self.running = False
        self._keepalive_task: Optional[asyncio.Task] = None

    def set_logger(self, logger):
        """Set the logger instance."""
        self.logger = logger

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

    def _generate_signature(self, params: Dict[str, Any]) -> str:
        """Generate HMAC SHA256 signature for Aster API authentication."""
        # Use urlencode to properly format the query string
        query_string = urlencode(params)

        # Generate HMAC SHA256 signature
        signature = hmac.new(
            self.secret_key.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

        return signature

    async def get_listen_key(self) -> str:
        """Get listen key for user data stream."""
        params = {
            'timestamp': int(time.time() * 1000)
        }
        signature = self._generate_signature(params)
        params['signature'] = signature

        headers = {
            'X-MBX-APIKEY': self.api_key,
            'Content-Type': 'application/x-www-form-urlencoded'
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f'{self.base_url}/fapi/v1/listenKey',
                headers=headers,
                data=params
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    listen_key = result.get('listenKey')
                    if not listen_key:
                        raise Exception("Listen key not found in response")
                    return listen_key
                else:
                    raise Exception(f"Failed to get listen key: {response.status}")

    async def keepalive_listen_key(self) -> bool:
        """Keep alive the listen key to prevent timeout."""
        try:
            if not self.listen_key:
                return False

            params = {
                'timestamp': int(time.time() * 1000)
            }
            signature = self._generate_signature(params)
            params['signature'] = signature

            headers = {
                'X-MBX-APIKEY': self.api_key,
                'Content-Type': 'application/x-www-form-urlencoded'
            }

            async with aiohttp.ClientSession() as session:
                async with session.put(
                    f"{self.base_url}/fapi/v1/listenKey",
                    headers=headers,
                    data=params
                ) as response:
                    if response.status == 200:
                        self._log("Listen key keepalive successful", "DEBUG")
                        return True
                    else:
                        self._log(f"Failed to keepalive listen key: {response.status}", "WARNING")
                        return False
        except Exception as e:
            self._log(f"Error keeping alive listen key: {e}", "ERROR")
            return False

    async def check_connection_health(self) -> bool:
        """Check if the WebSocket connection is healthy based on ping timing."""
        if not self._last_ping_time:
            return True  # No pings received yet, assume healthy

        # Check if we haven't received a ping in the last 10 minutes
        # (server sends pings every 5 minutes, so 10 minutes indicates a problem)
        time_since_last_ping = time.time() - self._last_ping_time
        if time_since_last_ping > 10 * 60:  # 10 minutes
            self._log(
                f"No ping received for {time_since_last_ping/60:.1f} minutes, "
                "connection may be unhealthy",
                "WARNING"
            )
            return False

        return True

    def update_ping_time(self):
        """Update the last ping time (called when ping frame is received)."""
        self._last_ping_time = time.time()

    async def open_connection(self, listen_key: str) -> websockets.WebSocketClientProtocol:
        """
        Open WebSocket connection to user data stream.
        
        Args:
            listen_key: Listen key for user data stream
            
        Returns:
            WebSocket connection
        """
        ws_url = f"{self.ws_url}/ws/{listen_key}"
        self.websocket = await websockets.connect(ws_url)
        self.listen_key = listen_key
        self.running = True
        return self.websocket

    async def close_connection(self) -> None:
        """Close the WebSocket connection."""
        if self.websocket:
            await self.websocket.close()
            self.websocket = None
        self.running = False

    async def start_keepalive_task(self, reconnect_fn: Optional[Callable[[], Awaitable[None]]] = None):
        """
        Start the keepalive task to extend listen key validity and monitor connection health.
        
        Args:
            reconnect_fn: Optional function to call for reconnection
        """
        while self.running:
            try:
                # Check connection health every 5 minutes
                await asyncio.sleep(5 * 60)

                if not self.running:
                    break

                # Check if connection is healthy
                if not await self.check_connection_health():
                    self._log("Connection health check failed, reconnecting...", "WARNING")
                    if reconnect_fn:
                        try:
                            await reconnect_fn()
                        except Exception as e:
                            self._log(f"Reconnection failed: {e}", "ERROR")
                            await asyncio.sleep(30)
                    continue

                # Check if we need to keepalive the listen key (every 50 minutes)
                if self.listen_key and time.time() % (50 * 60) < 5 * 60:  # Within 5 minutes of 50-minute mark
                    success = await self.keepalive_listen_key()
                    if not success:
                        self._log("Listen key keepalive failed, reconnecting...", "WARNING")
                        if reconnect_fn:
                            try:
                                await reconnect_fn()
                            except Exception as e:
                                self._log(f"Reconnection failed: {e}", "ERROR")
                                await asyncio.sleep(30)

            except Exception as e:
                self._log(f"Error in keepalive task: {e}", "ERROR")
                # Wait a bit before retrying
                await asyncio.sleep(60)

