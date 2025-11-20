"""
Atomic Multi-Order Executor - orchestrates delta-neutral order placement.

The implementation is split across helper modules to keep the executor focused on
high-level orchestration while contexts, hedging, and utility logic live in their
own files.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from helpers.unified_logger import get_core_logger, log_stage
from strategies.execution.core.utils import coerce_decimal

# Keep imports patchable for tests that monkeypatch LiquidityAnalyzer
from strategies.execution.core.liquidity_analyzer import (
    LiquidityAnalyzer as LiquidityAnalyzer,  # re-export for test patches
)

from .contexts import OrderContext
from .components import (
    ExecutionState,
    ExposureVerifier,
    FullFillHandler,
    HedgeManager,
    ImbalanceAnalyzer,
    PartialFillHandler,
    PostExecutionValidator,
    PreFlightChecker,
    RollbackManager,
    WebsocketManager,
)
from .utils import (
    apply_result_to_context,
    context_to_filled_dict,
    execution_result_to_dict,
    reconcile_context_after_cancel,
)


@dataclass
class OrderSpec:
    """
    Specification for a single order in atomic batch.
    """

    exchange_client: Any
    symbol: str
    side: str  # "buy" or "sell"
    size_usd: Decimal
    quantity: Optional[Decimal] = None
    execution_mode: str = "limit_only"
    timeout_seconds: float = 60.0
    limit_price_offset_pct: Optional[Decimal] = None
    reduce_only: bool = False  # If True, can only close/reduce positions (bypasses min notional)


@dataclass
class AtomicExecutionResult:
    """
    Result of atomic multi-order execution.
    """

    success: bool
    all_filled: bool
    filled_orders: List[Dict[str, Any]]
    partial_fills: List[Dict[str, Any]]
    total_slippage_usd: Decimal
    execution_time_ms: int
    error_message: Optional[str] = None
    rollback_performed: bool = False
    rollback_cost_usd: Optional[Decimal] = None
    residual_imbalance_usd: Decimal = Decimal("0")


class AtomicMultiOrderExecutor:
    """Executes multiple orders atomically—either all succeed or the trade is unwound."""

    def __init__(self, price_provider=None, account_name: Optional[str] = None, notification_service: Optional[Any] = None, leverage_validator: Optional[Any] = None) -> None:
        self.price_provider = price_provider
        self.logger = get_core_logger("atomic_multi_order")
        self._hedge_manager = HedgeManager(price_provider=price_provider)
        self._preflight_checker = PreFlightChecker(
            price_provider=price_provider,
            leverage_validator=leverage_validator,
            notification_service=notification_service,
            logger=self.logger
        )
        self._rollback_manager = RollbackManager(logger=self.logger)
        self._imbalance_analyzer = ImbalanceAnalyzer(logger=self.logger)
        self._exposure_verifier = ExposureVerifier(logger=self.logger)
        self._post_trade_max_imbalance_pct = Decimal("0.02")  # 2% net exposure tolerance
        self._post_trade_base_tolerance = Decimal("0.0001")  # residual quantity tolerance
        
        # Initialize component handlers
        self._full_fill_handler = FullFillHandler(
            hedge_manager=self._hedge_manager,
            rollback_manager=self._rollback_manager,
            imbalance_analyzer=self._imbalance_analyzer,
            logger=self.logger,
            executor=self,
        )
        self._partial_fill_handler = PartialFillHandler(
            hedge_manager=self._hedge_manager,
            rollback_manager=self._rollback_manager,
            logger=self.logger,
            executor=self,
        )
        self._post_execution_validator = PostExecutionValidator(
            imbalance_analyzer=self._imbalance_analyzer,
            exposure_verifier=self._exposure_verifier,
            logger=self.logger,
            post_trade_max_imbalance_pct=self._post_trade_max_imbalance_pct,
            post_trade_base_tolerance=self._post_trade_base_tolerance,
        )
        self._websocket_manager = WebsocketManager(logger=self.logger)
        
        self.account_name = account_name
        self.notification_service = notification_service
        # ⭐ Optional shared leverage validator (from strategy) for caching leverage info
        # If provided, use this instead of creating a new instance to benefit from caching
        self._leverage_validator = leverage_validator
        # Store normalized leverage per (exchange_name, symbol) for margin calculations
        # This ensures balance checks use the normalized leverage, not the symbol's max leverage
        self._normalized_leverage: Dict[Tuple[str, str], int] = {}
        # Track margin error state per (exchange_name, symbol) to prevent notification spam
        # Key: (exchange_name, symbol), Value: True if we've already notified about insufficient margin
        # Resets to False when margin becomes sufficient again
        self._margin_error_notified: Dict[Tuple[str, str], bool] = {}
        # Track liquidation risk notification state per (exchange_name, symbol) to prevent notification spam
        # Key: (exchange_name, symbol), Value: True if we've already notified about liquidation risk
        # Resets to False when liquidation risk becomes acceptable again
        self._liquidation_risk_notified: Dict[Tuple[str, str], bool] = {}

    async def execute_atomically(
        self,
        orders: List[OrderSpec],
        rollback_on_partial: bool = True,
        pre_flight_check: bool = True,
        skip_preflight_leverage: bool = False,
        stage_prefix: Optional[str] = None,
        enable_liquidation_prevention: Optional[bool] = None,  # Config parameter
        min_liquidation_distance_pct: Optional[Decimal] = None,  # Config parameter
    ) -> AtomicExecutionResult:
        # Store stage_prefix for use in rollback logic
        self._current_stage_prefix = stage_prefix
        start_time = time.time()
        elapsed_ms = lambda: int((time.time() - start_time) * 1000)

        if not orders:
            self.logger.info("No orders supplied; skipping atomic execution.")
            # Create empty contexts list for result building
            empty_contexts: List[OrderContext] = []
            return self._build_execution_result(
                contexts=empty_contexts,
                orders=orders,
                elapsed_ms=elapsed_ms(),
                success=True,
                all_filled=True,
                error_message=None,
                rollback_performed=False,
                rollback_cost=Decimal("0"),
            )

        # Initialize variables for exception handling (before try block so accessible in finally)
        _exception = None
        rollback_performed = False
        rollback_cost = Decimal("0")
        execution_successful = False  # Track if execution succeeded to prevent rollback in finally block
        contexts: List[OrderContext] = []
        
        try:
            compose_stage = lambda *parts: self._compose_stage_id(stage_prefix, *parts)

            self.logger.info(
                f"Starting atomic execution of {len(orders)} orders "
                f"(rollback_on_partial={rollback_on_partial})"
            )

            if pre_flight_check:
                log_stage(self.logger, "Pre-flight Checks", icon="🔍", stage_id=compose_stage("1"))
                # Use provided config or default to None (disabled)
                liquidation_prevention_enabled = enable_liquidation_prevention if enable_liquidation_prevention is not None else False
                liquidation_distance_threshold = min_liquidation_distance_pct
                
                preflight_ok, preflight_error = await self._preflight_checker.check(
                    orders,
                    skip_leverage_check=skip_preflight_leverage,
                    stage_prefix=compose_stage("1"),
                    normalized_leverage=self._normalized_leverage,
                    margin_error_notified=self._margin_error_notified,
                    liquidation_risk_notified=self._liquidation_risk_notified,
                    enable_liquidation_prevention=liquidation_prevention_enabled,
                    min_liquidation_distance_pct=liquidation_distance_threshold,
                )
                if not preflight_ok:
                    # Create empty contexts list for result building
                    empty_contexts: List[OrderContext] = []
                    return self._build_execution_result(
                        contexts=empty_contexts,
                        orders=orders,
                        elapsed_ms=elapsed_ms(),
                        success=False,
                        all_filled=False,
                        error_message=f"Pre-flight check failed: {preflight_error}",
                        rollback_performed=False,
                        rollback_cost=Decimal("0"),
                    )

            log_stage(self.logger, "Order Placement", icon="🚀", stage_id=compose_stage("2"))
            self.logger.info("🚀 Placing all orders simultaneously...")

            # Set up websocket callbacks for real-time fill tracking
            exchange_clients = {spec.exchange_client for spec in orders}
            self._websocket_manager.register_callbacks(list(exchange_clients))

            # Initialize execution state
            task_map: Dict[asyncio.Task, OrderContext] = {}
            pending_tasks: set[asyncio.Task] = set()

            for spec in orders:
                cancel_event = asyncio.Event()
                task = asyncio.create_task(self._place_single_order(spec, cancel_event=cancel_event))
                ctx = OrderContext(spec=spec, cancel_event=cancel_event, task=task)
                contexts.append(ctx)
                task_map[task] = ctx
                pending_tasks.add(task)

            # Create execution state manager
            state = ExecutionState(
                contexts=contexts,
                task_map=task_map,
                pending_tasks=pending_tasks,
                logger=self.logger,
                executor=self,
            )

            trigger_ctx: Optional[OrderContext] = None
            hedge_error: Optional[str] = None

            # Main execution loop
            while not state.is_complete():
                update = await state.process_next_batch()

                # Priority 1: Handle full fills first (highest priority)
                if update.has_full_fill and trigger_ctx is None:
                    potential_trigger = state.get_full_fill_trigger(update.newly_filled)
                    if potential_trigger:
                        trigger_ctx = potential_trigger
                        other_contexts = [c for c in contexts if c is not trigger_ctx]
                        
                        full_fill_result = await self._full_fill_handler.handle(
                            trigger_ctx=trigger_ctx,
                            other_contexts=other_contexts,
                            contexts=contexts,
                            pending_tasks=state.pending_tasks,
                            rollback_on_partial=rollback_on_partial,
                            stage_prefix=stage_prefix,
                        )
                        
                        hedge_error = full_fill_result.error_message
                        if full_fill_result.rollback_performed:
                            rollback_performed = True
                            rollback_cost = full_fill_result.rollback_cost
                        
                        if full_fill_result.success:
                            # Hedge succeeded and orders are balanced - mark success and break
                            execution_successful = True
                            self.logger.info(
                                "🎯 Execution successful - both orders filled and balanced. "
                                "Skipping redundant post-execution checks."
                            )
                            break  # Exit the loop, will return success at the end of try block
                        else:
                            break

                # Priority 2: Handle partial fills that have COMPLETED (timed out or canceled)
                if trigger_ctx is None and update.has_partial_fill:
                    partial_ctx = state.get_partial_fill(update.partial_fill_contexts)
                    if partial_ctx:
                        partial_result = await self._partial_fill_handler.handle(
                            partial_ctx=partial_ctx,
                            contexts=contexts,
                            rollback_on_partial=rollback_on_partial,
                            stage_prefix=stage_prefix,
                        )
                        
                        if partial_result.success:
                            # Partial fill hedge succeeded
                            break
                        else:
                            if partial_result.rollback_performed:
                                rollback_performed = True
                                rollback_cost = partial_result.rollback_cost
                            break

                # Priority 3: Handle retryable failures (post-only violations)
                if not update.all_completed and update.has_retryable:
                    for ctx in update.retryable_contexts:
                        state.retry_context(ctx, self._place_single_order)

                if update.all_completed:
                    break

            # Wait for any remaining tasks
            remaining_tasks = [ctx.task for ctx in contexts if not ctx.completed]
            if remaining_tasks:
                await asyncio.gather(*remaining_tasks, return_exceptions=True)
            for ctx in contexts:
                await reconcile_context_after_cancel(ctx, self.logger)



            exec_ms = elapsed_ms()
            
            # CRITICAL: If execution succeeded (both orders filled and balanced), return success immediately
            # This prevents the post-execution imbalance checks from re-evaluating and potentially triggering a rollback
            if execution_successful:
                self.logger.info("✅ Returning success result - both orders filled and balanced.")
                return self._build_execution_result(
                    contexts=contexts,
                    orders=orders,
                    elapsed_ms=exec_ms,
                    success=True,
                    all_filled=True,
                    error_message=None,
                    rollback_performed=False,
                    rollback_cost=Decimal("0"),
                )
            
            # Post-execution validation
            validation = await self._post_execution_validator.validate(
                contexts=contexts,
                orders=orders,
                rollback_performed=rollback_performed,
                hedge_error=hedge_error,
                rollback_on_partial=rollback_on_partial,
                stage_prefix=stage_prefix,
            )
            
            # Handle rollback if validator says we should
            if validation.should_rollback:
                rollback_cost = await self._rollback_manager.perform_emergency_rollback(
                    contexts=contexts,
                    reason=validation.error_message or "Critical imbalance",
                    imbalance_tokens=validation.imbalance_tokens,
                    imbalance_pct=validation.imbalance_pct,
                    stage_prefix=stage_prefix,
                )
                return self._build_execution_result(
                    contexts=contexts,
                    orders=orders,
                    elapsed_ms=exec_ms,
                    success=False,
                    all_filled=validation.all_filled,
                    error_message=f"Rolled back: {validation.error_message}",
                    rollback_performed=True,
                    rollback_cost=rollback_cost,
                )
            
            # Return validation result
            return self._build_execution_result(
                contexts=contexts,
                orders=orders,
                elapsed_ms=exec_ms,
                success=validation.passed,
                all_filled=validation.all_filled,
                error_message=validation.error_message,
                rollback_performed=validation.should_rollback,
                rollback_cost=validation.rollback_cost,
            )

        except Exception as exc:
            self.logger.error(f"Atomic execution failed: {exc}", exc_info=True)
            # Store exception for use in finally block
            _exception = exc
        
        finally:
            # Cleanup: Restore original callbacks and clear registries
            self._websocket_manager.cleanup()
            
            # CRITICAL: Only return failure result if execution did NOT succeed
            # If execution succeeded, the try block already returned success - don't override it
            if not execution_successful:
                # Execution did not succeed - proceed with rollback checks and return failure result
                # Check if we need to rollback - only if rollback wasn't already performed
                rollback_cost = None
                
                # Also check if contexts have been cleared (filled_quantity == 0 for all)
                contexts_cleared = True
                if contexts:
                    for ctx in contexts:
                        if ctx.filled_quantity > Decimal("0"):
                            contexts_cleared = False
                            break
                
                filled_orders_count = sum(1 for ctx in contexts if ctx.result and ctx.filled_quantity > Decimal("0"))
                
                # Only rollback if:
                # 1. Rollback wasn't already performed (flag check)
                # 2. Contexts haven't been cleared (safety check - rollback_manager clears them)
                # 3. There are filled orders
                # 4. Rollback on partial is enabled
                if not rollback_performed and not contexts_cleared and filled_orders_count > 0 and rollback_on_partial:
                    filled_orders_list = [
                        ctx.result
                        for ctx in contexts
                        if ctx.result and ctx.filled_quantity > Decimal("0")
                    ]
                    if filled_orders_list:
                        rollback_cost = await self._rollback_manager.rollback(filled_orders_list, stage_prefix=stage_prefix)
                        # Clear contexts after rollback to prevent any further rollback attempts
                        # (rollback() doesn't clear contexts, but perform_emergency_rollback() does)
                        for ctx in contexts:
                            ctx.filled_quantity = Decimal("0")
                            ctx.filled_usd = Decimal("0")
                        rollback_performed = True  # Mark as performed
                
                # Get exception message safely
                exc_message = str(_exception) if _exception is not None else "Unknown error"

                return self._build_execution_result(
                    contexts=contexts,
                    orders=orders,
                    elapsed_ms=elapsed_ms(),
                    success=False,
                    all_filled=False,
                    error_message=exc_message,
                    rollback_performed=bool(rollback_cost and rollback_on_partial),
                    rollback_cost=rollback_cost or Decimal("0"),
                )
            # If execution_successful is True, don't return anything - let try block's return propagate

    def _is_order_fully_filled(
        self,
        ctx: OrderContext,
        tolerance: Decimal = Decimal("0.0001")
    ) -> bool:
        """
        Check if an order is actually fully filled (not just partially filled).
        
        Args:
            ctx: Order context to check
            tolerance: Tolerance for rounding differences
            
        Returns:
            True if order is fully filled, False otherwise
        """
        remaining_qty = ctx.remaining_quantity
        result_filled = ctx.result and ctx.result.get("filled", False)
        
        # Order is fully filled if:
        # 1. remaining_quantity is zero (or within tolerance for rounding)
        # 2. AND the result indicates it's filled (not just a partial fill timeout)
        return remaining_qty <= tolerance and result_filled

    def _create_error_result_dict(
        self,
        ctx: OrderContext,
        error: str
    ) -> Dict[str, Any]:
        """
        Create an error result dictionary for a failed order task.
        
        Args:
            ctx: Order context that failed
            error: Error message
            
        Returns:
            Error result dictionary
        """
        return {
            "success": False,
            "filled": False,
            "error": error,
            "order_id": None,
            "exchange_client": ctx.spec.exchange_client,
            "symbol": ctx.spec.symbol,
            "side": ctx.spec.side,
            "slippage_usd": Decimal("0"),
            "execution_mode_used": "error",
            "filled_quantity": Decimal("0"),
            "fill_price": None,
        }

    def _build_execution_result(
        self,
        contexts: List[OrderContext],
        orders: List[OrderSpec],
        elapsed_ms: int,
        success: bool,
        all_filled: bool,
        error_message: Optional[str],
        rollback_performed: bool,
        rollback_cost: Decimal,
    ) -> AtomicExecutionResult:
        """
        Build AtomicExecutionResult from execution state.
        
        Args:
            contexts: List of order contexts
            orders: Original order specs
            elapsed_ms: Execution time in milliseconds
            success: Whether execution succeeded
            all_filled: Whether all orders filled
            error_message: Error message if any
            rollback_performed: Whether rollback was performed
            rollback_cost: Cost of rollback if performed
            
        Returns:
            AtomicExecutionResult instance
        """
        filled_orders = [
            ctx.result for ctx in contexts if ctx.result and ctx.filled_quantity > Decimal("0")
        ]
        partial_fills = [
            {"spec": ctx.spec, "result": ctx.result}
            for ctx in contexts
            if not (ctx.result and ctx.filled_quantity > Decimal("0"))
        ]
        
        total_slippage = sum(
            coerce_decimal(order.get("slippage_usd")) or Decimal("0") for order in filled_orders
        )
        # Note: Returns quantity imbalance (tokens), not USD, for true delta-neutrality
        total_long_tokens, total_short_tokens, imbalance_tokens, _ = self._imbalance_analyzer.calculate_imbalance(contexts)
        
        return AtomicExecutionResult(
            success=success,
            all_filled=all_filled,
            filled_orders=filled_orders if not rollback_performed else [],
            partial_fills=partial_fills,
            total_slippage_usd=total_slippage if not rollback_performed else Decimal("0"),
            execution_time_ms=elapsed_ms,
            error_message=error_message,
            rollback_performed=rollback_performed,
            rollback_cost_usd=rollback_cost,
            # Note: residual_imbalance_usd field name kept for backward compatibility,
            # but value is actually quantity imbalance (tokens), not USD
            residual_imbalance_usd=imbalance_tokens if not rollback_performed else Decimal("0"),
        )

    async def _place_single_order(
        self, spec: OrderSpec, cancel_event: Optional[asyncio.Event] = None
    ) -> Dict[str, Any]:
        """Place a single order from spec and return a normalised result dictionary."""
        # Lazy imports to avoid circular dependency
        from strategies.execution.core.execution_types import ExecutionMode
        from strategies.execution.core.order_executor import OrderExecutor

        executor = OrderExecutor(price_provider=self.price_provider)

        mode_map = {
            "limit_only": ExecutionMode.LIMIT_ONLY,
            "limit_with_fallback": ExecutionMode.LIMIT_WITH_FALLBACK,
            "market_only": ExecutionMode.MARKET_ONLY,
            "adaptive": ExecutionMode.ADAPTIVE,
        }

        execution_mode = mode_map.get(spec.execution_mode, ExecutionMode.LIMIT_WITH_FALLBACK)

        result = await executor.execute_order(
            exchange_client=spec.exchange_client,
            symbol=spec.symbol,
            side=spec.side,
            size_usd=spec.size_usd,
            quantity=spec.quantity,
            mode=execution_mode,
            timeout_seconds=spec.timeout_seconds,
            limit_price_offset_pct=spec.limit_price_offset_pct,
            cancel_event=cancel_event,
            reduce_only=spec.reduce_only,
        )

        return execution_result_to_dict(spec, result)

    def _compose_stage_id(self, stage_prefix: Optional[str], *parts: str) -> Optional[str]:
        if stage_prefix:
            if parts:
                return ".".join([stage_prefix, *parts])
            return stage_prefix
        if parts:
            return ".".join(parts)
        return None
    
    def _register_order_context(self, ctx: "OrderContext", order_id: str) -> None:
        """Delegate to websocket manager for order context registration."""
        self._websocket_manager.register_order_context(ctx, order_id)
