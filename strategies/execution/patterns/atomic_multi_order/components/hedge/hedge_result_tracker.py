"""Hedge result tracking utilities for atomic multi-order execution."""

from __future__ import annotations

from decimal import Decimal
from typing import Optional, Any, Tuple

from ...contexts import OrderContext
from ...utils import apply_result_to_context, execution_result_to_dict


class HedgeResultTracker:
    """Tracks hedge execution results and updates context consistently."""
    
    def apply_market_hedge_result(
        self,
        ctx: OrderContext,
        execution_result: Any,
        spec: Any,
        initial_maker_qty: Optional[Decimal] = None,
        executor: Optional[Any] = None
    ) -> None:
        """
        Apply market hedge execution result to context with maker/taker tracking.
        
        Args:
            ctx: Order context to update
            execution_result: ExecutionResult from OrderExecutor
            spec: OrderSpec for the hedge order
            initial_maker_qty: Maker quantity from previous aggressive limit hedge (if any)
            executor: Optional AtomicMultiOrderExecutor instance for websocket callback registration
        """
        hedge_dict = execution_result_to_dict(spec, execution_result, hedge=True)
        apply_result_to_context(ctx, hedge_dict, executor=executor)
        
        # Track taker quantity (market order fills) for mixed execution type display
        # If we already have maker_qty from aggressive limit hedge, add taker_qty
        if ctx.result:
            if initial_maker_qty is not None and initial_maker_qty > Decimal("0"):
                # Market hedge filled the remaining quantity after aggressive limit hedge
                market_filled_qty = execution_result.filled_quantity or Decimal("0")
                ctx.result["taker_qty"] = market_filled_qty
                ctx.result["maker_qty"] = initial_maker_qty
                # Update total filled_quantity to include both maker and taker
                ctx.result["filled_quantity"] = ctx.filled_quantity
            elif "maker_qty" not in ctx.result:
                # Pure market hedge (no aggressive limit hedge before)
                # All fills are taker
                ctx.result["maker_qty"] = Decimal("0")
                ctx.result["taker_qty"] = execution_result.filled_quantity or Decimal("0")
    
    def apply_aggressive_limit_hedge_result(
        self,
        ctx: OrderContext,
        execution_result: Any,
        spec: Any,
        initial_filled_qty: Decimal,
        accumulated_filled_qty: Decimal,
        fill_price: Decimal,
        order_id: str,
        pricing_strategy: str,
        executor: Optional[Any] = None
    ) -> None:
        """
        Apply aggressive limit hedge execution result to context.
        
        Args:
            ctx: Order context to update
            execution_result: ExecutionResult-like object (can be SimpleNamespace)
            spec: OrderSpec for the hedge order
            initial_filled_qty: Initial fills before aggressive limit hedge started
            accumulated_filled_qty: New fills from aggressive limit hedge
            fill_price: Fill price for the hedge
            order_id: Order ID
            pricing_strategy: Pricing strategy used (for execution_mode)
            executor: Optional AtomicMultiOrderExecutor instance for websocket callback registration
        """
        hedge_dict = execution_result_to_dict(spec, execution_result, hedge=True)
        apply_result_to_context(ctx, hedge_dict, executor=executor)
        
        # Track maker quantity (all fills from aggressive limit hedge are maker)
        # Include initial_filled_qty (from initial limit orders) as maker too
        if not ctx.result:
            ctx.result = {}
        ctx.result["maker_qty"] = initial_filled_qty + accumulated_filled_qty
        ctx.result["taker_qty"] = Decimal("0")  # Pure limit order fills
    
    def track_partial_fills_before_market_fallback(
        self,
        ctx: OrderContext,
        initial_filled_qty: Decimal,
        accumulated_filled_qty: Decimal,
        accumulated_fill_price: Optional[Decimal],
        total_filled_qty: Decimal
    ) -> None:
        """
        Track partial fills from aggressive limit hedge before falling back to market.
        
        This updates the context with partial fills so the market hedge can calculate
        the correct remaining quantity.
        
        Args:
            ctx: Order context to update
            initial_filled_qty: Initial fills before aggressive limit hedge started
            accumulated_filled_qty: New fills from aggressive limit hedge
            accumulated_fill_price: Fill price from accumulated fills
            total_filled_qty: Total fills (initial + accumulated)
        """
        # Update ctx.filled_quantity by ADDING new fills (don't overwrite initial fills)
        # This ensures market hedge calculates remaining quantity correctly
        ctx.filled_quantity = total_filled_qty  # Initial + new fills
        
        if accumulated_fill_price:
            # Also update result if available to track partial fills
            if not ctx.result:
                ctx.result = {}
            ctx.result["fill_price"] = accumulated_fill_price
            ctx.result["filled_quantity"] = total_filled_qty  # Total fills
            # Track maker quantity (limit order fills)
            # Include initial_filled_qty (from initial limit orders) as maker too
            ctx.result["maker_qty"] = initial_filled_qty + accumulated_filled_qty
            ctx.result["taker_qty"] = Decimal("0")  # Will be updated after market fallback
    
    def track_partial_fill(
        self,
        accumulated_fills: Decimal,
        new_fill_qty: Decimal,
        new_fill_price: Decimal,
        accumulated_price: Optional[Decimal]
    ) -> Tuple[Decimal, Decimal]:
        """
        Track accumulated partial fills across retries.
        
        Args:
            accumulated_fills: Current accumulated fills
            new_fill_qty: New fill quantity from this order
            new_fill_price: Fill price from this order
            accumulated_price: Current accumulated fill price
            
        Returns:
            Tuple of (updated_accumulated_fills, updated_accumulated_price)
        """
        updated_fills = accumulated_fills + new_fill_qty
        
        # Update accumulated price (use new price if we don't have one, or average)
        if accumulated_price is None:
            updated_price = new_fill_price
        else:
            # Use weighted average if we have both prices
            # For simplicity, use the new price (could be improved with weighted average)
            updated_price = new_fill_price
        
        return updated_fills, updated_price

