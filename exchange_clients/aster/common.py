"""
Common utilities for Aster exchange

Shared functions used by both the trading client and funding adapter.
"""

import re
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
    - "1000FLOKIUSDT" -> "FLOKI" (multipliers for low-priced tokens)
    
    Args:
        symbol: Aster symbol format (e.g., "BTCUSDT", "1000FLOKIUSDT")
        
    Returns:
        Normalized symbol (e.g., "BTC", "FLOKI")
    """
    normalized = symbol.upper()
    # Remove perpetual suffixes in order of specificity
    normalized = normalized.replace('USDT', '')
    normalized = normalized.replace('USDC', '')  # fallback
    normalized = normalized.strip('-_/')
    
    # Handle multipliers (e.g., "1000FLOKI" -> "FLOKI")
    # Aster uses multipliers for low-priced tokens (similar to Binance)
    match = re.match(r'^(\d+)([A-Z]+)$', normalized)
    if match:
        multiplier, base_symbol = match.groups()
        return base_symbol
    
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

