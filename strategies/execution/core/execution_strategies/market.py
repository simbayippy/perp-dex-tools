"""Market order execution strategy."""

from decimal import Decimal
from typing import Optional

from exchange_clients import BaseExchangeClient

from ..execution_types import ExecutionResult
from ..order_execution.market_order_executor import MarketOrderExecutor
from ..order_execution.limit_order_executor import LimitOrderExecutor
from ..order_execution.order_confirmation import OrderConfirmationWaiter
from ..price_provider import PriceProvider
from .base import ExecutionStrategy


class MarketExecutionStrategy(ExecutionStrategy):
    """Market order execution strategy (wraps MarketOrderExecutor)."""
    
    def __init__(
        self,
        price_provider=None,
        market_executor: Optional[MarketOrderExecutor] = None,
        limit_executor: Optional[LimitOrderExecutor] = None,
        confirmation_waiter: Optional[OrderConfirmationWaiter] = None,
        use_websocket_events: bool = True
    ):
        """
        Initialize market execution strategy.
        
        Args:
            price_provider: Optional PriceProvider for BBO price retrieval
            market_executor: Optional MarketOrderExecutor instance
            limit_executor: Optional LimitOrderExecutor for slippage fallback
            confirmation_waiter: Optional OrderConfirmationWaiter instance
            use_websocket_events: If True, use event-based order tracking (faster)
        """
        # Initialize base class with websocket support
        super().__init__(use_websocket_events=use_websocket_events)
        
        self.price_provider = price_provider or PriceProvider()
        if market_executor is None:
            limit_exec = limit_executor or LimitOrderExecutor(price_provider=self.price_provider)
            conf_waiter = confirmation_waiter or OrderConfirmationWaiter()
            self.market_executor = MarketOrderExecutor(
                price_provider=self.price_provider,
                limit_executor=limit_exec,
                confirmation_waiter=conf_waiter
            )
        else:
            self.market_executor = market_executor
    
    async def execute(
        self,
        exchange_client: BaseExchangeClient,
        symbol: str,
        side: str,
        quantity: Optional[Decimal] = None,
        size_usd: Optional[Decimal] = None,
        reduce_only: bool = False,
        logger=None,
        **kwargs
    ) -> ExecutionResult:
        """
        Execute order using market order.
        
        Args:
            exchange_client: Exchange client instance
            symbol: Trading pair (e.g., "BTC-PERP")
            side: "buy" or "sell"
            quantity: Order quantity
            size_usd: Order size in USD (alternative to quantity)
            reduce_only: If True, order can only reduce existing positions
            logger: Optional logger instance (ignored, MarketOrderExecutor has its own logger)
            **kwargs: Additional parameters (ignored)
            
        Returns:
            ExecutionResult with execution details
        """
        return await self.market_executor.execute(
            exchange_client=exchange_client,
            symbol=symbol,
            side=side,
            size_usd=size_usd,
            quantity=quantity,
            reduce_only=reduce_only,
        )

