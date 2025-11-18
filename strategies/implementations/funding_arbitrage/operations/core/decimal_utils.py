"""Decimal conversion utilities."""

from decimal import Decimal, InvalidOperation
from typing import Any, Optional


def to_decimal(value: Any, default: Decimal = Decimal("0")) -> Decimal:
    """
    Convert value to Decimal safely, handling None, float, int, and Decimal.
    
    Args:
        value: Value to convert
        default: Default value if conversion fails (defaults to Decimal("0"))
        
    Returns:
        Decimal representation of value, or default if conversion fails
    """
    if value is None:
        return default
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (ValueError, TypeError, InvalidOperation, Exception):
        return default


def to_decimal_optional(value: Any) -> Optional[Decimal]:
    """
    Best-effort conversion to Decimal, returning None if conversion fails.
    
    Args:
        value: Value to convert
        
    Returns:
        Decimal representation of value, or None if conversion fails
    """
    if isinstance(value, Decimal):
        return value
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def add_decimal(base: Any, increment: Any) -> Optional[Decimal]:
    """
    Add two numeric values after Decimal coercion.
    
    Args:
        base: Base value
        increment: Increment value
        
    Returns:
        Sum as Decimal, or None if both values are None
    """
    base_dec = to_decimal_optional(base)
    inc_dec = to_decimal_optional(increment)
    if base_dec is None and inc_dec is None:
        return None
    if base_dec is None:
        return inc_dec
    if inc_dec is None:
        return base_dec
    return base_dec + inc_dec

