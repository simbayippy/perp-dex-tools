"""
Aster WebSocket Manager

Handles WebSocket connections for Aster exchange order updates.
Uses Binance Futures-compatible WebSocket API with listen keys and keepalive.
"""

import asyncio
import json
import time
import hmac
import hashlib
from typing import Dict, Any, Optional, Callable
from urllib.parse import urlencode
import aiohttp
import websockets


class AsterWebSocketManager:
    """WebSocket manager for Aster order updates."""

    def __init__(self, config: Dict[str, Any], api_key: str, secret_key: str, order_update_callback: Callable):
        self.api_key = api_key
        self.secret_key = secret_key
        self.order_update_callback = order_update_callback
        self.websocket = None
        self.running = False
        self.base_url = "https://fapi.asterdex.com"
        self.ws_url = "wss://fstream.asterdex.com"
        self.listen_key = None
        self.logger = None
        self._keepalive_task = None
        self._last_ping_time = None
        self.config = config

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

    async def _get_listen_key(self) -> str:
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
                'https://fapi.asterdex.com/fapi/v1/listenKey',
                headers=headers,
                data=params
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    return result.get('listenKey')
                else:
                    raise Exception(f"Failed to get listen key: {response.status}")

    async def _keepalive_listen_key(self) -> bool:
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
                        if self.logger:
                            self.logger.debug("Listen key keepalive successful")
                        return True
                    else:
                        if self.logger:
                            self.logger.warning(f"Failed to keepalive listen key: {response.status}")
                        return False
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error keeping alive listen key: {e}")
            return False

    async def _check_connection_health(self) -> bool:
        """Check if the WebSocket connection is healthy based on ping timing."""
        if not self._last_ping_time:
            return True  # No pings received yet, assume healthy

        # Check if we haven't received a ping in the last 10 minutes
        # (server sends pings every 5 minutes, so 10 minutes indicates a problem)
        time_since_last_ping = time.time() - self._last_ping_time
        if time_since_last_ping > 10 * 60:  # 10 minutes
            if self.logger:
                self.logger.warning(
                    f"No ping received for {time_since_last_ping/60:.1f} minutes, "
                    "connection may be unhealthy"
                )
            return False

        return True

    async def _start_keepalive_task(self):
        """Start the keepalive task to extend listen key validity and monitor connection health."""
        while self.running:
            try:
                # Check connection health every 5 minutes
                await asyncio.sleep(5 * 60)

                if not self.running:
                    break

                # Check if connection is healthy
                if not await self._check_connection_health():
                    if self.logger:
                        self.logger.warning("Connection health check failed, reconnecting...")
                    # Try to reconnect
                    try:
                        await self.connect()
                    except Exception as e:
                        if self.logger:
                            self.logger.error(f"Reconnection failed: {e}")
                        # Wait before retrying
                        await asyncio.sleep(30)
                    continue

                # Check if we need to keepalive the listen key (every 50 minutes)
                if self.listen_key and time.time() % (50 * 60) < 5 * 60:  # Within 5 minutes of 50-minute mark
                    success = await self._keepalive_listen_key()
                    if not success:
                        if self.logger:
                            self.logger.warning("Listen key keepalive failed, reconnecting...")
                        # Try to reconnect
                        try:
                            await self.connect()
                        except Exception as e:
                            if self.logger:
                                self.logger.error(f"Reconnection failed: {e}")
                            # Wait before retrying
                            await asyncio.sleep(30)

            except Exception as e:
                if self.logger:
                    self.logger.error(f"Error in keepalive task: {e}")
                # Wait a bit before retrying
                await asyncio.sleep(60)

    async def connect(self):
        """Connect to Aster WebSocket."""
        try:
            # Get listen key
            self.listen_key = await self._get_listen_key()
            if not self.listen_key:
                raise Exception("Failed to get listen key")

            # Connect to WebSocket
            ws_url = f"{self.ws_url}/ws/{self.listen_key}"
            self.websocket = await websockets.connect(ws_url)
            self.running = True

            if self.logger:
                self.logger.info("Connected to Aster WebSocket with listen key")

            # Start keepalive task
            self._keepalive_task = asyncio.create_task(self._start_keepalive_task())

            # Start listening for messages
            await self._listen()

        except Exception as e:
            if self.logger:
                self.logger.error(f"WebSocket connection error: {e}")
            raise

    async def _listen(self):
        """Listen for WebSocket messages."""
        try:
            async for message in self.websocket:
                if not self.running:
                    break

                # Check if this is a ping frame (websockets library handles pong automatically)
                if isinstance(message, bytes) and message == b'\x89\x00':  # Ping frame
                    self._last_ping_time = time.time()
                    if self.logger:
                        self.logger.debug("Received ping frame, sending pong")
                    continue

                try:
                    data = json.loads(message)
                    await self._handle_message(data)
                except json.JSONDecodeError as e:
                    if self.logger:
                        self.logger.error(f"Failed to parse WebSocket message: {e}")
                except Exception as e:
                    if self.logger:
                        self.logger.error(f"Error handling WebSocket message: {e}")

        except websockets.exceptions.ConnectionClosed:
            if self.logger:
                self.logger.warning("WebSocket connection closed")
        except Exception as e:
            if self.logger:
                self.logger.error(f"WebSocket listen error: {e}")

    async def _handle_message(self, data: Dict[str, Any]):
        """Handle incoming WebSocket messages."""
        try:
            event_type = data.get('e', '')

            if event_type == 'ORDER_TRADE_UPDATE':
                await self._handle_order_update(data)
            elif event_type == 'listenKeyExpired':
                if self.logger:
                    self.logger.warning("Listen key expired, reconnecting...")
                # Reconnect with new listen key
                await self.connect()
            else:
                if self.logger:
                    self.logger.debug(f"Unknown WebSocket message: {data}")

        except Exception as e:
            if self.logger:
                self.logger.error(f"Error handling WebSocket message: {e}")

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
            if hasattr(self, 'order_update_callback') and self.order_update_callback:
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
            if self.logger:
                self.logger.error(f"Error handling order update: {e}")

    async def disconnect(self):
        """Disconnect from WebSocket."""
        self.running = False

        # Cancel keepalive task
        if self._keepalive_task and not self._keepalive_task.done():
            self._keepalive_task.cancel()
            try:
                await self._keepalive_task
            except asyncio.CancelledError:
                pass

        if self.websocket:
            await self.websocket.close()
            if self.logger:
                self.logger.info("WebSocket disconnected")

    def set_logger(self, logger):
        """Set the logger instance."""
        self.logger = logger

