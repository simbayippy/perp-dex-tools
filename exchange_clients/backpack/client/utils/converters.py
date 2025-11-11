"""
Converters for Backpack client.

Order info builders from raw API responses.
"""

from decimal import Decimal
from typing import Any, Callable, Dict, Optional

from exchange_clients.base_models import OrderInfo, CancelReason
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
        
        # Extract status and normalize cancel reason
        status = result.get("status", "")
        cancel_reason = ""
        
        # Check for rejected/canceled status and extract error code if available
        if status.upper() in {'CANCELED', 'CANCELLED', 'REJECTED', 'EXPIRED'}:
            # Backpack may include error code in the response
            # Check for error code in various possible fields
            error_code = result.get('code') or result.get('errorCode') or result.get('error_code')
            error_msg = result.get('msg') or result.get('message') or result.get('error') or ""
            
            # Normalize Backpack error codes to standard CancelReason values
            # Note: Proactively adding -2021 based on error code format matching Aster
            # Can adjust if actual error format differs
            if error_code == -2021 or (isinstance(error_msg, str) and 'ORDER_WOULD_IMMEDIATELY_TRIGGER' in error_msg.upper()):
                # Backpack uses -2021 for post-only orders that would immediately cross
                cancel_reason = CancelReason.POST_ONLY_VIOLATION
            elif status.upper() == 'EXPIRED':
                cancel_reason = CancelReason.EXPIRED
            elif error_code:
                # Other error codes - pass through as lowercase string
                cancel_reason = str(error_code).lower()
            else:
                cancel_reason = CancelReason.UNKNOWN
        
        info = OrderInfo(
            order_id=str(result.get("id", order_id)),
            side=side,
            size=size or Decimal("0"),
            price=price or Decimal("0"),
            status=status,
            filled_size=filled or Decimal("0"),
            remaining_size=remaining or Decimal("0"),
            cancel_reason=cancel_reason,
        )
        
        return info
        
    except Exception:
        return None

