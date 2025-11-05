"""
Converters for Aster client.

Order info builders from raw API responses.
"""

from decimal import Decimal
from typing import Any, Dict, Optional

from exchange_clients.base_models import OrderInfo
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
        
        info = OrderInfo(
            order_id=str(result.get('orderId', order_id)),
            side=(result.get('side') or '').lower(),
            size=size,
            price=price,
            status=result.get('status', ''),
            filled_size=filled,
            remaining_size=remaining or Decimal("0"),
        )
        
        return info
        
    except Exception:
        return None

