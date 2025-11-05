"""
Market switching logic for Aster WebSocket.

Handles market subscription switching, book ticker, and depth stream management.
"""

import asyncio
import json
import time
from typing import Dict, Any, Optional, Callable

import websockets

from exchange_clients.base_websocket import BBOData


class AsterMarketSwitcher:
    """Manages market switching and subscription logic for book ticker and depth streams."""

    def __init__(
        self,
        config: Dict[str, Any],
        ws_url: str,
        symbol_formatter: Optional[Callable[[str], str]] = None,
        order_book_manager: Optional[Any] = None,
        notify_bbo_update_fn: Optional[Callable] = None,
        running: bool = False,
        logger: Optional[Any] = None,
    ):
        """
        Initialize market switcher.
        
        Args:
            config: Configuration object
            ws_url: WebSocket base URL
            symbol_formatter: Function to format symbols
            order_book_manager: Order book manager instance
            notify_bbo_update_fn: Function to notify BBO updates
            running: Whether the manager is running
            logger: Logger instance
        """
        self.config = config
        self.ws_url = ws_url
        self.symbol_formatter = symbol_formatter
        self.order_book = order_book_manager
        self.notify_bbo_update = notify_bbo_update_fn
        self.running = running
        self.logger = logger
        
        # Stream state
        self._current_book_ticker_symbol: Optional[str] = None
        self._current_depth_symbol: Optional[str] = None
        self._book_ticker_ws: Optional[websockets.WebSocketClientProtocol] = None
        self._depth_ws: Optional[websockets.WebSocketClientProtocol] = None
        self._book_ticker_task: Optional[asyncio.Task] = None
        self._depth_task: Optional[asyncio.Task] = None

    def set_logger(self, logger):
        """Set the logger instance."""
        self.logger = logger

    def set_running(self, running: bool):
        """Set the running state."""
        self.running = running

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

    def format_symbol(self, symbol: str) -> str:
        """
        Format symbol for Aster streams.
        
        Args:
            symbol: Normalized symbol (e.g., "TOSHI")
            
        Returns:
            Aster-formatted symbol (e.g., "TOSHIUSDT")
        """
        if self.symbol_formatter:
            try:
                return self.symbol_formatter(symbol)
            except Exception:
                return symbol
        return symbol

    async def start_book_ticker(self, symbol: str):
        """
        Connect to book ticker WebSocket and wait for first BBO.
        
        This properly awaits the initial connection and first message,
        then starts a background task for continuous updates.
        
        Args:
            symbol: Normalized symbol (e.g., "MONUSDT", "SKYUSDT")
        """
        # If already subscribed to this symbol, no need to restart
        if self._current_book_ticker_symbol == symbol and self._book_ticker_task and not self._book_ticker_task.done():
            self._log(f"Already subscribed to book ticker for {symbol}", "DEBUG")
            return
        
        # Cancel existing book ticker task if different symbol
        if self._book_ticker_task and not self._book_ticker_task.done():
            self._log(f"Switching book ticker from {self._current_book_ticker_symbol} to {symbol}", "INFO")
            self._book_ticker_task.cancel()
            try:
                await self._book_ticker_task
            except asyncio.CancelledError:
                pass
        
        # Reset BBO state before starting new stream
        if self.order_book:
            self.order_book.best_bid = None
            self.order_book.best_ask = None
        self._current_book_ticker_symbol = symbol
        
        # Connect and wait for first BBO message
        stream_name = f"{symbol.lower()}@bookTicker"
        book_ticker_url = f"{self.ws_url}/ws/{stream_name}"
        
        self._log(f"📊 Connecting to Aster book ticker: {stream_name}", "INFO")
        
        try:
            # Connect to WebSocket
            self._book_ticker_ws = await websockets.connect(book_ticker_url)
            
            self._log(f"✅ Connected to Aster book ticker for {symbol}", "INFO")
            
            # Wait for first BBO message (up to 5 seconds)
            try:
                message = await asyncio.wait_for(self._book_ticker_ws.recv(), timeout=5.0)
                data = json.loads(message)
                
                # Process first message through order book manager
                if self.order_book:
                    await self.order_book.handle_book_ticker(data, self.notify_bbo_update)
                
                if self.order_book:
                    self._log(
                        f"✅ Book ticker ready: bid={self.order_book.best_bid}, ask={self.order_book.best_ask}",
                        "INFO"
                    )
            except asyncio.TimeoutError:
                self._log("⏱️  No BBO message received within 5s", "WARNING")
            
            # Start background task to continue listening
            self._book_ticker_task = asyncio.create_task(self._listen_book_ticker())
            
        except Exception as e:
            self._log(f"Failed to connect to book ticker: {e}", "ERROR")
            raise
    
    async def _listen_book_ticker(self):
        """
        Continuously listen for book ticker messages.
        
        This runs in a background task and processes all BBO updates.
        Includes automatic reconnection logic.
        """
        reconnect_delay = 1
        max_reconnect_delay = 30
        
        try:
            while self.running:
                try:
                    # Listen for messages on the already-connected WebSocket
                    async for message in self._book_ticker_ws:
                        if not self.running:
                            break
                        
                        try:
                            data = json.loads(message)
                            if self.order_book:
                                await self.order_book.handle_book_ticker(data, self.notify_bbo_update)
                        except json.JSONDecodeError as e:
                            self._log(f"Failed to parse book ticker message: {e}", "ERROR")
                        except Exception as e:
                            self._log(f"Error handling book ticker: {e}", "ERROR")
                
                except websockets.exceptions.ConnectionClosed:
                    if self.running:
                        self._log("Book ticker WebSocket closed, reconnecting...", "WARNING")
                    
                    # Reconnect
                    if self.running and self._current_book_ticker_symbol:
                        try:
                            stream_name = f"{self._current_book_ticker_symbol.lower()}@bookTicker"
                            book_ticker_url = f"{self.ws_url}/ws/{stream_name}"
                            self._book_ticker_ws = await websockets.connect(book_ticker_url)
                            reconnect_delay = 1  # Reset on successful reconnect
                            self._log("✅ Reconnected to book ticker", "INFO")
                        except Exception as e:
                            self._log(f"Reconnection failed: {e}", "ERROR")
                
                except Exception as e:
                    if self.running:
                        self._log(f"Book ticker WebSocket error: {e}", "ERROR")
                
                # Exponential backoff for reconnection
                if self.running:
                    await asyncio.sleep(reconnect_delay)
                    reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)
        
        except Exception as e:
            self._log(f"Fatal error in book ticker listener: {e}", "ERROR")

    def should_switch_depth_stream(self, target_symbol: str) -> bool:
        """
        Check if depth stream switch is needed.
        
        Args:
            target_symbol: Target symbol to switch to
            
        Returns:
            True if switch needed, False if already on target
        """
        if self._current_depth_symbol == target_symbol and self._depth_task and not self._depth_task.done():
            self._log(f"Already subscribed to depth stream for {target_symbol}", "DEBUG")
            return False
        return True
    
    async def switch_depth_stream(self, old_symbol: Optional[str], new_symbol: str) -> None:
        """
        Switch depth stream from old symbol to new symbol.
        
        Args:
            old_symbol: Current symbol (or None if first time)
            new_symbol: New symbol to switch to
        """
        # Cancel existing depth task if running
        if self._depth_task and not self._depth_task.done():
            self._log(f"Switching depth stream from {old_symbol} to {new_symbol}", "INFO")
            
            # Clear stale order book data
            if self.order_book:
                self.order_book.reset_order_book()
            
            self._depth_task.cancel()
            try:
                await self._depth_task
            except asyncio.CancelledError:
                pass
        
        # Start new depth task
        self._current_depth_symbol = new_symbol
        self._depth_task = asyncio.create_task(self._connect_depth_stream(new_symbol))
        
        # Update config to keep it synchronized
        self._update_market_config(new_symbol)
    
    def _update_market_config(self, symbol: str) -> None:
        """Update config to keep it synchronized with current market."""
        if hasattr(self.config, 'contract_id'):
            self.config.contract_id = symbol

    async def _connect_depth_stream(self, symbol: str):
        """
        Connect to order book depth stream.
        
        Stream: <symbol>@depth20@100ms
        Provides top 20 bids and asks every 100ms.
        
        Args:
            symbol: Symbol to subscribe to (e.g., "MONUSDT")
        """
        try:
            # Use partial depth stream: top 20 levels at 100ms
            stream_name = f"{symbol.lower()}@depth20@100ms"
            depth_url = f"{self.ws_url}/ws/{stream_name}"
            
            self._log(f"📚 [ASTER] Connecting to depth stream: {stream_name}", "INFO")
            
            reconnect_delay = 1
            max_reconnect_delay = 60
            
            while self.running:
                try:
                    async with websockets.connect(depth_url) as ws:
                        self._depth_ws = ws
                        
                        self._log(f"📚 [ASTER] Connected to depth stream for {symbol}", "INFO")
                        
                        # Reset reconnect delay on successful connection
                        reconnect_delay = 1
                        
                        # Listen for depth updates
                        async for message in ws:
                            if not self.running:
                                break
                            
                            try:
                                data = json.loads(message)
                                if self.order_book:
                                    await self.order_book.handle_depth_update(data, self.notify_bbo_update)
                            except json.JSONDecodeError as e:
                                self._log(f"Failed to parse depth message: {e}", "ERROR")
                            except Exception as e:
                                self._log(f"Error handling depth update: {e}", "ERROR")
                
                except websockets.exceptions.ConnectionClosed:
                    if self.running:
                        self._log("Depth WebSocket closed, reconnecting...", "WARNING")
                except Exception as e:
                    if self.running:
                        self._log(f"Depth WebSocket error: {e}", "ERROR")
                
                # Reconnect with exponential backoff
                if self.running:
                    await asyncio.sleep(reconnect_delay)
                    reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)
        
        except Exception as e:
            self._log(f"Failed to start depth WebSocket: {e}", "ERROR")

    async def wait_for_depth_ready(self, timeout: float = 5.0) -> bool:
        """
        Wait for depth stream to become ready.
        
        Args:
            timeout: Maximum time to wait in seconds
            
        Returns:
            True if ready, False if timeout
        """
        if not self.order_book:
            return False
        
        start_time = asyncio.get_event_loop().time()
        
        while not self.order_book.order_book_ready and (asyncio.get_event_loop().time() - start_time) < timeout:
            await asyncio.sleep(0.1)
        
        return self.order_book.order_book_ready
    
    def log_depth_switch_result(
        self, 
        old_symbol: Optional[str], 
        new_symbol: str, 
        success: bool
    ) -> None:
        """Log the result of depth stream switch."""
        if success and self.order_book:
            bids_count = len(self.order_book.order_book.get('bids', []))
            asks_count = len(self.order_book.order_book.get('asks', []))
            self._log(
                f"📚 [ASTER] ✅ Depth stream ready for {new_symbol} "
                f"({bids_count} bids, {asks_count} asks)",
                "INFO"
            )
        else:
            self._log(
                f"📚 [ASTER] ⚠️  Depth stream for {new_symbol} not ready yet (timeout after 5.0s)",
                "WARNING"
            )

    async def cancel_all_streams(self):
        """Cancel all streaming tasks and close connections."""
        # Cancel book ticker task
        if self._book_ticker_task and not self._book_ticker_task.done():
            self._book_ticker_task.cancel()
            try:
                await self._book_ticker_task
            except asyncio.CancelledError:
                pass
        
        # Cancel depth stream task
        if self._depth_task and not self._depth_task.done():
            self._depth_task.cancel()
            try:
                await self._depth_task
            except asyncio.CancelledError:
                pass
        
        # Close WebSockets
        if self._book_ticker_ws:
            await self._book_ticker_ws.close()
            self._book_ticker_ws = None
        
        if self._depth_ws:
            await self._depth_ws.close()
            self._depth_ws = None

