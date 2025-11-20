"""
Execution State Manager - manages state and flow for atomic multi-order execution.

This module extracts the state machine logic for processing completed tasks,
classifying fills, and determining execution priorities.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Set

from strategies.execution.core.utils import coerce_decimal

from ..contexts import OrderContext
from ..utils import apply_result_to_context


@dataclass
class StateUpdate:
    """Update from processing a batch of completed tasks."""
    
    newly_filled: List[OrderContext]
    retryable_contexts: List[OrderContext]
    partial_fill_contexts: List[OrderContext]
    all_completed: bool
    
    @property
    def has_full_fill(self) -> bool:
        """Check if there's a newly filled order that's fully filled."""
        return len(self.newly_filled) > 0
    
    @property
    def has_partial_fill(self) -> bool:
        """Check if there's a partial fill that needs handling."""
        return len(self.partial_fill_contexts) > 0
    
    @property
    def has_retryable(self) -> bool:
        """Check if there are retryable failures."""
        return len(self.retryable_contexts) > 0


class ExecutionState:
    """Manages execution state for atomic multi-order execution."""
    
    def __init__(
        self,
        contexts: List[OrderContext],
        task_map: Dict[asyncio.Task, OrderContext],
        pending_tasks: Set[asyncio.Task],
        logger,
        executor: Optional[Any] = None,
    ):
        self.contexts = contexts
        self.task_map = task_map
        self.pending_tasks = pending_tasks
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
    
    async def process_next_batch(self) -> StateUpdate:
        """
        Process the next batch of completed tasks.
        
        Returns:
            StateUpdate with classified contexts
        """
        if not self.pending_tasks:
            return StateUpdate(
                newly_filled=[],
                retryable_contexts=[],
                partial_fill_contexts=[],
                all_completed=True,
            )
        
        done, self.pending_tasks = await asyncio.wait(
            self.pending_tasks, return_when=asyncio.FIRST_COMPLETED
        )
        
        newly_filled: List[OrderContext] = []
        retryable_contexts: List[OrderContext] = []
        partial_fill_contexts: List[OrderContext] = []

        for task in done:
            ctx = self.task_map[task]
            previous_fill = ctx.filled_quantity
            try:
                result = task.result()
            except Exception as exc:  # pragma: no cover - defensive
                self.logger.error(f"Order task failed for {ctx.spec.symbol}: {exc}")
                result = self._create_error_result_dict(ctx, str(exc))
            apply_result_to_context(ctx, result, executor=self._executor)
            
            # Check for retryable failures (post-only violations)
            if ctx.result and ctx.result.get("retryable", False):
                retryable_contexts.append(ctx)
            
            # Check for fills
            if ctx.filled_quantity > previous_fill:
                newly_filled.append(ctx)
            
            # Check for partial fills that have COMPLETED (timed out or canceled)
            # Only hedge partial fills when the order task is done, not while it's still active
            if ctx.completed and ctx.filled_quantity > Decimal("0"):
                is_fully_filled = self._is_order_fully_filled(ctx)
                # Only treat as partial fill if:
                # 1. Order has completed (timed out or canceled)
                # 2. Has fills (filled_quantity > 0)
                # 3. Not fully filled (remaining_quantity > 0)
                # 4. Not a retryable failure (post-only violations get retried, not hedged)
                if not is_fully_filled and not (ctx.result and ctx.result.get("retryable", False)):
                    partial_fill_contexts.append(ctx)

        all_completed = all(context.completed for context in self.contexts)
        
        return StateUpdate(
            newly_filled=newly_filled,
            retryable_contexts=retryable_contexts,
            partial_fill_contexts=partial_fill_contexts,
            all_completed=all_completed,
        )
    
    def is_complete(self) -> bool:
        """Check if execution is complete (all tasks done)."""
        return all(context.completed for context in self.contexts)
    
    def get_full_fill_trigger(self, newly_filled: List[OrderContext]) -> Optional[OrderContext]:
        """
        Get the first fully filled order from newly filled contexts.
        
        Args:
            newly_filled: List of newly filled contexts
            
        Returns:
            First fully filled context, or None if none found
        """
        for ctx in newly_filled:
            if self._is_order_fully_filled(ctx):
                return ctx
        return None
    
    def get_partial_fill(self, partial_fill_contexts: List[OrderContext]) -> Optional[OrderContext]:
        """
        Get the first partial fill context.
        
        Args:
            partial_fill_contexts: List of partial fill contexts
            
        Returns:
            First partial fill context, or None if empty
        """
        return partial_fill_contexts[0] if partial_fill_contexts else None
    
    def retry_context(
        self,
        ctx: OrderContext,
        place_order_func,
    ) -> None:
        """
        Retry a context by placing a new order.
        
        Args:
            ctx: Context to retry
            place_order_func: Function to place a new order (takes spec, cancel_event)
        """
        exchange_name = ctx.spec.exchange_client.get_exchange_name().upper()
        symbol = ctx.spec.symbol
        self.logger.info(
            f"🔄 [{exchange_name}] Post-only violation detected for {symbol}. "
            f"Retrying immediately with fresh BBO."
        )
        # Place new order with fresh BBO
        cancel_event = asyncio.Event()
        task = asyncio.create_task(place_order_func(ctx.spec, cancel_event=cancel_event))
        ctx.cancel_event = cancel_event
        ctx.task = task
        ctx.completed = False
        ctx.result = None
        self.task_map[task] = ctx
        self.pending_tasks.add(task)

