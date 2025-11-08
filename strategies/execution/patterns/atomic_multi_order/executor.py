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
from typing import Any, Dict, List, Optional

from helpers.unified_logger import get_core_logger, log_stage

# Keep imports patchable for tests that monkeypatch LiquidityAnalyzer
from strategies.execution.core.liquidity_analyzer import (
    LiquidityAnalyzer as LiquidityAnalyzer,  # re-export for test patches
)

from .contexts import OrderContext
from .hedge_manager import HedgeManager
from .retry_manager import RetryManager, RetryPolicy
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
    timeout_seconds: float = 30.0
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
    retry_attempts: int = 0
    retry_success: bool = False


class AtomicMultiOrderExecutor:
    """Executes multiple orders atomically—either all succeed or the trade is unwound."""

    def __init__(self, price_provider=None) -> None:
        self.price_provider = price_provider
        self.logger = get_core_logger("atomic_multi_order")
        self._hedge_manager = HedgeManager(price_provider=price_provider)
        self._retry_manager = RetryManager(price_provider=price_provider)
        self._post_trade_max_imbalance_pct = Decimal("0.02")  # 2% net exposure tolerance
        self._post_trade_base_tolerance = Decimal("0.0001")  # residual quantity tolerance

    async def execute_atomically(
        self,
        orders: List[OrderSpec],
        rollback_on_partial: bool = True,
        pre_flight_check: bool = True,
        skip_preflight_leverage: bool = False,
        stage_prefix: Optional[str] = None,
        retry_policy: Optional[RetryPolicy] = None,
    ) -> AtomicExecutionResult:
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
                retry_attempts=0,
                retry_success=False,
            )

        try:
            compose_stage = lambda *parts: self._compose_stage_id(stage_prefix, *parts)

            self.logger.info(
                f"Starting atomic execution of {len(orders)} orders "
                f"(rollback_on_partial={rollback_on_partial})"
            )

            if pre_flight_check:
                log_stage(self.logger, "Pre-flight Checks", icon="🔍", stage_id=compose_stage("1"))
                preflight_ok, preflight_error = await self._run_preflight_checks(
                    orders,
                    skip_leverage_check=skip_preflight_leverage,
                    stage_prefix=compose_stage("1"),
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
                        retry_attempts=0,
                        retry_success=False,
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
            retry_attempts = 0
            retry_success = False

            while pending_tasks:
                done, pending_tasks = await asyncio.wait(
                    pending_tasks, return_when=asyncio.FIRST_COMPLETED
                )
                newly_filled: List[OrderContext] = []

                for task in done:
                    ctx = task_map[task]
                    previous_fill = ctx.filled_quantity
                    try:
                        result = task.result()
                    except Exception as exc:  # pragma: no cover - defensive
                        self.logger.error(f"Order task failed for {ctx.spec.symbol}: {exc}")
                        result = self._create_error_result_dict(ctx, str(exc))
                    apply_result_to_context(ctx, result)
                    if ctx.filled_quantity > previous_fill:
                        newly_filled.append(ctx)

                all_completed = all(context.completed for context in contexts)

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

                if all_completed:
                    break

            remaining_tasks = [ctx.task for ctx in contexts if not ctx.completed]
            if remaining_tasks:
                await asyncio.gather(*remaining_tasks, return_exceptions=True)
            for ctx in contexts:
                await reconcile_context_after_cancel(ctx, self.logger)

            # Check if retries are needed and execute them
            retry_executed, retry_attempts, retry_success, retry_rollback_cost = await self._check_and_execute_retries(
                contexts=contexts,
                retry_policy=retry_policy,
                compose_stage=compose_stage,
            )
            
            # If retry triggered rollback, update state
            if retry_rollback_cost > Decimal("0"):
                rollback_performed = True
                rollback_cost = retry_rollback_cost


            exec_ms = elapsed_ms()
            total_long_usd, total_short_usd, imbalance, imbalance_pct = self._calculate_imbalance(contexts)
            imbalance_tolerance = Decimal("0.01")

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
                    retry_attempts=retry_attempts,
                    retry_success=retry_success,
                )

            # Check if all orders filled
            filled_orders_count = sum(1 for ctx in contexts if ctx.result and ctx.filled_quantity > Decimal("0"))
            if filled_orders_count == len(orders):
                # Check if imbalance is within acceptable bounds
                is_critical, _, _ = self._check_critical_imbalance(total_long_usd, total_short_usd)
                
                if is_critical:
                    self.logger.error(
                        f"⚠️ CRITICAL IMBALANCE detected despite all orders filled: "
                        f"longs=${total_long_usd:.2f}, shorts=${total_short_usd:.2f}, "
                        f"imbalance=${imbalance:.2f} ({imbalance_pct*100:.1f}%). Triggering emergency rollback."
                    )
                    rollback_performed = True
                    rollback_cost = await self._perform_emergency_rollback(
                        contexts, "all filled imbalance", imbalance, imbalance_pct
                    )
                    return self._build_execution_result(
                        contexts=contexts,
                        orders=orders,
                        elapsed_ms=exec_ms,
                        success=False,
                        all_filled=False,
                        error_message=f"Rolled back due to critical imbalance: ${imbalance:.2f}",
                        rollback_performed=True,
                        rollback_cost=rollback_cost,
                        retry_attempts=retry_attempts,
                        retry_success=retry_success,
                    )
                elif imbalance > imbalance_tolerance:
                    self.logger.warning(
                        f"Minor imbalance detected after hedge: longs=${total_long_usd:.5f}, "
                        f"shorts=${total_short_usd:.5f}, imbalance=${imbalance:.5f} "
                        f"({imbalance_pct*100:.1f}% within 5% tolerance)"
                    )

                post_trade = await self._verify_post_trade_exposure(contexts)
                if post_trade is not None:
                    net_usd = post_trade.get("net_usd", Decimal("0"))
                    net_pct = post_trade.get("net_pct", Decimal("0"))
                    net_qty = post_trade.get("net_qty", Decimal("0"))
                    imbalance = max(imbalance, net_usd)

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
                    retry_attempts=retry_attempts,
                    retry_success=retry_success,
                )

            # Critical fix: Check for dangerous imbalance and rollback if needed
            filled_orders_count = sum(1 for ctx in contexts if ctx.result and ctx.filled_quantity > Decimal("0"))
            error_message = hedge_error or f"Partial fill: {filled_orders_count}/{len(orders)}"
            if imbalance > imbalance_tolerance:
                self.logger.error(
                    f"Exposure imbalance detected after hedge: longs=${total_long_usd:.5f}, "
                    f"shorts=${total_short_usd:.5f}"
                )
                imbalance_msg = f"imbalance {imbalance:.5f} USD"
                error_message = f"{error_message}; {imbalance_msg}" if error_message else imbalance_msg
                
                # If we have a significant imbalance and rollback is enabled, close filled positions
                if rollback_on_partial and filled_orders_count > 0:
                    is_critical, _, _ = self._check_critical_imbalance(total_long_usd, total_short_usd)
                    if is_critical:
                        self.logger.warning(
                            f"⚠️ Critical imbalance ${imbalance:.2f} detected after retries exhausted. "
                            f"Initiating rollback to close {filled_orders_count} filled positions."
                        )
                        rollback_cost = await self._perform_emergency_rollback(
                            contexts, "retries exhausted", imbalance, imbalance_pct
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
                            retry_attempts=retry_attempts,
                            retry_success=retry_success,
                        )

            post_trade = await self._verify_post_trade_exposure(contexts)
            if post_trade is not None:
                net_usd = post_trade.get("net_usd", Decimal("0"))
                net_pct = post_trade.get("net_pct", Decimal("0"))
                net_qty = post_trade.get("net_qty", Decimal("0"))
                imbalance = max(imbalance, net_usd)
                if net_usd > Decimal("0") and net_pct > self._post_trade_max_imbalance_pct:
                    self.logger.warning(
                        "⚠️ Residual exposure detected after partial execution: "
                        f"net_qty={net_qty:.6f}, net_usd=${net_usd:.4f} ({net_pct*100:.2f}%)."
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
                retry_attempts=retry_attempts,
                retry_success=retry_success,
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
                rollback_cost = await self._rollback_filled_orders(filled_orders_list)

            return self._build_execution_result(
                contexts=contexts,
                orders=orders,
                elapsed_ms=elapsed_ms(),
                success=False,
                all_filled=False,
                error_message=str(exc),
                rollback_performed=bool(rollback_cost and rollback_on_partial),
                rollback_cost=rollback_cost or Decimal("0"),
                retry_attempts=0,
                retry_success=False,
            )

    @staticmethod
    def _estimate_required_margin(size_usd: Decimal) -> Decimal:
        """Conservative margin estimate (assumes 20% initial margin)."""
        return size_usd * Decimal("0.20")

    def _calculate_imbalance(
        self,
        contexts: List[OrderContext]
    ) -> tuple[Decimal, Decimal, Decimal, Decimal]:
        """
        Calculate exposure imbalance from contexts.
        
        Args:
            contexts: List of order contexts to analyze
            
        Returns:
            Tuple of (total_long_usd, total_short_usd, imbalance_usd, imbalance_pct)
        """
        total_long_usd = sum(ctx.filled_usd for ctx in contexts if ctx.spec.side == "buy")
        total_short_usd = sum(ctx.filled_usd for ctx in contexts if ctx.spec.side == "sell")
        imbalance_usd = abs(total_long_usd - total_short_usd)
        
        # Calculate imbalance as percentage: (max - min) / max
        min_usd = min(total_long_usd, total_short_usd)
        max_usd = max(total_long_usd, total_short_usd)
        imbalance_pct = Decimal("0")
        if max_usd > Decimal("0"):
            imbalance_pct = (max_usd - min_usd) / max_usd
        
        return total_long_usd, total_short_usd, imbalance_usd, imbalance_pct

    def _check_critical_imbalance(
        self,
        total_long_usd: Decimal,
        total_short_usd: Decimal,
        threshold_pct: Decimal = Decimal("0.05")
    ) -> tuple[bool, Decimal, Decimal]:
        """
        Check if imbalance exceeds critical threshold.
        
        Args:
            total_long_usd: Total USD value of long positions
            total_short_usd: Total USD value of short positions
            threshold_pct: Critical imbalance threshold (default 5%)
            
        Returns:
            Tuple of (is_critical, imbalance_usd, imbalance_pct)
        """
        imbalance_usd = abs(total_long_usd - total_short_usd)
        min_usd = min(total_long_usd, total_short_usd)
        max_usd = max(total_long_usd, total_short_usd)
        
        imbalance_pct = Decimal("0")
        if max_usd > Decimal("0"):
            imbalance_pct = (max_usd - min_usd) / max_usd
        
        is_critical = imbalance_pct > threshold_pct
        return is_critical, imbalance_usd, imbalance_pct

    async def _perform_emergency_rollback(
        self,
        contexts: List[OrderContext],
        reason: str,
        imbalance_usd: Decimal,
        imbalance_pct: Decimal
    ) -> Decimal:
        """
        Perform emergency rollback of all filled orders.
        
        Args:
            contexts: List of order contexts to rollback
            reason: Reason for rollback (for logging)
            imbalance_usd: USD imbalance amount
            imbalance_pct: Percentage imbalance
            
        Returns:
            Rollback cost in USD
        """
        # Log context state for debugging
        for c in contexts:
            if c.filled_quantity > Decimal("0"):
                result_qty = Decimal("0")
                if c.result:
                    result_qty = coerce_decimal(c.result.get("filled_quantity")) or Decimal("0")
                self.logger.debug(
                    f"Rollback ({reason}) context for {c.spec.symbol} ({c.spec.side}): "
                    f"accumulated={c.filled_quantity}, "
                    f"result_dict={result_qty}, "
                    f"match={'✓' if abs(c.filled_quantity - result_qty) < Decimal('0.0001') else '✗ MISMATCH'}"
                )
        
        rollback_payload = [
            context_to_filled_dict(c)
            for c in contexts
            if c.filled_quantity > Decimal("0") and c.result
        ]
        rollback_cost = await self._rollback_filled_orders(rollback_payload)
        self.logger.warning(
            f"🛡️ Emergency rollback completed; cost=${rollback_cost:.4f}. "
            f"Prevented ${imbalance_usd:.2f} ({imbalance_pct*100:.1f}%) directional exposure."
        )
        
        # Clear filled quantities to prevent position creation
        for ctx in contexts:
            ctx.filled_quantity = Decimal("0")
            ctx.filled_usd = Decimal("0")
        
        return rollback_cost

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

        # Execute hedge
        hedge_success, hedge_error = await self._hedge_manager.hedge(
            trigger_ctx, contexts, self.logger
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
                
                rollback_payload = [
                    context_to_filled_dict(c)
                    for c in contexts
                    if c.filled_quantity > Decimal("0") and c.result
                ]
                rollback_cost = await self._rollback_filled_orders(rollback_payload)
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
        retry_attempts: int,
        retry_success: bool,
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
            retry_attempts: Number of retry attempts
            retry_success: Whether retries succeeded
            
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
        total_long_usd, total_short_usd, imbalance, _ = self._calculate_imbalance(contexts)
        
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
            residual_imbalance_usd=imbalance if not rollback_performed else Decimal("0"),
            retry_attempts=retry_attempts,
            retry_success=retry_success,
        )

    async def _check_and_execute_retries(
        self,
        contexts: List[OrderContext],
        retry_policy: Optional[RetryPolicy],
        compose_stage: callable,
    ) -> tuple[bool, int, bool, Decimal]:
        """
        Check if retries are needed and execute them.
        
        Args:
            contexts: List of order contexts
            retry_policy: Retry policy configuration
            compose_stage: Function to compose stage IDs
            
        Returns:
            Tuple of (needs_retry_executed, retry_attempts, retry_success, rollback_cost)
        """
        # Check if retry is needed, but ignore tiny rounding dust
        # (e.g., 0.2 remaining out of 1176 due to step_size rounding is not worth retrying)
        RETRY_THRESHOLD_PCT = Decimal("0.01")  # 1% of planned quantity
        
        needs_retry = False
        retryable_failures = []
        for ctx in contexts:
            # Check if result indicates retryable failure (e.g., post-only violation)
            result_retryable = False
            if ctx.result:
                result_retryable = ctx.result.get("retryable", False)
                if result_retryable:
                    retryable_failures.append(ctx.spec.symbol)
            
            if ctx.remaining_quantity > Decimal("0"):
                # Calculate what % of the planned quantity is remaining
                planned_qty = ctx.spec.quantity or (ctx.spec.size_usd / Decimal("100"))  # rough estimate
                if planned_qty > Decimal("0"):
                    remaining_pct = ctx.remaining_quantity / planned_qty
                    if remaining_pct > RETRY_THRESHOLD_PCT:
                        self.logger.debug(
                            f"[{ctx.spec.exchange_client.get_exchange_name().upper()}] {ctx.spec.symbol}: "
                            f"Significant remainder {ctx.remaining_quantity} ({remaining_pct*100:.1f}% of {planned_qty})"
                        )
                        needs_retry = True
                    else:
                        # Even if below threshold, retry if marked as retryable (e.g., post-only violation)
                        if result_retryable:
                            self.logger.info(
                                f"[{ctx.spec.exchange_client.get_exchange_name().upper()}] {ctx.spec.symbol}: "
                                f"Retryable failure (e.g., post-only violation), retrying despite "
                                f"small remainder {ctx.remaining_quantity} ({remaining_pct*100:.2f}% of {planned_qty})"
                            )
                            needs_retry = True
                        else:
                            self.logger.debug(
                                f"[{ctx.spec.exchange_client.get_exchange_name().upper()}] {ctx.spec.symbol}: "
                                f"Ignoring rounding dust {ctx.remaining_quantity} ({remaining_pct*100:.2f}% of {planned_qty})"
                            )
        
        if retryable_failures:
            self.logger.info(
                f"🔁 Retryable failures detected for: {', '.join(retryable_failures)}. "
                "Will retry with fresh BBO."
            )
        
        retry_attempts = 0
        retry_success = False
        rollback_cost = Decimal("0")
        
        if needs_retry and retry_policy and retry_policy.max_attempts > 0:
            self.logger.info("🔁 Initiating retry cycle for unmatched legs.")
            retry_success, retry_attempts = await self._retry_manager.execute_retries(
                contexts=contexts,
                policy=retry_policy,
                place_order=lambda spec, cancel_event: self._place_single_order(
                    spec, cancel_event=cancel_event
                ),
                logger=self.logger,
                compose_stage=compose_stage,
            )
            if retry_attempts:
                for ctx in contexts:
                    await reconcile_context_after_cancel(ctx, self.logger)
                if retry_success:
                    self.logger.info("✅ Retry attempts filled remaining deficits.")
                else:
                    # Retry failed - check if imbalance is critical
                    retry_long_usd, retry_short_usd, retry_imbalance_usd, retry_imbalance_pct = self._calculate_imbalance(contexts)
                    is_critical, _, _ = self._check_critical_imbalance(retry_long_usd, retry_short_usd)
                    
                    if is_critical:
                        self.logger.warning(
                            f"⚠️ CRITICAL IMBALANCE after retry failure: "
                            f"longs=${retry_long_usd:.2f}, shorts=${retry_short_usd:.2f}, "
                            f"imbalance=${retry_imbalance_usd:.2f} ({retry_imbalance_pct*100:.1f}%). Triggering emergency rollback."
                        )
                        rollback_cost = await self._perform_emergency_rollback(
                            contexts, "retry failure", retry_imbalance_usd, retry_imbalance_pct
                        )
                        return True, retry_attempts, False, rollback_cost
                    else:
                        self.logger.warning(
                            f"⚠️ Retry attempts exhausted; residual imbalance ${retry_imbalance_usd:.2f} "
                            f"({retry_imbalance_pct*100:.1f}%) within 5% tolerance."
                        )
        
        return needs_retry and retry_attempts > 0, retry_attempts, retry_success, rollback_cost

    async def _place_single_order(
        self, spec: OrderSpec, cancel_event: Optional[asyncio.Event] = None
    ) -> Dict[str, Any]:
        """Place a single order from spec and return a normalised result dictionary."""
        from strategies.execution.core.order_executor import ExecutionMode, OrderExecutor

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

    async def _verify_post_trade_exposure(self, contexts: List[OrderContext]) -> Optional[Dict[str, Decimal]]:
        """Pull live position snapshots and detect any residual exposure."""
        unique_keys = set()
        tasks = []

        for ctx in contexts:
            client = ctx.spec.exchange_client
            symbol = ctx.spec.symbol
            key = (id(client), symbol)
            if key in unique_keys:
                continue
            getter = getattr(client, "get_position_snapshot", None)
            if getter is None or not callable(getter):
                continue
            unique_keys.add(key)

            async def fetch_snapshot(exchange_client=client, sym=symbol):
                try:
                    snapshot = await exchange_client.get_position_snapshot(sym)
                except Exception as exc:  # pragma: no cover - defensive
                    self.logger.warning(
                        f"⚠️ [{exchange_client.get_exchange_name().upper()}] Position snapshot fetch failed for {sym}: {exc}"
                    )
                    return None
                return snapshot

            tasks.append(fetch_snapshot())

        if not tasks:
            return None

        snapshots = await asyncio.gather(*tasks, return_exceptions=True)

        total_long_qty = Decimal("0")
        total_short_qty = Decimal("0")
        total_long_usd = Decimal("0")
        total_short_usd = Decimal("0")

        for idx, snapshot in enumerate(snapshots):
            if isinstance(snapshot, Exception) or snapshot is None:
                continue
            quantity = snapshot.quantity or Decimal("0")
            exposure_usd = snapshot.exposure_usd
            mark_price = snapshot.mark_price or snapshot.entry_price

            abs_qty = quantity.copy_abs()
            if exposure_usd is None and mark_price is not None:
                exposure_usd = abs_qty * mark_price
            elif exposure_usd is None:
                exposure_usd = Decimal("0")

            if quantity > Decimal("0"):
                total_long_qty += abs_qty
                total_long_usd += exposure_usd or Decimal("0")
            elif quantity < Decimal("0"):
                total_short_qty += abs_qty
                total_short_usd += exposure_usd or Decimal("0")

        net_qty = (total_long_qty - total_short_qty).copy_abs()
        net_usd = (total_long_usd - total_short_usd).copy_abs()

        max_usd = max(total_long_usd, total_short_usd)
        net_pct = net_usd / max_usd if max_usd > Decimal("0") else Decimal("0")

        return {
            "net_qty": net_qty,
            "net_usd": net_usd,
            "net_pct": net_pct,
            "long_usd": total_long_usd,
            "short_usd": total_short_usd,
        }

    async def _run_preflight_checks(
        self,
        orders: List[OrderSpec],
        skip_leverage_check: bool = False,
        stage_prefix: Optional[str] = None,
    ) -> tuple[bool, Optional[str]]:
        """Replicated from original implementation—unchanged but moved for clarity."""
        try:
            compose_stage = lambda *parts: self._compose_stage_id(stage_prefix, *parts)
            symbols_to_check: Dict[str, List[OrderSpec]] = {}
            for order_spec in orders:
                symbol = order_spec.symbol
                if symbol not in symbols_to_check:
                    symbols_to_check[symbol] = []
                symbols_to_check[symbol].append(order_spec)

            if not skip_leverage_check:
                log_stage(self.logger, "Leverage Validation", icon="📐", stage_id=compose_stage("1"))
                from strategies.execution.core.leverage_validator import LeverageValidator

                leverage_validator = LeverageValidator()

                for symbol, symbol_orders in symbols_to_check.items():
                    exchange_clients = [order.exchange_client for order in symbol_orders]
                    requested_size = symbol_orders[0].size_usd

                    max_size, limiting_exchange = await leverage_validator.get_max_position_size(
                        exchange_clients=exchange_clients,
                        symbol=symbol,
                        requested_size_usd=requested_size,
                        check_balance=True,
                    )

                    if max_size < requested_size:
                        error_msg = (
                            f"Position size too large for {symbol}: "
                            f"Requested ${requested_size:.2f}, "
                            f"maximum supported: ${max_size:.2f} "
                            f"(limited by {limiting_exchange})"
                        )
                        self.logger.warning(f"⚠️  {error_msg}")
                        return False, error_msg

                for symbol, symbol_orders in symbols_to_check.items():
                    exchange_clients = [order.exchange_client for order in symbol_orders]
                    requested_size = symbol_orders[0].size_usd

                    self.logger.info(f"Normalizing leverage for {symbol}...")
                    min_leverage, limiting = await leverage_validator.normalize_and_set_leverage(
                        exchange_clients=exchange_clients,
                        symbol=symbol,
                        requested_size_usd=requested_size,
                    )

                    if min_leverage is not None:
                        self.logger.info(
                            f"✅ [LEVERAGE] {symbol} normalized to {min_leverage}x "
                            f"(limited by {limiting})"
                        )
                    else:
                        self.logger.warning(
                            f"⚠️  [LEVERAGE] Could not normalize leverage for {symbol}. "
                            f"Orders may execute with different leverage!"
                        )

            log_stage(self.logger, "Margin & Balance Checks", icon="💰", stage_id=compose_stage("2"))
            self.logger.info("Running balance checks...")

            exchange_margin_required: Dict[str, Decimal] = {}
            for order_spec in orders:
                exchange_name = order_spec.exchange_client.get_exchange_name()
                estimated_margin = self._estimate_required_margin(order_spec.size_usd)
                exchange_margin_required.setdefault(exchange_name, Decimal("0"))
                exchange_margin_required[exchange_name] += estimated_margin

            for exchange_name, required_margin in exchange_margin_required.items():
                exchange_client = next(
                    (
                        order.exchange_client
                        for order in orders
                        if order.exchange_client.get_exchange_name() == exchange_name
                    ),
                    None,
                )
                if not exchange_client:
                    continue

                try:
                    available_balance = await exchange_client.get_account_balance()
                except Exception as exc:  # pragma: no cover - defensive
                    self.logger.warning(
                        f"⚠️ Balance check failed for {exchange_name}: {exc}"
                    )
                    continue

                if available_balance is None:
                    self.logger.warning(
                        f"⚠️ Cannot verify balance for {exchange_name} (required: ~${required_margin:.2f})"
                    )
                    continue

                required_with_buffer = required_margin * Decimal("1.10")
                if available_balance < required_with_buffer:
                    error_msg = (
                        f"Insufficient balance on {exchange_name}: "
                        f"available=${available_balance:.2f}, required=${required_with_buffer:.2f} "
                        f"(${required_margin:.2f} + 10% buffer)"
                    )
                    self.logger.error(f"❌ {error_msg}")
                    return False, error_msg

                self.logger.info(
                    f"✅ {exchange_name} balance OK: ${available_balance:.2f} >= ${required_with_buffer:.2f}"
                )

            log_stage(self.logger, "Order Book Liquidity", icon="🌊", stage_id=compose_stage("3"))
            self.logger.info("Running liquidity checks...")

            analyzer = LiquidityAnalyzer(price_provider=self.price_provider, max_spread_bps=100)

            for i, order_spec in enumerate(orders):
                self.logger.debug(
                    f"Checking liquidity for order {i}: {order_spec.side} {order_spec.symbol} ${order_spec.size_usd}"
                )
                report = await analyzer.check_execution_feasibility(
                    exchange_client=order_spec.exchange_client,
                    symbol=order_spec.symbol,
                    side=order_spec.side,
                    size_usd=order_spec.size_usd,
                )

                if not analyzer.is_execution_acceptable(report):
                    error_msg = (
                        f"Order {i} ({order_spec.side} {order_spec.symbol}) "
                        f"failed liquidity check: {report.recommendation}"
                    )
                    self.logger.warning(f"❌ {error_msg}")
                    return False, error_msg

            log_stage(
                self.logger,
                "Minimum Order Notional",
                icon="💵",
                stage_id=compose_stage("4"),
            )
            self.logger.info("Validating minimum notional requirements...")

            for order_spec in orders:
                planned_notional = order_spec.size_usd
                if planned_notional is None:
                    continue
                if not isinstance(planned_notional, Decimal):
                    planned_notional = Decimal(str(planned_notional))

                exchange_client = order_spec.exchange_client
                try:
                    min_notional = exchange_client.get_min_order_notional(order_spec.symbol)
                except Exception as exc:  # pragma: no cover - defensive
                    self.logger.debug(
                        f"Skipping min notional check for "
                        f"{exchange_client.get_exchange_name().upper()}:{order_spec.symbol} "
                        f"(error: {exc})"
                    )
                    continue

                if min_notional is None or min_notional <= Decimal("0"):
                    continue

                exchange_name = exchange_client.get_exchange_name().upper()
                if planned_notional < min_notional:
                    error_msg = (
                        f"[{exchange_name}] {order_spec.symbol} order notional "
                        f"${planned_notional:.2f} below minimum ${min_notional:.2f}"
                    )
                    self.logger.warning(f"❌ {error_msg}")
                    return False, error_msg

                self.logger.info(
                    f"✅ [{exchange_name}] {order_spec.symbol} notional ${planned_notional:.2f} "
                    f"meets minimum ${min_notional:.2f}"
                )

            self.logger.info("✅ All pre-flight checks passed")
            return True, None

        except Exception as exc:
            self.logger.error(f"Pre-flight check error: {exc}")
            self.logger.warning("⚠️ Continuing despite pre-flight check error")
            return True, None

    async def _rollback_filled_orders(self, filled_orders: List[Dict[str, Any]]) -> Decimal:
        """Rollback helper copied intact from the original implementation."""
        self.logger.warning(
            f"🚨 EMERGENCY ROLLBACK: Closing {len(filled_orders)} filled orders"
        )

        total_rollback_cost = Decimal("0")

        self.logger.info("Step 1/3: Canceling all orders to prevent further fills...")
        cancel_tasks = []
        for order in filled_orders:
            if order.get("order_id"):
                try:
                    cancel_task = order["exchange_client"].cancel_order(order["order_id"])
                    cancel_tasks.append(cancel_task)
                except Exception as exc:
                    self.logger.error(
                        f"Failed to create cancel task for {order.get('order_id')}: {exc}"
                    )

        if cancel_tasks:
            cancel_results = await asyncio.gather(*cancel_tasks, return_exceptions=True)
            for i, result in enumerate(cancel_results):
                if isinstance(result, Exception):
                    self.logger.warning(f"Cancel failed for order {i}: {result}")
            await asyncio.sleep(0.5)

        self.logger.info("Step 2/3: Querying actual filled amounts...")
        actual_fills = []
        for order in filled_orders:
            exchange_client = order["exchange_client"]
            symbol = order["symbol"]
            side = order["side"]
            order_id = order.get("order_id")
            fallback_quantity = coerce_decimal(order.get("filled_quantity"))
            fallback_price = coerce_decimal(order.get("fill_price")) or Decimal("0")

            self.logger.debug(
                f"Rollback order info: {symbol} ({side}), "
                f"order_id={order_id}, "
                f"payload_quantity={fallback_quantity}"
            )

            actual_quantity: Optional[Decimal] = None

            if order_id:
                try:
                    order_info = await exchange_client.get_order_info(order_id)
                except Exception as exc:
                    self.logger.error(f"Failed to get actual fill for {order_id}: {exc}")
                    order_info = None

                if order_info is not None:
                    reported_qty = coerce_decimal(getattr(order_info, "filled_size", None))

                    if reported_qty is not None and reported_qty > Decimal("0"):
                        actual_quantity = reported_qty

                        if (
                            fallback_quantity is not None
                            and abs(reported_qty - fallback_quantity) > Decimal("0.0001")
                        ):
                            self.logger.warning(
                                f"⚠️ Fill amount changed for {symbol}: "
                                f"{fallback_quantity} → {reported_qty} "
                                f"(Δ={reported_qty - fallback_quantity})"
                            )
                    else:
                        if fallback_quantity is not None and fallback_quantity > Decimal("0"):
                            self.logger.warning(
                                f"⚠️ Exchange reported 0 filled size for {symbol} after cancel; "
                                f"falling back to cached filled quantity {fallback_quantity}"
                            )
                            actual_quantity = fallback_quantity
                        else:
                            self.logger.warning(
                                f"⚠️ No filled quantity reported for {symbol} ({order_id}); nothing to close"
                            )
            if actual_quantity is None:
                if fallback_quantity is not None and fallback_quantity > Decimal("0"):
                    actual_quantity = fallback_quantity
                else:
                    self.logger.warning(
                        f"⚠️ Skipping rollback close for {symbol}: unable to determine filled quantity"
                    )
                    continue

            actual_fills.append(
                {
                    "exchange_client": exchange_client,
                    "symbol": symbol,
                    "side": side,
                    "filled_quantity": actual_quantity,
                    "fill_price": fallback_price,
                }
            )

        self.logger.info(f"Step 3/3: Closing {len(actual_fills)} filled positions...")
        rollback_tasks = []
        for fill in actual_fills:
            try:
                close_side = "sell" if fill["side"] == "buy" else "buy"
                close_quantity = fill["filled_quantity"]
                exchange_client = fill["exchange_client"]
                exchange_config = getattr(exchange_client, "config", None)
                contract_id = getattr(exchange_config, "contract_id", fill["symbol"])

                self.logger.info(
                    f"Rollback: {close_side} {fill['symbol']} {close_quantity} @ market "
                    f"(contract_id={contract_id}, exchange={exchange_client.get_exchange_name()})"
                )
                
                # Log multiplier info if available
                try:
                    multiplier = exchange_client.get_quantity_multiplier(fill["symbol"])
                    if multiplier != 1:
                        actual_tokens = close_quantity * Decimal(str(multiplier))
                        self.logger.debug(
                            f"Rollback quantity multiplier: {close_quantity} units × {multiplier} = "
                            f"{actual_tokens} actual tokens"
                        )
                except Exception:
                    pass  # Ignore multiplier errors

                self.logger.debug(
                    f"Rollback: Using contract_id='{contract_id}' for symbol '{fill['symbol']}'"
                )

                close_task = exchange_client.place_market_order(
                    contract_id=contract_id,
                    quantity=float(close_quantity),
                    side=close_side,
                )
                rollback_tasks.append((close_task, fill))
            except Exception as exc:
                self.logger.error(f"Failed to create rollback order for {fill['symbol']}: {exc}")

        if rollback_tasks:
            rollback_results = await asyncio.gather(
                *(task for task, _ in rollback_tasks), return_exceptions=True
            )
            for (task, fill), result in zip(rollback_tasks, rollback_results):
                if isinstance(result, Exception):
                    self.logger.warning(
                        f"Rollback market order failed for {fill['symbol']}: {result}"
                    )
                else:
                    entry_price = fill["fill_price"] or Decimal("0")
                    exit_price = Decimal(str(result.price)) if getattr(result, "price", None) else entry_price
                    cost = abs(exit_price - entry_price) * fill["filled_quantity"]
                    total_rollback_cost += cost
                    self.logger.warning(
                        f"Rollback cost for {fill['symbol']}: ${cost:.2f} "
                        f"(entry: ${entry_price}, exit: ${exit_price})"
                    )

        self.logger.warning(
            f"✅ Rollback complete. Total cost: ${total_rollback_cost:.2f}"
        )
        return total_rollback_cost

    def _compose_stage_id(self, stage_prefix: Optional[str], *parts: str) -> Optional[str]:
        if stage_prefix:
            if parts:
                return ".".join([stage_prefix, *parts])
            return stage_prefix
        if parts:
            return ".".join(parts)
        return None
