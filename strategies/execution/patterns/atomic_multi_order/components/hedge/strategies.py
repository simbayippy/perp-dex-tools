"""Hedge execution strategies for atomic multi-order execution."""

from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Any, Optional

from strategies.execution.core.execution_types import ExecutionMode

from ...contexts import OrderContext
from .hedge_target_calculator import HedgeTargetCalculator
from .hedge_result_tracker import HedgeResultTracker
from .hedge_pricer import HedgePricer
from .order_reconciler import OrderReconciler


class HedgeResult:
    """Result of hedge execution."""
    
    def __init__(
        self,
        success: bool,
        filled_quantity: Decimal = Decimal("0"),
        fill_price: Optional[Decimal] = None,
        execution_mode: str = "",
        maker_quantity: Decimal = Decimal("0"),
        taker_quantity: Decimal = Decimal("0"),
        error_message: Optional[str] = None,
        retries_used: int = 0
    ):
        self.success = success
        self.filled_quantity = filled_quantity
        self.fill_price = fill_price
        self.execution_mode = execution_mode
        self.maker_quantity = maker_quantity
        self.taker_quantity = taker_quantity
        self.error_message = error_message
        self.retries_used = retries_used


class HedgeStrategy(ABC):
    """Base class for hedge execution strategies."""
    
    @abstractmethod
    async def execute_hedge(
        self,
        trigger_ctx: Optional[OrderContext],
        target_ctx: OrderContext,
        hedge_target: Decimal,
        logger,
        reduce_only: bool = False,
        **kwargs
    ) -> HedgeResult:
        """
        Execute hedge using this strategy.
        
        Args:
            trigger_ctx: The context that triggered the hedge (fully filled order)
            target_ctx: The context that needs to be hedged
            hedge_target: Target quantity to hedge
            logger: Logger instance
            reduce_only: If True, orders can only reduce existing positions
            **kwargs: Additional strategy-specific parameters
            
        Returns:
            HedgeResult with execution details
        """
        pass


class MarketHedgeStrategy(HedgeStrategy):
    """Simple market order hedge strategy."""
    
    def __init__(self, price_provider=None, tracker: Optional[HedgeResultTracker] = None):
        self._price_provider = price_provider
        self._tracker = tracker or HedgeResultTracker()
        self._target_calculator = HedgeTargetCalculator()
    
    async def execute_hedge(
        self,
        trigger_ctx: Optional[OrderContext],
        target_ctx: OrderContext,
        hedge_target: Decimal,
        logger,
        reduce_only: bool = False,
        executor: Optional[Any] = None,
        **kwargs
    ) -> HedgeResult:
        """
        Execute hedge using market orders.
        
        This is a simple, fast hedge strategy that uses market orders to immediately
        flatten exposure. No retries, no price optimization - just execute and done.
        """
        # Lazy import to avoid circular dependency
        from strategies.execution.core.order_executor import OrderExecutor
        hedge_executor = OrderExecutor(price_provider=self._price_provider)
        
        spec = target_ctx.spec
        exchange_name = spec.exchange_client.get_exchange_name().upper()
        
        # Calculate remaining quantity using helper
        remaining_qty = self._target_calculator.calculate_remaining_quantity(
            target_ctx,
            hedge_target
        )
        
        if remaining_qty <= Decimal("0"):
            # Check for suspicious scenario
            if trigger_ctx and trigger_ctx.filled_quantity > Decimal("0"):
                if target_ctx.hedge_target_quantity is not None:
                    hedge_target_val = Decimal(str(target_ctx.hedge_target_quantity))
                    if hedge_target_val > Decimal("0"):
                        logger.warning(
                            f"⚠️ [HEDGE] {exchange_name} {spec.symbol}: Skipping hedge (remaining_qty=0) "
                            f"but trigger {trigger_ctx.spec.exchange_client.get_exchange_name().upper()} "
                            f"{trigger_ctx.spec.symbol} filled {trigger_ctx.filled_quantity} and hedge_target={hedge_target_val}. "
                            f"ctx.filled_quantity={target_ctx.filled_quantity}. "
                            f"This suggests reconciliation may have incorrectly added fills for a canceled order. "
                            f"Check reconciliation logs for warnings."
                        )
            return HedgeResult(
                success=True,
                filled_quantity=Decimal("0"),
                execution_mode="market_skip",
                error_message=None
            )
        
        # Calculate USD estimate from quantity using latest BBO (for logging only)
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
                quantity=remaining_qty,
                mode=ExecutionMode.MARKET_ONLY,
                timeout_seconds=spec.timeout_seconds,
                reduce_only=reduce_only,
            )
        except Exception as exc:
            logger.error(f"Hedge order failed on {exchange_name}: {exc}")
            return HedgeResult(
                success=False,
                error_message=str(exc),
                execution_mode="market_error"
            )
        
        # ⚠️ CRITICAL: Check for partial fills even when execution.success=False
        # Market orders can be cancelled after partial fills (e.g., slippage protection),
        # and we MUST track these partial fills for rollback to work correctly.
        partial_fill_qty = execution.filled_quantity or Decimal("0")
        has_partial_fill = execution.filled and partial_fill_qty > Decimal("0")
        
        if has_partial_fill and not execution.success:
            # Partial fill occurred but order was cancelled - MUST record for rollback
            logger.warning(
                f"⚠️ [{exchange_name}] Market hedge had partial fill ({partial_fill_qty} @ ${execution.fill_price or 'unknown'}) "
                f"before cancellation. Recording for rollback tracking."
            )
            # Track partial fill using tracker
            initial_maker_qty = target_ctx.result.get("maker_qty") if target_ctx.result else None
            self._tracker.apply_market_hedge_result(
                target_ctx,
                execution,
                spec,
                initial_maker_qty=Decimal(str(initial_maker_qty)) if initial_maker_qty else None,
                executor=executor
            )
            
            error_msg = execution.error_message or f"Market hedge failed on {exchange_name} (partial fill: {partial_fill_qty})"
            logger.error(error_msg)
            return HedgeResult(
                success=False,
                filled_quantity=partial_fill_qty,
                fill_price=execution.fill_price,
                execution_mode="market_partial_canceled",
                taker_quantity=partial_fill_qty,
                error_message=error_msg
            )
        
        if not execution.success or not execution.filled:
            error_msg = execution.error_message or f"Market hedge failed on {exchange_name}"
            logger.error(error_msg)
            return HedgeResult(
                success=False,
                error_message=error_msg,
                execution_mode="market_failed"
            )
        
        # Apply successful result
        initial_maker_qty = target_ctx.result.get("maker_qty") if target_ctx.result else None
        self._tracker.apply_market_hedge_result(
            target_ctx,
            execution,
            spec,
            initial_maker_qty=Decimal(str(initial_maker_qty)) if initial_maker_qty else None,
            executor=executor
        )
        
        filled_qty = execution.filled_quantity or Decimal("0")
        maker_qty = target_ctx.result.get("maker_qty", Decimal("0")) if target_ctx.result else Decimal("0")
        taker_qty = target_ctx.result.get("taker_qty", Decimal("0")) if target_ctx.result else Decimal("0")
        
        return HedgeResult(
            success=True,
            filled_quantity=filled_qty,
            fill_price=execution.fill_price,
            execution_mode="market",
            maker_quantity=maker_qty,
            taker_quantity=taker_qty
        )


class AggressiveLimitHedgeStrategy(HedgeStrategy):
    """Aggressive limit order hedge with retries and adaptive pricing."""
    
    def __init__(
        self,
        price_provider=None,
        pricer: Optional[HedgePricer] = None,
        reconciler: Optional[OrderReconciler] = None,
        tracker: Optional[HedgeResultTracker] = None,
        market_fallback: Optional["MarketHedgeStrategy"] = None
    ):
        self._price_provider = price_provider
        self._pricer = pricer or HedgePricer(price_provider=price_provider)
        self._reconciler = reconciler or OrderReconciler()
        self._tracker = tracker or HedgeResultTracker()
        self._target_calculator = HedgeTargetCalculator()
        self._market_fallback = market_fallback or MarketHedgeStrategy(price_provider=price_provider, tracker=self._tracker)
    
    async def execute_hedge(
        self,
        trigger_ctx: Optional[OrderContext],
        target_ctx: OrderContext,
        hedge_target: Decimal,
        logger,
        reduce_only: bool = False,
        max_retries: Optional[int] = None,
        retry_backoff_ms: Optional[int] = None,
        total_timeout_seconds: Optional[float] = None,
        inside_tick_retries: Optional[int] = None,
        max_deviation_pct: Optional[Decimal] = None,
        executor: Optional[Any] = None,
        **kwargs
    ) -> HedgeResult:
        """
        Execute hedge using aggressive limit orders with adaptive pricing.
        
        Strategy:
        - Start with limit orders 1 tick inside spread (safer, avoids post-only violations)
        - After inside_tick_retries attempts, move to touch (at best bid/ask)
        - Retry on post-only violations with fresh BBO
        - Fallback to market orders if timeout or retries exhausted
        
        For closing operations (reduce_only=True), uses more aggressive settings:
        - Shorter timeout (3s vs 6s) for faster exit
        - Fewer retries (5 vs 8) to avoid delay
        - Faster fallback to market orders
        """
        import asyncio
        import time
        from types import SimpleNamespace
        
        # Auto-configure parameters based on operation type
        if reduce_only:
            max_retries = max_retries if max_retries is not None else 5
            retry_backoff_ms = retry_backoff_ms if retry_backoff_ms is not None else 50
            total_timeout_seconds = total_timeout_seconds if total_timeout_seconds is not None else 3.0
            inside_tick_retries = inside_tick_retries if inside_tick_retries is not None else 2
        else:
            max_retries = max_retries if max_retries is not None else 8
            retry_backoff_ms = retry_backoff_ms if retry_backoff_ms is not None else 75
            total_timeout_seconds = total_timeout_seconds if total_timeout_seconds is not None else 6.0
            inside_tick_retries = inside_tick_retries if inside_tick_retries is not None else 3
        
        spec = target_ctx.spec
        exchange_name = spec.exchange_client.get_exchange_name().upper()
        symbol = spec.symbol
        
        # Calculate remaining quantity using helper
        remaining_qty = self._target_calculator.calculate_remaining_quantity(
            target_ctx,
            hedge_target
        )
        
        if remaining_qty <= Decimal("0"):
            return HedgeResult(
                success=True,
                filled_quantity=Decimal("0"),
                execution_mode="aggressive_limit_skip"
            )
        
        # Calculate USD estimate for logging
        estimated_usd = Decimal("0")
        try:
            if self._price_provider and remaining_qty > Decimal("0"):
                best_bid, best_ask = await self._price_provider.get_bbo_prices(
                    spec.exchange_client, symbol
                )
                price = best_ask if spec.side == "buy" else best_bid
                estimated_usd = remaining_qty * price
        except Exception:
            pass
        
        logger.info("=" * 80)
        logger.info(
            f"⚡ AGGRESSIVE LIMIT HEDGE: {symbol} on {exchange_name}: "
            f"{remaining_qty} qty" + (f" (≈${float(estimated_usd):.2f})" if estimated_usd > Decimal("0") else "")
        )
        logger.info("=" * 80)
        
        # Track fills during aggressive limit hedge
        start_time = time.time()
        hedge_success = False
        hedge_error: Optional[str] = None
        initial_filled_qty = target_ctx.filled_quantity
        accumulated_filled_qty = Decimal("0")
        accumulated_fill_price: Optional[Decimal] = None
        last_order_filled_qty = Decimal("0")
        last_order_id: Optional[str] = None
        retries_used = 0
        last_pricing_strategy = "unknown"  # Track pricing strategy for final result
        
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
            
            current_order_filled_qty = Decimal("0")
            
            try:
                # Calculate hedge price using pricer helper
                price_result = await self._pricer.calculate_aggressive_limit_price(
                    spec=spec,
                    trigger_ctx=trigger_ctx,
                    retry_count=retry_count,
                    inside_tick_retries=inside_tick_retries,
                    max_deviation_pct=max_deviation_pct,
                    logger=logger,
                    exchange_name=exchange_name,
                    symbol=symbol,
                )
                last_pricing_strategy = price_result.pricing_strategy  # Track for final result
                
                # Calculate remaining quantity after accumulated partial fills
                total_filled_qty = initial_filled_qty + accumulated_filled_qty
                remaining_qty = hedge_target - total_filled_qty
                if remaining_qty <= Decimal("0"):
                    hedge_success = True
                    break
                
                # Round quantity to step size
                order_quantity = spec.exchange_client.round_to_step(remaining_qty)
                
                if order_quantity <= Decimal("0"):
                    logger.warning(
                        f"⚠️ [{exchange_name}] Order quantity rounded to zero for {symbol} "
                        f"(accumulated_filled={accumulated_filled_qty}, hedge_target={hedge_target})"
                    )
                    if accumulated_filled_qty > Decimal("0"):
                        hedge_success = True
                    break
                
                strategy_info = f"{price_result.pricing_strategy}"
                if price_result.break_even_strategy and price_result.break_even_strategy != price_result.pricing_strategy:
                    strategy_info += f" (break_even: {price_result.break_even_strategy})"
                
                logger.debug(
                    f"🔄 [{exchange_name}] Aggressive limit hedge attempt {retry_count + 1}/{max_retries} "
                    f"for {symbol}: {strategy_info} @ ${price_result.limit_price} qty={order_quantity} "
                    f"(best_bid=${price_result.best_bid}, best_ask=${price_result.best_ask})"
                )
                
                # Place limit order
                contract_id = spec.exchange_client.resolve_contract_id(symbol)
                order_result = await spec.exchange_client.place_limit_order(
                    contract_id=contract_id,
                    quantity=float(order_quantity),
                    price=float(price_result.limit_price),
                    side=spec.side,
                    reduce_only=reduce_only,
                )
                
                if not order_result.success:
                    error_msg = order_result.error_message or f"Limit order placement failed on {exchange_name}"
                    logger.warning(f"⚠️ [{exchange_name}] Limit order placement failed for {symbol}: {error_msg}")
                    
                    if "post" in error_msg.lower() or "post-only" in error_msg.lower():
                        logger.info(
                            f"🔄 [{exchange_name}] Post-only violation detected for {symbol}. "
                            f"Retrying with fresh BBO ({price_result.pricing_strategy} strategy)."
                        )
                        await asyncio.sleep(retry_backoff_ms / 1000.0)
                        retries_used += 1
                        continue
                    else:
                        hedge_error = error_msg
                        break
                
                order_id = order_result.order_id
                if not order_id:
                    logger.warning(f"⚠️ [{exchange_name}] No order_id returned for {symbol}")
                    await asyncio.sleep(retry_backoff_ms / 1000.0)
                    retries_used += 1
                    continue
                
                last_order_id = order_id
                
                # Register hedge order context for websocket callbacks (real-time fill tracking)
                if executor is not None:
                    executor._register_order_context(target_ctx, str(order_id))
                
                # Wait for fill with timeout per attempt
                remaining_timeout = total_timeout_seconds - elapsed_time
                if remaining_timeout <= 0:
                    try:
                        order_status_check = await spec.exchange_client.get_order_info(order_id)
                        if order_status_check and order_status_check.status not in {"CANCELED", "CANCELLED", "FILLED"}:
                            await spec.exchange_client.cancel_order(order_id)
                    except Exception:
                        pass
                    break
                
                attempt_timeout = min(1.5, remaining_timeout)
                
                # Poll for fill status using reconciler helper
                recon_result = await self._reconciler.poll_order_until_filled(
                    exchange_client=spec.exchange_client,
                    order_id=order_id,
                    order_quantity=order_quantity,
                    limit_price=price_result.limit_price,
                    accumulated_filled_qty=accumulated_filled_qty,
                    current_order_filled_qty=current_order_filled_qty,
                    initial_filled_qty=initial_filled_qty,
                    hedge_target=hedge_target,
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
                
                if recon_result.hedge_error:
                    hedge_error = recon_result.hedge_error
                
                if recon_result.fill_price:
                    accumulated_fill_price = recon_result.fill_price
                
                # Check if filled
                if recon_result.filled and accumulated_filled_qty > Decimal("0"):
                    filled_qty = accumulated_filled_qty
                    fill_price = accumulated_fill_price or price_result.limit_price
                    
                    total_filled_qty = initial_filled_qty + accumulated_filled_qty
                    if total_filled_qty >= hedge_target * Decimal("0.99"):  # 99% threshold
                        logger.info("=" * 80)
                        logger.info(
                            f"✅ AGGRESSIVE LIMIT HEDGE SUCCESS: [{exchange_name}] {symbol} "
                            f"@ ${fill_price} qty={filled_qty} new fills (total: {total_filled_qty}/{hedge_target}, "
                            f"attempt {retry_count + 1})"
                        )
                        logger.info("=" * 80)
                        
                        # Apply result using tracker
                        execution_result = SimpleNamespace(
                            success=True,
                            filled=True,
                            fill_price=fill_price,
                            filled_quantity=filled_qty,
                            slippage_usd=Decimal("0"),
                            execution_mode_used=f"aggressive_limit_{price_result.pricing_strategy}",
                            order_id=order_id,
                            retryable=False,
                        )
                        self._tracker.apply_aggressive_limit_hedge_result(
                            target_ctx,
                            execution_result,
                            spec,
                            initial_filled_qty,
                            accumulated_filled_qty,
                            fill_price,
                            order_id,
                            price_result.pricing_strategy,
                            executor=executor
                        )
                        
                        hedge_success = True
                        retries_used = retry_count + 1
                        break
                    else:
                        # Partial fill but not enough - continue retrying
                        logger.info(
                            f"📊 [{exchange_name}] Partial fill {filled_qty} new fills "
                            f"(total: {total_filled_qty}/{hedge_target}) for {symbol}. "
                            f"Continuing to fill remainder."
                        )
                        if not recon_result.partial_fill_detected:
                            try:
                                await spec.exchange_client.cancel_order(order_id)
                            except Exception:
                                pass
                        await asyncio.sleep(retry_backoff_ms / 1000.0)
                        retries_used += 1
                        continue
                elif recon_result.partial_fill_detected:
                    # Had partial fill but loop exited - continue retrying
                    total_filled_qty = initial_filled_qty + accumulated_filled_qty
                    logger.debug(
                        f"📊 [{exchange_name}] Partial fill {accumulated_filled_qty} new fills "
                        f"(total: {total_filled_qty}/{hedge_target}) for {symbol}. "
                        f"Retrying for remainder."
                    )
                    await asyncio.sleep(retry_backoff_ms / 1000.0)
                    retries_used += 1
                    continue
                elif not recon_result.filled:
                    # Order not filled, cancel and retry
                    try:
                        order_status_check = await spec.exchange_client.get_order_info(order_id)
                        if order_status_check and order_status_check.status not in {"CANCELED", "CANCELLED"}:
                            await spec.exchange_client.cancel_order(order_id)
                    except Exception:
                        pass
                    
                    if hedge_error:
                        break
                    await asyncio.sleep(retry_backoff_ms / 1000.0)
                    retries_used += 1
                    continue
                
            except Exception as exc:
                logger.error(
                    f"❌ [{exchange_name}] Aggressive limit hedge attempt {retry_count + 1} "
                    f"exception for {symbol}: {exc}"
                )
                hedge_error = str(exc)
                await asyncio.sleep(retry_backoff_ms / 1000.0)
                retries_used += 1
        
        # Final reconciliation check
        if not hedge_success and last_order_id:
            logger.info("-" * 80)
            logger.info(f"📋 Final Reconciliation Check: {symbol} on {exchange_name}")
            logger.info("-" * 80)
            
            accumulated_filled_qty, accumulated_fill_price = await self._reconciler.reconcile_final_state(
                exchange_client=spec.exchange_client,
                order_id=last_order_id,
                last_known_fills=last_order_filled_qty,
                accumulated_filled_qty=accumulated_filled_qty,
                accumulated_fill_price=accumulated_fill_price,
                logger=logger,
                exchange_name=exchange_name,
                symbol=symbol,
            )
        
        # Fallback to market if limit hedge failed
        if not hedge_success:
            logger.info("=" * 80)
            logger.info(f"⚠️ AGGRESSIVE LIMIT HEDGE FAILED: Falling back to market order")
            logger.info("=" * 80)
            
            total_filled_qty = initial_filled_qty + accumulated_filled_qty
            remaining_after_partial = hedge_target - total_filled_qty
            
            if accumulated_filled_qty > Decimal("0"):
                logger.info(
                    f"📊 [{exchange_name}] Aggressive limit hedge partial fills: {accumulated_filled_qty} new fills "
                    f"(total: {total_filled_qty}/{hedge_target}) for {symbol}. "
                    f"Falling back to market order for remaining {remaining_after_partial}."
                )
                # Track partial fills before market fallback
                self._tracker.track_partial_fills_before_market_fallback(
                    target_ctx,
                    initial_filled_qty,
                    accumulated_filled_qty,
                    accumulated_fill_price,
                    total_filled_qty
                )
            
            # Fallback to market hedge
            market_result = await self._market_fallback.execute_hedge(
                trigger_ctx=None,
                target_ctx=target_ctx,
                hedge_target=hedge_target,
                logger=logger,
                reduce_only=reduce_only,
                executor=executor
            )
            
            if not market_result.success:
                return HedgeResult(
                    success=False,
                    filled_quantity=accumulated_filled_qty,
                    fill_price=accumulated_fill_price,
                    execution_mode="aggressive_limit_fallback_failed",
                    maker_quantity=accumulated_filled_qty,
                    error_message=market_result.error_message,
                    retries_used=retries_used
                )
            else:
                logger.info("=" * 80)
                logger.info(f"✅ MARKET HEDGE FALLBACK COMPLETE: {symbol} on {exchange_name}")
                logger.info("=" * 80)
                
                # Combine results
                total_maker_qty = initial_filled_qty + accumulated_filled_qty
                total_taker_qty = market_result.taker_quantity
                
                return HedgeResult(
                    success=True,
                    filled_quantity=target_ctx.filled_quantity,
                    fill_price=market_result.fill_price or accumulated_fill_price,
                    execution_mode=f"aggressive_limit_fallback_market",
                    maker_quantity=total_maker_qty,
                    taker_quantity=total_taker_qty,
                    retries_used=retries_used
                )
        
        # Success case
        maker_qty = target_ctx.result.get("maker_qty", Decimal("0")) if target_ctx.result else Decimal("0")
        return HedgeResult(
            success=True,
            filled_quantity=accumulated_filled_qty,
            fill_price=accumulated_fill_price,
            execution_mode=f"aggressive_limit_{last_pricing_strategy}",
            maker_quantity=maker_qty,
            taker_quantity=Decimal("0"),
            retries_used=retries_used
        )

