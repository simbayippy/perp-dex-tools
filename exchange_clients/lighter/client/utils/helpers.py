"""
Helper utilities for Lighter client.

Decimal conversion and validation helpers.
"""

from decimal import Decimal, InvalidOperation
from typing import Any, Optional


def decimal_or_none(value: Any) -> Optional[Decimal]:
    """
    Convert raw numeric values to Decimal when possible.
    
    Args:
        value: Raw value to convert (can be None, Decimal, int, float, str, etc.)
        
    Returns:
        Decimal if conversion successful, None otherwise
    """
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


