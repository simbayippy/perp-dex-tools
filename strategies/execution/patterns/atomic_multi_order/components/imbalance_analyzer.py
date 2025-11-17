"""
Imbalance analyzer for atomic multi-order execution.

Calculates and checks exposure imbalances using quantity (normalized to actual tokens)
for true delta-neutrality.
"""

from __future__ import annotations

from decimal import Decimal
from typing import List

from helpers.unified_logger import get_core_logger

from ..contexts import OrderContext


class ImbalanceAnalyzer:
    """Analyzes exposure imbalances from order contexts."""

    def __init__(self, logger=None):
        self.logger = logger or get_core_logger("imbalance_analyzer")

    def calculate_imbalance(
        self,
        contexts: List[OrderContext]
    ) -> tuple[Decimal, Decimal, Decimal, Decimal]:
        """
        Calculate exposure imbalance from contexts using QUANTITY (normalized to actual tokens).

        CRITICAL: Uses quantity imbalance, not USD imbalance, for true delta-neutrality.
        Quantities are normalized to actual tokens using exchange multipliers to handle
        different unit systems (e.g., Lighter kTOSHI = 1000x, Aster TOSHI = 1x).

        For OPENING operations:
        - BUY orders increase long exposure
        - SELL orders increase short exposure
        - Checks if actual token quantities match (delta-neutral)

        For CLOSING operations (all orders have reduce_only=True):
        - BUY orders close SHORT positions (reduce short exposure)
        - SELL orders close LONG positions (reduce long exposure)
        - Imbalance check is skipped (positions are being closed, not opened)

        Args:
            contexts: List of order contexts to analyze

        Returns:
            Tuple of (total_long_tokens, total_short_tokens, imbalance_tokens, imbalance_pct)
            where tokens are normalized to actual token amounts (accounting for multipliers)
        """
        # Check if this is a closing operation (all orders have reduce_only=True)
        is_closing_operation = (
            len(contexts) > 0 and
            all(ctx.spec.reduce_only is True for ctx in contexts)
        )

        if is_closing_operation:
            # For closing operations, we're reducing exposure, not creating it
            # Return zeros to indicate no new exposure imbalance
            self.logger.debug(
                "Closing operation detected (all orders have reduce_only=True). "
                "Skipping imbalance check as we're reducing exposure, not creating it."
            )
            return Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0")

        # For opening operations, calculate QUANTITY imbalance (normalized to actual tokens)
        total_long_tokens = Decimal("0")
        total_short_tokens = Decimal("0")

        for ctx in contexts:
            if ctx.filled_quantity <= Decimal("0"):
                continue

            # Get multiplier for this exchange/symbol
            try:
                multiplier = Decimal(str(ctx.spec.exchange_client.get_quantity_multiplier(ctx.spec.symbol)))
            except Exception as exc:
                self.logger.warning(
                    f"Failed to get multiplier for {ctx.spec.symbol} on "
                    f"{ctx.spec.exchange_client.get_exchange_name()}: {exc}. Using 1."
                )
                multiplier = Decimal("1")

            # Convert filled quantity to actual tokens
            actual_tokens = ctx.filled_quantity * multiplier

            if ctx.spec.side == "buy":
                total_long_tokens += actual_tokens
            elif ctx.spec.side == "sell":
                total_short_tokens += actual_tokens

        # Calculate quantity imbalance
        imbalance_tokens = abs(total_long_tokens - total_short_tokens)

        # Calculate imbalance as percentage: (max - min) / max
        min_tokens = min(total_long_tokens, total_short_tokens)
        max_tokens = max(total_long_tokens, total_short_tokens)
        imbalance_pct = Decimal("0")
        if max_tokens > Decimal("0"):
            imbalance_pct = (max_tokens - min_tokens) / max_tokens

        return total_long_tokens, total_short_tokens, imbalance_tokens, imbalance_pct

    def check_critical_imbalance(
        self,
        total_long_tokens: Decimal,
        total_short_tokens: Decimal,
        threshold_pct: Decimal = Decimal("0.01")
    ) -> tuple[bool, Decimal, Decimal]:
        """
        Check if quantity imbalance exceeds critical threshold.

        Uses quantity (normalized to actual tokens) instead of USD for true delta-neutrality.

        Args:
            total_long_tokens: Total actual tokens for long positions (normalized)
            total_short_tokens: Total actual tokens for short positions (normalized)
            threshold_pct: Critical imbalance threshold (default 1%)

        Returns:
            Tuple of (is_critical, imbalance_tokens, imbalance_pct)
        """
        imbalance_tokens = abs(total_long_tokens - total_short_tokens)
        min_tokens = min(total_long_tokens, total_short_tokens)
        max_tokens = max(total_long_tokens, total_short_tokens)

        imbalance_pct = Decimal("0")
        if max_tokens > Decimal("0"):
            imbalance_pct = (max_tokens - min_tokens) / max_tokens

        is_critical = imbalance_pct > threshold_pct
        return is_critical, imbalance_tokens, imbalance_pct

