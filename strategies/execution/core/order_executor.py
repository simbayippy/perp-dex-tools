"""
Order Executor - Smart order placement with tiered execution.

⭐ Inspired by Hummingbot's PositionExecutor ⭐

Provides intelligent order execution with multiple modes:
- limit_only: Place limit order, wait for fill
- limit_with_fallback: Try limit first, fallback to market if timeout
- market_only: Immediate market order
- adaptive: Choose mode based on liquidity analysis

Key features:
- Automatic fallback from limit to market
- Timeout handling
- Slippage tracking
- Execution quality metrics
"""

from typing import Any, Dict, Optional
from decimal import Decimal
from enum import Enum
from dataclasses import dataclass
import time
import asyncio
import logging

logger = logging.getLogger(__name__)


class ExecutionMode(Enum):
    """
    Execution modes for order placement.
    
    ⭐ From Hummingbot's TripleBarrierConfig pattern ⭐
    """
    LIMIT_ONLY = "limit_only"
    LIMIT_WITH_FALLBACK = "limit_with_fallback"
    MARKET_ONLY = "market_only"
    ADAPTIVE = "adaptive"


@dataclass
class ExecutionResult:
    """
    Result of order execution.
    
    Contains all metrics needed for quality analysis.
    """
    success: bool
    filled: bool
    
    # Price & quantity
    fill_price: Optional[Decimal] = None
    filled_quantity: Optional[Decimal] = None
    
    # Quality metrics
    expected_price: Optional[Decimal] = None
    slippage_usd: Decimal = Decimal('0')
    slippage_pct: Decimal = Decimal('0')
    
    # Execution details
    execution_mode_used: str = ""
    execution_time_ms: int = 0
    
    # Error handling
    error_message: Optional[str] = None
    order_id: Optional[str] = None


class OrderExecutor:
    """
    Intelligent order executor with tiered execution strategy.
    
    ⭐ Inspired by Hummingbot's PositionExecutor ⭐
    
    Key Patterns:
    1. Limit orders for better pricing (maker orders)
    2. Market fallback if limit times out
    3. Configurable timeout per order
    4. Automatic price selection (mid-market with buffer)
    
    Example:
        executor = OrderExecutor()
        
        # Try limit, fallback to market after 30s
        result = await executor.execute_order(
            exchange_client=client,
            symbol="BTC-PERP",
            side="buy",
            size_usd=Decimal("1000"),
            mode=ExecutionMode.LIMIT_WITH_FALLBACK,
            timeout_seconds=30.0
        )
        
        if result.filled:
            print(f"Filled at ${result.fill_price}, slippage: {result.slippage_pct}%")
    """
    
    def __init__(self, default_timeout: float = 30.0):
        """
        Initialize order executor.
        
        Args:
            default_timeout: Default timeout for limit orders (seconds)
        """
        self.default_timeout = default_timeout
        self.logger = logging.getLogger(__name__)
    
    async def execute_order(
        self,
        exchange_client: Any,
        symbol: str,
        side: str,
        size_usd: Decimal,
        mode: ExecutionMode = ExecutionMode.LIMIT_WITH_FALLBACK,
        timeout_seconds: Optional[float] = None,
        limit_price_offset_pct: Decimal = Decimal("0.01")  # 0.01% = 1 basis point
    ) -> ExecutionResult:
        """
        Execute order with intelligent mode selection.
        
        Args:
            exchange_client: Exchange client instance
            symbol: Trading pair (e.g., "BTC-PERP")
            side: "buy" or "sell"
            size_usd: Order size in USD
            mode: Execution mode
            timeout_seconds: Timeout for limit orders (uses default if None)
            limit_price_offset_pct: Price improvement for limit orders (0.01% = better than market)
        
        Returns:
            ExecutionResult with all execution details
        """
        start_time = time.time()
        timeout = timeout_seconds or self.default_timeout
        
        self.logger.info(
            f"Executing {side} {symbol} for ${size_usd} in mode {mode.value}"
        )
        
        try:
            if mode == ExecutionMode.MARKET_ONLY:
                result = await self._execute_market(
                    exchange_client, symbol, side, size_usd
                )
            
            elif mode == ExecutionMode.LIMIT_ONLY:
                result = await self._execute_limit(
                    exchange_client, symbol, side, size_usd, timeout, limit_price_offset_pct
                )
            
            elif mode == ExecutionMode.LIMIT_WITH_FALLBACK:
                # Try limit first
                result = await self._execute_limit(
                    exchange_client, symbol, side, size_usd, timeout, limit_price_offset_pct
                )
                
                if not result.filled:
                    # Fallback to market
                    self.logger.info(
                        f"Limit order timeout for {symbol}, falling back to market"
                    )
                    result = await self._execute_market(
                        exchange_client, symbol, side, size_usd
                    )
                    result.execution_mode_used = "market_fallback"
            
            elif mode == ExecutionMode.ADAPTIVE:
                # Use liquidity analyzer to decide (will implement later)
                # For now, default to limit_with_fallback
                result = await self.execute_order(
                    exchange_client, symbol, side, size_usd,
                    ExecutionMode.LIMIT_WITH_FALLBACK, timeout, limit_price_offset_pct
                )
            
            else:
                raise ValueError(f"Unknown execution mode: {mode}")
            
            # Add execution time
            result.execution_time_ms = int((time.time() - start_time) * 1000)
            
            return result
        
        except Exception as e:
            self.logger.error(f"Order execution failed: {e}", exc_info=True)
            return ExecutionResult(
                success=False,
                filled=False,
                error_message=str(e),
                execution_time_ms=int((time.time() - start_time) * 1000)
            )
    
    async def _execute_limit(
        self,
        exchange_client: Any,
        symbol: str,
        side: str,
        size_usd: Decimal,
        timeout_seconds: float,
        price_offset_pct: Decimal
    ) -> ExecutionResult:
        """
        Place limit order at favorable price, wait for fill.
        
        Price selection (maker order):
        - Buy: best_ask - offset (better than market for us)
        - Sell: best_bid + offset (better than market for us)
        
        ⭐ Pattern from Hummingbot's PositionExecutor ⭐
        """
        try:
            # Get current prices
            best_bid, best_ask = await self._fetch_bbo_prices(exchange_client, symbol)
            mid_price = (best_bid + best_ask) / 2
            
            # Calculate limit price (maker order with small improvement)
            if side == "buy":
                # Buy at ask - offset (better than market taker)
                limit_price = best_ask * (Decimal('1') - price_offset_pct)
            else:
                # Sell at bid + offset (better than market taker)
                limit_price = best_bid * (Decimal('1') + price_offset_pct)
            
            # Convert USD size to quantity
            quantity = size_usd / limit_price
            
            self.logger.info(
                f"Placing limit {side} {symbol}: {quantity} @ ${limit_price} "
                f"(mid: ${mid_price}, offset: {price_offset_pct}%)"
            )
            
            # Place limit order
            order_result = await exchange_client.place_limit_order(
                contract_id=symbol,
                quantity=float(quantity),
                price=float(limit_price),
                side=side
            )
            
            if not order_result.success:
                return ExecutionResult(
                    success=False,
                    filled=False,
                    error_message=f"Limit order placement failed: {order_result.error_message}",
                    execution_mode_used="limit_failed"
                )
            
            order_id = order_result.order_id
            
            # Wait for fill (with timeout)
            start_wait = time.time()
            
            while time.time() - start_wait < timeout_seconds:
                # Check order status
                order_info = await exchange_client.get_order_info(order_id)
                
                if order_info and order_info.status == "FILLED":
                    fill_price = Decimal(str(order_info.price))
                    filled_qty = Decimal(str(order_info.filled_size))
                    
                    self.logger.info(
                        f"Limit order filled: {filled_qty} @ ${fill_price}"
                    )
                    
                    # Calculate slippage (should be near zero for maker orders)
                    slippage_usd = abs(fill_price - limit_price) * filled_qty
                    slippage_pct = abs(fill_price - limit_price) / limit_price if limit_price > 0 else Decimal('0')
                    
                    return ExecutionResult(
                        success=True,
                        filled=True,
                        fill_price=fill_price,
                        filled_quantity=filled_qty,
                        expected_price=limit_price,
                        slippage_usd=slippage_usd,
                        slippage_pct=slippage_pct,
                        execution_mode_used="limit",
                        order_id=order_id
                    )
                
                # Check more frequently near the end
                wait_interval = 0.5 if (timeout_seconds - (time.time() - start_wait)) > 5 else 0.2
                await asyncio.sleep(wait_interval)
            
            # Timeout - cancel order
            self.logger.warning(
                f"Limit order timeout after {timeout_seconds}s, canceling {order_id}"
            )
            
            try:
                await exchange_client.cancel_order(order_id)
            except Exception as e:
                self.logger.error(f"Failed to cancel order {order_id}: {e}")
            
            return ExecutionResult(
                success=False,
                filled=False,
                error_message=f"Limit order timeout after {timeout_seconds}s",
                execution_mode_used="limit_timeout",
                order_id=order_id
            )
        
        except Exception as e:
            self.logger.error(f"Limit order execution failed: {e}", exc_info=True)
            return ExecutionResult(
                success=False,
                filled=False,
                error_message=f"Limit execution error: {str(e)}",
                execution_mode_used="limit_error"
            )
    
    async def _execute_market(
        self,
        exchange_client: Any,
        symbol: str,
        side: str,
        size_usd: Decimal
    ) -> ExecutionResult:
        """
        Execute market order immediately.
        
        ⭐ Pattern from Hummingbot's market order execution ⭐
        """
        try:
            # Get current price for quantity calculation & slippage tracking
            best_bid, best_ask = await self._fetch_bbo_prices(exchange_client, symbol)
            mid_price = (best_bid + best_ask) / 2
            expected_price = best_ask if side == "buy" else best_bid
            
            # Calculate quantity
            quantity = size_usd / expected_price
            
            self.logger.info(
                f"Placing market {side} {symbol}: {quantity} @ ~${expected_price}"
            )
            
            # Place market order
            result = await exchange_client.place_market_order(
                contract_id=symbol,
                quantity=float(quantity),
                side=side
            )
            
            if not result.success:
                return ExecutionResult(
                    success=False,
                    filled=False,
                    error_message=f"Market order failed: {result.error_message}",
                    execution_mode_used="market_failed"
                )
            
            # Get actual fill price
            fill_price = Decimal(str(result.price)) if result.price else expected_price
            filled_qty = quantity  # Assume full fill for market orders
            
            # Calculate slippage
            slippage_usd = abs(fill_price - expected_price) * filled_qty
            slippage_pct = abs(fill_price - expected_price) / expected_price if expected_price > 0 else Decimal('0')
            
            self.logger.info(
                f"Market order filled: {filled_qty} @ ${fill_price} "
                f"(slippage: ${slippage_usd:.2f} / {slippage_pct*100:.3f}%)"
            )
            
            return ExecutionResult(
                success=True,
                filled=True,
                fill_price=fill_price,
                filled_quantity=filled_qty,
                expected_price=expected_price,
                slippage_usd=slippage_usd,
                slippage_pct=slippage_pct,
                execution_mode_used="market",
                order_id=result.order_id if hasattr(result, 'order_id') else None
            )
        
        except Exception as e:
            self.logger.error(f"Market order execution failed: {e}", exc_info=True)
            return ExecutionResult(
                success=False,
                filled=False,
                error_message=f"Market execution error: {str(e)}",
                execution_mode_used="market_error"
            )
    
    async def _fetch_bbo_prices(
        self,
        exchange_client: Any,
        symbol: str
    ) -> tuple[Decimal, Decimal]:
        """
        Fetch best bid/offer prices.
        
        Returns:
            (best_bid, best_ask) as Decimals
        """
        try:
            # Try dedicated BBO method if available
            if hasattr(exchange_client, 'fetch_bbo_prices'):
                bid, ask = await exchange_client.fetch_bbo_prices(symbol)
                return Decimal(str(bid)), Decimal(str(ask))
            
            # Fallback: Get from order book
            if hasattr(exchange_client, 'get_order_book_depth'):
                book = await exchange_client.get_order_book_depth(symbol, levels=1)
                best_bid = Decimal(str(book['bids'][0]['price']))
                best_ask = Decimal(str(book['asks'][0]['price']))
                return best_bid, best_ask
            
            # Last resort: Use mid-price if available
            raise NotImplementedError(
                "Exchange client must implement fetch_bbo_prices() or get_order_book_depth()"
            )
        
        except Exception as e:
            self.logger.error(f"Failed to fetch BBO prices: {e}")
            raise

