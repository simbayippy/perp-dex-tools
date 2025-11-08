"""
Converters for Lighter client.

Order info and position snapshot builders.
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional

from exchange_clients.base_models import ExchangePositionSnapshot, OrderInfo

from .helpers import decimal_or_none


def build_order_info_from_payload(order_obj: Any, order_id: str) -> Optional[OrderInfo]:
    """
    Convert Lighter order payload (active or inactive) into OrderInfo.
    
    Args:
        order_obj: Lighter order object from API
        order_id: Order identifier (string)
        
    Returns:
        OrderInfo instance, or None if conversion fails
    """
    try:
        size = Decimal(str(getattr(order_obj, "initial_base_amount", "0")))
    except Exception:
        size = Decimal("0")

    try:
        remaining = Decimal(str(getattr(order_obj, "remaining_base_amount", "0")))
    except Exception:
        remaining = Decimal("0")

    try:
        filled_base = Decimal(str(getattr(order_obj, "filled_base_amount", "0")))
    except Exception:
        filled_base = Decimal("0")

    # Get status early to check if order was canceled
    status_raw = str(getattr(order_obj, "status", "")).upper()
    
    # Only calculate filled_base from size - remaining if:
    # 1. filled_base is not provided/zero AND
    # 2. size >= remaining (sanity check) AND
    # 3. Either:
    #    a) Order is NOT canceled, OR
    #    b) Order IS canceled BUT remaining > 0 (partially filled before cancel)
    #    (We skip calculation for canceled orders with remaining=0, as they may have 0 fills)
    if filled_base <= Decimal("0") and size >= remaining:
        if status_raw != "CANCELED" or remaining > Decimal("0"):
            filled_base = size - remaining

    if filled_base < Decimal("0"):
        filled_base = Decimal("0")
    if remaining < Decimal("0"):
        remaining = Decimal("0")

    status_raw = str(getattr(order_obj, "status", "")).upper()
    if status_raw not in {"FILLED", "PARTIALLY_FILLED", "OPEN", "CANCELED"}:
        if filled_base >= size and size > 0:
            status_raw = "FILLED"
        elif filled_base > 0:
            status_raw = "PARTIALLY_FILLED"
        elif remaining >= size:
            status_raw = "OPEN"

    if status_raw == "FILLED":
        remaining = Decimal("0")
        filled_base = size if size > 0 else filled_base
    elif status_raw == "OPEN" and filled_base > 0:
        status_raw = "PARTIALLY_FILLED"

    side = "sell" if getattr(order_obj, "is_ask", False) else "buy"
    try:
        price = Decimal(str(getattr(order_obj, "price", "0")))
    except Exception:
        price = Decimal("0")

    return OrderInfo(
        order_id=str(order_id),
        side=side,
        size=size,
        price=price,
        status=status_raw,
        filled_size=filled_base,
        remaining_size=remaining,
    )


def build_snapshot_from_raw(
    normalized_symbol: str, raw: Dict[str, Any], decimal_helper=decimal_or_none
) -> Optional[ExchangePositionSnapshot]:
    """
    Construct an ExchangePositionSnapshot from cached raw position data.
    
    Args:
        normalized_symbol: Normalized symbol (e.g., "BTC", "TOSHI")
        raw: Raw position data dictionary from WebSocket or REST API
        decimal_helper: Function to convert values to Decimal (default: decimal_or_none)
        
    Returns:
        ExchangePositionSnapshot instance, or None if raw data is invalid
    """
    if raw is None:
        return None

    raw_quantity = raw.get("position") or raw.get("quantity") or Decimal("0")
    try:
        quantity = Decimal(raw_quantity)
    except Exception:
        quantity = decimal_helper(raw_quantity) or Decimal("0")

    sign_indicator = raw.get("sign")
    if isinstance(sign_indicator, int) and sign_indicator != 0:
        quantity = quantity.copy_abs() * (Decimal(1) if sign_indicator > 0 else Decimal(-1))

    entry_price = decimal_helper(raw.get("avg_entry_price"))
    exposure = decimal_helper(raw.get("position_value"))
    if exposure is not None:
        exposure = exposure.copy_abs()

    mark_price = decimal_helper(raw.get("mark_price"))
    if mark_price is None and exposure is not None and quantity != 0:
        mark_price = exposure / quantity.copy_abs()

    unrealized = decimal_helper(raw.get("unrealized_pnl"))
    realized = decimal_helper(raw.get("realized_pnl"))
    margin_reserved = decimal_helper(raw.get("allocated_margin"))
    liquidation_price = decimal_helper(raw.get("liquidation_price"))

    side: Optional[str] = None
    if isinstance(sign_indicator, int):
        if sign_indicator > 0:
            side = "long"
        elif sign_indicator < 0:
            side = "short"
    if side is None:
        if quantity > 0:
            side = "long"
        elif quantity < 0:
            side = "short"

    snapshot = ExchangePositionSnapshot(
        symbol=normalized_symbol,
        quantity=quantity,
        side=side,
        entry_price=entry_price,
        mark_price=mark_price,
        exposure_usd=exposure,
        unrealized_pnl=unrealized,
        realized_pnl=realized,
        funding_accrued=decimal_helper(raw.get("funding_accrued")),
        margin_reserved=margin_reserved,
        leverage=None,
        liquidation_price=liquidation_price,
        timestamp=datetime.now(timezone.utc),
        metadata={
            "market_id": raw.get("market_id"),
            "raw_sign": raw.get("sign"),
        },
    )

    return snapshot


