"""
Common utilities for Paradex exchange

Shared functions used by both the trading client and funding adapter.
"""

from decimal import Decimal
from typing import Dict, Any


def normalize_symbol(symbol: str) -> str:
    """
    Normalize Paradex symbol format to standard format
    
    Args:
        symbol: Paradex symbol format (e.g., "BTC-USD-PERP")
        
    Returns:
        Normalized symbol (e.g., "BTC")
    """
    normalized = symbol.upper()
    normalized = normalized.replace('-USD-PERP', '').replace('-PERP', '')
    normalized = normalized.replace('-USD', '')
    normalized = normalized.strip('-_/')
    return normalized


def get_paradex_symbol_format(normalized_symbol: str) -> str:
    """
    Convert normalized symbol back to Paradex format
    
    Args:
        normalized_symbol: Normalized symbol (e.g., "BTC")
        
    Returns:
        Paradex format (e.g., "BTC-USD-PERP")
    """
    return f"{normalized_symbol.upper()}-USD-PERP"

