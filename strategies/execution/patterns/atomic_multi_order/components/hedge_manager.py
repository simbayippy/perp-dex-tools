"""Hedging utilities for atomic multi-order execution."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, List, Optional

from ..contexts import OrderContext
from .hedge.hedge_target_calculator import HedgeTargetCalculator
from .hedge.strategies import MarketHedgeStrategy, AggressiveLimitHedgeStrategy, HedgeResult
from strategies.execution.core.execution_strategies.aggressive_limit import AggressiveLimitExecutionStrategy


class HedgeManager:
    """Orchestrates hedge execution using pluggable strategies."""

    def __init__(self, price_provider=None) -> None:
        """
        Initialize hedge manager.
        
        Args:
            price_provider: Optional PriceProvider for BBO price retrieval
        """
        self._price_provider = price_provider
        
        # Initialize helper components
        self._target_calculator = HedgeTargetCalculator()
        
        # Initialize execution strategy (create once, inject into hedge strategy)
        # Dependency injection: create execution strategy here and inject into hedge strategy
        execution_strategy = AggressiveLimitExecutionStrategy(price_provider=price_provider)
        
        # Initialize strategies
        self._market_strategy = MarketHedgeStrategy(price_provider=price_provider)
        self._aggressive_limit_strategy = AggressiveLimitHedgeStrategy(
            execution_strategy=execution_strategy,  # Required: inject dependency first
            price_provider=price_provider
        )

    async def hedge(
        self,
        trigger_ctx: Optional[OrderContext],
        contexts: List[OrderContext],
        logger,
        reduce_only: bool = False,
    ) -> HedgeResult:
        """
        Attempt to flatten any residual exposure using market orders.

        This method orchestrates hedge execution using MarketHedgeStrategy.

        Returns:
            HedgeResult with execution details.
        """
        for ctx in contexts:
            if ctx is trigger_ctx:
                continue

            spec = ctx.spec
            exchange_name = spec.exchange_client.get_exchange_name().upper()
            
            # Calculate hedge target using helper
            remaining_qty = self._target_calculator.get_remaining_quantity_for_hedge(ctx, logger)
            
            if remaining_qty <= Decimal("0"):
                # Check for suspicious scenario
                if trigger_ctx and trigger_ctx.filled_quantity > Decimal("0"):
                    if ctx.hedge_target_quantity is not None:
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

            # Determine hedge target
            hedge_target = ctx.hedge_target_quantity
            if hedge_target is None:
                spec_quantity = getattr(ctx.spec, "quantity", None)
                if spec_quantity is not None:
                    hedge_target = Decimal(str(spec_quantity))
                else:
                    continue  # Skip if no target can be determined
            
            # Execute using market strategy
            result = await self._market_strategy.execute_hedge(
                trigger_ctx=trigger_ctx,
                target_ctx=ctx,
                hedge_target=hedge_target,
                logger=logger,
                reduce_only=reduce_only
            )
            
            if not result.success:
                return result

        return HedgeResult(success=True)

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
        executor: Optional[Any] = None,
    ) -> HedgeResult:
        """
        Attempt to hedge using aggressive limit orders with adaptive pricing.
        
        This method orchestrates hedge execution using AggressiveLimitHedgeStrategy.
        
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
            executor: Optional AtomicMultiOrderExecutor instance for websocket callback registration
            
        Returns:
            HedgeResult with execution details
        """
        for ctx in contexts:
            if ctx is trigger_ctx:
                continue

            spec = ctx.spec
            exchange_name = spec.exchange_client.get_exchange_name().upper()
            symbol = spec.symbol
            
            # Calculate hedge target using helper
            # Note: executor.py already calculates hedge_target_quantity before calling this,
            # but we handle the case where it's not set
            hedge_target: Optional[Decimal] = None
            
            if ctx.hedge_target_quantity is not None:
                hedge_target = Decimal(str(ctx.hedge_target_quantity))
            else:
                # Fallback: calculate hedge_target from spec.quantity for tracking purposes
                spec_quantity = getattr(ctx.spec, "quantity", None)
                if spec_quantity is not None:
                    hedge_target = Decimal(str(spec_quantity))
            
            if hedge_target is None:
                error_msg = (
                    f"Cannot determine hedge_target for {exchange_name} {symbol}. "
                    f"hedge_target_quantity and spec.quantity are both None. This indicates a configuration issue."
                )
                logger.error(f"❌ [{exchange_name}] {error_msg}")
                return HedgeResult(success=False, error_message=error_msg)
            
            # Calculate remaining quantity
            remaining_qty = self._target_calculator.calculate_remaining_quantity(ctx, hedge_target)
            
            if remaining_qty <= Decimal("0"):
                continue
            
            # Execute using aggressive limit strategy
            result = await self._aggressive_limit_strategy.execute_hedge(
                trigger_ctx=trigger_ctx,
                target_ctx=ctx,
                hedge_target=hedge_target,
                logger=logger,
                reduce_only=reduce_only,
                max_retries=max_retries,
                retry_backoff_ms=retry_backoff_ms,
                total_timeout_seconds=total_timeout_seconds,
                inside_tick_retries=inside_tick_retries,
                max_deviation_pct=max_deviation_pct,
                executor=executor
            )
            
            if not result.success:
                return result

        return HedgeResult(success=True)
