"""Utility helpers for atomic multi-order execution."""

from __future__ import annotations

import inspect
from decimal import Decimal
from typing import Any, Dict, Optional

from .contexts import OrderContext


def coerce_decimal(value: Any) -> Optional[Decimal]:
    """Best-effort conversion to Decimal."""
    if isinstance(value, Decimal):
        return value
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:  # pragma: no cover - defensive
        return None


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


def apply_result_to_context(ctx: OrderContext, result: Dict[str, Any]) -> None:
    """Persist an execution result onto the associated context.
    
    Records the fill to accumulate ctx.filled_quantity, then updates ctx.result
    to reflect the accumulated total (not just this order's fill).
    """
    ctx.result = result.copy()  # Copy to avoid mutating original
    ctx.completed = True
    fill_qty = coerce_decimal(result.get("filled_quantity"))
    fill_price = coerce_decimal(result.get("fill_price"))
    ctx.record_fill(fill_qty, fill_price)
    
    # Update ctx.result to reflect accumulated total (may include multiple fills)
    # This ensures consistency between ctx.result and ctx.filled_quantity
    ctx.result["filled_quantity"] = ctx.filled_quantity


async def reconcile_context_after_cancel(ctx: OrderContext, logger) -> None:
    """
    After cancelling a limit order, fetch the final fill quantity.

    Some exchanges return partial fills even after cancellation, so we query the order
    info and update the context before hedging.
    """
    if ctx.remaining_usd <= Decimal("0"):
        return

    result = ctx.result or {}
    order_id = result.get("order_id")
    if not order_id:
        return

    exchange_client = ctx.spec.exchange_client
    get_order_info = getattr(exchange_client, "get_order_info", None)
    if get_order_info is None:
        return

    try:
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
        else:
            order_info = await get_order_info(order_id)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(
            f"⚠️ Failed to reconcile fill for {ctx.spec.symbol} after cancel: {exc}"
        )
        return

    if order_info is None:
        return

    reported_qty = coerce_decimal(getattr(order_info, "filled_size", None))
    if reported_qty is None or reported_qty <= Decimal("0"):
        # No fill reported, or zero fill - nothing to reconcile
        logger.debug(
            f"Reconcile: {ctx.spec.symbol} order {order_id} reported filled_size={reported_qty}, "
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
    }
