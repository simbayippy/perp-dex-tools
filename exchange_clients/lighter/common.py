"""
Common utilities for Lighter exchange

Shared functions used by both the trading client and funding adapter.
"""

import re
from decimal import Decimal
from typing import Dict, Any


# Special symbol mappings for Lighter
# Maps normalized symbol → Lighter-specific base symbol
# Used when Lighter uses different naming conventions (k-prefix for certain tokens)
LIGHTER_SYMBOL_OVERRIDES = {
    "FLOKI": "kFLOKI",  # Lighter uses kFLOKI-PERP instead of FLOKI-PERP
    "TOSHI": "kTOSHI",  # Lighter uses kTOSHI-PERP
    "BONK": "kBONK",    # Lighter uses kBONK-PERP
    "PEPE": "kPEPE",    # Lighter uses kPEPE-PERP
    "SHIB": "kSHIB",    # Lighter uses kSHIB-PERP
    # Add more overrides as discovered:
    # "EXAMPLE": "1000EXAMPLE",
}


def normalize_symbol(symbol: str) -> str:
    """
    Normalize Lighter symbol format to standard format
    
    Lighter symbols typically follow patterns like:
    - "BTC-PERP" -> "BTC"
    - "ETH-PERP" -> "ETH"
    - "1000PEPE-PERP" -> "PEPE" (some have multipliers)
    - "kFLOKI-PERP" -> "FLOKI" (special prefix)
    
    Args:
        symbol: Lighter-specific symbol format
        
    Returns:
        Normalized symbol (e.g., "BTC")
    """
    normalized = symbol.upper()
    
    # Remove "-PERP" suffix
    normalized = normalized.replace('-PERP', '')
    
    # Remove other common perpetual suffixes
    normalized = normalized.replace('-USD', '')
    normalized = normalized.replace('-USDC', '')
    normalized = normalized.replace('-USDT', '')
    normalized = normalized.replace('PERP', '')
    
    # Handle multipliers (e.g., "1000PEPE" -> "PEPE")
    match = re.match(r'^(\d+)([A-Z]+)$', normalized)
    if match:
        _, symbol_part = match.groups()
        normalized = symbol_part
    
    # Handle special prefixes (e.g., "kFLOKI" -> "FLOKI")
    # Check reverse mapping from LIGHTER_SYMBOL_OVERRIDES
    for standard_symbol, lighter_symbol in LIGHTER_SYMBOL_OVERRIDES.items():
        if normalized.upper() == lighter_symbol.upper():
            return standard_symbol
    
    # Clean up any remaining special characters
    normalized = normalized.strip('-_/')
    
    return normalized


def get_lighter_symbol_format(normalized_symbol: str) -> str:
    """
    Convert normalized symbol back to Lighter-specific format
    
    Handles special cases like FLOKI -> kFLOKI-PERP
    
    Args:
        normalized_symbol: Normalized symbol (e.g., "BTC", "FLOKI")
        
    Returns:
        Lighter-specific format (e.g., "BTC-PERP", "kFLOKI-PERP")
    """
    symbol_upper = normalized_symbol.upper()
    
    # Check for special symbol overrides
    if symbol_upper in LIGHTER_SYMBOL_OVERRIDES:
        lighter_base = LIGHTER_SYMBOL_OVERRIDES[symbol_upper]
        return f"{lighter_base}-PERP"
    
    # Default: standard format
    return f"{symbol_upper}-PERP"


def parse_order_response(raw_response: Dict[str, Any]) -> Dict[str, Any]:
    """
    Standardize Lighter order response format
    
    Args:
        raw_response: Raw order response from Lighter API
        
    Returns:
        Standardized order response
    """
    return {
        'order_id': raw_response.get('order_index', raw_response.get('id')),
        'status': raw_response.get('status', '').upper(),
        'filled_size': Decimal(str(raw_response.get('filled_base_amount', 0))),
        'remaining_size': Decimal(str(raw_response.get('remaining_base_amount', 0))),
        'price': Decimal(str(raw_response.get('price', 0))),
    }


def calculate_position_value(position_size: Decimal, price: Decimal) -> Decimal:
    """
    Calculate the USD value of a position
    
    Args:
        position_size: Size of the position (in base currency)
        price: Current price
        
    Returns:
        Position value in USD
    """
    return abs(position_size * price)


def format_lighter_error(error: Any) -> str:
    """
    Format Lighter API error for logging
    
    Args:
        error: Error from Lighter API
        
    Returns:
        Formatted error message
    """
    if isinstance(error, str):
        return error
    elif hasattr(error, 'message'):
        return error.message
    else:
        return str(error)

