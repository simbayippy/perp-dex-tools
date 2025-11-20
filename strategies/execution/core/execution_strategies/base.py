"""Base execution strategy interface."""

from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Optional

from exchange_clients import BaseExchangeClient

from ..execution_types import ExecutionResult


class ExecutionStrategy(ABC):
    """Base class for execution strategies."""
    
    @abstractmethod
    async def execute(
        self,
        exchange_client: BaseExchangeClient,
        symbol: str,
        side: str,
        quantity: Optional[Decimal] = None,
        size_usd: Optional[Decimal] = None,
        reduce_only: bool = False,
        **kwargs
    ) -> ExecutionResult:
        """
        Execute order using this strategy.
        
        Args:
            exchange_client: Exchange client instance
            symbol: Trading pair (e.g., "BTC-PERP")
            side: "buy" or "sell"
            quantity: Order quantity
            size_usd: Order size in USD (alternative to quantity)
            reduce_only: If True, order can only reduce existing positions
            **kwargs: Strategy-specific parameters
            
        Returns:
            ExecutionResult with execution details
        """
        pass

