"""
Base Component Interfaces

Defines abstract interfaces for shared components that strategies can use via composition.
Pattern: Composition over inheritance for functionality.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional, Dict, Any
from decimal import Decimal
from datetime import datetime
from uuid import UUID


# ============================================================================
# Position Models
# ============================================================================

@dataclass
class Position:
    """
    Base position model - flexible for both simple and complex strategies.
    
    Simple strategies: Use symbol, size_usd, entry_price
    Complex strategies: Use all fields including multi-DEX data
    """
    id: UUID
    symbol: str
    size_usd: Decimal
    
    # Optional fields
    entry_price: Optional[Decimal] = None
    
    # Multi-DEX fields (for funding arbitrage)
    long_dex: Optional[str] = None
    short_dex: Optional[str] = None
    entry_long_rate: Optional[Decimal] = None
    entry_short_rate: Optional[Decimal] = None
    
    # State
    opened_at: Optional[datetime] = None
    status: str = "open"  # 'open', 'closed', 'pending_close'
    
    # Metadata
    metadata: Optional[Dict[str, Any]] = None
    
    def __post_init__(self):
        if self.opened_at is None:
            self.opened_at = datetime.now()
        if self.metadata is None:
            self.metadata = {}


# ============================================================================
# Position Manager Interface
# ============================================================================

class BasePositionManager(ABC):
    """
    Interface for position tracking.
    
    Different strategies may need different implementations:
    - Simple strategies: Track single positions per symbol
    - Funding arb: Track delta-neutral pairs across DEXes
    - Market making: Track quote positions
    
    Pattern: Abstract interface allows easy swapping of implementations.
    """
    
    @abstractmethod
    async def add_position(self, position: Position) -> None:
        """
        Add a new position to tracking.
        
        Args:
            position: Position to add
        """
        pass
    
    @abstractmethod
    async def get_position(self, position_id: UUID) -> Optional[Position]:
        """
        Get position by ID.
        
        Args:
            position_id: Position UUID
            
        Returns:
            Position if found, None otherwise
        """
        pass
    
    @abstractmethod
    async def get_open_positions(self) -> List[Position]:
        """
        Get all open positions.
        
        Returns:
            List of open positions
        """
        pass
    
    @abstractmethod
    async def update_position(self, position: Position) -> None:
        """
        Update existing position.
        
        Args:
            position: Position with updated fields
        """
        pass
    
    @abstractmethod
    async def close_position(
        self, 
        position_id: UUID, 
        pnl_usd: Optional[Decimal] = None
    ) -> None:
        """
        Mark position as closed.
        
        Args:
            position_id: Position to close
            pnl_usd: Final PnL (optional)
        """
        pass
    
    @abstractmethod
    async def get_position_summary(
        self,
        position_id: UUID,
        current_market_data: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Get aggregated position summary.
        
        For multi-DEX positions, aggregates metrics from both sides.
        Pattern from Hummingbot's PositionHold.
        
        Args:
            position_id: Position to summarize
            current_market_data: Current prices/rates for calculations
            
        Returns:
            Dict with aggregated metrics (PnL, funding, etc.)
        """
        pass


# ============================================================================
# State Manager Interface
# ============================================================================

class BaseStateManager(ABC):
    """
    Interface for strategy state persistence.
    
    Supports multiple backends:
    - PostgreSQL (recommended for production)
    - SQLite (for local testing)
    - In-memory (for unit tests)
    
    Pattern: Backend-agnostic interface for state storage.
    """
    
    @abstractmethod
    async def save_state(
        self, 
        strategy_name: str, 
        state_data: Dict[str, Any]
    ) -> None:
        """
        Save strategy state.
        
        Args:
            strategy_name: Unique strategy identifier
            state_data: State to persist (must be JSON-serializable)
        """
        pass
    
    @abstractmethod
    async def load_state(self, strategy_name: str) -> Dict[str, Any]:
        """
        Load strategy state.
        
        Args:
            strategy_name: Strategy identifier
            
        Returns:
            Saved state dict, or {} if not found
        """
        pass
    
    @abstractmethod
    async def clear_state(self, strategy_name: str) -> None:
        """
        Clear strategy state.
        
        Args:
            strategy_name: Strategy to clear
        """
        pass


# ============================================================================
# Concrete Implementations (Simple Versions)
# ============================================================================

class InMemoryPositionManager(BasePositionManager):
    """
    Simple in-memory position manager for testing.
    
    For production, use PostgreSQL-backed implementation.
    """
    
    def __init__(self):
        self._positions: Dict[UUID, Position] = {}
    
    async def add_position(self, position: Position) -> None:
        self._positions[position.id] = position
    
    async def get_position(self, position_id: UUID) -> Optional[Position]:
        return self._positions.get(position_id)
    
    async def get_open_positions(self) -> List[Position]:
        return [p for p in self._positions.values() if p.status == "open"]
    
    async def update_position(self, position: Position) -> None:
        self._positions[position.id] = position
    
    async def close_position(
        self, 
        position_id: UUID, 
        pnl_usd: Optional[Decimal] = None
    ) -> None:
        if position_id in self._positions:
            self._positions[position_id].status = "closed"
            if pnl_usd is not None:
                self._positions[position_id].metadata['pnl_usd'] = pnl_usd
    
    async def get_position_summary(
        self,
        position_id: UUID,
        current_market_data: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Simple summary - override for complex aggregation"""
        position = self._positions.get(position_id)
        if not position:
            return {}
        
        return {
            'position_id': position.id,
            'symbol': position.symbol,
            'size_usd': position.size_usd,
            'status': position.status,
            'opened_at': position.opened_at
        }


class InMemoryStateManager(BaseStateManager):
    """
    Simple in-memory state manager for testing.
    
    For production, use PostgreSQL-backed implementation.
    """
    
    def __init__(self):
        self._state: Dict[str, Dict[str, Any]] = {}
    
    async def save_state(
        self, 
        strategy_name: str, 
        state_data: Dict[str, Any]
    ) -> None:
        self._state[strategy_name] = state_data
    
    async def load_state(self, strategy_name: str) -> Dict[str, Any]:
        return self._state.get(strategy_name, {})
    
    async def clear_state(self, strategy_name: str) -> None:
        if strategy_name in self._state:
            del self._state[strategy_name]

