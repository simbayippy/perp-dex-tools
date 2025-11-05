"""
Market switching logic for Lighter WebSocket.

Handles market subscription switching, market ID lookup, and subscription management.
"""

import json
import time
from typing import Dict, Any, Optional


class LighterMarketSwitcher:
    """Manages market switching and subscription logic."""

    def __init__(
        self,
        config: Dict[str, Any],
        ws: Optional[Any],
        running: bool,
        logger: Optional[Any] = None,
    ):
        """
        Initialize market switcher.
        
        Args:
            config: Configuration object
            ws: WebSocket connection object
            running: Whether the manager is running
            logger: Logger instance
        """
        self.config = config
        self.ws = ws
        self.running = running
        self.logger = logger
        self.market_index = config.contract_id
        self.account_index = config.account_index
        self.lighter_client = config.lighter_client

    def set_logger(self, logger):
        """Set the logger instance."""
        self.logger = logger

    def set_ws(self, ws):
        """Set the WebSocket connection."""
        self.ws = ws

    def set_running(self, running: bool):
        """Set the running state."""
        self.running = running

    def _log(self, message: str, level: str = "INFO"):
        """Log message using the logger if available."""
        if self.logger:
            self.logger.log(message, level)

    async def lookup_market_id(self, symbol: str) -> Optional[int]:
        """
        Look up the market_id for a given symbol by querying available markets.
        
        Args:
            symbol: Normalized symbol (e.g., "TOSHI", "PYTH")
            
        Returns:
            Integer market_id, or None if not found
        """
        # Import here to avoid circular dependency
        import lighter
        from exchange_clients.lighter.common import get_lighter_symbol_format
        
        # Convert normalized symbol to Lighter's format (e.g., "TOSHI" -> "1000TOSHI")
        lighter_symbol = get_lighter_symbol_format(symbol)
        
        # Validate dependencies
        if not hasattr(self.config, 'lighter_client') or self.config.lighter_client is None:
            self._log(
                f"[LIGHTER] No lighter_client available; cannot look up market_id for {symbol}",
                "WARNING",
            )
            return None
        
        api_client = getattr(self.config, 'api_client', None)
        if api_client is None:
            self._log(
                f"[LIGHTER] No api_client available; cannot look up market_id for {symbol}",
                "WARNING",
            )
            return None
        
        # Query available markets
        order_api = lighter.OrderApi(api_client)
        order_books = await order_api.order_books()
        
        # Find matching market
        for market in order_books.order_books:
            # Try Lighter-specific format first (e.g., "1000TOSHI")
            if market.symbol.upper() == lighter_symbol.upper():
                return market.market_id
            # Try exact match with original symbol
            elif market.symbol.upper() == symbol.upper():
                return market.market_id
        
        # Not found
        self._log(
            f"[LIGHTER] Symbol '{symbol}' (as '{lighter_symbol}') not found in available markets",
            "WARNING",
        )
        return None
    
    def validate_market_switch_needed(self, target_market: int) -> bool:
        """
        Check if a market switch is actually needed.
        
        Args:
            target_market: Target market_id
            
        Returns:
            True if switch is needed, False if already on target
        """
        if not self.ws or not self.running:
            self._log(f"Cannot switch market: WebSocket not connected", "WARNING")
            return False
        
        if self.market_index == target_market:
            self._log(f"Already subscribed to market {target_market}", "DEBUG")
            return False
        
        return True
    
    async def unsubscribe_market(self, market_id: int) -> None:
        """Unsubscribe from order book and account orders for a market."""
        if not self.ws:
            return
            
        # Unsubscribe from order book
        unsubscribe_msg = json.dumps({
            "type": "unsubscribe",
            "channel": f"order_book/{market_id}"
        })
        await self.ws.send_str(unsubscribe_msg)

        # Unsubscribe from account orders
        account_unsub_msg = json.dumps({
            "type": "unsubscribe",
            "channel": f"account_orders/{market_id}/{self.account_index}"
        })
        await self.ws.send_str(account_unsub_msg)
    
    async def subscribe_market(self, market_id: int) -> None:
        """Subscribe to order book and account orders for a market."""
        if not self.ws:
            return
            
        # Subscribe to order book
        subscribe_msg = json.dumps({
            "type": "subscribe",
            "channel": f"order_book/{market_id}"
        })
        await self.ws.send_str(subscribe_msg)

        # Subscribe to account orders (with auth)
        auth_token = None
        if self.lighter_client:
            try:
                expiry = int(time.time() + 10 * 60)
                auth_token, err = self.lighter_client.create_auth_token_with_expiry(expiry)
                if err:
                    self._log(f"Failed to create auth token for market switch: {err}", "WARNING")
            except Exception as exc:
                self._log(f"Error creating auth token for market switch: {exc}", "ERROR")

        account_sub_msg = {
            "type": "subscribe",
            "channel": f"account_orders/{market_id}/{self.account_index}",
        }
        if auth_token:
            account_sub_msg["auth"] = auth_token
        await self.ws.send_str(json.dumps(account_sub_msg))

    async def subscribe_channels(
        self,
        subscribe_positions: bool = False,
        subscribe_liquidations: bool = False,
        subscribe_user_stats: bool = False,
    ) -> None:
        """
        Subscribe to the required Lighter channels.
        
        Args:
            subscribe_positions: Whether to subscribe to positions channel
            subscribe_liquidations: Whether to subscribe to liquidations channel
            subscribe_user_stats: Whether to subscribe to user stats channel
        """
        if not self.ws:
            raise RuntimeError("WebSocket connection not available")

        await self.ws.send_str(json.dumps({
            "type": "subscribe",
            "channel": f"order_book/{self.market_index}"
        }))

        auth_token = None
        if self.lighter_client:
            try:
                expiry = int(time.time() + 10 * 60)
                auth_token, err = self.lighter_client.create_auth_token_with_expiry(expiry)
                if err:
                    self._log(f"Failed to create auth token for account orders subscription: {err}", "WARNING")
                    auth_token = None
            except Exception as exc:
                self._log(f"Error creating auth token for account orders subscription: {exc}", "ERROR")
                auth_token = None
        else:
            self._log(
                "No lighter client available - cannot subscribe to account orders or notifications",
                "WARNING",
            )

        subscription_messages = []
        if auth_token:
            subscription_messages.append({
                "type": "subscribe",
                "channel": f"account_orders/{self.market_index}/{self.account_index}",
                "auth": auth_token,
            })
            if subscribe_positions:
                subscription_messages.append({
                    "type": "subscribe",
                    "channel": f"account_all_positions/{self.account_index}",
                    "auth": auth_token,
                })
            if subscribe_liquidations:
                subscription_messages.append({
                    "type": "subscribe",
                    "channel": f"notification/{self.account_index}",
                    "auth": auth_token,
                })
            if subscribe_user_stats:
                subscription_messages.append({
                    "type": "subscribe",
                    "channel": f"user_stats/{self.account_index}",
                    "auth": auth_token,
                })
        
        for message in subscription_messages:
            await self.ws.send_str(json.dumps(message))

    def update_market_config(self, market_id: int):
        """Update config to keep it synchronized (critical for order placement!)."""
        if hasattr(self.config, 'contract_id'):
            self.config.contract_id = market_id

    def log_market_switch_result(
        self, 
        old_market_id: int, 
        new_market_id: int, 
        success: bool,
        order_book_size: Dict[str, int]
    ) -> None:
        """Log the result of a market switch operation."""
        if success:
            self._log(
                f"[LIGHTER] ✅ Switched order book from market {old_market_id} to {new_market_id} "
                f"({order_book_size.get('bids', 0)} bids, {order_book_size.get('asks', 0)} asks) | "
                f"config.contract_id updated to {new_market_id}",
                "INFO"
            )
        else:
            self._log(
                f"[LIGHTER] ⚠️  Switched to market {new_market_id} but snapshot not loaded yet "
                f"(timeout after 5.0s)",
                "WARNING"
            )

