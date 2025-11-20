"""Aggressive limit order execution strategy with retries and adaptive pricing."""

from __future__ import annotations

import asyncio
import time
from decimal import Decimal
from typing import Optional

from exchange_clients import BaseExchangeClient

from ..execution_types import ExecutionResult
from ..price_provider import PriceProvider
from .base import ExecutionStrategy
from ..execution_components.pricer import AggressiveLimitPricer
from ..execution_components.reconciler import OrderReconciler
from helpers.unified_logger import get_core_logger


class AggressiveLimitExecutionStrategy(ExecutionStrategy):
    """Aggressive limit order execution with retries and adaptive pricing."""
    
    def __init__(
        self,
        price_provider=None,
        pricer: Optional[AggressiveLimitPricer] = None,
        reconciler: Optional[OrderReconciler] = None,
        market_fallback: Optional[ExecutionStrategy] = None,
        use_websocket_events: bool = True,
    ):
        """
        Initialize aggressive limit execution strategy.
        
        Args:
            price_provider: Optional PriceProvider for BBO price retrieval
            pricer: Optional AggressiveLimitPricer instance
            reconciler: Optional OrderReconciler instance (fallback if websockets not available)
            market_fallback: Optional MarketExecutionStrategy for fallback
            use_websocket_events: If True, use event-based reconciler (faster). Falls back to polling if not supported.
        """
        # Initialize base class with websocket support
        super().__init__(use_websocket_events=use_websocket_events)
        
        self._price_provider = price_provider or PriceProvider()
        self._pricer = pricer or AggressiveLimitPricer(price_provider=self._price_provider)
        self._reconciler = reconciler or OrderReconciler()  # Fallback polling reconciler
        self.logger = get_core_logger("aggressive_limit_execution_strategy")
        
        # Lazy import to avoid circular dependency
        if market_fallback is None:
            from .market import MarketExecutionStrategy
            self._market_fallback = MarketExecutionStrategy(price_provider=self._price_provider)
        else:
            self._market_fallback = market_fallback
    
    async def execute(
        self,
        exchange_client: BaseExchangeClient,
        symbol: str,
        side: str,
        quantity: Optional[Decimal] = None,
        size_usd: Optional[Decimal] = None,
        reduce_only: bool = False,
        max_retries: Optional[int] = None,
        retry_backoff_ms: Optional[int] = None,
        total_timeout_seconds: Optional[float] = None,
        inside_tick_retries: Optional[int] = None,
        max_deviation_pct: Optional[Decimal] = None,
        trigger_fill_price: Optional[Decimal] = None,
        trigger_side: Optional[str] = None,
        logger=None,
        **kwargs
    ) -> ExecutionResult:
        """
        Execute order using aggressive limit orders with adaptive pricing.
        
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
            exchange_client: Exchange client instance
            symbol: Trading pair (e.g., "BTC-PERP")
            side: "buy" or "sell"
            quantity: Order quantity
            size_usd: Order size in USD (alternative to quantity)
            reduce_only: If True, orders can only reduce existing positions
            max_retries: Maximum retry attempts (None = auto: 5 for closing, 8 for opening)
            retry_backoff_ms: Delay between retries in milliseconds (None = auto: 50ms for closing, 75ms for opening)
            total_timeout_seconds: Total timeout before market fallback (None = auto: 3.0s for closing, 6.0s for opening)
            inside_tick_retries: Number of retries using "inside spread" pricing (None = auto: 2 for closing, 3 for opening)
            max_deviation_pct: Max market movement % to attempt break-even pricing (None = default: 0.5%)
            trigger_fill_price: Optional fill price from trigger order (for break-even pricing)
            trigger_side: Optional side of trigger order ("buy" or "sell")
            logger: Optional logger instance (uses default if None)
            **kwargs: Additional strategy-specific parameters
            
        Returns:
            ExecutionResult with execution details
        """
        if logger is None:
            logger = self.logger
        
        # Auto-configure parameters based on operation type
        if reduce_only:
            max_retries = max_retries if max_retries is not None else 8  # Increased from 5
            retry_backoff_ms = retry_backoff_ms if retry_backoff_ms is not None else 30  # Reduced from 50ms
            total_timeout_seconds = total_timeout_seconds if total_timeout_seconds is not None else 8.0  # Increased from 3.0s
            inside_tick_retries = inside_tick_retries if inside_tick_retries is not None else 2
        else:
            max_retries = max_retries if max_retries is not None else 15  # Increased from 12 to allow more attempts
            retry_backoff_ms = retry_backoff_ms if retry_backoff_ms is not None else 30  # Reduced from 75ms
            total_timeout_seconds = total_timeout_seconds if total_timeout_seconds is not None else 13.0  # Increased from 6.0s to allow more attempts
            inside_tick_retries = inside_tick_retries if inside_tick_retries is not None else 3
        
        exchange_name = exchange_client.get_exchange_name().upper()
        
        # Calculate quantity if not provided
        if quantity is None:
            if size_usd is None:
                return ExecutionResult(
                    success=False,
                    filled=False,
                    error_message="AggressiveLimitExecutionStrategy requires quantity or size_usd",
                    execution_mode_used="aggressive_limit_error"
                )
            # Get price for size_usd calculation
            try:
                best_bid, best_ask = await self._price_provider.get_bbo_prices(exchange_client, symbol)
                price = best_ask if side == "buy" else best_bid
                quantity = Decimal(str(size_usd)) / price
            except Exception as e:
                return ExecutionResult(
                    success=False,
                    filled=False,
                    error_message=f"Failed to calculate quantity from size_usd: {e}",
                    execution_mode_used="aggressive_limit_error"
                )
        
        quantity = Decimal(str(quantity))
        target_quantity = quantity  # Target quantity to fill
        
        if quantity <= Decimal("0"):
            return ExecutionResult(
                success=True,
                filled=True,
                filled_quantity=Decimal("0"),
                execution_mode_used="aggressive_limit_skip"
            )
        
        # Calculate USD estimate for logging
        estimated_usd = Decimal("0")
        try:
            if quantity > Decimal("0"):
                best_bid, best_ask = await self._price_provider.get_bbo_prices(
                    exchange_client, symbol
                )
                price = best_ask if side == "buy" else best_bid
                estimated_usd = quantity * price
        except Exception:
            pass
        
        logger.info("=" * 80)
        logger.info(
            f"⚡ AGGRESSIVE LIMIT EXECUTION: {symbol} on {exchange_name}: "
            f"{quantity} qty" + (f" (≈${float(estimated_usd):.2f})" if estimated_usd > Decimal("0") else "")
        )
        logger.info("=" * 80)
        
        # Register websocket callback for event-based tracking (if enabled and supported)
        use_event_based = self._register_websocket_callback(exchange_client)
        
        if not use_event_based:
            logger.debug(f"ℹ️ Using polling-based order tracking (websocket events not available)")
        
        try:
            # Track fills during aggressive limit execution
            start_time = time.time()
            execution_success = False
            execution_error: Optional[str] = None
            accumulated_filled_qty = Decimal("0")
            accumulated_fill_price: Optional[Decimal] = None
            last_order_filled_qty = Decimal("0")
            last_order_id: Optional[str] = None
            retries_used = 0
            last_pricing_strategy = "unknown"  # Track pricing strategy for final result
            
            for retry_count in range(max_retries):
                # Check total timeout before each attempt
                elapsed_time = time.time() - start_time
                if elapsed_time >= total_timeout_seconds:
                    logger.info("-" * 80)
                    logger.warning(
                        f"⏱️ Aggressive limit execution timeout after {elapsed_time:.2f}s for {exchange_name} {symbol} "
                        f"(placed {retry_count} orders, {retries_used} retries). Falling back to market order."
                    )
                    logger.info("-" * 80)
                    break
                
                current_order_filled_qty = Decimal("0")
                
                try:
                    # Calculate remaining quantity after accumulated partial fills
                    # CHECK THIS FIRST before calculating prices or placing orders
                    remaining_qty = target_quantity - accumulated_filled_qty
                    if remaining_qty <= Decimal("0"):
                        logger.info(
                            f"✅ [{exchange_name}] Target quantity fully filled for {symbol}: "
                            f"accumulated={accumulated_filled_qty}/{target_quantity}. "
                            f"Stopping retries (attempt {retry_count + 1})"
                        )
                        execution_success = True
                        break
                    
                    # Calculate price using pricer
                    price_result = await self._pricer.calculate_aggressive_limit_price(
                        exchange_client=exchange_client,
                        symbol=symbol,
                        side=side,
                        retry_count=retry_count,
                        inside_tick_retries=inside_tick_retries,
                        max_deviation_pct=max_deviation_pct,
                        trigger_fill_price=trigger_fill_price,
                        trigger_side=trigger_side,
                        logger=logger,
                    )
                    last_pricing_strategy = price_result.pricing_strategy  # Track for final result
                    
                    # Round quantity to step size
                    order_quantity = exchange_client.round_to_step(remaining_qty)
                    
                    if order_quantity <= Decimal("0"):
                        logger.warning(
                            f"⚠️ [{exchange_name}] Order quantity rounded to zero for {symbol} "
                            f"(accumulated_filled={accumulated_filled_qty}, target_quantity={target_quantity})"
                        )
                        if accumulated_filled_qty > Decimal("0"):
                            execution_success = True
                        break
                    
                    strategy_info = f"{price_result.pricing_strategy}"
                    if price_result.break_even_strategy and price_result.break_even_strategy != price_result.pricing_strategy:
                        strategy_info += f" (break_even: {price_result.break_even_strategy})"
                    
                    logger.debug(
                        f"🔄 [{exchange_name}] Aggressive limit execution attempt {retry_count + 1}/{max_retries} "
                        f"for {symbol}: {strategy_info} @ ${price_result.limit_price} qty={order_quantity} "
                        f"(best_bid=${price_result.best_bid}, best_ask=${price_result.best_ask})"
                    )
                    
                    # Place limit order
                    contract_id = exchange_client.resolve_contract_id(symbol)
                    order_result = await exchange_client.place_limit_order(
                        contract_id=contract_id,
                        quantity=float(order_quantity),
                        price=float(price_result.limit_price),
                        side=side,
                        reduce_only=reduce_only,
                    )
                    
                    if not order_result.success:
                        error_msg = order_result.error_message or f"Limit order placement failed on {exchange_name}"
                        logger.warning(f"⚠️ [{exchange_name}] Limit order placement failed for {symbol}: {error_msg}")
                        
                        # Check for retryable errors (post-only violations, expired orders, etc.)
                        error_lower = error_msg.lower()
                        is_retryable = (
                            "post" in error_lower or 
                            "post-only" in error_lower or
                            "expired" in error_lower or
                            "did not remain open" in error_lower or
                            "gtx" in error_lower
                        )
                        
                        if is_retryable:
                            logger.info(
                                f"🔄 [{exchange_name}] Retryable order rejection detected for {symbol}: {error_msg}. "
                                f"Retrying with adaptive pricing ({price_result.pricing_strategy} strategy, attempt {retry_count + 1}/{max_retries})."
                            )
                            await asyncio.sleep(retry_backoff_ms / 1000.0)
                            retries_used += 1
                            continue
                        else:
                            # Only fatal errors break the loop (e.g., insufficient balance, invalid symbol)
                            logger.error(
                                f"❌ [{exchange_name}] Fatal order placement error for {symbol}: {error_msg}. "
                                f"Stopping retries."
                            )
                            execution_error = error_msg
                            break
                    
                    order_id = order_result.order_id
                    if not order_id:
                        logger.warning(f"⚠️ [{exchange_name}] No order_id returned for {symbol}")
                        await asyncio.sleep(retry_backoff_ms / 1000.0)
                        retries_used += 1
                        continue
                    
                    last_order_id = order_id
                    
                    # Wait for fill with timeout per attempt
                    remaining_timeout = total_timeout_seconds - elapsed_time
                    if remaining_timeout <= 0:
                        try:
                            order_status_check = await exchange_client.get_order_info(order_id)
                            if order_status_check and order_status_check.status not in {"CANCELED", "CANCELLED", "FILLED"}:
                                await exchange_client.cancel_order(order_id)
                        except Exception:
                            pass
                        break
                    
                    # attempt_timeout: Time to wait for order to fill after placement
                    # This is just the wait time, not including price calc or order placement
                    attempt_timeout = min(2, remaining_timeout)
                    
                    # Wait for fill status using event-based reconciler (if available) or polling reconciler
                    if use_event_based and self._can_use_websocket_events(exchange_client):
                        # Use websocket events for instant response
                        recon_result = await self._event_reconciler.wait_for_order_event(
                            exchange_client=exchange_client,
                            order_id=order_id,
                            order_quantity=order_quantity,
                            limit_price=price_result.limit_price,
                            target_quantity=target_quantity,
                            accumulated_filled_qty=accumulated_filled_qty,
                            current_order_filled_qty=current_order_filled_qty,
                            attempt_timeout=attempt_timeout,
                            pricing_strategy=price_result.pricing_strategy,
                            retry_count=retry_count,
                            retry_backoff_ms=retry_backoff_ms,
                            logger=logger,
                            exchange_name=exchange_name,
                            symbol=symbol,
                        )
                    else:
                        # Fallback to polling reconciler
                        recon_result = await self._reconciler.poll_order_until_filled(
                            exchange_client=exchange_client,
                            order_id=order_id,
                            order_quantity=order_quantity,
                            limit_price=price_result.limit_price,
                            target_quantity=target_quantity,
                            accumulated_filled_qty=accumulated_filled_qty,
                            current_order_filled_qty=current_order_filled_qty,
                            attempt_timeout=attempt_timeout,
                            pricing_strategy=price_result.pricing_strategy,
                            retry_count=retry_count,
                            retry_backoff_ms=retry_backoff_ms,
                            logger=logger,
                            exchange_name=exchange_name,
                            symbol=symbol,
                        )
                    
                    last_order_filled_qty = recon_result.current_order_filled_qty
                    accumulated_filled_qty = recon_result.accumulated_filled_qty
                    
                    # DEBUG: Log reconciliation result for debugging
                    logger.info(
                        f"🔍 [{exchange_name}] Reconciliation result for {symbol}: "
                        f"filled={recon_result.filled}, "
                        f"filled_qty={recon_result.filled_qty}, "
                        f"accumulated={accumulated_filled_qty}/{target_quantity}, "
                        f"partial={recon_result.partial_fill_detected}, "
                        f"error={recon_result.error}"
                    )
                    
                    # Only set execution_error for fatal errors, not retryable cancellations
                    # "Order cancelled without fills" is retryable - should not break the loop
                    if recon_result.error and recon_result.error != "Order cancelled without fills":
                        execution_error = recon_result.error
                    
                    if recon_result.fill_price:
                        accumulated_fill_price = recon_result.fill_price
                    
                    # Log cancellation reason for debugging
                    if recon_result.error == "Order cancelled without fills":
                        # Try to get cancellation reason from order info
                        try:
                            order_status_check = await exchange_client.get_order_info(order_id)
                            if order_status_check:
                                cancel_reason = getattr(order_status_check, 'cancel_reason', None)
                                if cancel_reason:
                                    logger.info(
                                        f"🔄 [{exchange_name}] Order {order_id} cancelled without fills for {symbol}. "
                                        f"Reason: {cancel_reason}. Retrying with adaptive pricing."
                                    )
                                else:
                                    logger.info(
                                        f"🔄 [{exchange_name}] Order {order_id} cancelled without fills for {symbol}. "
                                        f"Retrying with adaptive pricing (attempt {retry_count + 1}/{max_retries})."
                                    )
                            else:
                                logger.info(
                                    f"🔄 [{exchange_name}] Order {order_id} cancelled without fills for {symbol}. "
                                    f"Retrying with adaptive pricing (attempt {retry_count + 1}/{max_retries})."
                                )
                        except Exception:
                            logger.info(
                                f"🔄 [{exchange_name}] Order {order_id} cancelled without fills for {symbol}. "
                                f"Retrying with adaptive pricing (attempt {retry_count + 1}/{max_retries})."
                            )
                    
                    # Check if filled
                    if recon_result.filled and accumulated_filled_qty > Decimal("0"):
                        filled_qty = accumulated_filled_qty
                        fill_price = accumulated_fill_price or price_result.limit_price
                        
                        logger.info(
                            f"✅ [{exchange_name}] Order filled for {symbol}: "
                            f"accumulated={accumulated_filled_qty}, target={target_quantity}, "
                            f"threshold={target_quantity * Decimal('0.99')}"
                        )
                        
                        if accumulated_filled_qty >= target_quantity * Decimal("0.99"):  # 99% threshold
                            logger.info("=" * 80)
                            logger.info(
                                f"✅ AGGRESSIVE LIMIT EXECUTION SUCCESS: [{exchange_name}] {symbol} "
                                f"@ ${fill_price} qty={filled_qty} fills (total: {accumulated_filled_qty}/{target_quantity}, "
                                f"attempt {retry_count + 1})"
                            )
                            logger.info("=" * 80)
                            
                            execution_success = True
                            retries_used = retry_count + 1
                            break
                        else:
                            # Partial fill but not enough - continue retrying
                            logger.info(
                                f"📊 [{exchange_name}] Partial fill {filled_qty} fills "
                                f"(total: {accumulated_filled_qty}/{target_quantity}) for {symbol}. "
                                f"Continuing to fill remainder."
                            )
                            if not recon_result.partial_fill_detected:
                                try:
                                    await exchange_client.cancel_order(order_id)
                                except Exception:
                                    pass
                            await asyncio.sleep(retry_backoff_ms / 1000.0)
                            retries_used += 1
                            continue
                    elif recon_result.partial_fill_detected:
                        # Had partial fill but loop exited - continue retrying
                        logger.debug(
                            f"📊 [{exchange_name}] Partial fill {accumulated_filled_qty} fills "
                            f"(total: {accumulated_filled_qty}/{target_quantity}) for {symbol}. "
                            f"Retrying for remainder."
                        )
                        await asyncio.sleep(retry_backoff_ms / 1000.0)
                        retries_used += 1
                        continue
                    elif not recon_result.filled:
                        # Order not filled - check if it was cancelled and get reason
                        cancellation_reason = None
                        try:
                            order_status_check = await exchange_client.get_order_info(order_id)
                            if order_status_check:
                                if order_status_check.status in {"CANCELED", "CANCELLED"}:
                                    cancellation_reason = getattr(order_status_check, 'cancel_reason', None)
                                    logger.info(
                                        f"🔄 [{exchange_name}] Order {order_id} cancelled without fills for {symbol}. "
                                        f"Reason: {cancellation_reason or 'unknown'}. "
                                        f"Retrying with adaptive pricing (attempt {retry_count + 1}/{max_retries})"
                                    )
                                elif order_status_check.status not in {"FILLED"}:
                                    logger.debug(
                                        f"🔄 [{exchange_name}] Order {order_id} not filled after {attempt_timeout}s, "
                                        f"canceling and retrying (attempt {retry_count + 1}/{max_retries})"
                                    )
                                    await exchange_client.cancel_order(order_id)
                            else:
                                logger.debug(
                                    f"🔄 [{exchange_name}] Order {order_id} status unknown. "
                                    f"Retrying (attempt {retry_count + 1}/{max_retries})"
                                )
                        except Exception as cancel_exc:
                            logger.debug(f"⚠️ [{exchange_name}] Exception during cancel check: {cancel_exc}")
                        
                        # Only break on fatal errors, not retryable cancellations
                        # "Order cancelled without fills" is retryable - continue loop
                        if execution_error and execution_error != "Order cancelled without fills":
                            logger.warning(
                                f"⚠️ [{exchange_name}] Fatal error detected: {execution_error}. "
                                f"Stopping retries for {symbol}"
                            )
                            break
                        
                        await asyncio.sleep(retry_backoff_ms / 1000.0)
                        retries_used += 1
                        continue
                
                except Exception as exc:
                    logger.error(
                        f"❌ [{exchange_name}] Aggressive limit execution attempt {retry_count + 1} "
                        f"exception for {symbol}: {exc}"
                    )
                    execution_error = str(exc)
                    await asyncio.sleep(retry_backoff_ms / 1000.0)
                    retries_used += 1
            
            # Final reconciliation check (safety check for any missed fills)
            # Use polling reconciler for this one-time check after retries exhausted
            if not execution_success and last_order_id:
                reconciler_to_use = self._reconciler  # Always use polling for final check
                accumulated_filled_qty, accumulated_fill_price = await reconciler_to_use.reconcile_final_state(
                    exchange_client=exchange_client,
                    order_id=last_order_id,
                    last_known_fills=last_order_filled_qty,
                    accumulated_filled_qty=accumulated_filled_qty,
                    accumulated_fill_price=accumulated_fill_price,
                    logger=logger,
                    exchange_name=exchange_name,
                    symbol=symbol,
                )
            
            # Fallback to market if limit execution failed
            if not execution_success:
                logger.info("=" * 80)
                logger.info(f"⚠️ AGGRESSIVE LIMIT EXECUTION FAILED: Falling back to market order")
                logger.info("=" * 80)
                
                remaining_after_partial = target_quantity - accumulated_filled_qty
                
                if accumulated_filled_qty > Decimal("0"):
                    logger.info(
                        f"📊 [{exchange_name}] Aggressive limit execution partial fills: {accumulated_filled_qty} fills "
                        f"(total: {accumulated_filled_qty}/{target_quantity}) for {symbol}. "
                        f"Falling back to market order for remaining {remaining_after_partial}."
                    )
                
                # Fallback to market execution
                market_result = await self._market_fallback.execute(
                    exchange_client=exchange_client,
                    symbol=symbol,
                    side=side,
                    quantity=remaining_after_partial if remaining_after_partial > Decimal("0") else quantity,
                    reduce_only=reduce_only,
                    logger=logger,
                )
                
                if not market_result.success:
                    # Return partial fill result if we have any
                    if accumulated_filled_qty > Decimal("0"):
                        return ExecutionResult(
                            success=False,
                            filled=True,  # Partial fill
                            filled_quantity=accumulated_filled_qty,
                            fill_price=accumulated_fill_price,
                            execution_mode_used="aggressive_limit_fallback_failed",
                            error_message=market_result.error_message,
                        )
                    else:
                        return ExecutionResult(
                            success=False,
                            filled=False,
                            error_message=market_result.error_message or execution_error or "Aggressive limit execution failed",
                            execution_mode_used="aggressive_limit_fallback_failed",
                        )
                else:
                    logger.info("=" * 80)
                    logger.info(f"✅ MARKET FALLBACK COMPLETE: {symbol} on {exchange_name}")
                    logger.info("=" * 80)
                    
                    # Combine results: limit fills + market fills
                    total_filled = accumulated_filled_qty + (market_result.filled_quantity or Decimal("0"))
                    
                    # Calculate weighted average price
                    if total_filled > Decimal("0"):
                        limit_cost = accumulated_filled_qty * (accumulated_fill_price or Decimal("0")) if accumulated_filled_qty > Decimal("0") else Decimal("0")
                        market_cost = (market_result.filled_quantity or Decimal("0")) * (market_result.fill_price or Decimal("0"))
                        avg_price = (limit_cost + market_cost) / total_filled if total_filled > Decimal("0") else market_result.fill_price
                    else:
                        avg_price = market_result.fill_price
                    
                    return ExecutionResult(
                        success=True,
                        filled=True,
                        filled_quantity=total_filled,
                        fill_price=avg_price,
                        execution_mode_used="aggressive_limit_fallback_market",
                    )
            
            # Success case
            return ExecutionResult(
                success=True,
                filled=True,
                filled_quantity=accumulated_filled_qty,
                fill_price=accumulated_fill_price,
                execution_mode_used=f"aggressive_limit_{last_pricing_strategy}",
            )
        
        finally:
            # Restore original websocket callback
            self._restore_websocket_callback(exchange_client)

