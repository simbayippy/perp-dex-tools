"""
Lighter WebSocket Manager

Handles WebSocket connections for Lighter exchange order updates and order book.
Custom implementation without using the official SDK for more control over connection management.

Features:
- Order book management with sequence validation
- Automatic reconnection with exponential backoff
- Order update callbacks for account orders
- Health checks and integrity validation
"""

import asyncio
import json
import os
import time
from typing import Dict, Any, List, Optional, Tuple, Callable, Awaitable
from urllib.parse import urlparse

import aiohttp

from exchange_clients.base_websocket import BaseWebSocketManager, BBOData


class LighterWebSocketManager(BaseWebSocketManager):
    """WebSocket manager for Lighter order updates and order book."""

    RECONNECT_BACKOFF_INITIAL = 1.0
    RECONNECT_BACKOFF_MAX = 30.0

    def __init__(
        self,
        config: Dict[str, Any],
        order_update_callback: Optional[Callable] = None,
        liquidation_callback: Optional[Callable[[List[Dict[str, Any]]], Awaitable[None]]] = None,
        positions_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
        user_stats_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    ):
        self.config = config
        self.order_update_callback = order_update_callback
        self.liquidation_callback = liquidation_callback
        self.positions_callback = positions_callback
        self.user_stats_callback = user_stats_callback
        super().__init__()
        self.ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._listener_task: Optional[asyncio.Task] = None

        # Order book state
        self.order_book = {"bids": {}, "asks": {}}
        self.best_bid = None
        self.best_ask = None
        self.snapshot_loaded = False
        self.order_book_offset = None
        self.order_book_sequence_gap = False
        self.order_book_lock = asyncio.Lock()

        # WebSocket URL
        self.ws_url = "wss://mainnet.zklighter.elliot.ai/stream"
        self.market_index = config.contract_id
        self.account_index = config.account_index
        self.lighter_client = config.lighter_client

    def set_logger(self, logger):
        """Set the logger instance."""
        self.logger = logger

    def _log(self, message: str, level: str = "INFO"):
        """Log message using the logger if available."""
        if self.logger:
            self.logger.log(message, level)

    async def _get_session(self) -> aiohttp.ClientSession:
        """
        Lazily initialize the aiohttp session used for websocket connections.

        The session is configured with trust_env=False so that only the explicit
        proxy arguments we pass are respected. This keeps behavior predictable
        when SessionProxyManager has applied environment variables.
        """
        if self._session and not self._session.closed:
            return self._session

        timeout = aiohttp.ClientTimeout(total=None)
        self._session = aiohttp.ClientSession(timeout=timeout, trust_env=False)
        return self._session

    async def _close_session(self) -> None:
        """Close the websocket session if it exists."""
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    def _proxy_kwargs(self) -> Dict[str, Any]:
        """
        Build proxy kwargs for aiohttp based on the active HTTP proxy.

        Returns empty dict when no HTTP proxy is configured. SOCKS proxies are
        already handled via SessionProxyManager's socket patching, so we skip
        explicit wiring in that case to avoid incompatible schemes.
        """
        proxy_url = (
            os.environ.get("HTTPS_PROXY")
            or os.environ.get("HTTP_PROXY")
            or os.environ.get("ALL_PROXY")
            or os.environ.get("https_proxy")
            or os.environ.get("http_proxy")
            or os.environ.get("all_proxy")
        )

        if not proxy_url:
            return {}

        parsed = urlparse(proxy_url)
        if parsed.scheme.lower() not in {"http", "https"}:
            # aiohttp does not support socks proxies natively; rely on socket patching.
            return {}

        proxy_auth = None
        if parsed.username or parsed.password:
            proxy_auth = aiohttp.BasicAuth(parsed.username or "", parsed.password or "")
            hostname = parsed.hostname or ""
            port = f":{parsed.port}" if parsed.port else ""
            netloc = f"{hostname}{port}"
            parsed = parsed._replace(netloc=netloc, path="", params="", query="", fragment="")

        clean_proxy_url = parsed.geturl()
        if proxy_auth:
            return {"proxy": clean_proxy_url, "proxy_auth": proxy_auth}
        return {"proxy": clean_proxy_url}

    def update_order_book(self, side: str, updates: List[Dict[str, Any]]):
        """Update the order book with new price/size information."""
        if side not in ["bids", "asks"]:
            self._log(f"Invalid side parameter: {side}. Must be 'bids' or 'asks'", "ERROR")
            return

        ob = self.order_book[side]

        if not isinstance(updates, list):
            self._log(f"Invalid updates format for {side}: expected list, got {type(updates)}", "ERROR")
            return

        for update in updates:
            try:
                if not isinstance(update, dict):
                    self._log(f"Invalid update format: expected dict, got {type(update)}", "ERROR")
                    continue

                if "price" not in update or "size" not in update:
                    self._log(f"Missing required fields in update: {update}", "ERROR")
                    continue

                price = float(update["price"])
                size = float(update["size"])

                # Validate price and size are reasonable
                if price <= 0:
                    self._log(f"Invalid price in update: {price}", "ERROR")
                    continue

                if size < 0:
                    self._log(f"Invalid size in update: {size}", "ERROR")
                    continue

                if size == 0:
                    ob.pop(price, None)
                else:
                    ob[price] = size
            except (KeyError, ValueError, TypeError) as e:
                self._log(f"Error processing order book update: {e}, update: {update}", "ERROR")
                continue

    def validate_order_book_offset(self, new_offset: int) -> bool:
        """Validate that the new offset is sequential and handle gaps."""
        if self.order_book_offset is None:
            # First offset, always valid
            self.order_book_offset = new_offset
            return True

        # Check if the new offset is sequential (should be +1)
        expected_offset = self.order_book_offset + 1
        if new_offset == expected_offset:
            # Sequential update, update our offset
            self.order_book_offset = new_offset
            self.order_book_sequence_gap = False
            return True
        elif new_offset > expected_offset:
            # Gap detected - we missed some updates
            self._log(f"Order book sequence gap detected! Expected offset {expected_offset}, got {new_offset}", "WARNING")
            self.order_book_sequence_gap = True
            return False
        else:
            # Out of order or duplicate update
            self._log(f"Out of order update received! Expected offset {expected_offset}, got {new_offset}", "WARNING")
            return True  # Don't reconnect for out-of-order updates, just ignore them

    def handle_order_book_cutoff(self, data: Dict[str, Any]) -> bool:
        """Handle cases where order book updates might be cutoff or incomplete."""
        order_book = data.get("order_book", {})

        # Validate required fields
        if not order_book or "code" not in order_book or "offset" not in order_book:
            self._log("Incomplete order book update - missing required fields", "WARNING")
            return False

        # Check if the order book has the expected structure
        if "asks" not in order_book or "bids" not in order_book:
            self._log("Incomplete order book update - missing bids/asks", "WARNING")
            return False

        # Validate that asks and bids are lists
        if not isinstance(order_book["asks"], list) or not isinstance(order_book["bids"], list):
            self._log("Invalid order book structure - asks/bids should be lists", "WARNING")
            return False

        return True

    def validate_order_book_integrity(self) -> bool:
        """Validate that the order book is internally consistent."""
        try:
            if not self.order_book["bids"] or not self.order_book["asks"]:
                # Empty order book is valid
                return True

            # Get best bid and best ask
            best_bid = max(self.order_book["bids"].keys())
            best_ask = min(self.order_book["asks"].keys())

            # Check if best bid is higher than best ask (inconsistent)
            if best_bid >= best_ask:
                self._log(f"Order book inconsistency detected! Best bid: {best_bid}, Best ask: {best_ask}", "WARNING")
                return False

            return True
        except (ValueError, KeyError) as e:
            self._log(f"Error validating order book integrity: {e}", "ERROR")
            return False

    async def prepare_market_feed(self, symbol: Optional[str]) -> None:
        """
        Ensure the order book stream targets the requested symbol.
        
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
            # Step 1: Lookup target market_id for the symbol
            target_market = await self._lookup_market_id(symbol)
            if target_market is None:
                return
            
            # Step 2: Validate if switch is needed
            if not self._validate_market_switch_needed(target_market):
                return
            
            # Step 3: Perform the market switch
            old_market_id = self.market_index
            await self._perform_market_switch(old_market_id, target_market)
            
            # Step 4: Wait for new data to arrive
            success = await self._wait_for_market_ready(timeout=5.0)
            
            # Step 5: Log result
            self._log_market_switch_result(old_market_id, target_market, success)
            
        except Exception as exc:
            self._log(f"Error switching market: {exc}", "ERROR")
    
    async def _lookup_market_id(self, symbol: str) -> Optional[int]:
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
    
    def _validate_market_switch_needed(self, target_market: int) -> bool:
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
    
    async def _perform_market_switch(self, old_market_id: int, new_market_id: int) -> None:
        """
        Execute the market switch: unsubscribe old, subscribe new, update config.
        
        Args:
            old_market_id: Current market_id to unsubscribe from
            new_market_id: New market_id to subscribe to
        """
        self._log(
            f"[LIGHTER] 🔄 Switching order book from market {old_market_id} to {new_market_id}",
            "INFO"
        )
        
        # Clear stale order book data
        await self.reset_order_book()
        
        # Unsubscribe from old market
        await self._unsubscribe_market(old_market_id)
        
        # Update internal state
        self.market_index = new_market_id
        
        # Update config to keep it synchronized (critical for order placement!)
        self._update_market_config(new_market_id)
        
        # Subscribe to new market
        await self._subscribe_market(new_market_id)
    
    async def _unsubscribe_market(self, market_id: int) -> None:
        """Unsubscribe from order book and account orders for a market."""
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
    
    async def _subscribe_market(self, market_id: int) -> None:
        """Subscribe to order book and account orders for a market."""
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
    
    async def _wait_for_market_ready(self, timeout: float = 5.0) -> bool:
        """
        Wait for new market data to arrive after switching.
        
        Args:
            timeout: Maximum time to wait in seconds
            
        Returns:
            True if snapshot loaded, False if timeout
        """
        start_time = asyncio.get_event_loop().time()
        
        while not self.snapshot_loaded and (asyncio.get_event_loop().time() - start_time) < timeout:
            await asyncio.sleep(0.1)
        
        return self.snapshot_loaded
    
    def _log_market_switch_result(
        self, 
        old_market_id: int, 
        new_market_id: int, 
        success: bool
    ) -> None:
        """Log the result of a market switch operation."""
        if success:
            self._log(
                f"[LIGHTER] ✅ Switched order book from market {old_market_id} to {new_market_id} "
                f"({len(self.order_book['bids'])} bids, {len(self.order_book['asks'])} asks) | "
                f"config.contract_id updated to {new_market_id}",
                "INFO"
            )
        else:
            self._log(
                f"[LIGHTER] ⚠️  Switched to market {new_market_id} but snapshot not loaded yet "
                f"(timeout after 5.0s)",
                "WARNING"
            )

    async def request_fresh_snapshot(self):
        """Request a fresh order book snapshot when we detect inconsistencies."""
        try:
            if not self.ws:
                return

            # Unsubscribe and resubscribe to get a fresh snapshot
            unsubscribe_msg = json.dumps({"type": "unsubscribe", "channel": f"order_book/{self.market_index}"})
            await self.ws.send_str(unsubscribe_msg)

            # Wait a moment for the unsubscribe to process
            await asyncio.sleep(1)

            # Resubscribe to get a fresh snapshot
            subscribe_msg = json.dumps({"type": "subscribe", "channel": f"order_book/{self.market_index}"})
            await self.ws.send_str(subscribe_msg)

            self._log("Requested fresh order book snapshot", "INFO")
        except Exception as e:
            self._log(f"Error requesting fresh snapshot: {e}", "ERROR")
            raise

    def get_best_levels(self, min_size_usd: float = 0) -> Tuple[Tuple[Optional[float], Optional[float]], Tuple[Optional[float], Optional[float]]]:
        """
        Get the best bid and ask levels from order book.
        
        Args:
            min_size_usd: Minimum size in USD (default: 0 = no filter, return true best bid/ask)
        
        Returns:
            ((best_bid_price, best_bid_size), (best_ask_price, best_ask_size))
        """
        try:
            # Get all bid levels with sufficient size
            bid_levels = [(price, size) for price, size in self.order_book["bids"].items()
                          if size * price >= min_size_usd]

            # Get all ask levels with sufficient size
            ask_levels = [(price, size) for price, size in self.order_book["asks"].items()
                          if size * price >= min_size_usd]

            # Get best bid (highest price) and best ask (lowest price)
            best_bid = max(bid_levels) if bid_levels else (None, None)
            best_ask = min(ask_levels) if ask_levels else (None, None)

            return best_bid, best_ask
        except (ValueError, KeyError) as e:
            self._log(f"Error getting best levels: {e}", "ERROR")
            return (None, None), (None, None)

    def get_order_book(self, levels: Optional[int] = None) -> Optional[Dict[str, List[Dict[str, Any]]]]:
        """
        Get formatted order book with optional level limiting.
        
        Args:
            levels: Optional number of levels to return per side.
            
        Returns:
            Order book dict with 'bids' and 'asks' lists, or None if not ready.
        """
        if not self.snapshot_loaded:
            return None
        
        try:
            from decimal import Decimal
            
            # Convert to standard format and sort
            bids = [
                {'price': Decimal(str(price)), 'size': Decimal(str(size))}
                for price, size in sorted(self.order_book["bids"].items(), reverse=True)
            ]
            asks = [
                {'price': Decimal(str(price)), 'size': Decimal(str(size))}
                for price, size in sorted(self.order_book["asks"].items())
            ]
            
            # Apply level limiting if requested
            if levels is not None:
                bids = bids[:levels]
                asks = asks[:levels]
            
            return {'bids': bids, 'asks': asks}
            
        except Exception as e:
            self._log(f"Error formatting order book: {e}", "ERROR")
            return None

    def cleanup_old_order_book_levels(self):
        """Clean up old order book levels to prevent memory leaks."""
        try:
            # Keep only the top 100 levels on each side to prevent memory bloat
            max_levels = 100

            # Clean up bids (keep highest prices)
            if len(self.order_book["bids"]) > max_levels:
                sorted_bids = sorted(self.order_book["bids"].items(), reverse=True)
                self.order_book["bids"].clear()
                for price, size in sorted_bids[:max_levels]:
                    self.order_book["bids"][price] = size

            # Clean up asks (keep lowest prices)
            if len(self.order_book["asks"]) > max_levels:
                sorted_asks = sorted(self.order_book["asks"].items())
                self.order_book["asks"].clear()
                for price, size in sorted_asks[:max_levels]:
                    self.order_book["asks"][price] = size

        except Exception as e:
            self._log(f"Error cleaning up order book levels: {e}", "ERROR")

    async def reset_order_book(self):
        """Reset the order book state when reconnecting."""
        async with self.order_book_lock:
            self.order_book["bids"].clear()
            self.order_book["asks"].clear()
            self.snapshot_loaded = False
            self.best_bid = None
            self.best_ask = None
            self.order_book_offset = None
            self.order_book_sequence_gap = False
            self.order_book_ready = False

    async def _subscribe_channels(self) -> None:
        """Subscribe to the required Lighter channels."""
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
            if self.positions_callback:
                subscription_messages.append({
                    "type": "subscribe",
                    "channel": f"account_all_positions/{self.account_index}",
                    "auth": auth_token,
                })
            if self.liquidation_callback:
                subscription_messages.append({
                    "type": "subscribe",
                    "channel": f"notification/{self.account_index}",
                    "auth": auth_token,
                })
            if self.user_stats_callback:
                subscription_messages.append({
                    "type": "subscribe",
                    "channel": f"user_stats/{self.account_index}",
                    "auth": auth_token,
                })
        else:
            if self.liquidation_callback or self.user_stats_callback:
                self._log("Skipping account order/notification/user_stats subscriptions (no auth token)", "WARNING")

        for message in subscription_messages:
            await self.ws.send_str(json.dumps(message))

    async def _open_connection(self) -> None:
        """Establish the websocket connection and subscribe to channels."""
        session = await self._get_session()
        proxy_kwargs = self._proxy_kwargs()
        if proxy_kwargs.get("proxy"):
            self._log(f"[LIGHTER] Using HTTP proxy for websocket: {proxy_kwargs['proxy']}", "INFO")

        try:
            self.ws = await session.ws_connect(self.ws_url, **proxy_kwargs)
            self._log("[LIGHTER] 🔗 Connected to websocket", "INFO")
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            self._log(f"Failed to connect to Lighter websocket: {exc}", "ERROR")
            raise

        try:
            await self._subscribe_channels()
        except Exception as exc:
            if self.ws and not self.ws.closed:
                await self.ws.close()
            self.ws = None
            self._log(f"Subscription failed: {exc}", "ERROR")
            raise

    async def _consume_messages(self) -> None:
        """Listen for messages on the WebSocket connection."""
        cleanup_counter = 0
        while self.running and self.ws:
            try:
                msg = await self.ws.receive()
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                self._log(f"Error receiving websocket message: {exc}", "ERROR")
                break
            except asyncio.CancelledError:
                raise

            if msg.type == aiohttp.WSMsgType.TEXT:
                raw_message = msg.data
            elif msg.type == aiohttp.WSMsgType.BINARY:
                raw_message = msg.data.decode(errors="ignore")
            elif msg.type == aiohttp.WSMsgType.PING:
                await self.ws.pong()
                continue
            elif msg.type == aiohttp.WSMsgType.PONG:
                continue
            elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.CLOSED):
                self._log("Lighter websocket connection closed by server", "WARNING")
                close_code = getattr(self.ws, "close_code", None) if self.ws else None
                close_reason = getattr(self.ws, "close_reason", None) if self.ws else None
                self._log(
                    (
                        f"[LIGHTER] Websocket close details: msg_type={msg.type.name}, "
                        f"msg_data={msg.data}, msg_extra={msg.extra}, "
                        f"session_close_code={close_code}, session_close_reason={close_reason}"
                    ),
                    "INFO",
                )
                break
            elif msg.type == aiohttp.WSMsgType.ERROR:
                self._log(f"Lighter websocket error: {msg.data}", "ERROR")
                close_code = getattr(self.ws, "close_code", None) if self.ws else None
                close_reason = getattr(self.ws, "close_reason", None) if self.ws else None
                self._log(
                    (
                        f"[LIGHTER] Websocket error details: msg_extra={msg.extra}, "
                        f"session_close_code={close_code}, session_close_reason={close_reason}"
                    ),
                    "INFO",
                )
                break
            else:
                # Skip ping/pong/unknown frames and continue looping
                continue

            try:
                data = json.loads(raw_message)
            except json.JSONDecodeError as exc:
                self._log(f"JSON parsing error in Lighter websocket: {exc}", "ERROR")
                continue

            notifications_for_dispatch: Optional[List[Dict[str, Any]]] = None
            request_snapshot = False
            positions_payload: Optional[Dict[str, Any]] = None
            user_stats_payload: Optional[Dict[str, Any]] = None

            async with self.order_book_lock:
                if data.get("type") == "subscribed/order_book":
                    self.order_book["bids"].clear()
                    self.order_book["asks"].clear()
                    order_book = data.get("order_book", {})
                    if order_book and "offset" in order_book:
                        self.order_book_offset = order_book["offset"]
                    self.update_order_book("bids", order_book.get("bids", []))
                    self.update_order_book("asks", order_book.get("asks", []))
                    self.snapshot_loaded = True
                    self.order_book_ready = True

                    # Extract BBO from the snapshot (not just updates)
                    (best_bid_price, _), (best_ask_price, _) = self.get_best_levels(min_size_usd=0)
                    if best_bid_price is not None:
                        self.best_bid = best_bid_price
                    if best_ask_price is not None:
                        self.best_ask = best_ask_price
                    await self._notify_bbo_update(
                        BBOData(
                            symbol=str(getattr(self.config, "ticker", self.market_index)),
                            bid=self.best_bid,
                            ask=self.best_ask,
                            timestamp=time.time(),
                            sequence=self.order_book_offset,
                        )
                    )

                    self._log(
                        f"[LIGHTER] Order book snapshot loaded with {len(self.order_book['bids'])} bids and "
                        f"{len(self.order_book['asks'])} asks (BBO: {self.best_bid}/{self.best_ask})",
                        "INFO",
                    )

                elif data.get("type") == "update/order_book" and self.snapshot_loaded:
                    if not self.handle_order_book_cutoff(data):
                        self._log("Skipping incomplete order book update", "WARNING")
                        continue

                    order_book = data.get("order_book", {})
                    offset = order_book.get("offset")
                    if offset is None:
                        self._log("Order book update missing offset, skipping", "WARNING")
                        continue

                    if not self.validate_order_book_offset(offset):
                        if self.order_book_sequence_gap:
                            request_snapshot = True
                        continue

                    self.update_order_book("bids", order_book.get("bids", []))
                    self.update_order_book("asks", order_book.get("asks", []))

                    if not self.validate_order_book_integrity():
                        request_snapshot = True
                    else:
                        (best_bid_price, _), (best_ask_price, _) = self.get_best_levels(min_size_usd=0)
                        if best_bid_price is not None:
                            self.best_bid = best_bid_price
                        if best_ask_price is not None:
                            self.best_ask = best_ask_price
                        await self._notify_bbo_update(
                            BBOData(
                                symbol=str(getattr(self.config, "ticker", self.market_index)),
                                bid=self.best_bid,
                                ask=self.best_ask,
                                timestamp=time.time(),
                                sequence=offset,
                            )
                        )

                elif data.get("type") == "ping":
                    await self.ws.send_str(json.dumps({"type": "pong"}))

                elif data.get("type") == "update/account_orders":
                    orders = data.get("orders", {}).get(str(self.market_index), [])
                    self.handle_order_update(orders)

                elif data.get("type") == "update/account_all_positions":
                    positions_payload = data

                elif data.get("type") == "update/notification":
                    notifications_for_dispatch = data.get("notifs", [])

                elif data.get("type") == "subscribed/notification":
                    self._log("Subscribed to notification channel", "DEBUG")

                elif data.get("type") == "subscribed/account_all_positions":
                    self._log("Subscribed to account positions channel", "DEBUG")

                elif data.get("type") == "update/user_stats":
                    user_stats_payload = data

                elif data.get("type") == "subscribed/user_stats":
                    self._log("Subscribed to user stats channel (real-time balance updates)", "DEBUG")

            cleanup_counter += 1
            if cleanup_counter >= 1000:
                self.cleanup_old_order_book_levels()
                cleanup_counter = 0

            if request_snapshot:
                try:
                    await self.request_fresh_snapshot()
                    self.order_book_sequence_gap = False
                except Exception as exc:
                    self._log(f"Failed to request fresh snapshot: {exc}", "ERROR")
                    break

            if notifications_for_dispatch:
                await self._dispatch_liquidations(notifications_for_dispatch)

            if positions_payload and self.positions_callback:
                try:
                    await self.positions_callback(positions_payload)
                except Exception as exc:
                    self._log(f"Error dispatching positions update: {exc}", "ERROR")

            if user_stats_payload and self.user_stats_callback:
                try:
                    await self.user_stats_callback(user_stats_payload)
                except Exception as exc:
                    self._log(f"Error dispatching user stats update: {exc}", "ERROR")

    async def _cleanup_current_ws(self) -> None:
        """Close the active websocket connection if it exists."""
        if self.ws and not self.ws.closed:
            try:
                await self.ws.close()
            except Exception as exc:
                self._log(f"Error closing websocket: {exc}", "ERROR")
        self.ws = None
        self.order_book_ready = False

    async def _reconnect(self) -> None:
        """Attempt to reconnect with exponential backoff."""
        delay = self.RECONNECT_BACKOFF_INITIAL
        attempt = 1
        while self.running:
            self._log(
                f"[LIGHTER] Reconnecting websocket (attempt {attempt})",
                "WARNING",
            )
            try:
                await self.reset_order_book()
                await self._open_connection()
                self._log("[LIGHTER] Websocket reconnect successful", "INFO")
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._log(
                    f"[LIGHTER] Reconnect attempt {attempt} failed: {exc}. Retrying in {delay:.1f}s",
                    "ERROR",
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, self.RECONNECT_BACKOFF_MAX)
                attempt += 1

        self._log("[LIGHTER] Reconnect aborted; manager no longer running", "WARNING")

    async def _listen_loop(self) -> None:
        """Keep the websocket stream alive and reconnect on failures."""
        try:
            while self.running:
                try:
                    await self._consume_messages()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._log(f"[LIGHTER] Websocket listener error: {exc}", "ERROR")
                finally:
                    await self._cleanup_current_ws()
                    self.order_book_ready = False
                    self.snapshot_loaded = False

                if not self.running:
                    break

                await self._reconnect()
        except asyncio.CancelledError:
            pass
        finally:
            await self._cleanup_current_ws()
            await self._close_session()
            self.running = False
            self._log("WebSocket listener stopped", "INFO")

    def handle_order_update(self, order_data_list: List[Dict[str, Any]]):
        """Handle order update from WebSocket."""
        try:
            # Call the order update callback if it exists
            if self.order_update_callback:
                self.order_update_callback(order_data_list)

        except Exception as e:
            self._log(f"Error handling order update: {e}", "ERROR")

    async def _dispatch_liquidations(self, notifs: List[Dict[str, Any]]) -> None:
        """Forward liquidation notifications to the registered callback."""
        if not self.liquidation_callback or not notifs:
            return

        try:
            await self.liquidation_callback(notifs)
        except Exception as exc:
            self._log(f"Error dispatching liquidation notifications: {exc}", "ERROR")

    async def connect(self):
        """Connect to the Lighter WebSocket and start the listener task."""
        if self.running:
            return

        await self.reset_order_book()

        try:
            await self._open_connection()
        except Exception:
            await self._close_session()
            raise

        self.running = True
        self._listener_task = asyncio.create_task(self._listen_loop(), name="lighter-ws-listener")

    async def disconnect(self):
        """Disconnect from WebSocket."""
        if not self.running and not self._listener_task:
            return

        self.running = False

        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
            finally:
                self._listener_task = None

        await self._cleanup_current_ws()
        await self._close_session()

        self._log("WebSocket disconnected", "INFO")
