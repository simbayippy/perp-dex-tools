"""
Converters for Aster client.

Order info builders from raw API responses.
"""

from decimal import Decimal
from typing import Any, Dict, Optional

from exchange_clients.base_models import OrderInfo, CancelReason
from .helpers import to_decimal


def build_order_info_from_raw(result: Dict[str, Any], order_id: str) -> Optional[OrderInfo]:
    """
    Convert Aster API order response into OrderInfo.
    
    Args:
        result: Raw API response dictionary
        order_id: Order identifier (string)
        
    Returns:
        OrderInfo instance, or None if conversion fails
    """
    try:
        # Handle different response formats
        size = to_decimal(result.get('origQty'), Decimal("0")) or Decimal("0")
        filled = to_decimal(result.get('executedQty'), Decimal("0")) or Decimal("0")
        
        # Determine price based on order type
        order_type = result.get('type', '')
        if order_type == 'MARKET':
            price = to_decimal(result.get('avgPrice'), Decimal("0")) or Decimal("0")
        else:
            price = to_decimal(result.get('price'), Decimal("0")) or Decimal("0")
        
        # Calculate remaining
        remaining = None
        if size is not None and filled is not None:
            remaining = size - filled
            if remaining < Decimal("0"):
                remaining = Decimal("0")
        
        # Extract status and normalize cancel reason
        status = result.get('status', '')
        cancel_reason = ""
        
        # Check for rejected/canceled status and extract error code if available
        if status.upper() in {'CANCELED', 'CANCELLED', 'REJECTED', 'EXPIRED'}:
            # Aster may include error code in the response (e.g., from exception handling)
            # Check for error code in various possible fields
            error_code = result.get('code') or result.get('errorCode') or result.get('error_code')
            error_msg = result.get('msg') or result.get('message') or result.get('error') or ""
            
            # Normalize Aster error codes to standard CancelReason values
            if error_code == -2021 or (isinstance(error_msg, str) and 'ORDER_WOULD_IMMEDIATELY_TRIGGER' in error_msg.upper()):
                # Aster uses -2021 for GTX (post-only) orders that would immediately cross
                cancel_reason = CancelReason.POST_ONLY_VIOLATION
            elif status.upper() == 'EXPIRED':
                cancel_reason = CancelReason.EXPIRED
            elif error_code:
                # Other error codes - pass through as lowercase string
                cancel_reason = str(error_code).lower()
            else:
                cancel_reason = CancelReason.UNKNOWN
        
        info = OrderInfo(
            order_id=str(result.get('orderId', order_id)),
            side=(result.get('side') or '').lower(),
            size=size,
            price=price,
            status=status,
            filled_size=filled,
            remaining_size=remaining or Decimal("0"),
            cancel_reason=cancel_reason,
        )
        
        return info
        
    except Exception:
        return None

