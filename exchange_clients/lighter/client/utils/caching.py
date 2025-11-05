"""
Caching utilities for Lighter client.

Market ID cache management to avoid expensive API calls.
"""

from typing import Dict, Optional


class MarketIdCache:
    """
    Cache manager for Lighter market IDs.
    
    Caches market_id lookups to avoid expensive order_books() API calls (300 weight).
    When fetching all markets, caches all discovered markets to amortize the cost.
    """
    
    def __init__(self):
        """Initialize empty cache."""
        self._cache: Dict[str, int] = {}
    
    def get(self, symbol: str) -> Optional[int]:
        """
        Get cached market_id for a symbol.
        
        Args:
            symbol: Trading symbol (e.g., 'BTC', 'ETH', 'TOSHI')
            
        Returns:
            Cached market_id if found, None otherwise
        """
        cache_key = symbol.upper()
        return self._cache.get(cache_key)
    
    def set(self, symbol: str, market_id: int) -> None:
        """
        Cache a market_id for a symbol.
        
        Args:
            symbol: Trading symbol
            market_id: Integer market_id to cache
        """
        cache_key = symbol.upper()
        self._cache[cache_key] = market_id
    
    def set_multiple(self, symbol_to_market_id: Dict[str, int]) -> None:
        """
        Cache multiple market_ids at once (e.g., when fetching all markets).
        
        Args:
            symbol_to_market_id: Dictionary mapping symbols to market_ids
        """
        for symbol, market_id in symbol_to_market_id.items():
            self.set(symbol, market_id)
    
    def clear(self) -> None:
        """Clear all cached market_ids."""
        self._cache.clear()
    
    def has(self, symbol: str) -> bool:
        """
        Check if a symbol is cached.
        
        Args:
            symbol: Trading symbol
            
        Returns:
            True if symbol is cached, False otherwise
        """
        cache_key = symbol.upper()
        return cache_key in self._cache


