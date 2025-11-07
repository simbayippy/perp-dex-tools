"""
Helper utilities for Paradex client.

Decimal conversion, order side normalization, and validation helpers.
"""

from decimal import Decimal, InvalidOperation
from typing import Optional, Union


def to_decimal(value: Optional[Union[str, int, float, Decimal]], default: Optional[Decimal] = None) -> Optional[Decimal]:
    """
    Safely convert a value to Decimal.
    
    Args:
        value: Value to convert (can be str, int, float, Decimal, or None)
        default: Default value to return if conversion fails (default: None)
        
    Returns:
        Decimal instance, or default if conversion fails
    """
    if value is None:
        return default
    
    if isinstance(value, Decimal):
        return value
    
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return default


def normalize_order_side(side: Union[str, object]) -> str:
    """
    Normalize order side to lowercase string ('buy' or 'sell').
    
    Handles:
    - Paradex OrderSide enum (OrderSide.Buy -> 'buy')
    - String values ('BUY', 'Buy', 'buy' -> 'buy')
    - String values ('SELL', 'Sell', 'sell' -> 'sell')
    
    Args:
        side: Order side (enum, string, or other)
        
    Returns:
        Normalized side string ('buy' or 'sell')
    """
    if side is None:
        return 'buy'  # Default
    
    # Handle Paradex OrderSide enum
    if hasattr(side, 'value'):
        side_str = str(side.value).upper()
    else:
        side_str = str(side).upper()
    
    if side_str in ('BUY', '1'):
        return 'buy'
    elif side_str in ('SELL', '2'):
        return 'sell'
    else:
        # Default to buy if unclear
        return 'buy'


def parse_order_side(side_str: str) -> str:
    """
    Parse order side string to normalized format.
    
    Args:
        side_str: Order side string ('buy', 'sell', 'BUY', 'SELL', etc.)
        
    Returns:
        Normalized side string ('buy' or 'sell')
    """
    return normalize_order_side(side_str)


def decimal_or_none(value: Optional[Union[str, int, float, Decimal]]) -> Optional[Decimal]:
    """
    Convert value to Decimal, returning None if conversion fails.
    
    Convenience wrapper around to_decimal with default=None.
    
    Args:
        value: Value to convert
        
    Returns:
        Decimal instance or None
    """
    return to_decimal(value, default=None)

