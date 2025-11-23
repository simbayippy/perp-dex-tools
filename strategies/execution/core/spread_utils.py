"""
Spread calculation utilities for order execution.

Provides spread calculation and validation for both opening and closing operations.
"""

from decimal import Decimal
from typing import Optional


# Spread protection thresholds
MAX_ENTRY_SPREAD_PCT = Decimal("0.02")  # 2% threshold for opening positions
MAX_EXIT_SPREAD_PCT = Decimal("0.02")  # 2% threshold for closing positions
MAX_EMERGENCY_CLOSE_SPREAD_PCT = Decimal("0.03")  # 3% threshold for emergency closes


def calculate_spread_pct(bid: Decimal, ask: Decimal) -> Optional[Decimal]:
    """
    Calculate spread percentage from bid and ask prices.
    
    Formula: (ask - bid) / mid_price
    
    Args:
        bid: Best bid price
        ask: Best ask price
        
    Returns:
        Spread percentage as Decimal (e.g., 0.01 = 1%), or None if invalid
    """
    if bid <= 0 or ask <= 0:
        return None
    
    if bid > ask:
        return None  # Invalid BBO
    
    mid_price = (bid + ask) / 2
    if mid_price <= 0:
        return None
    
    spread = ask - bid
    spread_pct = spread / mid_price
    
    return spread_pct


def is_spread_acceptable(
    bid: Decimal,
    ask: Decimal,
    is_opening: bool = True,
    is_critical: bool = False
) -> tuple[bool, Optional[Decimal], Optional[str]]:
    """
    Check if spread is acceptable for order placement.
    
    Args:
        bid: Best bid price
        ask: Best ask price
        is_opening: True if opening position, False if closing
        is_critical: True if critical exit (liquidation risk, etc.)
        
    Returns:
        Tuple of (is_acceptable, spread_pct, reason)
    """
    spread_pct = calculate_spread_pct(bid, ask)
    
    if spread_pct is None:
        return False, None, "Invalid BBO prices"
    
    # Determine threshold based on operation type
    if is_critical:
        threshold = MAX_EMERGENCY_CLOSE_SPREAD_PCT
        operation = "emergency close"
    elif is_opening:
        threshold = MAX_ENTRY_SPREAD_PCT
        operation = "opening"
    else:
        threshold = MAX_EXIT_SPREAD_PCT
        operation = "closing"
    
    if spread_pct > threshold:
        return False, spread_pct, f"Spread {spread_pct*100:.2f}% exceeds {operation} threshold {threshold*100:.2f}%"
    
    return True, spread_pct, None

