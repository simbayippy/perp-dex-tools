"""Entry validation utilities for position opening."""

from decimal import Decimal
from typing import Optional, Tuple

from helpers.unified_logger import get_core_logger

logger = get_core_logger("entry_validator")


class EntryValidator:
    """Validates entry conditions before opening positions."""
    
    @staticmethod
    def validate_price_divergence(
        long_bid: Decimal,
        long_ask: Decimal,
        short_bid: Decimal,
        short_ask: Decimal,
        max_divergence_pct: Decimal,
    ) -> Tuple[bool, Decimal, Optional[str]]:
        """
        Validate that price divergence between exchanges is acceptable.
        
        Args:
            long_bid: Long exchange best bid
            long_ask: Long exchange best ask
            short_bid: Short exchange best bid
            short_ask: Short exchange best ask
            max_divergence_pct: Maximum allowed divergence percentage (e.g., 0.02 = 2%)
            
        Returns:
            Tuple of (is_valid, divergence_pct, reason)
            - is_valid: True if divergence is acceptable, False otherwise
            - divergence_pct: Calculated divergence percentage
            - reason: None if valid, error message if invalid
        """
        # Validate inputs
        if long_bid <= 0 or long_ask <= 0 or short_bid <= 0 or short_ask <= 0:
            return False, Decimal("0"), "Invalid BBO prices (non-positive values)"
        
        if long_bid > long_ask:
            return False, Decimal("0"), "Invalid long BBO (bid > ask)"
        
        if short_bid > short_ask:
            return False, Decimal("0"), "Invalid short BBO (bid > ask)"
        
        # Calculate mid prices
        try:
            long_mid = (long_bid + long_ask) / Decimal("2")
            short_mid = (short_bid + short_ask) / Decimal("2")
        except Exception as exc:
            logger.warning(f"Failed to calculate mid prices: {exc}")
            return False, Decimal("0"), f"Mid price calculation failed: {exc}"
        
        # Validate mid prices
        if long_mid <= 0 or short_mid <= 0:
            return False, Decimal("0"), "Invalid mid prices (non-positive values)"
        
        # Calculate divergence
        try:
            min_mid = min(long_mid, short_mid)
            max_mid = max(long_mid, short_mid)
            divergence = max_mid - min_mid
            divergence_pct = divergence / min_mid if min_mid > 0 else Decimal("0")
        except Exception as exc:
            logger.warning(f"Failed to calculate divergence: {exc}")
            return False, Decimal("0"), f"Divergence calculation failed: {exc}"
        
        # Check if divergence exceeds threshold
        if divergence_pct > max_divergence_pct:
            reason = (
                f"Price divergence {divergence_pct*100:.2f}% exceeds maximum "
                f"{max_divergence_pct*100:.2f}% (long_mid={long_mid:.6f}, short_mid={short_mid:.6f})"
            )
            return False, divergence_pct, reason
        
        return True, divergence_pct, None

