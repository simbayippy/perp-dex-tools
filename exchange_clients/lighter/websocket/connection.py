"""
WebSocket connection management for Lighter.

Handles connection lifecycle, reconnection, session management, and proxy configuration.
"""

import asyncio
import os
from typing import Dict, Any, Optional, Callable
from urllib.parse import urlparse

import aiohttp

from exchange_clients.base_websocket import BaseWebSocketManager


class LighterWebSocketConnection:
    """Manages WebSocket connection lifecycle and configuration."""

    RECONNECT_BACKOFF_INITIAL = 1.0
    RECONNECT_BACKOFF_MAX = 30.0
    RECEIVE_TIMEOUT = 45.0  # seconds - timeout for receiving messages (detects dead connections)

    def __init__(self, config: Dict[str, Any], logger: Optional[Any] = None):
        """
        Initialize connection manager.
        
        Args:
            config: Configuration object
            logger: Logger instance
        """
        self.config = config
        self.logger = logger
        self.ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._listener_task: Optional[asyncio.Task] = None
        self.ws_url = "wss://mainnet.zklighter.elliot.ai/stream"

    def set_logger(self, logger):
        """Set the logger instance."""
        self.logger = logger

    def _log(self, message: str, level: str = "INFO"):
        """Log message using the logger if available."""
        if self.logger:
            self.logger.log(message, level)

    async def _get_session(self) -> aiohttp.ClientSession:
        """
        Lazily initialize the aiohttp session used for websocket connections.

        The session is configured with trust_env=False so that only the explicit
        proxy arguments we pass are respected. This keeps behavior predictable
        when SessionProxyManager has applied environment variables.
        """
        if self._session and not self._session.closed:
            return self._session

        timeout = aiohttp.ClientTimeout(total=None)
        self._session = aiohttp.ClientSession(timeout=timeout, trust_env=False)
        return self._session

    async def _close_session(self) -> None:
        """Close the websocket session if it exists."""
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    def _proxy_kwargs(self) -> Dict[str, Any]:
        """
        Build proxy kwargs for aiohttp based on the active HTTP proxy.

        Returns empty dict when no HTTP proxy is configured. SOCKS proxies are
        already handled via SessionProxyManager's socket patching, so we skip
        explicit wiring in that case to avoid incompatible schemes.
        """
        proxy_url = (
            os.environ.get("HTTPS_PROXY")
            or os.environ.get("HTTP_PROXY")
            or os.environ.get("ALL_PROXY")
            or os.environ.get("https_proxy")
            or os.environ.get("http_proxy")
            or os.environ.get("all_proxy")
        )

        if not proxy_url:
            return {}

        parsed = urlparse(proxy_url)
        if parsed.scheme.lower() not in {"http", "https"}:
            # aiohttp does not support socks proxies natively; rely on socket patching.
            return {}

        proxy_auth = None
        if parsed.username or parsed.password:
            proxy_auth = aiohttp.BasicAuth(parsed.username or "", parsed.password or "")
            hostname = parsed.hostname or ""
            port = f":{parsed.port}" if parsed.port else ""
            netloc = f"{hostname}{port}"
            parsed = parsed._replace(netloc=netloc, path="", params="", query="", fragment="")

        clean_proxy_url = parsed.geturl()
        if proxy_auth:
            return {"proxy": clean_proxy_url, "proxy_auth": proxy_auth}
        return {"proxy": clean_proxy_url}

    async def open_connection(self) -> None:
        """Establish the websocket connection."""
        session = await self._get_session()
        proxy_kwargs = self._proxy_kwargs()
        if proxy_kwargs.get("proxy"):
            self._log(f"[LIGHTER] Using HTTP proxy for websocket: {proxy_kwargs['proxy']}", "INFO")

        try:
            self.ws = await session.ws_connect(
                self.ws_url,
                receive_timeout=self.RECEIVE_TIMEOUT,
                **proxy_kwargs,
            )
            self._log("[LIGHTER] 🔗 Connected to websocket", "INFO")
            self._log(
                f"[LIGHTER] Websocket receive timeout: {self.RECEIVE_TIMEOUT:.0f}s "
                f"(server handles ping/pong messages)",
                "INFO",
            )
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            self._log(f"Failed to connect to Lighter websocket: {exc}", "ERROR")
            raise

    async def cleanup_current_ws(self) -> None:
        """Close the active websocket connection if it exists."""
        if self.ws and not self.ws.closed:
            try:
                await self.ws.close()
                # Wait a brief moment for the close to complete
                # This helps prevent "Cannot write to closing transport" errors
                await asyncio.sleep(0.1)
            except Exception as exc:
                # Ignore errors during cleanup (websocket might already be closed)
                self._log(f"Error closing websocket during cleanup: {exc}", "DEBUG")
        self.ws = None

    async def reconnect(
        self, 
        reset_order_book_fn, 
        subscribe_channels_fn, 
        running: bool,
        update_component_references_fn: Optional[Callable[[], None]] = None,
    ) -> None:
        """
        Attempt to reconnect with exponential backoff.
        
        Args:
            reset_order_book_fn: Function to reset order book state
            subscribe_channels_fn: Function to subscribe to channels
            running: Whether the manager is still running
            update_component_references_fn: Optional function to update component references
                                          after opening new connection (should be called
                                          before subscribe_channels_fn to ensure components
                                          use the new websocket)
        """
        delay = self.RECONNECT_BACKOFF_INITIAL
        attempt = 1
        while running:
            self._log(
                f"[LIGHTER] Reconnecting websocket (attempt {attempt})",
                "WARNING",
            )
            try:
                await reset_order_book_fn()
                await self.open_connection()
                
                # CRITICAL: Update component references BEFORE subscribing
                # This ensures market_switcher and message_handler use the NEW websocket
                # instead of the old closing one (which causes "Cannot write to closing transport")
                if update_component_references_fn:
                    update_component_references_fn()
                
                await subscribe_channels_fn()
                self._log("[LIGHTER] Websocket reconnect successful", "INFO")
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._log(
                    f"[LIGHTER] Reconnect attempt {attempt} failed: {exc}. Retrying in {delay:.1f}s",
                    "ERROR",
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, self.RECONNECT_BACKOFF_MAX)
                attempt += 1

        self._log("[LIGHTER] Reconnect aborted; manager no longer running", "WARNING")

