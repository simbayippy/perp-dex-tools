"""
Backpack WebSocket Manager

Handles WebSocket connections for Backpack exchange order updates.
Uses ED25519 signature authentication for secure connections.
"""

import asyncio
import base64
import json
import time
from typing import Any, Awaitable, Callable, Dict, Optional

from cryptography.hazmat.primitives.asymmetric import ed25519
import websockets

from exchange_clients.base_models import MissingCredentialsError


class BackpackWebSocketManager:
    """WebSocket manager for Backpack order updates."""

    def __init__(
        self,
        public_key: str,
        secret_key: str,
        symbol: Optional[str],
        order_update_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    ):
        self.public_key = public_key
        self.secret_key = secret_key
        self.symbol = symbol
        self.order_update_callback = order_update_callback

        self.websocket: Optional[websockets.WebSocketClientProtocol] = None
        self.running = False
        self.logger = None
        self.ws_url = "wss://ws.backpack.exchange"
        self._ready_event = asyncio.Event()

        try:
            secret_bytes = base64.b64decode(secret_key)
            self.private_key = ed25519.Ed25519PrivateKey.from_private_bytes(secret_bytes)
        except Exception as exc:  # pragma: no cover - defensive
            raise MissingCredentialsError(f"Invalid Backpack secret key: {exc}") from exc

    # ------------------------------------------------------------------ #
    # Public interface
    # ------------------------------------------------------------------ #

    def set_logger(self, logger) -> None:
        """Attach shared logger instance."""
        self.logger = logger

    async def wait_until_ready(self, timeout: float = 5.0) -> bool:
        """Wait until the first subscription attempt completes."""
        try:
            await asyncio.wait_for(self._ready_event.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def connect(self) -> None:
        """Run the WebSocket connection with automatic reconnection."""
        if self.running:
            return

        self.running = True
        backoff = 1.0

        while self.running:
            try:
                await self._connect_once()
                backoff = 1.0
                await self._listen()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - defensive
                if self.logger:
                    self.logger.error(f"[BACKPACK] WebSocket error: {exc}")
                await asyncio.sleep(min(backoff, 30.0))
                backoff = min(backoff * 2, 30.0)
            finally:
                await self._close_websocket()

        self._ready_event.clear()

    async def disconnect(self) -> None:
        """Stop the WebSocket connection."""
        self.running = False
        self._ready_event.clear()
        await self._close_websocket()
        if self.logger:
            self.logger.info("[BACKPACK] WebSocket disconnected")

    def update_symbol(self, symbol: Optional[str]) -> None:
        """Update symbol subscription (takes effect on next reconnect)."""
        self.symbol = symbol

    def set_order_filled_event(self, event) -> None:
        """Compatibility hook used by some strategies."""
        self.order_filled_event = event

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    async def _connect_once(self) -> None:
        """Establish a single WebSocket connection and subscribe to feeds."""
        if not self.symbol:
            if self.logger:
                self.logger.debug("[BACKPACK] No symbol configured for WebSocket subscription")
            self._ready_event.set()
            await asyncio.sleep(0.1)
            return

        if self.logger:
            self.logger.info(f"[BACKPACK] Connecting WebSocket for {self.symbol}")

        self.websocket = await websockets.connect(self.ws_url)
        await self._subscribe()
        self._ready_event.set()

    async def _subscribe(self) -> None:
        """Send subscription message for order updates."""
        if not self.websocket or not self.symbol:
            return

        timestamp = int(time.time() * 1000)
        signature = self._generate_signature("subscribe", timestamp)

        subscribe_message = {
            "method": "SUBSCRIBE",
            "params": [f"account.orderUpdate.{self.symbol}"],
            "signature": [
                self.public_key,
                signature,
                str(timestamp),
                "5000",
            ],
        }

        await self.websocket.send(json.dumps(subscribe_message))
        if self.logger:
            self.logger.info(f"[BACKPACK] Subscribed to order updates for {self.symbol}")

    async def _listen(self) -> None:
        """Process incoming WebSocket messages."""
        if not self.websocket:
            await asyncio.sleep(0.5)
            return

        try:
            async for message in self.websocket:
                if not self.running:
                    break
                await self._handle_message(message)
        except websockets.exceptions.ConnectionClosed:
            if self.logger:
                self.logger.warning("[BACKPACK] WebSocket connection closed")

    async def _handle_message(self, message: str) -> None:
        """Dispatch parsed WebSocket payloads."""
        try:
            data = json.loads(message)
        except json.JSONDecodeError as exc:
            if self.logger:
                self.logger.error(f"[BACKPACK] Failed to decode WS message: {exc}")
            return

        stream = data.get("stream", "")
        payload = data.get("data", {})

        if "orderUpdate" in stream:
            await self._handle_order_update(payload)
        elif self.logger:
            self.logger.debug(f"[BACKPACK] Unhandled WS message: {data}")

    async def _handle_order_update(self, payload: Dict[str, Any]) -> None:
        """Invoke order update callback."""
        if not self.order_update_callback:
            return

        try:
            await self.order_update_callback(payload)
        except Exception as exc:  # pragma: no cover - callback safety
            if self.logger:
                self.logger.error(f"[BACKPACK] Order update callback failed: {exc}")

    def _generate_signature(self, instruction: str, timestamp: int, window: int = 5000) -> str:
        """Generate ED25519 signature for WebSocket authentication."""
        message = f"instruction={instruction}&timestamp={timestamp}&window={window}"
        signature_bytes = self.private_key.sign(message.encode())
        return base64.b64encode(signature_bytes).decode()

    async def _close_websocket(self) -> None:
        """Close current WebSocket connection if open."""
        if self.websocket:
            try:
                await self.websocket.close()
            except Exception:
                pass
            finally:
                self.websocket = None
