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
from .hedge_manager import HedgeManager
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

    def __init__(self, price_provider=None, account_name: Optional[str] = None, notification_service: Optional[Any] = None) -> None:
        self.price_provider = price_provider
        self.logger = get_core_logger("atomic_multi_order")
        self._hedge_manager = HedgeManager(price_provider=price_provider)
        self._post_trade_max_imbalance_pct = Decimal("0.02")  # 2% net exposure tolerance
        self._post_trade_base_tolerance = Decimal("0.0001")  # residual quantity tolerance
        self.account_name = account_name
        self.notification_service = notification_service
        # Store normalized leverage per (exchange_name, symbol) for margin calculations
        # This ensures balance checks use the normalized leverage, not the symbol's max leverage
        self._normalized_leverage: Dict[Tuple[str, str], int] = {}

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
                            rollback_cost = await self._perform_emergency_rollback(
                                contexts, "Partial fill hedge failure", 
                                Decimal("0"), Decimal("0")
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
            total_long_tokens, total_short_tokens, imbalance_tokens, imbalance_pct = self._calculate_imbalance(contexts)
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
                is_critical, _, _ = self._check_critical_imbalance(total_long_tokens, total_short_tokens)
                
                if is_critical:
                    self.logger.error(
                        f"⚠️ CRITICAL QUANTITY IMBALANCE detected despite all orders filled: "
                        f"longs={total_long_tokens:.6f} tokens, shorts={total_short_tokens:.6f} tokens, "
                        f"imbalance={imbalance_tokens:.6f} tokens ({imbalance_pct*100:.2f}%). Triggering emergency rollback."
                    )
                    rollback_performed = True
                    rollback_cost = await self._perform_emergency_rollback(
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

                post_trade = await self._verify_post_trade_exposure(contexts)
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
                    is_critical, _, _ = self._check_critical_imbalance(total_long_tokens, total_short_tokens)
                    if is_critical:
                        self.logger.warning(
                            f"⚠️ Critical quantity imbalance {imbalance_tokens:.6f} tokens ({imbalance_pct*100:.2f}%) "
                            f"detected after retries exhausted. Initiating rollback to close {filled_orders_count} filled positions."
                        )
                        rollback_cost = await self._perform_emergency_rollback(
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

            post_trade = await self._verify_post_trade_exposure(contexts)
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
            )

    async def _estimate_required_margin(
        self, order_spec: OrderSpec, leverage_info_cache: Optional[Dict[tuple, Any]] = None
    ) -> Decimal:
        """
        Estimate required margin based on normalized leverage (if set) or leverage info for the symbol/exchange.
        
        ⭐ CRITICAL: Uses normalized leverage if available (from normalize_and_set_leverage),
        otherwise falls back to querying get_leverage_info. This ensures balance checks
        use the correct leverage (e.g., 5x normalized) instead of the symbol's max leverage (e.g., 20x).
        
        Args:
            order_spec: Order specification with exchange_client, symbol, and size_usd
            leverage_info_cache: Optional cache dict keyed by (exchange_name, symbol) to avoid duplicate API calls
            
        Returns:
            Estimated margin required in USD
        """
        from strategies.execution.core.leverage_validator import LeverageValidator
        
        exchange_name = order_spec.exchange_client.get_exchange_name()
        symbol = order_spec.symbol
        cache_key = (exchange_name, symbol)
        
        # ⭐ PRIORITY 1: Use normalized leverage if available (set during normalize_and_set_leverage)
        if cache_key in self._normalized_leverage:
            normalized_leverage = Decimal(str(self._normalized_leverage[cache_key]))
            estimated_margin = order_spec.size_usd / normalized_leverage
            self.logger.debug(
                f"📊 [{exchange_name}] Margin for {symbol}: ${estimated_margin:.2f} "
                f"(${order_spec.size_usd:.2f} / {normalized_leverage}x normalized leverage)"
            )
            return estimated_margin
        
        # ⭐ PRIORITY 2: Try to get leverage info from cache or fetch it
        leverage_info = None
        if leverage_info_cache and cache_key in leverage_info_cache:
            leverage_info = leverage_info_cache[cache_key]
        else:
            try:
                leverage_validator = LeverageValidator()
                leverage_info = await leverage_validator.get_leverage_info(
                    order_spec.exchange_client, symbol
                )
                if leverage_info_cache is not None:
                    leverage_info_cache[cache_key] = leverage_info
            except Exception as exc:
                self.logger.warning(
                    f"⚠️ Could not fetch leverage info for {exchange_name}:{symbol}: {exc}. "
                    "Using conservative 20% margin estimate."
                )
        
        # Calculate margin based on leverage info
        if leverage_info and leverage_info.margin_requirement is not None:
            # Use margin requirement directly (e.g., 0.3333 for 3x leverage)
            margin_requirement = leverage_info.margin_requirement
            estimated_margin = order_spec.size_usd * margin_requirement
            self.logger.debug(
                f"📊 [{exchange_name}] Margin for {symbol}: ${estimated_margin:.2f} "
                f"({margin_requirement*100:.2f}% of ${order_spec.size_usd:.2f})"
            )
            return estimated_margin
        elif leverage_info and leverage_info.max_leverage is not None:
            # Calculate from max leverage (margin = size / leverage)
            max_leverage = leverage_info.max_leverage
            estimated_margin = order_spec.size_usd / max_leverage
            self.logger.debug(
                f"📊 [{exchange_name}] Margin for {symbol}: ${estimated_margin:.2f} "
                f"(${order_spec.size_usd:.2f} / {max_leverage}x leverage)"
            )
            return estimated_margin
        else:
            # Fallback to conservative 20% estimate if leverage info unavailable
            self.logger.warning(
                f"⚠️ No leverage info available for {exchange_name}:{symbol}, "
                "using conservative 20% margin estimate"
            )
            return order_spec.size_usd * Decimal("0.20")

    async def _send_insufficient_margin_notification(
        self,
        exchange_name: str,
        available_balance: Decimal,
        required_margin: Decimal,
        exchange_leverage_info: Dict[str, Any],
        orders: List[OrderSpec]
    ) -> None:
        """
        Attempt to send insufficient margin notification via notification service.
        
        Args:
            exchange_name: Name of the exchange with insufficient margin
            available_balance: Available balance on the exchange
            required_margin: Required margin amount
            exchange_leverage_info: Dict of symbol -> LeverageInfo for this exchange
            orders: List of orders that failed margin check
        """
        if not self.notification_service:
            return
        
        try:
            # Get symbol from orders (use first order's symbol)
            symbol = orders[0].symbol if orders else "UNKNOWN"
            
            # Get leverage info for the symbol
            leverage_info = exchange_leverage_info.get(symbol)
            leverage_str = "N/A"
            if leverage_info:
                if hasattr(leverage_info, 'max_leverage') and leverage_info.max_leverage:
                    leverage_str = f"{leverage_info.max_leverage}x"
                elif hasattr(leverage_info, 'margin_requirement') and leverage_info.margin_requirement:
                    calculated_leverage = Decimal("1") / leverage_info.margin_requirement
                    leverage_str = f"{calculated_leverage:.1f}x"
            
            # Call notification service
            await self.notification_service.notify_insufficient_margin(
                symbol=symbol,
                exchange_name=exchange_name,
                available_balance=available_balance,
                required_margin=required_margin,
                leverage_info=leverage_str
            )
        except Exception as exc:
            # Don't fail the preflight check if notification fails
            self.logger.debug(f"Could not send insufficient margin notification: {exc}")

    def _calculate_imbalance(
        self,
        contexts: List[OrderContext]
    ) -> tuple[Decimal, Decimal, Decimal, Decimal]:
        """
        Calculate exposure imbalance from contexts using QUANTITY (normalized to actual tokens).
        
        CRITICAL: Uses quantity imbalance, not USD imbalance, for true delta-neutrality.
        Quantities are normalized to actual tokens using exchange multipliers to handle
        different unit systems (e.g., Lighter kTOSHI = 1000x, Aster TOSHI = 1x).
        
        For OPENING operations:
        - BUY orders increase long exposure
        - SELL orders increase short exposure
        - Checks if actual token quantities match (delta-neutral)
        
        For CLOSING operations (all orders have reduce_only=True):
        - BUY orders close SHORT positions (reduce short exposure)
        - SELL orders close LONG positions (reduce long exposure)
        - Imbalance check is skipped (positions are being closed, not opened)
        
        Args:
            contexts: List of order contexts to analyze
            
        Returns:
            Tuple of (total_long_tokens, total_short_tokens, imbalance_tokens, imbalance_pct)
            where tokens are normalized to actual token amounts (accounting for multipliers)
        """
        # Check if this is a closing operation (all orders have reduce_only=True)
        is_closing_operation = (
            len(contexts) > 0 and 
            all(ctx.spec.reduce_only is True for ctx in contexts)
        )
        
        if is_closing_operation:
            # For closing operations, we're reducing exposure, not creating it
            # Return zeros to indicate no new exposure imbalance
            self.logger.debug(
                "Closing operation detected (all orders have reduce_only=True). "
                "Skipping imbalance check as we're reducing exposure, not creating it."
            )
            return Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0")
        
        # For opening operations, calculate QUANTITY imbalance (normalized to actual tokens)
        total_long_tokens = Decimal("0")
        total_short_tokens = Decimal("0")
        
        for ctx in contexts:
            if ctx.filled_quantity <= Decimal("0"):
                continue
            
            # Get multiplier for this exchange/symbol
            try:
                multiplier = Decimal(str(ctx.spec.exchange_client.get_quantity_multiplier(ctx.spec.symbol)))
            except Exception as exc:
                self.logger.warning(
                    f"Failed to get multiplier for {ctx.spec.symbol} on "
                    f"{ctx.spec.exchange_client.get_exchange_name()}: {exc}. Using 1."
                )
                multiplier = Decimal("1")
            
            # Convert filled quantity to actual tokens
            actual_tokens = ctx.filled_quantity * multiplier
            
            if ctx.spec.side == "buy":
                total_long_tokens += actual_tokens
            elif ctx.spec.side == "sell":
                total_short_tokens += actual_tokens
        
        # Calculate quantity imbalance
        imbalance_tokens = abs(total_long_tokens - total_short_tokens)
        
        # Calculate imbalance as percentage: (max - min) / max
        min_tokens = min(total_long_tokens, total_short_tokens)
        max_tokens = max(total_long_tokens, total_short_tokens)
        imbalance_pct = Decimal("0")
        if max_tokens > Decimal("0"):
            imbalance_pct = (max_tokens - min_tokens) / max_tokens
        
        return total_long_tokens, total_short_tokens, imbalance_tokens, imbalance_pct

    def _check_critical_imbalance(
        self,
        total_long_tokens: Decimal,
        total_short_tokens: Decimal,
        threshold_pct: Decimal = Decimal("0.01")
    ) -> tuple[bool, Decimal, Decimal]:
        """
        Check if quantity imbalance exceeds critical threshold.
        
        Uses quantity (normalized to actual tokens) instead of USD for true delta-neutrality.
        
        Args:
            total_long_tokens: Total actual tokens for long positions (normalized)
            total_short_tokens: Total actual tokens for short positions (normalized)
            threshold_pct: Critical imbalance threshold (default 1%)
            
        Returns:
            Tuple of (is_critical, imbalance_tokens, imbalance_pct)
        """
        imbalance_tokens = abs(total_long_tokens - total_short_tokens)
        min_tokens = min(total_long_tokens, total_short_tokens)
        max_tokens = max(total_long_tokens, total_short_tokens)
        
        imbalance_pct = Decimal("0")
        if max_tokens > Decimal("0"):
            imbalance_pct = (max_tokens - min_tokens) / max_tokens
        
        is_critical = imbalance_pct > threshold_pct
        return is_critical, imbalance_tokens, imbalance_pct

    async def _perform_emergency_rollback(
        self,
        contexts: List[OrderContext],
        reason: str,
        imbalance_tokens: Decimal,
        imbalance_pct: Decimal,
        stage_prefix: Optional[str] = None,
    ) -> Decimal:
        """
        Perform emergency rollback of all filled orders.
        
        Args:
            contexts: List of order contexts to rollback
            reason: Reason for rollback (for logging)
            imbalance_tokens: Quantity imbalance amount (in actual tokens, normalized)
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
        
        # Safety check: Only rollback contexts with actual fills
        # Double-check that filled_quantity matches what the exchange reports
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
        
        rollback_cost = await self._rollback_filled_orders(
            rollback_payload, stage_prefix=stage_prefix
        )
        self.logger.warning(
            f"🛡️ Emergency rollback completed; cost=${rollback_cost:.4f}. "
            f"Prevented {imbalance_tokens:.6f} tokens ({imbalance_pct*100:.2f}%) quantity imbalance."
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
                
                rollback_cost = await self._rollback_filled_orders(
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
        total_long_tokens, total_short_tokens, imbalance_tokens, _ = self._calculate_imbalance(contexts)
        
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
                        # Store normalized leverage for each exchange to use in margin calculations
                        for order in symbol_orders:
                            exchange_name = order.exchange_client.get_exchange_name()
                            cache_key = (exchange_name, symbol)
                            self._normalized_leverage[cache_key] = min_leverage
                    else:
                        self.logger.warning(
                            f"⚠️  [LEVERAGE] Could not normalize leverage for {symbol}. "
                            f"Orders may execute with different leverage!"
                        )

            log_stage(self.logger, "Margin & Balance Checks", icon="💰", stage_id=compose_stage("2"))
            self.logger.info("Running balance checks...")

            # Cache leverage info to avoid duplicate API calls
            leverage_info_cache: Dict[tuple, Any] = {}

            exchange_margin_required: Dict[str, Decimal] = {}
            exchange_leverage_info: Dict[str, Dict[str, Any]] = {}  # Store leverage info for notifications
            
            for order_spec in orders:
                exchange_name = order_spec.exchange_client.get_exchange_name()
                estimated_margin = await self._estimate_required_margin(order_spec, leverage_info_cache)
                exchange_margin_required.setdefault(exchange_name, Decimal("0"))
                exchange_margin_required[exchange_name] += estimated_margin
                
                # Store leverage info for potential notifications
                cache_key = (exchange_name, order_spec.symbol)
                if cache_key in leverage_info_cache:
                    if exchange_name not in exchange_leverage_info:
                        exchange_leverage_info[exchange_name] = {}
                    exchange_leverage_info[exchange_name][order_spec.symbol] = leverage_info_cache[cache_key]

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

                required_with_buffer = required_margin * Decimal("1.05")
                if available_balance < required_with_buffer:
                    error_msg = (
                        f"Insufficient balance on {exchange_name}: "
                        f"available=${available_balance:.2f}, required=${required_with_buffer:.2f} "
                        f"(${required_margin:.2f} + 5% buffer)"
                    )
                    self.logger.error(f"❌ {error_msg}")
                    
                    # Attempt to send notification
                    await self._send_insufficient_margin_notification(
                        exchange_name=exchange_name,
                        available_balance=available_balance,
                        required_margin=required_margin,
                        exchange_leverage_info=exchange_leverage_info.get(exchange_name, {}),
                        orders=orders
                    )
                    
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

    async def _rollback_filled_orders(
        self, 
        filled_orders: List[Dict[str, Any]], 
        stage_prefix: Optional[str] = None
    ) -> Decimal:
        """
        Rollback helper for atomic execution failures.
        
        CRITICAL: When rolling back a position CLOSE operation (detected via reduce_only flag or stage_prefix),
        we query actual open positions instead of trying to "undo" the close orders.
        This prevents creating new positions when the original positions were already closed.
        
        When rolling back a position OPEN operation, we undo the open orders (current behavior).
        """
        # Detect close operation using reduce_only flag (more reliable) or stage_prefix (fallback)
        # Check reduce_only flag from filled orders if available
        has_reduce_only_flag = any(
            order.get("reduce_only", False) is True 
            for order in filled_orders
        )
        is_close_operation = has_reduce_only_flag or (stage_prefix == "close")
        
        if is_close_operation:
            self.logger.warning(
                f"🚨 EMERGENCY ROLLBACK (CLOSE OPERATION): Querying actual positions "
                f"for {len(filled_orders)} exchanges"
            )
        else:
            self.logger.warning(
                f"🚨 EMERGENCY ROLLBACK (OPEN OPERATION): Closing {len(filled_orders)} filled orders"
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

        if is_close_operation:
            # For close operations: Query actual open positions instead of using filled quantities
            self.logger.info("Step 2/3: Querying actual open positions from exchanges...")
            actual_fills = []
            for order in filled_orders:
                exchange_client = order["exchange_client"]
                symbol = order["symbol"]
                exchange_config = getattr(exchange_client, "config", None)
                contract_id = getattr(exchange_config, "contract_id", symbol)
                
                try:
                    # Query position snapshot to get both size and direction
                    position_snapshot = await exchange_client.get_position_snapshot(symbol)
                    
                    if position_snapshot and hasattr(position_snapshot, 'quantity'):
                        position_qty = coerce_decimal(position_snapshot.quantity) or Decimal("0")
                        position_size = abs(position_qty)
                        
                        if position_size <= Decimal("0.0001"):
                            self.logger.info(
                                f"✅ [{exchange_client.get_exchange_name()}] {symbol}: No open position "
                                f"(already closed or never opened)"
                            )
                            continue
                        
                        # Positive quantity = long, negative = short
                        is_long = position_qty > Decimal("0")
                        close_side = "sell" if is_long else "buy"
                    else:
                        # Fallback: Query absolute position size if snapshot unavailable
                        try:
                            # Try with contract_id parameter first
                            position_size = await exchange_client.get_account_positions(contract_id)
                        except TypeError:
                            # Fallback to no-arg version if contract_id not supported
                            position_size = await exchange_client.get_account_positions()
                        
                        if position_size is None:
                            position_size = Decimal("0")
                        else:
                            position_size = coerce_decimal(position_size) or Decimal("0")
                        
                        if position_size <= Decimal("0.0001"):
                            self.logger.info(
                                f"✅ [{exchange_client.get_exchange_name()}] {symbol}: No open position "
                                f"(already closed or never opened)"
                            )
                            continue
                        
                        # Without snapshot, we can't determine direction - assume long
                        self.logger.warning(
                            f"⚠️ [{exchange_client.get_exchange_name()}] Could not get position snapshot "
                            f"for {symbol}, assuming long position"
                        )
                        close_side = "sell"
                    
                    actual_fills.append(
                        {
                            "exchange_client": exchange_client,
                            "symbol": symbol,
                            "side": close_side,  # Side to close (opposite of position)
                            "filled_quantity": position_size,  # Actual position size
                            "fill_price": Decimal("0"),  # Price not relevant for close rollback
                        }
                    )
                    self.logger.info(
                        f"📊 [{exchange_client.get_exchange_name()}] {symbol}: Found open position "
                        f"{position_size} tokens, will close via {close_side}"
                    )
                except Exception as exc:
                    self.logger.error(
                        f"❌ [{exchange_client.get_exchange_name()}] Failed to query position for "
                        f"{symbol}: {exc}"
                    )
                    # Fallback to original logic if position query fails
                    fallback_quantity = coerce_decimal(order.get("filled_quantity"))
                    if fallback_quantity and fallback_quantity > Decimal("0"):
                        self.logger.warning(
                            f"⚠️ Falling back to filled quantity {fallback_quantity} for {symbol}"
                        )
                        original_side = order.get("side")
                        # For close operations, reverse the side to undo the close
                        close_side = "sell" if original_side == "buy" else "buy"
                        actual_fills.append(
                            {
                                "exchange_client": exchange_client,
                                "symbol": symbol,
                                "side": close_side,
                                "filled_quantity": fallback_quantity,
                                "fill_price": coerce_decimal(order.get("fill_price")) or Decimal("0"),
                            }
                        )
        else:
            # For open operations: Use original logic (undo the open orders)
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

                # Use reduce_only when rolling back close operations to prevent opening new positions
                close_task = exchange_client.place_market_order(
                    contract_id=contract_id,
                    quantity=float(close_quantity),
                    side=close_side,
                    reduce_only=is_close_operation,  # Critical: prevent opening new positions when rolling back closes
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
