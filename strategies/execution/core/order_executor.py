"""
Order Executor - Smart order placement with tiered execution.

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

from typing import Dict, Optional
from decimal import Decimal
from enum import Enum
from dataclasses import dataclass
import time
from helpers.unified_logger import get_core_logger
from exchange_clients import BaseExchangeClient

from .order_execution.limit_order_executor import LimitOrderExecutor
from .order_execution.market_order_executor import MarketOrderExecutor
from .order_execution.order_confirmation import OrderConfirmationWaiter
from .price_provider import PriceProvider

logger = get_core_logger("order_executor")


class ExecutionMode(Enum):
    """
    Execution modes for order placement.
    
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
    
    # Retry handling
    retryable: bool = False  # True if order failure is retryable (e.g., post-only violation)


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
                    timeout_seconds=40.0
        )
        
        if result.filled:
            print(f"Filled at ${result.fill_price}, slippage: {result.slippage_pct}%")
    """
    
    DEFAULT_LIMIT_PRICE_OFFSET_PCT = Decimal("0.0001")  # 1 basis point

    def __init__(
        self,
        default_timeout: float = 40.0,
        price_provider = None,  # Optional PriceProvider for shared BBO lookups
        default_limit_price_offset_pct: Decimal = DEFAULT_LIMIT_PRICE_OFFSET_PCT
    ):
        """
        Initialize order executor.
        
        Args:
            default_timeout: Default timeout for limit orders (seconds)
            price_provider: Optional PriceProvider for retrieving shared BBO data
            default_limit_price_offset_pct: Default maker improvement for limit orders
        """
        self.default_timeout = default_timeout
        self.price_provider = price_provider
        self.default_limit_price_offset_pct = default_limit_price_offset_pct
        self.logger = get_core_logger("order_executor")
        
        # Initialize extracted executors
        self.confirmation_waiter = OrderConfirmationWaiter()
        self.limit_executor = LimitOrderExecutor(price_provider=price_provider)
        self.market_executor = MarketOrderExecutor(
            price_provider=price_provider,
            limit_executor=self.limit_executor,
            confirmation_waiter=self.confirmation_waiter
        )
    
    async def execute_order(
        self,
        exchange_client: BaseExchangeClient,
        symbol: str,
        side: str,
        size_usd: Optional[Decimal] = None,
        quantity: Optional[Decimal] = None,
        mode: ExecutionMode = ExecutionMode.LIMIT_WITH_FALLBACK,
        timeout_seconds: Optional[float] = None,
        limit_price_offset_pct: Optional[Decimal] = None,
        cancel_event: Optional[asyncio.Event] = None,
        reduce_only: bool = False
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
            limit_price_offset_pct: Price improvement for limit orders (None = executor default)
            cancel_event: Optional asyncio.Event to request cancellation (only respected for limit orders)
        
        Returns:
            ExecutionResult with all execution details
        """
        if size_usd is None and quantity is None:
            raise ValueError("OrderExecutor.execute_order requires size_usd or quantity")

        start_time = time.time()
        timeout = timeout_seconds or self.default_timeout
        
        # Get exchange name for better logging
        try:
            exchange_name = exchange_client.get_exchange_name()
        except Exception:
            exchange_name = "unknown"
        
        # Choose emoji based on side
        emoji = "🟢" if side == "buy" else "🔴"
        
        size_components = []
        if size_usd is not None:
            size_components.append(f"${size_usd}")
        if quantity is not None:
            size_components.append(f"qty={quantity}")
        size_descriptor = " ".join(size_components)

        self.logger.info(
            f"{emoji} [{exchange_name.upper()}] Executing {side} {symbol} ({size_descriptor}) in mode {mode.value}"
        )
        
        try:
            offset_pct = (
                limit_price_offset_pct
                if limit_price_offset_pct is not None
                else self.default_limit_price_offset_pct
            )
            if not isinstance(offset_pct, Decimal):
                offset_pct = Decimal(str(offset_pct))

            if mode == ExecutionMode.MARKET_ONLY:
                result = await self.market_executor.execute(
                    exchange_client, symbol, side, size_usd, quantity, reduce_only
                )
            
            elif mode == ExecutionMode.LIMIT_ONLY:
                result = await self.limit_executor.execute(
                    exchange_client,
                    symbol,
                    side,
                    size_usd,
                    quantity,
                    timeout,
                    offset_pct,
                    cancel_event,
                    reduce_only,
                )
            
            elif mode == ExecutionMode.LIMIT_WITH_FALLBACK:
                # Try limit first
                result = await self.limit_executor.execute(
                    exchange_client,
                    symbol,
                    side,
                    size_usd,
                    quantity,
                    timeout,
                    offset_pct,
                    cancel_event,
                    reduce_only,
                )
                
                if not result.filled:
                    # Fallback to market
                    self.logger.info(
                        f"Limit order timeout for {symbol}, falling back to market"
                    )
                    result = await self.market_executor.execute(
                        exchange_client, symbol, side, size_usd, quantity, reduce_only
                    )
                    result.execution_mode_used = "market_fallback"
            
            elif mode == ExecutionMode.ADAPTIVE:
                # Use liquidity analyzer to decide (will implement later)
                # For now, default to limit_with_fallback
                result = await self.execute_order(
                    exchange_client=exchange_client,
                    symbol=symbol,
                    side=side,
                    size_usd=size_usd,
                    quantity=quantity,
                    mode=ExecutionMode.LIMIT_WITH_FALLBACK,
                    timeout_seconds=timeout,
                    limit_price_offset_pct=offset_pct,
                    cancel_event=cancel_event,
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
