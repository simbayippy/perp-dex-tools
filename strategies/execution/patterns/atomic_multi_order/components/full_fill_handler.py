"""
Full Fill Handler - handles scenarios when one leg fully fills.

This module extracts the logic for handling full fill triggers, including:
- Canceling sibling orders
- Calculating hedge targets with multiplier adjustments
- Executing aggressive limit hedge
- Verifying balance after hedge
- Determining if rollback is needed
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, List, Optional

from strategies.execution.core.utils import coerce_decimal

from ..contexts import OrderContext
from ..utils import apply_result_to_context, context_to_filled_dict, reconcile_context_after_cancel
from .hedge_manager import HedgeManager
from .imbalance_analyzer import ImbalanceAnalyzer
from .rollback_manager import RollbackManager


@dataclass
class FullFillResult:
    """Result of handling a full fill trigger."""
    
    success: bool
    error_message: Optional[str]
    rollback_performed: bool
    rollback_cost: Decimal
    contexts_cleared: bool


class FullFillHandler:
    """Handles full fill scenarios in atomic multi-order execution."""
    
    def __init__(
        self,
        hedge_manager: HedgeManager,
        rollback_manager: RollbackManager,
        imbalance_analyzer: ImbalanceAnalyzer,
        logger,
        executor: Optional[Any] = None,
    ):
        self._hedge_manager = hedge_manager
        self._rollback_manager = rollback_manager
        self._imbalance_analyzer = imbalance_analyzer
        self.logger = logger
        self._executor = executor  # For _is_order_fully_filled access
    
    def _is_order_fully_filled(
        self,
        ctx: OrderContext,
        tolerance: Decimal = Decimal("0.0001")
    ) -> bool:
        """
        Check if an order is actually fully filled (not just partially filled).
        
        Args:
            ctx: Order context to check
            tolerance: Tolerance for rounding differences
            
        Returns:
            True if order is fully filled, False otherwise
        """
        remaining_qty = ctx.remaining_quantity
        result_filled = ctx.result and ctx.result.get("filled", False)
        
        # Order is fully filled if:
        # 1. remaining_quantity is zero (or within tolerance for rounding)
        # 2. AND the result indicates it's filled (not just a partial fill timeout)
        return remaining_qty <= tolerance and result_filled
    
    def _create_error_result_dict(
        self,
        ctx: OrderContext,
        error: str
    ) -> dict:
        """
        Create an error result dictionary for a failed order task.
        
        Args:
            ctx: Order context that failed
            error: Error message
            
        Returns:
            Error result dictionary
        """
        return {
            "success": False,
            "filled": False,
            "error": error,
            "order_id": None,
            "exchange_client": ctx.spec.exchange_client,
            "symbol": ctx.spec.symbol,
            "side": ctx.spec.side,
            "slippage_usd": Decimal("0"),
            "execution_mode_used": "error",
            "filled_quantity": Decimal("0"),
            "fill_price": None,
        }
    
    async def handle(
        self,
        trigger_ctx: OrderContext,
        other_contexts: List[OrderContext],
        contexts: List[OrderContext],
        pending_tasks: set[asyncio.Task],
        rollback_on_partial: bool,
        stage_prefix: Optional[str] = None,
    ) -> FullFillResult:
        """
        Handle when one leg fully fills - cancel others and hedge.
        
        Args:
            trigger_ctx: The context that fully filled
            other_contexts: Other contexts to cancel/hedge
            contexts: All contexts (for rollback)
            pending_tasks: Set of pending tasks
            rollback_on_partial: Whether to rollback on partial fills
            stage_prefix: Optional stage prefix for logging
            
        Returns:
            FullFillResult with success status and rollback info
        """
        trigger_exchange = trigger_ctx.spec.exchange_client.get_exchange_name().upper()
        trigger_symbol = trigger_ctx.spec.symbol
        trigger_qty = trigger_ctx.filled_quantity
        
        self.logger.info(
            f"✅ {trigger_exchange} {trigger_symbol} fully filled ({trigger_qty}). "
            f"Cancelling remaining limit orders and hedging to prevent directional exposure."
        )
        
        # Cancel in-flight limits for the sibling legs.
        for ctx in other_contexts:
            exchange_name = ctx.spec.exchange_client.get_exchange_name().upper()
            symbol = ctx.spec.symbol
            self.logger.info(
                f"🔄 Cancelling limit order for {exchange_name} {symbol} "
                f"(remaining: {ctx.remaining_quantity}) → will hedge with market order"
            )
            ctx.cancel_event.set()

        # Wait for pending completions
        pending_contexts = [ctx for ctx in other_contexts if not ctx.completed]
        pending_completion = [ctx.task for ctx in pending_contexts]
        if pending_completion:
            gathered_results = await asyncio.gather(
                *pending_completion, return_exceptions=True
            )
            for ctx_result, ctx in zip(gathered_results, pending_contexts):
                previous_fill_ctx = ctx.filled_quantity
                if isinstance(ctx_result, Exception):  # pragma: no cover
                    self.logger.error(
                        f"Order task failed for {ctx.spec.symbol}: {ctx_result}"
                    )
                    result_dict = self._create_error_result_dict(ctx, str(ctx_result))
                else:
                    result_dict = ctx_result
                apply_result_to_context(ctx, result_dict, executor=self._executor)
                if ctx.filled_quantity > previous_fill_ctx:
                    # Note: newly_filled tracking happens in caller
                    pass

            # Update pending_tasks in place (remove completed tasks)
            pending_tasks.difference_update({task for task in pending_tasks if task.done()})

        # Calculate hedge target quantities with multiplier adjustments
        for ctx in other_contexts:
            await reconcile_context_after_cancel(ctx, self.logger)
            trigger_qty = trigger_ctx.filled_quantity
            if not isinstance(trigger_qty, Decimal):
                trigger_qty = Decimal(str(trigger_qty))
            trigger_qty = trigger_qty.copy_abs()

            # Account for quantity multipliers when matching across exchanges
            # Example: Lighter kTOSHI (84 units = 84k tokens) vs Aster TOSHI (84k units = 84k tokens)
            trigger_multiplier = trigger_ctx.spec.exchange_client.get_quantity_multiplier(trigger_ctx.spec.symbol)
            ctx_multiplier = ctx.spec.exchange_client.get_quantity_multiplier(ctx.spec.symbol)
            
            # Convert trigger quantity to "actual tokens" then to target exchange's units
            actual_tokens = trigger_qty * Decimal(str(trigger_multiplier))
            target_qty = actual_tokens / Decimal(str(ctx_multiplier))
            
            if trigger_multiplier != ctx_multiplier:
                self.logger.debug(
                    f"📊 Multiplier adjustment for {ctx.spec.symbol}: "
                    f"trigger_qty={trigger_qty} (×{trigger_multiplier}) → "
                    f"target_qty={target_qty} (×{ctx_multiplier})"
                )

            # Don't cap target_qty to spec.quantity when hedging after trigger fill
            # The trigger fill is the source of truth, and we need to match it exactly
            # (accounting for multipliers). spec.quantity might be from the original
            # order plan and could be wrong if there were rounding differences.
            # Only cap if target_qty exceeds spec.quantity significantly (safety check)
            spec_qty = getattr(ctx.spec, "quantity", None)
            if spec_qty is not None:
                spec_qty_dec = Decimal(str(spec_qty))
                # Only cap if target is significantly larger (more than 10% over)
                # This allows for small rounding differences but prevents huge errors
                if target_qty > spec_qty_dec * Decimal("1.1"):
                    self.logger.warning(
                        f"⚠️ [HEDGE] Calculated hedge target {target_qty} exceeds "
                        f"spec quantity {spec_qty_dec} by >10%. Capping to spec quantity."
                    )
                    target_qty = spec_qty_dec

            if target_qty < Decimal("0"):
                target_qty = Decimal("0")
            ctx.hedge_target_quantity = target_qty
            
            self.logger.debug(
                f"📊 [HEDGE] Set hedge_target_quantity for {ctx.spec.symbol}: "
                f"{target_qty} (trigger={trigger_qty}, multipliers={trigger_multiplier}×{ctx_multiplier})"
            )

        # Determine if this is a close operation (all orders have reduce_only=True)
        is_close_operation = (
            len(contexts) > 0 and 
            all(ctx.spec.reduce_only is True for ctx in contexts)
        )
        
        # For close operations, if both orders FULLY filled, positions are closed - no hedge needed
        # (Imbalance check is already skipped in _calculate_imbalance for closing operations)
        # CRITICAL: Must verify FULL fills, not just partial fills (filled_quantity > 0)
        if is_close_operation:
            fully_filled_count = sum(1 for ctx in contexts if self._is_order_fully_filled(ctx))
            if fully_filled_count == len(contexts):
                # Both close orders FULLY filled - positions are closed!
                self.logger.info(
                    f"✅ Close operation: Both orders fully filled, positions closed. No hedge needed."
                )
                return FullFillResult(
                    success=True,
                    error_message=None,
                    rollback_performed=False,
                    rollback_cost=Decimal("0"),
                    contexts_cleared=False,
                )
        
        # Execute aggressive limit hedge (with reduce_only flag for close operations)
        hedge_result = await self._hedge_manager.aggressive_limit_hedge(
            trigger_ctx, contexts, self.logger, reduce_only=is_close_operation, executor=self._executor
        )

        rollback_performed = False
        rollback_cost = Decimal("0")

        # CRITICAL: Check if both orders are fully filled and balanced BEFORE checking hedge_result.success
        # This prevents false rollback when both orders are filled and balanced, even if hedge reported failure
        # (hedge might fail due to timeout or other reasons, but orders might still be filled)
        fully_filled_count = sum(1 for ctx in contexts if self._is_order_fully_filled(ctx))
        all_fully_filled = fully_filled_count == len(contexts)
        
        if all_fully_filled:
            # Both orders fully filled - verify they're balanced
            total_long_tokens, total_short_tokens, imbalance_tokens, imbalance_pct = self._imbalance_analyzer.calculate_imbalance(contexts)
            imbalance_tolerance = Decimal("0.01")  # 1% tolerance
            
            if imbalance_pct <= imbalance_tolerance:
                self.logger.info(
                    f"✅ Both orders fully filled and balanced: "
                    f"longs={total_long_tokens:.6f}, shorts={total_short_tokens:.6f}, "
                    f"imbalance={imbalance_tokens:.6f} ({imbalance_pct*100:.2f}%). "
                    f"Returning success regardless of hedge_result.success status."
                )
                return FullFillResult(
                    success=True,
                    error_message=None,
                    rollback_performed=False,
                    rollback_cost=Decimal("0"),
                    contexts_cleared=False,
                )
            else:
                # Both fully filled but imbalanced - log warning
                self.logger.warning(
                    f"⚠️ Both orders fully filled but imbalanced: "
                    f"longs={total_long_tokens:.6f}, shorts={total_short_tokens:.6f}, "
                    f"imbalance={imbalance_tokens:.6f} ({imbalance_pct*100:.2f}%). "
                    f"Proceeding with hedge_result check..."
                )
        
        if hedge_result.success:
            # Hedge succeeded - return success
            if not all_fully_filled:
                # Hedge succeeded but not all orders fully filled - this is acceptable for hedge success
                self.logger.info(
                    f"✅ Hedge succeeded: {fully_filled_count}/{len(contexts)} orders fully filled"
                )
            return FullFillResult(
                success=True,
                error_message=None,
                rollback_performed=False,
                rollback_cost=Decimal("0"),
                contexts_cleared=False,
            )
        else:
            if not rollback_on_partial:
                return FullFillResult(
                    success=False,
                    error_message=hedge_result.error_message or "Hedge failure",
                    rollback_performed=False,
                    rollback_cost=Decimal("0"),
                    contexts_cleared=False,
                )
            else:
                self.logger.warning(
                    f"Hedge failed ({hedge_result.error_message or 'no error supplied'}) — attempting rollback of partial fills"
                )
                for ctx in contexts:
                    ctx.cancel_event.set()
                remaining = [ctx.task for ctx in contexts if not ctx.completed]
                if remaining:
                    await asyncio.gather(*remaining, return_exceptions=True)
                rollback_performed = True
                
                # Log context state for debugging
                for c in contexts:
                    if c.filled_quantity > Decimal("0"):
                        result_qty = Decimal("0")
                        if c.result:
                            result_qty = coerce_decimal(c.result.get("filled_quantity")) or Decimal("0")
                        self.logger.debug(
                            f"Rollback (hedge failure) context for {c.spec.symbol} ({c.spec.side}): "
                            f"accumulated={c.filled_quantity}, result_dict={result_qty}"
                        )
                
                # Safety check: Only rollback contexts with actual fills
                rollback_payload = []
                for c in contexts:
                    if c.filled_quantity > Decimal("0") and c.result:
                        # Additional safety: verify filled_quantity is reasonable
                        spec_qty = getattr(c.spec, "quantity", None)
                        if spec_qty is not None:
                            spec_qty_dec = Decimal(str(spec_qty))
                            # If filled_quantity exceeds spec.quantity significantly, something is wrong
                            if c.filled_quantity > spec_qty_dec * Decimal("1.1"):
                                self.logger.error(
                                    f"⚠️ ROLLBACK SKIP: {c.spec.symbol} ({c.spec.side}) has suspicious filled_quantity: "
                                    f"{c.filled_quantity} exceeds spec.quantity={spec_qty_dec} by >10%. "
                                    f"This likely indicates a bug. Skipping rollback for this context."
                                )
                                continue
                        
                        rollback_payload.append(context_to_filled_dict(c))
                
                # Use perform_emergency_rollback instead of rollback() directly
                # This ensures contexts are properly cleared to prevent double rollback
                rollback_cost = await self._rollback_manager.perform_emergency_rollback(
                    contexts=contexts,
                    reason="Hedge failure after full fill trigger",
                    imbalance_tokens=Decimal("0"),
                    imbalance_pct=Decimal("0"),
                    stage_prefix=stage_prefix
                )
                self.logger.warning(
                    f"Rollback completed after hedge failure; total cost ${rollback_cost:.4f}"
                )
                # Note: perform_emergency_rollback already clears contexts, no need to clear again
                
                return FullFillResult(
                    success=False,
                    error_message=hedge_result.error_message or "Hedge failure",
                    rollback_performed=True,
                    rollback_cost=rollback_cost,
                    contexts_cleared=True,  # perform_emergency_rollback clears contexts
                )

