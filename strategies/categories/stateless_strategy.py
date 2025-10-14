"""
Stateless Strategy - For Simple Strategies

Base class for simple strategies that don't require complex position tracking.

Examples: Grid trading, TWAP, signal-based strategies

Key characteristics:
- Single exchange client
- No persistent position tracking across cycles
- Simple execution flow (template method pattern)
- Helper methods for common operations
"""

from strategies.base_strategy import BaseStrategy
from abc import abstractmethod
import time


class StatelessStrategy(BaseStrategy):
    """
    Base class for simple strategies.
    
    Provides:
    - Market data fetching
    - Template method for execution flow
    - Helper methods for common operations
    
    Child strategies implement:
    - should_execute(): Check conditions
    - execute_strategy(): Place orders
    
    Pattern: Template Method - defines execution flow, child fills in details
    """
    
    def __init__(self, config, exchange_client):
        """
        Initialize stateless strategy.
        
        Args:
            config: Strategy config
            exchange_client: Single exchange client (required)
        """
        super().__init__(config, exchange_client)
        
        if exchange_client is None:
            raise ValueError("StatelessStrategy requires exchange_client")
    
    # ========================================================================
    # Template Method Pattern
    # ========================================================================
    
    async def execute_cycle(self) :
        """
        Template method pattern - defines execution flow.
        
        Flow:
        1. Get market data
        2. Check if should execute
        3. Execute strategy if conditions met
        4. Return result
        
        Rationale: Standardizes simple strategy flow
        
        Note: This is called from your trading_bot.py main loop
        """
        try:
            # Get current market data
            market_data = await self.get_market_data()
            
            # Check conditions
            if await self.should_execute(market_data):
                return await self.execute_strategy(market_data)
            
            
        except Exception as e:
            self.logger.log(f"Error in execute_cycle: {e}", "ERROR")
    
    # ========================================================================
    # Abstract Methods (Child Implements)
    # ========================================================================
    
    @abstractmethod
    async def should_execute(self) -> bool:
        """
        Check if strategy should execute.
        
        Child implements logic like:
        - Is price at grid level?
        - Is signal triggered?
        - Is time window correct?
        
        Args:
            market_data: Current market data
            
        Returns:
            True if strategy should execute
        """
        pass
    
    @abstractmethod
    async def execute_strategy(self) :
        """
        Execute the strategy logic.
        
        Child implements:
        - Calculate order parameters
        
        Args:
            market_data: Current market data
            
        """
        pass
    
    # ========================================================================
    # Helper Methods (Inherited from BaseStrategy)
    # ========================================================================
    
    # These are already defined in BaseStrategy:
    # - get_market_data()
    # - get_current_position()
    # - get_parameter()
    # - update_strategy_state()
    # - get_strategy_state()
    
    # Add stateless-specific helpers here if needed

