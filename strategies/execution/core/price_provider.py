"""
Price Provider - Unified price fetching with intelligent caching.

Provides a clean abstraction over multiple data sources:
- Cached order book data (from recent liquidity checks)
- REST API snapshots (for fresh data)
- WebSocket streams (for real-time monitoring)

Key features:
- Cache-first architecture (reuse recent data)
- Automatic cache invalidation (time-based TTL)
- Fallback chain for reliability
- Exchange-agnostic interface
"""

from typing import Any, Dict, Optional, Tuple
from decimal import Decimal
from dataclasses import dataclass
from datetime import datetime, timedelta
from helpers.unified_logger import get_core_logger

logger = get_core_logger("price_provider")


@dataclass
class PriceData:
    """
    Cached price data with metadata.
    """
    best_bid: Decimal
    best_ask: Decimal
    mid_price: Decimal
    timestamp: datetime
    source: str  # "cache", "rest_api", "websocket"
    
    def age_seconds(self) -> float:
        """Get age of price data in seconds."""
        return (datetime.now() - self.timestamp).total_seconds()
    
    def is_valid(self, max_age_seconds: float = 5.0) -> bool:
        """Check if price data is still valid."""
        return self.age_seconds() < max_age_seconds


class PriceCache:
    """
    Simple in-memory cache for recent price data.
    
    Thread-safe, time-based invalidation.
    """
    
    def __init__(self, default_ttl_seconds: float = 5.0):
        """
        Initialize price cache.
        
        Args:
            default_ttl_seconds: Default time-to-live for cached prices
        """
        self.default_ttl = default_ttl_seconds
        self._cache: Dict[str, PriceData] = {}
    
    def get(self, key: str, max_age_seconds: Optional[float] = None) -> Optional[PriceData]:
        """
        Get cached price data if still valid.
        
        Args:
            key: Cache key (e.g., "lighter:BTC")
            max_age_seconds: Maximum age to accept (uses default if None)
        
        Returns:
            PriceData if cached and valid, None otherwise
        """
        max_age = max_age_seconds if max_age_seconds is not None else self.default_ttl
        
        if key not in self._cache:
            return None
        
        price_data = self._cache[key]
        
        if not price_data.is_valid(max_age):
            # Expired - remove from cache
            del self._cache[key]
            return None
        
        return price_data
    
    def set(self, key: str, price_data: PriceData) -> None:
        """
        Store price data in cache.
        
        Args:
            key: Cache key (e.g., "lighter:BTC")
            price_data: Price data to cache
        """
        self._cache[key] = price_data
    
    def invalidate(self, key: str) -> None:
        """
        Manually invalidate cached data.
        
        Args:
            key: Cache key to invalidate
        """
        if key in self._cache:
            del self._cache[key]
    
    def clear(self) -> None:
        """Clear all cached data."""
        self._cache.clear()


class PriceProvider:
    """
    Unified price provider with intelligent caching.
    
    Provides best bid/ask prices from the most appropriate source:
    1. Cache (from recent liquidity check) - PREFERRED
    2. REST API (fresh snapshot) - FALLBACK
    3. WebSocket (real-time, if available) - OPTIONAL
    
    Example:
        provider = PriceProvider()
        
        # During liquidity check, automatically caches prices
        order_book = await exchange_client.get_order_book_depth("BTC", levels=20)
        provider.cache_order_book(exchange_name, symbol, order_book)
        
        # Later, during order execution, reuses cached data
        bid, ask = await provider.get_bbo_prices(exchange_client, "BTC")
        # ↑ Returns cached data (no API call!) if < 5 seconds old
    """
    
    def __init__(
        self,
        cache_ttl_seconds: float = 5.0,
        prefer_websocket: bool = False
    ):
        """
        Initialize price provider.
        
        Args:
            cache_ttl_seconds: How long to consider cached prices valid (default: 5 seconds)
            prefer_websocket: If True, prefer WebSocket over cache (for HFT strategies)
        """
        self.cache = PriceCache(default_ttl_seconds=cache_ttl_seconds)
        self.prefer_websocket = prefer_websocket
        self.logger = get_core_logger("price_provider")
    
    def _make_cache_key(self, exchange_name: str, symbol: str) -> str:
        """Generate cache key for exchange + symbol."""
        return f"{exchange_name}:{symbol}"
    
    async def get_bbo_prices(
        self,
        exchange_client: Any,
        symbol: str,
        max_cache_age_seconds: Optional[float] = None
    ) -> Tuple[Decimal, Decimal]:
        """
        Get best bid/offer prices from best available source.
        
        Priority (unless prefer_websocket=True):
        1. Cache (if valid)
        2. REST API
        3. WebSocket (if available)
        
        Args:
            exchange_client: Exchange client instance
            symbol: Trading symbol
            max_cache_age_seconds: Maximum age to accept from cache
        
        Returns:
            (best_bid, best_ask) as Decimals
        """
        exchange_name = exchange_client.get_exchange_name()
        cache_key = self._make_cache_key(exchange_name, symbol)
        
        # Strategy 1: Try cache first (most common case)
        if not self.prefer_websocket:
            cached = self.cache.get(cache_key, max_cache_age_seconds)
            if cached:
                self.logger.info(
                    f"✅ [PRICE] Using cached BBO for {exchange_name}:{symbol} "
                    f"(age: {cached.age_seconds():.2f}s, source: {cached.source})"
                )
                return cached.best_bid, cached.best_ask
        
        # Strategy 2: Fetch fresh data from REST API
        try:
            self.logger.info(
                f"🔄 [PRICE] Fetching fresh BBO for {exchange_name}:{symbol} via REST API"
            )
            
            order_book = await exchange_client.get_order_book_depth(symbol)
            
            if not order_book['bids'] or not order_book['asks']:
                raise ValueError(f"Empty order book for {symbol}")
            
            best_bid = order_book['bids'][0]['price']
            best_ask = order_book['asks'][0]['price']
            
            # Cache the result
            price_data = PriceData(
                best_bid=best_bid,
                best_ask=best_ask,
                mid_price=(best_bid + best_ask) / 2,
                timestamp=datetime.now(),
                source="rest_api"
            )
            self.cache.set(cache_key, price_data)
            
            self.logger.info(
                f"✅ [PRICE] Got fresh BBO: bid={best_bid}, ask={best_ask}"
            )
            
            return best_bid, best_ask
        
        except Exception as e:
            self.logger.error(f"❌ [PRICE] Failed to fetch BBO: {e}")
            raise ValueError(f"Unable to fetch BBO prices for {symbol}: {e}")
    
    def cache_order_book(
        self,
        exchange_name: str,
        symbol: str,
        order_book: Dict[str, list],
        source: str = "liquidity_check"
    ) -> None:
        """
        Cache order book data (called by liquidity analyzer).
        
        This is the KEY method that makes the cache-first strategy work.
        When liquidity analyzer fetches order book, it calls this to cache the result.
        
        Args:
            exchange_name: Exchange name
            symbol: Trading symbol
            order_book: Order book dict with 'bids' and 'asks'
            source: Source of data (for logging)
        """
        if not order_book.get('bids') or not order_book.get('asks'):
            self.logger.warning(
                f"⚠️  [PRICE] Cannot cache empty order book for {exchange_name}:{symbol}"
            )
            return
        
        best_bid = order_book['bids'][0]['price']
        best_ask = order_book['asks'][0]['price']
        
        price_data = PriceData(
            best_bid=best_bid,
            best_ask=best_ask,
            mid_price=(best_bid + best_ask) / 2,
            timestamp=datetime.now(),
            source=source
        )
        
        cache_key = self._make_cache_key(exchange_name, symbol)
        self.cache.set(cache_key, price_data)
        
        self.logger.debug(
            f"💾 [PRICE] Cached BBO for {exchange_name}:{symbol}: "
            f"bid={best_bid}, ask={best_ask} (source: {source})"
        )
    
    def invalidate_cache(self, exchange_name: str, symbol: str) -> None:
        """
        Manually invalidate cached prices.
        
        Use when you know prices are stale (e.g., after large order fill).
        """
        cache_key = self._make_cache_key(exchange_name, symbol)
        self.cache.invalidate(cache_key)
        self.logger.debug(f"🗑️  [PRICE] Invalidated cache for {exchange_name}:{symbol}")

