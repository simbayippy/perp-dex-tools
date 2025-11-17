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

# Keep imports patchable for tests that monkeypatch LiquidityAnalyzer
from strategies.execution.core.liquidity_analyzer import (
    LiquidityAnalyzer as LiquidityAnalyzer,  # re-export for test patches
)

from .contexts import OrderContext
from .components import (
    ExposureVerifier,
    HedgeManager,
    ImbalanceAnalyzer,
    PreFlightChecker,
    RollbackManager,
)
from .utils import (
    apply_result_to_context,
    context_to_filled_dict,
    coerce_decimal,
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

    async def execute_atomically(
        self,
        orders: List[OrderSpec],
        rollback_on_partial: bool = True,
        pre_flight_check: bool = True,
        skip_preflight_leverage: bool = False,
        stage_prefix: Optional[str] = None,
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

        try:
            compose_stage = lambda *parts: self._compose_stage_id(stage_prefix, *parts)

            self.logger.info(
                f"Starting atomic execution of {len(orders)} orders "
                f"(rollback_on_partial={rollback_on_partial})"
            )

            if pre_flight_check:
                log_stage(self.logger, "Pre-flight Checks", icon="🔍", stage_id=compose_stage("1"))
                preflight_ok, preflight_error = await self._preflight_checker.check(
                    orders,
                    skip_leverage_check=skip_preflight_leverage,
                    stage_prefix=compose_stage("1"),
                    normalized_leverage=self._normalized_leverage,
                    margin_error_notified=self._margin_error_notified,
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

            contexts: List[OrderContext] = []
            task_map: Dict[asyncio.Task, OrderContext] = {}
            pending_tasks: set[asyncio.Task] = set()

            for spec in orders:
                cancel_event = asyncio.Event()
                task = asyncio.create_task(self._place_single_order(spec, cancel_event=cancel_event))
                ctx = OrderContext(spec=spec, cancel_event=cancel_event, task=task)
                contexts.append(ctx)
                task_map[task] = ctx
                pending_tasks.add(task)

            trigger_ctx: Optional[OrderContext] = None
            hedge_error: Optional[str] = None
            rollback_performed = False
            rollback_cost = Decimal("0")

            while pending_tasks:
                done, pending_tasks = await asyncio.wait(
                    pending_tasks, return_when=asyncio.FIRST_COMPLETED
                )
                newly_filled: List[OrderContext] = []
                retryable_contexts: List[OrderContext] = []
                partial_fill_contexts: List[OrderContext] = []

                for task in done:
                    ctx = task_map[task]
                    previous_fill = ctx.filled_quantity
                    try:
                        result = task.result()
                    except Exception as exc:  # pragma: no cover - defensive
                        self.logger.error(f"Order task failed for {ctx.spec.symbol}: {exc}")
                        result = self._create_error_result_dict(ctx, str(exc))
                    apply_result_to_context(ctx, result)
                    
                    # Check for retryable failures (post-only violations)
                    if ctx.result and ctx.result.get("retryable", False):
                        retryable_contexts.append(ctx)
                    
                    # Check for fills
                    if ctx.filled_quantity > previous_fill:
                        newly_filled.append(ctx)
                    
                    # Check for partial fills that have COMPLETED (timed out or canceled)
                    # Only hedge partial fills when the order task is done, not while it's still active
                    if ctx.completed and ctx.filled_quantity > Decimal("0"):
                        is_fully_filled = self._is_order_fully_filled(ctx)
                        # Only treat as partial fill if:
                        # 1. Order has completed (timed out or canceled)
                        # 2. Has fills (filled_quantity > 0)
                        # 3. Not fully filled (remaining_quantity > 0)
                        # 4. Not a retryable failure (post-only violations get retried, not hedged)
                        if not is_fully_filled and not (ctx.result and ctx.result.get("retryable", False)):
                            partial_fill_contexts.append(ctx)

                all_completed = all(context.completed for context in contexts)

                # Priority 1: Handle full fills first (highest priority)
                if newly_filled and trigger_ctx is None:
                    # Check if the newly filled order is actually fully filled
                    potential_trigger = newly_filled[0]
                    is_fully_filled = self._is_order_fully_filled(potential_trigger)
                    
                    if is_fully_filled:
                        trigger_ctx = potential_trigger
                        other_contexts = [c for c in contexts if c is not trigger_ctx]
                        
                        hedge_success, hedge_error, rollback_performed, rollback_cost = await self._handle_full_fill_trigger(
                            trigger_ctx=trigger_ctx,
                            other_contexts=other_contexts,
                            contexts=contexts,
                            pending_tasks=pending_tasks,
                            rollback_on_partial=rollback_on_partial,
                        )
                        
                        if hedge_success:
                            all_completed = True
                        else:
                            break

                # Priority 2: Handle partial fills that have COMPLETED (timed out or canceled)
                # Only hedge partial fills when the order task is done, not while it's still active
                if trigger_ctx is None and partial_fill_contexts:
                    partial_ctx = partial_fill_contexts[0]  # Handle first partial fill
                    exchange_name = partial_ctx.spec.exchange_client.get_exchange_name().upper()
                    symbol = partial_ctx.spec.symbol
                    filled_qty = partial_ctx.filled_quantity
                    self.logger.info(
                        f"⚡ [{exchange_name}] Partial fill completed (timed out/canceled) for {symbol} ({filled_qty}). "
                        f"Cancelling other side and hedging immediately."
                    )
                    
                    # Cancel other contexts
                    other_contexts = [c for c in contexts if c is not partial_ctx]
                    for other_ctx in other_contexts:
                        other_ctx.cancel_event.set()
                    
                    # Wait for cancellations
                    pending_cancels = [c.task for c in other_contexts if not c.completed]
                    if pending_cancels:
                        await asyncio.gather(*pending_cancels, return_exceptions=True)
                    
                    # Reconcile after cancel
                    for other_ctx in other_contexts:
                        await reconcile_context_after_cancel(other_ctx, self.logger)
                    
                    # Set hedge target for other contexts (accounting for multipliers)
                    for other_ctx in other_contexts:
                        trigger_qty = partial_ctx.filled_quantity
                        if not isinstance(trigger_qty, Decimal):
                            trigger_qty = Decimal(str(trigger_qty))
                        trigger_qty = trigger_qty.copy_abs()
                        
                        trigger_multiplier = partial_ctx.spec.exchange_client.get_quantity_multiplier(partial_ctx.spec.symbol)
                        ctx_multiplier = other_ctx.spec.exchange_client.get_quantity_multiplier(other_ctx.spec.symbol)
                        
                        actual_tokens = trigger_qty * Decimal(str(trigger_multiplier))
                        target_qty = actual_tokens / Decimal(str(ctx_multiplier))
                        
                        spec_qty = getattr(other_ctx.spec, "quantity", None)
                        if spec_qty is not None:
                            spec_qty_dec = Decimal(str(spec_qty))
                            if target_qty > spec_qty_dec * Decimal("1.1"):
                                target_qty = spec_qty_dec
                        
                        if target_qty < Decimal("0"):
                            target_qty = Decimal("0")
                        other_ctx.hedge_target_quantity = target_qty
                    
                    # Hedge immediately
                    hedge_success, hedge_error = await self._hedge_manager.hedge(
                        partial_ctx, contexts, self.logger
                    )
                    
                    if hedge_success:
                        all_completed = True
                    else:
                        if rollback_on_partial:
                            rollback_performed = True
                            rollback_cost = await self._rollback_manager.perform_emergency_rollback(
                                contexts, "Partial fill hedge failure", 
                                Decimal("0"), Decimal("0"), stage_prefix=stage_prefix
                            )
                        break

                # Priority 3: Handle retryable failures (post-only violations)
                # Only retry if we haven't completed execution
                if not all_completed:
                    for ctx in retryable_contexts:
                        exchange_name = ctx.spec.exchange_client.get_exchange_name().upper()
                        symbol = ctx.spec.symbol
                        self.logger.info(
                            f"🔄 [{exchange_name}] Post-only violation detected for {symbol}. "
                            f"Retrying immediately with fresh BBO."
                        )
                        # Place new order with fresh BBO
                        cancel_event = asyncio.Event()
                        task = asyncio.create_task(self._place_single_order(ctx.spec, cancel_event=cancel_event))
                        ctx.cancel_event = cancel_event
                        ctx.task = task
                        ctx.completed = False
                        ctx.result = None
                        task_map[task] = ctx
                        pending_tasks.add(task)

                if all_completed:
                    break

            remaining_tasks = [ctx.task for ctx in contexts if not ctx.completed]
            if remaining_tasks:
                await asyncio.gather(*remaining_tasks, return_exceptions=True)
            for ctx in contexts:
                await reconcile_context_after_cancel(ctx, self.logger)



            exec_ms = elapsed_ms()
            total_long_tokens, total_short_tokens, imbalance_tokens, imbalance_pct = self._imbalance_analyzer.calculate_imbalance(contexts)
            imbalance_tolerance = Decimal("0.01")  # 1% tolerance for quantity imbalance

            if rollback_performed:
                return self._build_execution_result(
                    contexts=contexts,
                    orders=orders,
                    elapsed_ms=exec_ms,
                    success=False,
                    all_filled=False,
                    error_message=hedge_error or "Rolled back after hedge failure",
                    rollback_performed=True,
                    rollback_cost=rollback_cost,
                )

            # Check if all orders filled
            filled_orders_count = sum(1 for ctx in contexts if ctx.result and ctx.filled_quantity > Decimal("0"))
            if filled_orders_count == len(orders):
                # Check if quantity imbalance is within acceptable bounds (1% threshold)
                is_critical, _, _ = self._imbalance_analyzer.check_critical_imbalance(total_long_tokens, total_short_tokens)
                
                if is_critical:
                    self.logger.error(
                        f"⚠️ CRITICAL QUANTITY IMBALANCE detected despite all orders filled: "
                        f"longs={total_long_tokens:.6f} tokens, shorts={total_short_tokens:.6f} tokens, "
                        f"imbalance={imbalance_tokens:.6f} tokens ({imbalance_pct*100:.2f}%). Triggering emergency rollback."
                    )
                    rollback_performed = True
                    rollback_cost = await self._rollback_manager.perform_emergency_rollback(
                        contexts, "all filled imbalance", imbalance_tokens, imbalance_pct, stage_prefix=stage_prefix
                    )
                    return self._build_execution_result(
                        contexts=contexts,
                        orders=orders,
                        elapsed_ms=exec_ms,
                        success=False,
                        all_filled=False,
                        error_message=f"Rolled back due to critical quantity imbalance: {imbalance_tokens:.6f} tokens ({imbalance_pct*100:.2f}%)",
                        rollback_performed=True,
                        rollback_cost=rollback_cost,
                    )
                elif imbalance_pct > imbalance_tolerance:
                    self.logger.warning(
                        f"Minor quantity imbalance detected after hedge: longs={total_long_tokens:.6f} tokens, "
                        f"shorts={total_short_tokens:.6f} tokens, imbalance={imbalance_tokens:.6f} tokens "
                        f"({imbalance_pct*100:.2f}% within 1% tolerance)"
                    )

                post_trade = await self._exposure_verifier.verify_post_trade_exposure(contexts)
                if post_trade is not None:
                    net_usd = post_trade.get("net_usd", Decimal("0"))
                    net_pct = post_trade.get("net_pct", Decimal("0"))
                    net_qty = post_trade.get("net_qty", Decimal("0"))
                    # Note: net_qty from post_trade is already in actual tokens (from exchange snapshots)
                    # Use net_qty for quantity comparison, net_usd is kept for logging only
                    imbalance_tokens = max(imbalance_tokens, net_qty)

                    if net_usd > Decimal("0"):
                        if net_pct > self._post_trade_max_imbalance_pct:
                            self.logger.warning(
                                "⚠️ Post-trade exposure detected after hedging: "
                                f"net_qty={net_qty:.6f}, net_usd=${net_usd:.4f} ({net_pct*100:.2f}%)."
                            )
                        elif net_qty > self._post_trade_base_tolerance:
                            self.logger.debug(
                                "Post-trade exposure within tolerance: "
                                f"net_qty={net_qty:.6f}, net_usd=${net_usd:.4f} ({net_pct*100:.2f}%)."
                            )
                
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

            # Critical fix: Check for dangerous quantity imbalance and rollback if needed
            filled_orders_count = sum(1 for ctx in contexts if ctx.result and ctx.filled_quantity > Decimal("0"))
            error_message = hedge_error or f"Partial fill: {filled_orders_count}/{len(orders)}"
            if imbalance_pct > imbalance_tolerance:
                self.logger.error(
                    f"Quantity imbalance detected after hedge: longs={total_long_tokens:.6f} tokens, "
                    f"shorts={total_short_tokens:.6f} tokens, imbalance={imbalance_tokens:.6f} tokens ({imbalance_pct*100:.2f}%)"
                )
                imbalance_msg = f"quantity imbalance {imbalance_tokens:.6f} tokens ({imbalance_pct*100:.2f}%)"
                error_message = f"{error_message}; {imbalance_msg}" if error_message else imbalance_msg
                
                # If we have a significant imbalance and rollback is enabled, close filled positions
                if rollback_on_partial and filled_orders_count > 0:
                    is_critical, _, _ = self._imbalance_analyzer.check_critical_imbalance(total_long_tokens, total_short_tokens)
                    if is_critical:
                        self.logger.warning(
                            f"⚠️ Critical quantity imbalance {imbalance_tokens:.6f} tokens ({imbalance_pct*100:.2f}%) "
                            f"detected after retries exhausted. Initiating rollback to close {filled_orders_count} filled positions."
                        )
                        rollback_cost = await self._rollback_manager.perform_emergency_rollback(
                            contexts, "retries exhausted", imbalance_tokens, imbalance_pct, stage_prefix=stage_prefix
                        )
                        return self._build_execution_result(
                            contexts=contexts,
                            orders=orders,
                            elapsed_ms=exec_ms,
                            success=False,
                            all_filled=False,
                            error_message=f"Rolled back due to critical imbalance: {error_message}",
                            rollback_performed=True,
                            rollback_cost=rollback_cost,
                        )

            post_trade = await self._exposure_verifier.verify_post_trade_exposure(contexts)
            if post_trade is not None:
                net_usd = post_trade.get("net_usd", Decimal("0"))
                net_pct = post_trade.get("net_pct", Decimal("0"))
                net_qty = post_trade.get("net_qty", Decimal("0"))
                # Use net_qty (quantity) for imbalance comparison, not net_usd
                # net_usd is kept for logging/monitoring purposes only
                imbalance_tokens = max(imbalance_tokens, net_qty)
                if net_qty > Decimal("0"):
                    # Calculate quantity imbalance percentage
                    max_qty = max(total_long_tokens, total_short_tokens)
                    net_qty_pct = net_qty / max_qty if max_qty > Decimal("0") else Decimal("0")
                    if net_qty_pct > self._post_trade_max_imbalance_pct:
                        self.logger.warning(
                            "⚠️ Residual quantity exposure detected after partial execution: "
                            f"net_qty={net_qty:.6f} tokens ({net_qty_pct*100:.2f}%), net_usd=${net_usd:.4f} (for reference)."
                        )

            return self._build_execution_result(
                contexts=contexts,
                orders=orders,
                elapsed_ms=exec_ms,
                success=False,
                all_filled=False,
                error_message=error_message,
                rollback_performed=False,
                rollback_cost=Decimal("0"),
            )

        except Exception as exc:
            self.logger.error(f"Atomic execution failed: {exc}", exc_info=True)

            # Get contexts from locals if available
            contexts = locals().get("contexts", [])
            
            # Check if we need to rollback
            rollback_cost = None
            filled_orders_count = sum(1 for ctx in contexts if ctx.result and ctx.filled_quantity > Decimal("0"))
            if filled_orders_count > 0 and rollback_on_partial:
                filled_orders_list = [
                    ctx.result
                    for ctx in contexts
                    if ctx.result and ctx.filled_quantity > Decimal("0")
                ]
                rollback_cost = await self._rollback_manager.rollback(filled_orders_list, stage_prefix=stage_prefix)

            return self._build_execution_result(
                contexts=contexts,
                orders=orders,
                elapsed_ms=elapsed_ms(),
                success=False,
                all_filled=False,
                error_message=str(exc),
                rollback_performed=bool(rollback_cost and rollback_on_partial),
                rollback_cost=rollback_cost or Decimal("0"),
            )

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

    async def _handle_full_fill_trigger(
        self,
        trigger_ctx: OrderContext,
        other_contexts: List[OrderContext],
        contexts: List[OrderContext],
        pending_tasks: set[asyncio.Task],
        rollback_on_partial: bool,
    ) -> tuple[bool, Optional[str], bool, Decimal]:
        """
        Handle when one leg fully fills - cancel others and hedge.
        
        Args:
            trigger_ctx: The context that fully filled
            other_contexts: Other contexts to cancel/hedge
            contexts: All contexts (for rollback)
            pending_tasks: Set of pending tasks
            rollback_on_partial: Whether to rollback on partial fills
            
        Returns:
            Tuple of (hedge_success, hedge_error, rollback_performed, rollback_cost)
        """
        trigger_exchange = trigger_ctx.spec.exchange_client.get_exchange_name().upper()
        trigger_symbol = trigger_ctx.spec.symbol
        trigger_qty = trigger_ctx.filled_quantity
        
        self.logger.info(
            f"✅ {trigger_exchange} {trigger_symbol} fully filled ({trigger_qty}). "
            f"Cancelling remaining limit orders and hedging to prevent directional exposure."
        )
        
        # Cancel in-flight limits for the sibling legs.
        for ctx in other_contexts:
            exchange_name = ctx.spec.exchange_client.get_exchange_name().upper()
            symbol = ctx.spec.symbol
            self.logger.info(
                f"🔄 Cancelling limit order for {exchange_name} {symbol} "
                f"(remaining: {ctx.remaining_quantity}) → will hedge with market order"
            )
            ctx.cancel_event.set()

        # Wait for pending completions
        pending_contexts = [ctx for ctx in other_contexts if not ctx.completed]
        pending_completion = [ctx.task for ctx in pending_contexts]
        if pending_completion:
            gathered_results = await asyncio.gather(
                *pending_completion, return_exceptions=True
            )
            for ctx_result, ctx in zip(gathered_results, pending_contexts):
                previous_fill_ctx = ctx.filled_quantity
                if isinstance(ctx_result, Exception):  # pragma: no cover
                    self.logger.error(
                        f"Order task failed for {ctx.spec.symbol}: {ctx_result}"
                    )
                    result_dict = self._create_error_result_dict(ctx, str(ctx_result))
                else:
                    result_dict = ctx_result
                apply_result_to_context(ctx, result_dict)
                if ctx.filled_quantity > previous_fill_ctx:
                    # Note: newly_filled tracking happens in caller
                    pass

            # Update pending_tasks in place (remove completed tasks)
            pending_tasks.difference_update({task for task in pending_tasks if task.done()})

        # Calculate hedge target quantities with multiplier adjustments
        for ctx in other_contexts:
            await reconcile_context_after_cancel(ctx, self.logger)
            trigger_qty = trigger_ctx.filled_quantity
            if not isinstance(trigger_qty, Decimal):
                trigger_qty = Decimal(str(trigger_qty))
            trigger_qty = trigger_qty.copy_abs()

            # Account for quantity multipliers when matching across exchanges
            # Example: Lighter kTOSHI (84 units = 84k tokens) vs Aster TOSHI (84k units = 84k tokens)
            trigger_multiplier = trigger_ctx.spec.exchange_client.get_quantity_multiplier(trigger_ctx.spec.symbol)
            ctx_multiplier = ctx.spec.exchange_client.get_quantity_multiplier(ctx.spec.symbol)
            
            # Convert trigger quantity to "actual tokens" then to target exchange's units
            actual_tokens = trigger_qty * Decimal(str(trigger_multiplier))
            target_qty = actual_tokens / Decimal(str(ctx_multiplier))
            
            if trigger_multiplier != ctx_multiplier:
                self.logger.debug(
                    f"📊 Multiplier adjustment for {ctx.spec.symbol}: "
                    f"trigger_qty={trigger_qty} (×{trigger_multiplier}) → "
                    f"target_qty={target_qty} (×{ctx_multiplier})"
                )

            # Don't cap target_qty to spec.quantity when hedging after trigger fill
            # The trigger fill is the source of truth, and we need to match it exactly
            # (accounting for multipliers). spec.quantity might be from the original
            # order plan and could be wrong if there were rounding differences.
            # Only cap if target_qty exceeds spec.quantity significantly (safety check)
            spec_qty = getattr(ctx.spec, "quantity", None)
            if spec_qty is not None:
                spec_qty_dec = Decimal(str(spec_qty))
                # Only cap if target is significantly larger (more than 10% over)
                # This allows for small rounding differences but prevents huge errors
                if target_qty > spec_qty_dec * Decimal("1.1"):
                    self.logger.warning(
                        f"⚠️ [HEDGE] Calculated hedge target {target_qty} exceeds "
                        f"spec quantity {spec_qty_dec} by >10%. Capping to spec quantity."
                    )
                    target_qty = spec_qty_dec

            if target_qty < Decimal("0"):
                target_qty = Decimal("0")
            ctx.hedge_target_quantity = target_qty
            
            self.logger.debug(
                f"📊 [HEDGE] Set hedge_target_quantity for {ctx.spec.symbol}: "
                f"{target_qty} (trigger={trigger_qty}, multipliers={trigger_multiplier}×{ctx_multiplier})"
            )

        # Determine if this is a close operation (all orders have reduce_only=True)
        is_close_operation = (
            len(contexts) > 0 and 
            all(ctx.spec.reduce_only is True for ctx in contexts)
        )
        
        # For close operations, if both orders filled, positions are closed - no hedge needed
        # (Imbalance check is already skipped in _calculate_imbalance for closing operations)
        if is_close_operation:
            filled_count = sum(1 for ctx in contexts if ctx.filled_quantity > Decimal("0"))
            if filled_count == len(contexts):
                # Both close orders filled - positions are closed!
                self.logger.info(
                    f"✅ Close operation: Both orders filled, positions closed. No hedge needed."
                )
                return True, None, False, Decimal("0")
        
        # Execute hedge (with reduce_only flag for close operations)
        hedge_success, hedge_error = await self._hedge_manager.hedge(
            trigger_ctx, contexts, self.logger, reduce_only=is_close_operation
        )

        rollback_performed = False
        rollback_cost = Decimal("0")

        if hedge_success:
            return True, None, False, Decimal("0")
        else:
            if not rollback_on_partial:
                return False, hedge_error or "Hedge failure", False, Decimal("0")
            else:
                self.logger.warning(
                    f"Hedge failed ({hedge_error or 'no error supplied'}) — attempting rollback of partial fills"
                )
                for ctx in contexts:
                    ctx.cancel_event.set()
                remaining = [ctx.task for ctx in contexts if not ctx.completed]
                if remaining:
                    await asyncio.gather(*remaining, return_exceptions=True)
                rollback_performed = True
                
                # Log context state for debugging
                for c in contexts:
                    if c.filled_quantity > Decimal("0"):
                        result_qty = Decimal("0")
                        if c.result:
                            result_qty = coerce_decimal(c.result.get("filled_quantity")) or Decimal("0")
                        self.logger.debug(
                            f"Rollback (hedge failure) context for {c.spec.symbol} ({c.spec.side}): "
                            f"accumulated={c.filled_quantity}, result_dict={result_qty}"
                        )
                
                # Safety check: Only rollback contexts with actual fills
                rollback_payload = []
                for c in contexts:
                    if c.filled_quantity > Decimal("0") and c.result:
                        # Additional safety: verify filled_quantity is reasonable
                        spec_qty = getattr(c.spec, "quantity", None)
                        if spec_qty is not None:
                            spec_qty_dec = Decimal(str(spec_qty))
                            # If filled_quantity exceeds spec.quantity significantly, something is wrong
                            if c.filled_quantity > spec_qty_dec * Decimal("1.1"):
                                self.logger.error(
                                    f"⚠️ ROLLBACK SKIP: {c.spec.symbol} ({c.spec.side}) has suspicious filled_quantity: "
                                    f"{c.filled_quantity} exceeds spec.quantity={spec_qty_dec} by >10%. "
                                    f"This likely indicates a bug. Skipping rollback for this context."
                                )
                                continue
                        
                        rollback_payload.append(context_to_filled_dict(c))
                
                rollback_cost = await self._rollback_manager.rollback(
                    rollback_payload, stage_prefix=self._current_stage_prefix
                )
                self.logger.warning(
                    f"Rollback completed after hedge failure; total cost ${rollback_cost:.4f}"
                )
                for ctx in contexts:
                    ctx.filled_quantity = Decimal("0")
                    ctx.filled_usd = Decimal("0")
                
                return False, hedge_error, True, rollback_cost

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
