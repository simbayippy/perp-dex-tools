"""
Market switching logic for Paradex WebSocket.

Handles market subscription switching, contract ID lookup, and subscription management.
"""

from typing import Dict, Any, Optional

from exchange_clients.paradex.common import get_paradex_symbol_format


class ParadexMarketSwitcher:
    """Manages market switching and subscription logic."""

    def __init__(
        self,
        config: Dict[str, Any],
        ws_client: Optional[Any],
        running: bool,
        logger: Optional[Any] = None,
    ):
        """
        Initialize market switcher.
        
        Args:
            config: Configuration object
            ws_client: Paradex WebSocket client instance
            running: Whether the manager is running
            logger: Logger instance
        """
        self.config = config
        self.ws_client = ws_client
        self.running = running
        self.logger = logger
        self.current_contract_id = getattr(config, 'contract_id', None)
        
        # Track subscriptions (channel_name -> True)
        self._subscriptions: Dict[str, bool] = {}

    def set_logger(self, logger):
        """Set the logger instance."""
        self.logger = logger

    def set_ws_client(self, ws_client):
        """Set the WebSocket client."""
        self.ws_client = ws_client

    def set_running(self, running: bool):
        """Set the running state."""
        self.running = running

    def lookup_contract_id(self, symbol: str) -> Optional[str]:
        """
        Look up the contract_id for a given symbol.
        
        For Paradex, contract_id is simply "{SYMBOL}-USD-PERP".
        
        Args:
            symbol: Normalized symbol (e.g., "BTC", "ETH")
            
        Returns:
            Contract ID string (e.g., "BTC-USD-PERP"), or None if invalid
        """
        if not symbol:
            return None
        
        # Convert to Paradex format
        contract_id = get_paradex_symbol_format(symbol)
        return contract_id
    
    def validate_market_switch_needed(self, target_contract_id: str) -> bool:
        """
        Check if a market switch is actually needed.
        
        Args:
            target_contract_id: Target contract ID (e.g., "BTC-USD-PERP")
            
        Returns:
            True if switch is needed, False if already on target
        """
        if not self.ws_client or not self.running:
            if self.logger:
                self.logger.warning(f"Cannot switch market: WebSocket not connected")
            return False
        
        if self.current_contract_id == target_contract_id:
            if self.logger:
                self.logger.debug(f"Already subscribed to market {target_contract_id}")
            return False
        
        return True
    
    def _get_channel_names(self, contract_id: str) -> Dict[str, str]:
        """
        Get channel names for a contract_id.
        
        Args:
            contract_id: Contract ID (e.g., "BTC-USD-PERP")
            
        Returns:
            Dictionary mapping channel type to channel name
        """
        return {
            'orders': f"orders.{contract_id}",
            'order_book': f"order_book.{contract_id}.snapshot@15@100ms@0_1",
            'bbo': f"bbo.{contract_id}",
            'fills': f"fills.{contract_id}",
        }
    
    async def unsubscribe_market(self, contract_id: str) -> None:
        """Unsubscribe from all channels for a market."""
        if not self.ws_client:
            return
        
        channel_names = self._get_channel_names(contract_id)
        
        for channel_type, channel_name in channel_names.items():
            try:
                # Check if we're actually subscribed
                subscriptions = self.ws_client.get_subscriptions()
                if channel_name in subscriptions and subscriptions[channel_name]:
                    await self.ws_client.unsubscribe_by_name(channel_name)
                    self._subscriptions.pop(channel_name, None)
                    if self.logger:
                        self.logger.debug(f"Unsubscribed from {channel_type} for {contract_id}")
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"Error unsubscribing from {channel_type} for {contract_id}: {e}")
    
    async def subscribe_market(
        self,
        contract_id: str,
        order_callback: Optional[Any] = None,
        order_book_callback: Optional[Any] = None,
        bbo_callback: Optional[Any] = None,
        fills_callback: Optional[Any] = None,
    ) -> None:
        """
        Subscribe to all channels for a market.
        
        Args:
            contract_id: Contract ID (e.g., "BTC-USD-PERP")
            order_callback: Callback for order updates
            order_book_callback: Callback for order book updates
            bbo_callback: Callback for BBO updates
            fills_callback: Callback for fills updates
        """
        if not self.ws_client:
            return
        
        from paradex_py.api.ws_client import ParadexWebsocketChannel
        
        channel_names = self._get_channel_names(contract_id)
        
        try:
            # Subscribe to orders
            if order_callback:
                await self.ws_client.subscribe(
                    ParadexWebsocketChannel.ORDERS,
                    callback=order_callback,
                    params={"market": contract_id}
                )
                self._subscriptions[channel_names['orders']] = True
            
            # Subscribe to order book
            if order_book_callback:
                await self.ws_client.subscribe(
                    ParadexWebsocketChannel.ORDER_BOOK,
                    callback=order_book_callback,
                    params={
                        "market": contract_id,
                        "depth": 15,
                        "refresh_rate": "100ms",
                        "price_tick": "0_1",
                    }
                )
                self._subscriptions[channel_names['order_book']] = True
            
            # Subscribe to BBO
            if bbo_callback:
                await self.ws_client.subscribe(
                    ParadexWebsocketChannel.BBO,
                    callback=bbo_callback,
                    params={"market": contract_id}
                )
                self._subscriptions[channel_names['bbo']] = True
            
            # Subscribe to fills
            if fills_callback:
                await self.ws_client.subscribe(
                    ParadexWebsocketChannel.FILLS,
                    callback=fills_callback,
                    params={"market": contract_id}
                )
                self._subscriptions[channel_names['fills']] = True
            
            if self.logger:
                self.logger.debug(f"Subscribed to all channels for {contract_id}")
            
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error subscribing to channels for {contract_id}: {e}")
            raise

    async def subscribe_channels(
        self,
        contract_id: str,
        order_callback: Optional[Any] = None,
        order_book_callback: Optional[Any] = None,
        bbo_callback: Optional[Any] = None,
        fills_callback: Optional[Any] = None,
    ) -> None:
        """
        Subscribe to the required Paradex channels for a market.
        
        Args:
            contract_id: Contract ID (e.g., "BTC-USD-PERP")
            order_callback: Callback for order updates
            order_book_callback: Callback for order book updates
            bbo_callback: Callback for BBO updates
            fills_callback: Callback for fills updates
        """
        if not self.ws_client:
            raise RuntimeError("WebSocket client not available")
        
        await self.subscribe_market(
            contract_id=contract_id,
            order_callback=order_callback,
            order_book_callback=order_book_callback,
            bbo_callback=bbo_callback,
            fills_callback=fills_callback,
        )
        
        # Update current contract_id
        self.current_contract_id = contract_id

    def update_market_config(self, contract_id: str):
        """Update config to keep it synchronized (critical for order placement!)."""
        if hasattr(self.config, 'contract_id'):
            self.config.contract_id = contract_id
        self.current_contract_id = contract_id

    def log_market_switch_result(
        self, 
        old_contract_id: Optional[str], 
        new_contract_id: str, 
        success: bool,
        order_book_size: Dict[str, int]
    ) -> None:
        """Log the result of a market switch operation."""
        if not self.logger:
            return
        if success:
            self.logger.info(
                f"[PARADEX] ✅ Switched from {old_contract_id} to {new_contract_id} "
                f"({order_book_size.get('bids', 0)} bids, {order_book_size.get('asks', 0)} asks) | "
                f"config.contract_id updated to {new_contract_id}"
            )
        else:
            self.logger.warning(
                f"[PARADEX] ⚠️  Switched to {new_contract_id} but order book not ready yet "
                f"(timeout after 5.0s)"
            )

