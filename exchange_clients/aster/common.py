"""
Common utilities for Aster exchange

Shared functions used by both the trading client and funding adapter.
"""

import re
from decimal import Decimal
from typing import Dict, Any


# Aster's 1000-prefix tokens (1000FLOKIUSDT, etc.)
# These tokens use a 1000x price multiplier similar to Lighter's k-prefix tokens.
# Example: 1000BONKUSDT at $0.01467 vs CoinGecko BONK at $0.00001467 (1000x)
# The price shown is already multiplied by 1000 relative to the actual token price.
ASTER_1000_PREFIX_SYMBOLS = {"FLOKI", "BONK", "PEPE", "SHIB", "CHEEMS"}
ASTER_QUANTITY_MULTIPLIER = 1000  # 1000-prefix = 1000x multiplier


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


def get_quantity_multiplier(normalized_symbol: str) -> int:
    """
    Get the quantity multiplier for a symbol on Aster.
    
    Aster's 1000-prefix tokens (1000BONKUSDT, etc.) have a 1000x multiplier.
    The price shown is 1000x the actual token price (same as CoinGecko price × 1000).
    
    Example: 1000BONKUSDT shows $0.01467, CoinGecko shows $0.00001467 (1000x)
    
    Args:
        normalized_symbol: Normalized symbol (e.g., "BONK")
        
    Returns:
        1000 for 1000-prefix tokens, 1 for others
    """
    if normalized_symbol.upper() in ASTER_1000_PREFIX_SYMBOLS:
        return ASTER_QUANTITY_MULTIPLIER
    return 1

