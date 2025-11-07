"""
Caching utilities for Paradex client.

Cache management classes for contract IDs and market metadata.
"""

from typing import Dict, Optional


class ContractIdCache:
    """
    Cache for contract IDs per symbol (for multi-symbol trading support).
    
    Maps normalized_symbol -> contract_id (e.g., "BTC" -> "BTC-USD-PERP")
    
    Supports dict-like access (cache[key] = value) and .get() method for safe access
    that returns None instead of raising KeyError.
    """
    
    def __init__(self):
        self._cache: Dict[str, str] = {}
    
    def get(self, symbol: str) -> Optional[str]:
        """
        Get contract ID for symbol.
        
        Returns None if symbol not found (safer than __getitem__ which raises KeyError).
        """
        key = symbol.upper()
        return self._cache.get(key)
    
    def __contains__(self, symbol: str) -> bool:
        """Check if symbol is in cache (supports 'in' operator)."""
        key = symbol.upper()
        return key in self._cache
    
    def __getitem__(self, symbol: str) -> str:
        """Dict-like access: cache[symbol] (raises KeyError if not found)."""
        key = symbol.upper()
        return self._cache[key]
    
    def __setitem__(self, symbol: str, contract_id: str) -> None:
        """Dict-like access: cache[symbol] = contract_id"""
        key = symbol.upper()
        self._cache[key] = contract_id
    
    def clear(self) -> None:
        """Clear all cached contract IDs."""
        self._cache.clear()

