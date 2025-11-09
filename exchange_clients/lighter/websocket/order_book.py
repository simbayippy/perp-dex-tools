"""
Order book state management for Lighter WebSocket.

Handles order book updates, validation, BBO extraction, and state management.
"""

import asyncio
import time
from typing import Dict, Any, List, Optional, Tuple
from decimal import Decimal


class LighterOrderBook:
    """Manages order book state and validation."""

    # Staleness threshold: if no updates for 60 seconds, consider order book stale
    STALENESS_THRESHOLD_SECONDS = 60.0
    # Reconnect threshold: if no updates for 180 seconds (3 minutes), trigger full reconnect
    RECONNECT_THRESHOLD_SECONDS = 180.0

    def __init__(self, logger: Optional[Any] = None):
        """
        Initialize order book manager.
        
        Args:
            logger: Logger instance
        """
        self.logger = logger
        
        # Order book state
        self.order_book = {"bids": {}, "asks": {}}
        self.best_bid: Optional[float] = None
        self.best_ask: Optional[float] = None
        self.snapshot_loaded = False
        self.order_book_offset: Optional[int] = None
        self.order_book_sequence_gap = False
        self.order_book_lock = asyncio.Lock()
        self.order_book_ready = False
        
        # Track last update time to detect staleness
        self.last_update_timestamp: Optional[float] = None

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

        # Track update timestamp (only if we actually process updates)
        has_valid_updates = False
        
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
                
                has_valid_updates = True
            except (KeyError, ValueError, TypeError) as e:
                self._log(f"Error processing order book update: {e}, update: {update}", "ERROR")
                continue
        
        # Update timestamp only if we processed valid updates
        if has_valid_updates:
            self.last_update_timestamp = time.time()

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
            self._log(
                f"Order book sequence gap detected! Expected offset {expected_offset}, got {new_offset}",
                "WARNING"
            )
            self.order_book_sequence_gap = True
            return False
        else:
            # Out of order or duplicate update
            self._log(
                f"Out of order update received! Expected offset {expected_offset}, got {new_offset}",
                "WARNING"
            )
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
                self._log(
                    f"Order book inconsistency detected! Best bid: {best_bid}, Best ask: {best_ask}",
                    "WARNING"
                )
                return False

            return True
        except (ValueError, KeyError) as e:
            self._log(f"Error validating order book integrity: {e}", "ERROR")
            return False

    def get_best_levels(
        self, min_size_usd: float = 0
    ) -> Tuple[Tuple[Optional[float], Optional[float]], Tuple[Optional[float], Optional[float]]]:
        """
        Get the best bid and ask levels from order book.
        
        Args:
            min_size_usd: Minimum size in USD (default: 0 = no filter, return true best bid/ask)
        
        Returns:
            ((best_bid_price, best_bid_size), (best_ask_price, best_ask_size))
        """
        try:
            # Get all bid levels with sufficient size
            bid_levels = [
                (price, size)
                for price, size in self.order_book["bids"].items()
                if size * price >= min_size_usd
            ]

            # Get all ask levels with sufficient size
            ask_levels = [
                (price, size)
                for price, size in self.order_book["asks"].items()
                if size * price >= min_size_usd
            ]

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

    def get_staleness_seconds(self) -> Optional[float]:
        """
        Get how many seconds since last order book update.
        
        Returns:
            Seconds since last update, or None if never updated
        """
        if self.last_update_timestamp is None:
            return None
        return time.time() - self.last_update_timestamp
    
    def is_stale(self) -> bool:
        """
        Check if order book is stale (no updates for threshold period).
        
        Returns:
            True if order book is stale, False otherwise
        """
        if not self.snapshot_loaded:
            return True  # Not loaded yet, consider stale
        
        staleness_seconds = self.get_staleness_seconds()
        if staleness_seconds is None:
            # Snapshot loaded but no updates received yet - not stale if just loaded
            return False
        
        return staleness_seconds > self.STALENESS_THRESHOLD_SECONDS
    
    def needs_reconnect(self) -> bool:
        """
        Check if order book is stale enough to warrant a full websocket reconnect.
        
        Returns:
            True if order book needs reconnect (stale for > RECONNECT_THRESHOLD_SECONDS), False otherwise
        """
        if not self.snapshot_loaded:
            return False  # Not loaded yet, don't reconnect (will reconnect on connection failure)
        
        staleness_seconds = self.get_staleness_seconds()
        if staleness_seconds is None:
            return False  # Just loaded, don't reconnect
        
        return staleness_seconds > self.RECONNECT_THRESHOLD_SECONDS

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
            self.last_update_timestamp = None

