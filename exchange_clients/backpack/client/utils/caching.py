"""
Caching utilities for Backpack client.

Symbol precision cache and market symbol map cache.
"""

from typing import Dict, Optional


class SymbolPrecisionCache:
    """Cache for symbol decimal precision."""
    
    def __init__(self, max_price_decimals: int = 3):
        """
        Initialize precision cache.
        
        Args:
            max_price_decimals: Default max decimal places
        """
        self._cache: Dict[str, int] = {}
        self.max_price_decimals = max_price_decimals
    
    def get(self, symbol: Optional[str]) -> int:
        """
        Get cached precision for symbol.
        
        Args:
            symbol: Symbol to look up
            
        Returns:
            Precision (number of decimal places)
        """
        if symbol:
            return self._cache.get(symbol, self.max_price_decimals)
        return self.max_price_decimals
    
    def set(self, symbol: str, precision: int) -> None:
        """
        Cache precision for symbol.
        
        Args:
            symbol: Symbol to cache
            precision: Precision value
        """
        self._cache[symbol] = precision
    
    def __contains__(self, symbol: str) -> bool:
        """Check if symbol is in cache (supports 'in' operator)."""
        return symbol in self._cache
    
    def __getitem__(self, symbol: str) -> int:
        """Get precision for symbol (supports subscript notation)."""
        if symbol not in self._cache:
            return self.max_price_decimals
        return self._cache[symbol]
    
    def __setitem__(self, symbol: str, precision: int) -> None:
        """Set precision for symbol (supports subscript notation)."""
        self._cache[symbol] = precision


class MarketSymbolMapCache:
    """Cache for market symbol mappings (normalized -> exchange format)."""
    
    def __init__(self):
        """Initialize market symbol map cache."""
        self._cache: Dict[str, str] = {}
    
    def get(self, normalized: str) -> Optional[str]:
        """
        Get exchange symbol for normalized symbol.
        
        Args:
            normalized: Normalized symbol
            
        Returns:
            Exchange symbol or None
        """
        return self._cache.get(normalized.upper())
    
    def set(self, normalized: str, exchange_symbol: str) -> None:
        """
        Cache mapping from normalized to exchange symbol.
        
        Args:
            normalized: Normalized symbol
            exchange_symbol: Exchange-formatted symbol
        """
        self._cache[normalized.upper()] = exchange_symbol
    
    def __contains__(self, normalized: str) -> bool:
        """Check if normalized symbol is in cache (supports 'in' operator)."""
        return normalized.upper() in self._cache
    
    def __getitem__(self, normalized: str) -> Optional[str]:
        """Get exchange symbol for normalized symbol (supports subscript notation)."""
        return self._cache.get(normalized.upper())
    
    def __setitem__(self, normalized: str, exchange_symbol: str) -> None:
        """Set mapping for normalized symbol (supports subscript notation)."""
        self._cache[normalized.upper()] = exchange_symbol

