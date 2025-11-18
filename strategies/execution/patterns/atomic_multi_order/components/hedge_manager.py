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
            
            # remaining_usd is unreliable after cancellation (may be based on wrong spec.size_usd)
            # Only use it as fallback if remaining_qty is 0
            remaining_usd = ctx.remaining_usd
            
            if remaining_usd <= Decimal("0") and remaining_qty <= Decimal("0"):
                # CRITICAL: Detect suspicious scenario where we're skipping hedge after a trigger fill
                # This can happen if reconciliation incorrectly added a false fill for a canceled order
                # If trigger filled but remaining_qty=0, something is wrong
                trigger_filled = trigger_ctx.filled_quantity > Decimal("0")
                if trigger_filled and ctx.hedge_target_quantity is not None:
                    hedge_target = Decimal(str(ctx.hedge_target_quantity))
                    if hedge_target > Decimal("0"):
                        logger.warning(
                            f"⚠️ [HEDGE] {exchange_name} {spec.symbol}: Skipping hedge (remaining_qty=0, remaining_usd=0) "
                            f"but trigger {trigger_ctx.spec.exchange_client.get_exchange_name().upper()} "
                            f"{trigger_ctx.spec.symbol} filled {trigger_ctx.filled_quantity} and hedge_target={hedge_target}. "
                            f"ctx.filled_quantity={ctx.filled_quantity}. "
                            f"This suggests reconciliation may have incorrectly added fills for a canceled order. "
                            f"Check reconciliation logs for warnings."
                        )
                continue

            log_parts = []
            if remaining_qty > Decimal("0"):
                log_parts.append(f"qty={remaining_qty}")
            if remaining_usd > Decimal("0"):
                log_parts.append(f"${float(remaining_usd):.2f}")
            descriptor = ", ".join(log_parts) if log_parts else "0"
            logger.info(
                f"⚡ Hedging {spec.symbol} on {exchange_name} for remaining {descriptor}"
            )

            size_usd_arg: Optional[Decimal] = None
            quantity_arg: Optional[Decimal] = None
            try:
                # Always prioritize quantity over USD when hedging (more accurate)
                if remaining_qty > Decimal("0"):
                    quantity_arg = remaining_qty
                elif remaining_usd > Decimal("0"):
                    size_usd_arg = remaining_usd
                else:
                    continue
                
                execution = await hedge_executor.execute_order(
                    exchange_client=spec.exchange_client,
                    symbol=spec.symbol,
                    side=spec.side,
                    size_usd=size_usd_arg,
                    quantity=quantity_arg,
                    mode=ExecutionMode.MARKET_ONLY,
                    timeout_seconds=spec.timeout_seconds,
                    reduce_only=reduce_only,  # Use reduce_only when hedging close operations
                )
            except Exception as exc:  # pragma: no cover - defensive
                logger.error(f"Hedge order failed on {exchange_name}: {exc}")
                return False, str(exc)

            if not execution.success or not execution.filled:
                error = execution.error_message or f"Market hedge failed on {exchange_name}"
                logger.error(error)
                return False, error

            hedge_dict = execution_result_to_dict(spec, execution, hedge=True)
            apply_result_to_context(ctx, hedge_dict)

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
        - Shorter timeout (2s vs 4s) for faster exit
        - Fewer retries (5 vs 8) to avoid delay
        - Faster fallback to market orders
        
        Args:
            trigger_ctx: The context that triggered the hedge (fully filled order)
            contexts: All order contexts (for rollback if needed)
            logger: Logger instance
            reduce_only: If True, orders can only reduce existing positions (closing operation)
            max_retries: Maximum retry attempts (None = auto: 5 for closing, 8 for opening)
            retry_backoff_ms: Delay between retries in milliseconds (None = auto: 50ms for closing, 75ms for opening)
            total_timeout_seconds: Total timeout before market fallback (None = auto: 2.0s for closing, 4.0s for opening)
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
            total_timeout_seconds = total_timeout_seconds if total_timeout_seconds is not None else 2.0
            inside_tick_retries = inside_tick_retries if inside_tick_retries is not None else 2
        else:
            # For opening, prioritize slippage savings
            max_retries = max_retries if max_retries is not None else 8
            retry_backoff_ms = retry_backoff_ms if retry_backoff_ms is not None else 75
            total_timeout_seconds = total_timeout_seconds if total_timeout_seconds is not None else 4.0
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
            
            if ctx.hedge_target_quantity is not None:
                hedge_target = Decimal(str(ctx.hedge_target_quantity))
                remaining_qty = hedge_target - ctx.filled_quantity
                if remaining_qty < Decimal("0"):
                    remaining_qty = Decimal("0")
            else:
                remaining_qty = ctx.remaining_quantity
            
            remaining_usd = ctx.remaining_usd
            
            if remaining_usd <= Decimal("0") and remaining_qty <= Decimal("0"):
                continue

            log_parts = []
            if remaining_qty > Decimal("0"):
                log_parts.append(f"qty={remaining_qty}")
            if remaining_usd > Decimal("0"):
                log_parts.append(f"${float(remaining_usd):.2f}")
            descriptor = ", ".join(log_parts) if log_parts else "0"
            
            logger.info(
                f"⚡ Aggressive limit hedging {symbol} on {exchange_name} for remaining {descriptor}"
            )

            size_usd_arg: Optional[Decimal] = None
            quantity_arg: Optional[Decimal] = None
            if remaining_qty > Decimal("0"):
                quantity_arg = remaining_qty
            elif remaining_usd > Decimal("0"):
                size_usd_arg = remaining_usd
            else:
                continue

            # Aggressive limit order retry loop
            hedge_success = False
            hedge_error: Optional[str] = None
            
            for retry_count in range(max_retries):
                # Check total timeout
                elapsed_time = time.time() - start_time
                if elapsed_time >= total_timeout_seconds:
                    logger.warning(
                        f"⏱️ Aggressive limit hedge timeout after {elapsed_time:.2f}s for {exchange_name} {symbol}. "
                        f"Falling back to market order."
                    )
                    break

                try:
                    # Fetch fresh BBO
                    best_bid, best_ask = await self._price_provider.get_bbo_prices(
                        spec.exchange_client, symbol
                    )
                    
                    if best_bid <= Decimal("0") or best_ask <= Decimal("0"):
                        logger.warning(
                            f"⚠️ Invalid BBO for {exchange_name} {symbol}: bid={best_bid}, ask={best_ask}"
                        )
                        break
                    
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
                            # Use break-even price, but still apply adaptive pricing strategy
                            limit_price = break_even_price
                            pricing_strategy = "break_even"
                            logger.info(
                                f"✅ [{exchange_name}] Using break-even hedge price: {limit_price:.6f} "
                                f"< trigger {trigger_fill_price:.6f} for {symbol} "
                                f"(strategy: {break_even_strategy})"
                            )
                        else:
                            # Break-even not feasible, use BBO-based adaptive pricing
                            logger.info(
                                f"ℹ️ [{exchange_name}] Break-even not feasible for {symbol} "
                                f"(reason: {break_even_strategy}). Using BBO-based adaptive pricing "
                                f"to prioritize fill probability."
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
                    
                    # Round quantity to step size
                    if quantity_arg is not None:
                        order_quantity = spec.exchange_client.round_to_step(quantity_arg)
                    else:
                        # Calculate quantity from size_usd
                        order_quantity = (size_usd_arg / limit_price) if size_usd_arg else Decimal("0")
                        order_quantity = spec.exchange_client.round_to_step(order_quantity)
                    
                    if order_quantity <= Decimal("0"):
                        logger.warning(
                            f"⚠️ [{exchange_name}] Order quantity rounded to zero for {symbol}"
                        )
                        break
                    
                    strategy_info = f"{pricing_strategy}"
                    if break_even_strategy and break_even_strategy != pricing_strategy:
                        strategy_info += f" (break_even: {break_even_strategy})"
                    
                    logger.debug(
                        f"🔄 [{exchange_name}] Aggressive limit hedge attempt {retry_count + 1}/{max_retries} "
                        f"for {symbol}: {strategy_info} @ ${limit_price} qty={order_quantity} "
                        f"(best_bid=${best_bid}, best_ask=${best_ask}, tick_size={tick_size})"
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
                    
                    # Wait for fill with short timeout per attempt
                    attempt_timeout = min(1.0, (total_timeout_seconds - elapsed_time) / (max_retries - retry_count))
                    if attempt_timeout <= 0:
                        # Cancel order before breaking
                        try:
                            await spec.exchange_client.cancel_order(order_id)
                        except Exception:
                            pass
                        break
                    
                    # Poll for fill status
                    fill_start_time = time.time()
                    filled = False
                    filled_qty = Decimal("0")
                    fill_price: Optional[Decimal] = None
                    
                    while time.time() - fill_start_time < attempt_timeout:
                        try:
                            order_info = await spec.exchange_client.get_order_info(order_id)
                            if order_info:
                                if order_info.status == "FILLED":
                                    filled = True
                                    filled_qty = Decimal(str(order_info.filled_size)) if hasattr(order_info, 'filled_size') else order_quantity
                                    fill_price = Decimal(str(order_info.price)) if hasattr(order_info, 'price') else limit_price
                                    break
                                elif order_info.status in {"CANCELED", "CANCELLED"}:
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
                                        # Other cancellation, fallback to market
                                        hedge_error = f"Order cancelled: {cancel_reason}"
                                        filled = False
                                        break
                        except Exception as exc:
                            logger.debug(f"Error checking order status: {exc}")
                        
                        await asyncio.sleep(0.1)  # Poll every 100ms
                    
                    # Check if filled
                    if filled and filled_qty > Decimal("0"):
                        logger.info(
                            f"✅ [{exchange_name}] Aggressive limit hedge filled for {symbol} "
                            f"@ ${fill_price} qty={filled_qty} (attempt {retry_count + 1})"
                        )
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
                        hedge_success = True
                        break
                    elif not filled:
                        # Order not filled, cancel it and retry
                        try:
                            await spec.exchange_client.cancel_order(order_id)
                        except Exception:
                            pass
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
            
            # If aggressive limit hedge failed, fallback to market
            if not hedge_success:
                logger.warning(
                    f"⚠️ [{exchange_name}] Aggressive limit hedge exhausted for {symbol}. "
                    f"Falling back to market order."
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
            
        return True, None
