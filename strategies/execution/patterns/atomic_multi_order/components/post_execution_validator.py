"""
Post-Execution Validator - validates final execution results.

This module extracts the validation logic that runs after execution completes,
including imbalance checks, exposure verification, and rollback decisions.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional

from ..contexts import OrderContext
from .exposure_verifier import ExposureVerifier
from .imbalance_analyzer import ImbalanceAnalyzer


@dataclass
class ValidationResult:
    """Result of post-execution validation."""
    
    passed: bool
    should_rollback: bool
    error_message: Optional[str]
    imbalance_tokens: Decimal
    imbalance_pct: Decimal
    all_filled: bool
    rollback_cost: Decimal = Decimal("0")


class PostExecutionValidator:
    """Validates execution results after completion."""
    
    def __init__(
        self,
        imbalance_analyzer: ImbalanceAnalyzer,
        exposure_verifier: ExposureVerifier,
        logger,
        post_trade_max_imbalance_pct: Decimal = Decimal("0.02"),
        post_trade_base_tolerance: Decimal = Decimal("0.0001"),
    ):
        self._imbalance_analyzer = imbalance_analyzer
        self._exposure_verifier = exposure_verifier
        self.logger = logger
        self._post_trade_max_imbalance_pct = post_trade_max_imbalance_pct
        self._post_trade_base_tolerance = post_trade_base_tolerance
    
    async def validate(
        self,
        contexts: List[OrderContext],
        orders: List,
        rollback_performed: bool,
        hedge_error: Optional[str],
        rollback_on_partial: bool,
        stage_prefix: Optional[str] = None,
    ) -> ValidationResult:
        """
        Validate execution results and determine if rollback is needed.
        
        Args:
            contexts: All order contexts
            orders: Original order specs
            rollback_performed: Whether rollback was already performed
            hedge_error: Optional error message from hedge
            rollback_on_partial: Whether to rollback on partial fills
            stage_prefix: Optional stage prefix for logging
            
        Returns:
            ValidationResult with validation status and rollback decision
        """
        # If rollback already performed, return failure result
        if rollback_performed:
            return ValidationResult(
                passed=False,
                should_rollback=False,  # Already rolled back
                error_message=hedge_error or "Rolled back after hedge failure",
                imbalance_tokens=Decimal("0"),
                imbalance_pct=Decimal("0"),
                all_filled=False,
                rollback_cost=Decimal("0"),
            )
        
        # Determine if this is a close operation (for skipping imbalance checks)
        is_close_operation = (
            len(contexts) > 0 and 
            all(ctx.spec.reduce_only is True for ctx in contexts)
        )
        
        # Calculate imbalance
        total_long_tokens, total_short_tokens, imbalance_tokens, imbalance_pct = self._imbalance_analyzer.calculate_imbalance(contexts)
        imbalance_tolerance = Decimal("0.01")  # 1% tolerance for quantity imbalance
        
        # Check if all orders filled
        filled_orders_count = sum(1 for ctx in contexts if ctx.result and ctx.filled_quantity > Decimal("0"))
        all_filled = filled_orders_count == len(orders)
        
        if all_filled:
            # For close operations, imbalance doesn't matter - goal is qty = 0, not matching quantities
            # Skip imbalance checks for close operations
            is_critical = False
            if not is_close_operation:
                # Check if quantity imbalance is within acceptable bounds (1% threshold)
                is_critical, _, _ = self._imbalance_analyzer.check_critical_imbalance(total_long_tokens, total_short_tokens)
            
            if is_critical:
                self.logger.error(
                    f"⚠️ CRITICAL QUANTITY IMBALANCE detected despite all orders filled: "
                    f"longs={total_long_tokens:.6f} tokens, shorts={total_short_tokens:.6f} tokens, "
                    f"imbalance={imbalance_tokens:.6f} tokens ({imbalance_pct*100:.2f}%). Triggering emergency rollback."
                )
                return ValidationResult(
                    passed=False,
                    should_rollback=True,
                    error_message=f"Critical quantity imbalance: {imbalance_tokens:.6f} tokens ({imbalance_pct*100:.2f}%)",
                    imbalance_tokens=imbalance_tokens,
                    imbalance_pct=imbalance_pct,
                    all_filled=True,
                    rollback_cost=Decimal("0"),  # Will be set by caller after rollback
                )
            elif imbalance_pct > imbalance_tolerance:
                self.logger.warning(
                    f"Minor quantity imbalance detected after hedge: longs={total_long_tokens:.6f} tokens, "
                    f"shorts={total_short_tokens:.6f} tokens, imbalance={imbalance_tokens:.6f} tokens "
                    f"({imbalance_pct*100:.2f}% within 1% tolerance)"
                )

            # Verify post-trade exposure
            post_trade = await self._exposure_verifier.verify_post_trade_exposure(contexts)
            if post_trade is not None:
                net_qty = post_trade.get("net_qty", Decimal("0"))
                # Use net_qty for quantity comparison (exposure verified purely on quantity)
                imbalance_tokens = max(imbalance_tokens, net_qty)

                if net_qty > self._post_trade_base_tolerance:
                    self.logger.warning(
                        "⚠️ Post-trade exposure detected after hedging: "
                        f"net_qty={net_qty:.6f} tokens."
                    )
                elif net_qty > Decimal("0"):
                    self.logger.debug(
                        f"Post-trade exposure within tolerance: net_qty={net_qty:.6f} tokens."
                    )
            
            # All filled and balanced (or close operation) - success
            return ValidationResult(
                passed=True,
                should_rollback=False,
                error_message=None,
                imbalance_tokens=imbalance_tokens,
                imbalance_pct=imbalance_pct,
                all_filled=True,
                rollback_cost=Decimal("0"),
            )
        
        # Not all filled - check for critical imbalance
        error_message = hedge_error or f"Partial fill: {filled_orders_count}/{len(orders)}"
        if not is_close_operation and imbalance_pct > imbalance_tolerance:
            self.logger.error(
                f"Quantity imbalance detected after hedge: longs={total_long_tokens:.6f} tokens, "
                f"shorts={total_short_tokens:.6f} tokens, imbalance={imbalance_tokens:.6f} tokens ({imbalance_pct*100:.2f}%)"
            )
            imbalance_msg = f"quantity imbalance {imbalance_tokens:.6f} tokens ({imbalance_pct*100:.2f}%)"
            error_message = f"{error_message}; {imbalance_msg}" if error_message else imbalance_msg
            
            # If we have a significant imbalance and rollback is enabled, close filled positions
            if rollback_on_partial and filled_orders_count > 0:
                is_critical, _, _ = self._imbalance_analyzer.check_critical_imbalance(total_long_tokens, total_short_tokens)
                if is_critical:
                    self.logger.warning(
                        f"⚠️ Critical quantity imbalance {imbalance_tokens:.6f} tokens ({imbalance_pct*100:.2f}%) "
                        f"detected after retries exhausted. Initiating rollback to close {filled_orders_count} filled positions."
                    )
                    return ValidationResult(
                        passed=False,
                        should_rollback=True,
                        error_message=f"Critical imbalance: {error_message}",
                        imbalance_tokens=imbalance_tokens,
                        imbalance_pct=imbalance_pct,
                        all_filled=False,
                        rollback_cost=Decimal("0"),  # Will be set by caller after rollback
                    )

        # Verify post-trade exposure for partial fills
        post_trade = await self._exposure_verifier.verify_post_trade_exposure(contexts)
        if post_trade is not None:
            net_qty = post_trade.get("net_qty", Decimal("0"))
            # Use net_qty for imbalance comparison (exposure verified purely on quantity)
            imbalance_tokens = max(imbalance_tokens, net_qty)
            if net_qty > Decimal("0"):
                # Calculate quantity imbalance percentage
                max_qty = max(total_long_tokens, total_short_tokens)
                net_qty_pct = net_qty / max_qty if max_qty > Decimal("0") else Decimal("0")
                if net_qty_pct > self._post_trade_max_imbalance_pct:
                    self.logger.warning(
                        "⚠️ Residual quantity exposure detected after partial execution: "
                        f"net_qty={net_qty:.6f} tokens ({net_qty_pct*100:.2f}%)."
                    )

        # Partial fill - failure
        return ValidationResult(
            passed=False,
            should_rollback=False,
            error_message=error_message,
            imbalance_tokens=imbalance_tokens,
            imbalance_pct=imbalance_pct,
            all_filled=False,
            rollback_cost=Decimal("0"),
        )

