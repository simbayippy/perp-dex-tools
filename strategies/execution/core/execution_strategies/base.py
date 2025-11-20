"""Base execution strategy interface."""

from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Optional

from exchange_clients import BaseExchangeClient

from ..execution_types import ExecutionResult
from ..execution_components.event_reconciler import EventBasedReconciler
from helpers.unified_logger import get_core_logger


class ExecutionStrategy(ABC):
    """
    Base class for execution strategies.
    
    Provides websocket-based order tracking infrastructure that all strategies can use
    for faster, more efficient order fill detection.
    """
    
    def __init__(self, use_websocket_events: bool = True):
        """
        Initialize base execution strategy.
        
        Args:
            use_websocket_events: If True, use event-based order tracking (faster).
                                  Falls back to polling if websockets not available.
        """
        self._use_websocket_events = use_websocket_events
        self._event_reconciler: Optional[EventBasedReconciler] = None
        self._websocket_registered = False
        self._original_callback = None
        self._logger = get_core_logger("execution_strategy")
        
        if use_websocket_events:
            self._event_reconciler = EventBasedReconciler(logger=self._logger)
    
    def _register_websocket_callback(self, exchange_client: BaseExchangeClient) -> bool:
        """
        Register websocket callback for event-based order tracking.
        
        Args:
            exchange_client: Exchange client to register callback for
            
        Returns:
            True if callback registered successfully, False if not supported
        """
        if not self._use_websocket_events or self._event_reconciler is None:
            return False
        
        if not hasattr(exchange_client, 'order_fill_callback'):
            return False
        
        if self._websocket_registered:
            return True  # Already registered
        
        # Store original callback to restore later
        self._original_callback = exchange_client.order_fill_callback
        # Set our callback router
        exchange_client.order_fill_callback = self._event_reconciler.get_callback_router()
        self._websocket_registered = True
        self._logger.debug(f"✅ Registered websocket callback for event-based order tracking")
        return True
    
    def _restore_websocket_callback(self, exchange_client: BaseExchangeClient) -> None:
        """
        Restore original websocket callback.
        
        Args:
            exchange_client: Exchange client to restore callback for
        """
        if self._websocket_registered and self._original_callback is not None:
            exchange_client.order_fill_callback = self._original_callback
            self._websocket_registered = False
            self._original_callback = None
            self._logger.debug(f"✅ Restored original websocket callback")
    
    def _can_use_websocket_events(self, exchange_client: BaseExchangeClient) -> bool:
        """
        Check if websocket events can be used for this exchange client.
        
        Args:
            exchange_client: Exchange client to check
            
        Returns:
            True if websocket events are available and enabled
        """
        return (
            self._use_websocket_events and
            self._event_reconciler is not None and
            hasattr(exchange_client, 'order_fill_callback')
        )
    
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

