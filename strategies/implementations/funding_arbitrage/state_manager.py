"""
State Manager for Funding Arbitrage Strategy.

Handles persistence of strategy-level state:
- Configuration snapshots
- Performance metrics
- Operational flags (paused, circuit breaker, etc.)
- Runtime statistics

⭐ Uses PostgreSQL via funding_rate_service database ⭐

Note: Position tracking is handled by FundingArbPositionManager.
This manager focuses on strategy-level state only.
"""

from typing import Dict, Any, Optional
from decimal import Decimal
from datetime import datetime
import json
from helpers.unified_logger import get_core_logger

from strategies.components.base_components import BaseStateManager

# Import database connection from funding_rate_service (optional for testing)
try:
    from funding_rate_service.database.connection import database
    DATABASE_AVAILABLE = True
except ImportError:
    # For testing - database not available
    database = None
    DATABASE_AVAILABLE = False


class FundingArbStateManager(BaseStateManager):
    """
    State manager for funding arbitrage strategy.
    
    Responsibilities:
    - Persist strategy configuration
    - Store performance metrics
    - Track operational state
    - Enable strategy restart/recovery
    
    Database: Uses strategy_state table (JSONB column for flexibility)
    """
    
    def __init__(self, strategy_name: str = "funding_arbitrage"):
        """
        Initialize state manager.
        
        Args:
            strategy_name: Name of the strategy (used as DB key)
        """
        super().__init__()
        self.strategy_name = strategy_name
        self.logger = get_core_logger("funding_arb_state_manager")
        self._initialized = False
    
    def _check_database_available(self) -> bool:
        """Check if database is available for operations."""
        if not DATABASE_AVAILABLE:
            self.logger.warning("Database operation skipped - running in test mode")
            return False
        return True
    
    async def initialize(self) -> None:
        """
        Initialize state manager and load state from database.
        
        Called once on strategy startup.
        """
        if self._initialized:
            return
        
        # Load state from database
        loaded_state = await self.load_state(self.strategy_name)
        
        if loaded_state:
            self._state = loaded_state
            self.logger.info(
                f"State manager initialized with existing state for {self.strategy_name}"
            )
        else:
            # Initialize default state
            self._state = self._get_default_state()
            await self.save_state(self.strategy_name, self._state)
            self.logger.info(
                f"State manager initialized with default state for {self.strategy_name}"
            )
        
        self._initialized = True
    
    def _get_default_state(self) -> Dict[str, Any]:
        """Get default strategy state."""
        return {
            'initialized_at': datetime.now().isoformat(),
            'is_paused': False,
            'circuit_breaker_active': False,
            'performance_metrics': {
                'total_positions_opened': 0,
                'total_positions_closed': 0,
                'total_pnl_usd': 0.0,
                'total_funding_received': 0.0,
                'successful_trades': 0,
                'failed_trades': 0
            },
            'last_opportunity_check': None,
            'last_rebalance_time': None,
            'config_snapshot': {}
        }
    
    async def save_state(self, strategy_name: str, state: Dict[str, Any]) -> None:
        """
        Save strategy state to database.
        
        Args:
            strategy_name: Name of the strategy (used as key)
            state: State dictionary to persist
        """
        if not self._check_database_available():
            # Store in memory only for testing
            self._state = state
            return
        
        # Convert state to JSON (handle Decimal/datetime types)
        state_json = json.loads(
            json.dumps(state, default=self._json_serializer)
        )
        
        # Upsert into database (strategy_name is used as the state_key)
        query = """
            INSERT INTO strategy_state (strategy_name, state_data, last_updated)
            VALUES (:strategy_name, :state_data, NOW())
            ON CONFLICT (strategy_name)
            DO UPDATE SET
                state_data = :state_data,
                last_updated = NOW()
        """
        
        await database.execute(query, values={
            "strategy_name": strategy_name,
            "state_data": json.dumps(state_json)
        })
        
        # Store in memory
        self._state = state
        
        self.logger.debug(f"State saved to database: {len(state)} keys")
    
    async def load_state(self, strategy_name: str) -> Dict[str, Any]:
        """
        Load strategy state from database.
        
        Args:
            strategy_name: Name of the strategy to load state for
        
        Returns:
            State dictionary (empty if not found)
        """
        if not self._check_database_available():
            return {}
        
        query = """
            SELECT state_data, last_updated
            FROM strategy_state
            WHERE strategy_name = :strategy_name
        """
        
        row = await database.fetch_one(query, values={
            "strategy_name": strategy_name
        })
        
        if row:
            state_data = row['state_data']
            
            # Parse JSON
            if isinstance(state_data, str):
                state = json.loads(state_data)
            else:
                # Already a dict (JSONB type)
                state = state_data
            
            self.logger.debug(
                f"Loaded state from database: {len(state)} keys "
                f"(last updated: {row['last_updated']})"
            )
            
            return state
        else:
            self.logger.debug("No existing state found in database")
            return {}
    
    async def update_state(self, key: str, value: Any) -> None:
        """
        Update a single state key.
        
        Args:
            key: State key to update
            value: New value
        """
        current_state = await self.get_state()
        current_state[key] = value
        await self.save_state(self.strategy_name, current_state)
    
    async def update_performance_metric(self, metric_name: str, value: Any) -> None:
        """
        Update a performance metric.
        
        Args:
            metric_name: Name of the metric
            value: New value
        """
        current_state = await self.get_state()
        
        if 'performance_metrics' not in current_state:
            current_state['performance_metrics'] = {}
        
        current_state['performance_metrics'][metric_name] = value
        await self.save_state(self.strategy_name, current_state)
    
    async def increment_metric(self, metric_name: str, amount: float = 1.0) -> None:
        """
        Increment a performance metric.
        
        Args:
            metric_name: Name of the metric
            amount: Amount to increment by (default: 1.0)
        """
        current_state = await self.get_state()
        
        if 'performance_metrics' not in current_state:
            current_state['performance_metrics'] = {}
        
        current_value = current_state['performance_metrics'].get(metric_name, 0.0)
        current_state['performance_metrics'][metric_name] = current_value + amount
        
        await self.save_state(self.strategy_name, current_state)
    
    async def set_paused(self, paused: bool, reason: Optional[str] = None) -> None:
        """
        Set strategy pause state.
        
        Args:
            paused: Whether strategy is paused
            reason: Reason for pause (optional)
        """
        current_state = await self.get_state()
        current_state['is_paused'] = paused
        
        if reason:
            current_state['pause_reason'] = reason
            current_state['paused_at'] = datetime.now().isoformat()
        elif not paused:
            # Clear pause info when unpausing
            current_state.pop('pause_reason', None)
            current_state.pop('paused_at', None)
        
        await self.save_state(self.strategy_name, current_state)
        
        self.logger.info(f"Strategy {'paused' if paused else 'resumed'}: {reason or 'No reason'}")
    
    async def activate_circuit_breaker(self, reason: str) -> None:
        """
        Activate circuit breaker (emergency stop).
        
        Args:
            reason: Reason for circuit breaker activation
        """
        current_state = await self.get_state()
        current_state['circuit_breaker_active'] = True
        current_state['circuit_breaker_reason'] = reason
        current_state['circuit_breaker_activated_at'] = datetime.now().isoformat()
        
        await self.save_state(self.strategy_name, current_state)
        
        self.logger.critical(f"CIRCUIT BREAKER ACTIVATED: {reason}")
    
    async def deactivate_circuit_breaker(self) -> None:
        """Deactivate circuit breaker."""
        current_state = await self.get_state()
        current_state['circuit_breaker_active'] = False
        current_state.pop('circuit_breaker_reason', None)
        current_state.pop('circuit_breaker_activated_at', None)
        
        await self.save_state(self.strategy_name, current_state)
        
        self.logger.info("Circuit breaker deactivated")
    
    async def save_config_snapshot(self, config: Dict[str, Any]) -> None:
        """
        Save configuration snapshot.
        
        Useful for tracking config changes over time.
        
        Args:
            config: Current strategy configuration
        """
        current_state = await self.get_state()
        current_state['config_snapshot'] = config
        current_state['config_updated_at'] = datetime.now().isoformat()
        
        await self.save_state(self.strategy_name, current_state)
    
    async def clear_state(self) -> None:
        """
        Clear all state from both database and memory.
        """
        # Delete from database
        query = """
            DELETE FROM strategy_state
            WHERE strategy_name = :strategy_name
        """
        
        await database.execute(query, values={
            "strategy_name": self.strategy_name
        })
        
        # Reset to default
        self._state = self._get_default_state()
        await self.save_state(self.strategy_name, self._state)
        
        self.logger.info("State reset to default")
    
    @staticmethod
    def _json_serializer(obj):
        """
        JSON serializer for objects not serializable by default.
        
        Handles Decimal and datetime types.
        """
        if isinstance(obj, Decimal):
            return float(obj)
        if isinstance(obj, datetime):
            return obj.isoformat()
        raise TypeError(f"Type {type(obj)} not serializable")
    
    async def close(self):
        """Close state manager and cleanup resources."""
        # No specific cleanup needed for state manager
        # Database connection is shared and managed by position manager
        self.logger.info("State manager closed")
