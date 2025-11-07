"""
Main WebSocket manager for Paradex exchange.

Orchestrates connection, order book, message handling, and market switching.
"""

import asyncio
from typing import Dict, Any, List, Optional, Callable, Awaitable

from exchange_clients.base_websocket import BaseWebSocketManager, BBOData

from .connection import ParadexWebSocketConnection
from .order_book import ParadexOrderBook
from .market_switcher import ParadexMarketSwitcher
from .message_handler import ParadexMessageHandler


class ParadexWebSocketManager(BaseWebSocketManager):
    """WebSocket manager for Paradex order updates and order book."""

    def __init__(
        self,
        config: Dict[str, Any],
        paradex_ws_client: Any,
        order_update_callback: Optional[Callable] = None,
        liquidation_callback: Optional[Callable[[List[Dict[str, Any]]], Awaitable[None]]] = None,
        positions_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
        user_stats_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    ):
        """
        Initialize WebSocket manager.
        
        Args:
            config: Configuration object
            paradex_ws_client: Paradex SDK WebSocket client instance
            order_update_callback: Callback for order updates
            liquidation_callback: Callback for liquidations
            positions_callback: Callback for positions
            user_stats_callback: Callback for user stats
        """
        super().__init__()
        self.config = config
        self.paradex_ws_client = paradex_ws_client
        
        # Initialize components
        self.connection = ParadexWebSocketConnection(paradex_ws_client)
        self.order_book = ParadexOrderBook()
        self.market_switcher = ParadexMarketSwitcher(
            config=config,
            ws_client=paradex_ws_client,
            running=False,  # Will be set when connected
        )
        self.message_handler = ParadexMessageHandler(
            config=config,
            order_book_manager=self.order_book,
            order_update_callback=order_update_callback,
            liquidation_callback=liquidation_callback,
            positions_callback=positions_callback,
            user_stats_callback=user_stats_callback,
            notify_bbo_update_fn=self._notify_bbo_update,
        )
        
        # Store callbacks
        self.order_update_callback = order_update_callback
        self.liquidation_callback = liquidation_callback
        self.positions_callback = positions_callback
        self.user_stats_callback = user_stats_callback
        
        # Track running state
        self.running = False

    def set_logger(self, logger):
        """Set the logger instance for all components."""
        self.logger = logger
        self.connection.set_logger(logger)
        self.order_book.set_logger(logger)
        self.market_switcher.set_logger(logger)
        self.message_handler.set_logger(logger)

    # Delegate order book methods
    def get_order_book(self, levels: Optional[int] = None) -> Optional[Dict[str, List[Dict[str, Any]]]]:
        """Get formatted order book with optional level limiting."""
        return self.order_book.get_order_book(levels)

    def get_best_levels(
        self, min_size_usd: float = 0
    ) -> tuple[tuple[Optional[Any], Optional[Any]], tuple[Optional[Any], Optional[Any]]]:
        """Get the best bid and ask levels from order book."""
        return self.order_book.get_best_levels(min_size_usd)

    @property
    def best_bid(self) -> Optional[Any]:
        """Get current best bid."""
        return self.order_book.best_bid

    @property
    def best_ask(self) -> Optional[Any]:
        """Get current best ask."""
        return self.order_book.best_ask

    @property
    def order_book_ready(self) -> bool:
        """Check if order book is ready."""
        return self.order_book.order_book_ready

    async def connect(self) -> None:
        """Establish websocket connections and start background processing."""
        if self.running:
            if self.logger:
                self.logger.warning("[PARADEX] WebSocket manager already running")
            return
        
        # Connect using connection manager
        success = await self.connection.open_connection()
        if not success:
            raise RuntimeError("Failed to connect to Paradex WebSocket")
        
        self.running = True
        self.market_switcher.set_running(True)
        
        # Subscribe to initial market
        contract_id = getattr(self.config, 'contract_id', None)
        if contract_id:
            await self._subscribe_to_market(contract_id)
        
        if self.logger:
            self.logger.info("[PARADEX] 🔗 WebSocket connected and subscribed")

    async def disconnect(self) -> None:
        """Tear down websocket connections and cancel background tasks."""
        if not self.running:
            return
        
        self.running = False
        self.market_switcher.set_running(False)
        
        try:
            # Close connection using connection manager
            await self.connection.cleanup_current_ws()
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error closing WebSocket connection: {e}")
        
        if self.logger:
            self.logger.info("[PARADEX] WebSocket disconnected")

    async def prepare_market_feed(self, symbol: Optional[str]) -> None:
        """
        Ensure websocket subscriptions align with the requested trading symbol.
        
        Implementation follows the recommended pattern from BaseWebSocketManager:
        1. Validate: Check if already on target market
        2. Clear: Reset stale order book data
        3. Switch: Unsubscribe old, subscribe new
        4. Wait: Block until new data arrives
        5. Update: Synchronize config state
        """
        if symbol is None:
            return

        try:
            # Step 1: Lookup target contract_id for the symbol
            target_contract_id = self.market_switcher.lookup_contract_id(symbol)
            if target_contract_id is None:
                if self.logger:
                    self.logger.warning(f"[PARADEX] Cannot resolve contract_id for symbol {symbol}")
                return
            
            # Step 2: Validate if switch is needed
            if not self.market_switcher.validate_market_switch_needed(target_contract_id):
                return
            
            # Step 3: Perform the market switch
            old_contract_id = self.market_switcher.current_contract_id
            await self._perform_market_switch(old_contract_id, target_contract_id)
            
            # Step 4: Wait for new data to arrive
            success = await self._wait_for_market_ready(timeout=5.0)
            
            # Step 5: Log result
            order_book_size = {
                'bids': len(self.order_book.order_book['bids']),
                'asks': len(self.order_book.order_book['asks'])
            }
            self.market_switcher.log_market_switch_result(
                old_contract_id, target_contract_id, success, order_book_size
            )
            
        except Exception as exc:
            if self.logger:
                self.logger.error(f"[PARADEX] Error switching market: {exc}")

    async def _perform_market_switch(self, old_contract_id: Optional[str], new_contract_id: str) -> None:
        """Execute the market switch: unsubscribe old, subscribe new, update config."""
        if self.logger:
            self.logger.info(
                f"[PARADEX] 🔄 Switching from {old_contract_id} to {new_contract_id}"
            )
        
        # Reset order book state
        self.order_book.reset_order_book()
        
        # Unsubscribe from old market
        if old_contract_id:
            await self.market_switcher.unsubscribe_market(old_contract_id)
        
        # Subscribe to new market
        await self._subscribe_to_market(new_contract_id)
        
        # Update config
        self.market_switcher.update_market_config(new_contract_id)

    async def _subscribe_to_market(self, contract_id: str) -> None:
        """Subscribe to all channels for a market."""
        await self.market_switcher.subscribe_channels(
            contract_id=contract_id,
            order_callback=self._handle_order_update,
            order_book_callback=self._handle_order_book_update,
            bbo_callback=self._handle_bbo_update,
            fills_callback=self._handle_fill_update,
        )

    async def _wait_for_market_ready(self, timeout: float = 5.0) -> bool:
        """
        Wait for order book to be ready after market switch.
        
        Args:
            timeout: Maximum time to wait in seconds
            
        Returns:
            True if order book is ready, False if timeout
        """
        start_time = asyncio.get_event_loop().time()
        while not self.order_book.order_book_ready:
            if asyncio.get_event_loop().time() - start_time > timeout:
                return False
            await asyncio.sleep(0.1)
        return True

    # WebSocket message handlers (delegated from SDK callbacks)
    
    async def _handle_order_update(self, ws_channel: Any, message: Dict[str, Any]) -> None:
        """Handle order update from WebSocket."""
        if self.order_update_callback:
            try:
                # Extract order data from message
                params = message.get('params', {})
                data = params.get('data', {})
                
                # Call callback with order data
                if callable(self.order_update_callback):
                    if asyncio.iscoroutinefunction(self.order_update_callback):
                        await self.order_update_callback(data)
                    else:
                        self.order_update_callback(data)
            except Exception as e:
                if self.logger:
                    self.logger.error(f"Error in order update callback: {e}")

    async def _handle_order_book_update(self, ws_channel: Any, message: Dict[str, Any]) -> None:
        """Handle order book update from WebSocket."""
        try:
            params = message.get('params', {})
            data = params.get('data', {})
            market = data.get('market')
            
            if market:
                self.order_book.update_order_book(market, data)
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error handling order book update: {e}")

    async def _handle_bbo_update(self, ws_channel: Any, message: Dict[str, Any]) -> None:
        """Handle BBO (Best Bid/Offer) update from WebSocket."""
        try:
            import time
            
            params = message.get('params', {})
            data = params.get('data', {})
            market = data.get('market')
            
            if not market:
                return
            
            bid = data.get('bid') or data.get('best_bid')
            ask = data.get('ask') or data.get('best_ask')
            
            if bid and ask:
                # Create BBO data
                bbo = BBOData(
                    symbol=market,
                    bid=bid,
                    ask=ask,
                    timestamp=time.time(),
                )
                
                # Notify listeners
                await self._notify_bbo_update(bbo)
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error handling BBO update: {e}")

    async def _handle_fill_update(self, ws_channel: Any, message: Dict[str, Any]) -> None:
        """Handle fill update from WebSocket (includes liquidations)."""
        try:
            params = message.get('params', {})
            data = params.get('data', {})
            
            # Check if this is a liquidation
            fill_type = data.get('fill_type') or data.get('trade_type')
            if fill_type == "LIQUIDATION" and self.liquidation_callback:
                # Call liquidation callback
                if asyncio.iscoroutinefunction(self.liquidation_callback):
                    await self.liquidation_callback([data])
                else:
                    self.liquidation_callback([data])
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error handling fill update: {e}")

