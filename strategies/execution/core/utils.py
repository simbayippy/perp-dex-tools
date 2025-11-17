"""
Shared utility functions for the execution layer.

These utilities are used across multiple execution components to avoid circular imports.
"""

from decimal import Decimal
from typing import Optional, Any


def coerce_decimal(value: Any) -> Optional[Decimal]:
    """
    Best-effort conversion to Decimal.
    
    Args:
        value: Value to convert (can be Decimal, str, int, float, None, etc.)
        
    Returns:
        Decimal if conversion successful, None otherwise
    """
    if isinstance(value, Decimal):
        return value
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:  # pragma: no cover - defensive
        return None

