"""
Order book state management for Paradex WebSocket.

Handles order book updates, validation, BBO extraction, and state management.
"""

import asyncio
import time
from typing import Dict, Any, List, Optional, Tuple
from decimal import Decimal

from exchange_clients.paradex.client.utils.helpers import to_decimal


class ParadexOrderBook:
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
        self.best_bid: Optional[Decimal] = None
        self.best_ask: Optional[Decimal] = None
        self.snapshot_loaded = False
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

    def update_order_book(self, market: str, data: Dict[str, Any]) -> None:
        """
        Update the order book with new data from WebSocket.
        
        Paradex sends order book updates with 'deletes', 'inserts', and 'updates' arrays.
        
        Args:
            market: Market symbol (e.g., "BTC-USD-PERP")
            data: Order book update data from WebSocket
        """
        try:
            update_type = data.get('update_type')
            deletes = data.get('deletes', [])
            inserts = data.get('inserts', [])
            updates = data.get('updates', [])
            
            # If snapshot (update_type == 's'), clear existing state first
            if update_type == 's':
                self.order_book['bids'].clear()
                self.order_book['asks'].clear()
                self._log(f"[PARADEX] Order book snapshot received for {market}, clearing old state", "DEBUG")
            
            # Process deletes
            for delete_item in deletes:
                side = delete_item.get('side', '').upper()
                price = to_decimal(delete_item.get('price'))
                if side == 'BUY' and price:
                    self.order_book['bids'].pop(float(price), None)
                elif side == 'SELL' and price:
                    self.order_book['asks'].pop(float(price), None)
            
            # Process inserts
            for insert_item in inserts:
                side = insert_item.get('side', '').upper()
                price = to_decimal(insert_item.get('price'))
                size = to_decimal(insert_item.get('size'))
                if side == 'BUY' and price and size and size > 0:
                    self.order_book['bids'][float(price)] = float(size)
                elif side == 'SELL' and price and size and size > 0:
                    self.order_book['asks'][float(price)] = float(size)
            
            # Process updates
            for update_item in updates:
                side = update_item.get('side', '').upper()
                price = to_decimal(update_item.get('price'))
                size = to_decimal(update_item.get('size'))
                if side == 'BUY' and price and size:
                    if size > 0:
                        self.order_book['bids'][float(price)] = float(size)
                    else:
                        # Size 0 means remove this level
                        self.order_book['bids'].pop(float(price), None)
                elif side == 'SELL' and price and size:
                    if size > 0:
                        self.order_book['asks'][float(price)] = float(size)
                    else:
                        # Size 0 means remove this level
                        self.order_book['asks'].pop(float(price), None)
            
            # Update best bid/ask
            if self.order_book['bids']:
                self.best_bid = Decimal(str(max(self.order_book['bids'].keys())))
            if self.order_book['asks']:
                self.best_ask = Decimal(str(min(self.order_book['asks'].keys())))
            
            # Mark as ready after first snapshot or when we have data
            if update_type == 's' or (not self.snapshot_loaded and (self.order_book['bids'] or self.order_book['asks'])):
                self.snapshot_loaded = True
                self.order_book_ready = True
                self._log(
                    f"[PARADEX] Order book ready for {market}: "
                    f"{len(self.order_book['bids'])} bids, {len(self.order_book['asks'])} asks",
                    "INFO"
                )
            
            # Update timestamp
            self.last_update_timestamp = time.time()
            
        except Exception as e:
            self._log(f"Error updating order book: {e}", "ERROR")

    def reset_order_book(self) -> None:
        """Reset order book state (called when switching markets or reconnecting)."""
        self.order_book = {"bids": {}, "asks": {}}
        self.best_bid = None
        self.best_ask = None
        self.snapshot_loaded = False
        self.order_book_ready = False
        self.last_update_timestamp = None

    def get_order_book(self, levels: Optional[int] = None) -> Optional[Dict[str, List[Dict[str, Decimal]]]]:
        """
        Get formatted order book with optional level limiting.
        
        Args:
            levels: Optional number of levels to return per side
            
        Returns:
            Order book dict with 'bids' and 'asks' lists, or None if not ready.
            Format: {'bids': [{'price': Decimal, 'size': Decimal}, ...], 
                     'asks': [{'price': Decimal, 'size': Decimal}, ...]}
        """
        if not self.order_book_ready:
            return None
        
        try:
            bids = []
            asks = []
            
            # Sort bids descending (highest first)
            sorted_bids = sorted(self.order_book['bids'].items(), key=lambda x: x[0], reverse=True)
            if levels:
                sorted_bids = sorted_bids[:levels]
            
            for price, size in sorted_bids:
                bids.append({'price': Decimal(str(price)), 'size': Decimal(str(size))})
            
            # Sort asks ascending (lowest first)
            sorted_asks = sorted(self.order_book['asks'].items(), key=lambda x: x[0])
            if levels:
                sorted_asks = sorted_asks[:levels]
            
            for price, size in sorted_asks:
                asks.append({'price': Decimal(str(price)), 'size': Decimal(str(size))})
            
            return {'bids': bids, 'asks': asks}
            
        except Exception as e:
            self._log(f"Error getting order book: {e}", "ERROR")
            return None

    def get_best_levels(
        self, min_size_usd: float = 0
    ) -> Tuple[Tuple[Optional[Decimal], Optional[Decimal]], Tuple[Optional[Decimal], Optional[Decimal]]]:
        """
        Get the best bid and ask levels from order book.
        
        Args:
            min_size_usd: Minimum size in USD (not used for Paradex, kept for compatibility)
            
        Returns:
            Tuple of ((best_bid_price, best_bid_size), (best_ask_price, best_ask_size))
        """
        if not self.order_book_ready:
            return ((None, None), (None, None))
        
        best_bid_price = self.best_bid
        best_ask_price = self.best_ask
        
        best_bid_size = None
        best_ask_size = None
        
        if best_bid_price:
            best_bid_size = Decimal(str(self.order_book['bids'].get(float(best_bid_price), 0)))
        if best_ask_price:
            best_ask_size = Decimal(str(self.order_book['asks'].get(float(best_ask_price), 0)))
        
        return ((best_bid_price, best_bid_size), (best_ask_price, best_ask_size))

    def is_stale(self) -> bool:
        """Check if order book is stale (no updates for threshold time)."""
        if self.last_update_timestamp is None:
            return True
        
        elapsed = time.time() - self.last_update_timestamp
        return elapsed > self.STALENESS_THRESHOLD_SECONDS

    def needs_reconnect(self) -> bool:
        """Check if order book needs reconnect (no updates for reconnect threshold time)."""
        if self.last_update_timestamp is None:
            return True
        
        elapsed = time.time() - self.last_update_timestamp
        return elapsed > self.RECONNECT_THRESHOLD_SECONDS

