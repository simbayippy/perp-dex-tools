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

    def update_order_book(self, market: str, data: Dict[str, Any]) -> None:
        """
        Update the order book with new data from WebSocket.
        
        Paradex sends order book updates with 'deletes', 'inserts', and 'updates' arrays.
        Alternatively, it may send 'bids' and 'asks' arrays directly.
        
        IMPORTANT: When using price_tick parameter (e.g., "0_1"), Paradex groups prices
        into tick buckets. The prices in inserts/updates/deletes represent tick levels,
        not exact prices. For exact BBO prices, use the BBO stream instead.
        
        Args:
            market: Market symbol (e.g., "BTC-USD-PERP")
            data: Order book update data from WebSocket
        """
        try:
            # Debug: Log raw data structure to understand format
            if self.logger:
                self.logger.debug(
                    f"[PARADEX] 🔍 Raw order book data keys: {list(data.keys())} | "
                    f"update_type={data.get('update_type')} | "
                    f"has_deletes={bool(data.get('deletes'))} | "
                    f"has_inserts={bool(data.get('inserts'))} | "
                    f"has_updates={bool(data.get('updates'))} | "
                    f"has_bids={bool(data.get('bids'))} | "
                    f"has_asks={bool(data.get('asks'))}"
                )
            
            update_type = data.get('update_type')
            deletes = data.get('deletes', [])
            inserts = data.get('inserts', [])
            updates = data.get('updates', [])
            
            # Check if Paradex sends bids/asks directly (alternative format)
            bids_raw = data.get('bids', [])
            asks_raw = data.get('asks', [])
            
            # If snapshot (update_type == 's'), clear existing state first
            if update_type == 's':
                self.order_book['bids'].clear()
                self.order_book['asks'].clear()
                if self.logger:
                    self.logger.debug(f"[PARADEX] Order book snapshot received for {market}, clearing old state")
            
            # Handle direct bids/asks format (if present)
            if bids_raw or asks_raw:
                if self.logger:
                    self.logger.debug(
                        f"[PARADEX] Processing direct bids/asks format: "
                        f"{len(bids_raw)} bids, {len(asks_raw)} asks"
                    )
                
                # Process bids
                for bid_item in bids_raw:
                    if isinstance(bid_item, (list, tuple)) and len(bid_item) >= 2:
                        # Format: [price, size]
                        price = to_decimal(bid_item[0])
                        size = to_decimal(bid_item[1])
                        if price and size and size > 0:
                            self.order_book['bids'][float(price)] = float(size)
                    elif isinstance(bid_item, dict):
                        # Format: {'price': ..., 'size': ...}
                        price = to_decimal(bid_item.get('price'))
                        size = to_decimal(bid_item.get('size'))
                        if price and size and size > 0:
                            self.order_book['bids'][float(price)] = float(size)
                
                # Process asks
                for ask_item in asks_raw:
                    if isinstance(ask_item, (list, tuple)) and len(ask_item) >= 2:
                        # Format: [price, size]
                        price = to_decimal(ask_item[0])
                        size = to_decimal(ask_item[1])
                        if price and size and size > 0:
                            self.order_book['asks'][float(price)] = float(size)
                    elif isinstance(ask_item, dict):
                        # Format: {'price': ..., 'size': ...}
                        price = to_decimal(ask_item.get('price'))
                        size = to_decimal(ask_item.get('size'))
                        if price and size and size > 0:
                            self.order_book['asks'][float(price)] = float(size)
            
            # Process deletes
            for delete_item in deletes:
                side = delete_item.get('side', '')
                if isinstance(side, str):
                    side = side.upper()
                elif side == 1 or side == '1':
                    side = 'BUY'
                elif side == 2 or side == '2':
                    side = 'SELL'
                else:
                    side = str(side).upper()
                
                price = to_decimal(delete_item.get('price'))
                if side == 'BUY' and price:
                    self.order_book['bids'].pop(float(price), None)
                elif side == 'SELL' and price:
                    self.order_book['asks'].pop(float(price), None)
            
            # Process inserts
            inserts_processed = 0
            for insert_item in inserts:
                side = insert_item.get('side', '')
                if isinstance(side, str):
                    side = side.upper()
                elif side == 1 or side == '1':
                    side = 'BUY'
                elif side == 2 or side == '2':
                    side = 'SELL'
                else:
                    side = str(side).upper()
                
                price = to_decimal(insert_item.get('price'))
                size = to_decimal(insert_item.get('size'))
                
                # Debug: Log first few inserts to see format
                if inserts_processed < 3 and self.logger:
                    self.logger.debug(
                        f"[PARADEX] 🔍 Insert item {inserts_processed}: "
                        f"side={insert_item.get('side')} -> {side}, "
                        f"price={insert_item.get('price')} -> {price}, "
                        f"size={insert_item.get('size')} -> {size}"
                    )
                
                if side == 'BUY' and price and size and size > 0:
                    self.order_book['bids'][float(price)] = float(size)
                    inserts_processed += 1
                elif side == 'SELL' and price and size and size > 0:
                    self.order_book['asks'][float(price)] = float(size)
                    inserts_processed += 1
            
            if inserts_processed > 0 and self.logger:
                # Log sample prices to validate
                sample_bids = list(self.order_book['bids'].keys())[:3] if self.order_book['bids'] else []
                sample_asks = list(self.order_book['asks'].keys())[:3] if self.order_book['asks'] else []
                self.logger.debug(
                    f"[PARADEX] Processed {inserts_processed} inserts: "
                    f"{len(self.order_book['bids'])} bids, {len(self.order_book['asks'])} asks | "
                    f"Sample bid prices: {sample_bids} | Sample ask prices: {sample_asks}"
                )
            
            # Process updates
            for update_item in updates:
                side = update_item.get('side', '')
                if isinstance(side, str):
                    side = side.upper()
                elif side == 1 or side == '1':
                    side = 'BUY'
                elif side == 2 or side == '2':
                    side = 'SELL'
                else:
                    side = str(side).upper()
                
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
            
            # Log best prices for validation
            if self.best_bid and self.best_ask and self.logger:
                spread_bps = ((self.best_ask - self.best_bid) / self.best_bid * 10000) if self.best_bid > 0 else 0
                self.logger.debug(
                    f"[PARADEX] Best prices: bid={self.best_bid}, ask={self.best_ask}, "
                    f"spread={spread_bps:.0f} bps"
                )
            
            # Mark as ready after first snapshot or when we have data
            if update_type == 's' or (not self.snapshot_loaded and (self.order_book['bids'] or self.order_book['asks'])):
                self.snapshot_loaded = True
                self.order_book_ready = True
                best_bid_str = str(self.best_bid) if self.best_bid else "N/A"
                best_ask_str = str(self.best_ask) if self.best_ask else "N/A"
            
            # Update timestamp
            self.last_update_timestamp = time.time()
            
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error updating order book: {e}")

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
            if self.logger:
                self.logger.error(f"Error getting order book: {e}")
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

