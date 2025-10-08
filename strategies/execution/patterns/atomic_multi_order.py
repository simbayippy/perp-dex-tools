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
    
    def __init__(self):
        """Initialize atomic multi-order executor."""
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
            
            # Step 2: Place all orders simultaneously
            self.logger.info("Placing all orders simultaneously...")
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
        
        Returns:
            (all_checks_passed, error_message)
        """
        try:
            # Import here to avoid circular dependency
            from strategies.execution.core.liquidity_analyzer import LiquidityAnalyzer
            
            analyzer = LiquidityAnalyzer()
            
            for i, order_spec in enumerate(orders):
                # Check liquidity
                report = await analyzer.check_execution_feasibility(
                    exchange_client=order_spec.exchange_client,
                    symbol=order_spec.symbol,
                    side=order_spec.side,
                    size_usd=order_spec.size_usd
                )
                
                if not analyzer.is_execution_acceptable(report):
                    error_msg = (
                        f"Order {i} ({order_spec.side} {order_spec.symbol}) "
                        f"failed pre-flight: {report.recommendation}"
                    )
                    self.logger.warning(error_msg)
                    return False, error_msg
            
            self.logger.info("✅ All pre-flight checks passed")
            return True, None
        
        except Exception as e:
            self.logger.error(f"Pre-flight check error: {e}")
            # Don't fail execution on pre-flight check errors
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
            
            executor = OrderExecutor()
            
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
        
        Args:
            filled_orders: List of successfully filled orders
        
        Returns:
            Total rollback cost in USD
        """
        self.logger.warning(
            f"🚨 EMERGENCY ROLLBACK: Closing {len(filled_orders)} filled orders"
        )
        
        rollback_tasks = []
        total_rollback_cost = Decimal('0')
        
        for order in filled_orders:
            try:
                # Market close the opposite side
                close_side = "sell" if order['side'] == "buy" else "buy"
                
                self.logger.info(
                    f"Rollback: {close_side} {order['symbol']} "
                    f"{order['filled_quantity']} @ market"
                )
                
                # Place market close order
                close_task = order['exchange_client'].place_market_order(
                    contract_id=order['symbol'],
                    quantity=float(order['filled_quantity']),
                    side=close_side
                )
                rollback_tasks.append(close_task)
            
            except Exception as e:
                self.logger.error(
                    f"Failed to create rollback order for {order['symbol']}: {e}"
                )
        
        # Execute all rollback orders
        if rollback_tasks:
            results = await asyncio.gather(*rollback_tasks, return_exceptions=True)
            
            # Calculate rollback cost (slippage from entry to exit)
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    self.logger.error(f"Rollback order {i} failed: {result}")
                elif hasattr(result, 'price'):
                    # Calculate cost difference
                    order = filled_orders[i]
                    entry_price = order['fill_price']
                    exit_price = Decimal(str(result.price))
                    quantity = order['filled_quantity']
                    
                    rollback_cost = abs(exit_price - entry_price) * quantity
                    total_rollback_cost += rollback_cost
                    
                    self.logger.warning(
                        f"Rollback cost for {order['symbol']}: ${rollback_cost:.2f}"
                    )
        
        self.logger.warning(
            f"Total rollback cost: ${total_rollback_cost:.2f}"
        )
        
        return total_rollback_cost

