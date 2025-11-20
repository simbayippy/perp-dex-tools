"""Utility helpers for atomic multi-order execution."""

from __future__ import annotations

import inspect
from decimal import Decimal
from typing import Any, Dict, Optional

from strategies.execution.core.utils import coerce_decimal

from .contexts import OrderContext


def execution_result_to_dict(spec, execution_result, hedge: bool = False) -> Dict[str, Any]:
    """Normalise execution results into a shared dict structure."""
    data: Dict[str, Any] = {
        "success": execution_result.success,
        "filled": execution_result.filled,
        "fill_price": execution_result.fill_price,
        "filled_quantity": execution_result.filled_quantity,
        "slippage_usd": execution_result.slippage_usd,
        "execution_mode_used": execution_result.execution_mode_used,
        "order_id": execution_result.order_id,
        "exchange_client": spec.exchange_client,
        "symbol": spec.symbol,
        "side": spec.side,
        "retryable": getattr(execution_result, "retryable", False),
    }
    if hedge:
        data["hedge"] = True
    return data


def apply_result_to_context(
    ctx: OrderContext, 
    result: Dict[str, Any],
    executor: Optional[Any] = None
) -> None:
    """Persist an execution result onto the associated context.
    
    Records the fill to accumulate ctx.filled_quantity, then updates ctx.result
    to reflect the accumulated total (not just this order's fill).
    
    Also registers context with executor for websocket callback routing if executor
    is provided and order_id is available.
    
    Args:
        ctx: OrderContext to update
        result: Execution result dictionary
        executor: Optional AtomicMultiOrderExecutor instance for websocket callback registration
    """
    ctx.result = result.copy()  # Copy to avoid mutating original
    ctx.completed = True
    fill_qty = coerce_decimal(result.get("filled_quantity"))
    fill_price = coerce_decimal(result.get("fill_price"))
    ctx.record_fill(fill_qty, fill_price)
    
    # Update ctx.result to reflect accumulated total (may include multiple fills)
    # This ensures consistency between ctx.result and ctx.filled_quantity
    ctx.result["filled_quantity"] = ctx.filled_quantity
    
    # Register context with executor for websocket callback routing
    if executor is not None:
        order_id = result.get("order_id")
        if order_id:
            executor._register_order_context(ctx, str(order_id))


async def reconcile_context_after_cancel(ctx: OrderContext, logger) -> None:
    """
    After cancelling a limit order, fetch the final fill quantity.

    Some exchanges return partial fills even after cancellation, so we query the order
    info and update the context before hedging.
    
    If websocket already handled the cancellation (websocket_cancelled=True), skip
    reconciliation as websocket data is the source of truth.
    """
    # If websocket already handled cancellation, skip reconciliation
    if ctx.websocket_cancelled:
        logger.debug(
            f"Reconcile: {ctx.spec.symbol} order already handled by websocket callback. "
            f"Skipping reconciliation."
        )
        return
    
    # Use quantity check - USD tracking is unreliable after cancellations
    if ctx.remaining_quantity <= Decimal("0"):
        return

    result = ctx.result or {}
    order_id = result.get("order_id")
    if not order_id:
        return

    exchange_client = ctx.spec.exchange_client
    get_order_info = getattr(exchange_client, "get_order_info", None)
    if get_order_info is None:
        return

    # CRITICAL: Check websocket cache FIRST (without force_refresh) to get accurate fill data.
    # Websocket updates are the source of truth and show the actual filled_size (e.g., 0.000 for cancelled orders).
    # REST API may incorrectly calculate filled_size = order_size - remaining_size for cancelled orders.
    #
    # Exchange behavior varies:
    # - Lighter/Paradex: Return cached data immediately if available (when force_refresh=False)
    # - Aster/Backpack: Only return cached data if status is final (FILLED/CANCELED)
    # All exchanges have websocket caches (latest_orders) updated by websocket handlers.
    order_info = None
    try:
        # First, try to get order info from websocket cache (most accurate)
        # This will use the latest_orders cache which is updated by websocket handlers.
        # For Lighter/Paradex: Returns cached data immediately if available.
        # For Aster/Backpack: Returns cached data only if status is final (FILLED/CANCELED).
        order_info = await get_order_info(order_id)
        
        # CRITICAL: If we got CANCELED status from websocket cache, trust it completely
        # Websocket data is the source of truth - don't query REST API which may have incorrect data
        # This prevents false fills when REST API incorrectly calculates filled_size = size - remaining
        # for cancelled orders (where remaining=0, so filled_size=size, but no actual fills occurred)
        if order_info is not None:
            order_status = getattr(order_info, "status", "").upper()
            reported_qty = coerce_decimal(getattr(order_info, "filled_size", None)) or Decimal("0")
            
            # If order is CANCELED from websocket cache, trust the websocket data completely
            # Only reconcile if we have fills recorded in context that need to be verified
            if order_status == "CANCELED":
                if ctx.filled_quantity <= Decimal("0"):
                    # No fills in context and order is CANCELED - trust websocket (no fills occurred)
                    logger.debug(
                        f"Reconcile: {ctx.spec.symbol} order {order_id} is CANCELED with 0 fills "
                        f"(from websocket cache, reported_qty={reported_qty}). "
                        f"Trusting websocket data - skipping reconciliation to prevent false fills."
                    )
                    return
                else:
                    # We have fills in context - verify they match websocket data
                    # If websocket reports 0 fills but we have fills, something is wrong
                    if reported_qty <= Decimal("0"):
                        logger.warning(
                            f"⚠️ Reconcile: {ctx.spec.symbol} order {order_id} is CANCELED from websocket "
                            f"with reported_qty={reported_qty}, but context has fills={ctx.filled_quantity}. "
                            f"Trusting websocket data (no fills occurred)."
                        )
                        # Clear context fills since websocket says no fills
                        ctx.filled_quantity = Decimal("0")
                        return
                    # If websocket reports fills, use that (should match context)
                    if reported_qty <= ctx.filled_quantity:
                        logger.debug(
                            f"Reconcile: {ctx.spec.symbol} order {order_id} CANCELED with fills "
                            f"already accounted (websocket={reported_qty}, context={ctx.filled_quantity})"
                )
                return
        
        # If websocket cache doesn't have final status or we need fresh data, query REST API
        # This happens if:
        # - Cache doesn't have the order yet (websocket update hasn't arrived)
        # - Aster/Backpack: Cache has non-final status (OPEN/PARTIALLY_FILLED)
        # But we'll apply defensive checks to REST API data to catch incorrect filled_size
        if order_info is None or getattr(order_info, "status", "").upper() not in {"FILLED", "CANCELED", "CANCELLED"}:
            supports_force = False
            try:
                signature = inspect.signature(get_order_info)
                supports_force = "force_refresh" in signature.parameters
            except (TypeError, ValueError):  # pragma: no cover - defensive
                supports_force = False

            if supports_force:
                try:
                    order_info = await get_order_info(order_id, force_refresh=True)
                except TypeError:
                    order_info = await get_order_info(order_id)
            elif order_info is None:
                order_info = await get_order_info(order_id)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(
            f"⚠️ Failed to reconcile fill for {ctx.spec.symbol} after cancel: {exc}"
        )
        return

    if order_info is None:
        return

    # Check order status - if CANCELED with 0 fills, don't reconcile
    order_status = getattr(order_info, "status", "").upper()
    reported_qty = coerce_decimal(getattr(order_info, "filled_size", None)) or Decimal("0")
    remaining_size = coerce_decimal(getattr(order_info, "remaining_size", None)) or Decimal("0")
    spec_qty = getattr(ctx.spec, "quantity", None)
    spec_qty_dec = Decimal(str(spec_qty)) if spec_qty is not None else None
    
    # CRITICAL: If order is CANCELED and filled_size is 0 (from websocket or REST),
    # and we have no fills in context, skip reconciliation - order was cancelled without any fills
    if order_status == "CANCELED" and reported_qty <= Decimal("0") and ctx.filled_quantity <= Decimal("0"):
        logger.debug(
            f"Reconcile: {ctx.spec.symbol} order {order_id} is CANCELED with 0 fills "
            f"(reported_qty={reported_qty}, ctx.filled_quantity={ctx.filled_quantity}). "
            f"Skipping reconciliation - no fills to record."
        )
        return
    
    # CRITICAL: If order is CANCELED from REST API but we have no fills in context,
    # be very suspicious of reported_qty that matches spec_qty (likely incorrect REST API data)
    # This catches cases where REST API incorrectly calculates filled_size = size - remaining
    # for cancelled orders with remaining=0 (giving filled_size=size even though no fills occurred)
    if order_status == "CANCELED" and ctx.filled_quantity <= Decimal("0") and spec_qty_dec is not None:
        # Check if reported_qty is suspiciously close to spec_qty (within 5%)
        qty_diff_pct = abs(reported_qty - spec_qty_dec) / spec_qty_dec * Decimal("100") if spec_qty_dec > Decimal("0") else Decimal("100")
        if qty_diff_pct < Decimal("5") and remaining_size <= Decimal("0.0001"):
            logger.warning(
                f"⚠️ Reconcile: CANCELED order {order_id} from REST API reports filled_size={reported_qty} "
                f"(within {qty_diff_pct:.2f}% of spec.quantity={spec_qty_dec}), but context shows 0 fills "
                f"and remaining_size={remaining_size}. This suggests incorrect REST API data. "
                f"Skipping reconciliation to prevent false fills."
        )
        return
    
    # CRITICAL: If order is CANCELED and remaining_size is 0 (nothing remaining),
    # then filled_size should equal what was actually filled. If we have no fills recorded
    # in context but REST API reports filled_size close to spec_qty, this is suspicious.
    # This can happen when exchange clients incorrectly calculate filled_size = size - remaining
    # for canceled orders with 0 fills (where remaining=0, so filled_size=size, but no actual fills occurred).
    if order_status == "CANCELED":
        # CRITICAL FIX: For CANCELED orders, if remaining_size is 0 (or very small), we should be
        # very suspicious of filled_size that equals (or is close to) spec_qty. Many exchanges
        # incorrectly calculate filled_size = order_size - remaining_size, which for a cancelled
        # order with remaining_size=0 gives filled_size=order_size, even if no fills occurred.
        # 
        # We should skip reconciliation if:
        # 1. remaining_size is 0 (or very small, < 1% of spec_qty)
        # 2. AND reported_qty is close to spec_qty (within 10% - this catches exact matches and rounding)
        # 3. AND we have no fills recorded in context (ctx.filled_quantity <= 0)
        #
        # This prevents false fills from being recorded when an order was cancelled without any fills.
        if spec_qty_dec is not None and reported_qty is not None:
            remaining_pct = (remaining_size / spec_qty_dec * Decimal("100")) if spec_qty_dec > Decimal("0") else Decimal("100")
            reported_pct = (reported_qty / spec_qty_dec * Decimal("100")) if spec_qty_dec > Decimal("0") else Decimal("0")
            qty_diff_pct = abs(reported_qty - spec_qty_dec) / spec_qty_dec * Decimal("100") if spec_qty_dec > Decimal("0") else Decimal("100")
            
            # If remaining is very small (< 1% of spec) and reported_qty is close to spec_qty (within 10%),
            # and we have no fills recorded, this is likely incorrect REST API data
            if remaining_pct < Decimal("1") and qty_diff_pct < Decimal("10") and ctx.filled_quantity <= Decimal("0"):
                logger.warning(
                    f"⚠️ Reconcile: CANCELED order {order_id} reports filled_size={reported_qty} "
                    f"({reported_pct:.2f}% of spec.quantity={spec_qty_dec}, diff={qty_diff_pct:.2f}%), "
                    f"but context shows 0 fills and remaining_size={remaining_size} ({remaining_pct:.2f}% remaining). "
                    f"This suggests the exchange client incorrectly calculated filled_size for a canceled order. "
                    f"Skipping reconciliation to prevent false fills."
                )
                return
        
        # Additional check: If order is CANCELED and remaining_size is exactly 0 or very close to 0,
        # and reported_qty is close to spec_qty but we have no fills, skip reconciliation
        # This catches cases where the exact match check above might miss due to rounding
        if remaining_size <= Decimal("0.0001") and ctx.filled_quantity <= Decimal("0"):
            if spec_qty_dec is not None and reported_qty is not None:
                # If reported_qty is within 5% of spec_qty, it's suspicious for a canceled order with 0 fills
                qty_diff_pct = abs(reported_qty - spec_qty_dec) / spec_qty_dec * Decimal("100") if spec_qty_dec > Decimal("0") else Decimal("100")
                if qty_diff_pct < Decimal("5"):
                    logger.warning(
                        f"⚠️ Reconcile: CANCELED order {order_id} with remaining_size={remaining_size} "
                        f"reports filled_size={reported_qty} (within {qty_diff_pct:.2f}% of spec.quantity={spec_qty_dec}), "
                        f"but context shows 0 fills. Likely incorrect REST API data. Skipping reconciliation."
                    )
                    return
    
    if reported_qty is None or reported_qty <= Decimal("0"):
        # No fill reported, or zero fill - nothing to reconcile
        logger.debug(
            f"Reconcile: {ctx.spec.symbol} order {order_id} (status={order_status}) reported filled_size={reported_qty}, "
            f"current ctx.filled_quantity={ctx.filled_quantity}. No reconciliation needed."
        )
        return
    
    if reported_qty <= ctx.filled_quantity:
        # Already accounted for this fill (or less than what we have)
        logger.debug(
            f"Reconcile: {ctx.spec.symbol} order {order_id} reported filled_size={reported_qty} "
            f"<= current ctx.filled_quantity={ctx.filled_quantity}. No reconciliation needed."
        )
        return

    price_candidates = [
        getattr(order_info, attr, None)
        for attr in ("price", "average_price", "avg_price")
    ]
    reported_price = None
    for candidate in price_candidates:
        reported_price = coerce_decimal(candidate)
        if reported_price is not None:
            break

    additional = reported_qty - ctx.filled_quantity
    
    # Safety check: if additional is suspiciously large (more than spec quantity), log warning
    spec_qty = getattr(ctx.spec, "quantity", None)
    if spec_qty is not None:
        spec_qty_dec = Decimal(str(spec_qty))
        if additional > spec_qty_dec * Decimal("1.1"):  # More than 10% over expected
            logger.warning(
                f"⚠️ Reconcile: Suspicious fill detected for {ctx.spec.symbol} order {order_id}: "
                f"additional={additional} exceeds spec.quantity={spec_qty_dec} by >10%. "
                f"reported_qty={reported_qty}, ctx.filled_quantity={ctx.filled_quantity}. "
                f"Skipping reconciliation to prevent incorrect position tracking."
            )
            return
    
    logger.info(
        f"Reconcile: {ctx.spec.symbol} order {order_id} - adding fill: "
        f"additional={additional} @ {reported_price or 'unknown price'}, "
        f"total will be {ctx.filled_quantity + additional}"
    )
    ctx.record_fill(additional, reported_price)

    if ctx.result is None:
        ctx.result = {
            "success": True,
            "filled": True,
            "fill_price": reported_price,
            "filled_quantity": ctx.filled_quantity,  # Use accumulated total after record_fill
            "slippage_usd": Decimal("0"),
            "execution_mode_used": "limit",
            "order_id": order_id,
            "exchange_client": ctx.spec.exchange_client,
            "symbol": ctx.spec.symbol,
            "side": ctx.spec.side,
        }
    else:
        ctx.result["filled"] = True
        ctx.result["filled_quantity"] = ctx.filled_quantity  # Use accumulated total after record_fill
        if reported_price is not None:
            ctx.result["fill_price"] = reported_price


def context_to_filled_dict(ctx: OrderContext) -> Dict[str, Any]:
    """Convert a context into the structure expected by rollback helpers.
    
    CRITICAL: Always uses ctx.filled_quantity (accumulated total across all fills)
    instead of ctx.result["filled_quantity"] (which may only contain last order's fill).
    
    This ensures rollback closes the FULL accumulated position, not just the last order.
    """
    if ctx.result:
        # Use accumulated filled_quantity (may include multiple fills: initial + retry + hedge)
        # but preserve other fields from ctx.result (order_id, fill_price, etc.)
        result_dict = ctx.result.copy()
        result_dict["filled_quantity"] = ctx.filled_quantity  # Use accumulated total
        # Preserve reduce_only flag for rollback logic
        result_dict["reduce_only"] = getattr(ctx.spec, "reduce_only", False)
        return result_dict
    
    # Fallback if no result dict exists (shouldn't happen, but defensive)
    return {
        "success": True,
        "filled": True,
        "fill_price": None,
        "filled_quantity": ctx.filled_quantity,
        "slippage_usd": Decimal("0"),
        "execution_mode_used": "hedge",
        "order_id": None,
        "exchange_client": ctx.spec.exchange_client,
        "symbol": ctx.spec.symbol,
        "side": ctx.spec.side,
        "reduce_only": getattr(ctx.spec, "reduce_only", False),
    }
