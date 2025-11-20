"""
Partial Fill Handler - handles scenarios when one leg partially fills.

This module extracts the logic for handling partial fill scenarios, including:
- Canceling other side
- Calculating hedge targets with multiplier adjustments
- Executing hedge with proper multiplier handling
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, List, Optional

from ..contexts import OrderContext
from ..utils import reconcile_context_after_cancel
from .hedge_manager import HedgeManager
from .rollback_manager import RollbackManager


@dataclass
class PartialFillResult:
    """Result of handling a partial fill."""
    
    success: bool
    error_message: Optional[str]
    trigger_context: OrderContext
    rollback_performed: bool
    rollback_cost: Decimal


class PartialFillHandler:
    """Handles partial fill scenarios in atomic multi-order execution."""
    
    def __init__(
        self,
        hedge_manager: HedgeManager,
        rollback_manager: RollbackManager,
        logger,
        executor: Optional[Any] = None,
    ):
        self._hedge_manager = hedge_manager
        self._rollback_manager = rollback_manager
        self.logger = logger
        self._executor = executor
    
    async def handle(
        self,
        partial_ctx: OrderContext,
        contexts: List[OrderContext],
        rollback_on_partial: bool,
        stage_prefix: Optional[str] = None,
    ) -> PartialFillResult:
        """
        Handle partial fill scenario - cancel other side and hedge.
        
        Args:
            partial_ctx: The context that partially filled
            contexts: All contexts
            rollback_on_partial: Whether to rollback on partial fills
            stage_prefix: Optional stage prefix for logging
            
        Returns:
            PartialFillResult with success status and rollback info
        """
        exchange_name = partial_ctx.spec.exchange_client.get_exchange_name().upper()
        symbol = partial_ctx.spec.symbol
        filled_qty = partial_ctx.filled_quantity
        self.logger.info(
            f"⚡ [{exchange_name}] Partial fill completed (timed out/canceled) for {symbol} ({filled_qty}). "
            f"Cancelling other side and hedging immediately."
        )
        
        # Cancel other contexts
        other_contexts = [c for c in contexts if c is not partial_ctx]
        for other_ctx in other_contexts:
            other_ctx.cancel_event.set()
        
        # Wait for cancellations
        pending_cancels = [c.task for c in other_contexts if not c.completed]
        if pending_cancels:
            await asyncio.gather(*pending_cancels, return_exceptions=True)
        
        # Reconcile after cancel
        for other_ctx in other_contexts:
            await reconcile_context_after_cancel(other_ctx, self.logger)
        
        # Set hedge target for other contexts (accounting for multipliers)
        for other_ctx in other_contexts:
            trigger_qty = partial_ctx.filled_quantity
            if not isinstance(trigger_qty, Decimal):
                trigger_qty = Decimal(str(trigger_qty))
            trigger_qty = trigger_qty.copy_abs()
            
            trigger_multiplier = partial_ctx.spec.exchange_client.get_quantity_multiplier(partial_ctx.spec.symbol)
            ctx_multiplier = other_ctx.spec.exchange_client.get_quantity_multiplier(other_ctx.spec.symbol)
            
            actual_tokens = trigger_qty * Decimal(str(trigger_multiplier))
            target_qty = actual_tokens / Decimal(str(ctx_multiplier))
            
            spec_qty = getattr(other_ctx.spec, "quantity", None)
            if spec_qty is not None:
                spec_qty_dec = Decimal(str(spec_qty))
                if target_qty > spec_qty_dec * Decimal("1.1"):
                    target_qty = spec_qty_dec
            
            if target_qty < Decimal("0"):
                target_qty = Decimal("0")
            other_ctx.hedge_target_quantity = target_qty
        
        # Hedge immediately with aggressive limit orders
        hedge_result = await self._hedge_manager.aggressive_limit_hedge(
            partial_ctx, contexts, self.logger, executor=self._executor
        )
        
        if hedge_result.success:
            return PartialFillResult(
                success=True,
                error_message=None,
                trigger_context=partial_ctx,
                rollback_performed=False,
                rollback_cost=Decimal("0"),
            )
        else:
            rollback_performed = False
            rollback_cost = Decimal("0")
            
            if rollback_on_partial:
                rollback_performed = True
                rollback_cost = await self._rollback_manager.perform_emergency_rollback(
                    contexts, "Partial fill hedge failure", 
                    Decimal("0"), Decimal("0"), stage_prefix=stage_prefix
                )
            
            return PartialFillResult(
                success=False,
                error_message=hedge_result.error_message or "Partial fill hedge failure",
                trigger_context=partial_ctx,
                rollback_performed=rollback_performed,
                rollback_cost=rollback_cost,
            )

