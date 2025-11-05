"""
WebSocket connection management for Backpack.

Handles connection lifecycle for both account (private) and depth (public) streams.
"""

import asyncio
import base64
import json
import time
from typing import Any, Callable, Dict, Optional

from cryptography.hazmat.primitives.asymmetric import ed25519
import websockets

from exchange_clients.base_models import MissingCredentialsError


class BackpackWebSocketConnection:
    """Manages WebSocket connection lifecycle for account and depth streams."""

    _MAX_BACKOFF_SECONDS = 30.0

    def __init__(
        self,
        public_key: str,
        secret_key: str,
        ws_url: str = "wss://ws.backpack.exchange",
        logger: Optional[Any] = None,
    ):
        """
        Initialize connection manager.
        
        Args:
            public_key: Backpack public key
            secret_key: Backpack secret key (base64 encoded)
            ws_url: WebSocket URL
            logger: Logger instance
        """
        self.public_key = public_key
        self.secret_key = secret_key
        self.ws_url = ws_url
        self.logger = logger
        
        # Account stream connection (exposed for manager access)
        self._account_ws: Optional[websockets.WebSocketClientProtocol] = None
        
        # Depth stream connection (exposed for manager access)
        self._depth_ws: Optional[websockets.WebSocketClientProtocol] = None
        
        # Running state
        self.running = False
        
        # Initialize private key for signature generation
        try:
            secret_bytes = base64.b64decode(secret_key)
            self.private_key = ed25519.Ed25519PrivateKey.from_private_bytes(secret_bytes)
        except Exception as exc:
            raise MissingCredentialsError(f"Invalid Backpack secret key: {exc}") from exc

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

    def _generate_signature(self, instruction: str, timestamp: int, window: int = 5000) -> str:
        """
        Generate Ed25519 signature for WebSocket authentication.
        
        Args:
            instruction: Instruction string (e.g., "subscribe")
            timestamp: Timestamp in milliseconds
            window: Window in milliseconds
            
        Returns:
            Base64-encoded signature
        """
        message = f"instruction={instruction}&timestamp={timestamp}&window={window}"
        signature_bytes = self.private_key.sign(message.encode())
        return base64.b64encode(signature_bytes).decode()

    async def connect_account_stream(self, symbol: str) -> websockets.WebSocketClientProtocol:
        """
        Connect to account (private) WebSocket stream.
        
        Args:
            symbol: Symbol to subscribe to
            
        Returns:
            WebSocket connection
        """
        if self.logger:
            self.logger.info(f"[BACKPACK] Connecting account stream for {symbol}")

        ws = await websockets.connect(self.ws_url)
        await self._subscribe_account_stream(ws, symbol)
        return ws

    async def _subscribe_account_stream(self, ws: websockets.WebSocketClientProtocol, symbol: str) -> None:
        """
        Subscribe to account order update stream.
        
        Args:
            ws: WebSocket connection
            symbol: Symbol to subscribe to
        """
        timestamp = int(time.time() * 1000)
        signature = self._generate_signature("subscribe", timestamp)

        message = {
            "method": "SUBSCRIBE",
            "params": [f"account.orderUpdate.{symbol}"],
            "signature": [
                self.public_key,
                signature,
                str(timestamp),
                "5000",
            ],
        }

        await ws.send(json.dumps(message))
        if self.logger:
            self.logger.info(f"[BACKPACK] Subscribed to account.orderUpdate.{symbol}")

    async def connect_depth_stream(self, symbol: str, depth_stream_interval: str = "realtime") -> websockets.WebSocketClientProtocol:
        """
        Connect to depth (public) WebSocket stream.
        
        Args:
            symbol: Symbol to subscribe to
            depth_stream_interval: Depth stream interval (e.g., "realtime")
            
        Returns:
            WebSocket connection
        """
        if self.logger:
            self.logger.info(f"[BACKPACK] Connecting depth stream for {symbol}")

        ws = await websockets.connect(self.ws_url)
        await self._subscribe_depth_stream(ws, symbol, depth_stream_interval)
        return ws

    async def _subscribe_depth_stream(
        self,
        ws: websockets.WebSocketClientProtocol,
        symbol: str,
        depth_stream_interval: str = "realtime",
    ) -> None:
        """
        Subscribe to depth and book ticker streams.
        
        Args:
            ws: WebSocket connection
            symbol: Symbol to subscribe to
            depth_stream_interval: Depth stream interval
        """
        # Build depth stream name
        if depth_stream_interval == "realtime" or not depth_stream_interval:
            prefix = "depth"
        else:
            prefix = f"depth.{depth_stream_interval}"
        depth_stream = f"{prefix}.{symbol}"
        
        streams = [depth_stream, f"bookTicker.{symbol}"]
        message = {
            "method": "SUBSCRIBE",
            "params": streams,
        }

        await ws.send(json.dumps(message))
        if self.logger:
            self.logger.info(f"[BACKPACK] Subscribed to streams: {streams}")

    async def close_account_ws(self, ws: Optional[websockets.WebSocketClientProtocol]) -> None:
        """Close account WebSocket connection."""
        if ws:
            try:
                await ws.close()
            except Exception:
                pass

    async def close_depth_ws(self, ws: Optional[websockets.WebSocketClientProtocol]) -> None:
        """Close depth WebSocket connection."""
        if ws:
            try:
                await ws.close()
            except Exception:
                pass

