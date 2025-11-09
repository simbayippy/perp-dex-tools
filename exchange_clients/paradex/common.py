"""
Common utilities for Paradex exchange

Shared functions used by both the trading client and funding adapter.
"""

from decimal import Decimal
from typing import Dict, Any


# Known k-prefix tokens on Paradex (1000x multipliers)
# These tokens use "k" prefix: kPEPE, kSHIB, kBONK, kFLOKI
# Based on Paradex API docs: contract name "kFLOKI-USD-PERP", base currency "kFLOKI"
PARADEX_K_PREFIX_TOKENS = {'PEPE', 'SHIB', 'BONK', 'FLOKI'}


def normalize_symbol(symbol: str) -> str:
    """
    Normalize Paradex symbol format to standard format
    
    Paradex uses k-prefix for 1000x multiplier tokens:
    - "BTC-USD-PERP" -> "BTC"
    - "kPEPE-USD-PERP" -> "PEPE" (removes k-prefix)
    - "kFLOKI-USD-PERP" -> "FLOKI" (removes k-prefix)
    
    Args:
        symbol: Paradex symbol format (e.g., "BTC-USD-PERP", "kPEPE-USD-PERP")
        
    Returns:
        Normalized symbol (e.g., "BTC", "PEPE")
    """
    normalized = symbol.upper()
    # Remove Paradex-specific suffixes
    normalized = normalized.replace('-USD-PERP', '').replace('-PERP', '')
    normalized = normalized.replace('-USD', '')
    normalized = normalized.strip('-_/')
    
    # Handle k-prefix for 1000x tokens (e.g., "kPEPE" -> "PEPE", "kFLOKI" -> "FLOKI")
    if normalized.startswith('K') and len(normalized) > 1:
        base_symbol = normalized[1:]  # Remove 'K' prefix
        if base_symbol in PARADEX_K_PREFIX_TOKENS:
            return base_symbol
    
    return normalized


def get_paradex_symbol_format(normalized_symbol: str) -> str:
    """
    Convert normalized symbol back to Paradex format
    
    Handles k-prefix for known 1000x multiplier tokens:
    - "BTC" -> "BTC-USD-PERP"
    - "PEPE" -> "kPEPE-USD-PERP" (adds k-prefix)
    - "FLOKI" -> "kFLOKI-USD-PERP" (adds k-prefix)
    
    Args:
        normalized_symbol: Normalized symbol (e.g., "BTC", "PEPE")
        
    Returns:
        Paradex format (e.g., "BTC-USD-PERP", "kPEPE-USD-PERP")
    """
    symbol_upper = normalized_symbol.upper()
    
    # Add k-prefix for known 1000x tokens
    if symbol_upper in PARADEX_K_PREFIX_TOKENS:
        return f"k{symbol_upper}-USD-PERP"
    
    return f"{symbol_upper}-USD-PERP"


def get_quantity_multiplier(normalized_symbol: str) -> int:
    """
    Get the quantity multiplier for a symbol on Paradex.
    
    Paradex's k-prefix tokens (kPEPE, kSHIB, kBONK, kFLOKI) represent bundles of 1000 tokens.
    So 1 contract unit = 1000 actual tokens.
    
    Based on Paradex API docs:
    - Order size increment: "1 kFLOKI" means 1 contract = 1000 actual FLOKI tokens
    - Base currency: "kFLOKI" indicates 1000x multiplier
    
    Args:
        normalized_symbol: Normalized symbol (e.g., "FLOKI", "BTC")
        
    Returns:
        Multiplier (1000 for k-prefix tokens, 1 for others)
    """
    if normalized_symbol.upper() in PARADEX_K_PREFIX_TOKENS:
        return 1000  # k-prefix = 1000x multiplier
    return 1

