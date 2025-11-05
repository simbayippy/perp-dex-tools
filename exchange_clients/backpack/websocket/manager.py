"""
Main WebSocket manager for Backpack exchange.

Orchestrates connection, order book, message handling, and market switching.
"""

import asyncio
import time
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Awaitable

import websockets

from exchange_clients.base_websocket import BaseWebSocketManager, BBOData

from .connection import BackpackWebSocketConnection
from .order_book import BackpackOrderBook
from .message_handler import BackpackMessageHandler
from .market_switcher import BackpackMarketSwitcher


class BackpackWebSocketManager(BaseWebSocketManager):
    """WebSocket manager for Backpack order updates and order book."""

    _MAX_BACKOFF_SECONDS = 30.0

    def __init__(
        self,
        public_key: str,
        secret_key: str,
        symbol: Optional[str],
        order_update_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
        liquidation_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
        depth_fetcher: Optional[Callable[[str], Dict[str, Any]]] = None,
        depth_stream_interval: str = "realtime",
        symbol_formatter: Optional[Callable[[str], str]] = None,
    ):
        """
        Initialize WebSocket manager.
        
        Args:
            public_key: Backpack public key
            secret_key: Backpack secret key
            symbol: Initial symbol to subscribe to
            order_update_callback: Callback for order updates
            liquidation_callback: Callback for liquidations
            depth_fetcher: Function to fetch depth snapshot
            depth_stream_interval: Depth stream interval
            symbol_formatter: Function to format symbols
        """
        super().__init__()
        self.public_key = public_key
        self.secret_key = secret_key
        self.symbol = symbol
        self.depth_fetcher = depth_fetcher
        self.depth_stream_interval = depth_stream_interval
        
        # Initialize components
        self.connection = BackpackWebSocketConnection(
            public_key=public_key,
            secret_key=secret_key,
            ws_url="wss://ws.backpack.exchange",
        )
        self.order_book = BackpackOrderBook()
        self.message_handler = BackpackMessageHandler(
            order_update_callback=order_update_callback,
            liquidation_callback=liquidation_callback,
        )
        self.market_switcher = BackpackMarketSwitcher(
            symbol=symbol,
            symbol_formatter=symbol_formatter,
            update_symbol_fn=self._update_symbol_internal,
            update_market_config_fn=self._update_market_config,
        )
        
        # Track running state
        self.running = False
        self._account_task: Optional[asyncio.Task] = None
        self._depth_task: Optional[asyncio.Task] = None
        
        # Ready events
        self._ready_event = asyncio.Event()
        self._account_ready_event = asyncio.Event()
        self._depth_ready_event = asyncio.Event()

    def set_logger(self, logger):
        """Set the logger instance for all components."""
        self.logger = logger
        self.connection.set_logger(logger)
        self.order_book.set_logger(logger)
        self.message_handler.set_logger(logger)
        self.market_switcher.set_logger(logger)

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

    def _update_symbol_internal(self, symbol: Optional[str]) -> None:
        """Internal method to update symbol and clear order book."""
        if symbol == self.symbol:
            return
        self.symbol = symbol
        self.order_book.reset()
        self._depth_ready_event.clear()

    def _update_market_config(self, symbol: str) -> None:
        """Update market config (can be overridden by client)."""
        pass

    # Delegate order book methods
    def get_order_book(self, levels: Optional[int] = None) -> Optional[Dict[str, List[Dict[str, Decimal]]]]:
        """Get formatted order book with optional level limiting."""
        return self.order_book.get_order_book(levels)

    @property
    def best_bid(self) -> Optional[Decimal]:
        """Get current best bid."""
        return self.order_book.best_bid

    @property
    def best_ask(self) -> Optional[Decimal]:
        """Get current best ask."""
        return self.order_book.best_ask

    @property
    def order_book_ready(self) -> bool:
        """Check if order book is ready."""
        return self.order_book.order_book_ready

    async def prepare_market_feed(self, symbol: Optional[str]) -> None:
        """
        Ensure both account and depth streams are aligned with the requested symbol.
        
        Implementation follows the recommended pattern from BaseWebSocketManager:
        1. Validate: Check if already on target market
        2. Clear: Reset stale order book data
        3. Switch: Full disconnect/reconnect cycle
        4. Wait: Block until new data arrives
        5. Update: Log completion
        """
        if not symbol:
            return

        # Format symbol for Backpack
        target_symbol = self.market_switcher.format_symbol(symbol)
        
        # Check if already on target (Step 1)
        if not self.market_switcher.should_switch_symbol(target_symbol):
            # Already aligned; make sure order book is marked ready if needed
            if not self.order_book_ready and self.depth_fetcher:
                await self.wait_for_order_book(timeout=2.0)
            return
        
        # Perform the switch (Steps 2 & 3)
        self.market_switcher.perform_symbol_switch(target_symbol)
        
        # Full reconnection cycle
        if self.running:
            await self.disconnect()
            await self.connect()
        
        # Wait for ready and log result (Steps 4 & 5)
        if self.depth_fetcher:
            success = await self.wait_for_order_book(timeout=5.0)
            self._log_switch_result(target_symbol, success)

    def _log_switch_result(self, symbol: str, success: bool) -> None:
        """Log the result of symbol switch operation."""
        if success and self.logger:
            bid_count = len(self.order_book.order_book.get("bids", []))
            ask_count = len(self.order_book.order_book.get("asks", []))
            self.logger.info(
                f"[BACKPACK] ✅ Market switch complete for {symbol}: "
                f"{bid_count} bids, {ask_count} asks | "
                f"BBO: {self.best_bid}/{self.best_ask}"
            )
        elif not success and self.logger:
            self.logger.warning(
                f"[BACKPACK] ⚠️  Order book for {symbol} not ready after 5s timeout"
            )

    async def wait_until_ready(self, timeout: float = 5.0) -> bool:
        """Wait until the account stream is ready."""
        try:
            await asyncio.wait_for(self._ready_event.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def wait_for_order_book(self, timeout: float = 5.0) -> bool:
        """Wait until the depth stream has produced an order book snapshot."""
        try:
            await asyncio.wait_for(self._depth_ready_event.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def connect(self) -> None:
        """Start background tasks to maintain account and market-data streams."""
        if self.running:
            return

        self.running = True
        self.connection.running = True
        self._ready_event.clear()
        self._account_ready_event.clear()
        self._depth_ready_event.clear()

        # Start account stream task
        self._account_task = asyncio.create_task(
            self._run_account_stream(),
            name="backpack-account-ws"
        )
        
        # Start depth stream task if depth fetcher available
        if self.depth_fetcher:
            self._depth_task = asyncio.create_task(
                self._run_depth_stream(),
                name="backpack-depth-ws"
            )
        else:
            # No depth stream - mark as ready so callers don't hang
            self._depth_ready_event.set()

        # Allow tasks to spin up
        await asyncio.sleep(0)
        self._log("[BACKPACK] 🔗 Connected to ws", "INFO")

    async def disconnect(self) -> None:
        """Stop websocket tasks and close sockets."""
        if not self.running:
            return

        self.running = False
        self.connection.running = False

        tasks = [self._account_task, self._depth_task]
        for task in tasks:
            if task:
                task.cancel()

        for task in tasks:
            if task:
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        await self.connection.close_account_ws(self.connection._account_ws)
        await self.connection.close_depth_ws(self.connection._depth_ws)

        self._account_task = None
        self._depth_task = None

        self._ready_event.clear()
        self._account_ready_event.clear()
        self._depth_ready_event.clear()
        self.order_book.reset()

    def update_symbol(self, symbol: Optional[str]) -> None:
        """
        Update symbol subscription.

        To fully switch symbols, call `disconnect()`, update the symbol, and
        then call `connect()`.
        """
        self._update_symbol_internal(symbol)

    async def _run_account_stream(self) -> None:
        """Run account stream with reconnection logic."""
        backoff_seconds = 1.0
        while self.running:
            if not self.symbol:
                await asyncio.sleep(0.5)
                continue
            try:
                self.connection._account_ws = await self.connection.connect_account_stream(self.symbol)
                self._account_ready_event.set()
                self._ready_event.set()
                backoff_seconds = 1.0
                await self._listen_account_ws()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                if self.logger:
                    self.logger.error(f"[BACKPACK] Account WS error: {exc}")
                await asyncio.sleep(min(backoff_seconds, self._MAX_BACKOFF_SECONDS))
                backoff_seconds = min(backoff_seconds * 2, self._MAX_BACKOFF_SECONDS)
            finally:
                await self.connection.close_account_ws(self.connection._account_ws)
                self.connection._account_ws = None

        self._account_ready_event.clear()

    async def _listen_account_ws(self) -> None:
        """Listen for account stream messages."""
        assert self.connection._account_ws is not None
        try:
            async for message in self.connection._account_ws:
                if not self.running:
                    break
                await self.message_handler.process_account_message(message)
        except websockets.exceptions.ConnectionClosed:
            if self.logger:
                self.logger.warning("[BACKPACK] Account stream closed")

    async def _run_depth_stream(self) -> None:
        """Run depth stream with reconnection logic."""
        backoff_seconds = 1.0
        while self.running:
            if not self.symbol:
                await asyncio.sleep(0.5)
                continue
            try:
                loaded = await self._load_initial_depth()
                if not loaded:
                    await asyncio.sleep(min(backoff_seconds, self._MAX_BACKOFF_SECONDS))
                    backoff_seconds = min(backoff_seconds * 2, self._MAX_BACKOFF_SECONDS)
                    continue

                self.connection._depth_ws = await self.connection.connect_depth_stream(
                    self.symbol,
                    self.depth_stream_interval
                )
                backoff_seconds = 1.0
                await self._listen_depth_ws()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                if self.logger:
                    self.logger.error(f"[BACKPACK] Depth WS error: {exc}")
                await asyncio.sleep(min(backoff_seconds, self._MAX_BACKOFF_SECONDS))
                backoff_seconds = min(backoff_seconds * 2, self._MAX_BACKOFF_SECONDS)
            finally:
                await self.connection.close_depth_ws(self.connection._depth_ws)
                self.connection._depth_ws = None

        self._depth_ready_event.clear()
        self.order_book.reset()

    async def _load_initial_depth(self) -> bool:
        """Load initial depth snapshot."""
        if not self.depth_fetcher or not self.symbol:
            return False

        try:
            snapshot = await asyncio.to_thread(self.depth_fetcher, self.symbol)
        except Exception as exc:
            if self.logger:
                self.logger.error(f"[BACKPACK] Failed to fetch depth snapshot: {exc}")
            return False

        self.order_book.load_snapshot(snapshot)
        self._depth_ready_event.set()
        return True

    async def _reload_depth_snapshot(self) -> None:
        """Reload depth snapshot when gap detected."""
        async with self.order_book._depth_reload_lock:
            self.order_book.order_book_ready = False
            await self._load_initial_depth()

    async def _listen_depth_ws(self) -> None:
        """Listen for depth stream messages."""
        assert self.connection._depth_ws is not None
        try:
            async for message in self.connection._depth_ws:
                if not self.running:
                    break
                result = self.message_handler.process_depth_message(message)
                
                if result["type"] == "depth":
                    applied = self.order_book.apply_depth_update(result["payload"], self.symbol)
                    if not applied:
                        # Gap detected - reload snapshot
                        asyncio.create_task(self._reload_depth_snapshot())
                    else:
                        bbo_data = self.order_book._rebuild_order_book()
                        if bbo_data:
                            bbo_data.symbol = self.symbol or ""
                            asyncio.create_task(self._notify_bbo_update(bbo_data))
                        self._depth_ready_event.set()
                elif result["type"] == "book_ticker":
                    self.order_book.apply_book_ticker(result["payload"])
                    # Notify BBO update
                    if self.order_book.best_bid and self.order_book.best_ask:
                        asyncio.create_task(
                            self._notify_bbo_update(
                                BBOData(
                                    symbol=self.symbol or "",
                                    bid=float(self.order_book.best_bid),
                                    ask=float(self.order_book.best_ask),
                                    timestamp=time.time(),
                                    sequence=self.order_book._last_update_id,
                                )
                            )
                        )
        except websockets.exceptions.ConnectionClosed:
            if self.logger:
                self.logger.warning("[BACKPACK] Depth stream closed")

