"""
Helper utilities for Aster client.

Decimal conversion and validation helpers.
"""

from decimal import Decimal, InvalidOperation
from typing import Any, Optional


def to_decimal(value: Any, default: Optional[Decimal] = None) -> Optional[Decimal]:
    """
    Best-effort conversion to Decimal.
    
    Args:
        value: Raw value to convert (can be None, Decimal, int, float, str, etc.)
        default: Default value to return if conversion fails (default: None)
        
    Returns:
        Decimal if conversion successful, default otherwise
    """
    if value is None or value == "":
        return default
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return default

