"""Simple limit order execution strategy (single attempt, no retries)."""

from decimal import Decimal
from typing import Optional

from exchange_clients import BaseExchangeClient

from ..execution_types import ExecutionResult
from ..order_execution.limit_order_executor import LimitOrderExecutor
from ..price_provider import PriceProvider
from .base import ExecutionStrategy


class SimpleLimitExecutionStrategy(ExecutionStrategy):
    """Simple limit order execution strategy (wraps LimitOrderExecutor)."""
    
    def __init__(
        self,
        price_provider=None,
        limit_executor: Optional[LimitOrderExecutor] = None,
        use_websocket_events: bool = True
    ):
        """
        Initialize simple limit execution strategy.
        
        Args:
            price_provider: Optional PriceProvider for BBO price retrieval
            limit_executor: Optional LimitOrderExecutor instance
            use_websocket_events: If True, use event-based order tracking (faster)
        """
        # Initialize base class with websocket support
        super().__init__(use_websocket_events=use_websocket_events)
        
        self.price_provider = price_provider or PriceProvider()
        self.limit_executor = limit_executor or LimitOrderExecutor(price_provider=self.price_provider)
    
    async def execute(
        self,
        exchange_client: BaseExchangeClient,
        symbol: str,
        side: str,
        quantity: Optional[Decimal] = None,
        size_usd: Optional[Decimal] = None,
        reduce_only: bool = False,
        timeout_seconds: float = 40.0,
        price_offset_pct: Decimal = Decimal("0.0001"),
        cancel_event=None,
        **kwargs
    ) -> ExecutionResult:
        """
        Execute order using simple limit order (single attempt, no retries).
        
        Args:
            exchange_client: Exchange client instance
            symbol: Trading pair (e.g., "BTC-PERP")
            side: "buy" or "sell"
            quantity: Order quantity
            size_usd: Order size in USD (alternative to quantity)
            reduce_only: If True, order can only reduce existing positions
            timeout_seconds: Timeout for waiting for fill
            price_offset_pct: Price improvement offset (e.g., 0.0001 for 1bp)
            cancel_event: Optional asyncio.Event to request cancellation
            **kwargs: Additional parameters (ignored)
            
        Returns:
            ExecutionResult with execution details
        """
        return await self.limit_executor.execute(
            exchange_client=exchange_client,
            symbol=symbol,
            side=side,
            size_usd=size_usd,
            quantity=quantity,
            timeout_seconds=timeout_seconds,
            price_offset_pct=price_offset_pct,
            cancel_event=cancel_event,
            reduce_only=reduce_only,
        )

