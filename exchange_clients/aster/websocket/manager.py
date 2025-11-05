"""
Main WebSocket manager for Aster exchange.

Orchestrates connection, order book, message handling, and market switching.
"""

import asyncio
import json
from typing import Dict, Any, Optional, Callable, Awaitable, List

import websockets

from exchange_clients.base_websocket import BaseWebSocketManager, BBOData

from .connection import AsterWebSocketConnection
from .order_book import AsterOrderBook
from .message_handler import AsterMessageHandler
from .market_switcher import AsterMarketSwitcher


class AsterWebSocketManager(BaseWebSocketManager):
    """WebSocket manager for Aster order updates and order book."""

    def __init__(
        self,
        config: Dict[str, Any],
        api_key: str,
        secret_key: str,
        order_update_callback: Callable,
        liquidation_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
        symbol_formatter: Optional[Callable[[str], str]] = None,
    ):
        """
        Initialize WebSocket manager.
        
        Args:
            config: Configuration object
            api_key: API key for authentication
            secret_key: Secret key for signature generation
            order_update_callback: Callback for order updates
            liquidation_callback: Callback for liquidations
            symbol_formatter: Function to format symbols
        """
        super().__init__()
        self.config = config
        self.api_key = api_key
        self.secret_key = secret_key
        self.order_update_callback = order_update_callback
        self.liquidation_callback = liquidation_callback
        self.symbol_formatter = symbol_formatter
        
        # URLs
        self.base_url = "https://fapi.asterdex.com"
        self.ws_url = "wss://fstream.asterdex.com"
        
        # Initialize components
        self.connection = AsterWebSocketConnection(
            config=config,
            api_key=api_key,
            secret_key=secret_key,
            ws_url=self.ws_url,
            base_url=self.base_url,
        )
        self.order_book = AsterOrderBook()
        self.market_switcher = AsterMarketSwitcher(
            config=config,
            ws_url=self.ws_url,
            symbol_formatter=symbol_formatter,
            order_book_manager=self.order_book,
            notify_bbo_update_fn=self._notify_bbo_update,
            running=False,
        )
        self.message_handler = AsterMessageHandler(
            config=config,
            ws=None,  # Will be set after connection
            order_update_callback=order_update_callback,
            liquidation_callback=liquidation_callback,
            notify_bbo_update_fn=self._notify_bbo_update,
            connection_manager=self.connection,
        )
        
        # Track running state
        self.running = False
        self._listener_task: Optional[asyncio.Task] = None

    def set_logger(self, logger):
        """Set the logger instance for all components."""
        self.logger = logger
        self.connection.set_logger(logger)
        self.order_book.set_logger(logger)
        self.market_switcher.set_logger(logger)
        self.message_handler.set_logger(logger)

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

    # Delegate order book methods
    def get_order_book(self, levels: Optional[int] = None) -> Optional[Dict[str, List[Dict[str, Any]]]]:
        """Get formatted order book with optional level limiting."""
        return self.order_book.get_order_book(levels)

    @property
    def best_bid(self) -> Optional[float]:
        """Get current best bid."""
        return self.order_book.best_bid

    @property
    def best_ask(self) -> Optional[float]:
        """Get current best ask."""
        return self.order_book.best_ask

    @property
    def order_book_ready(self) -> bool:
        """Check if order book is ready."""
        return self.order_book.order_book_ready

    async def prepare_market_feed(self, symbol: Optional[str]) -> None:
        """
        Ensure both book ticker and depth streams are aligned with the requested symbol.
        
        Implementation follows the recommended pattern from BaseWebSocketManager:
        1. Validate: Check if already on target market
        2. Clear: Reset stale order book data
        3. Switch: Cancel old streams, start new ones
        4. Wait: Block until new data arrives
        5. Update: Log completion
        """
        if not symbol:
            return

        # Format symbol for Aster (e.g., "TOSHI" -> "TOSHIUSDT")
        stream_symbol = self.market_switcher.format_symbol(symbol)
        
        # Start book ticker for BBO (handles its own switching logic)
        await self.market_switcher.start_book_ticker(stream_symbol)
        
        # Check if depth stream switch is needed
        if not self.market_switcher.should_switch_depth_stream(stream_symbol):
            return
        
        # Perform the depth stream switch
        old_symbol = self.market_switcher._current_depth_symbol
        await self.market_switcher.switch_depth_stream(old_symbol, stream_symbol)
        
        # Wait for new data and log result
        success = await self.market_switcher.wait_for_depth_ready(timeout=5.0)
        self.market_switcher.log_depth_switch_result(old_symbol, stream_symbol, success)

    async def connect(self):
        """Connect to Aster WebSocket for order updates and book ticker."""
        if self.running:
            return

        try:
            # Get listen key for user data stream
            listen_key = await self.connection.get_listen_key()
            
            # Open connection to user data stream
            ws = await self.connection.open_connection(listen_key)
            self.running = True
            self.connection.running = True
            self.market_switcher.set_running(True)
            
            # Update message handler with WebSocket reference
            self.message_handler.set_ws(ws)
            
            self._log("[ASTER] 🔗 Connected to ws", "INFO")

            # Start keepalive task
            self.connection._keepalive_task = asyncio.create_task(
                self.connection.start_keepalive_task(reconnect_fn=self.connect)
            )
            
            # Start market data streams for the already-configured symbol
            ticker = getattr(self.config, 'ticker', None)
            contract_id = getattr(self.config, 'contract_id', None)
            
            if contract_id and contract_id not in {'ALL', 'MULTI', 'MULTI_SYMBOL'}:
                # Start streams for pre-configured symbol
                self._log(f"[ASTER] Starting market feeds for {contract_id}", "INFO")
                await self.market_switcher.start_book_ticker(contract_id)
                self.market_switcher._current_depth_symbol = contract_id
                self.market_switcher._depth_task = asyncio.create_task(
                    self.market_switcher._connect_depth_stream(contract_id)
                )
            elif ticker and ticker not in {'ALL', 'MULTI', 'MULTI_SYMBOL'}:
                # Fallback to ticker if contract_id not set
                symbol = f"{ticker}USDT" if not ticker.endswith("USDT") else ticker
                self._log(f"[ASTER] Starting market feeds for {symbol}", "INFO")
                await self.market_switcher.start_book_ticker(symbol)
                self.market_switcher._current_depth_symbol = symbol
                self.market_switcher._depth_task = asyncio.create_task(
                    self.market_switcher._connect_depth_stream(symbol)
                )

            # Start listening for messages on user data stream
            self._listener_task = asyncio.create_task(self._listen_loop())

        except Exception as e:
            self._log(f"WebSocket connection error: {e}", "ERROR")
            raise

    async def _listen_loop(self):
        """Listen for WebSocket messages on user data stream."""
        try:
            while self.running and self.connection.websocket:
                try:
                    async for message in self.connection.websocket:
                        if not self.running:
                            break
                        
                        # Process message through handler
                        await self.message_handler.process_message(message)
                
                except Exception as e:
                    if self.running:
                        self._log(f"WebSocket listen error: {e}", "ERROR")
                    break
        
        except websockets.exceptions.ConnectionClosed:
            if self.running:
                self._log("WebSocket connection closed", "WARNING")
        except Exception as e:
            if self.running:
                self._log(f"WebSocket listen error: {e}", "ERROR")

    async def disconnect(self):
        """Disconnect from WebSocket."""
        self.running = False
        self.connection.running = False
        self.market_switcher.set_running(False)

        # Cancel listener task
        if self._listener_task and not self._listener_task.done():
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass

        # Cancel keepalive task
        if self.connection._keepalive_task and not self.connection._keepalive_task.done():
            self.connection._keepalive_task.cancel()
            try:
                await self.connection._keepalive_task
            except asyncio.CancelledError:
                pass

        # Cancel all market data streams
        await self.market_switcher.cancel_all_streams()

        # Close main connection
        await self.connection.close_connection()
        
        self._log("WebSocket disconnected", "INFO")

