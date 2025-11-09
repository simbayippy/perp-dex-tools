"""
Message parsing and routing for Paradex WebSocket.

Handles incoming WebSocket message parsing, type detection, and routing.
Note: Paradex SDK uses callbacks for message handling, but this module
provides message parsing utilities and can be used for debugging/logging.
"""

import asyncio
import json
import time
from typing import Dict, Any, List, Optional, Callable, Awaitable

from exchange_clients.base_websocket import BBOData


class ParadexMessageHandler:
    """Handles WebSocket message parsing and routing."""

    def __init__(
        self,
        config: Dict[str, Any],
        order_book_manager: Any,
        order_update_callback: Optional[Callable] = None,
        liquidation_callback: Optional[Callable[[List[Dict[str, Any]]], Awaitable[None]]] = None,
        positions_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
        user_stats_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
        notify_bbo_update_fn: Optional[Callable] = None,
        logger: Optional[Any] = None,
    ):
        """
        Initialize message handler.
        
        Args:
            config: Configuration object
            order_book_manager: Order book manager instance
            order_update_callback: Callback for order updates
            liquidation_callback: Callback for liquidations
            positions_callback: Callback for positions
            user_stats_callback: Callback for user stats
            notify_bbo_update_fn: Function to notify BBO updates
            logger: Logger instance
        """
        self.config = config
        self.order_book_manager = order_book_manager
        self.order_update_callback = order_update_callback
        self.liquidation_callback = liquidation_callback
        self.positions_callback = positions_callback
        self.user_stats_callback = user_stats_callback
        self.notify_bbo_update = notify_bbo_update_fn
        self.logger = logger

    def set_logger(self, logger):
        """Set the logger instance."""
        self.logger = logger

    def parse_message(self, raw_message: str) -> Optional[Dict[str, Any]]:
        """
        Parse a raw WebSocket message string into a dictionary.
        
        Paradex WebSocket messages are JSON-RPC 2.0 format:
        {
            "jsonrpc": "2.0",
            "method": "subscription",
            "params": {
                "channel": "channel_name",
                "data": {...}
            }
        }
        
        Args:
            raw_message: Raw message string from WebSocket
            
        Returns:
            Parsed message dictionary, or None if parsing fails
        """
        try:
            data = json.loads(raw_message)
            return data
        except json.JSONDecodeError as exc:
            if self.logger:
                self.logger.error(f"JSON parsing error in Paradex websocket: {exc}")
            return None

    def extract_channel_name(self, message: Dict[str, Any]) -> Optional[str]:
        """
        Extract channel name from a parsed message.
        
        Args:
            message: Parsed message dictionary
            
        Returns:
            Channel name string, or None if not found
        """
        params = message.get('params', {})
        channel = params.get('channel')
        return channel

    def extract_message_data(self, message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Extract data payload from a parsed message.
        
        Args:
            message: Parsed message dictionary
            
        Returns:
            Data dictionary, or None if not found
        """
        params = message.get('params', {})
        data = params.get('data')
        return data

    def is_subscription_message(self, message: Dict[str, Any]) -> bool:
        """
        Check if message is a subscription confirmation.
        
        Args:
            message: Parsed message dictionary
            
        Returns:
            True if message is a subscription confirmation
        """
        method = message.get('method')
        return method == 'subscription'

    def handle_order_book_message(self, channel: str, data: Dict[str, Any], market: str) -> None:
        """
        Handle order book update message.
        
        Args:
            channel: Channel name
            data: Message data payload
            market: Market symbol
        """
        try:
            self.order_book_manager.update_order_book(market, data)
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error handling order book message: {e}")

    def handle_bbo_message(self, channel: str, data: Dict[str, Any], market: str) -> None:
        """
        Handle BBO (Best Bid/Offer) message.
        
        Args:
            channel: Channel name
            data: Message data payload
            market: Market symbol
        """
        try:
            bid = data.get('bid') or data.get('best_bid')
            ask = data.get('ask') or data.get('best_ask')
            
            if bid and ask and self.notify_bbo_update:
                bbo = BBOData(
                    symbol=market,
                    bid=bid,
                    ask=ask,
                    timestamp=time.time(),
                )
                # Note: notify_bbo_update is async, but we can't await here
                # The actual callback invocation happens in manager
                if self.logger:
                    self.logger.debug(
                        f"[PARADEX] BBO update for {market}: bid={bid}, ask={ask}"
                    )
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error handling BBO message: {e}")

    def handle_order_message(self, channel: str, data: Dict[str, Any]) -> None:
        """
        Handle order update message.
        
        Args:
            channel: Channel name
            data: Message data payload
        """
        try:
            if self.order_update_callback:
                if asyncio.iscoroutinefunction(self.order_update_callback):
                    # Note: Can't await here, callback will be invoked in manager
                    if self.logger:
                        self.logger.debug(f"[PARADEX] Order update received on {channel}")
                else:
                    self.order_update_callback(data)
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error handling order message: {e}")

    def handle_fill_message(self, channel: str, data: Dict[str, Any]) -> None:
        """
        Handle fill message (includes liquidations).
        
        Args:
            channel: Channel name
            data: Message data payload
        """
        try:
            fill_type = data.get('fill_type') or data.get('trade_type')
            if fill_type == "LIQUIDATION" and self.liquidation_callback:
                if self.logger:
                    self.logger.info(f"[PARADEX] Liquidation detected on {channel}")
                # Note: Callback will be invoked in manager
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error handling fill message: {e}")

    async def dispatch_liquidations(self, notifs: List[Dict[str, Any]]) -> None:
        """Forward liquidation notifications to the registered callback."""
        if not self.liquidation_callback or not notifs:
            return

        try:
            await self.liquidation_callback(notifs)
        except Exception as exc:
            if self.logger:
                self.logger.error(f"Error dispatching liquidation notifications: {exc}")

    async def dispatch_positions(self, payload: Dict[str, Any]) -> None:
        """Forward positions update to the registered callback."""
        if not self.positions_callback:
            return

        try:
            await self.positions_callback(payload)
        except Exception as exc:
            if self.logger:
                self.logger.error(f"Error dispatching positions update: {exc}")

    async def dispatch_user_stats(self, payload: Dict[str, Any]) -> None:
        """Forward user stats update to the registered callback."""
        if not self.user_stats_callback:
            return

        try:
            await self.user_stats_callback(payload)
        except Exception as exc:
            if self.logger:
                self.logger.error(f"Error dispatching user stats update: {exc}")

