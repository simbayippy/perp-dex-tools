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
from decimal import Decimal
from dataclasses import dataclass
from enum import Enum
import time
import asyncio
import logging

logger = logging.getLogger(__name__)


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
        self.logger = logging.getLogger(__name__)
    
    async def execute_atomically(
        self,
        orders: List[OrderSpec],
        rollback_on_partial: bool = True,
        pre_flight_check: bool = True
    ) -> AtomicExecutionResult:
        """
        Execute all orders atomically. If any fail and rollback_on_partial=True,
        market-close all successful fills.
        
        Args:
            orders: List of order specifications
            rollback_on_partial: If True, rollback all on partial fill
            pre_flight_check: If True, check liquidity before placing orders
        
        Returns:
            AtomicExecutionResult with execution details
        
        Flow:
        1. Pre-flight checks (liquidity, balance) - optional
        2. Place all orders simultaneously (asyncio.gather)
        3. Monitor fills with timeout
        4. If partial fill detected → rollback or accept
        5. Return execution result
        """
        start_time = time.time()
        filled_orders = []
        partial_fills = []
        
        try:
            self.logger.info(
                f"Starting atomic execution of {len(orders)} orders "
                f"(rollback_on_partial={rollback_on_partial})"
            )
            
            # Step 1: Pre-flight checks (optional)
            if pre_flight_check:
                # Separator to indicate pre-flight checks are starting
                self.logger.info("=" * 60)
                self.logger.info("🔍 RUNNING PRE-FLIGHT CHECKS")
                self.logger.info("=" * 60)
                
                preflight_ok, preflight_error = await self._run_preflight_checks(orders)
                if not preflight_ok:
                    return AtomicExecutionResult(
                        success=False,
                        all_filled=False,
                        filled_orders=[],
                        partial_fills=[],
                        total_slippage_usd=Decimal('0'),
                        execution_time_ms=int((time.time() - start_time) * 1000),
                        error_message=f"Pre-flight check failed: {preflight_error}"
                    )
                
                # Separator to indicate pre-flight checks are complete
                self.logger.info("=" * 60)
                self.logger.info("✅ PRE-FLIGHT CHECKS COMPLETE - PROCEEDING TO ORDER PLACEMENT")
                self.logger.info("=" * 60)
            
            # Step 2: Place all orders simultaneously
            self.logger.info("🚀 Placing all orders simultaneously...")
            order_tasks = [
                self._place_single_order(spec) for spec in orders
            ]
            results = await asyncio.gather(*order_tasks, return_exceptions=True)
            
            # Step 3: Analyze results
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    partial_fills.append({
                        'order_index': i,
                        'error': str(result),
                        'spec': orders[i]
                    })
                elif isinstance(result, dict) and result.get('filled'):
                    filled_orders.append(result)
                else:
                    partial_fills.append({
                        'order_index': i,
                        'result': result,
                        'spec': orders[i]
                    })
            
            # Check if ALL succeeded
            all_success = len(filled_orders) == len(orders)
            
            if all_success:
                # ✅ Perfect atomic execution
                total_slippage = sum(
                    Decimal(str(r.get('slippage_usd', 0))) for r in filled_orders
                )
                
                self.logger.info(
                    f"✅ Atomic execution successful: {len(filled_orders)}/{len(orders)} filled"
                )
                
                return AtomicExecutionResult(
                    success=True,
                    all_filled=True,
                    filled_orders=filled_orders,
                    partial_fills=[],
                    total_slippage_usd=total_slippage,
                    execution_time_ms=int((time.time() - start_time) * 1000)
                )
            
            # Step 4: Partial fill detected
            self.logger.warning(
                f"⚠️ Partial fill detected: {len(filled_orders)}/{len(orders)} filled"
            )
            
            if rollback_on_partial and filled_orders:
                # 🚨 Emergency rollback
                self.logger.error("Performing emergency rollback of filled orders...")
                rollback_cost = await self._rollback_filled_orders(filled_orders)
                
                return AtomicExecutionResult(
                    success=False,
                    all_filled=False,
                    filled_orders=[],
                    partial_fills=partial_fills,
                    total_slippage_usd=Decimal('0'),
                    execution_time_ms=int((time.time() - start_time) * 1000),
                    error_message=f"Partial fill: {len(filled_orders)}/{len(orders)}, rolled back",
                    rollback_performed=True,
                    rollback_cost_usd=rollback_cost
                )
            else:
                # Accept partial fill (caller decides what to do)
                total_slippage = sum(
                    Decimal(str(r.get('slippage_usd', 0))) for r in filled_orders
                )
                
                return AtomicExecutionResult(
                    success=False,
                    all_filled=False,
                    filled_orders=filled_orders,
                    partial_fills=partial_fills,
                    total_slippage_usd=total_slippage,
                    execution_time_ms=int((time.time() - start_time) * 1000),
                    error_message=f"Partial fill: {len(filled_orders)}/{len(orders)}, no rollback"
                )
        
        except Exception as e:
            self.logger.error(f"Atomic execution failed: {e}", exc_info=True)
            
            # Try to rollback any successful fills
            if filled_orders and rollback_on_partial:
                rollback_cost = await self._rollback_filled_orders(filled_orders)
            else:
                rollback_cost = None
            
            return AtomicExecutionResult(
                success=False,
                all_filled=False,
                filled_orders=filled_orders if not rollback_on_partial else [],
                partial_fills=partial_fills,
                total_slippage_usd=Decimal('0'),
                execution_time_ms=int((time.time() - start_time) * 1000),
                error_message=str(e),
                rollback_performed=bool(filled_orders and rollback_on_partial),
                rollback_cost_usd=rollback_cost
            )
    
    async def _run_preflight_checks(
        self,
        orders: List[OrderSpec]
    ) -> tuple[bool, Optional[str]]:
        """
        Run pre-flight checks on all orders.
        
        Checks:
        1. Account balance sufficiency (CRITICAL - prevents partial fills due to insufficient margin)
        2. Liquidity availability (prevents high slippage)
        
        Returns:
            (all_checks_passed, error_message)
        """
        try:
            # Import here to avoid circular dependency
            from strategies.execution.core.liquidity_analyzer import LiquidityAnalyzer
            
            # ========================================================================
            # CHECK 0: Leverage Limit Validation (NEW - CRITICAL FOR DELTA NEUTRAL)
            # ========================================================================
            self.logger.info("🔍 Checking leverage limits across exchanges...")
            
            # Import leverage validator
            from strategies.execution.core.leverage_validator import LeverageValidator
            
            leverage_validator = LeverageValidator()
            
            # Group orders by symbol (for delta-neutral we need same size on both sides)
            symbols_to_check: Dict[str, List[OrderSpec]] = {}
            for order_spec in orders:
                symbol = order_spec.symbol
                if symbol not in symbols_to_check:
                    symbols_to_check[symbol] = []
                symbols_to_check[symbol].append(order_spec)
            
            # For each symbol, verify all exchanges can support the requested size
            adjusted_orders = []
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
                
                # If size needs adjustment, update all orders for this symbol
                if max_size < requested_size:
                    error_msg = (
                        f"Position size too large for {symbol}: "
                        f"Requested ${requested_size:.2f}, "
                        f"maximum supported: ${max_size:.2f} "
                        f"(limited by {limiting_exchange})"
                    )
                    self.logger.warning(f"⚠️  {error_msg}")
                    
                    # For atomic execution, we can't have mismatched sizes
                    # Return error so strategy can decide (reduce size or skip)
                    return False, error_msg
            
            self.logger.info("✅ Leverage limits validated for all exchanges")
            
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
            
            # ========================================================================
            # CHECK 2: Liquidity Analysis
            # ========================================================================
            self.logger.info("Running liquidity checks...")
            
            # Use shared price_provider if available (enables caching)
            analyzer = LiquidityAnalyzer(price_provider=self.price_provider)
            
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
    
    async def _place_single_order(self, spec: OrderSpec) -> Dict:
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
                limit_price_offset_pct=spec.limit_price_offset_pct or Decimal("0.01")
            )
            
            # Convert ExecutionResult to dict
            return {
                'success': result.success,
                'filled': result.filled,
                'fill_price': result.fill_price,
                'filled_quantity': result.filled_quantity,
                'slippage_usd': result.slippage_usd,
                'execution_mode_used': result.execution_mode_used,
                'order_id': result.order_id,
                'exchange_client': spec.exchange_client,
                'symbol': spec.symbol,
                'side': spec.side
            }
        
        except Exception as e:
            self.logger.error(f"Single order placement failed: {e}", exc_info=True)
            raise
    
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
            if order.get('order_id'):
                try:
                    order_info = await order['exchange_client'].get_order_info(order['order_id'])
                    if order_info and order_info.filled_size > 0:
                        actual_fills.append({
                            'exchange_client': order['exchange_client'],
                            'symbol': order['symbol'],
                            'side': order['side'],
                            'filled_quantity': order_info.filled_size,  # ✅ ACTUAL filled amount
                            'fill_price': order['fill_price']  # Use original price for cost calc
                        })
                        
                        # Warn if fill amount changed
                        original_qty = order['filled_quantity']
                        actual_qty = order_info.filled_size
                        if abs(actual_qty - original_qty) > Decimal('0.0001'):
                            self.logger.warning(
                                f"⚠️ Fill amount changed for {order['symbol']}: "
                                f"{original_qty} → {actual_qty} "
                                f"(Δ={actual_qty - original_qty})"
                            )
                except Exception as e:
                    self.logger.error(
                        f"Failed to get actual fill for {order.get('order_id')}: {e}"
                    )
                    # Fallback to original quantity (pessimistic approach)
                    actual_fills.append({
                        'exchange_client': order['exchange_client'],
                        'symbol': order['symbol'],
                        'side': order['side'],
                        'filled_quantity': order['filled_quantity'],
                        'fill_price': order['fill_price']
                    })
            else:
                # No order ID (shouldn't happen, but handle gracefully)
                actual_fills.append({
                    'exchange_client': order['exchange_client'],
                    'symbol': order['symbol'],
                    'side': order['side'],
                    'filled_quantity': order['filled_quantity'],
                    'fill_price': order['fill_price']
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
                
                # Place market close order
                close_task = fill['exchange_client'].place_market_order(
                    contract_id=fill['symbol'],
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

