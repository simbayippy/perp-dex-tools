"""Aggressive limit order execution strategy with retries and adaptive pricing."""

from __future__ import annotations

import asyncio
import time
from decimal import Decimal
from typing import Optional, Tuple
from dataclasses import dataclass

from exchange_clients import BaseExchangeClient

from ..execution_types import ExecutionResult
from ..price_provider import PriceProvider
from .base import ExecutionStrategy
from ..execution_components.pricer import AggressiveLimitPricer
from ..execution_components.reconciler import OrderReconciler
from ..spread_utils import SpreadCheckType, is_spread_acceptable
from helpers.unified_logger import get_core_logger


@dataclass
class ExecutionConfig:
    """Configuration for aggressive limit execution."""
    max_retries: int
    retry_backoff_ms: int
    total_timeout_seconds: float
    inside_tick_retries: int


@dataclass
class ExecutionState:
    """Tracks state during order execution."""
    accumulated_filled_qty: Decimal
    accumulated_fill_price: Optional[Decimal]
    last_order_filled_qty: Decimal
    last_order_id: Optional[str]
    retries_used: int
    last_pricing_strategy: str
    execution_success: bool
    execution_error: Optional[str]
    # Progressive price walking state
    consecutive_wide_spread_skips: int = 0
    fallback_mode: str = "aggressive"  # "aggressive" | "progressive" | "market"
    progressive_attempt_count: int = 0


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
        super().__init__(use_websocket_events=use_websocket_events)
        
        self._price_provider = price_provider or PriceProvider()
        self._pricer = pricer or AggressiveLimitPricer(price_provider=self._price_provider)
        self._reconciler = reconciler or OrderReconciler()
        self.logger = get_core_logger("aggressive_limit_execution_strategy")
        
        # Lazy import to avoid circular dependency
        if market_fallback is None:
            from .market import MarketExecutionStrategy
            self._market_fallback = MarketExecutionStrategy(price_provider=self._price_provider)
        else:
            self._market_fallback = market_fallback
    
    def _get_execution_config(
        self,
        reduce_only: bool,
        max_retries: Optional[int],
        retry_backoff_ms: Optional[int],
        total_timeout_seconds: Optional[float],
        inside_tick_retries: Optional[int],
    ) -> ExecutionConfig:
        """
        Get execution configuration based on operation type.
        
        Args:
            reduce_only: If True, use more aggressive settings for faster exit
            max_retries: Override max retries
            retry_backoff_ms: Override retry backoff
            total_timeout_seconds: Override total timeout
            inside_tick_retries: Override inside tick retries
            
        Returns:
            ExecutionConfig with configured parameters
        """
        if reduce_only:
            return ExecutionConfig(
                max_retries=max_retries if max_retries is not None else 8,
                retry_backoff_ms=retry_backoff_ms if retry_backoff_ms is not None else 30,
                total_timeout_seconds=total_timeout_seconds if total_timeout_seconds is not None else 8.0,
                inside_tick_retries=inside_tick_retries if inside_tick_retries is not None else 2,
            )
        else:
            return ExecutionConfig(
                max_retries=max_retries if max_retries is not None else 15,
                retry_backoff_ms=retry_backoff_ms if retry_backoff_ms is not None else 30,
                total_timeout_seconds=total_timeout_seconds if total_timeout_seconds is not None else 13.0,
                inside_tick_retries=inside_tick_retries if inside_tick_retries is not None else 3,
            )
    
    async def _calculate_quantity_from_usd(
        self,
        exchange_client: BaseExchangeClient,
        symbol: str,
        side: str,
        size_usd: Decimal,
    ) -> Tuple[bool, Optional[Decimal], Optional[str]]:
        """
        Calculate quantity from USD size.
        
        Returns:
            Tuple of (success, quantity, error_message)
        """
        try:
            best_bid, best_ask = await self._price_provider.get_bbo_prices(exchange_client, symbol)
            price = best_ask if side == "buy" else best_bid
            quantity = Decimal(str(size_usd)) / price
            return True, quantity, None
        except Exception as e:
            return False, None, f"Failed to calculate quantity from size_usd: {e}"
    
    async def _estimate_usd_value(
        self,
        exchange_client: BaseExchangeClient,
        symbol: str,
        side: str,
        quantity: Decimal,
    ) -> Decimal:
        """Estimate USD value for logging purposes."""
        try:
            if quantity > Decimal("0"):
                best_bid, best_ask = await self._price_provider.get_bbo_prices(exchange_client, symbol)
                price = best_ask if side == "buy" else best_bid
                return quantity * price
        except Exception:
            pass
        return Decimal("0")
    
    def _log_execution_start(
        self,
        exchange_name: str,
        symbol: str,
        quantity: Decimal,
        estimated_usd: Decimal,
        logger,
    ):
        """Log execution start banner."""
        logger.info("=" * 80)
        logger.info(
            f"⚡ AGGRESSIVE LIMIT EXECUTION: {symbol} on {exchange_name}: "
            f"{quantity} qty" + (f" (≈${float(estimated_usd):.2f})" if estimated_usd > Decimal("0") else "")
        )
        logger.info("=" * 80)
    
    def _check_timeout(
        self,
        start_time: float,
        total_timeout_seconds: float,
        retry_count: int,
        retries_used: int,
        exchange_name: str,
        symbol: str,
        logger,
    ) -> bool:
        """
        Check if total timeout has been exceeded.
        
        Returns:
            True if timeout exceeded, False otherwise
        """
        elapsed_time = time.time() - start_time
        if elapsed_time >= total_timeout_seconds:
            logger.info("-" * 80)
            logger.warning(
                f"⏱️ Aggressive limit execution timeout after {elapsed_time:.2f}s for {exchange_name} {symbol} "
                f"(placed {retry_count} orders, {retries_used} retries). Falling back to market order."
            )
            logger.info("-" * 80)
            return True
        return False
    
    def _check_target_filled(
        self,
        accumulated_filled_qty: Decimal,
        target_quantity: Decimal,
        exchange_name: str,
        symbol: str,
        retry_count: int,
        logger,
    ) -> bool:
        """
        Check if target quantity is fully filled.
        
        Returns:
            True if target is filled, False otherwise
        """
        remaining_qty = target_quantity - accumulated_filled_qty
        if remaining_qty <= Decimal("0"):
            logger.info(
                f"✅ [{exchange_name}] Target quantity fully filled for {symbol}: "
                f"accumulated={accumulated_filled_qty}/{target_quantity}. "
                f"Stopping retries (attempt {retry_count + 1})"
            )
            return True
        return False
    
    async def _place_limit_order(
        self,
        exchange_client: BaseExchangeClient,
        symbol: str,
        side: str,
        order_quantity: Decimal,
        limit_price: Decimal,
        reduce_only: bool,
        exchange_name: str,
        logger,
    ):
        """
        Place a limit order.
        
        Returns:
            OrderResult from exchange
        """
        contract_id = exchange_client.resolve_contract_id(symbol)
        return await exchange_client.place_limit_order(
            contract_id=contract_id,
            quantity=float(order_quantity),
            price=float(limit_price),
            side=side,
            reduce_only=reduce_only,
        )
    
    def _is_retryable_error(self, error_msg: str) -> bool:
        """Check if an error is retryable (post-only violations, expired orders, etc.)."""
        error_lower = error_msg.lower()
        return (
            "post" in error_lower or 
            "post-only" in error_lower or
            "expired" in error_lower or
            "did not remain open" in error_lower or
            "gtx" in error_lower
        )
    
    async def _handle_order_placement_failure(
        self,
        error_msg: str,
        exchange_name: str,
        symbol: str,
        pricing_strategy: str,
        retry_count: int,
        max_retries: int,
        retry_backoff_ms: int,
        logger,
    ) -> Tuple[bool, bool]:
        """
        Handle order placement failure.
        
        Returns:
            Tuple of (should_continue, is_fatal_error)
        """
        logger.warning(f"⚠️ [{exchange_name}] Limit order placement failed for {symbol}: {error_msg}")
        
        if self._is_retryable_error(error_msg):
            logger.info(
                f"🔄 [{exchange_name}] Retryable order rejection detected for {symbol}: {error_msg}. "
                f"Retrying with adaptive pricing ({pricing_strategy} strategy, attempt {retry_count + 1}/{max_retries})."
            )
            await asyncio.sleep(retry_backoff_ms / 1000.0)
            return True, False  # Continue, not fatal
        else:
            logger.error(
                f"❌ [{exchange_name}] Fatal order placement error for {symbol}: {error_msg}. "
                f"Stopping retries."
            )
            return False, True  # Don't continue, is fatal
    
    async def _wait_for_order_fill(
        self,
        exchange_client: BaseExchangeClient,
        use_event_based: bool,
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
        exchange_name: str,
        symbol: str,
        logger,
    ):
        """
        Wait for order to fill using event-based or polling reconciler.
        
        Returns:
            ReconciliationResult
        """
        if use_event_based and self._can_use_websocket_events(exchange_client):
            # Use websocket events for instant response
            return await self._event_reconciler.wait_for_order_event(
                exchange_client=exchange_client,
                order_id=order_id,
                order_quantity=order_quantity,
                limit_price=limit_price,
                target_quantity=target_quantity,
                accumulated_filled_qty=accumulated_filled_qty,
                current_order_filled_qty=current_order_filled_qty,
                attempt_timeout=attempt_timeout,
                pricing_strategy=pricing_strategy,
                retry_count=retry_count,
                retry_backoff_ms=retry_backoff_ms,
                logger=logger,
                exchange_name=exchange_name,
                symbol=symbol,
            )
        else:
            # Fallback to polling reconciler
            return await self._reconciler.poll_order_until_filled(
                exchange_client=exchange_client,
                order_id=order_id,
                order_quantity=order_quantity,
                limit_price=limit_price,
                target_quantity=target_quantity,
                accumulated_filled_qty=accumulated_filled_qty,
                current_order_filled_qty=current_order_filled_qty,
                attempt_timeout=attempt_timeout,
                pricing_strategy=pricing_strategy,
                retry_count=retry_count,
                retry_backoff_ms=retry_backoff_ms,
                logger=logger,
                exchange_name=exchange_name,
                symbol=symbol,
            )
    
    async def _log_cancellation_reason(
        self,
        exchange_client: BaseExchangeClient,
        order_id: str,
        exchange_name: str,
        symbol: str,
        retry_count: int,
        max_retries: int,
        logger,
    ):
        """Log cancellation reason for debugging."""
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
    
    def _check_fill_threshold(
        self,
        accumulated_filled_qty: Decimal,
        target_quantity: Decimal,
        fill_threshold: Decimal = Decimal("0.99"),
    ) -> bool:
        """Check if accumulated fills meet threshold (default 99%)."""
        return accumulated_filled_qty >= target_quantity * fill_threshold
    
    def _log_fill_success(
        self,
        exchange_name: str,
        symbol: str,
        fill_price: Decimal,
        filled_qty: Decimal,
        accumulated_filled_qty: Decimal,
        target_quantity: Decimal,
        retry_count: int,
        logger,
    ):
        """Log successful fill."""
        logger.info("=" * 80)
        logger.info(
            f"✅ AGGRESSIVE LIMIT EXECUTION SUCCESS: [{exchange_name}] {symbol} "
            f"@ ${fill_price} qty={filled_qty} fills (total: {accumulated_filled_qty}/{target_quantity}, "
            f"attempt {retry_count + 1})"
        )
        logger.info("=" * 80)
    
    async def _handle_unfilled_order(
        self,
        exchange_client: BaseExchangeClient,
        order_id: str,
        attempt_timeout: float,
        exchange_name: str,
        symbol: str,
        retry_count: int,
        max_retries: int,
        logger,
    ) -> Optional[str]:
        """
        Handle an order that wasn't filled.
        
        Returns:
            Cancellation reason if order was cancelled, None otherwise
        """
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
                    logger.info(
                        f"🔄 [{exchange_name}] Order {order_id} not filled after {attempt_timeout}s, "
                        f"canceling and retrying (attempt {retry_count + 1}/{max_retries})"
                    )
                    try:
                        await exchange_client.cancel_order(order_id)
                    except Exception as cancel_exc:
                        logger.warning(
                            f"⚠️ [{exchange_name}] Failed to cancel order {order_id}: {cancel_exc}"
                        )
            else:
                logger.debug(
                    f"🔄 [{exchange_name}] Order {order_id} status unknown. "
                    f"Retrying (attempt {retry_count + 1}/{max_retries})"
                )
        except Exception as cancel_exc:
            logger.debug(f"⚠️ [{exchange_name}] Exception during cancel check: {cancel_exc}")
        
        return cancellation_reason
    
    async def _execute_market_fallback(
        self,
        exchange_client: BaseExchangeClient,
        symbol: str,
        side: str,
        quantity: Decimal,
        target_quantity: Decimal,
        accumulated_filled_qty: Decimal,
        accumulated_fill_price: Optional[Decimal],
        reduce_only: bool,
        exchange_name: str,
        execution_error: Optional[str],
        logger,
    ) -> ExecutionResult:
        """Execute market order fallback after limit execution fails."""
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
                    filled=True,
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
    
    async def _perform_final_reconciliation(
        self,
        exchange_client: BaseExchangeClient,
        last_order_id: str,
        last_order_filled_qty: Decimal,
        accumulated_filled_qty: Decimal,
        accumulated_fill_price: Optional[Decimal],
        target_quantity: Decimal,
        exchange_name: str,
        symbol: str,
        logger,
    ) -> Tuple[bool, Decimal, Optional[Decimal]]:
        """
        Perform final reconciliation check after retries exhausted.
        
        Returns:
            Tuple of (execution_success, accumulated_filled_qty, accumulated_fill_price)
        """
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
        
        # Check if final reconciliation found that we actually filled completely
        if self._check_fill_threshold(accumulated_filled_qty, target_quantity):
            logger.info("=" * 80)
            logger.info(
                f"✅ AGGRESSIVE LIMIT EXECUTION SUCCESS (via final reconciliation): "
                f"[{exchange_name}] {symbol} @ ${accumulated_fill_price or 'n/a'} "
                f"qty={accumulated_filled_qty} (total: {accumulated_filled_qty}/{target_quantity})"
            )
            logger.info("=" * 80)
            return True, accumulated_filled_qty, accumulated_fill_price
        
        return False, accumulated_filled_qty, accumulated_fill_price
    
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
        - Shorter timeout (8s vs 13s) for faster exit
        - Fewer retries (8 vs 15) to avoid delay
        - Faster fallback to market orders
        
        Args:
            exchange_client: Exchange client instance
            symbol: Trading pair (e.g., "BTC-PERP")
            side: "buy" or "sell"
            quantity: Order quantity
            size_usd: Order size in USD (alternative to quantity)
            reduce_only: If True, orders can only reduce existing positions
            max_retries: Maximum retry attempts
            retry_backoff_ms: Delay between retries in milliseconds
            total_timeout_seconds: Total timeout before market fallback
            inside_tick_retries: Number of retries using "inside spread" pricing
            max_deviation_pct: Max market movement % to attempt break-even pricing
            trigger_fill_price: Optional fill price from trigger order (for break-even pricing)
            trigger_side: Optional side of trigger order ("buy" or "sell")
            logger: Optional logger instance
            **kwargs: Additional strategy-specific parameters
            
        Returns:
            ExecutionResult with execution details
        """
        if logger is None:
            logger = self.logger
        
        # Get execution configuration
        config = self._get_execution_config(
            reduce_only, max_retries, retry_backoff_ms, total_timeout_seconds, inside_tick_retries
        )
        
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
            
            success, calculated_qty, error_msg = await self._calculate_quantity_from_usd(
                exchange_client, symbol, side, Decimal(str(size_usd))
            )
            if not success:
                return ExecutionResult(
                    success=False,
                    filled=False,
                    error_message=error_msg,
                    execution_mode_used="aggressive_limit_error"
                )
            quantity = calculated_qty
        
        quantity = Decimal(str(quantity))
        target_quantity = quantity
        
        if quantity <= Decimal("0"):
            return ExecutionResult(
                success=True,
                filled=True,
                filled_quantity=Decimal("0"),
                execution_mode_used="aggressive_limit_skip"
            )
        
        # Estimate USD value for logging
        estimated_usd = await self._estimate_usd_value(exchange_client, symbol, side, quantity)
        self._log_execution_start(exchange_name, symbol, quantity, estimated_usd, logger)
        
        # Register websocket callback for event-based tracking
        use_event_based = self._register_websocket_callback(exchange_client)
        if not use_event_based:
            logger.debug(f"ℹ️ Using polling-based order tracking (websocket events not available)")
        
        try:
            # Initialize execution state
            state = ExecutionState(
                accumulated_filled_qty=Decimal("0"),
                accumulated_fill_price=None,
                last_order_filled_qty=Decimal("0"),
                last_order_id=None,
                retries_used=0,
                last_pricing_strategy="unknown",
                execution_success=False,
                execution_error=None,
            )
            
            start_time = time.time()
            
            # Main retry loop
            for retry_count in range(config.max_retries):
                # Check total timeout
                if self._check_timeout(
                    start_time, config.total_timeout_seconds, retry_count,
                    state.retries_used, exchange_name, symbol, logger
                ):
                    break
                
                # Check if target is already filled
                if self._check_target_filled(
                    state.accumulated_filled_qty, target_quantity,
                    exchange_name, symbol, retry_count, logger
                ):
                    state.execution_success = True
                    break
                
                try:
                    # Calculate remaining quantity
                    remaining_qty = target_quantity - state.accumulated_filled_qty

                    # Calculate price based on current fallback mode
                    if state.fallback_mode == "progressive":
                        # Progressive price walking: start at mid-price and walk towards aggressive side
                        price_result = await self._pricer.calculate_progressive_walk_price(
                            exchange_client=exchange_client,
                            symbol=symbol,
                            side=side,
                            attempt_number=state.progressive_attempt_count,
                            max_attempts=getattr(config, 'progressive_walk_max_attempts', 5),
                            step_ticks=getattr(config, 'progressive_walk_step_ticks', 1),
                            min_spread_pct=getattr(config, 'progressive_walk_min_spread_pct', Decimal("0.10")),
                            logger=logger,
                        )
                        state.progressive_attempt_count += 1
                        state.last_pricing_strategy = f"progressive_walk_{state.progressive_attempt_count}"

                        # Check if exhausted progressive attempts
                        max_progressive = getattr(config, 'progressive_walk_max_attempts', 5)
                        if state.progressive_attempt_count >= max_progressive:
                            logger.info(
                                f"📉 [{exchange_name}] Progressive walking exhausted ({state.progressive_attempt_count}/{max_progressive}) "
                                f"for {symbol}. Will fall back to market order."
                            )
                            # Will trigger market fallback after this attempt if no fill

                        # Reset spread skip counter since we're in progressive mode now
                        state.consecutive_wide_spread_skips = 0

                    else:
                        # Aggressive limit pricing (existing logic)
                        price_result = await self._pricer.calculate_aggressive_limit_price(
                            exchange_client=exchange_client,
                            symbol=symbol,
                            side=side,
                            retry_count=retry_count,
                            inside_tick_retries=config.inside_tick_retries,
                            max_deviation_pct=max_deviation_pct,
                            trigger_fill_price=trigger_fill_price,
                            trigger_side=trigger_side,
                            logger=logger,
                        )
                        state.last_pricing_strategy = price_result.pricing_strategy

                        # Validate spread hasn't widened abnormally (only check in aggressive mode)
                        is_acceptable, spread_pct, spread_reason = is_spread_acceptable(
                            price_result.best_bid,
                            price_result.best_ask,
                            check_type=SpreadCheckType.AGGRESSIVE_HEDGE,
                        )

                        if not is_acceptable:
                            logger.warning(
                                f"⚠️ [{exchange_name}] Spread too wide for {symbol} on attempt {retry_count + 1}: "
                                f"{spread_pct*100:.4f}% > threshold "
                                f"(bid={price_result.best_bid}, ask={price_result.best_ask}). "
                                f"Reason: {spread_reason} "
                                f"Skipping aggressive limit and will retry."
                            )

                            # Track consecutive spread failures
                            state.consecutive_wide_spread_skips += 1

                            # Check if should switch to progressive walking
                            fallback_threshold = getattr(config, 'wide_spread_fallback_threshold', 3)
                            if (state.consecutive_wide_spread_skips >= fallback_threshold and
                                state.fallback_mode == "aggressive"):

                                state.fallback_mode = "progressive"
                                state.progressive_attempt_count = 0
                                logger.info(
                                    f"🔄 [{exchange_name}] Switching to progressive price walking for {symbol} "
                                    f"after {state.consecutive_wide_spread_skips} consecutive wide spread attempts. "
                                    f"Will start at mid-price and progressively walk towards aggressive side."
                                )

                            await asyncio.sleep(config.retry_backoff_ms / 1000.0)
                            state.retries_used += 1
                            continue

                    # Round quantity to step size
                    order_quantity = exchange_client.round_to_step(remaining_qty)
                    
                    if order_quantity <= Decimal("0"):
                        logger.warning(
                            f"⚠️ [{exchange_name}] Order quantity rounded to zero for {symbol} "
                            f"(accumulated_filled={state.accumulated_filled_qty}, target_quantity={target_quantity})"
                        )
                        if state.accumulated_filled_qty > Decimal("0"):
                            state.execution_success = True
                        break
                    
                    # Log attempt
                    strategy_info = f"{price_result.pricing_strategy}"
                    if price_result.break_even_strategy and price_result.break_even_strategy != price_result.pricing_strategy:
                        strategy_info += f" (break_even: {price_result.break_even_strategy})"
                    
                    logger.debug(
                        f"🔄 [{exchange_name}] Aggressive limit execution attempt {retry_count + 1}/{config.max_retries} "
                        f"for {symbol}: {strategy_info} @ ${price_result.limit_price} qty={order_quantity} "
                        f"(best_bid=${price_result.best_bid}, best_ask=${price_result.best_ask})"
                    )
                    
                    # Place limit order
                    order_result = await self._place_limit_order(
                        exchange_client, symbol, side, order_quantity,
                        price_result.limit_price, reduce_only, exchange_name, logger
                    )
                    
                    # Handle order placement failure
                    if not order_result.success:
                        error_msg = order_result.error_message or f"Limit order placement failed on {exchange_name}"
                        should_continue, is_fatal = await self._handle_order_placement_failure(
                            error_msg, exchange_name, symbol, price_result.pricing_strategy,
                            retry_count, config.max_retries, config.retry_backoff_ms, logger
                        )
                        
                        if is_fatal:
                            state.execution_error = error_msg
                            break
                        if should_continue:
                            state.retries_used += 1
                            continue
                    
                    # Get order ID
                    order_id = order_result.order_id
                    if not order_id:
                        logger.warning(f"⚠️ [{exchange_name}] No order_id returned for {symbol}")
                        await asyncio.sleep(config.retry_backoff_ms / 1000.0)
                        state.retries_used += 1
                        continue
                    
                    state.last_order_id = order_id
                    
                    # Check remaining timeout
                    elapsed_time = time.time() - start_time
                    remaining_timeout = config.total_timeout_seconds - elapsed_time
                    if remaining_timeout <= 0:
                        try:
                            order_status_check = await exchange_client.get_order_info(order_id)
                            if order_status_check and order_status_check.status not in {"CANCELED", "CANCELLED", "FILLED"}:
                                await exchange_client.cancel_order(order_id)
                        except Exception:
                            pass
                        break
                    
                    # Wait for fill (max 5 seconds per attempt)
                    attempt_timeout = min(5, remaining_timeout)
                    
                    # Wait for order fill
                    recon_result = await self._wait_for_order_fill(
                        exchange_client, use_event_based, order_id, order_quantity,
                        price_result.limit_price, target_quantity, state.accumulated_filled_qty,
                        Decimal("0"), attempt_timeout, price_result.pricing_strategy,
                        retry_count, config.retry_backoff_ms, exchange_name, symbol, logger
                    )
                    
                    # Update state with reconciliation results
                    state.last_order_filled_qty = recon_result.current_order_filled_qty
                    state.accumulated_filled_qty = recon_result.accumulated_filled_qty
                    
                    # Log reconciliation result
                    logger.info(
                        f"🔍 [{exchange_name}] Reconciliation result for {symbol}: "
                        f"filled={recon_result.filled}, "
                        f"filled_qty={recon_result.filled_qty}, "
                        f"accumulated={state.accumulated_filled_qty}/{target_quantity}, "
                        f"partial={recon_result.partial_fill_detected}, "
                        f"error={recon_result.error}"
                    )
                    
                    # Handle errors
                    if recon_result.error and recon_result.error != "Order cancelled without fills":
                        state.execution_error = recon_result.error
                    
                    if recon_result.fill_price:
                        state.accumulated_fill_price = recon_result.fill_price
                    
                    # Log cancellation reason if order was cancelled
                    if recon_result.error == "Order cancelled without fills":
                        await self._log_cancellation_reason(
                            exchange_client, order_id, exchange_name, symbol,
                            retry_count, config.max_retries, logger
                        )
                    
                    # Defensive check: if accumulated fills meet threshold, treat as filled
                    # even if reconciliation result says filled=False (handles timeout edge cases)
                    if not recon_result.filled and self._check_fill_threshold(state.accumulated_filled_qty, target_quantity):
                        logger.warning(
                            f"⚠️ [{exchange_name}] Reconciliation returned filled=False but accumulated fills "
                            f"({state.accumulated_filled_qty}/{target_quantity}) meet threshold. "
                            f"Treating as filled to prevent double-ordering."
                        )
                        # Treat as filled
                        filled_qty = state.accumulated_filled_qty
                        fill_price = state.accumulated_fill_price or price_result.limit_price
                        self._log_fill_success(
                            exchange_name, symbol, fill_price, filled_qty,
                            state.accumulated_filled_qty, target_quantity, retry_count, logger
                        )
                        state.execution_success = True
                        state.retries_used = retry_count + 1
                        break
                    
                    # Check if filled
                    if recon_result.filled and state.accumulated_filled_qty > Decimal("0"):
                        filled_qty = state.accumulated_filled_qty
                        fill_price = state.accumulated_fill_price or price_result.limit_price
                        
                        logger.info(
                            f"✅ [{exchange_name}] Order filled for {symbol}: "
                            f"accumulated={state.accumulated_filled_qty}, target={target_quantity}, "
                            f"threshold={target_quantity * Decimal('0.99')}"
                        )
                        
                        if self._check_fill_threshold(state.accumulated_filled_qty, target_quantity):
                            self._log_fill_success(
                                exchange_name, symbol, fill_price, filled_qty,
                                state.accumulated_filled_qty, target_quantity, retry_count, logger
                            )
                            state.execution_success = True
                            state.retries_used = retry_count + 1
                            break
                        else:
                            # Partial fill - continue retrying
                            logger.info(
                                f"📊 [{exchange_name}] Partial fill {filled_qty} fills "
                                f"(total: {state.accumulated_filled_qty}/{target_quantity}) for {symbol}. "
                                f"Continuing to fill remainder."
                            )
                            if not recon_result.partial_fill_detected:
                                try:
                                    await exchange_client.cancel_order(order_id)
                                except Exception:
                                    pass
                            await asyncio.sleep(config.retry_backoff_ms / 1000.0)
                            state.retries_used += 1
                            continue
                    
                    elif recon_result.partial_fill_detected:
                        # Partial fill detected - continue retrying
                        logger.debug(
                            f"📊 [{exchange_name}] Partial fill {state.accumulated_filled_qty} fills "
                            f"(total: {state.accumulated_filled_qty}/{target_quantity}) for {symbol}. "
                            f"Retrying for remainder."
                        )
                        await asyncio.sleep(config.retry_backoff_ms / 1000.0)
                        state.retries_used += 1
                        continue
                    
                    elif not recon_result.filled:
                        # Order not filled - handle unfilled order
                        await self._handle_unfilled_order(
                            exchange_client, order_id, attempt_timeout, exchange_name,
                            symbol, retry_count, config.max_retries, logger
                        )
                        
                        # Check for fatal errors
                        if state.execution_error and state.execution_error != "Order cancelled without fills":
                            logger.warning(
                                f"⚠️ [{exchange_name}] Fatal error detected: {state.execution_error}. "
                                f"Stopping retries for {symbol}"
                            )
                            break
                        
                        await asyncio.sleep(config.retry_backoff_ms / 1000.0)
                        state.retries_used += 1
                        continue
                
                except Exception as exc:
                    logger.error(
                        f"❌ [{exchange_name}] Aggressive limit execution attempt {retry_count + 1} "
                        f"exception for {symbol}: {exc}"
                    )
                    state.execution_error = str(exc)
                    await asyncio.sleep(config.retry_backoff_ms / 1000.0)
                    state.retries_used += 1
            
            # Final reconciliation check
            if not state.execution_success and state.last_order_id:
                state.execution_success, state.accumulated_filled_qty, state.accumulated_fill_price = \
                    await self._perform_final_reconciliation(
                        exchange_client, state.last_order_id, state.last_order_filled_qty,
                        state.accumulated_filled_qty, state.accumulated_fill_price,
                        target_quantity, exchange_name, symbol, logger
                    )
            
            # Fallback to market if limit execution failed
            if not state.execution_success:
                return await self._execute_market_fallback(
                    exchange_client, symbol, side, quantity, target_quantity,
                    state.accumulated_filled_qty, state.accumulated_fill_price,
                    reduce_only, exchange_name, state.execution_error, logger
                )
            
            # Success case
            return ExecutionResult(
                success=True,
                filled=True,
                filled_quantity=state.accumulated_filled_qty,
                fill_price=state.accumulated_fill_price,
                execution_mode_used=f"aggressive_limit_{state.last_pricing_strategy}",
                order_id=state.last_order_id,
            )
        
        finally:
            # Restore original websocket callback
            self._restore_websocket_callback(exchange_client)