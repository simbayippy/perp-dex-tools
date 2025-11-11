"""
Converters for Paradex client.

Order info and position snapshot builders from Paradex API responses.
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional

from exchange_clients.base_models import ExchangePositionSnapshot, OrderInfo

from .helpers import to_decimal, normalize_order_side, decimal_or_none


def build_order_info_from_paradex(order_data: Dict[str, Any], order_id: Optional[str] = None) -> Optional[OrderInfo]:
    """
    Convert Paradex API order response into OrderInfo.
    
    Args:
        order_data: Raw API response dictionary from fetch_order or fetch_orders
        order_id: Optional order ID (if not in order_data)
        
    Returns:
        OrderInfo instance, or None if conversion fails
    """
    try:
        # Extract order ID
        order_id = order_id or order_data.get('id') or order_data.get('order_id')
        if not order_id:
            return None
        
        # Extract size and remaining
        size = to_decimal(order_data.get('size'), Decimal("0")) or Decimal("0")
        remaining_size = to_decimal(order_data.get('remaining') or order_data.get('remaining_size'), Decimal("0")) or Decimal("0")
        
        # Calculate filled size
        filled_size = size - remaining_size
        if filled_size < Decimal("0"):
            filled_size = Decimal("0")
        if remaining_size < Decimal("0"):
            remaining_size = Decimal("0")
        
        # Extract price (limit orders have price, market orders may have avg_price)
        price = to_decimal(order_data.get('price') or order_data.get('limit_price') or order_data.get('avg_price'), Decimal("0")) or Decimal("0")
        
        # Extract and normalize side
        side = normalize_order_side(order_data.get('side') or order_data.get('order_side'))
        
        # Extract and normalize status
        status_raw = str(order_data.get('status', '')).upper()
        # Map Paradex statuses to our statuses
        status_map = {
            'NEW': 'OPEN',  # NEW orders are still open
            'OPEN': 'OPEN',
            'CLOSED': 'FILLED' if filled_size >= size else 'CANCELED',
        }
        status = status_map.get(status_raw, status_raw.lower())
        
        # Determine status from filled/remaining if status is unclear
        if status not in ('OPEN', 'FILLED', 'PARTIALLY_FILLED', 'CANCELED'):
            if filled_size >= size and size > 0:
                status = 'FILLED'
            elif filled_size > 0:
                status = 'PARTIALLY_FILLED'
            elif remaining_size >= size:
                status = 'OPEN'
            else:
                status = 'CANCELED'
        
        # Extract cancel reason if available and normalize to standard format
        cancel_reason_raw = str(order_data.get('cancel_reason', '')).upper()
        cancel_reason = ""
        
        # Normalize Paradex cancel reasons to standard CancelReason values
        if cancel_reason_raw in ("POST_ONLY_WOULD_CROSS", "POST_ONLY"):
            # Paradex uses POST_ONLY_WOULD_CROSS, normalize to our standard
            from exchange_clients.base_models import CancelReason
            cancel_reason = CancelReason.POST_ONLY_VIOLATION
        elif cancel_reason_raw:
            # Pass through other cancel reasons as-is (lowercase for consistency)
            cancel_reason = cancel_reason_raw.lower()
        
        return OrderInfo(
            order_id=str(order_id),
            side=side,
            size=size,
            price=price,
            status=status,
            filled_size=filled_size,
            remaining_size=remaining_size,
            cancel_reason=cancel_reason,
        )
        
    except Exception:
        return None


def build_snapshot_from_paradex(
    normalized_symbol: str,
    position_data: Dict[str, Any],
    decimal_helper=decimal_or_none
) -> Optional[ExchangePositionSnapshot]:
    """
    Construct an ExchangePositionSnapshot from Paradex position data.
    
    Args:
        normalized_symbol: Normalized symbol (e.g., "BTC", "ETH")
        position_data: Raw position data dictionary from fetch_positions
        decimal_helper: Function to convert values to Decimal (default: decimal_or_none)
        
    Returns:
        ExchangePositionSnapshot instance, or None if position is closed/invalid
    """
    if not position_data:
        return None
    
    # Check if position is open
    status = str(position_data.get('status', '')).upper()
    if status != 'OPEN':
        return None
    
    # Extract quantity (signed: positive for long, negative for short)
    size_str = position_data.get('size')
    quantity = decimal_helper(size_str) or Decimal("0")
    
    # Determine side from quantity sign or explicit side field
    side: Optional[str] = None
    side_field = position_data.get('side')
    if side_field:
        side_str = str(side_field).upper()
        if side_str == 'LONG':
            side = 'long'
        elif side_str == 'SHORT':
            side = 'short'
    
    # If side not explicit, infer from quantity sign
    if side is None:
        if quantity > 0:
            side = 'long'
        elif quantity < 0:
            side = 'short'
            quantity = abs(quantity)  # Make quantity positive for short positions
    
    # Extract prices
    entry_price = decimal_helper(position_data.get('average_entry_price') or position_data.get('average_entry_price_usd'))
    mark_price = None  # Not directly available in position response, may need to fetch separately
    
    # Extract PnL
    unrealized_pnl = decimal_helper(position_data.get('unrealized_pnl'))
    realized_pnl = decimal_helper(position_data.get('realized_positional_pnl'))
    
    # Extract funding
    funding_accrued = decimal_helper(position_data.get('unrealized_funding_pnl'))
    
    # Extract margin and leverage
    leverage = decimal_helper(position_data.get('leverage'))
    liquidation_price = decimal_helper(position_data.get('liquidation_price'))
    
    # Calculate exposure (position value in USD)
    # Use cost_usd if available, otherwise calculate from size and entry price
    exposure_usd = decimal_helper(position_data.get('cost_usd'))
    if exposure_usd is None and entry_price is not None and quantity > 0:
        exposure_usd = abs(quantity * entry_price)
    
    # Margin reserved - not directly available, may need to calculate from leverage
    margin_reserved = None
    if leverage is not None and leverage > 0 and exposure_usd is not None:
        margin_reserved = exposure_usd / leverage
    
    snapshot = ExchangePositionSnapshot(
        symbol=normalized_symbol,
        quantity=abs(quantity),  # Always positive
        side=side,
        entry_price=entry_price,
        mark_price=mark_price,
        exposure_usd=exposure_usd,
        unrealized_pnl=unrealized_pnl,
        realized_pnl=realized_pnl,
        funding_accrued=funding_accrued,
        margin_reserved=margin_reserved,
        leverage=leverage,
        liquidation_price=liquidation_price,
        timestamp=datetime.now(timezone.utc),
        metadata={
            "market": position_data.get('market'),
            "position_id": position_data.get('id'),
            "account": position_data.get('account'),
            "seq_no": position_data.get('seq_no'),
        },
    )
    
    return snapshot

