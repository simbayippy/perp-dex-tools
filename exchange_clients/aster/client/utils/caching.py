"""
Caching utilities for Aster client.

Cache management classes for market data, tick sizes, and order notional.
"""

from decimal import Decimal
from typing import Dict, Optional


class TickSizeCache:
    """
    Cache for tick sizes per symbol (for multi-symbol trading support).
    
    Maps normalized_symbol -> tick_size (e.g., "STBL" -> Decimal("0.0001"))
    """
    
    def __init__(self):
        self._cache: Dict[str, Decimal] = {}
    
    def get(self, symbol: str) -> Optional[Decimal]:
        """Get tick size for symbol."""
        key = symbol.upper()
        return self._cache.get(key)
    
    def set(self, symbol: str, tick_size: Decimal) -> None:
        """Set tick size for symbol."""
        key = symbol.upper()
        self._cache[key] = tick_size
    
    def __contains__(self, symbol: str) -> bool:
        """Check if symbol is in cache (supports 'in' operator)."""
        key = symbol.upper()
        return key in self._cache
    
    def clear(self) -> None:
        """Clear all cached tick sizes."""
        self._cache.clear()


class ContractIdCache:
    """
    Cache for contract IDs per symbol (for multi-symbol trading support).
    
    Maps normalized_symbol -> contract_id (e.g., "STBL" -> "STBLUSDT")
    """
    
    def __init__(self):
        self._cache: Dict[str, str] = {}
    
    def get(self, symbol: str) -> Optional[str]:
        """Get contract ID for symbol."""
        key = symbol.upper()
        return self._cache.get(key)
    
    def set(self, symbol: str, contract_id: str) -> None:
        """Set contract ID for symbol."""
        key = symbol.upper()
        self._cache[key] = contract_id
    
    def __contains__(self, symbol: str) -> bool:
        """Check if symbol is in cache (supports 'in' operator)."""
        key = symbol.upper()
        return key in self._cache
    
    def clear(self) -> None:
        """Clear all cached contract IDs."""
        self._cache.clear()

