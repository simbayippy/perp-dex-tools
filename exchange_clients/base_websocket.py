"""
Base WebSocket manager interface and helpers.

Defines the minimal contract that custom exchange websocket managers must
implement so higher-level strategy code can interact with them uniformly.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Awaitable, Callable, Optional

import websockets

from networking import ProxySelector, ProxyEndpoint
from networking.exceptions import ProxyUnavailableError
from networking.websocket import connect_via_proxy


class BaseWebSocketManager(ABC):
    """
    Lightweight abstract base class for exchange websocket managers.

    Implementations are responsible for maintaining their own connection state
    but must expose a consistent surface so strategies can request market data
    streams for specific symbols before placing orders.
    """

    def __init__(self) -> None:
        self.logger: Any = None
        self.running: bool = False
        self._bbo_listeners: list[Callable[["BBOData"], Optional[Awaitable[None]]]] = []
        self._latest_bbo: Optional["BBOData"] = None
        self._proxy_selector: Optional[ProxySelector] = None

    def set_logger(self, logger: Any) -> None:
        """Attach a logger instance (expects unified_logger-style interface)."""
        self.logger = logger

    def set_proxy_selector(self, selector: Optional[ProxySelector]) -> None:
        """Attach a proxy selector to the websocket manager."""
        self._proxy_selector = selector

    def _log(self, message: str, level: str = "INFO") -> None:
        if self.logger and hasattr(self.logger, "log"):
            self.logger.log(message, level)

    def _acquire_proxy(self) -> Optional[ProxyEndpoint]:
        if not self._proxy_selector:
            return None
        try:
            return self._proxy_selector.current()
        except ProxyUnavailableError as exc:
            self._log(f"No proxy available: {exc}", "WARNING")
            return None

    def _mark_proxy_unhealthy(self, endpoint: ProxyEndpoint) -> None:
        if not self._proxy_selector:
            return
        try:
            self._proxy_selector.mark_unhealthy(endpoint)
        except ProxyUnavailableError:
            pass

    async def _connect_via_proxy(
        self,
        uri: str,
        *,
        connect_kwargs: Optional[dict[str, Any]] = None,
    ):
        """
        Helper to establish a websocket connection with optional proxy support.

        Returns:
            The websocket connection object.
        """
        connect_kwargs = connect_kwargs or {}
        proxy_endpoint = self._acquire_proxy()
        try:
            if proxy_endpoint:
                self._log(
                    f"Connecting websocket via proxy '{proxy_endpoint.label}' -> {uri}",
                    "DEBUG",
                )
                return await connect_via_proxy(uri, proxy_endpoint, connect_kwargs=connect_kwargs)
            return await websockets.connect(uri, **connect_kwargs)  # type: ignore[name-defined]  # noqa: F821
        except Exception as exc:
            if proxy_endpoint:
                self._log(
                    f"Proxy '{proxy_endpoint.label}' failed for {uri}: {exc}",
                    "WARNING",
                )
                self._mark_proxy_unhealthy(proxy_endpoint)
            raise

    @abstractmethod
    async def connect(self) -> None:
        """Establish websocket connections and start background processing."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Tear down websocket connections and cancel background tasks."""

    @abstractmethod
    async def prepare_market_feed(self, symbol: Optional[str]) -> None:
        """
        Ensure websocket subscriptions align with the requested trading symbol.

        Implementations may subscribe to additional channels, restart streams,
        or no-op when the feed is already aligned. The symbol is provided in the
        strategy's normalized format and implementers are responsible for any
        required conversion.
        
        Recommended implementation pattern:
        1. Validate: Check if already on target market
        2. Clear: Reset stale order book data
        3. Switch: Unsubscribe old, subscribe new
        4. Wait: Block until new data arrives
        5. Update: Synchronize config state (contract_id, market_index, etc.)
        """
    
    def _update_market_config(self, market_identifier: Any) -> None:
        """
        Update config with new market identifier after successful market switch.
        
        This is a helper method that exchanges can use to keep config synchronized
        with the active market. Exchanges should override this if they have
        additional config fields to update.
        
        Args:
            market_identifier: Exchange-specific market ID (int, str, etc.)
        """
        if hasattr(self, 'config'):
            # Update common config fields that most exchanges use
            if hasattr(self.config, 'contract_id'):
                self.config.contract_id = market_identifier
            if hasattr(self.config, 'market_index'):
                self.config.market_index = market_identifier
            if hasattr(self.config, 'market_id'):
                self.config.market_id = market_identifier

    @abstractmethod
    def get_order_book(self, levels: Optional[int] = None) -> Optional[Any]:
        """
        Get formatted order book with optional level limiting.
        
        Args:
            levels: Optional number of levels to return per side.
            
        Returns:
            Order book dict with 'bids' and 'asks' lists, or None if not ready.
            Format: {'bids': [{'price': Decimal, 'size': Decimal}, ...], 
                     'asks': [{'price': Decimal, 'size': Decimal}, ...]}
        """
        ...

    # ------------------------------------------------------------------
    # Best bid/ask streaming helpers
    # ------------------------------------------------------------------

    def register_bbo_listener(
        self,
        listener: Callable[["BBOData"], Optional[Awaitable[None]]],
    ) -> None:
        """Register a listener that will be invoked on every BBO update."""
        if listener not in self._bbo_listeners:
            self._bbo_listeners.append(listener)

    def unregister_bbo_listener(
        self,
        listener: Callable[["BBOData"], Optional[Awaitable[None]]],
    ) -> None:
        """Remove a previously registered BBO listener."""
        if listener in self._bbo_listeners:
            self._bbo_listeners.remove(listener)

    def get_latest_bbo(self) -> Optional["BBOData"]:
        """Return the most recently cached BBO snapshot, if any."""
        return self._latest_bbo

    async def _notify_bbo_update(self, bbo: "BBOData") -> None:
        """Store and fan-out a new BBO update to listeners."""
        self._latest_bbo = bbo
        for listener in list(self._bbo_listeners):
            try:
                result = listener(bbo)
                if isinstance(result, Awaitable):
                    await result
            except Exception as exc:  # pragma: no cover - defensive logging
                if self.logger and hasattr(self.logger, "log"):
                    self.logger.log(f"BBO listener error: {exc}", "ERROR")


class BBOData:
    """Simple container for best bid/ask updates."""

    __slots__ = ("symbol", "bid", "ask", "timestamp", "sequence")

    def __init__(
        self,
        *,
        symbol: str,
        bid: Any,
        ask: Any,
        timestamp: float,
        sequence: Optional[int] = None,
    ) -> None:
        self.symbol = symbol
        self.bid = bid
        self.ask = ask
        self.timestamp = timestamp
        self.sequence = sequence
