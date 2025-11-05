"""
Base Strategy Interface
Defines the contract that all trading strategies must implement.

Enhanced with Hummingbot patterns:
- Event-driven pattern (from ExecutorBase)
- Status lifecycle management
- Event listener registration
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, Any, List, Optional, Tuple, Callable
from enum import Enum
import time
from helpers.unified_logger import get_strategy_logger


class BaseStrategy(ABC):
    """
    Base class for all trading strategies.
    
    Enhanced with Hummingbot patterns:
    - Status lifecycle management
    - Event listener registration
    - Start/stop control
    """
    
    def __init__(self, config, exchange_client=None):
        """
        Initialize strategy with configuration and exchange client.
        
        Args:
            config: Strategy configuration
            exchange_client: Single exchange client (optional, for multi-DEX use None)
        """
        self.config = config
        self.exchange_client = exchange_client
        
        # Initialize logger with context including account_name if available
        context = {
            'exchange': getattr(config, 'exchange', 'unknown'),
            'ticker': getattr(config, 'ticker', 'unknown')
        }
        # Add account_name to context if available (for multi-account support)
        account_name = getattr(config, 'account_name', None)
        if account_name:
            context['account'] = account_name
        
        self.logger = get_strategy_logger(
            self.get_strategy_name().lower().replace(' ', '_'),
            **context
        )
        
        # Strategy state
        self.is_initialized = False
        self.last_action_time = 0
        self.strategy_state = {}
        
        self._event_listeners: Dict[str, Callable] = {}
    
    async def initialize(self):
        """Initialize strategy-specific components."""
        if not self.is_initialized:
            await self._initialize_strategy()
            self.is_initialized = True
            self.logger.info(f"Strategy '{self.get_strategy_name()}' initialized")
    
    @abstractmethod
    async def _initialize_strategy(self):
        """Strategy-specific initialization logic."""
        pass
    
    # ========================================================================
    # Event-Driven Pattern (from Hummingbot ExecutorBase)
    # ========================================================================
    
    def start(self):
        """
        Start the strategy.
        
        Pattern from Hummingbot:
        1. Register event listeners
        2. Set status to RUNNING
        """
        
        # Register event listeners (child can override)
        self.register_events()
        
        # Start running
        self.logger.info(f"Strategy '{self.get_strategy_name()}' started")
    
    def stop(self):
        """
        Stop the strategy gracefully.
        
        Pattern from Hummingbot:
        1. Set status to SHUTTING_DOWN
        2. Unregister event listeners
        3. Set status to TERMINATED
        """
        # Cleanup event listeners
        self.unregister_events()
        
        self.logger.info(f"Strategy '{self.get_strategy_name()}' terminated")
    
    def register_events(self):
        """
        Register event listeners.
        
        Override in child strategy to add listeners:
        
        Example:
        --------
        def register_events(self):
            self.add_listener('order_filled', self._on_order_filled)
            self.add_listener('funding_payment', self._on_funding_payment)
        """
        pass
    
    def unregister_events(self):
        """Cleanup all event listeners"""
        self._event_listeners.clear()
    
    def add_listener(self, event_name: str, callback: Callable):
        """
        Add an event listener.
        
        Args:
            event_name: Event name (e.g., 'order_filled')
            callback: Function to call when event occurs
        """
        self._event_listeners[event_name] = callback
    
    def remove_listener(self, event_name: str):
        """
        Remove an event listener.
        
        Args:
            event_name: Event to stop listening to
        """
        if event_name in self._event_listeners:
            del self._event_listeners[event_name]
    
    @abstractmethod
    async def should_execute(self) -> bool:
        """Determine if strategy should execute based on market conditions."""
        pass
    
    @abstractmethod
    async def execute_strategy(self):
        """Execute the strategy and return the result."""
        pass
    
    @abstractmethod
    def get_strategy_name(self) -> str:
        """Get the strategy name."""
        pass
    
    @abstractmethod
    def get_required_parameters(self) -> List[str]:
        """Get list of required strategy parameters."""
        pass
    
    def validate_parameters(self) -> bool:
        """Validate that all required parameters are present."""
        required_params = self.get_required_parameters()
        strategy_params = getattr(self.config, 'strategy_params', {})
        
        missing_params = [param for param in required_params if param not in strategy_params]
        
        if missing_params:
            self.logger.error(f"Missing required parameters: {missing_params}")
            return False
        
        return True
    
    def get_parameter(self, param_name: str, default_value: Any = None) -> Any:
        """Get strategy parameter with optional default."""
        strategy_params = getattr(self.config, 'strategy_params', {})
        return strategy_params.get(param_name, default_value)
    
    async def cleanup(self):
        """Cleanup strategy resources."""
        self.logger.info(f"Strategy '{self.get_strategy_name()}' cleanup completed")
        # CRITICAL: Flush logs to ensure all buffered/enqueued logs are written
        if hasattr(self.logger, 'flush'):
            self.logger.flush()
    
    # Helper methods for common operations
    
    async def get_current_position(self) -> Decimal:
        """Get current position size."""
        try:
            return await self.exchange_client.get_account_positions()
        except Exception as e:
            self.logger.error(f"Error getting position: {e}")
            return Decimal('0')
    
    def update_strategy_state(self, key: str, value: Any):
        """Update strategy state."""
        self.strategy_state[key] = value
    
    def get_strategy_state(self, key: str, default_value: Any = None) -> Any:
        """Get strategy state value."""
        return self.strategy_state.get(key, default_value)
