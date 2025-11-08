"""
Common utilities for Backpack exchange

Shared functions used by both the trading client and funding adapter.
"""

from decimal import Decimal
from typing import Dict, Any


# Known k-prefix tokens on Backpack (1000x multipliers)
# These tokens use "k" prefix: kPEPE, kSHIB, kBONK
BACKPACK_K_PREFIX_TOKENS = {'PEPE', 'SHIB', 'BONK'}


def normalize_symbol(symbol: str) -> str:
    """
    Normalize Backpack symbol format to standard format
    
    Backpack uses k-prefix for 1000x multiplier tokens:
    - "BTC_USDC_PERP" -> "BTC"
    - "kPEPE_USDC_PERP" -> "PEPE" (removes k-prefix)
    - "kSHIB_USDC_PERP" -> "SHIB" (removes k-prefix)
    
    Args:
        symbol: Backpack symbol format (e.g., "BTC_USDC_PERP", "kPEPE_USDC_PERP")
        
    Returns:
        Normalized symbol (e.g., "BTC", "PEPE")
    """
    normalized = symbol.upper()
    # Remove Backpack-specific suffixes
    normalized = normalized.replace('_USDC_PERP', '').replace('_PERP', '')
    normalized = normalized.replace('_USDC', '').replace('_USD', '')
    normalized = normalized.strip('-_/')
    
    # Handle k-prefix for 1000x tokens (e.g., "kPEPE" -> "PEPE")
    if normalized.startswith('K') and len(normalized) > 1:
        base_symbol = normalized[1:]  # Remove 'K' prefix
        if base_symbol in BACKPACK_K_PREFIX_TOKENS:
            return base_symbol
    
    return normalized


def get_backpack_symbol_format(normalized_symbol: str) -> str:
    """
    Convert normalized symbol back to Backpack format
    
    Handles k-prefix for known 1000x multiplier tokens:
    - "BTC" -> "BTC_USDC_PERP"
    - "PEPE" -> "kPEPE_USDC_PERP" (adds k-prefix)
    - "SHIB" -> "kSHIB_USDC_PERP" (adds k-prefix)
    
    Args:
        normalized_symbol: Normalized symbol (e.g., "BTC", "PEPE")
        
    Returns:
        Backpack format (e.g., "BTC_USDC_PERP", "kPEPE_USDC_PERP")
    """
    symbol_upper = normalized_symbol.upper()
    
    # Add k-prefix for known 1000x tokens
    if symbol_upper in BACKPACK_K_PREFIX_TOKENS:
        return f"k{symbol_upper}_USDC_PERP"
    
    return f"{symbol_upper}_USDC_PERP"


def get_quantity_multiplier(normalized_symbol: str) -> int:
    """
    Get the quantity multiplier for a symbol on Backpack.
    
    Backpack's k-prefix tokens (kPEPE, kSHIB, kBONK) represent bundles of 1000 tokens.
    So 1 contract unit = 1000 actual tokens.
    
    Args:
        normalized_symbol: Normalized symbol (e.g., "PEPE", "BTC")
        
    Returns:
        Multiplier (1000 for k-prefix tokens, 1 for others)
    """
    if normalized_symbol.upper() in BACKPACK_K_PREFIX_TOKENS:
        return 1000  # k-prefix = 1000x multiplier
    return 1

