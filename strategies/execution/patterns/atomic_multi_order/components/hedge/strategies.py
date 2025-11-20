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
        execution_strategy,  # Required: injected dependency - breaks circular dependency
        price_provider=None,
        pricer: Optional[HedgePricer] = None,
        reconciler: Optional[OrderReconciler] = None,
        tracker: Optional[HedgeResultTracker] = None,
        market_fallback: Optional["MarketHedgeStrategy"] = None,
    ):
        """
        Initialize aggressive limit hedge strategy.
        
        Args:
            execution_strategy: AggressiveLimitExecutionStrategy instance (required).
                This dependency injection breaks circular dependencies and makes
                dependencies explicit. Must be provided by the caller (e.g., HedgeManager).
            price_provider: Optional PriceProvider for BBO price retrieval
            pricer: Optional HedgePricer instance (for hedge-specific pricing logic)
            reconciler: Optional OrderReconciler instance (for hedge-specific reconciliation)
            tracker: Optional HedgeResultTracker instance
            market_fallback: Optional MarketHedgeStrategy for fallback
        """
        self._execution_strategy = execution_strategy
        self._price_provider = price_provider
        # Keep old pricer/reconciler for hedge-specific logic (if needed)
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
        from types import SimpleNamespace
        
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
        
        # Extract trigger fill price and side for break-even pricing
        trigger_fill_price: Optional[Decimal] = None
        trigger_side: Optional[str] = None
        if trigger_ctx and trigger_ctx.result:
            trigger_fill_price_raw = trigger_ctx.result.get("fill_price")
            if trigger_fill_price_raw:
                try:
                    trigger_fill_price = Decimal(str(trigger_fill_price_raw))
                    trigger_side = trigger_ctx.spec.side
                except (ValueError, TypeError):
                    pass
        
        # Track initial filled quantity before execution
        initial_filled_qty = target_ctx.filled_quantity
        
        # Execute using general-purpose aggressive limit execution strategy
        execution_result = await self._execution_strategy.execute(
            exchange_client=spec.exchange_client,
            symbol=symbol,
            side=spec.side,
            quantity=remaining_qty,
            reduce_only=reduce_only,
            max_retries=max_retries,
            retry_backoff_ms=retry_backoff_ms,
            total_timeout_seconds=total_timeout_seconds,
            inside_tick_retries=inside_tick_retries,
            max_deviation_pct=max_deviation_pct,
            trigger_fill_price=trigger_fill_price,
            trigger_side=trigger_side,
            logger=logger,
        )
        
        # Convert ExecutionResult to HedgeResult
        if execution_result.success and execution_result.filled:
            # Success case - track result using tracker
            filled_qty = execution_result.filled_quantity or Decimal("0")
            fill_price = execution_result.fill_price
            
            # Create execution_result-like object for tracker
            execution_result_for_tracker = SimpleNamespace(
                success=True,
                filled=True,
                fill_price=fill_price,
                filled_quantity=filled_qty,
                slippage_usd=Decimal("0"),
                execution_mode_used=execution_result.execution_mode_used or "aggressive_limit",
                order_id=execution_result.order_id,
                retryable=False,
            )
            
            # Track maker quantity (all fills from aggressive limit are maker)
            self._tracker.apply_aggressive_limit_hedge_result(
                target_ctx,
                execution_result_for_tracker,
                spec,
                initial_filled_qty,
                filled_qty,  # accumulated_filled_qty (new fills only)
                fill_price or Decimal("0"),
                execution_result.order_id or "",
                execution_result.execution_mode_used.replace("aggressive_limit_", "") if execution_result.execution_mode_used else "unknown",
                executor=executor
            )
            
            maker_qty = target_ctx.result.get("maker_qty", Decimal("0")) if target_ctx.result else Decimal("0")
            
            return HedgeResult(
                success=True,
                filled_quantity=filled_qty,
                fill_price=fill_price,
                execution_mode=execution_result.execution_mode_used or "aggressive_limit",
                maker_quantity=maker_qty,
                taker_quantity=Decimal("0"),
            )
        elif execution_result.filled and execution_result.filled_quantity and execution_result.filled_quantity > Decimal("0"):
            # Partial fill case - fallback to market for remainder
            accumulated_filled_qty = execution_result.filled_quantity
            accumulated_fill_price = execution_result.fill_price
            
            logger.info("=" * 80)
            logger.info(f"⚠️ AGGRESSIVE LIMIT HEDGE PARTIAL: Falling back to market order")
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
                    execution_mode="aggressive_limit_fallback_market",
                    maker_quantity=total_maker_qty,
                    taker_quantity=total_taker_qty,
                )
        else:
            # Complete failure - fallback to market
            logger.info("=" * 80)
            logger.info(f"⚠️ AGGRESSIVE LIMIT HEDGE FAILED: Falling back to market order")
            logger.info("=" * 80)
            
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
                    filled_quantity=Decimal("0"),
                    execution_mode="aggressive_limit_fallback_failed",
                    error_message=market_result.error_message or execution_result.error_message or "Aggressive limit hedge failed",
                )
            else:
                return HedgeResult(
                    success=True,
                    filled_quantity=market_result.filled_quantity,
                    fill_price=market_result.fill_price,
                    execution_mode="aggressive_limit_fallback_market",
                    maker_quantity=Decimal("0"),
                    taker_quantity=market_result.taker_quantity,
                )

