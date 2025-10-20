"""
Base WebSocket manager interface and helpers.

Defines the minimal contract that custom exchange websocket managers must
implement so higher-level strategy code can interact with them uniformly.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional


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

    def set_logger(self, logger: Any) -> None:
        """Attach a logger instance (expects unified_logger-style interface)."""
        self.logger = logger

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
        """
