"""
Order book state management for Backpack WebSocket.

Handles order book updates, validation, BBO extraction, and state management.
"""

import asyncio
import time
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

from exchange_clients.base_websocket import BBOData


class BackpackOrderBook:
    """Manages order book state and validation."""

    def __init__(self, logger: Optional[Any] = None):
        """
        Initialize order book manager.
        
        Args:
            logger: Logger instance
        """
        self.logger = logger
        
        # Order book state
        self.order_book: Dict[str, List[Dict[str, Decimal]]] = {"bids": [], "asks": []}
        self.best_bid: Optional[Decimal] = None
        self.best_ask: Optional[Decimal] = None
        self.order_book_ready: bool = False
        
        # Internal order book representation keyed by price
        self._order_levels: Dict[str, Dict[Decimal, Decimal]] = {
            "bids": {},
            "asks": {},
        }
        self._last_update_id: Optional[int] = None
        self._depth_reload_lock = asyncio.Lock()

    def set_logger(self, logger):
        """Set the logger instance."""
        self.logger = logger

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

    def reset(self) -> None:
        """Reset order book state."""
        self.order_book_ready = False
        self.best_bid = None
        self.best_ask = None
        self.order_book = {"bids": [], "asks": []}
        self._order_levels = {"bids": {}, "asks": {}}
        self._last_update_id = None

    def apply_book_ticker(self, payload: Dict[str, Any]) -> None:
        """
        Apply book ticker update (BBO snapshot).
        
        Args:
            payload: Book ticker payload
        """
        try:
            bid = Decimal(str(payload.get("b")))
            ask = Decimal(str(payload.get("a")))
            self.best_bid = bid
            self.best_ask = ask
        except (InvalidOperation, TypeError):
            return

    def apply_depth_update(self, payload: Dict[str, Any], symbol: Optional[str] = None) -> bool:
        """
        Apply depth update to order book.
        
        Args:
            payload: Depth update payload
            symbol: Optional symbol to validate against
            
        Returns:
            True if update was applied, False if rejected
        """
        if not payload or payload.get("e") != "depth":
            return False
        if symbol and payload.get("s") and payload["s"] != symbol:
            return False

        first_update = self._to_int(payload.get("U"))
        final_update = self._to_int(payload.get("u"))

        if self._last_update_id is not None and first_update is not None:
            if final_update is not None and final_update <= self._last_update_id:
                return False  # Stale update
            if first_update > self._last_update_id + 1:
                # Gap detected - need to reload snapshot
                return False

        self._apply_depth_side("bids", payload.get("b", []))
        self._apply_depth_side("asks", payload.get("a", []))

        if final_update is not None:
            self._last_update_id = final_update

        self._rebuild_order_book()
        self.order_book_ready = True
        return True

    def _apply_depth_side(self, side: str, updates: List[List[str]]) -> None:
        """
        Apply depth updates for one side (bids or asks).
        
        Args:
            side: "bids" or "asks"
            updates: List of [price, size] pairs
        """
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

    def load_snapshot(self, snapshot: Dict[str, Any]) -> None:
        """
        Load initial order book snapshot.
        
        Args:
            snapshot: Depth snapshot dictionary
        """
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

    def _rebuild_order_book(self) -> Optional[BBOData]:
        """
        Rebuild formatted order book from internal levels.
        
        Returns:
            BBOData if BBO changed, None otherwise
        """
        bids_sorted = sorted(self._order_levels["bids"].items(), key=lambda kv: kv[0], reverse=True)
        asks_sorted = sorted(self._order_levels["asks"].items(), key=lambda kv: kv[0])

        self.order_book["bids"] = [{"price": price, "size": size} for price, size in bids_sorted]
        self.order_book["asks"] = [{"price": price, "size": size} for price, size in asks_sorted]

        previous_bid = self.best_bid
        previous_ask = self.best_ask
        self.best_bid = bids_sorted[0][0] if bids_sorted else None
        self.best_ask = asks_sorted[0][0] if asks_sorted else None

        # Return BBO data if changed (for notification)
        if (
            self.best_bid is not None
            and self.best_ask is not None
            and (self.best_bid != previous_bid or self.best_ask != previous_ask)
        ):
            return BBOData(
                symbol="",  # Will be set by caller
                bid=float(self.best_bid),
                ask=float(self.best_ask),
                timestamp=time.time(),
                sequence=self._last_update_id,
            )
        return None

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

    @staticmethod
    def _to_int(value: Any) -> Optional[int]:
        """Convert value to int safely."""
        if value is None:
            return None
        try:
            return int(str(value))
        except (TypeError, ValueError):
            return None

