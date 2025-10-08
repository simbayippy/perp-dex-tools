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
from helpers.logger import TradingLogger


# ============================================================================
# Enums
# ============================================================================

class RunnableStatus(Enum):
    """
    Strategy lifecycle states.
    
    Pattern from Hummingbot ExecutorBase.
    
    Transitions:
    NOT_STARTED → RUNNING → SHUTTING_DOWN → TERMINATED
    """
    NOT_STARTED = 1
    RUNNING = 2
    SHUTTING_DOWN = 3
    TERMINATED = 4


class StrategyAction(Enum):
    """Strategy action types."""
    NONE = "none"
    PLACE_ORDER = "place_order"
    CLOSE_POSITION = "close_position"
    REBALANCE = "rebalance"
    WAIT = "wait"


@dataclass
class OrderParams:
    """Parameters for order placement."""
    side: str  # 'buy' or 'sell'
    quantity: Decimal
    price: Optional[Decimal] = None  # None for market orders
    order_type: str = "limit"  # 'limit', 'market'
    reduce_only: bool = False
    time_in_force: str = "GTC"  # 'GTC', 'IOC', 'FOK'
    
    # Multi-exchange support
    exchange: Optional[str] = None
    contract_id: Optional[str] = None
    
    # Strategy-specific metadata
    metadata: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


@dataclass
class StrategyResult:
    """Result of strategy execution."""
    action: StrategyAction
    orders: List[OrderParams] = None
    message: str = ""
    wait_time: float = 0  # Seconds to wait before next check
    
    def __post_init__(self):
        if self.orders is None:
            self.orders = []


@dataclass
class MarketData:
    """Market data container for strategy decisions."""
    ticker: str
    best_bid: Decimal
    best_ask: Decimal
    mid_price: Decimal
    timestamp: float
    
    # Additional data
    volume_24h: Optional[Decimal] = None
    funding_rate: Optional[Decimal] = None
    open_interest: Optional[Decimal] = None
    
    # Multi-exchange data
    exchange_data: Dict[str, Dict[str, Any]] = None
    
    def __post_init__(self):
        if self.exchange_data is None:
            self.exchange_data = {}
        if self.mid_price == 0:
            self.mid_price = (self.best_bid + self.best_ask) / 2


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
        
        # Initialize logger
        self.logger = TradingLogger(
            exchange=getattr(config, 'exchange', 'unknown'),
            ticker=getattr(config, 'ticker', 'unknown'),
            log_to_console=False
        )
        
        # Strategy state
        self.is_initialized = False
        self.last_action_time = 0
        self.strategy_state = {}
        
        # Event-driven pattern from Hummingbot
        self.status = RunnableStatus.NOT_STARTED
        self._event_listeners: Dict[str, Callable] = {}
    
    async def initialize(self):
        """Initialize strategy-specific components."""
        if not self.is_initialized:
            await self._initialize_strategy()
            self.is_initialized = True
            self.logger.log(f"Strategy '{self.get_strategy_name()}' initialized", "INFO")
    
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
        if self.status != RunnableStatus.NOT_STARTED:
            self.logger.log(
                f"Cannot start strategy in status {self.status}",
                "WARNING"
            )
            return
        
        # Register event listeners (child can override)
        self.register_events()
        
        # Start running
        self.status = RunnableStatus.RUNNING
        
        self.logger.log(f"Strategy '{self.get_strategy_name()}' started", "INFO")
    
    def stop(self):
        """
        Stop the strategy gracefully.
        
        Pattern from Hummingbot:
        1. Set status to SHUTTING_DOWN
        2. Unregister event listeners
        3. Set status to TERMINATED
        """
        if self.status == RunnableStatus.RUNNING:
            self.status = RunnableStatus.SHUTTING_DOWN
            self.logger.log(f"Strategy '{self.get_strategy_name()}' shutting down...", "INFO")
        
        # Cleanup event listeners
        self.unregister_events()
        
        self.status = RunnableStatus.TERMINATED
        self.logger.log(f"Strategy '{self.get_strategy_name()}' terminated", "INFO")
    
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
    async def should_execute(self, market_data: MarketData) -> bool:
        """Determine if strategy should execute based on market conditions."""
        pass
    
    @abstractmethod
    async def execute_strategy(self, market_data: MarketData) -> StrategyResult:
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
            self.logger.log(f"Missing required parameters: {missing_params}", "ERROR")
            return False
        
        return True
    
    def get_parameter(self, param_name: str, default_value: Any = None) -> Any:
        """Get strategy parameter with optional default."""
        strategy_params = getattr(self.config, 'strategy_params', {})
        return strategy_params.get(param_name, default_value)
    
    async def cleanup(self):
        """Cleanup strategy resources."""
        self.logger.log(f"Strategy '{self.get_strategy_name()}' cleanup completed", "INFO")
    
    # Helper methods for common operations
    
    async def get_current_position(self) -> Decimal:
        """Get current position size."""
        try:
            return await self.exchange_client.get_account_positions()
        except Exception as e:
            self.logger.log(f"Error getting position: {e}", "ERROR")
            return Decimal('0')
    
    def update_strategy_state(self, key: str, value: Any):
        """Update strategy state."""
        self.strategy_state[key] = value
    
    def get_strategy_state(self, key: str, default_value: Any = None) -> Any:
        """Get strategy state value."""
        return self.strategy_state.get(key, default_value)



