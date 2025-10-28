"""
Backpack WebSocket Manager

Maintains both private (account) and public (market data) websocket streams.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from decimal import Decimal, InvalidOperation
from typing import Any, Awaitable, Callable, Dict, List, Optional

from cryptography.hazmat.primitives.asymmetric import ed25519
import websockets

from exchange_clients.base_models import MissingCredentialsError
from exchange_clients.base_websocket import BaseWebSocketManager, BBOData


class BackpackWebSocketManager(BaseWebSocketManager):
    """WebSocket manager for Backpack order, position, and depth streams."""

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
        super().__init__()
        self.public_key = public_key
        self.secret_key = secret_key
        self.symbol = symbol
        self.order_update_callback = order_update_callback
        self.depth_fetcher = depth_fetcher
        self.liquidation_callback = liquidation_callback
        self.depth_stream_interval = depth_stream_interval
        self._symbol_formatter = symbol_formatter

        self.ws_url = "wss://ws.backpack.exchange"

        self._account_ws: Optional[websockets.WebSocketClientProtocol] = None
        self._depth_ws: Optional[websockets.WebSocketClientProtocol] = None
        self._account_task: Optional[asyncio.Task] = None
        self._depth_task: Optional[asyncio.Task] = None

        self._ready_event = asyncio.Event()
        self._account_ready_event = asyncio.Event()
        self._depth_ready_event = asyncio.Event()

        self.best_bid: Optional[Decimal] = None
        self.best_ask: Optional[Decimal] = None
        self.order_book: Dict[str, List[Dict[str, Decimal]]] = {"bids": [], "asks": []}
        self.order_book_ready: bool = False

        # Maintain internal order book representation keyed by price
        self._order_levels: Dict[str, Dict[Decimal, Decimal]] = {
            "bids": {},
            "asks": {},
        }
        self._last_update_id: Optional[int] = None
        self._depth_reload_lock = asyncio.Lock()

        try:
            secret_bytes = base64.b64decode(secret_key)
            self.private_key = ed25519.Ed25519PrivateKey.from_private_bytes(secret_bytes)
        except Exception as exc:  # pragma: no cover - defensive
            raise MissingCredentialsError(f"Invalid Backpack secret key: {exc}") from exc

    # ------------------------------------------------------------------ #
    # Public interface
    # ------------------------------------------------------------------ #

    async def prepare_market_feed(self, symbol: Optional[str]) -> None:
        """
        Ensure account and depth streams follow the requested symbol.
        
        Implementation follows the recommended pattern from BaseWebSocketManager:
        1. Validate: Check if already on target market
        2. Clear: Reset stale order book data (via update_symbol)
        3. Switch: Full disconnect/reconnect cycle
        4. Wait: Block until new data arrives
        5. Update: Log completion
        """
        if not symbol:
            return

        # Format symbol for Backpack
        target_symbol = self._format_symbol(symbol)
        
        # Check if already on target (Step 1)
        if not self._should_switch_symbol(target_symbol):
            # Already aligned; make sure order book is marked ready if needed
            if not self.order_book_ready and self.depth_fetcher:
                await self.wait_for_order_book(timeout=2.0)
            return
        
        # Perform the switch (Steps 2 & 3)
        await self._perform_symbol_switch(target_symbol)
        
        # Wait for ready and log result (Steps 4 & 5)
        if self.depth_fetcher:
            success = await self.wait_for_order_book(timeout=5.0)
            self._log_switch_result(target_symbol, success)
    
    def _format_symbol(self, symbol: str) -> str:
        """
        Format symbol for Backpack streams.
        
        Args:
            symbol: Normalized symbol
            
        Returns:
            Backpack-formatted symbol
        """
        if self._symbol_formatter:
            try:
                return self._symbol_formatter(symbol)
            except Exception:
                return symbol
        return symbol
    
    def _should_switch_symbol(self, target_symbol: str) -> bool:
        """
        Check if symbol switch is needed.
        
        Args:
            target_symbol: Target symbol to switch to
            
        Returns:
            True if switch needed, False if already on target
        """
        if target_symbol == self.symbol:
            # Already aligned - no switch needed
            return False
        return True
    
    async def _perform_symbol_switch(self, new_symbol: str) -> None:
        """
        Execute symbol switch via full disconnect/reconnect cycle.
        
        This is Backpack's approach - unlike Lighter/Aster which switch subscriptions,
        Backpack does a clean disconnect/reconnect for simpler state management.
        
        Args:
            new_symbol: New symbol to switch to
        """
        if self.logger:
            self.logger.info(f"[BACKPACK] 🔄 Switching websocket streams to {new_symbol}")

        # Clear stale data and update symbol
        self.update_symbol(new_symbol)
        
        # Update config to keep it synchronized
        self._update_market_config(new_symbol)

        # Full reconnection cycle
        if self.running:
            await self.disconnect()
            await self.connect()
    
    def _log_switch_result(self, symbol: str, success: bool) -> None:
        """Log the result of symbol switch operation."""
        if success and self.logger:
            bid_count = len(self.order_book.get("bids", []))
            ask_count = len(self.order_book.get("asks", []))
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
        self._ready_event.clear()
        self._account_ready_event.clear()
        self._depth_ready_event.clear()

        self._account_task = asyncio.create_task(self._run_account_stream(), name="backpack-account-ws")
        if self.depth_fetcher:
            self._depth_task = asyncio.create_task(self._run_depth_stream(), name="backpack-depth-ws")
        else:
            # No depth stream - mark as ready so callers don't hang.
            self._depth_ready_event.set()

        # Allow tasks to spin up
        await asyncio.sleep(0)
        if self.logger:
            self.logger.info("[BACKPACK] 🔗 Connected to ws")

    async def disconnect(self) -> None:
        """Stop websocket tasks and close sockets."""
        if not self.running:
            return

        self.running = False

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

        await self._close_account_ws()
        await self._close_depth_ws()

        self._account_task = None
        self._depth_task = None

        self._ready_event.clear()
        self._account_ready_event.clear()
        self._depth_ready_event.clear()
        self.order_book_ready = False

    def update_symbol(self, symbol: Optional[str]) -> None:
        """
        Update symbol subscription.

        To fully switch symbols, call `disconnect()`, update the symbol, and
        then call `connect()`.
        """
        if symbol == self.symbol:
            return
        self.symbol = symbol
        self.order_book_ready = False
        self._depth_ready_event.clear()
        
        # ✅ CRITICAL: Clear cached order book data to prevent serving stale prices
        self.best_bid = None
        self.best_ask = None
        self.order_book = {"bids": [], "asks": []}
        self._order_levels = {"bids": {}, "asks": {}}
        self._last_update_id = None

    def get_order_book(self, levels: Optional[int] = None) -> Optional[Dict[str, List[Dict[str, Decimal]]]]:
        """
        Retrieve a snapshot of the maintained order book.

        Args:
            levels: Optional number of levels to return per side.

        Returns:
            Order book dict or None if not ready.
        """
        if not self.order_book_ready:
            return None

        bids = self.order_book["bids"]
        asks = self.order_book["asks"]
        if levels is not None:
            bids = bids[:levels]
            asks = asks[:levels]

        return {
            "bids": [{"price": level["price"], "size": level["size"]} for level in bids],
            "asks": [{"price": level["price"], "size": level["size"]} for level in asks],
        }

    # ------------------------------------------------------------------ #
    # Account (private) stream management
    # ------------------------------------------------------------------ #

    async def _run_account_stream(self) -> None:
        backoff_seconds = 1.0
        while self.running:
            if not self.symbol:
                await asyncio.sleep(0.5)
                continue
            try:
                await self._connect_account_ws()
                self._account_ready_event.set()
                self._ready_event.set()
                backoff_seconds = 1.0
                await self._listen_account_ws()
            except asyncio.CancelledError:
                break
            except Exception as exc:  # pragma: no cover - defensive
                if self.logger:
                    self.logger.error(f"[BACKPACK] Account WS error: {exc}")
                await asyncio.sleep(min(backoff_seconds, self._MAX_BACKOFF_SECONDS))
                backoff_seconds = min(backoff_seconds * 2, self._MAX_BACKOFF_SECONDS)
            finally:
                await self._close_account_ws()

        self._account_ready_event.clear()

    async def _connect_account_ws(self) -> None:
        if self.logger:
            self.logger.info(f"[BACKPACK] Connecting account stream for {self.symbol}")

        self._account_ws = await self._connect_via_proxy(self.ws_url)
        await self._subscribe_account_stream()

    async def _subscribe_account_stream(self) -> None:
        if not self._account_ws or not self.symbol:
            return

        timestamp = int(time.time() * 1000)
        signature = self._generate_signature("subscribe", timestamp)

        message = {
            "method": "SUBSCRIBE",
            "params": [f"account.orderUpdate.{self.symbol}"],
            "signature": [
                self.public_key,
                signature,
                str(timestamp),
                "5000",
            ],
        }

        await self._account_ws.send(json.dumps(message))
        if self.logger:
            self.logger.info(f"[BACKPACK] Subscribed to account.orderUpdate.{self.symbol}")

    async def _listen_account_ws(self) -> None:
        assert self._account_ws is not None
        try:
            async for message in self._account_ws:
                if not self.running:
                    break
                await self._handle_account_message(message)
        except websockets.exceptions.ConnectionClosed:
            if self.logger:
                self.logger.warning("[BACKPACK] Account stream closed")

    async def _handle_account_message(self, message: str) -> None:
        try:
            data = json.loads(message)
        except json.JSONDecodeError as exc:
            if self.logger:
                self.logger.error(f"[BACKPACK] Failed to decode account message: {exc}")
            return

        stream = data.get("stream", "")
        payload = data.get("data", {})

        if "orderUpdate" in stream:
            await self._handle_order_update(payload)
        elif self.logger:
            self.logger.debug(f"[BACKPACK] Ignoring account stream message: {data}")

    async def _handle_order_update(self, payload: Dict[str, Any]) -> None:
        if not self.order_update_callback:
            return
        try:
            await self.order_update_callback(payload)
        except Exception as exc:  # pragma: no cover - callback safety
            if self.logger:
                self.logger.error(f"[BACKPACK] Order update callback failed: {exc}")

        # Since the liquidation event is a part of orderUpdate
        await self._maybe_dispatch_liquidation(payload)

    async def _maybe_dispatch_liquidation(self, payload: Dict[str, Any]) -> None:
        if not self.liquidation_callback:
            return

        origin = (payload.get("O") or "").upper()
        event_type = (payload.get("e") or "").lower()
        if origin not in {
            "LIQUIDATION_AUTOCLOSE",
            "ADL_AUTOCLOSE",
            "BACKSTOP_LIQUIDITY_PROVIDER",
        }:
            return
        if event_type != "orderfill":
            return

        last_fill = payload.get("l")
        executed = payload.get("z")
        try:
            last_qty = Decimal(str(last_fill)) if last_fill is not None else Decimal("0")
        except (InvalidOperation, TypeError):
            last_qty = Decimal("0")
        if last_qty <= 0:
            try:
                exec_qty = Decimal(str(executed)) if executed is not None else Decimal("0")
            except (InvalidOperation, TypeError):
                exec_qty = Decimal("0")
            if exec_qty <= 0:
                return
        try:
            await self.liquidation_callback(payload)
        except Exception as exc:  # pragma: no cover - callback safety
            if self.logger:
                self.logger.error(f"[BACKPACK] Liquidation callback failed: {exc}")

    async def _close_account_ws(self) -> None:
        if self._account_ws:
            try:
                await self._account_ws.close()
            except Exception:
                pass
            finally:
                self._account_ws = None

    # ------------------------------------------------------------------ #
    # Depth (public) stream management
    # ------------------------------------------------------------------ #

    async def _run_depth_stream(self) -> None:
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

                await self._connect_depth_ws()
                backoff_seconds = 1.0
                await self._listen_depth_ws()
            except asyncio.CancelledError:
                break
            except Exception as exc:  # pragma: no cover - defensive
                if self.logger:
                    self.logger.error(f"[BACKPACK] Depth WS error: {exc}")
                await asyncio.sleep(min(backoff_seconds, self._MAX_BACKOFF_SECONDS))
                backoff_seconds = min(backoff_seconds * 2, self._MAX_BACKOFF_SECONDS)
            finally:
                await self._close_depth_ws()

        self._depth_ready_event.clear()
        self.order_book_ready = False

    async def _connect_depth_ws(self) -> None:
        if self.logger:
            self.logger.info(f"[BACKPACK] Connecting depth stream for {self.symbol}")

        self._depth_ws = await self._connect_via_proxy(self.ws_url)
        await self._subscribe_depth_stream()

    async def _subscribe_depth_stream(self) -> None:
        if not self._depth_ws or not self.symbol:
            return

        streams = [self._depth_stream_name(), f"bookTicker.{self.symbol}"]
        message = {
            "method": "SUBSCRIBE",
            "params": streams,
        }

        await self._depth_ws.send(json.dumps(message))
        if self.logger:
            self.logger.info(f"[BACKPACK] Subscribed to streams: {streams}")

    async def _listen_depth_ws(self) -> None:
        assert self._depth_ws is not None
        try:
            async for message in self._depth_ws:
                if not self.running:
                    break
                self._handle_depth_message(message)
        except websockets.exceptions.ConnectionClosed:
            if self.logger:
                self.logger.warning("[BACKPACK] Depth stream closed")

    def _handle_depth_message(self, message: str) -> None:
        try:
            data = json.loads(message)
        except json.JSONDecodeError as exc:
            if self.logger:
                self.logger.error(f"[BACKPACK] Failed to decode depth message: {exc}")
            return

        stream = data.get("stream", "")
        payload = data.get("data", {})

        if stream.startswith("depth"):
            self._apply_depth_update(payload)
        elif stream.startswith("bookTicker"):
            self._apply_book_ticker(payload)
        elif self.logger:
            self.logger.debug(f"[BACKPACK] Ignoring depth stream message: {data}")

    def _apply_book_ticker(self, payload: Dict[str, Any]) -> None:
        try:
            bid = Decimal(str(payload.get("b")))
            ask = Decimal(str(payload.get("a")))
            self.best_bid = bid
            self.best_ask = ask
        except (InvalidOperation, TypeError):
            return

    def _apply_depth_update(self, payload: Dict[str, Any]) -> None:
        if not payload or payload.get("e") != "depth":
            return
        if self.symbol and payload.get("s") and payload["s"] != self.symbol:
            return

        first_update = self._to_int(payload.get("U"))
        final_update = self._to_int(payload.get("u"))

        if self._last_update_id is not None and first_update is not None:
            if final_update is not None and final_update <= self._last_update_id:
                return
            if first_update > self._last_update_id + 1:
                asyncio.create_task(self._reload_depth_snapshot())
                return

        self._apply_depth_side("bids", payload.get("b", []))
        self._apply_depth_side("asks", payload.get("a", []))

        if final_update is not None:
            self._last_update_id = final_update

        self._rebuild_order_book()
        self.order_book_ready = True
        self._depth_ready_event.set()

    def _apply_depth_side(self, side: str, updates: List[List[str]]) -> None:
        if side not in self._order_levels:
            return
        levels = self._order_levels[side]
        for price_str, size_str in updates:
            try:
                price = Decimal(str(price_str))
                size = Decimal(str(size_str))
            except (InvalidOperation, TypeError):
                continue

            if size <= 0:
                levels.pop(price, None)
            else:
                levels[price] = size

    async def _reload_depth_snapshot(self) -> None:
        async with self._depth_reload_lock:
            self.order_book_ready = False
            await self._load_initial_depth()

    async def _load_initial_depth(self) -> bool:
        if not self.depth_fetcher or not self.symbol:
            return False

        try:
            snapshot = await asyncio.to_thread(self.depth_fetcher, self.symbol)
        except Exception as exc:
            if self.logger:
                self.logger.error(f"[BACKPACK] Failed to fetch depth snapshot: {exc}")
            return False

        bids = snapshot.get("bids") or []
        asks = snapshot.get("asks") or []

        self._order_levels["bids"].clear()
        self._order_levels["asks"].clear()

        for price_str, size_str in bids:
            try:
                price = Decimal(str(price_str))
                size = Decimal(str(size_str))
            except (InvalidOperation, TypeError):
                continue
            if size > 0:
                self._order_levels["bids"][price] = size

        for price_str, size_str in asks:
            try:
                price = Decimal(str(price_str))
                size = Decimal(str(size_str))
            except (InvalidOperation, TypeError):
                continue
            if size > 0:
                self._order_levels["asks"][price] = size

        last_update_raw = snapshot.get("lastUpdateId") or snapshot.get("u")
        self._last_update_id = self._to_int(last_update_raw)
        self._rebuild_order_book()
        self.order_book_ready = True
        self._depth_ready_event.set()
        return True

    def _rebuild_order_book(self) -> None:
        bids_sorted = sorted(self._order_levels["bids"].items(), key=lambda kv: kv[0], reverse=True)
        asks_sorted = sorted(self._order_levels["asks"].items(), key=lambda kv: kv[0])

        self.order_book["bids"] = [{"price": price, "size": size} for price, size in bids_sorted]
        self.order_book["asks"] = [{"price": price, "size": size} for price, size in asks_sorted]

        previous_bid = self.best_bid
        previous_ask = self.best_ask
        self.best_bid = bids_sorted[0][0] if bids_sorted else None
        self.best_ask = asks_sorted[0][0] if asks_sorted else None

        if (
            self.best_bid is not None
            and self.best_ask is not None
            and (self.best_bid != previous_bid or self.best_ask != previous_ask)
        ):
            asyncio.create_task(
                self._notify_bbo_update(
                    BBOData(
                        symbol=self.symbol or "",
                        bid=self.best_bid,
                        ask=self.best_ask,
                        timestamp=time.time(),
                        sequence=self._last_update_id,
                    )
                )
            )

    async def _close_depth_ws(self) -> None:
        if self._depth_ws:
            try:
                await self._depth_ws.close()
            except Exception:
                pass
            finally:
                self._depth_ws = None

    def _depth_stream_name(self) -> str:
        if self.depth_stream_interval == "realtime" or not self.depth_stream_interval:
            prefix = "depth"
        else:
            prefix = f"depth.{self.depth_stream_interval}"
        return f"{prefix}.{self.symbol}"

    @staticmethod
    def _to_int(value: Any) -> Optional[int]:
        if value is None:
            return None
        try:
            return int(str(value))
        except (TypeError, ValueError):
            return None

    # ------------------------------------------------------------------ #
    # Utilities
    # ------------------------------------------------------------------ #

    def _generate_signature(self, instruction: str, timestamp: int, window: int = 5000) -> str:
        message = f"instruction={instruction}&timestamp={timestamp}&window={window}"
        signature_bytes = self.private_key.sign(message.encode())
        return base64.b64encode(signature_bytes).decode()
