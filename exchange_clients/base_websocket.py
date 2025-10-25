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
