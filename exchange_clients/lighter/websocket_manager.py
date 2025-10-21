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
import time
from typing import Dict, Any, List, Optional, Tuple, Callable, Awaitable
import websockets

from exchange_clients.base_websocket import BaseWebSocketManager


class LighterWebSocketManager(BaseWebSocketManager):
    """WebSocket manager for Lighter order updates and order book."""

    def __init__(
        self,
        config: Dict[str, Any],
        order_update_callback: Optional[Callable] = None,
        liquidation_callback: Optional[Callable[[List[Dict[str, Any]]], Awaitable[None]]] = None,
    ):
        self.config = config
        self.order_update_callback = order_update_callback
        self.liquidation_callback = liquidation_callback
        super().__init__()
        self.ws = None
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
        """Ensure the order book stream targets the requested symbol."""
        if symbol is None:
            return

        market_id = getattr(self.config, "contract_id", None)
        if market_id is None:
            self._log(
                f"[LIGHTER] No contract_id set on config; cannot align websocket for {symbol}",
                "DEBUG",
            )
            return

        try:
            target_market = int(market_id)

            if not self.ws or not self.running:
                self._log(f"Cannot switch market: WebSocket not connected", "WARNING")
                return
            
            if self.market_index == target_market:
                self._log(f"Already subscribed to market {target_market}", "DEBUG")
                return
            
            self._log(f"[LIGHTER] 🔄 Switching order book from market {self.market_index} to {target_market}", "INFO")
            
            # Unsubscribe from current market order book
            unsubscribe_msg = json.dumps({
                "type": "unsubscribe",
                "channel": f"order_book/{self.market_index}"
            })
            await self.ws.send(unsubscribe_msg)

            # Unsubscribe from current account orders channel
            account_unsub_msg = json.dumps({
                "type": "unsubscribe",
                "channel": f"account_orders/{self.market_index}/{self.account_index}"
            })
            await self.ws.send(account_unsub_msg)
            
            old_market_id = self.market_index
            self.market_index = target_market
            
            await self.reset_order_book()
            
            subscribe_msg = json.dumps({
                "type": "subscribe",
                "channel": f"order_book/{target_market}"
            })
            await self.ws.send(subscribe_msg)

            auth_token = None
            if self.lighter_client:
                try:
                    expiry = int(time.time() + 10 * 60)
                    auth_token, err = self.lighter_client.create_auth_token_with_expiry(expiry)
                    if err:
                        self._log(f"Failed to create auth token for market switch: {err}", "WARNING")
                except Exception as exc:
                    self._log(f"Error creating auth token for market switch: {exc}", "ERROR")

            # Subscribe to account orders for new market
            account_sub_msg = {
                "type": "subscribe",
                "channel": f"account_orders/{target_market}/{self.account_index}",
            }
            if auth_token:
                account_sub_msg["auth"] = auth_token
            await self.ws.send(json.dumps(account_sub_msg))
            
            self._log(f"[LIGHTER] ✅ Switched order book from market {old_market_id} to {target_market}", "INFO")
            
            await asyncio.sleep(0.5)
        except Exception as exc:
            self._log(f"Error switching market: {exc}", "ERROR")

    async def request_fresh_snapshot(self):
        """Request a fresh order book snapshot when we detect inconsistencies."""
        try:
            if not self.ws:
                return

            # Unsubscribe and resubscribe to get a fresh snapshot
            unsubscribe_msg = json.dumps({"type": "unsubscribe", "channel": f"order_book/{self.market_index}"})
            await self.ws.send(unsubscribe_msg)

            # Wait a moment for the unsubscribe to process
            await asyncio.sleep(1)

            # Resubscribe to get a fresh snapshot
            subscribe_msg = json.dumps({"type": "subscribe", "channel": f"order_book/{self.market_index}"})
            await self.ws.send(subscribe_msg)

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

    async def _subscribe_channels(self) -> None:
        """Subscribe to the required Lighter channels."""
        if not self.ws:
            raise RuntimeError("WebSocket connection not available")

        await self.ws.send(json.dumps({
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
            if self.liquidation_callback:
                subscription_messages.append({
                    "type": "subscribe",
                    "channel": f"notification/{self.account_index}",
                    "auth": auth_token,
                })
        else:
            if self.liquidation_callback:
                self._log("Skipping account order/notification subscriptions (no auth token)", "WARNING")

        for message in subscription_messages:
            await self.ws.send(json.dumps(message))

    async def _listen(self) -> None:
        """Listen for messages on the WebSocket connection."""
        cleanup_counter = 0
        try:
            while self.running and self.ws:
                try:
                    raw_message = await self.ws.recv()
                except websockets.exceptions.ConnectionClosed as exc:
                    self._log(f"Lighter websocket connection closed: {exc}", "WARNING")
                    break
                except Exception as exc:
                    self._log(f"Error receiving websocket message: {exc}", "ERROR")
                    break

                try:
                    data = json.loads(raw_message)
                except json.JSONDecodeError as exc:
                    self._log(f"JSON parsing error in Lighter websocket: {exc}", "ERROR")
                    continue

                notifications_for_dispatch: Optional[List[Dict[str, Any]]] = None
                request_snapshot = False

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
                        self._log(
                            f"[LIGHTER] Order book snapshot loaded with {len(self.order_book['bids'])} bids and "
                            f"{len(self.order_book['asks'])} asks",
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

                    elif data.get("type") == "ping":
                        await self.ws.send(json.dumps({"type": "pong"}))

                    elif data.get("type") == "update/account_orders":
                        orders = data.get("orders", {}).get(str(self.market_index), [])
                        self.handle_order_update(orders)

                    elif data.get("type") == "update/notification":
                        notifications_for_dispatch = data.get("notifs", [])

                    elif data.get("type") == "subscribed/notification":
                        self._log("Subscribed to notification channel", "DEBUG")

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

        finally:
            self.running = False
            self.order_book_ready = self.snapshot_loaded
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
            self.ws = await websockets.connect(self.ws_url)
            self._log("[LIGHTER] 🔗 Connected to websocket", "INFO")
        except Exception as exc:
            self._log(f"Failed to connect to Lighter websocket: {exc}", "ERROR")
            raise

        try:
            await self._subscribe_channels()
        except Exception as exc:
            await self.ws.close()
            self.ws = None
            self._log(f"Subscription failed: {exc}", "ERROR")
            raise

        self.running = True
        self._listener_task = asyncio.create_task(self._listen(), name="lighter-ws-listener")

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

        if self.ws:
            try:
                await self.ws.close()
            except Exception as exc:
                self._log(f"Error closing websocket: {exc}", "ERROR")
            finally:
                self.ws = None

        self._log("WebSocket disconnected", "INFO")
