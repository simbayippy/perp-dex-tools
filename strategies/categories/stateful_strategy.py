"""
Stateful Strategy - For Complex Strategies

Base class for complex strategies that track positions and state across multiple DEXes.

Examples: Funding arbitrage, market making, portfolio strategies

Key characteristics:
- Multiple exchange clients (multi-DEX)
- Position tracking across cycles
- State persistence
- Complex execution flow (child controls)
- Component composition (position manager, state manager)
"""

from strategies.base_strategy import BaseStrategy
from strategies.components import BasePositionManager, BaseStateManager, InMemoryPositionManager, InMemoryStateManager
from abc import abstractmethod
from typing import Dict, Optional, Any


class StatefulStrategy(BaseStrategy):
    """
    Base class for complex strategies.
    
    Provides via COMPOSITION (not inheritance):
    - Position manager (via factory method)
    - State manager (via factory method)
    - Optional helper methods
    
    DOES NOT provide:
    - Enforced execution flow (child has full control)
    - Hardcoded components (child can override factories)
    
    Rationale: Maximum flexibility for complex strategies
    
    Pattern: Composition + Factory Methods
    """
    
    def __init__(self, config, exchange_clients: Optional[Dict[str, Any]] = None):
        """
        Initialize stateful strategy.
        
        Args:
            config: Strategy config
            exchange_clients: Dict of exchange clients {dex_name: client}
                            (multi-DEX support)
        
        Note: exchange_client=None for multi-DEX strategies
        """
        # Note: exchange_client=None for multi-DEX strategies
        super().__init__(config, exchange_client=None)
        
        self.exchange_clients = exchange_clients or {}
        
        # COMPOSITION: Create components via factory methods
        # Child can override _create_* methods to customize
        self.position_manager = self._create_position_manager()
        self.state_manager = self._create_state_manager()
    
    # ========================================================================
    # Factory Methods (Override to Customize)
    # ========================================================================
    
    def _create_position_manager(self) -> BasePositionManager:
        """
        Factory method - override to customize position tracking.
        
        Default: Simple in-memory position manager
        Override: Database-backed, multi-DEX aware, etc.
        
        Rationale: Allows child to inject custom implementation
        
        Example override:
        ------------------
        def _create_position_manager(self):
            from .position_manager import FundingArbPositionManager
            return FundingArbPositionManager(
                database_url=self.config.database_url
            )
        """
        return InMemoryPositionManager()
    
    def _create_state_manager(self) -> BaseStateManager:
        """
        Factory method - override to customize state persistence.
        
        Default: In-memory state manager (for testing)
        Override: PostgreSQL, SQLite, etc.
        
        Rationale: Allows different storage backends
        
        Example override:
        ------------------
        def _create_state_manager(self):
            from strategies.components.state_manager import PostgreSQLStateManager
            return PostgreSQLStateManager(
                connection_string=self.config.database_url
            )
        """
        return InMemoryStateManager()
    
    # ========================================================================
    # Initialization
    # ========================================================================
    
    async def _initialize_strategy(self):
        """
        Default initialization for stateful strategies.
        
        Loads:
        - Persisted state
        - Open positions from storage
        
        Child can override to add custom initialization
        """
        # Load persisted state
        state = await self.state_manager.load_state(self.get_strategy_name())
        if state:
            self.strategy_state = state
        
        # Log open positions
        open_positions = await self.position_manager.get_open_positions()
        self.logger.log(
            f"Loaded {len(open_positions)} open positions",
            "INFO"
        )
    
    # ========================================================================
    # Execution (Child Has Full Control)
    # ========================================================================
    
    # Stateful strategies are expected to implement BaseStrategy.execute_strategy()
    # and orchestrate their own multi-phase loops there. No additional template
    # is enforced here – child classes retain full control over execution flow.
    
    # ========================================================================
    # Cleanup with State Saving
    # ========================================================================
    
    async def cleanup(self):
        """Cleanup with state saving"""
        # Save current state
        await self.state_manager.save_state(
            self.get_strategy_name(),
            self._get_state_data()
        )
        
        await super().cleanup()
    
    def _get_state_data(self) -> dict:
        """
        Get current state for persistence.
        
        Override to customize what gets saved.
        
        Returns:
            Dict of state data (must be JSON-serializable)
        """
        return {
            "last_run": str(self.last_action_time),
            "strategy_state": self.strategy_state
        }
    
    # ========================================================================
    # Optional Helper Methods
    # ========================================================================
    
    async def _monitor_positions_helper(self):
        """
        Optional helper for position monitoring.
        
        Child can use this or implement own logic.
        
        Rationale: Provides common logic but doesn't force its use
        """
        positions = await self.position_manager.get_open_positions()
        
        for position in positions:
            # Basic monitoring logic
            summary = await self.position_manager.get_position_summary(
                position.id
            )
            
            self.logger.log(
                f"Position {position.id}: {position.symbol} @ ${position.size_usd}",
                "INFO"
            )
