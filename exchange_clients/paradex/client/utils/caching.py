"""
Caching utilities for Paradex client.

Cache management classes for contract IDs and market metadata.
"""

from typing import Dict, Optional


class ContractIdCache:
    """
    Cache for contract IDs per symbol (for multi-symbol trading support).
    
    Maps normalized_symbol -> contract_id (e.g., "BTC" -> "BTC-USD-PERP")
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

