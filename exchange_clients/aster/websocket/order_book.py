"""
Order book state management for Aster WebSocket.

Handles order book updates, BBO extraction, and state management.
"""

import time
from decimal import Decimal
from typing import Dict, Any, List, Optional, Callable

from exchange_clients.base_websocket import BBOData


class AsterOrderBook:
    """Manages order book state and BBO tracking."""

    def __init__(self, logger: Optional[Any] = None):
        """
        Initialize order book manager.
        
        Args:
            logger: Logger instance
        """
        self.logger = logger
        
        # Order book state (from depth stream)
        self.order_book = {"bids": [], "asks": []}  # Snapshot format: [{'price': Decimal, 'size': Decimal}, ...]
        self.order_book_ready = False
        
        # BBO state (from book ticker stream)
        self.best_bid: Optional[float] = None
        self.best_ask: Optional[float] = None

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

    def reset_order_book(self):
        """Reset order book state."""
        self.order_book = {"bids": [], "asks": []}
        self.order_book_ready = False

    def update_order_book_from_depth(self, bids: List[Dict[str, Decimal]], asks: List[Dict[str, Decimal]]):
        """
        Update order book state from depth stream snapshot.
        
        Args:
            bids: List of bid levels [{'price': Decimal, 'size': Decimal}, ...]
            asks: List of ask levels [{'price': Decimal, 'size': Decimal}, ...]
        """
        self.order_book = {
            'bids': bids,
            'asks': asks
        }
        self.order_book_ready = True
        
        # Extract BBO from depth stream (ensures freshness even if book ticker hasn't updated)
        if bids:
            self.best_bid = float(bids[0]['price'])  # Already sorted, first is best
        if asks:
            self.best_ask = float(asks[0]['price'])  # Already sorted, first is best

    def update_bbo_from_book_ticker(self, best_bid: float, best_ask: float):
        """
        Update BBO from book ticker stream.
        
        Args:
            best_bid: Best bid price
            best_ask: Best ask price
        """
        self.best_bid = best_bid
        self.best_ask = best_ask

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
            self._log(f"Error formatting order book: {e}", "ERROR")
            return None

    async def handle_depth_update(self, data: Dict[str, Any], notify_bbo_fn: Optional[Callable] = None):
        """
        Handle order book depth updates from depth stream.
        
        Format (partial depth):
        {
          "e": "depthUpdate",
          "E": 1571889248277,  // Event time
          "s": "BTCUSDT",
          "b": [["7403.89", "0.002"], ["7403.90", "3.906"], ...],  // Top 20 bids
          "a": [["7405.96", "3.340"], ["7406.63", "4.525"], ...]   // Top 20 asks
        }
        
        Args:
            data: Raw depth update message
            notify_bbo_fn: Optional function to notify BBO updates
        """
        try:
            if data.get('e') != 'depthUpdate':
                return
            
            # Extract bids and asks
            bids_raw = data.get('b', [])
            asks_raw = data.get('a', [])
            
            # Convert to standard format
            bids = [
                {'price': Decimal(price), 'size': Decimal(qty)}
                for price, qty in bids_raw
            ]
            asks = [
                {'price': Decimal(price), 'size': Decimal(qty)}
                for price, qty in asks_raw
            ]
            
            # Update order book state (snapshot, not incremental)
            self.update_order_book_from_depth(bids, asks)
            
            # Notify BBO update if callback provided
            if notify_bbo_fn and self.best_bid and self.best_ask:
                symbol = data.get('s', '')
                await notify_bbo_fn(
                    BBOData(
                        symbol=symbol,
                        bid=self.best_bid,
                        ask=self.best_ask,
                        timestamp=time.time(),
                        sequence=data.get('u'),
                    )
                )
            
        except Exception as e:
            self._log(f"Error processing depth update: {e}", "ERROR")

    async def handle_book_ticker(self, data: Dict[str, Any], notify_bbo_fn: Optional[Callable] = None):
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
        
        Args:
            data: Raw book ticker message
            notify_bbo_fn: Optional function to notify BBO updates
        """
        try:
            if data.get('e') != 'bookTicker':
                return
            
            # Extract symbol, best bid and ask
            symbol = data.get('s', '')
            best_bid_str = data.get('b')
            best_ask_str = data.get('a')
            
            if best_bid_str and best_ask_str:
                best_bid = float(best_bid_str)
                best_ask = float(best_ask_str)
                
                self.update_bbo_from_book_ticker(best_bid, best_ask)
                
                # Notify BBO update if callback provided
                if notify_bbo_fn:
                    await notify_bbo_fn(
                        BBOData(
                            symbol=symbol,
                            bid=best_bid,
                            ask=best_ask,
                            timestamp=time.time(),
                            sequence=data.get('u'),
                        )
                    )
        
        except Exception as e:
            self._log(f"Error processing book ticker: {e}", "ERROR")

