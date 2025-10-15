"""
Atomic Multi-Order Executor - Delta-neutral execution with rollback.

⭐ Inspired by Hummingbot's ArbitrageExecutor ⭐

Executes multiple orders atomically - all must succeed or all rollback.
Critical for delta-neutral strategies where partial fills create directional exposure.

Key features:
- Simultaneous order placement across multiple DEXes
- Pre-flight liquidity checks
- Automatic rollback on partial fills
- Execution quality tracking

Use cases:
- Funding arb: Long DEX A + Short DEX B (must both fill)
- Cross-DEX arbitrage: Buy + Sell simultaneously
- Market making: Bid + Ask on same DEX
"""

from typing import Any, List, Optional, Dict
from decimal import Decimal, InvalidOperation
from dataclasses import dataclass
from enum import Enum
import time
import asyncio
from helpers.unified_logger import get_core_logger, log_stage

logger = get_core_logger("atomic_multi_order")


@dataclass
class _OrderContext:
    spec: "OrderSpec"
    cancel_event: asyncio.Event
    task: asyncio.Task
    result: Optional[Dict] = None
    completed: bool = False
    filled_quantity: Decimal = Decimal("0")
    filled_usd: Decimal = Decimal("0")

    @property
    def remaining_usd(self) -> Decimal:
        remaining = self.spec.size_usd - self.filled_usd
        return remaining if remaining > Decimal("0") else Decimal("0")

    def record_fill(self, quantity: Optional[Decimal], price: Optional[Decimal]) -> None:
        if quantity is None or quantity <= Decimal("0"):
            return
        self.filled_quantity += quantity
        if price is not None and price > Decimal("0"):
            self.filled_usd += quantity * price
        elif self.filled_usd == Decimal("0"):
            self.filled_usd = self.spec.size_usd
        if self.filled_usd > self.spec.size_usd:
            self.filled_usd = self.spec.size_usd


@dataclass
class OrderSpec:
    """
    Specification for a single order in atomic batch.
    
    Defines all parameters needed to execute one order.
    """
    exchange_client: Any
    symbol: str
    side: str  # "buy" or "sell"
    size_usd: Decimal
    execution_mode: str = "limit_with_fallback"  # or "market_only"
    timeout_seconds: float = 30.0
    
    # Optional overrides
    limit_price_offset_pct: Optional[Decimal] = None  # Custom offset for limit orders


@dataclass
class AtomicExecutionResult:
    """
    Result of atomic multi-order execution.
    
    Contains detailed information about the execution outcome.
    """
    success: bool
    all_filled: bool
    
    # Filled orders (list of ExecutionResult-like dicts)
    filled_orders: List[Dict]
    partial_fills: List[Dict]  # Orders that didn't fill or failed
    
    # Aggregated metrics
    total_slippage_usd: Decimal
    execution_time_ms: int
    
    # Error handling
    error_message: Optional[str] = None
    rollback_performed: bool = False
    rollback_cost_usd: Optional[Decimal] = None  # Cost of emergency rollback


class AtomicMultiOrderExecutor:
    """
    Executes multiple orders atomically - all must succeed or all rollback.
    
    ⭐ Inspired by Hummingbot's ArbitrageExecutor ⭐
    
    Critical for delta-neutral strategies where partial fills create exposure.
    
    Example:
        executor = AtomicMultiOrderExecutor()
        
        # Execute long + short atomically
        result = await executor.execute_atomically(
            orders=[
                OrderSpec(long_client, "BTC-PERP", "buy", Decimal("1000")),
                OrderSpec(short_client, "BTC-PERP", "sell", Decimal("1000"))
            ],
            rollback_on_partial=True  # 🚨 Both fill or neither
        )
        
        if result.all_filled:
            print("Delta neutral position opened!")
        else:
            print(f"Atomic execution failed: {result.error_message}")
    """
    
    def __init__(self, price_provider=None):
        """
        Initialize atomic multi-order executor.
        
        Args:
            price_provider: Optional PriceProvider for shared price caching
        """
        self.price_provider = price_provider
        self.logger = get_core_logger("atomic_multi_order")
    
    def _compose_stage_id(
        self,
        stage_prefix: Optional[str],
        *parts: str
    ) -> Optional[str]:
        """
        Compose hierarchical stage identifiers like "3.1.2".
        
        Args:
            stage_prefix: Base prefix inherited from caller (e.g., "3")
            *parts: Additional segments to append (e.g., "1", "2")
        
        Returns:
            Combined identifier or None if no segments provided.
        """
        if stage_prefix:
            if parts:
                return ".".join([stage_prefix, *parts])
            return stage_prefix
        if parts:
            return ".".join(parts)
        return None
    
    async def execute_atomically(
        self,
        orders: List[OrderSpec],
        rollback_on_partial: bool = True,
        pre_flight_check: bool = True,
        skip_preflight_leverage: bool = False,
        stage_prefix: Optional[str] = None
    ) -> AtomicExecutionResult:
        start_time = time.time()
        execution_time_ms = lambda: int((time.time() - start_time) * 1000)
        if not orders:
            self.logger.info("Starting atomic execution of 0 orders (nothing to do)")
            return AtomicExecutionResult(
                success=True,
                all_filled=True,
                filled_orders=[],
                partial_fills=[],
                total_slippage_usd=Decimal('0'),
                execution_time_ms=execution_time_ms(),
                error_message=None,
                rollback_performed=False,
                rollback_cost_usd=Decimal('0')
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
                    stage_prefix=compose_stage("1")
                )
                if not preflight_ok:
                    return AtomicExecutionResult(
                        success=False,
                        all_filled=False,
                        filled_orders=[],
                        partial_fills=[],
                        total_slippage_usd=Decimal('0'),
                        execution_time_ms=execution_time_ms(),
                        error_message=f"Pre-flight check failed: {preflight_error}",
                        rollback_performed=False,
                        rollback_cost_usd=Decimal('0')
                    )

            log_stage(self.logger, "Order Placement", icon="🚀", stage_id=compose_stage("2"))
            self.logger.info("🚀 Placing all orders simultaneously...")

            contexts: List[_OrderContext] = []
            task_map: Dict[asyncio.Task, _OrderContext] = {}
            pending_tasks: set[asyncio.Task] = set()

            for spec in orders:
                cancel_event = asyncio.Event()
                task = asyncio.create_task(self._place_single_order(spec, cancel_event=cancel_event))
                ctx = _OrderContext(spec=spec, cancel_event=cancel_event, task=task)
                contexts.append(ctx)
                task_map[task] = ctx
                pending_tasks.add(task)

            trigger_ctx: Optional[_OrderContext] = None
            hedge_error: Optional[str] = None
            rollback_performed = False
            rollback_cost = Decimal('0')

            while pending_tasks:
                done, pending_tasks = await asyncio.wait(pending_tasks, return_when=asyncio.FIRST_COMPLETED)
                newly_filled_ctxs: List[_OrderContext] = []

                for task in done:
                    ctx = task_map[task]
                    prev_filled = ctx.filled_quantity
                    try:
                        result = task.result()
                    except Exception as exc:  # pragma: no cover - defensive
                        self.logger.error(f"Order task failed for {ctx.spec.symbol}: {exc}")
                        result = {
                            'success': False,
                            'filled': False,
                            'error': str(exc),
                            'order_id': None,
                            'exchange_client': ctx.spec.exchange_client,
                            'symbol': ctx.spec.symbol,
                            'side': ctx.spec.side,
                            'slippage_usd': Decimal('0'),
                            'execution_mode_used': 'error',
                            'filled_quantity': Decimal('0'),
                            'fill_price': None,
                        }
                    self._apply_result_to_context(ctx, result)
                    if ctx.filled_quantity > prev_filled:
                        newly_filled_ctxs.append(ctx)


                all_completed = all(ctx.completed for ctx in contexts)

                if newly_filled_ctxs and trigger_ctx is None:
                    trigger_ctx = newly_filled_ctxs[0]
                    other_contexts = [c for c in contexts if c is not trigger_ctx]

                    for ctx in other_contexts:
                        ctx.cancel_event.set()

                    pending_completion = [ctx.task for ctx in other_contexts if not ctx.completed]
                    if pending_completion:
                        await asyncio.gather(*pending_completion, return_exceptions=True)

                    for ctx in other_contexts:
                        await self._reconcile_context_after_cancel(ctx)

                    hedge_success, hedge_error = await self._execute_market_hedge(trigger_ctx, contexts)

                    if hedge_success:
                        all_completed = True
                    else:
                        for ctx in contexts:
                            ctx.cancel_event.set()
                        remaining = [ctx.task for ctx in contexts if not ctx.completed]
                        if remaining:
                            await asyncio.gather(*remaining, return_exceptions=True)
                        rollback_performed = True
                        rollback_payload = [
                            self._context_to_filled_dict(c)
                            for c in contexts
                            if c.filled_quantity > Decimal('0') and c.result
                        ]
                        rollback_cost = await self._rollback_filled_orders(rollback_payload)
                        for ctx in contexts:
                            ctx.filled_quantity = Decimal('0')
                            ctx.filled_usd = Decimal('0')
                        break

                if all_completed:
                    break

            remaining = [ctx.task for ctx in contexts if not ctx.completed]
            if remaining:
                await asyncio.gather(*remaining, return_exceptions=True)
                for ctx in contexts:
                    await self._reconcile_context_after_cancel(ctx)

            filled_orders = [ctx.result for ctx in contexts if ctx.result and ctx.filled_quantity > Decimal('0')]
            partial_fills = [
                {'spec': ctx.spec, 'result': ctx.result}
                for ctx in contexts
                if not (ctx.result and ctx.filled_quantity > Decimal('0'))
            ]

            total_slippage = sum(
                self._coerce_decimal(order.get('slippage_usd')) or Decimal('0')
                for order in filled_orders
            )
            total_long_usd = sum(ctx.filled_usd for ctx in contexts if ctx.spec.side == 'buy')
            total_short_usd = sum(ctx.filled_usd for ctx in contexts if ctx.spec.side == 'sell')
            imbalance = abs(total_long_usd - total_short_usd)
            imbalance_tolerance = Decimal('0.01')

            exec_ms = execution_time_ms()

            if rollback_performed:
                return AtomicExecutionResult(
                    success=False,
                    all_filled=False,
                    filled_orders=[],
                    partial_fills=partial_fills,
                    total_slippage_usd=Decimal('0'),
                    execution_time_ms=exec_ms,
                    error_message=hedge_error or "Rolled back after hedge failure",
                    rollback_performed=True,
                    rollback_cost_usd=rollback_cost
                )

            if filled_orders and imbalance <= imbalance_tolerance and len(filled_orders) == len(orders):
                return AtomicExecutionResult(
                    success=True,
                    all_filled=True,
                    filled_orders=filled_orders,
                    partial_fills=[],
                    total_slippage_usd=total_slippage,
                    execution_time_ms=exec_ms,
                    error_message=None,
                    rollback_performed=False,
                    rollback_cost_usd=Decimal('0')
                )

            error_message = hedge_error or f"Partial fill: {len(filled_orders)}/{len(orders)}"
            if imbalance > imbalance_tolerance:
                self.logger.error(
                    f"Exposure imbalance detected after hedge: longs=${total_long_usd:.5f}, shorts=${total_short_usd:.5f}"
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
                rollback_cost_usd=Decimal('0')
            )

        except Exception as e:
            self.logger.error(f"Atomic execution failed: {e}", exc_info=True)

            filled_orders = [ctx.result for ctx in locals().get('contexts', []) if ctx.result and ctx.filled_quantity > Decimal('0')]
            partial_fills = [
                {'spec': ctx.spec, 'result': ctx.result}
                for ctx in locals().get('contexts', [])
                if not (ctx.result and ctx.filled_quantity > Decimal('0'))
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
                total_slippage_usd=Decimal('0'),
                execution_time_ms=execution_time_ms(),
                error_message=str(e),
                rollback_performed=bool(rollback_cost and rollback_on_partial),
                rollback_cost_usd=rollback_cost
            )

    async def _run_preflight_checks(
        self,
        orders: List[OrderSpec],
        skip_leverage_check: bool = False,
        stage_prefix: Optional[str] = None
    ) -> tuple[bool, Optional[str]]:
        """
        Run pre-flight checks on all orders.
        
        Checks (unless skipped via flags):
        1. Leverage limits & normalization
        2. Account balance sufficiency (CRITICAL - prevents partial fills due to insufficient margin)
        3. Liquidity availability (prevents high slippage)
        
        Returns:
            (all_checks_passed, error_message)
        """
        try:
            compose_stage = lambda *parts: self._compose_stage_id(stage_prefix, *parts)
            # Group orders by symbol (for delta-neutral we need same size on both sides)
            symbols_to_check: Dict[str, List[OrderSpec]] = {}
            for order_spec in orders:
                symbol = order_spec.symbol
                if symbol not in symbols_to_check:
                    symbols_to_check[symbol] = []
                symbols_to_check[symbol].append(order_spec)
            
            # Import here to avoid circular dependency
            from strategies.execution.core.liquidity_analyzer import LiquidityAnalyzer
            
            if not skip_leverage_check:
                log_stage(self.logger, "Leverage Validation", icon="📐", stage_id=compose_stage("1"))
                # ====================================================================
                # CHECK 0: Leverage Limit Validation (CRITICAL FOR DELTA NEUTRAL)
                # ====================================================================
                self.logger.info("Checking leverage limits across exchanges...")
                
                # Import leverage validator
                from strategies.execution.core.leverage_validator import LeverageValidator
                
                leverage_validator = LeverageValidator()
                
                # For each symbol, verify all exchanges can support the requested size
                for symbol, symbol_orders in symbols_to_check.items():
                    # Get all exchange clients for this symbol
                    exchange_clients = [order.exchange_client for order in symbol_orders]
                    
                    # Get requested size (should be same for all orders in delta-neutral strategy)
                    requested_size = symbol_orders[0].size_usd
                    
                    # Check max size supported by ALL exchanges
                    max_size, limiting_exchange = await leverage_validator.get_max_position_size(
                        exchange_clients=exchange_clients,
                        symbol=symbol,
                        requested_size_usd=requested_size,
                        check_balance=True  # Also consider available balance
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
                
                # 🔧 CRITICAL: Now set the leverage to min(exchange1, exchange2)
                for symbol, symbol_orders in symbols_to_check.items():
                    exchange_clients = [order.exchange_client for order in symbol_orders]
                    requested_size = symbol_orders[0].size_usd
                    
                    self.logger.info(f"Normalizing leverage for {symbol}...")
                    min_leverage, limiting = await leverage_validator.normalize_and_set_leverage(
                        exchange_clients=exchange_clients,
                        symbol=symbol,
                        requested_size_usd=requested_size
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
            
            log_stage(self.logger, "Market Data Streams", icon="📡", stage_id=compose_stage("1"))
            # ========================================================================
            # SETUP: Start WebSocket book tickers for real-time BBO (Issue #3 fix)
            # ========================================================================
            self.logger.info("Starting WebSocket book tickers for real-time pricing...")
            
            for symbol, symbol_orders in symbols_to_check.items():
                for order in symbol_orders:
                    exchange_client = order.exchange_client
                    exchange_name = exchange_client.get_exchange_name()
                    
                    # Start WebSocket streams if supported
                    if hasattr(exchange_client, 'ws_manager') and exchange_client.ws_manager:
                        ws_manager = exchange_client.ws_manager
                        
                        # Get normalized symbol for this exchange
                        if exchange_name == "aster":
                            # Aster needs full symbol like "SKYUSDT"
                            normalized_symbol = getattr(exchange_client.config, 'contract_id', f"{symbol}USDT")
                            
                            # Start book ticker for BBO (limit orders)
                            if hasattr(ws_manager, 'start_book_ticker'):
                                await ws_manager.start_book_ticker(normalized_symbol)
                                self.logger.info(f"✅ Started Aster book ticker for {normalized_symbol}")
                            
                            # Start order book depth stream for liquidity checks
                            if hasattr(ws_manager, 'start_order_book_stream'):
                                await ws_manager.start_order_book_stream(normalized_symbol)
                                self.logger.info(f"✅ Started Aster order book depth stream for {normalized_symbol}")
                        
                        elif exchange_name == "lighter":
                            # Lighter WebSocket needs to switch to the correct market
                            # Get market_id for the opportunity symbol (not the startup default!)
                            try:
                                market_id = await exchange_client._get_market_id_for_symbol(symbol)
                                if market_id is None:
                                    self.logger.warning(f"⚠️  Could not find market_id for {symbol} on Lighter")
                                    continue
                                
                                if hasattr(ws_manager, 'switch_market'):
                                    # Switch to the opportunity's market
                                    success = await ws_manager.switch_market(market_id)
                                    if success:
                                        self.logger.info(f"✅ Lighter order book WebSocket switched to market {market_id} ({symbol})")
                                    else:
                                        self.logger.warning(f"⚠️  Failed to switch Lighter WebSocket to market {market_id}")
                                else:
                                    self.logger.info(f"✅ Lighter order book WebSocket already active for {symbol}")
                            except Exception as e:
                                self.logger.error(f"Error switching Lighter market: {e}")
            
            # Give WebSockets time to receive first updates:
            # - Aster: book ticker + depth stream subscription (~1s)
            # - Lighter: market switch + order book snapshot (~0.5s)
            await asyncio.sleep(2.0)
            
            log_stage(self.logger, "Margin & Balance Checks", icon="💰", stage_id=compose_stage("2"))
            # ========================================================================
            # CHECK 1: Account Balance Validation (CRITICAL FIX)
            # ========================================================================
            self.logger.info("Running balance checks...")
            
            # Calculate total required margin per exchange
            exchange_margin_required: Dict[str, Decimal] = {}
            
            for order_spec in orders:
                exchange_name = order_spec.exchange_client.get_exchange_name()
                
                # Estimate required margin (conservative: assume 5x leverage = 20% margin)
                # Most perp DEXs use 5-20x leverage, so 20% margin is conservative
                estimated_margin = order_spec.size_usd * Decimal('0.20')
                
                if exchange_name not in exchange_margin_required:
                    exchange_margin_required[exchange_name] = Decimal('0')
                
                exchange_margin_required[exchange_name] += estimated_margin
            
            # Check each exchange's available balance
            for exchange_name, required_margin in exchange_margin_required.items():
                # Find exchange client for this exchange
                exchange_client = None
                for order_spec in orders:
                    if order_spec.exchange_client.get_exchange_name() == exchange_name:
                        exchange_client = order_spec.exchange_client
                        break
                
                if not exchange_client:
                    continue
                
                # Check if exchange supports balance queries
                try:
                    available_balance = await exchange_client.get_account_balance()
                    
                    if available_balance is None:
                        # Exchange doesn't support balance queries - log warning but continue
                        self.logger.warning(
                            f"⚠️ Cannot verify balance for {exchange_name} "
                            f"(required: ~${required_margin:.2f})"
                        )
                        continue
                    
                    # Add 10% buffer for safety (fees, slippage, etc.)
                    required_with_buffer = required_margin * Decimal('1.10')
                    
                    if available_balance < required_with_buffer:
                        error_msg = (
                            f"Insufficient balance on {exchange_name}: "
                            f"available=${available_balance:.2f}, "
                            f"required=${required_with_buffer:.2f} "
                            f"(${required_margin:.2f} + 10% buffer)"
                        )
                        self.logger.error(f"❌ {error_msg}")
                        return False, error_msg
                    
                    self.logger.info(
                        f"✅ {exchange_name} balance OK: "
                        f"${available_balance:.2f} >= ${required_with_buffer:.2f}"
                    )
                
                except Exception as e:
                    # Balance check failed - log but don't fail execution
                    self.logger.warning(
                        f"⚠️ Balance check failed for {exchange_name}: {e}"
                    )
            
            log_stage(self.logger, "Order Book Liquidity", icon="🌊", stage_id=compose_stage("3"))
            # ========================================================================
            # CHECK 2: Liquidity Analysis
            # ========================================================================
            self.logger.info("Running liquidity checks...")
            
            # Use shared price_provider if available (enables caching)
            # Increase spread tolerance for smaller tokens like PROVE
            analyzer = LiquidityAnalyzer(
                price_provider=self.price_provider,
                max_spread_bps=100,  # Allow up to 100 bps (1%) for smaller tokens
            )
            
            for i, order_spec in enumerate(orders):
                # Check liquidity
                self.logger.debug(
                    f"Checking liquidity for order {i}: {order_spec.side} {order_spec.symbol} ${order_spec.size_usd}"
                )
                report = await analyzer.check_execution_feasibility(
                    exchange_client=order_spec.exchange_client,
                    symbol=order_spec.symbol,
                    side=order_spec.side,
                    size_usd=order_spec.size_usd
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
        
        except Exception as e:
            self.logger.error(f"Pre-flight check error: {e}")
            # Don't fail execution on pre-flight check errors (defensive)
            # Better to attempt execution than block potentially valid trades
            self.logger.warning("⚠️ Continuing despite pre-flight check error")
            return True, None
    
    async def _place_single_order(self, spec: OrderSpec, cancel_event: Optional[asyncio.Event] = None) -> Dict:
        """
        Place a single order from spec.
        
        Returns:
            ExecutionResult-like dict
        """
        try:
            # Import here to avoid circular dependency
            from strategies.execution.core.order_executor import OrderExecutor, ExecutionMode
            
            # Use shared price_provider if available
            executor = OrderExecutor(price_provider=self.price_provider)
            
            # Map string mode to enum
            mode_map = {
                "limit_only": ExecutionMode.LIMIT_ONLY,
                "limit_with_fallback": ExecutionMode.LIMIT_WITH_FALLBACK,
                "market_only": ExecutionMode.MARKET_ONLY,
                "adaptive": ExecutionMode.ADAPTIVE
            }
            
            execution_mode = mode_map.get(spec.execution_mode, ExecutionMode.LIMIT_WITH_FALLBACK)
            
            # Execute order
            result = await executor.execute_order(
                exchange_client=spec.exchange_client,
                symbol=spec.symbol,
                side=spec.side,
                size_usd=spec.size_usd,
                mode=execution_mode,
                timeout_seconds=spec.timeout_seconds,
                limit_price_offset_pct=spec.limit_price_offset_pct,
                cancel_event=cancel_event
            )
            
            return self._execution_result_to_dict(spec, result)
        
        except Exception as e:
            self.logger.error(f"Single order placement failed: {e}", exc_info=True)
            raise

    def _execution_result_to_dict(self, spec: OrderSpec, execution_result, hedge: bool = False) -> Dict:
        data = {
            'success': execution_result.success,
            'filled': execution_result.filled,
            'fill_price': execution_result.fill_price,
            'filled_quantity': execution_result.filled_quantity,
            'slippage_usd': execution_result.slippage_usd,
            'execution_mode_used': execution_result.execution_mode_used,
            'order_id': execution_result.order_id,
            'exchange_client': spec.exchange_client,
            'symbol': spec.symbol,
            'side': spec.side,
        }
        if hedge:
            data['hedge'] = True
        return data

    def _apply_result_to_context(self, ctx: _OrderContext, result: Dict) -> None:
        ctx.result = result
        ctx.completed = True
        fill_qty = self._coerce_decimal(result.get('filled_quantity'))
        fill_price = self._coerce_decimal(result.get('fill_price'))
        ctx.record_fill(fill_qty, fill_price)

    async def _reconcile_context_after_cancel(self, ctx: _OrderContext) -> None:
        if ctx.remaining_usd <= Decimal('0'):
            return
        result = ctx.result or {}
        order_id = result.get('order_id')
        if not order_id:
            return
        try:
            order_info = await ctx.spec.exchange_client.get_order_info(order_id)
        except Exception as exc:  # pragma: no cover - defensive
            self.logger.warning(
                f"⚠️ Failed to reconcile fill for {ctx.spec.symbol} on cancel: {exc}"
            )
            return
        if order_info is None:
            return
        reported_qty = self._coerce_decimal(getattr(order_info, 'filled_size', None))
        if reported_qty is None or reported_qty <= ctx.filled_quantity:
            return
        price_candidates = [
            getattr(order_info, attr, None) for attr in ('price', 'average_price', 'avg_price')
        ]
        reported_price = None
        for candidate in price_candidates:
            reported_price = self._coerce_decimal(candidate)
            if reported_price is not None:
                break
        additional = reported_qty - ctx.filled_quantity
        ctx.record_fill(additional, reported_price)
        if ctx.result is None:
            ctx.result = {
                'success': True,
                'filled': True,
                'fill_price': reported_price,
                'filled_quantity': reported_qty,
                'slippage_usd': Decimal('0'),
                'execution_mode_used': 'limit',
                'order_id': order_id,
                'exchange_client': ctx.spec.exchange_client,
                'symbol': ctx.spec.symbol,
                'side': ctx.spec.side
            }
        else:
            ctx.result['filled'] = True
            ctx.result['filled_quantity'] = reported_qty
            if reported_price is not None:
                ctx.result['fill_price'] = reported_price

    def _context_to_filled_dict(self, ctx: _OrderContext) -> Dict:
        if ctx.result:
            return ctx.result
        return {
            'success': True,
            'filled': True,
            'fill_price': None,
            'filled_quantity': ctx.filled_quantity,
            'slippage_usd': Decimal('0'),
            'execution_mode_used': 'hedge',
            'order_id': None,
            'exchange_client': ctx.spec.exchange_client,
            'symbol': ctx.spec.symbol,
            'side': ctx.spec.side
        }

    async def _execute_market_hedge(
        self,
        trigger_ctx: _OrderContext,
        contexts: List[_OrderContext],
    ) -> tuple[bool, Optional[str]]:
        from strategies.execution.core.order_executor import OrderExecutor, ExecutionMode

        hedge_executor = OrderExecutor(price_provider=self.price_provider)

        for ctx in contexts:
            if ctx is trigger_ctx:
                continue

            remaining_usd = ctx.remaining_usd
            if remaining_usd <= Decimal('0'):
                continue

            spec = ctx.spec
            exchange_name = spec.exchange_client.get_exchange_name().upper()
            self.logger.info(
                f"⚡ Hedging {spec.symbol} on {exchange_name} for remaining ${float(remaining_usd):.2f}"
            )

            try:
                execution = await hedge_executor.execute_order(
                    exchange_client=spec.exchange_client,
                    symbol=spec.symbol,
                    side=spec.side,
                    size_usd=remaining_usd,
                    mode=ExecutionMode.MARKET_ONLY,
                    timeout_seconds=spec.timeout_seconds
                )
            except Exception as exc:
                self.logger.error(f"Hedge order failed on {exchange_name}: {exc}")
                return False, str(exc)

            if not execution.success or not execution.filled:
                error = execution.error_message or f"Market hedge failed on {exchange_name}"
                self.logger.error(error)
                return False, error

            hedge_dict = self._execution_result_to_dict(spec, execution, hedge=True)
            self._apply_result_to_context(ctx, hedge_dict)

        return True, None
    
    async def _rollback_filled_orders(self, filled_orders: List[Dict]) -> Decimal:
        """
        Emergency rollback: Market close all filled orders.
        
        ⚠️ This will incur slippage but prevents directional exposure.
        
        🔒 CRITICAL FIX: Race condition protection
        1. Cancel all orders FIRST to prevent further fills
        2. Query actual filled amounts AFTER cancellation
        3. Close actual filled amounts (not cached values)
        
        Args:
            filled_orders: List of successfully filled orders
        
        Returns:
            Total rollback cost in USD
        """
        self.logger.warning(
            f"🚨 EMERGENCY ROLLBACK: Closing {len(filled_orders)} filled orders"
        )
        
        total_rollback_cost = Decimal('0')
        
        # Step 1: CANCEL ALL ORDERS IMMEDIATELY (stop the bleeding)
        self.logger.info("Step 1/3: Canceling all orders to prevent further fills...")
        cancel_tasks = []
        for order in filled_orders:
            if order.get('order_id'):
                try:
                    cancel_task = order['exchange_client'].cancel_order(order['order_id'])
                    cancel_tasks.append(cancel_task)
                except Exception as e:
                    self.logger.error(f"Failed to create cancel task for {order.get('order_id')}: {e}")
        
        if cancel_tasks:
            cancel_results = await asyncio.gather(*cancel_tasks, return_exceptions=True)
            for i, result in enumerate(cancel_results):
                if isinstance(result, Exception):
                    self.logger.warning(f"Cancel failed for order {i}: {result}")
            
            # Small delay to ensure cancellation propagates through exchange systems
            await asyncio.sleep(0.5)
        
        # Step 2: Query ACTUAL filled amounts after cancellation
        self.logger.info("Step 2/3: Querying actual filled amounts...")
        actual_fills = []
        for order in filled_orders:
            exchange_client = order['exchange_client']
            symbol = order['symbol']
            side = order['side']
            order_id = order.get('order_id')
            fallback_quantity = self._coerce_decimal(order.get('filled_quantity'))
            fallback_price = self._coerce_decimal(order.get('fill_price')) or Decimal('0')

            actual_quantity: Optional[Decimal] = None

            if order_id:
                try:
                    order_info = await exchange_client.get_order_info(order_id)
                except Exception as e:
                    self.logger.error(
                        f"Failed to get actual fill for {order_id}: {e}"
                    )
                    order_info = None

                if order_info is not None:
                    reported_qty = self._coerce_decimal(getattr(order_info, 'filled_size', None))

                    if reported_qty is not None and reported_qty > Decimal('0'):
                        actual_quantity = reported_qty

                        if fallback_quantity is not None and abs(reported_qty - fallback_quantity) > Decimal('0.0001'):
                            self.logger.warning(
                                f"⚠️ Fill amount changed for {symbol}: "
                                f"{fallback_quantity} → {reported_qty} "
                                f"(Δ={reported_qty - fallback_quantity})"
                            )
                    else:
                        # Some exchanges report 0 after cancellation; fallback to cached quantity
                        if fallback_quantity is not None and fallback_quantity > Decimal('0'):
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
                if fallback_quantity is not None and fallback_quantity > Decimal('0'):
                    actual_quantity = fallback_quantity
                else:
                    self.logger.warning(
                        f"⚠️ Skipping rollback close for {symbol}: unable to determine filled quantity"
                    )
                    continue

            actual_fills.append({
                'exchange_client': exchange_client,
                'symbol': symbol,
                'side': side,
                'filled_quantity': actual_quantity,
                'fill_price': fallback_price
            })
        
        # Step 3: Close actual filled amounts
        self.logger.info(f"Step 3/3: Closing {len(actual_fills)} filled positions...")
        rollback_tasks = []
        for fill in actual_fills:
            try:
                close_side = "sell" if fill['side'] == "buy" else "buy"
                
                self.logger.info(
                    f"Rollback: {close_side} {fill['symbol']} "
                    f"{fill['filled_quantity']} @ market"
                )
                
                # 🔧 FIX: Get contract_id from exchange client config
                # get_contract_attributes() doesn't take arguments - it uses self.config.ticker
                # The contract_id is already set during exchange client initialization
                exchange_client = fill['exchange_client']
                exchange_config = getattr(exchange_client, 'config', None)
                contract_id = getattr(exchange_config, 'contract_id', fill['symbol'])
                
                self.logger.debug(
                    f"Rollback: Using contract_id='{contract_id}' for symbol '{fill['symbol']}'"
                )
                
                # Place market close order
                close_task = fill['exchange_client'].place_market_order(
                    contract_id=contract_id,
                    quantity=float(fill['filled_quantity']),  # ✅ Use ACTUAL fill
                    side=close_side
                )
                rollback_tasks.append((close_task, fill))
            except Exception as e:
                self.logger.error(
                    f"Failed to create rollback order for {fill['symbol']}: {e}"
                )
        
        # Execute all rollback orders
        if rollback_tasks:
            tasks_only = [task for task, _ in rollback_tasks]
            results = await asyncio.gather(*tasks_only, return_exceptions=True)
            
            # Calculate rollback cost (slippage from entry to exit)
            for i, result in enumerate(results):
                fill = rollback_tasks[i][1]
                if isinstance(result, Exception):
                    self.logger.error(
                        f"Rollback order failed for {fill['symbol']}: {result}"
                    )
                elif hasattr(result, 'price'):
                    entry_price = fill['fill_price']
                    exit_price = Decimal(str(result.price))
                    quantity = fill['filled_quantity']
                    
                    rollback_cost = abs(exit_price - entry_price) * quantity
                    total_rollback_cost += rollback_cost
                    
                    self.logger.warning(
                        f"Rollback cost for {fill['symbol']}: ${rollback_cost:.2f} "
                        f"(entry: ${entry_price}, exit: ${exit_price})"
                    )
        
        self.logger.warning(
            f"✅ Rollback complete. Total cost: ${total_rollback_cost:.2f}"
        )
        
        return total_rollback_cost

    @staticmethod
    def _coerce_decimal(value: Any) -> Optional[Decimal]:
        """Best-effort conversion to Decimal for heterogeneous exchange payloads."""
        if isinstance(value, Decimal):
            return value
        if value is None:
            return None
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            return None
