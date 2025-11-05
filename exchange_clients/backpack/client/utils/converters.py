"""
Converters for Backpack client.

Order info builders from raw API responses.
"""

from decimal import Decimal
from typing import Any, Callable, Dict, Optional

from exchange_clients.base_models import OrderInfo
from .helpers import to_decimal


def build_order_info_from_raw(
    result: Dict[str, Any],
    order_id: str,
    to_decimal_fn: Optional[Callable[[Any, Optional[Decimal]], Optional[Decimal]]] = None,
) -> Optional[OrderInfo]:
    """
    Convert Backpack API order response into OrderInfo.
    
    Args:
        result: Raw API response dictionary
        order_id: Order identifier (string)
        to_decimal_fn: Optional function to convert to Decimal (defaults to to_decimal)
        
    Returns:
        OrderInfo instance, or None if conversion fails
    """
    if to_decimal_fn is None:
        to_decimal_fn = to_decimal
    
    try:
        side_raw = (result.get("side") or "").lower()
        if side_raw == "bid":
            side = "buy"
        elif side_raw == "ask":
            side = "sell"
        else:
            side = side_raw or ""
        
        size = to_decimal_fn(result.get("quantity"), Decimal("0"))
        price = to_decimal_fn(result.get("price"), Decimal("0"))
        filled = to_decimal_fn(result.get("executedQuantity"), Decimal("0"))
        
        remaining = None
        if size is not None and filled is not None:
            remaining = size - filled
        
        info = OrderInfo(
            order_id=str(result.get("id", order_id)),
            side=side,
            size=size or Decimal("0"),
            price=price or Decimal("0"),
            status=result.get("status", ""),
            filled_size=filled or Decimal("0"),
            remaining_size=remaining or Decimal("0"),
        )
        
        return info
        
    except Exception:
        return None

