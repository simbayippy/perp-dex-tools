"""
Message parsing and routing for Lighter WebSocket.

Handles incoming WebSocket message parsing, type detection, and routing to appropriate handlers.
"""

import json
import time
from typing import Dict, Any, List, Optional, Callable, Awaitable

import aiohttp

from exchange_clients.base_websocket import BBOData


class LighterMessageHandler:
    """Handles WebSocket message parsing and routing."""

    def __init__(
        self,
        config: Dict[str, Any],
        ws: Optional[aiohttp.ClientWebSocketResponse],
        market_index: int,
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
            ws: WebSocket connection
            market_index: Current market index
            order_book_manager: Order book manager instance
            order_update_callback: Callback for order updates
            liquidation_callback: Callback for liquidations
            positions_callback: Callback for positions
            user_stats_callback: Callback for user stats
            notify_bbo_update_fn: Function to notify BBO updates
            logger: Logger instance
        """
        self.config = config
        self.ws = ws
        self.market_index = market_index
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

    def set_ws(self, ws):
        """Set the WebSocket connection."""
        self.ws = ws

    def set_market_index(self, market_index: int):
        """Set the current market index."""
        self.market_index = market_index

    def _log(self, message: str, level: str = "INFO"):
        """Log message using the logger if available."""
        if self.logger:
            self.logger.log(message, level)

    async def process_message(self, msg: aiohttp.WSMessage) -> Optional[Dict[str, Any]]:
        """
        Process a WebSocket message and return extracted payloads.
        
        Returns:
            Dict with keys: 'notifications', 'positions', 'user_stats', 'request_snapshot'
        """
        # Handle different message types
        if msg.type == aiohttp.WSMsgType.TEXT:
            raw_message = msg.data
        elif msg.type == aiohttp.WSMsgType.BINARY:
            raw_message = msg.data.decode(errors="ignore")
        elif msg.type == aiohttp.WSMsgType.PING:
            if self.ws:
                await self.ws.pong()
            return None
        elif msg.type == aiohttp.WSMsgType.PONG:
            return None
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
            return {"close": True}
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
            return {"error": True}
        else:
            # Skip ping/pong/unknown frames and continue looping
            return None

        try:
            data = json.loads(raw_message)
        except json.JSONDecodeError as exc:
            self._log(f"JSON parsing error in Lighter websocket: {exc}", "ERROR")
            return None

        result = {
            "notifications": None,
            "positions": None,
            "user_stats": None,
            "request_snapshot": False,
        }

        # Process order book messages
        if data.get("type") == "subscribed/order_book":
            await self._handle_order_book_snapshot(data)
        elif data.get("type") == "update/order_book" and self.order_book_manager.snapshot_loaded:
            request_snapshot = await self._handle_order_book_update(data)
            result["request_snapshot"] = request_snapshot
        elif data.get("type") == "ping":
            if self.ws:
                await self.ws.send_str(json.dumps({"type": "pong"}))
        elif data.get("type") == "update/account_orders":
            orders = data.get("orders", {}).get(str(self.market_index), [])
            self._handle_order_update(orders)
        elif data.get("type") == "update/account_all_positions":
            result["positions"] = data
        elif data.get("type") == "update/notification":
            result["notifications"] = data.get("notifs", [])
        elif data.get("type") == "update/user_stats":
            result["user_stats"] = data
        elif data.get("type") == "subscribed/notification":
            self._log("Subscribed to notification channel", "DEBUG")
        elif data.get("type") == "subscribed/account_all_positions":
            self._log("Subscribed to account positions channel", "DEBUG")
        elif data.get("type") == "subscribed/user_stats":
            self._log("Subscribed to user stats channel (real-time balance updates)", "DEBUG")

        return result

    async def _handle_order_book_snapshot(self, data: Dict[str, Any]):
        """Handle order book snapshot message."""
        async with self.order_book_manager.order_book_lock:
            self.order_book_manager.order_book["bids"].clear()
            self.order_book_manager.order_book["asks"].clear()
            order_book = data.get("order_book", {})
            if order_book and "offset" in order_book:
                self.order_book_manager.order_book_offset = order_book["offset"]
            self.order_book_manager.update_order_book("bids", order_book.get("bids", []))
            self.order_book_manager.update_order_book("asks", order_book.get("asks", []))
            self.order_book_manager.snapshot_loaded = True
            self.order_book_manager.order_book_ready = True
            # Mark snapshot as fresh update (ensure timestamp is set even if order book was empty)
            self.order_book_manager.last_update_timestamp = time.time()

            # Extract BBO from the snapshot
            (best_bid_price, _), (best_ask_price, _) = self.order_book_manager.get_best_levels(min_size_usd=0)
            if best_bid_price is not None:
                self.order_book_manager.best_bid = best_bid_price
            if best_ask_price is not None:
                self.order_book_manager.best_ask = best_ask_price
            
            if self.notify_bbo_update:
                await self.notify_bbo_update(
                    BBOData(
                        symbol=str(getattr(self.config, "ticker", self.market_index)),
                        bid=self.order_book_manager.best_bid,
                        ask=self.order_book_manager.best_ask,
                        timestamp=time.time(),
                        sequence=self.order_book_manager.order_book_offset,
                    )
                )

            self._log(
                f"[LIGHTER] Order book snapshot loaded with {len(self.order_book_manager.order_book['bids'])} bids and "
                f"{len(self.order_book_manager.order_book['asks'])} asks (BBO: {self.order_book_manager.best_bid}/{self.order_book_manager.best_ask})",
                "INFO",
            )

    async def _handle_order_book_update(self, data: Dict[str, Any]) -> bool:
        """Handle order book update message. Returns True if snapshot should be requested."""
        if not self.order_book_manager.handle_order_book_cutoff(data):
            self._log("Skipping incomplete order book update", "WARNING")
            return False

        order_book = data.get("order_book", {})
        offset = order_book.get("offset")
        if offset is None:
            self._log("Order book update missing offset, skipping", "WARNING")
            return False

        if not self.order_book_manager.validate_order_book_offset(offset):
            if self.order_book_manager.order_book_sequence_gap:
                return True
            return False

        self.order_book_manager.update_order_book("bids", order_book.get("bids", []))
        self.order_book_manager.update_order_book("asks", order_book.get("asks", []))

        if not self.order_book_manager.validate_order_book_integrity():
            return True
        else:
            (best_bid_price, _), (best_ask_price, _) = self.order_book_manager.get_best_levels(min_size_usd=0)
            if best_bid_price is not None:
                self.order_book_manager.best_bid = best_bid_price
            if best_ask_price is not None:
                self.order_book_manager.best_ask = best_ask_price
            
            if self.notify_bbo_update:
                await self.notify_bbo_update(
                    BBOData(
                        symbol=str(getattr(self.config, "ticker", self.market_index)),
                        bid=self.order_book_manager.best_bid,
                        ask=self.order_book_manager.best_ask,
                        timestamp=time.time(),
                        sequence=offset,
                    )
                )
        
        return False

    def _handle_order_update(self, order_data_list: List[Dict[str, Any]]):
        """Handle order update from WebSocket."""
        try:
            # Call the order update callback if it exists
            if self.order_update_callback:
                self.order_update_callback(order_data_list)
        except Exception as e:
            self._log(f"Error handling order update: {e}", "ERROR")

    async def dispatch_liquidations(self, notifs: List[Dict[str, Any]]) -> None:
        """Forward liquidation notifications to the registered callback."""
        if not self.liquidation_callback or not notifs:
            return

        try:
            await self.liquidation_callback(notifs)
        except Exception as exc:
            self._log(f"Error dispatching liquidation notifications: {exc}", "ERROR")

    async def dispatch_positions(self, payload: Dict[str, Any]) -> None:
        """Forward positions update to the registered callback."""
        if not self.positions_callback:
            return

        try:
            await self.positions_callback(payload)
        except Exception as exc:
            self._log(f"Error dispatching positions update: {exc}", "ERROR")

    async def dispatch_user_stats(self, payload: Dict[str, Any]) -> None:
        """Forward user stats update to the registered callback."""
        if not self.user_stats_callback:
            return

        try:
            await self.user_stats_callback(payload)
        except Exception as exc:
            self._log(f"Error dispatching user stats update: {exc}", "ERROR")

