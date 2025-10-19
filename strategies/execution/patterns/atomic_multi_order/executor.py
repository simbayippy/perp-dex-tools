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

    def __init__(self, price_provider=None) -> None:
        self.price_provider = price_provider
        self.logger = get_core_logger("atomic_multi_order")
        self._hedge_manager = HedgeManager(price_provider=price_provider)

    async def execute_atomically(
        self,
        orders: List[OrderSpec],
        rollback_on_partial: bool = True,
        pre_flight_check: bool = True,
        skip_preflight_leverage: bool = False,
        stage_prefix: Optional[str] = None,
    ) -> AtomicExecutionResult:
        start_time = time.time()
        elapsed_ms = lambda: int((time.time() - start_time) * 1000)

        if not orders:
            self.logger.info("No orders supplied; skipping atomic execution.")
            return AtomicExecutionResult(
                success=True,
                all_filled=True,
                filled_orders=[],
                partial_fills=[],
                total_slippage_usd=Decimal("0"),
                execution_time_ms=elapsed_ms(),
                error_message=None,
                rollback_performed=False,
                rollback_cost_usd=Decimal("0"),
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
                    return AtomicExecutionResult(
                        success=False,
                        all_filled=False,
                        filled_orders=[],
                        partial_fills=[],
                        total_slippage_usd=Decimal("0"),
                        execution_time_ms=elapsed_ms(),
                        error_message=f"Pre-flight check failed: {preflight_error}",
                        rollback_performed=False,
                        rollback_cost_usd=Decimal("0"),
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

                for task in done:
                    ctx = task_map[task]
                    previous_fill = ctx.filled_quantity
                    try:
                        result = task.result()
                    except Exception as exc:  # pragma: no cover - defensive
                        self.logger.error(f"Order task failed for {ctx.spec.symbol}: {exc}")
                        result = {
                            "success": False,
                            "filled": False,
                            "error": str(exc),
                            "order_id": None,
                            "exchange_client": ctx.spec.exchange_client,
                            "symbol": ctx.spec.symbol,
                            "side": ctx.spec.side,
                            "slippage_usd": Decimal("0"),
                            "execution_mode_used": "error",
                            "filled_quantity": Decimal("0"),
                            "fill_price": None,
                        }
                    apply_result_to_context(ctx, result)
                    if ctx.filled_quantity > previous_fill:
                        newly_filled.append(ctx)

                all_completed = all(context.completed for context in contexts)

                if newly_filled and trigger_ctx is None:
                    trigger_ctx = newly_filled[0]
                    other_contexts = [c for c in contexts if c is not trigger_ctx]

                    # Cancel in-flight limits for the sibling legs.
                    for ctx in other_contexts:
                        ctx.cancel_event.set()

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
                                result_dict = {
                                    "success": False,
                                    "filled": False,
                                    "error": str(ctx_result),
                                    "order_id": None,
                                    "exchange_client": ctx.spec.exchange_client,
                                    "symbol": ctx.spec.symbol,
                                    "side": ctx.spec.side,
                                    "slippage_usd": Decimal("0"),
                                    "execution_mode_used": "error",
                                    "filled_quantity": Decimal("0"),
                                    "fill_price": None,
                                }
                            else:
                                result_dict = ctx_result
                            apply_result_to_context(ctx, result_dict)
                            if ctx.filled_quantity > previous_fill_ctx:
                                newly_filled.append(ctx)

                        pending_tasks = {task for task in pending_tasks if not task.done()}

                    for ctx in other_contexts:
                        await reconcile_context_after_cancel(ctx, self.logger)
                        trigger_qty = trigger_ctx.filled_quantity
                        if not isinstance(trigger_qty, Decimal):
                            trigger_qty = Decimal(str(trigger_qty))
                        trigger_qty = trigger_qty.copy_abs()

                        spec_qty = getattr(ctx.spec, "quantity", None)
                        if spec_qty is not None:
                            target_qty = min(trigger_qty, Decimal(str(spec_qty)))
                        else:
                            target_qty = trigger_qty

                        if target_qty < Decimal("0"):
                            target_qty = Decimal("0")
                        ctx.hedge_target_quantity = target_qty

                    hedge_success, hedge_error = await self._hedge_manager.hedge(
                        trigger_ctx, contexts, self.logger
                    )

                    if hedge_success:
                        all_completed = True
                    else:
                        if not rollback_on_partial:
                            hedge_error = hedge_error or "Hedge failure"
                        else:
                            for ctx in contexts:
                                ctx.cancel_event.set()
                            remaining = [ctx.task for ctx in contexts if not ctx.completed]
                            if remaining:
                                await asyncio.gather(*remaining, return_exceptions=True)
                            rollback_performed = True
                            rollback_payload = [
                                context_to_filled_dict(c)
                                for c in contexts
                                if c.filled_quantity > Decimal("0") and c.result
                            ]
                            rollback_cost = await self._rollback_filled_orders(rollback_payload)
                            for ctx in contexts:
                                ctx.filled_quantity = Decimal("0")
                                ctx.filled_usd = Decimal("0")
                        break

                if all_completed:
                    break

            remaining_tasks = [ctx.task for ctx in contexts if not ctx.completed]
            if remaining_tasks:
                await asyncio.gather(*remaining_tasks, return_exceptions=True)
                for ctx in contexts:
                    await reconcile_context_after_cancel(ctx, self.logger)

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
            total_long_usd = sum(ctx.filled_usd for ctx in contexts if ctx.spec.side == "buy")
            total_short_usd = sum(ctx.filled_usd for ctx in contexts if ctx.spec.side == "sell")
            imbalance = abs(total_long_usd - total_short_usd)
            imbalance_tolerance = Decimal("0.01")

            exec_ms = elapsed_ms()

            if rollback_performed:
                return AtomicExecutionResult(
                    success=False,
                    all_filled=False,
                    filled_orders=[],
                    partial_fills=partial_fills,
                    total_slippage_usd=Decimal("0"),
                    execution_time_ms=exec_ms,
                    error_message=hedge_error or "Rolled back after hedge failure",
                    rollback_performed=True,
                    rollback_cost_usd=rollback_cost,
                    residual_imbalance_usd=imbalance,
                )

            if filled_orders and len(filled_orders) == len(orders):
                if imbalance > imbalance_tolerance:
                    self.logger.warning(
                        f"Exposure imbalance detected after hedge: longs=${total_long_usd:.5f}, "
                        f"shorts=${total_short_usd:.5f}"
                    )
                return AtomicExecutionResult(
                    success=True,
                    all_filled=True,
                    filled_orders=filled_orders,
                    partial_fills=[],
                    total_slippage_usd=total_slippage,
                    execution_time_ms=exec_ms,
                    error_message=None,
                    rollback_performed=False,
                    rollback_cost_usd=Decimal("0"),
                    residual_imbalance_usd=imbalance,
                )

            error_message = hedge_error or f"Partial fill: {len(filled_orders)}/{len(orders)}"
            if imbalance > imbalance_tolerance:
                self.logger.error(
                    f"Exposure imbalance detected after hedge: longs=${total_long_usd:.5f}, "
                    f"shorts=${total_short_usd:.5f}"
                )
                imbalance_msg = f"imbalance {imbalance:.5f} USD"
                error_message = f"{error_message}; {imbalance_msg}" if error_message else imbalance_msg

            return AtomicExecutionResult(
                success=False,
                all_filled=False,
                filled_orders=filled_orders,
                partial_fills=partial_fills,
                total_slippage_usd=total_slippage,
                execution_time_ms=exec_ms,
                error_message=error_message,
                rollback_performed=False,
                rollback_cost_usd=Decimal("0"),
                residual_imbalance_usd=imbalance,
            )

        except Exception as exc:
            self.logger.error(f"Atomic execution failed: {exc}", exc_info=True)

            filled_orders = [
                ctx.result
                for ctx in locals().get("contexts", [])
                if ctx.result and ctx.filled_quantity > Decimal("0")
            ]
            partial_fills = [
                {"spec": ctx.spec, "result": ctx.result}
                for ctx in locals().get("contexts", [])
                if not (ctx.result and ctx.filled_quantity > Decimal("0"))
            ]

            rollback_cost = None
            if filled_orders and rollback_on_partial:
                rollback_cost = await self._rollback_filled_orders(filled_orders)
                filled_orders = []

            return AtomicExecutionResult(
                success=False,
                all_filled=False,
                filled_orders=filled_orders,
                partial_fills=partial_fills,
                total_slippage_usd=Decimal("0"),
                execution_time_ms=elapsed_ms(),
                error_message=str(exc),
                rollback_performed=bool(rollback_cost and rollback_on_partial),
                rollback_cost_usd=rollback_cost,
                residual_imbalance_usd=Decimal("0"),
            )

    @staticmethod
    def _estimate_required_margin(size_usd: Decimal) -> Decimal:
        """Conservative margin estimate (assumes 20% initial margin)."""
        return size_usd * Decimal("0.20")

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
        )

        return execution_result_to_dict(spec, result)

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

                self.logger.info(
                    f"Rollback: {close_side} {fill['symbol']} {fill['filled_quantity']} @ market"
                )

                exchange_client = fill["exchange_client"]
                exchange_config = getattr(exchange_client, "config", None)
                contract_id = getattr(exchange_config, "contract_id", fill["symbol"])

                self.logger.debug(
                    f"Rollback: Using contract_id='{contract_id}' for symbol '{fill['symbol']}'"
                )

                close_task = exchange_client.place_market_order(
                    contract_id=contract_id,
                    quantity=float(fill["filled_quantity"]),
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
