"""
Aster WebSocket Manager

Handles WebSocket connections for Aster exchange order updates.
Uses Binance Futures-compatible WebSocket API with listen keys and keepalive.
"""

import asyncio
import json
import time
import hmac
import hashlib
from typing import Dict, Any, Optional, Callable, Awaitable
from urllib.parse import urlencode
import aiohttp
import websockets

from exchange_clients.base_websocket import BaseWebSocketManager, BBOData


class AsterWebSocketManager2(BaseWebSocketManager):
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
        super().__init__()
        self.api_key = api_key
        self.secret_key = secret_key
        self.order_update_callback = order_update_callback
        self.liquidation_callback = liquidation_callback
        self._symbol_formatter = symbol_formatter
        self.websocket = None
        self.base_url = "https://fapi.asterdex.com"
        self.ws_url = "wss://fstream.asterdex.com"
        self.listen_key = None
        self._keepalive_task = None
        self._last_ping_time = None
        self.config = config
        
        # 📊 Order book state (for real-time BBO via book ticker)
        self.best_bid = None
        self.best_ask = None
        self._book_ticker_ws = None  # Separate WebSocket for book ticker
        self._book_ticker_task = None
        self._current_book_ticker_symbol = None  # Track which symbol we're subscribed to
        
        # 📚 Full order book state (for liquidity checks via depth stream)
        self.order_book = {"bids": [], "asks": []}  # Snapshot format: [{'price': Decimal, 'size': Decimal}, ...]
        self._depth_ws = None  # Separate WebSocket for order book depth
        self._depth_task = None
        self._current_depth_symbol = None
        self.order_book_ready = False

    def _generate_signature(self, params: Dict[str, Any]) -> str:
        """Generate HMAC SHA256 signature for Aster API authentication."""
        # Use urlencode to properly format the query string
        query_string = urlencode(params)

        # Generate HMAC SHA256 signature
        signature = hmac.new(
            self.secret_key.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

        return signature

    async def _get_listen_key(self) -> str:
        """Get listen key for user data stream."""
        params = {
            'timestamp': int(time.time() * 1000)
        }
        signature = self._generate_signature(params)
        params['signature'] = signature

        headers = {
            'X-MBX-APIKEY': self.api_key,
            'Content-Type': 'application/x-www-form-urlencoded'
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                'https://fapi.asterdex.com/fapi/v1/listenKey',
                headers=headers,
                data=params
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    return result.get('listenKey')
                else:
                    raise Exception(f"Failed to get listen key: {response.status}")

    async def _keepalive_listen_key(self) -> bool:
        """Keep alive the listen key to prevent timeout."""
        try:
            if not self.listen_key:
                return False

            params = {
                'timestamp': int(time.time() * 1000)
            }
            signature = self._generate_signature(params)
            params['signature'] = signature

            headers = {
                'X-MBX-APIKEY': self.api_key,
                'Content-Type': 'application/x-www-form-urlencoded'
            }

            async with aiohttp.ClientSession() as session:
                async with session.put(
                    f"{self.base_url}/fapi/v1/listenKey",
                    headers=headers,
                    data=params
                ) as response:
                    if response.status == 200:
                        if self.logger:
                            self.logger.debug("Listen key keepalive successful")
                        return True
                    else:
                        if self.logger:
                            self.logger.warning(f"Failed to keepalive listen key: {response.status}")
                        return False
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error keeping alive listen key: {e}")
            return False

    async def _check_connection_health(self) -> bool:
        """Check if the WebSocket connection is healthy based on ping timing."""
        if not self._last_ping_time:
            return True  # No pings received yet, assume healthy

        # Check if we haven't received a ping in the last 10 minutes
        # (server sends pings every 5 minutes, so 10 minutes indicates a problem)
        time_since_last_ping = time.time() - self._last_ping_time
        if time_since_last_ping > 10 * 60:  # 10 minutes
            if self.logger:
                self.logger.warning(
                    f"No ping received for {time_since_last_ping/60:.1f} minutes, "
                    "connection may be unhealthy"
                )
            return False

        return True

    async def _start_keepalive_task(self):
        """Start the keepalive task to extend listen key validity and monitor connection health."""
        while self.running:
            try:
                # Check connection health every 5 minutes
                await asyncio.sleep(5 * 60)

                if not self.running:
                    break

                # Check if connection is healthy
                if not await self._check_connection_health():
                    if self.logger:
                        self.logger.warning("Connection health check failed, reconnecting...")
                    # Try to reconnect
                    try:
                        await self.connect()
                    except Exception as e:
                        if self.logger:
                            self.logger.error(f"Reconnection failed: {e}")
                        # Wait before retrying
                        await asyncio.sleep(30)
                    continue

                # Check if we need to keepalive the listen key (every 50 minutes)
                if self.listen_key and time.time() % (50 * 60) < 5 * 60:  # Within 5 minutes of 50-minute mark
                    success = await self._keepalive_listen_key()
                    if not success:
                        if self.logger:
                            self.logger.warning("Listen key keepalive failed, reconnecting...")
                        # Try to reconnect
                        try:
                            await self.connect()
                        except Exception as e:
                            if self.logger:
                                self.logger.error(f"Reconnection failed: {e}")
                            # Wait before retrying
                            await asyncio.sleep(30)

            except Exception as e:
                if self.logger:
                    self.logger.error(f"Error in keepalive task: {e}")
                # Wait a bit before retrying
                await asyncio.sleep(60)

    async def connect(self):
        """Connect to Aster WebSocket for order updates and book ticker."""
        try:
            # Get listen key for user data stream
            self.listen_key = await self._get_listen_key()
            if not self.listen_key:
                raise Exception("Failed to get listen key")

            # Connect to user data stream (order updates)
            ws_url = f"{self.ws_url}/ws/{self.listen_key}"
            self.websocket = await websockets.connect(ws_url)
            self.running = True

            if self.logger:
                self.logger.info("[ASTER] 🔗 Connected to ws")

            # Start keepalive task
            self._keepalive_task = asyncio.create_task(self._start_keepalive_task())
            
            # Start market data streams for the already-configured symbol
            # (Similar to how Lighter subscribes to its market_index, Backpack starts tasks for its symbol)
            ticker = getattr(self.config, 'ticker', None)
            contract_id = getattr(self.config, 'contract_id', None)
            
            if contract_id and contract_id not in {'ALL', 'MULTI', 'MULTI_SYMBOL'}:
                # Start streams for pre-configured symbol
                if self.logger:
                    self.logger.info(f"[ASTER] Starting market feeds for {contract_id}")
                await self.start_book_ticker(contract_id)
                self._current_depth_symbol = contract_id
                self._depth_task = asyncio.create_task(self._connect_depth_stream(contract_id))
            elif ticker and ticker not in {'ALL', 'MULTI', 'MULTI_SYMBOL'}:
                # Fallback to ticker if contract_id not set
                symbol = f"{ticker}USDT" if not ticker.endswith("USDT") else ticker
                if self.logger:
                    self.logger.info(f"[ASTER] Starting market feeds for {symbol}")
                await self.start_book_ticker(symbol)
                self._current_depth_symbol = symbol
                self._depth_task = asyncio.create_task(self._connect_depth_stream(symbol))

            # Start listening for messages
            await self._listen()

        except Exception as e:
            if self.logger:
                self.logger.error(f"WebSocket connection error: {e}")
            raise

    async def _listen(self):
        """Listen for WebSocket messages."""
        try:
            async for message in self.websocket:
                if not self.running:
                    break

                # Check if this is a ping frame (websockets library handles pong automatically)
                if isinstance(message, bytes) and message == b'\x89\x00':  # Ping frame
                    self._last_ping_time = time.time()
                    if self.logger:
                        self.logger.debug("Received ping frame, sending pong")
                    continue

                try:
                    data = json.loads(message)
                    await self._handle_message(data)
                except json.JSONDecodeError as e:
                    if self.logger:
                        self.logger.error(f"Failed to parse WebSocket message: {e}")
                except Exception as e:
                    if self.logger:
                        self.logger.error(f"Error handling WebSocket message: {e}")

        except websockets.exceptions.ConnectionClosed:
            if self.logger:
                self.logger.warning("WebSocket connection closed")
        except Exception as e:
            if self.logger:
                self.logger.error(f"WebSocket listen error: {e}")

    async def _handle_message(self, data: Dict[str, Any]):
        """Handle incoming WebSocket messages."""
        try:
            event_type = data.get('e', '')

            if event_type == 'ORDER_TRADE_UPDATE':
                await self._handle_order_update(data)
            elif event_type == 'forceOrder':
                if self.liquidation_callback:
                    await self.liquidation_callback(data)
            elif event_type == 'listenKeyExpired':
                if self.logger:
                    self.logger.warning("Listen key expired, reconnecting...")
                # Reconnect with new listen key
                await self.connect()
            else:
                if self.logger:
                    self.logger.debug(f"Unknown WebSocket message: {data}")

        except Exception as e:
            if self.logger:
                self.logger.error(f"Error handling WebSocket message: {e}")

    async def _handle_order_update(self, order_data: Dict[str, Any]):
        """Handle order update messages."""
        try:
            order_info = order_data.get('o', {})

            order_id = order_info.get('i', '')
            symbol = order_info.get('s', '')
            side = order_info.get('S', '')
            quantity = order_info.get('q', '0')
            price = order_info.get('p', '0')
            executed_qty = order_info.get('z', '0')
            status = order_info.get('X', '')

            # Map status
            status_map = {
                'NEW': 'OPEN',
                'PARTIALLY_FILLED': 'PARTIALLY_FILLED',
                'FILLED': 'FILLED',
                'CANCELED': 'CANCELED',
                'REJECTED': 'REJECTED',
                'EXPIRED': 'EXPIRED'
            }
            mapped_status = status_map.get(status, status)

            # Call the order update callback if it exists
            if hasattr(self, 'order_update_callback') and self.order_update_callback:
                # Let strategy determine order type
                order_type = "ORDER"

                await self.order_update_callback({
                    'order_id': order_id,
                    'side': side.lower(),
                    'order_type': order_type,
                    'status': mapped_status,
                    'size': quantity,
                    'price': price,
                    'contract_id': symbol,
                    'filled_size': executed_qty
                })

        except Exception as e:
            if self.logger:
                self.logger.error(f"Error handling order update: {e}")

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
            if self.logger:
                self.logger.debug(f"Already subscribed to book ticker for {symbol}")
            return
        
        # Cancel existing book ticker task if different symbol
        if self._book_ticker_task and not self._book_ticker_task.done():
            if self.logger:
                self.logger.info(f"Switching book ticker from {self._current_book_ticker_symbol} to {symbol}")
            self._book_ticker_task.cancel()
            try:
                await self._book_ticker_task
            except asyncio.CancelledError:
                pass
        
        # Reset BBO state before starting new stream
        self.best_bid = None
        self.best_ask = None
        self._current_book_ticker_symbol = symbol
        
        # Connect and wait for first BBO message
        stream_name = f"{symbol.lower()}@bookTicker"
        book_ticker_url = f"{self.ws_url}/ws/{stream_name}"
        
        if self.logger:
            self.logger.info(f"📊 Connecting to Aster book ticker: {stream_name}")
        
        try:
            # Connect to WebSocket
            self._book_ticker_ws = await websockets.connect(book_ticker_url)
            
            if self.logger:
                self.logger.info(f"✅ Connected to Aster book ticker for {symbol}")
            
            # Wait for first BBO message (up to 5 seconds)
            try:
                message = await asyncio.wait_for(self._book_ticker_ws.recv(), timeout=5.0)
                data = json.loads(message)
                await self._handle_book_ticker(data)
                
                if self.logger:
                    self.logger.info(f"✅ Book ticker ready: bid={self.best_bid}, ask={self.best_ask}")
                await self._notify_bbo_update(
                    BBOData(
                        symbol=symbol,
                        bid=self.best_bid,
                        ask=self.best_ask,
                        timestamp=time.time(),
                    )
                )
            except asyncio.TimeoutError:
                if self.logger:
                    self.logger.warning(f"⏱️  No BBO message received within 5s")
            
            # Start background task to continue listening
            self._book_ticker_task = asyncio.create_task(self._listen_book_ticker())
            
        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to connect to book ticker: {e}")
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
                            await self._handle_book_ticker(data)
                        except json.JSONDecodeError as e:
                            if self.logger:
                                self.logger.error(f"Failed to parse book ticker message: {e}")
                        except Exception as e:
                            if self.logger:
                                self.logger.error(f"Error handling book ticker: {e}")
                
                except websockets.exceptions.ConnectionClosed:
                    if self.logger and self.running:
                        self.logger.warning("Book ticker WebSocket closed, reconnecting...")
                    
                    # Reconnect
                    if self.running and self._current_book_ticker_symbol:
                        try:
                            stream_name = f"{self._current_book_ticker_symbol.lower()}@bookTicker"
                            book_ticker_url = f"{self.ws_url}/ws/{stream_name}"
                            self._book_ticker_ws = await websockets.connect(book_ticker_url)
                            reconnect_delay = 1  # Reset on successful reconnect
                            if self.logger:
                                self.logger.info(f"✅ Reconnected to book ticker")
                        except Exception as e:
                            if self.logger:
                                self.logger.error(f"Reconnection failed: {e}")
                
                except Exception as e:
                    if self.logger and self.running:
                        self.logger.error(f"Book ticker WebSocket error: {e}")
                
                # Exponential backoff for reconnection
                if self.running:
                    await asyncio.sleep(reconnect_delay)
                    reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)
        
        except Exception as e:
            if self.logger:
                self.logger.error(f"Fatal error in book ticker listener: {e}")
    
    async def _connect_book_ticker(self, symbol: str):
        """
        DEPRECATED: Old infinite-loop connection method.
        
        Kept for backwards compatibility but should not be used.
        Use start_book_ticker() instead which properly awaits first message.
        """
        try:
            
            # Construct book ticker stream URL
            stream_name = f"{symbol.lower()}@bookTicker"
            book_ticker_url = f"{self.ws_url}/ws/{stream_name}"
            
            if self.logger:
                self.logger.info(f"📊 Connecting to Aster book ticker: {stream_name}")
            
            # Connect with reconnection logic
            reconnect_delay = 1
            max_reconnect_delay = 30
            
            while self.running:
                try:
                    async with websockets.connect(book_ticker_url) as ws:
                        self._book_ticker_ws = ws
                        
                        if self.logger:
                            self.logger.info(f"✅ Connected to Aster book ticker for {symbol}")
                        
                        # Reset reconnect delay on successful connection
                        reconnect_delay = 1
                        
                        # Listen for book ticker updates
                        async for message in ws:
                            if not self.running:
                                break
                            
                            try:
                                data = json.loads(message)
                                await self._handle_book_ticker(data)
                            except json.JSONDecodeError as e:
                                if self.logger:
                                    self.logger.error(f"Failed to parse book ticker message: {e}")
                            except Exception as e:
                                if self.logger:
                                    self.logger.error(f"Error handling book ticker: {e}")
                
                except websockets.exceptions.ConnectionClosed:
                    if self.logger and self.running:
                        self.logger.warning("Book ticker WebSocket closed, reconnecting...")
                except Exception as e:
                    if self.logger and self.running:
                        self.logger.error(f"Book ticker WebSocket error: {e}")
                
                # Reconnect with exponential backoff
                if self.running:
                    await asyncio.sleep(reconnect_delay)
                    reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)
        
        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to start book ticker WebSocket: {e}")
    
    async def _handle_book_ticker(self, data: Dict[str, Any]):
        """
        Handle book ticker updates.
        
        Format:
        {
          "e": "bookTicker",     // Event type
          "u": 400900217,        // Order book updateId
          "s": "BNBUSDT",        // Symbol
          "b": "25.35190000",    // Best bid price
          "B": "31.21000000",    // Best bid qty
          "a": "25.36520000",    // Best ask price
          "A": "40.66000000"     // Best ask qty
        }
        """
        try:
            if data.get('e') != 'bookTicker':
                return
            
            # Extract symbol, best bid and ask
            symbol = data.get('s', '')
            best_bid_str = data.get('b')
            best_ask_str = data.get('a')
            
            if best_bid_str and best_ask_str:
                self.best_bid = float(best_bid_str)
                self.best_ask = float(best_ask_str)
                await self._notify_bbo_update(
                    BBOData(
                        symbol=symbol,
                        bid=self.best_bid,
                        ask=self.best_ask,
                        timestamp=time.time(),
                        sequence=data.get('u'),
                    )
                )

        
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error processing book ticker: {e}")

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
        stream_symbol = self._format_symbol(symbol)
        
        # Start book ticker for BBO (handles its own switching logic)
        await self.start_book_ticker(stream_symbol)
        
        # Check if depth stream switch is needed
        if not self._should_switch_depth_stream(stream_symbol):
            return
        
        # Perform the depth stream switch
        old_symbol = self._current_depth_symbol
        await self._switch_depth_stream(old_symbol, stream_symbol)
        
        # Wait for new data and log result
        success = await self._wait_for_depth_ready(timeout=5.0)
        self._log_depth_switch_result(old_symbol, stream_symbol, success)
    
    def _format_symbol(self, symbol: str) -> str:
        """
        Format symbol for Aster streams.
        
        Args:
            symbol: Normalized symbol (e.g., "TOSHI")
            
        Returns:
            Aster-formatted symbol (e.g., "TOSHIUSDT")
        """
        if self._symbol_formatter:
            try:
                return self._symbol_formatter(symbol)
            except Exception:
                return symbol
        return symbol
    
    def _should_switch_depth_stream(self, target_symbol: str) -> bool:
        """
        Check if depth stream switch is needed.
        
        Args:
            target_symbol: Target symbol to switch to
            
        Returns:
            True if switch needed, False if already on target
        """
        if self._current_depth_symbol == target_symbol and self._depth_task and not self._depth_task.done():
            if self.logger:
                self.logger.debug(f"Already subscribed to depth stream for {target_symbol}")
            return False
        return True
    
    async def _switch_depth_stream(self, old_symbol: Optional[str], new_symbol: str) -> None:
        """
        Switch depth stream from old symbol to new symbol.
        
        Args:
            old_symbol: Current symbol (or None if first time)
            new_symbol: New symbol to switch to
        """
        # Cancel existing depth task if running
        if self._depth_task and not self._depth_task.done():
            if self.logger:
                self.logger.info(f"Switching depth stream from {old_symbol} to {new_symbol}")
            
            # Clear stale order book data
            self.order_book = {"bids": [], "asks": []}
            self.order_book_ready = False
            
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
    
    async def _wait_for_depth_ready(self, timeout: float = 5.0) -> bool:
        """
        Wait for depth stream to become ready.
        
        Args:
            timeout: Maximum time to wait in seconds
            
        Returns:
            True if ready, False if timeout
        """
        start_time = asyncio.get_event_loop().time()
        
        while not self.order_book_ready and (asyncio.get_event_loop().time() - start_time) < timeout:
            await asyncio.sleep(0.1)
        
        return self.order_book_ready
    
    def _log_depth_switch_result(
        self, 
        old_symbol: Optional[str], 
        new_symbol: str, 
        success: bool
    ) -> None:
        """Log the result of depth stream switch."""
        if success:
            if self.logger:
                self.logger.info(
                    f"📚 [ASTER] ✅ Depth stream ready for {new_symbol} "
                    f"({len(self.order_book['bids'])} bids, {len(self.order_book['asks'])} asks)"
                )
        else:
            if self.logger:
                self.logger.warning(
                    f"📚 [ASTER] ⚠️  Depth stream for {new_symbol} not ready yet (timeout after 5.0s)"
                )
    
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
            
            if self.logger:
                self.logger.info(f"📚 [ASTER] Connecting to depth stream: {stream_name}")
            
            reconnect_delay = 1
            max_reconnect_delay = 60
            
            while self.running:
                try:
                    async with websockets.connect(depth_url) as ws:
                        self._depth_ws = ws
                        
                        if self.logger:
                            self.logger.info(f"📚 [ASTER] Connected to depth stream for {symbol}")
                        
                        # Reset reconnect delay on successful connection
                        reconnect_delay = 1
                        
                        # Listen for depth updates
                        async for message in ws:
                            if not self.running:
                                break
                            
                            try:
                                data = json.loads(message)
                                await self._handle_depth_update(data)
                            except json.JSONDecodeError as e:
                                if self.logger:
                                    self.logger.error(f"Failed to parse depth message: {e}")
                            except Exception as e:
                                if self.logger:
                                    self.logger.error(f"Error handling depth update: {e}")
                
                except websockets.exceptions.ConnectionClosed:
                    if self.logger and self.running:
                        self.logger.warning("Depth WebSocket closed, reconnecting...")
                except Exception as e:
                    if self.logger and self.running:
                        self.logger.error(f"Depth WebSocket error: {e}")
                
                # Reconnect with exponential backoff
                if self.running:
                    await asyncio.sleep(reconnect_delay)
                    reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)
        
        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to start depth WebSocket: {e}")
    
    async def _handle_depth_update(self, data: Dict[str, Any]):
        """
        Handle order book depth updates.
        
        Format (partial depth):
        {
          "e": "depthUpdate",
          "E": 1571889248277,  // Event time
          "s": "BTCUSDT",
          "b": [["7403.89", "0.002"], ["7403.90", "3.906"], ...],  // Top 20 bids
          "a": [["7405.96", "3.340"], ["7406.63", "4.525"], ...]   // Top 20 asks
        }
        """
        try:
            if data.get('e') != 'depthUpdate':
                return
            
            # Extract bids and asks
            bids_raw = data.get('b', [])
            asks_raw = data.get('a', [])
            
            # Convert to standard format
            from decimal import Decimal
            
            bids = [
                {'price': Decimal(price), 'size': Decimal(qty)}
                for price, qty in bids_raw
            ]
            asks = [
                {'price': Decimal(price), 'size': Decimal(qty)}
                for price, qty in asks_raw
            ]
            
            # Update order book state (snapshot, not incremental)
            self.order_book = {
                'bids': bids,
                'asks': asks
            }
            self.order_book_ready = True
            
            # ✅ CRITICAL FIX: Extract BBO from depth stream (like Lighter does)
            # This ensures best_bid/best_ask are ALWAYS fresh from snapshot stream
            # even if book ticker stream hasn't received an event yet
            if bids:
                self.best_bid = float(bids[0]['price'])  # Already sorted, first is best
            if asks:
                self.best_ask = float(asks[0]['price'])  # Already sorted, first is best
            
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error processing depth update: {e}")

    def get_order_book(self, levels: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """
        Get formatted order book with optional level limiting.
        
        Args:
            levels: Optional number of levels to return per side.
            
        Returns:
            Order book dict with 'bids' and 'asks' lists, or None if not ready.
        """
        if not self.order_book_ready:
            return None
        
        try:
            # Order book is already in standard format from depth stream
            bids = self.order_book.get('bids', [])
            asks = self.order_book.get('asks', [])
            
            # Validate we have data
            if not bids or not asks:
                return None
            
            # Apply level limiting if requested
            if levels is not None:
                bids = bids[:levels]
                asks = asks[:levels]
            
            return {'bids': bids, 'asks': asks}
            
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error formatting order book: {e}")
            return None

    async def disconnect(self):
        """Disconnect from WebSocket."""
        self.running = False

        # Cancel keepalive task
        if self._keepalive_task and not self._keepalive_task.done():
            self._keepalive_task.cancel()
            try:
                await self._keepalive_task
            except asyncio.CancelledError:
                pass
        
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
        if self.websocket:
            await self.websocket.close()
        
        if self._book_ticker_ws:
            await self._book_ticker_ws.close()
        
        if self._depth_ws:
            await self._depth_ws.close()
            
        if self.logger:
            self.logger.info("WebSocket disconnected")

    def set_logger(self, logger):
        """Set the logger instance."""
        self.logger = logger
