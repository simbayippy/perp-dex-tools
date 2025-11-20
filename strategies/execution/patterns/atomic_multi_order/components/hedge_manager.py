"""Hedging utilities for atomic multi-order execution."""

from __future__ import annotations

import asyncio
import time
from decimal import Decimal
from typing import List, Optional, Tuple

from strategies.execution.core.execution_types import ExecutionMode
from strategies.execution.core.price_alignment import BreakEvenPriceAligner

from ..contexts import OrderContext
from ..utils import apply_result_to_context, execution_result_to_dict


class HedgeManager:
    """Handles market hedges for partially filled atomic orders."""

    def __init__(self, price_provider=None) -> None:
        self._price_provider = price_provider

    async def hedge(
        self,
        trigger_ctx: OrderContext,
        contexts: List[OrderContext],
        logger,
        reduce_only: bool = False,
    ) -> Tuple[bool, Optional[str]]:
        """
        Attempt to flatten any residual exposure using market orders.

        Returns:
            Tuple of (success, error message).
        """
        # Lazy import to avoid circular dependency
        from strategies.execution.core.order_executor import OrderExecutor
        hedge_executor = OrderExecutor(price_provider=self._price_provider)

        for ctx in contexts:
            if ctx is trigger_ctx:
                continue

            spec = ctx.spec
            exchange_name = spec.exchange_client.get_exchange_name().upper()
            
            # CRITICAL: When hedging after a trigger fill, prioritize hedge_target_quantity
            # This ensures we hedge the correct amount to match the trigger fill, accounting
            # for quantity multipliers across exchanges.
            # Example: Aster fills 233960 TOSHI → Lighter should hedge 233.96 (233960/1000)
            remaining_qty = Decimal("0")
            
            # If hedge_target_quantity is set, use it directly (it's already calculated with multipliers)
            # This is the authoritative target quantity after accounting for cross-exchange multipliers
            if ctx.hedge_target_quantity is not None:
                hedge_target = Decimal(str(ctx.hedge_target_quantity))
                remaining_qty = hedge_target - ctx.filled_quantity
                if remaining_qty < Decimal("0"):
                    remaining_qty = Decimal("0")
                
                logger.debug(
                    f"📊 [HEDGE] {exchange_name} {spec.symbol}: "
                    f"hedge_target={hedge_target}, filled={ctx.filled_quantity}, "
                    f"remaining_qty={remaining_qty}"
                )
            else:
                # Fallback to remaining_quantity property (uses spec.quantity)
                remaining_qty = ctx.remaining_quantity
                logger.debug(
                    f"📊 [HEDGE] {exchange_name} {spec.symbol}: "
                    f"no hedge_target_quantity, using remaining_quantity={remaining_qty}"
                )
            
            # Always use quantity for hedging - USD tracking is unreliable after cancellations
            if remaining_qty <= Decimal("0"):
                # CRITICAL: Detect suspicious scenario where we're skipping hedge after a trigger fill
                # This can happen if reconciliation incorrectly added a false fill for a canceled order
                # If trigger filled but remaining_qty=0, something is wrong
                trigger_filled = trigger_ctx.filled_quantity > Decimal("0")
                if trigger_filled and ctx.hedge_target_quantity is not None:
                    hedge_target = Decimal(str(ctx.hedge_target_quantity))
                    if hedge_target > Decimal("0"):
                        logger.warning(
                            f"⚠️ [HEDGE] {exchange_name} {spec.symbol}: Skipping hedge (remaining_qty=0) "
                            f"but trigger {trigger_ctx.spec.exchange_client.get_exchange_name().upper()} "
                            f"{trigger_ctx.spec.symbol} filled {trigger_ctx.filled_quantity} and hedge_target={hedge_target}. "
                            f"ctx.filled_quantity={ctx.filled_quantity}. "
                            f"This suggests reconciliation may have incorrectly added fills for a canceled order. "
                            f"Check reconciliation logs for warnings."
                        )
                continue

            # Calculate USD estimate from quantity using latest BBO (for logging only, not decisions)
            estimated_usd = Decimal("0")
            try:
                if self._price_provider and remaining_qty > Decimal("0"):
                    best_bid, best_ask = await self._price_provider.get_bbo_prices(
                        spec.exchange_client, spec.symbol
                    )
                    price = best_ask if spec.side == "buy" else best_bid
                    estimated_usd = remaining_qty * price
            except Exception:
                pass  # Skip USD estimate if BBO unavailable

            logger.info(
                f"⚡ Hedging {spec.symbol} on {exchange_name}: "
                f"{remaining_qty} qty" + (f" (≈${float(estimated_usd):.2f})" if estimated_usd > Decimal("0") else "")
            )

            try:
                execution = await hedge_executor.execute_order(
                    exchange_client=spec.exchange_client,
                    symbol=spec.symbol,
                    side=spec.side,
                    quantity=remaining_qty,  # Always use quantity - more reliable than USD
                    mode=ExecutionMode.MARKET_ONLY,
                    timeout_seconds=spec.timeout_seconds,
                    reduce_only=reduce_only,  # Use reduce_only when hedging close operations
                )
            except Exception as exc:  # pragma: no cover - defensive
                logger.error(f"Hedge order failed on {exchange_name}: {exc}")
                return False, str(exc)

            # ⚠️ CRITICAL: Check for partial fills even when execution.success=False
            # Market orders can be cancelled after partial fills (e.g., slippage protection),
            # and we MUST track these partial fills for rollback to work correctly.
            # Without this, rollback will miss exposed positions!
            partial_fill_qty = execution.filled_quantity or Decimal("0")
            has_partial_fill = execution.filled and partial_fill_qty > Decimal("0")
            
            if has_partial_fill and not execution.success:
                # Partial fill occurred but order was cancelled - MUST record for rollback
                logger.warning(
                    f"⚠️ [{exchange_name}] Market hedge had partial fill ({partial_fill_qty} @ ${execution.fill_price or 'unknown'}) "
                    f"before cancellation. Recording for rollback tracking."
                )
                hedge_dict = execution_result_to_dict(spec, execution, hedge=True)
                apply_result_to_context(ctx, hedge_dict)
                # Still return error since hedge didn't complete successfully
                error = execution.error_message or f"Market hedge failed on {exchange_name} (partial fill: {partial_fill_qty})"
                logger.error(error)
                return False, error
            
            if not execution.success or not execution.filled:
                error = execution.error_message or f"Market hedge failed on {exchange_name}"
                logger.error(error)
                return False, error

            hedge_dict = execution_result_to_dict(spec, execution, hedge=True)
            apply_result_to_context(ctx, hedge_dict)
            
            # Track taker quantity (market order fills) for mixed execution type display
            # If we already have maker_qty from aggressive limit hedge, add taker_qty
            if ctx.result and "maker_qty" in ctx.result:
                # Market hedge filled the remaining quantity
                market_filled_qty = execution.filled_quantity or Decimal("0")
                ctx.result["taker_qty"] = market_filled_qty
                # Update total filled_quantity to include both maker and taker
                ctx.result["filled_quantity"] = ctx.filled_quantity
            elif ctx.result:
                # Pure market hedge (no aggressive limit hedge before)
                # All fills are taker
                ctx.result["maker_qty"] = Decimal("0")
                ctx.result["taker_qty"] = execution.filled_quantity or Decimal("0")

        return True, None

    async def aggressive_limit_hedge(
        self,
        trigger_ctx: OrderContext,
        contexts: List[OrderContext],
        logger,
        reduce_only: bool = False,
        max_retries: Optional[int] = None,
        retry_backoff_ms: Optional[int] = None,
        total_timeout_seconds: Optional[float] = None,
        inside_tick_retries: Optional[int] = None,
        max_deviation_pct: Optional[Decimal] = None,
    ) -> Tuple[bool, Optional[str]]:
        """
        Attempt to hedge using aggressive limit orders with adaptive pricing.
        
        Strategy:
        - Start with limit orders 1 tick inside spread (safer, avoids post-only violations)
        - After inside_tick_retries attempts, move to touch (at best bid/ask)
        - Retry on post-only violations with fresh BBO
        - Fallback to market orders if timeout or retries exhausted
        
        For closing operations (reduce_only=True), uses more aggressive settings:
        - Shorter timeout (3s vs 6s) for faster exit
        - Fewer retries (5 vs 8) to avoid delay
        - Faster fallback to market orders
        
        Args:
            trigger_ctx: The context that triggered the hedge (fully filled order)
            contexts: All order contexts (for rollback if needed)
            logger: Logger instance
            reduce_only: If True, orders can only reduce existing positions (closing operation)
            max_retries: Maximum retry attempts (None = auto: 5 for closing, 8 for opening)
            retry_backoff_ms: Delay between retries in milliseconds (None = auto: 50ms for closing, 75ms for opening)
            total_timeout_seconds: Total timeout before market fallback (None = auto: 3.0s for closing, 6.0s for opening)
            inside_tick_retries: Number of retries using "inside spread" pricing (None = auto: 2 for closing, 3 for opening)
            max_deviation_pct: Max market movement % to attempt break-even hedge (None = default: 0.5%)
            
        Returns:
            Tuple of (success, error message)
        """
        # Auto-configure parameters based on operation type
        # For closing (reduce_only=True), prioritize speed over slippage savings
        if reduce_only:
            max_retries = max_retries if max_retries is not None else 5
            retry_backoff_ms = retry_backoff_ms if retry_backoff_ms is not None else 50
            total_timeout_seconds = total_timeout_seconds if total_timeout_seconds is not None else 3.0
            inside_tick_retries = inside_tick_retries if inside_tick_retries is not None else 2
        else:
            # For opening, prioritize slippage savings but balance with delta neutrality speed
            max_retries = max_retries if max_retries is not None else 8
            retry_backoff_ms = retry_backoff_ms if retry_backoff_ms is not None else 75
            total_timeout_seconds = total_timeout_seconds if total_timeout_seconds is not None else 6.0
            inside_tick_retries = inside_tick_retries if inside_tick_retries is not None else 3
        
        # Lazy import to avoid circular dependency
        from strategies.execution.core.order_executor import OrderExecutor
        
        hedge_executor = OrderExecutor(price_provider=self._price_provider)
        start_time = time.time()
        
        for ctx in contexts:
            if ctx is trigger_ctx:
                continue

            spec = ctx.spec
            exchange_name = spec.exchange_client.get_exchange_name().upper()
            symbol = spec.symbol
            
            # Calculate remaining quantity (same logic as existing hedge method)
            remaining_qty = Decimal("0")
            hedge_target: Optional[Decimal] = None  # Initialize hedge_target
            
            if ctx.hedge_target_quantity is not None:
                hedge_target = Decimal(str(ctx.hedge_target_quantity))
                remaining_qty = hedge_target - ctx.filled_quantity
                if remaining_qty < Decimal("0"):
                    remaining_qty = Decimal("0")
            else:
                # Fallback: calculate hedge_target from spec.quantity for tracking purposes
                spec_quantity = getattr(ctx.spec, "quantity", None)
                if spec_quantity is not None:
                    hedge_target = Decimal(str(spec_quantity))
                remaining_qty = ctx.remaining_quantity
            
            # Always use quantity for hedging - USD tracking is unreliable after cancellations
            if remaining_qty <= Decimal("0"):
                continue

            # If hedge_target is still None, fail fast - we can't track partial fills properly
            if hedge_target is None:
                error_msg = (
                    f"Cannot determine hedge_target for {exchange_name} {symbol}. "
                    f"hedge_target_quantity and spec.quantity are both None. This indicates a configuration issue."
                )
                logger.error(f"❌ [{exchange_name}] {error_msg}")
                return False, error_msg

            # Calculate USD estimate from quantity using latest BBO (for logging only, not decisions)
            estimated_usd = Decimal("0")
            try:
                if self._price_provider and remaining_qty > Decimal("0"):
                    best_bid, best_ask = await self._price_provider.get_bbo_prices(
                        spec.exchange_client, symbol
                    )
                    price = best_ask if spec.side == "buy" else best_bid
                    estimated_usd = remaining_qty * price
            except Exception:
                pass  # Skip USD estimate if BBO unavailable
            
            logger.info("=" * 80)
            logger.info(
                f"⚡ AGGRESSIVE LIMIT HEDGE: {symbol} on {exchange_name}: "
                f"{remaining_qty} qty" + (f" (≈${float(estimated_usd):.2f})" if estimated_usd > Decimal("0") else "")
            )
            logger.info("=" * 80)

            # Aggressive limit order retry loop
            hedge_success = False
            hedge_error: Optional[str] = None
            # Track NEW fills during aggressive limit hedge (starts from 0, relative to ctx.filled_quantity)
            initial_filled_qty = ctx.filled_quantity  # Store initial state
            accumulated_filled_qty = Decimal("0")  # Track NEW partial fills across retries (sum of all orders)
            accumulated_fill_price: Optional[Decimal] = None
            last_order_filled_qty = Decimal("0")  # Track fills for last order (for final reconciliation)
            last_order_id: Optional[str] = None  # Track last order_id for final reconciliation
            
            for retry_count in range(max_retries):
                # Check total timeout
                elapsed_time = time.time() - start_time
                if elapsed_time >= total_timeout_seconds:
                    logger.info("-" * 80)
                    logger.warning(
                        f"⏱️ Aggressive limit hedge timeout after {elapsed_time:.2f}s for {exchange_name} {symbol}. "
                        f"Falling back to market order."
                    )
                    logger.info("-" * 80)
                    break

                # Reset partial fill detection flag for this retry iteration
                partial_fill_detected_this_iteration = False
                # Reset current order fill tracking for new order (local to this iteration)
                current_order_filled_qty = Decimal("0")

                try:
                    # Calculate hedge price using break-even or adaptive pricing
                    best_bid, best_ask, limit_price, pricing_strategy, break_even_strategy = await self._calculate_hedge_price(
                        spec=spec,
                        trigger_ctx=trigger_ctx,
                        retry_count=retry_count,
                        inside_tick_retries=inside_tick_retries,
                        max_deviation_pct=max_deviation_pct,
                        logger=logger,
                        exchange_name=exchange_name,
                        symbol=symbol,
                    )
                    
                    # Calculate remaining quantity after accumulated partial fills
                    # Total filled = initial fills + new fills from aggressive limit hedge
                    total_filled_qty = initial_filled_qty + accumulated_filled_qty
                    remaining_qty = hedge_target - total_filled_qty
                    if remaining_qty <= Decimal("0"):
                        # Already fully filled from previous partial fills
                        hedge_success = True
                        break
                    
                    # Round quantity to step size
                    # Always use remaining_qty (calculated from hedge_target) - more reliable than USD
                    order_quantity = spec.exchange_client.round_to_step(remaining_qty)
                    
                    if order_quantity <= Decimal("0"):
                        logger.warning(
                            f"⚠️ [{exchange_name}] Order quantity rounded to zero for {symbol} "
                            f"(accumulated_filled={accumulated_filled_qty}, hedge_target={hedge_target})"
                        )
                        # If we have accumulated fills, consider it success
                        if accumulated_filled_qty > Decimal("0"):
                            hedge_success = True
                        break
                    
                    strategy_info = f"{pricing_strategy}"
                    if break_even_strategy and break_even_strategy != pricing_strategy:
                        strategy_info += f" (break_even: {break_even_strategy})"
                    
                    logger.debug(
                        f"🔄 [{exchange_name}] Aggressive limit hedge attempt {retry_count + 1}/{max_retries} "
                        f"for {symbol}: {strategy_info} @ ${limit_price} qty={order_quantity} "
                        f"(best_bid=${best_bid}, best_ask=${best_ask})"
                    )
                    
                    # Place limit order directly with calculated price
                    contract_id = spec.exchange_client.resolve_contract_id(symbol)
                    order_result = await spec.exchange_client.place_limit_order(
                        contract_id=contract_id,
                        quantity=float(order_quantity),
                        price=float(limit_price),
                        side=spec.side,
                        reduce_only=reduce_only,
                    )
                    
                    if not order_result.success:
                        error_msg = order_result.error_message or f"Limit order placement failed on {exchange_name}"
                        logger.warning(
                            f"⚠️ [{exchange_name}] Limit order placement failed for {symbol}: {error_msg}"
                        )
                        # Check if it's a post-only violation (retryable)
                        # Most exchanges return this in error_message or cancel_reason
                        if "post" in error_msg.lower() or "post-only" in error_msg.lower():
                            logger.info(
                                f"🔄 [{exchange_name}] Post-only violation detected for {symbol}. "
                                f"Retrying with fresh BBO ({pricing_strategy} strategy)."
                            )
                            await asyncio.sleep(retry_backoff_ms / 1000.0)
                            continue
                        else:
                            # Non-retryable error, fallback to market
                            hedge_error = error_msg
                            break
                    
                    order_id = order_result.order_id
                    if not order_id:
                        logger.warning(
                            f"⚠️ [{exchange_name}] No order_id returned for {symbol}"
                        )
                        await asyncio.sleep(retry_backoff_ms / 1000.0)
                        continue
                    
                    # Track last order_id for final reconciliation
                    last_order_id = order_id
                    
                    # Wait for fill with timeout per attempt
                    # Give each attempt reasonable fixed time, but respect total timeout
                    remaining_timeout = total_timeout_seconds - elapsed_time
                    if remaining_timeout <= 0:
                        # Cancel order before breaking (if not already cancelled)
                        try:
                            order_status_check = await spec.exchange_client.get_order_info(order_id)
                            if order_status_check and order_status_check.status not in {"CANCELED", "CANCELLED", "FILLED"}:
                                await spec.exchange_client.cancel_order(order_id)
                        except Exception as cancel_exc:
                            # Order might already be cancelled - that's fine
                            error_str = str(cancel_exc).lower()
                            if "closed" not in error_str and "cancelled" not in error_str and "canceled" not in error_str:
                                logger.debug(f"Failed to cancel order {order_id} on timeout: {cancel_exc}")
                        break
                    # Use fixed 1.5s per attempt (or remaining timeout if less)
                    attempt_timeout = min(1.5, remaining_timeout)
                    
                    # Poll for fill status using helper method
                    (filled, filled_qty, fill_price, accumulated_filled_qty, 
                     current_order_filled_qty, partial_fill_detected_this_iteration, 
                     poll_hedge_error) = await self._poll_order_fill_status(
                        spec=spec,
                        order_id=order_id,
                        order_quantity=order_quantity,
                        limit_price=limit_price,
                        accumulated_filled_qty=accumulated_filled_qty,
                        current_order_filled_qty=current_order_filled_qty,
                        initial_filled_qty=initial_filled_qty,
                        hedge_target=hedge_target,
                        attempt_timeout=attempt_timeout,
                        pricing_strategy=pricing_strategy,
                        retry_count=retry_count,
                        retry_backoff_ms=retry_backoff_ms,
                        logger=logger,
                        exchange_name=exchange_name,
                        symbol=symbol,
                    )
                    
                    # Track last order's filled quantity for final reconciliation
                    last_order_filled_qty = current_order_filled_qty
                    
                    # Update hedge_error if poll returned an error
                    if poll_hedge_error:
                        hedge_error = poll_hedge_error
                    
                    # Update accumulated fill price if we got a fill price from poll
                    if fill_price:
                        accumulated_fill_price = fill_price
                    
                    # Check if filled (fully or via accumulated partial fills)
                    if filled and accumulated_filled_qty > Decimal("0"):
                        # Always use accumulated_filled_qty as source of truth for NEW fills
                        filled_qty = accumulated_filled_qty
                        # Use accumulated fill price, fallback to limit_price if None
                        fill_price = accumulated_fill_price or limit_price
                        
                        # Verify we've filled enough (within tolerance) - check TOTAL fills
                        total_filled_qty = initial_filled_qty + accumulated_filled_qty
                        if total_filled_qty >= hedge_target * Decimal("0.99"):  # 99% threshold for rounding
                            logger.info("=" * 80)
                            logger.info(
                                f"✅ AGGRESSIVE LIMIT HEDGE SUCCESS: [{exchange_name}] {symbol} "
                                f"@ ${fill_price} qty={filled_qty} new fills (total: {total_filled_qty}/{hedge_target}, "
                                f"attempt {retry_count + 1})"
                            )
                            logger.info("=" * 80)
                            # Create execution result dict compatible with execution_result_to_dict
                            # We need to create a dict-like object that matches ExecutionResult structure
                            from types import SimpleNamespace
                            execution_result = SimpleNamespace(
                                success=True,
                                filled=True,
                                fill_price=fill_price,
                                filled_quantity=filled_qty,
                                slippage_usd=Decimal("0"),  # Limit orders have minimal slippage
                                execution_mode_used=f"aggressive_limit_{pricing_strategy}",
                                order_id=order_id,
                                retryable=False,
                            )
                            hedge_dict = execution_result_to_dict(spec, execution_result, hedge=True)
                            apply_result_to_context(ctx, hedge_dict)
                            # Track maker quantity (all fills from aggressive limit hedge are maker)
                            # Include initial_filled_qty (from initial limit orders) as maker too
                            if not ctx.result:
                                ctx.result = {}
                            ctx.result["maker_qty"] = initial_filled_qty + accumulated_filled_qty
                            ctx.result["taker_qty"] = Decimal("0")  # Pure limit order fills
                            hedge_success = True
                            break
                        else:
                            # Partial fill but not enough - continue retrying for remainder
                            total_filled_qty = initial_filled_qty + filled_qty
                            logger.info(
                                f"📊 [{exchange_name}] Partial fill {filled_qty} new fills "
                                f"(total: {total_filled_qty}/{hedge_target}) for {symbol}. "
                                f"Continuing to fill remainder."
                            )
                            # Don't cancel here - partial_fill_detected_this_iteration already handled cancellation
                            if not partial_fill_detected_this_iteration:
                                # Only cancel if we didn't already cancel due to partial fill
                                try:
                                    await spec.exchange_client.cancel_order(order_id)
                                except Exception:
                                    pass
                            await asyncio.sleep(retry_backoff_ms / 1000.0)
                            continue
                    elif partial_fill_detected_this_iteration:
                        # Had partial fill but loop exited - continue retrying for remainder
                        total_filled_qty = initial_filled_qty + accumulated_filled_qty
                        logger.debug(
                            f"📊 [{exchange_name}] Partial fill {accumulated_filled_qty} new fills "
                            f"(total: {total_filled_qty}/{hedge_target}) for {symbol}. "
                            f"Retrying for remainder."
                        )
                        await asyncio.sleep(retry_backoff_ms / 1000.0)
                        continue
                    elif not filled:
                        # Order not filled, cancel it and retry
                        # But first check if it's already cancelled (e.g., post-only violation)
                        try:
                            order_status_check = await spec.exchange_client.get_order_info(order_id)
                            if order_status_check and order_status_check.status in {"CANCELED", "CANCELLED"}:
                                logger.debug(
                                    f"🔄 [{exchange_name}] Order {order_id} already cancelled, skipping cancel call"
                                )
                            else:
                                await spec.exchange_client.cancel_order(order_id)
                        except Exception as cancel_exc:
                            # Order might already be cancelled (e.g., post-only violation)
                            error_str = str(cancel_exc).lower()
                            if "closed" in error_str or "cancelled" in error_str or "canceled" in error_str:
                                logger.debug(
                                    f"🔄 [{exchange_name}] Order {order_id} already closed/cancelled: {cancel_exc}"
                                )
                            else:
                                logger.debug(f"Failed to cancel order {order_id}: {cancel_exc}")
                        # Check if we should continue retrying
                        if hedge_error:
                            break
                        await asyncio.sleep(retry_backoff_ms / 1000.0)
                        continue
                    
                except Exception as exc:
                    logger.error(
                        f"❌ [{exchange_name}] Aggressive limit hedge attempt {retry_count + 1} "
                        f"exception for {symbol}: {exc}"
                    )
                    hedge_error = str(exc)
                    # Continue to next retry or fallback
                    await asyncio.sleep(retry_backoff_ms / 1000.0)
            
            # Before falling back to market, do final reconciliation check for any orders
            # that might have partially filled after polling loop exited
            # This handles cases where order was cancelled with fills after polling timeout
            if not hedge_success:
                logger.info("-" * 80)
                logger.info(f"📋 Final Reconciliation Check: {symbol} on {exchange_name}")
                logger.info("-" * 80)
            
            if not hedge_success and last_order_id:
                try:
                    final_order_info = await spec.exchange_client.get_order_info(last_order_id)
                    if final_order_info:
                        final_filled_size = Decimal(str(final_order_info.filled_size)) if hasattr(final_order_info, 'filled_size') and final_order_info.filled_size else Decimal("0")
                        # final_filled_size is the fills for the LAST order only
                        # accumulated_filled_qty is the sum of fills from ALL orders
                        # If final_filled_size > last_order_filled_qty, we missed some fills from the last order
                        # Add the difference to accumulated_filled_qty
                        if final_filled_size > last_order_filled_qty:
                            additional_fills = final_filled_size - last_order_filled_qty
                            accumulated_filled_qty += additional_fills
                            if not accumulated_fill_price:
                                accumulated_fill_price = Decimal(str(final_order_info.price)) if hasattr(final_order_info, 'price') else None
                            logger.info(
                                f"📊 [{exchange_name}] Final reconciliation: Found {additional_fills} additional fills "
                                f"from last order (total accumulated: {accumulated_filled_qty}) for {symbol} "
                                f"that were missed during polling."
                            )
                except Exception as recon_exc:
                    logger.debug(f"Final reconciliation check failed for order {last_order_id}: {recon_exc}")
            
            # If aggressive limit hedge failed, fallback to market
            if not hedge_success:
                logger.info("=" * 80)
                logger.info(f"⚠️ AGGRESSIVE LIMIT HEDGE FAILED: Falling back to market order")
                logger.info("=" * 80)
                
                # Calculate remaining quantity after accumulated partial fills
                # Total filled = initial fills + new fills from aggressive limit hedge
                total_filled_qty = initial_filled_qty + accumulated_filled_qty
                remaining_after_partial = hedge_target - total_filled_qty
                
                if accumulated_filled_qty > Decimal("0"):
                    logger.info(
                        f"📊 [{exchange_name}] Aggressive limit hedge partial fills: {accumulated_filled_qty} new fills "
                        f"(total: {total_filled_qty}/{hedge_target}) for {symbol}. "
                        f"Falling back to market order for remaining {remaining_after_partial}."
                    )
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
                else:
                    logger.warning(
                        f"⚠️ [{exchange_name}] Aggressive limit hedge exhausted for {symbol}. "
                        f"Falling back to market order for full quantity {hedge_target} "
                        f"(current fills: {initial_filled_qty})."
                    )
                
                # Use existing market hedge method
                # Pass None as trigger_ctx since we're only hedging this specific context
                market_success, market_error = await self.hedge(
                    trigger_ctx=None,  # No trigger context for fallback
                    contexts=[ctx],  # Only hedge this specific context
                    logger=logger,
                    reduce_only=reduce_only,
                )
                if not market_success:
                    return False, market_error or f"Market hedge fallback failed for {exchange_name} {symbol}"
                else:
                    logger.info("=" * 80)
                    logger.info(f"✅ MARKET HEDGE FALLBACK COMPLETE: {symbol} on {exchange_name}")
                    logger.info("=" * 80)
            
        return True, None

    async def _calculate_hedge_price(
        self,
        spec,
        trigger_ctx: Optional[OrderContext],
        retry_count: int,
        inside_tick_retries: int,
        max_deviation_pct: Optional[Decimal],
        logger,
        exchange_name: str,
        symbol: str,
    ) -> Tuple[Decimal, Decimal, Decimal, str, Optional[str]]:
        """
        Calculate hedge price using break-even or adaptive pricing strategy.
        
        Returns:
            Tuple of (best_bid, best_ask, limit_price, pricing_strategy, break_even_strategy)
        """
        # Fetch fresh BBO
        best_bid, best_ask = await self._price_provider.get_bbo_prices(
            spec.exchange_client, symbol
        )
        
        if best_bid <= Decimal("0") or best_ask <= Decimal("0"):
            raise ValueError(f"Invalid BBO for {exchange_name} {symbol}: bid={best_bid}, ask={best_ask}")
        
        # Get tick_size with fallback
        tick_size = getattr(spec.exchange_client.config, 'tick_size', None)
        if tick_size is None:
            # Fallback: use 0.01% of price (1 basis point)
            tick_size = best_ask * Decimal('0.0001')
        else:
            tick_size = Decimal(str(tick_size))
        
        # Attempt break-even pricing relative to trigger fill price
        limit_price = None
        pricing_strategy = None
        break_even_strategy = None
        
        # Get trigger fill price if available
        trigger_fill_price = None
        trigger_side = None
        if trigger_ctx and trigger_ctx.result:
            trigger_fill_price_raw = trigger_ctx.result.get("fill_price")
            if trigger_fill_price_raw:
                try:
                    trigger_fill_price = Decimal(str(trigger_fill_price_raw))
                    trigger_side = trigger_ctx.spec.side
                except (ValueError, TypeError):
                    pass
        
        # Try break-even pricing if trigger fill price available
        if trigger_fill_price and trigger_side:
            # Use provided max_deviation_pct or default (0.5%)
            if max_deviation_pct is None:
                max_deviation_pct = BreakEvenPriceAligner.DEFAULT_MAX_DEVIATION_PCT
            
            break_even_price, break_even_strategy = BreakEvenPriceAligner.calculate_break_even_hedge_price(
                trigger_fill_price=trigger_fill_price,
                trigger_side=trigger_side,
                hedge_bid=best_bid,
                hedge_ask=best_ask,
                hedge_side=spec.side,
                tick_size=tick_size,
                max_deviation_pct=max_deviation_pct,
            )
            
            if break_even_strategy == "break_even":
                # Use break-even price
                limit_price = break_even_price
                pricing_strategy = "break_even"
                # Determine comparison operator based on sides
                if trigger_side == "buy" and spec.side == "sell":
                    comparison = "<"  # short < long
                elif trigger_side == "sell" and spec.side == "buy":
                    comparison = "<"  # long < short
                else:
                    comparison = "?"
                
                logger.info(
                    f"✅ [{exchange_name}] Using break-even hedge price: {limit_price:.6f} "
                    f"{comparison} trigger {trigger_fill_price:.6f} for {symbol} "
                )
            else:
                # Break-even not feasible, use BBO-based adaptive pricing
                logger.debug(
                    f"ℹ️ [{exchange_name}] Break-even not feasible for {symbol}. "
                    f"Using BBO-based pricing to prioritize fill probability"
                )
        
        # If break-even not attempted or not feasible, use adaptive pricing strategy
        if limit_price is None:
            if retry_count < inside_tick_retries:
                # Start inside spread (1 tick away from touch)
                pricing_strategy = "inside_spread"
                if spec.side == "buy":
                    limit_price = best_ask - tick_size
                else:
                    limit_price = best_bid + tick_size
            else:
                # Move to touch (at best bid/ask)
                pricing_strategy = "touch"
                if spec.side == "buy":
                    limit_price = best_ask
                else:
                    limit_price = best_bid
        
        # Round price to tick size
        limit_price = spec.exchange_client.round_to_tick(limit_price)
        
        return best_bid, best_ask, limit_price, pricing_strategy, break_even_strategy

    async def _poll_order_fill_status(
        self,
        spec,
        order_id: str,
        order_quantity: Decimal,
        limit_price: Decimal,
        accumulated_filled_qty: Decimal,
        current_order_filled_qty: Decimal,
        initial_filled_qty: Decimal,
        hedge_target: Decimal,
        attempt_timeout: float,
        pricing_strategy: str,
        retry_count: int,
        retry_backoff_ms: int,
        logger,
        exchange_name: str,
        symbol: str,
    ) -> Tuple[bool, Decimal, Optional[Decimal], Decimal, Decimal, bool, Optional[str]]:
        """
        Poll order for fill status, handling full fills, partial fills, and cancellations.
        
        Returns:
            Tuple of (filled, filled_qty, fill_price, accumulated_filled_qty, 
                     current_order_filled_qty, partial_fill_detected, hedge_error)
        """
        fill_start_time = time.time()
        filled = False
        filled_qty = Decimal("0")
        fill_price: Optional[Decimal] = None
        accumulated_fill_price: Optional[Decimal] = None
        partial_fill_detected_this_iteration = False
        hedge_error: Optional[str] = None
        
        while time.time() - fill_start_time < attempt_timeout:
            try:
                order_info = await spec.exchange_client.get_order_info(order_id)
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
                            
                            # Calculate total filled (initial + new) and remaining
                            total_filled_qty = initial_filled_qty + accumulated_filled_qty
                            remaining_after_partial = hedge_target - total_filled_qty
                            logger.info(
                                f"📊 [{exchange_name}] Partial fill detected for {symbol}: "
                                f"+{new_fills_from_order} (total new: {accumulated_filled_qty}, "
                                f"total all: {total_filled_qty}/{hedge_target}) @ ${accumulated_fill_price} "
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
                                await spec.exchange_client.cancel_order(order_id)
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
                            total_filled_qty = initial_filled_qty + accumulated_filled_qty
                            logger.info(
                                f"📊 [{exchange_name}] Partial fill before cancellation for {symbol}: "
                                f"+{new_fills_from_order} (total new: {accumulated_filled_qty}, "
                                f"total all: {total_filled_qty}/{hedge_target}) @ ${accumulated_fill_price}"
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
                            # Other cancellation - check if we have enough fills (total, not just new)
                            total_filled_qty = initial_filled_qty + accumulated_filled_qty
                            if total_filled_qty >= hedge_target * Decimal("0.99"):  # 99% threshold
                                filled = True
                                filled_qty = accumulated_filled_qty  # NEW fills only
                                fill_price = accumulated_fill_price or limit_price
                            else:
                                hedge_error = f"Order cancelled: {cancel_reason}"
                                filled = False
                            break
            except Exception as exc:
                logger.debug(f"Error checking order status: {exc}")
            
            await asyncio.sleep(0.1)  # Poll every 100ms
        
        return (filled, filled_qty, fill_price, accumulated_filled_qty, 
                current_order_filled_qty, partial_fill_detected_this_iteration, hedge_error)
