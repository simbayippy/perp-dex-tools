"""Order polling and reconciliation utilities for order execution."""

from __future__ import annotations

import asyncio
import time
from decimal import Decimal
from typing import Optional, Tuple


class ReconciliationResult:
    """Result of order reconciliation."""
    
    def __init__(
        self,
        filled: bool,
        filled_qty: Decimal,
        fill_price: Optional[Decimal],
        accumulated_filled_qty: Decimal,
        current_order_filled_qty: Decimal,
        partial_fill_detected: bool,
        error: Optional[str] = None
    ):
        self.filled = filled
        self.filled_qty = filled_qty
        self.fill_price = fill_price
        self.accumulated_filled_qty = accumulated_filled_qty
        self.current_order_filled_qty = current_order_filled_qty
        self.partial_fill_detected = partial_fill_detected
        self.error = error


class OrderReconciler:
    """Handles order polling and reconciliation for order execution."""
    
    async def poll_order_until_filled(
        self,
        exchange_client,
        order_id: str,
        order_quantity: Decimal,
        limit_price: Decimal,
        target_quantity: Decimal,
        accumulated_filled_qty: Decimal,
        current_order_filled_qty: Decimal,
        attempt_timeout: float,
        pricing_strategy: str,
        retry_count: int,
        retry_backoff_ms: int,
        logger,
        exchange_name: str,
        symbol: str,
    ) -> ReconciliationResult:
        """
        Poll order for fill status, handling full fills, partial fills, and cancellations.
        
        This method continuously polls the order status until:
        - Order is fully filled (via single fill or accumulated partial fills)
        - Order is cancelled (with or without partial fills)
        - Timeout is reached
        
        Args:
            exchange_client: Exchange client instance
            order_id: Order ID to poll
            order_quantity: Original order quantity
            limit_price: Limit price used for the order
            target_quantity: Target quantity to fill
            accumulated_filled_qty: Accumulated fills across all retry attempts
            current_order_filled_qty: Fills for current order (to detect new fills)
            attempt_timeout: Timeout for this polling attempt
            pricing_strategy: Pricing strategy used (for logging)
            retry_count: Current retry count (for logging)
            retry_backoff_ms: Backoff delay in milliseconds
            logger: Logger instance
            exchange_name: Exchange name for logging
            symbol: Trading symbol
            
        Returns:
            ReconciliationResult with fill status and details
        """
        fill_start_time = time.time()
        filled = False
        filled_qty = Decimal("0")
        fill_price: Optional[Decimal] = None
        accumulated_fill_price: Optional[Decimal] = None
        partial_fill_detected_this_iteration = False
        error: Optional[str] = None
        
        while time.time() - fill_start_time < attempt_timeout:
            try:
                order_info = await exchange_client.get_order_info(order_id)
                if order_info:
                    # Check for filled_size (handles both full and partial fills)
                    order_filled_size = Decimal(str(order_info.filled_size)) if hasattr(order_info, 'filled_size') and order_info.filled_size else Decimal("0")
                    
                    if order_info.status == "FILLED":
                        # Fully filled - this order is complete
                        filled = True
                        # order_filled_size is the total filled for THIS order only
                        filled_qty = order_filled_size if order_filled_size > Decimal("0") else order_quantity
                        # Update accumulated_filled_qty by adding this order's fills
                        # (subtract what we've already counted for this order to avoid double-counting)
                        new_fills_from_order = filled_qty - current_order_filled_qty
                        if new_fills_from_order > Decimal("0"):
                            accumulated_filled_qty += new_fills_from_order
                            current_order_filled_qty = filled_qty
                        fill_price = Decimal(str(order_info.price)) if hasattr(order_info, 'price') else limit_price
                        break
                    elif order_info.status == "PARTIALLY_FILLED" or (order_info.status == "OPEN" and order_filled_size > Decimal("0")):
                        # Partial fill detected - accumulate and continue
                        # order_filled_size is the filled size for THIS order only (per-order, not cumulative)
                        # Track new fills from this order (increment since last check)
                        new_fills_from_order = order_filled_size - current_order_filled_qty
                        
                        if new_fills_from_order > Decimal("0"):
                            partial_fill_detected_this_iteration = True
                            # Add new fills to accumulated total
                            accumulated_filled_qty += new_fills_from_order
                            current_order_filled_qty = order_filled_size  # Update tracking for this order
                            accumulated_fill_price = Decimal(str(order_info.price)) if hasattr(order_info, 'price') else limit_price
                            
                            # Calculate total filled and remaining
                            remaining_after_partial = target_quantity - accumulated_filled_qty
                            logger.info(
                                f"📊 [{exchange_name}] Partial fill detected for {symbol}: "
                                f"+{new_fills_from_order} (total new: {accumulated_filled_qty}, "
                                f"total all: {accumulated_filled_qty}/{target_quantity}) @ ${accumulated_fill_price} "
                                f"(remaining: {remaining_after_partial})"
                            )
                            
                            # If fully filled via partial fills, break
                            if remaining_after_partial <= Decimal("0"):
                                filled = True
                                filled_qty = accumulated_filled_qty  # NEW fills only
                                fill_price = accumulated_fill_price
                                break
                            
                            # Cancel remaining quantity and place new order for remainder
                            try:
                                await exchange_client.cancel_order(order_id)
                                logger.debug(
                                    f"🔄 [{exchange_name}] Cancelled partially filled order {order_id} "
                                    f"to place new order for remaining {remaining_after_partial}"
                                )
                            except Exception as cancel_exc:
                                logger.warning(
                                    f"⚠️ [{exchange_name}] Failed to cancel partial fill order {order_id}: {cancel_exc}"
                                )
                            
                            # Break to retry with remaining quantity
                            break
                    elif order_info.status in {"CANCELED", "CANCELLED"}:
                        # Check for partial fills before cancellation
                        # order_filled_size is the total filled for THIS order
                        new_fills_from_order = order_filled_size - current_order_filled_qty
                        if new_fills_from_order > Decimal("0"):
                            accumulated_filled_qty += new_fills_from_order
                            current_order_filled_qty = order_filled_size
                            accumulated_fill_price = Decimal(str(order_info.price)) if hasattr(order_info, 'price') else limit_price
                            logger.info(
                                f"📊 [{exchange_name}] Partial fill before cancellation for {symbol}: "
                                f"+{new_fills_from_order} (total new: {accumulated_filled_qty}, "
                                f"total all: {accumulated_filled_qty}/{target_quantity}) @ ${accumulated_fill_price}"
                            )
                        
                        cancel_reason = getattr(order_info, 'cancel_reason', '') or ''
                        # Check if post-only violation
                        if "post" in cancel_reason.lower() or "post-only" in cancel_reason.lower():
                            logger.info(
                                f"🔄 [{exchange_name}] Post-only violation on attempt {retry_count + 1} for {symbol}. "
                                f"Retrying with fresh BBO ({pricing_strategy} strategy)."
                            )
                            await asyncio.sleep(retry_backoff_ms / 1000.0)
                            filled = False  # Will trigger retry
                            break
                        else:
                            # Other cancellation - check if we have enough fills
                            if accumulated_filled_qty >= target_quantity * Decimal("0.99"):  # 99% threshold
                                filled = True
                                filled_qty = accumulated_filled_qty  # NEW fills only
                                fill_price = accumulated_fill_price or limit_price
                            else:
                                error = f"Order cancelled: {cancel_reason}"
                                filled = False
                            break
            except Exception as exc:
                logger.debug(f"Error checking order status: {exc}")
            
            await asyncio.sleep(0.05)  # Poll every 50ms for faster response
        
        return ReconciliationResult(
            filled=filled,
            filled_qty=filled_qty,
            fill_price=fill_price,
            accumulated_filled_qty=accumulated_filled_qty,
            current_order_filled_qty=current_order_filled_qty,
            partial_fill_detected=partial_fill_detected_this_iteration,
            error=error
        )
    
    async def reconcile_final_state(
        self,
        exchange_client,
        order_id: str,
        last_known_fills: Decimal,
        accumulated_filled_qty: Decimal,
        accumulated_fill_price: Optional[Decimal],
        logger,
        exchange_name: str,
        symbol: str,
    ) -> Tuple[Decimal, Optional[Decimal]]:
        """
        Perform final reconciliation check for any fills that occurred after polling loop exited.
        
        This handles cases where an order was cancelled with fills after the polling timeout,
        ensuring we don't miss any partial fills.
        
        Args:
            exchange_client: Exchange client instance
            order_id: Order ID to check
            last_known_fills: Last known filled quantity for this order
            accumulated_filled_qty: Current accumulated fills across all orders
            accumulated_fill_price: Current accumulated fill price
            logger: Logger instance
            exchange_name: Exchange name for logging
            symbol: Trading symbol
            
        Returns:
            Tuple of (updated_accumulated_filled_qty, updated_accumulated_fill_price)
        """
        if not order_id:
            return accumulated_filled_qty, accumulated_fill_price
        
        try:
            final_order_info = await exchange_client.get_order_info(order_id)
            if final_order_info:
                final_filled_size = Decimal(str(final_order_info.filled_size)) if hasattr(final_order_info, 'filled_size') and final_order_info.filled_size else Decimal("0")
                # final_filled_size is the fills for the LAST order only
                # accumulated_filled_qty is the sum of fills from ALL orders
                # If final_filled_size > last_known_fills, we missed some fills from the last order
                # Add the difference to accumulated_filled_qty
                if final_filled_size > last_known_fills:
                    additional_fills = final_filled_size - last_known_fills
                    accumulated_filled_qty += additional_fills
                    if not accumulated_fill_price:
                        accumulated_fill_price = Decimal(str(final_order_info.price)) if hasattr(final_order_info, 'price') else None
                    logger.info(
                        f"📊 [{exchange_name}] Final reconciliation: Found {additional_fills} additional fills "
                        f"from last order (total accumulated: {accumulated_filled_qty}) for {symbol} "
                        f"that were missed during polling."
                    )
        except Exception as recon_exc:
            logger.debug(f"Final reconciliation check failed for order {order_id}: {recon_exc}")
        
        return accumulated_filled_qty, accumulated_fill_price

