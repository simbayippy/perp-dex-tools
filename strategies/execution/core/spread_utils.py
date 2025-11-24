"""
Spread calculation utilities for order execution.

Provides spread calculation and validation for both opening and closing operations.
"""

from decimal import Decimal
from enum import Enum
from typing import Optional


class SpreadCheckType(Enum):
    """Types of spread checks with different thresholds."""
    ENTRY = "entry"  # Opening new positions
    EXIT = "exit"  # Normal position closes
    EMERGENCY_CLOSE = "emergency_close"  # Critical exits (liquidation risk, severe imbalance)
    AGGRESSIVE_HEDGE = "aggressive_hedge"  # Aggressive limit hedge retries


# Spread protection thresholds (internal - use SpreadCheckType enum in public API)
# These defaults can be overridden via configure_spread_thresholds()
_SPREAD_THRESHOLDS = {
    SpreadCheckType.ENTRY: Decimal("0.001"),  # 0.1% threshold for opening positions
    SpreadCheckType.EXIT: Decimal("0.001"),  # 0.1% threshold for closing positions
    SpreadCheckType.EMERGENCY_CLOSE: Decimal("0.002"),  # 0.2% threshold for emergency closes
    SpreadCheckType.AGGRESSIVE_HEDGE: Decimal("0.0005"),  # 0.05% threshold for aggressive hedge retries
}


def configure_spread_thresholds(
    entry_threshold: Optional[Decimal] = None,
    exit_threshold: Optional[Decimal] = None,
    emergency_threshold: Optional[Decimal] = None,
    hedge_threshold: Optional[Decimal] = None,
) -> None:
    """
    Configure spread thresholds from strategy config.

    This allows strategies to customize spread protection thresholds at initialization
    without modifying the module-level defaults. Once configured, all subsequent calls
    to is_spread_acceptable() will use the configured thresholds.

    Args:
        entry_threshold: Threshold for opening positions (e.g., Decimal("0.001") = 0.1%)
        exit_threshold: Threshold for normal position closes (e.g., Decimal("0.001") = 0.1%)
        emergency_threshold: Threshold for emergency closes (e.g., Decimal("0.002") = 0.2%)
        hedge_threshold: Threshold for aggressive hedge retries (e.g., Decimal("0.0005") = 0.05%)

    Example:
        >>> # Configure from funding arbitrage config
        >>> configure_spread_thresholds(
        ...     entry_threshold=config.max_entry_spread_pct,
        ...     exit_threshold=config.max_exit_spread_pct,
        ...     emergency_threshold=config.max_emergency_close_spread_pct,
        ... )
    """
    global _SPREAD_THRESHOLDS

    if entry_threshold is not None:
        _SPREAD_THRESHOLDS[SpreadCheckType.ENTRY] = entry_threshold

    if exit_threshold is not None:
        _SPREAD_THRESHOLDS[SpreadCheckType.EXIT] = exit_threshold

    if emergency_threshold is not None:
        _SPREAD_THRESHOLDS[SpreadCheckType.EMERGENCY_CLOSE] = emergency_threshold

    if hedge_threshold is not None:
        _SPREAD_THRESHOLDS[SpreadCheckType.AGGRESSIVE_HEDGE] = hedge_threshold



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
    check_type: SpreadCheckType = SpreadCheckType.EXIT,
) -> tuple[bool, Optional[Decimal], Optional[str]]:
    """
    Check if spread is acceptable for order placement.

    Args:
        bid: Best bid price
        ask: Best ask price
        check_type: Type of spread check (ENTRY, EXIT, EMERGENCY_CLOSE, AGGRESSIVE_HEDGE)

    Returns:
        Tuple of (is_acceptable, spread_pct, reason)

    Examples:
        >>> # Opening position
        >>> is_spread_acceptable(bid, ask, SpreadCheckType.ENTRY)

        >>> # Normal close
        >>> is_spread_acceptable(bid, ask, SpreadCheckType.EXIT)

        >>> # Emergency close (liquidation risk)
        >>> is_spread_acceptable(bid, ask, SpreadCheckType.EMERGENCY_CLOSE)

        >>> # Aggressive hedge retry
        >>> is_spread_acceptable(bid, ask, SpreadCheckType.AGGRESSIVE_HEDGE)
    """
    spread_pct = calculate_spread_pct(bid, ask)

    if spread_pct is None:
        return False, None, "Invalid BBO prices"

    # Get threshold for this check type
    threshold = _SPREAD_THRESHOLDS[check_type]
    operation = check_type.value.replace("_", " ")

    if spread_pct > threshold:
        return False, spread_pct, f"Spread {spread_pct*100:.4f}% exceeds {operation} threshold {threshold*100:.4f}%"

    return True, spread_pct, None

