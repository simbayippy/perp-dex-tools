"""
Common utilities for Aster exchange

Shared functions used by both the trading client and funding adapter.
"""

from decimal import Decimal
from typing import Dict, Any


def normalize_symbol(symbol: str) -> str:
    """
    Normalize Aster symbol format to standard format
    
    Aster symbols follow the pattern (confirmed from SDK examples):
    - "BTCUSDT" -> "BTC"  (similar to Binance format, no separators)
    - "ETHUSDT" -> "ETH"
    - "SOLUSDT" -> "SOL"
    - "PEPEUSDT" -> "PEPE"
    
    Args:
        symbol: Aster symbol format (e.g., "BTCUSDT")
        
    Returns:
        Normalized symbol (e.g., "BTC")
    """
    normalized = symbol.upper()
    # Remove perpetual suffixes in order of specificity
    normalized = normalized.replace('USDT', '')
    normalized = normalized.replace('USDC', '')  # fallback
    normalized = normalized.strip('-_/')
    return normalized


def get_aster_symbol_format(normalized_symbol: str) -> str:
    """
    Convert normalized symbol back to Aster format
    
    Args:
        normalized_symbol: Normalized symbol (e.g., "BTC")
        
    Returns:
        Aster format (e.g., "BTCUSDT")
    """
    return f"{normalized_symbol.upper()}USDT"

