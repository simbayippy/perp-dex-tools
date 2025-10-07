"""
Common utilities for Backpack exchange

Shared functions used by both the trading client and funding adapter.
"""

from decimal import Decimal
from typing import Dict, Any


def normalize_symbol(symbol: str) -> str:
    """
    Normalize Backpack symbol format to standard format
    
    Args:
        symbol: Backpack symbol format (e.g., "BTC_USDC_PERP")
        
    Returns:
        Normalized symbol (e.g., "BTC")
    """
    normalized = symbol.upper()
    normalized = normalized.replace('_USDC_PERP', '').replace('_PERP', '')
    normalized = normalized.replace('_USDC', '').replace('_USD', '')
    normalized = normalized.strip('-_/')
    return normalized


def get_backpack_symbol_format(normalized_symbol: str) -> str:
    """
    Convert normalized symbol back to Backpack format
    
    Args:
        normalized_symbol: Normalized symbol (e.g., "BTC")
        
    Returns:
        Backpack format (e.g., "BTC_USDC_PERP")
    """
    return f"{normalized_symbol.upper()}_USDC_PERP"

