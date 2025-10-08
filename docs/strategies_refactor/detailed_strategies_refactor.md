# Strategy Layer Refactoring: Task Breakdown

## 📋 Overview

This document outlines the complete refactoring plan to migrate the trading strategy layer from a simple flat structure to a **3-Level Hierarchy + Composition Hybrid** architecture.

**Goal:** Create a flexible, testable, and extensible strategy system that supports both simple strategies (Grid) and complex multi-DEX strategies (Funding Arbitrage).

**Architecture Pattern:** Hybrid of Inheritance (for shared contracts) + Composition (for flexibility)

---

## 🎯 Target Structure

```
/strategies/                          
├── base_strategy.py                  # Level 1: Minimal interface (150 lines)
├── factory.py                        # Strategy factory (100 lines)
│
├── /categories/                      # Level 2: Strategy archetypes
│   ├── __init__.py
│   ├── stateless_strategy.py        # For simple strategies (150 lines)
│   └── stateful_strategy.py         # For complex strategies (200 lines)
│
├── /components/                      # Shared reusable components
│   ├── __init__.py
│   ├── position_manager.py          # Multi-DEX position tracking (300 lines)
│   ├── state_manager.py             # State persistence (200 lines)
│   └── base_components.py           # Component interfaces (100 lines)
│
└── /implementations/                 # Level 3: Concrete strategies
    ├── __init__.py
    │
    ├── /grid/                        # Simple strategy package (~500 lines)
    │   ├── __init__.py
    │   ├── strategy.py               # Main strategy logic (300 lines)
    │   ├── config.py                 # Pydantic config models (100 lines)
    │   └── models.py                 # Data models (100 lines)
    │
    └── /funding_arbitrage/           # Complex strategy package (~2500 lines)
        ├── __init__.py
        ├── strategy.py               # Main orchestrator (500 lines)
        ├── config.py                 # Pydantic config models (200 lines)
        ├── models.py                 # Data models (Position, Transfer, etc.) (200 lines)
        ├── position_manager.py       # Multi-DEX position tracker (400 lines)
        ├── rebalancer.py             # Rebalancing orchestrator (250 lines)
        │
        ├── /rebalance_strategies/    # Pluggable sub-strategies (~700 lines)
        │   ├── __init__.py           # Factory function
        │   ├── base.py               # BaseRebalanceStrategy interface (80 lines)
        │   ├── profit_erosion.py     # Profit erosion checker (100 lines)
        │   ├── divergence_flip.py    # Divergence flip checker (80 lines)
        │   ├── better_opportunity.py # Opportunity switcher (120 lines)
        │   ├── time_based.py         # Time-based exit (80 lines)
        │   ├── oi_imbalance.py       # OI imbalance checker (100 lines)
        │   └── combined.py           # Combined strategy (140 lines)
        │
        └── /operations/              # Complex operations (~450 lines)
            ├── __init__.py
            ├── fund_transfer.py      # Cross-DEX fund transfers (250 lines)
            └── bridge_manager.py     # Cross-chain bridging (200 lines)
```

**Total New Code:** ~4,000 lines  
**Refactored Code:** ~500 lines  
**Deleted Code:** ~100 lines

---

## 📊 Database Requirements

### Current State
- ✅ **Funding Rate Service** uses PostgreSQL (existing)
  - Tables: `dexes`, `symbols`, `funding_rates`, `opportunities`

### New Requirements

#### Option A: Extend Existing PostgreSQL (Recommended)
Add new tables to the existing `funding_rate_service` database:

```sql
-- New tables for strategy state persistence

CREATE TABLE strategy_positions (
    id UUID PRIMARY KEY,
    strategy_name VARCHAR(50) NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    long_dex VARCHAR(20) NOT NULL,
    short_dex VARCHAR(20) NOT NULL,
    size_usd DECIMAL(20, 8) NOT NULL,
    entry_long_rate DECIMAL(20, 8) NOT NULL,
    entry_short_rate DECIMAL(20, 8) NOT NULL,
    entry_divergence DECIMAL(20, 8) NOT NULL,
    current_divergence DECIMAL(20, 8),
    opened_at TIMESTAMP NOT NULL,
    last_check TIMESTAMP,
    status VARCHAR(20) NOT NULL, -- 'open', 'pending_close', 'closed'
    rebalance_pending BOOLEAN DEFAULT FALSE,
    rebalance_reason VARCHAR(50),
    closed_at TIMESTAMP,
    pnl_usd DECIMAL(20, 8),
    metadata JSONB,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE fund_transfers (
    id UUID PRIMARY KEY,
    position_id UUID REFERENCES strategy_positions(id),
    from_dex VARCHAR(20) NOT NULL,
    to_dex VARCHAR(20) NOT NULL,
    amount_usd DECIMAL(20, 8) NOT NULL,
    reason VARCHAR(50) NOT NULL,
    status VARCHAR(20) NOT NULL, -- 'pending', 'withdrawing', 'bridging', 'depositing', 'completed', 'failed'
    withdrawal_tx VARCHAR(100),
    bridge_tx VARCHAR(100),
    deposit_tx VARCHAR(100),
    retry_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW(),
    completed_at TIMESTAMP,
    error_message TEXT
);

CREATE TABLE strategy_state (
    strategy_name VARCHAR(50) PRIMARY KEY,
    state_data JSONB NOT NULL,
    last_updated TIMESTAMP DEFAULT NOW()
);

-- Indexes for performance
CREATE INDEX idx_positions_strategy_status ON strategy_positions(strategy_name, status);
CREATE INDEX idx_positions_opened_at ON strategy_positions(opened_at);
CREATE INDEX idx_transfers_status ON fund_transfers(status);
CREATE INDEX idx_transfers_position ON fund_transfers(position_id);
```

**Rationale:**
- ✅ Reuse existing database infrastructure
- ✅ Single source of truth for all data
- ✅ Easy to query positions + funding rates together
- ✅ Simpler deployment (one database)

#### Option B: SQLite for Strategy State (Alternative)
Use SQLite for local strategy state if you want isolation:

```python
# /strategies/components/state_manager.py
# Can support both backends via configuration
```

**Rationale:**
- ✅ No network dependency
- ✅ Faster for local state
- ❌ Can't query across services
- ❌ Harder to debug/inspect

**Recommendation:** Use Option A (PostgreSQL) for consistency.

---

## 🚀 Implementation Phases

### **Phase 0: Preparation (Day 1)**

#### Tasks:
1. **Backup current code**
   ```bash
   git checkout -b feature/strategy-refactor
   cp -r strategies strategies_backup
   ```

2. **Create database migration**
   ```bash
   # Create migration file
   touch funding_rate_service/database/migrations/004_add_strategy_tables.sql
   ```

3. **Document current behavior**
   - Test current Grid strategy
   - Document all existing strategy parameters
   - List all breaking changes

#### Deliverables:
- ✅ Git branch created
- ✅ Database migration file ready
- ✅ Current behavior documented

---

### **Phase 1: Foundation Layer (Days 2-3)**

Build the base infrastructure without breaking existing code.

#### 1.1 Update Base Strategy Interface

**File:** `/strategies/base_strategy.py`

```python
"""
Refactored BaseStrategy - Minimal interface for all strategies
Changes from current:
- Simplified to essential methods only
- Removed get_required_parameters() (use Pydantic instead)
- Renamed execute_strategy() -> execute_cycle() for clarity
- Removed should_execute() (strategies decide internally)
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, List, Dict, Any
from enum import Enum

class StrategyAction(Enum):
    """Strategy action types - KEEP existing"""
    NONE = "none"
    PLACE_ORDER = "place_order"
    CLOSE_POSITION = "close_position"
    REBALANCE = "rebalance"
    WAIT = "wait"

@dataclass
class OrderParams:
    """KEEP existing - no changes needed"""
    side: str
    quantity: Decimal
    price: Optional[Decimal] = None
    order_type: str = "limit"
    reduce_only: bool = False
    # ... rest of existing fields

@dataclass
class StrategyResult:
    """KEEP existing - no changes needed"""
    action: StrategyAction
    orders: List[OrderParams] = None
    message: str = ""
    wait_time: float = 0

@dataclass
class MarketData:
    """KEEP existing - no changes needed"""
    ticker: str
    best_bid: Decimal
    best_ask: Decimal
    # ... rest of existing fields

class BaseStrategy(ABC):
    """
    Minimal interface all strategies must implement.
    
    Key changes:
    - execute_strategy() -> execute_cycle() (better name)
    - Removed should_execute() (strategies handle internally)
    - Removed get_required_parameters() (use Pydantic configs)
    """
    
    def __init__(self, config, exchange_client=None):
        """
        Args:
            config: Strategy configuration (will be Pydantic model)
            exchange_client: Single exchange client (for simple strategies)
        """
        self.config = config
        self.exchange_client = exchange_client
        self.logger = TradingLogger(...)
        self.is_initialized = False
    
    async def initialize(self):
        """Setup strategy resources - KEEP existing logic"""
        if not self.is_initialized:
            await self._initialize_strategy()
            self.is_initialized = True
            self.logger.log(f"Strategy '{self.get_strategy_name()}' initialized", "INFO")
    
    @abstractmethod
    async def _initialize_strategy(self):
        """Strategy-specific initialization - KEEP existing"""
        pass
    
    @abstractmethod
    async def execute_cycle(self) -> StrategyResult:
        """
        Main execution loop - called by trading_bot.py every iteration.
        
        Renamed from execute_strategy() for clarity.
        
        Returns:
            StrategyResult: Action taken and any orders to place
        """
        pass
    
    @abstractmethod
    def get_strategy_name(self) -> str:
        """Get strategy name - KEEP existing"""
        pass
    
    async def cleanup(self):
        """Cleanup resources - KEEP existing"""
        self.logger.log(f"Strategy '{self.get_strategy_name()}' cleanup completed", "INFO")
    
    def get_parameter(self, param_name: str, default_value: Any = None) -> Any:
        """Get strategy parameter - KEEP for backward compatibility"""
        strategy_params = getattr(self.config, 'strategy_params', {})
        return strategy_params.get(param_name, default_value)
```

**Rationale:**
- Minimal interface = easier to implement
- Removed validation logic (Pydantic handles this)
- Clearer naming (execute_cycle vs execute_strategy)
- Backward compatible (kept helper methods)

#### 1.2 Create Component Interfaces

**File:** `/strategies/components/base_components.py`

```python
"""
Base interfaces for shared components.
These define contracts for Position Managers, State Managers, etc.
"""

from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any
from decimal import Decimal
from datetime import datetime
from uuid import UUID

class Position:
    """
    Base position model - used by both simple and complex strategies.
    
    Simple strategies may use only: symbol, size, entry_price
    Complex strategies use: all fields including multi-DEX data
    """
    def __init__(
        self,
        id: UUID,
        symbol: str,
        size_usd: Decimal,
        entry_price: Optional[Decimal] = None,
        # Multi-DEX fields (optional)
        long_dex: Optional[str] = None,
        short_dex: Optional[str] = None,
        entry_long_rate: Optional[Decimal] = None,
        entry_short_rate: Optional[Decimal] = None,
        opened_at: Optional[datetime] = None,
        status: str = "open",
        metadata: Optional[Dict[str, Any]] = None
    ):
        self.id = id
        self.symbol = symbol
        self.size_usd = size_usd
        self.entry_price = entry_price
        self.long_dex = long_dex
        self.short_dex = short_dex
        self.entry_long_rate = entry_long_rate
        self.entry_short_rate = entry_short_rate
        self.opened_at = opened_at or datetime.now()
        self.status = status
        self.metadata = metadata or {}

class BasePositionManager(ABC):
    """
    Interface for position tracking.
    
    Different strategies may need different implementations:
    - Simple strategies: Track single positions per symbol
    - Funding arb: Track delta-neutral pairs across DEXs
    - Market making: Track quote positions
    """
    
    @abstractmethod
    async def add_position(self, position: Position):
        """Add a new position to tracking"""
        pass
    
    @abstractmethod
    async def get_position(self, position_id: UUID) -> Optional[Position]:
        """Get position by ID"""
        pass
    
    @abstractmethod
    async def get_open_positions(self) -> List[Position]:
        """Get all open positions"""
        pass
    
    @abstractmethod
    async def update_position(self, position: Position):
        """Update existing position"""
        pass
    
    @abstractmethod
    async def close_position(self, position_id: UUID, pnl_usd: Optional[Decimal] = None):
        """Mark position as closed"""
        pass

class BaseStateManager(ABC):
    """
    Interface for state persistence.
    
    Supports multiple backends:
    - PostgreSQL (recommended for production)
    - SQLite (for local testing)
    - In-memory (for unit tests)
    """
    
    @abstractmethod
    async def save_state(self, strategy_name: str, state_data: Dict[str, Any]):
        """Save strategy state"""
        pass
    
    @abstractmethod
    async def load_state(self, strategy_name: str) -> Dict[str, Any]:
        """Load strategy state"""
        pass
    
    @abstractmethod
    async def clear_state(self, strategy_name: str):
        """Clear strategy state"""
        pass
```

**Rationale:**
- Abstract interfaces = easy to swap implementations
- Position model flexible enough for all strategies
- StateManager supports multiple backends

#### 1.3 Run Database Migration

```bash
# Run the new migration
cd funding_rate_service
python scripts/run_migration.py 004_add_strategy_tables.sql
```

#### Deliverables:
- ✅ Updated `base_strategy.py` with minimal interface
- ✅ Created `components/base_components.py` with interfaces
- ✅ Database tables created
- ✅ All tests still pass (nothing broken yet)

---

### **Phase 2: Category Layer (Days 4-5)**

Create the two strategy archetypes that extend BaseStrategy.

#### 2.1 Create Stateless Strategy Category

**File:** `/strategies/categories/stateless_strategy.py`

```python
"""
StatelessStrategy - For simple strategies that don't track positions.

Examples: Grid trading, TWAP, signal-based strategies

Key characteristics:
- Single exchange client
- No position tracking across cycles
- Simple execution flow
"""

from strategies.base_strategy import BaseStrategy, StrategyResult, MarketData, StrategyAction
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
    """
    
    def __init__(self, config, exchange_client):
        """
        Args:
            config: Strategy config (Pydantic model)
            exchange_client: Single exchange client (required)
        """
        super().__init__(config, exchange_client)
        
        if exchange_client is None:
            raise ValueError("StatelessStrategy requires exchange_client")
    
    async def execute_cycle(self) -> StrategyResult:
        """
        Template method pattern - defines execution flow.
        
        Flow:
        1. Get market data
        2. Check if should execute
        3. Execute strategy if conditions met
        4. Return result
        
        Rationale: Standardizes simple strategy flow
        """
        try:
            # Get current market data
            market_data = await self.get_market_data()
            
            # Check conditions
            if await self.should_execute(market_data):
                return await self.execute_strategy(market_data)
            
            return StrategyResult(
                action=StrategyAction.WAIT,
                message="Conditions not met",
                wait_time=self.config.check_interval or 60
            )
            
        except Exception as e:
            self.logger.log(f"Error in execute_cycle: {e}", "ERROR")
            return StrategyResult(
                action=StrategyAction.WAIT,
                message=f"Error: {e}",
                wait_time=60
            )
    
    @abstractmethod
    async def should_execute(self, market_data: MarketData) -> bool:
        """
        Check if strategy should execute.
        
        Child implements logic like:
        - Is price at grid level?
        - Is signal triggered?
        - Is time window correct?
        """
        pass
    
    @abstractmethod
    async def execute_strategy(self, market_data: MarketData) -> StrategyResult:
        """
        Execute the strategy logic.
        
        Child implements:
        - Calculate order parameters
        - Return StrategyResult with orders
        """
        pass
    
    # Helper methods (keep from current base_strategy.py)
    
    async def get_market_data(self) -> MarketData:
        """Fetch current market data from exchange"""
        best_bid, best_ask = await self.exchange_client.fetch_bbo_prices(
            self.config.contract_id
        )
        return MarketData(
            ticker=self.config.ticker,
            best_bid=best_bid,
            best_ask=best_ask,
            mid_price=(best_bid + best_ask) / 2,
            timestamp=time.time()
        )
    
    async def get_current_position(self):
        """Get current position from exchange"""
        return await self.exchange_client.get_account_positions()
```

**Rationale:**
- Template method provides consistent flow for simple strategies
- All market data logic centralized
- Child only implements decision logic

#### 2.2 Create Stateful Strategy Category

**File:** `/strategies/categories/stateful_strategy.py`

```python
"""
StatefulStrategy - For complex strategies that track positions and state.

Examples: Funding arbitrage, market making, portfolio strategies

Key characteristics:
- Multiple exchange clients (multi-DEX)
- Position tracking across cycles
- State persistence
- Complex execution flow (child controls)
"""

from strategies.base_strategy import BaseStrategy, StrategyResult
from strategies.components.base_components import BasePositionManager, BaseStateManager
from abc import abstractmethod
from typing import Dict, Optional

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
    """
    
    def __init__(self, config, exchange_clients: Optional[Dict[str, Any]] = None):
        """
        Args:
            config: Strategy config (Pydantic model)
            exchange_clients: Dict of exchange clients {dex_name: client}
                            (multi-DEX support)
        """
        # Note: exchange_client=None for multi-DEX strategies
        super().__init__(config, exchange_client=None)
        
        self.exchange_clients = exchange_clients or {}
        
        # COMPOSITION: Create components via factory methods
        # Child can override _create_* methods to customize
        self.position_manager = self._create_position_manager()
        self.state_manager = self._create_state_manager()
    
    def _create_position_manager(self) -> BasePositionManager:
        """
        Factory method - override to customize position tracking.
        
        Default: Simple in-memory position manager
        Override: Database-backed, multi-DEX aware, etc.
        
        Rationale: Allows child to inject custom implementation
        """
        from strategies.components.position_manager import PositionManager
        return PositionManager()
    
    def _create_state_manager(self) -> BaseStateManager:
        """
        Factory method - override to customize state persistence.
        
        Default: PostgreSQL state manager
        Override: SQLite, in-memory (for testing), etc.
        
        Rationale: Allows different storage backends
        """
        from strategies.components.state_manager import PostgreSQLStateManager
        return PostgreSQLStateManager(
            connection_string=self.config.database_url
        )
    
    async def _initialize_strategy(self):
        """
        Default initialization for stateful strategies.
        
        Loads:
        - Persisted state
        - Open positions from database
        
        Child can override to add custom initialization
        """
        await self.state_manager.load_state(self.get_strategy_name())
        await self.position_manager.restore_positions()
        
        self.logger.log(
            f"Loaded {len(await self.position_manager.get_open_positions())} positions",
            "INFO"
        )
    
    # Optional helper methods (NOT enforced)
    
    async def _monitor_positions_helper(self):
        """
        Optional helper for position monitoring.
        
        Child can use this or implement own logic.
        
        Rationale: Provides common logic but doesn't force its use
        """
        positions = await self.position_manager.get_open_positions()
        
        for position in positions:
            # Basic monitoring logic
            self.logger.log(
                f"Position {position.id}: {position.symbol} @ {position.size_usd} USD",
                "INFO"
            )
    
    @abstractmethod
    async def execute_cycle(self) -> StrategyResult:
        """
        Child has FULL CONTROL over execution flow.
        
        No template method - child implements entire logic:
        - Phase 1: Monitor positions
        - Phase 2: Rebalance if needed
        - Phase 3: Open new positions
        - Phase 4: Handle operations (fund transfers, etc.)
        
        Rationale: Complex strategies have unique flows
        """
        pass
    
    async def cleanup(self):
        """Cleanup with state saving"""
        await self.state_manager.save_state(
            self.get_strategy_name(),
            self._get_state_data()
        )
        await super().cleanup()
    
    def _get_state_data(self) -> dict:
        """Get current state for persistence - override to customize"""
        return {
            "last_run": str(datetime.now()),
            # Child adds more state data
        }
```

**Rationale:**
- Factory methods = child can customize components
- No template method = child controls flow
- Helper methods = optional convenience
- Multi-DEX support built-in

#### Deliverables:
- ✅ `categories/stateless_strategy.py` created
- ✅ `categories/stateful_strategy.py` created
- ✅ Category tests written
- ✅ Documentation updated

---

### **Phase 3: Shared Components (Days 6-7)**

Build the reusable components that strategies compose.

#### 3.1 Position Manager Implementation

**File:** `/strategies/components/position_manager.py`

```python
"""
Position Manager - Tracks open positions with database persistence.

Supports:
- Single-DEX positions (Grid strategy)
- Multi-DEX positions (Funding arbitrage)
- Database sync
"""

from strategies.components.base_components import BasePositionManager, Position
from typing import List, Optional, Dict
from uuid import UUID
from decimal import Decimal
import asyncpg

class PositionManager(BasePositionManager):
    """
    Database-backed position tracker.
    
    Uses PostgreSQL for persistence (funding_rate_service DB).
    
    Key features:
    - Automatic sync to database
    - In-memory cache for fast access
    - Multi-DEX aware
    """
    
    def __init__(self, db_connection: Optional[asyncpg.Connection] = None):
        """
        Args:
            db_connection: Optional DB connection (for testing, pass None for default)
        """
        self.db = db_connection
        self._positions_cache: Dict[UUID, Position] = {}
    
    async def restore_positions(self):
        """Load open positions from database into cache"""
        # Query: SELECT * FROM strategy_positions WHERE status = 'open'
        # Populate _positions_cache
        pass
    
    async def add_position(self, position: Position):
        """Add position to cache and database"""
        # INSERT INTO strategy_positions ...
        self._positions_cache[position.id] = position
        pass
    
    async def get_position(self, position_id: UUID) -> Optional[Position]:
        """Get position from cache (fast path)"""
        return self._positions_cache.get(position_id)
    
    async def get_open_positions(self) -> List[Position]:
        """Get all open positions from cache"""
        return [p for p in self._positions_cache.values() if p.status == "open"]
    
    async def update_position(self, position: Position):
        """Update position in cache and database"""
        # UPDATE strategy_positions SET ... WHERE id = position.id
        self._positions_cache[position.id] = position
        pass
    
    async def close_position(self, position_id: UUID, pnl_usd: Optional[Decimal] = None):
        """Mark position as closed"""
        # UPDATE strategy_positions SET status='closed', closed_at=NOW(), pnl_usd=...
        if position_id in self._positions_cache:
            self._positions_cache[position_id].status = "closed"
        pass
```

**Rationale:**
- In-memory cache = fast reads
- Database sync = persistence
- Single implementation works for all strategies

#### 3.2 State Manager Implementation

**File:** `/strategies/components/state_manager.py`

```python
"""
State Manager - Persists arbitrary strategy state.

Supports:
- PostgreSQL (production)
- SQLite (testing)
- In-memory (unit tests)
"""

from strategies.components.base_components import BaseStateManager
from typing import Dict, Any
import json

class PostgreSQLStateManager(BaseStateManager):
    """
    PostgreSQL-backed state persistence.
    
    Uses strategy_state table in funding_rate_service DB.
    """
    
    def __init__(self, connection_string: str):
        self.connection_string = connection_string
    
    async def save_state(self, strategy_name: str, state_data: Dict[str, Any]):
        """Save state as JSON"""
        # INSERT INTO strategy_state (strategy_name, state_data)
        # VALUES (%s, %s)
        # ON CONFLICT (strategy_name) DO UPDATE SET state_data=%s
        pass
    
    async def load_state(self, strategy_name: str) -> Dict[str, Any]:
        """Load state from database"""
        # SELECT state_data FROM strategy_state WHERE strategy_name = %s
        # Return {} if not found
        pass
    
    async def clear_state(self, strategy_name: str):
        """Delete strategy state"""
        # DELETE FROM strategy_state WHERE strategy_name = %s
        pass

class InMemoryStateManager(BaseStateManager):
    """In-memory state manager for testing"""
    
    def __init__(self):
        self._state: Dict[str, Dict[str, Any]] = {}
    
    async def save_state(self, strategy_name: str, state_data: Dict[str, Any]):
        self._state[strategy_name] = state_data
    
    async def load_state(self, strategy_name: str) -> Dict[str, Any]:
        return self._state.get(strategy_name, {})
    
    async def clear_state(self, strategy_name: str):
        if strategy_name in self._state:
            del self._state[strategy_name]
```

**Rationale:**
- Multiple implementations for different environments
- JSON storage = flexible schema
- Easy to add new backends

#### Deliverables:
- ✅ `components/position_manager.py` implemented
- ✅ `components/state_manager.py` implemented
- ✅ Component tests written
- ✅ Database queries tested

---

### **Phase 4: Migrate Grid Strategy (Day 8)**

Migrate existing Grid strategy to new structure - validates the design.

#### 4.1 Create Grid Strategy Package

**File:** `/strategies/implementations/grid/strategy.py`

```python
"""
Grid Trading Strategy - Migrated to new architecture.

Changes from old version:
- Extends StatelessStrategy (not BaseStrategy)
- Uses execute_cycle() (not execute_strategy())
- Config is Pydantic model (not dict)
"""

from strategies.categories.stateless_strategy import StatelessStrategy
from strategies.base_strategy import StrategyResult, StrategyAction, OrderParams, MarketData
from .config import GridStrategyConfig
from decimal import Decimal

class GridStrategy(StatelessStrategy):
    """
    Places buy/sell orders at fixed price intervals.
    
    Simple stateless strategy - perfect example for StatelessStrategy.
    """
    
    def __init__(self, config: GridStrategyConfig, exchange_client):
        super().__init__(config, exchange_client)
        
        # Grid-specific state
        self.grid_levels = self._calculate_grid_levels()
    
    def get_strategy_name(self) -> str:
        return "Grid Trading"
    
    async def _initialize_strategy(self):
        """Grid-specific initialization"""
        self.logger.log(f"Initialized grid with {len(self.grid_levels)} levels", "INFO")
    
    async def should_execute(self, market_data: MarketData) -> bool:
        """Check if any grid levels need orders"""
        # Check if current price crosses any grid level
        # Or if any orders were filled
        return True  # For now, always check
    
    async def execute_strategy(self, market_data: MarketData) -> StrategyResult:
        """Place grid orders"""
        orders = []
        
        # Logic: Place buy orders below price, sell orders above
        for level in self.grid_levels:
            if level < market_data.mid_price:
                # Place buy order
                orders.append(OrderParams(
                    side="buy",
                    quantity=self.config.grid_size,
                    price=level,
                    order_type="limit"
                ))
            else:
                # Place sell order
                orders.append(OrderParams(
                    side="sell",
                    quantity=self.config.grid_size,
                    price=level,
                    order_type="limit"
                ))
        
        return StrategyResult(
            action=StrategyAction.PLACE_ORDER,
            orders=orders,
            message=f"Placed {len(orders)} grid orders"
        )
    
    def _calculate_grid_levels(self) -> list[Decimal]:
        """Calculate grid price levels"""
        # Grid logic from current implementation
        return []  # Placeholder
```

**File:** `/strategies/implementations/grid/config.py`

```python
"""
Grid Strategy Configuration - Pydantic models.

Benefits of Pydantic:
- Automatic validation
- Type safety
- Easy serialization
- Clear documentation
"""

from pydantic import BaseModel, Field
from decimal import Decimal

class GridStrategyConfig(BaseModel):
    """Configuration for Grid Trading Strategy"""
    
    # Exchange settings
    exchange: str = Field(..., description="Exchange name (e.g., 'lighter')")
    ticker: str = Field(..., description="Trading pair (e.g., 'BTC')")
    contract_id: str = Field(..., description="Contract ID on exchange")
    
    # Grid parameters
    grid_size: Decimal = Field(..., description="Size per grid order")
    num_grids: int = Field(..., description="Number of grid levels")
    grid_spacing: Decimal = Field(..., description="Spacing between grids")
    center_price: Decimal = Field(..., description="Center price for grid")
    
    # Execution settings
    check_interval: int = Field(default=60, description="Seconds between checks")
    
    class Config:
        # Pydantic config
        use_enum_values = True
        validate_assignment = True
```

**Rationale:**
- Pydantic replaces manual parameter validation
- Type safety catches errors at config time
- Self-documenting

#### 4.2 Update Factory

**File:** `/strategies/factory.py` (Updated)

```python
"""
Strategy Factory - Updated for new architecture.

Changes:
- Imports from implementations/
- Handles both single and multi-exchange strategies
- Uses Pydantic configs
"""

from typing import Dict, Type, Optional, Any
from .base_strategy import BaseStrategy
from .categories.stateless_strategy import StatelessStrategy
from .categories.stateful_strategy import StatefulStrategy

# Import implementations
from .implementations.grid.strategy import GridStrategy
from .implementations.grid.config import GridStrategyConfig

class StrategyFactory:
    """Factory for creating strategy instances"""
    
    _strategies: Dict[str, Type[BaseStrategy]] = {
        'grid': GridStrategy,
        # 'funding_arbitrage': FundingArbitrageStrategy,  # Phase 5
    }
    
    _config_classes: Dict[str, Type[BaseModel]] = {
        'grid': GridStrategyConfig,
        # 'funding_arbitrage': FundingArbitrageConfig,  # Phase 5
    }
    
    @classmethod
    def create_strategy(
        cls,
        strategy_name: str,
        config_dict: dict,
        exchange_client: Optional[Any] = None,
        exchange_clients: Optional[Dict[str, Any]] = None
    ) -> BaseStrategy:
        """
        Create strategy instance with Pydantic config validation.
        
        Args:
            strategy_name: Strategy to create
            config_dict: Raw config dict (will be validated)
            exchange_client: Single client (for stateless strategies)
            exchange_clients: Multiple clients (for stateful strategies)
        
        Returns:
            Initialized strategy instance
        """
        strategy_name = strategy_name.lower()
        
        if strategy_name not in cls._strategies:
            raise ValueError(f"Unknown strategy: {strategy_name}")
        
        # Validate config with Pydantic
        config_class = cls._config_classes[strategy_name]
        config = config_class(**config_dict)
        
        # Create strategy
        strategy_class = cls._strategies[strategy_name]
        
        # Check if stateful or stateless
        if issubclass(strategy_class, StatefulStrategy):
            return strategy_class(config, exchange_clients or {})
        else:
            if exchange_client is None:
                raise ValueError(f"{strategy_name} requires exchange_client")
            return strategy_class(config, exchange_client)
    
    @classmethod
    def get_supported_strategies(cls) -> list[str]:
        """Get list of available strategies"""
        return list(cls._strategies.keys())
```

**Rationale:**
- Validates configs automatically
- Handles both strategy types
- Type-safe

#### Deliverables:
- ✅ Grid strategy migrated to new structure
- ✅ Grid config as Pydantic model
- ✅ Factory updated
- ✅ Grid tests pass
- ✅ Backward compatibility maintained (via config_dict conversion)

---

### **Phase 5: Build Funding Arbitrage Foundation (Days 9-12)**

Build the complex funding arbitrage strategy package.

#### 5.1 Create Funding Arbitrage Config

**File:** `/strategies/implementations/funding_arbitrage/config.py`

```python
"""
Funding Arbitrage Configuration Models.

Hierarchical config structure:
- FundingArbConfig (main)
  - RebalanceConfig (sub-config)
  - BridgeConfig (sub-config)
"""

from pydantic import BaseModel, Field, HttpUrl
from decimal import Decimal
from typing import List, Optional

class BridgeConfig(BaseModel):
    """Cross-chain bridge configuration"""
    enabled: bool = Field(default=True, description="Enable cross-chain transfers")
    preferred_bridge: str = Field(default="layerzero", description="Bridge to use")
    max_bridge_time_minutes: int = Field(default=30, description="Max bridge wait time")
    bridge_fee_bps: int = Field(default=5, description="Bridge fee in basis points")
    
    # Bridge-specific settings
    layerzero_api_key: Optional[str] = None
    axelar_rpc_url: Optional[str] = None

class RebalanceConfig(BaseModel):
    """Rebalancing strategy configuration"""
    strategy: str = Field(default="combined", description="Rebalance sub-strategy")
    
    # Thresholds
    min_erosion_threshold: float = Field(default=0.5, description="Exit at X% profit loss")
    rebalance_cost_bps: int = Field(default=8, description="Total trading cost")
    min_profit_improvement: float = Field(default=0.002, description="Min improvement to switch")
    max_position_age_hours: int = Field(default=168, description="Max position age (1 week)")
    
    # Execution
    check_interval_seconds: int = Field(default=60, description="Check frequency")
    enable_better_opportunity: bool = Field(default=True, description="Switch to better opps")

class FundingArbConfig(BaseModel):
    """Main funding arbitrage configuration"""
    
    # Basic settings
    strategy_name: str = Field(default="funding_arbitrage", description="Strategy identifier")
    
    # Exchange settings
    exchanges: List[str] = Field(..., description="DEXs to use (e.g., ['lighter', 'grvt'])")
    
    # Position management
    max_positions: int = Field(default=10, description="Max concurrent positions")
    max_new_positions_per_cycle: int = Field(default=2, description="Max new positions per cycle")
    min_profit: float = Field(default=0.001, description="Min profit threshold (0.1%)")
    max_oi_usd: float = Field(default=500_000, description="Max OI filter for point farming")
    
    # Risk management
    max_position_size_usd: float = Field(default=10_000, description="Max size per position")
    max_total_exposure_usd: float = Field(default=50_000, description="Max total exposure")
    
    # Rebalancing
    rebalance_config: RebalanceConfig = Field(default_factory=RebalanceConfig)
    
    # Fund transfers
    bridge_config: BridgeConfig = Field(default_factory=BridgeConfig)
    auto_rebalance_funds: bool = Field(default=True, description="Auto transfer funds")
    min_transfer_amount_usd: float = Field(default=100, description="Min transfer amount")
    
    # API
    funding_api_url: HttpUrl = Field(default="http://localhost:8000", description="Funding rate service URL")
    
    # Database
    database_url: str = Field(..., description="PostgreSQL connection string")
    
    class Config:
        use_enum_values = True
        validate_assignment = True
```

**Rationale:**
- Hierarchical config = organized
- Validation prevents misconfig
- Defaults for safe operation

#### 5.2 Create Data Models

**File:** `/strategies/implementations/funding_arbitrage/models.py`

```python
"""
Data models for Funding Arbitrage Strategy.

Models:
- Position (extends base Position)
- TransferOperation
- RebalanceAction
- OpportunityData
"""

from dataclasses import dataclass
from decimal import Decimal
from datetime import datetime
from uuid import UUID
from typing import Optional, Dict, Any

@dataclass
class FundingArbPosition:
    """
    Funding arbitrage position (delta-neutral pair).
    
    Extends base Position with funding-specific fields.
    """
    id: UUID
    symbol: str
    long_dex: str
    short_dex: str
    size_usd: Decimal
    
    # Entry data
    entry_long_rate: Decimal
    entry_short_rate: Decimal
    entry_divergence: Decimal
    opened_at: datetime
    
    # Current data
    current_divergence: Optional[Decimal] = None
    last_check: Optional[datetime] = None
    
    # Status
    status: str = "open"  # 'open', 'pending_close', 'closed'
    rebalance_pending: bool = False
    rebalance_reason: Optional[str] = None
    
    # Close data
    closed_at: Optional[datetime] = None
    pnl_usd: Optional[Decimal] = None
    
    # Metadata
    metadata: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}

@dataclass
class TransferOperation:
    """Cross-DEX fund transfer operation"""
    id: UUID
    position_id: Optional[UUID]
    from_dex: str
    to_dex: str
    amount_usd: Decimal
    reason: str
    
    # Status tracking
    status: str  # 'pending', 'withdrawing', 'bridging', 'depositing', 'completed', 'failed'
    withdrawal_tx: Optional[str] = None
    bridge_tx: Optional[str] = None
    deposit_tx: Optional[str] = None
    
    # Error handling
    retry_count: int = 0
    error_message: Optional[str] = None
    
    # Timestamps
    created_at: datetime = None
    completed_at: Optional[datetime] = None
    
    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now()

@dataclass
class RebalanceAction:
    """Action to be taken during rebalancing"""
    action_type: str  # 'close_position', 'open_position', 'transfer_funds'
    position_id: UUID
    reason: str
    details: Dict[str, Any]

@dataclass
class OpportunityData:
    """Opportunity from funding rate service"""
    symbol: str
    long_dex: str
    short_dex: str
    divergence: Decimal
    long_rate: Decimal
    short_rate: Decimal
    net_profit_apy: Decimal
    long_oi_usd: Decimal
    short_oi_usd: Decimal
```

**Rationale:**
- Dataclasses = simple, fast
- Rich metadata support
- Clear status tracking

#### 5.3 Create Main Strategy Orchestrator

**File:** `/strategies/implementations/funding_arbitrage/strategy.py`

```python
"""
Funding Arbitrage Strategy - Main Orchestrator.

4-Phase Execution Loop:
1. Monitor existing positions
2. Execute rebalancing if needed
3. Open new positions from opportunities
4. Process fund transfers

This is the "brain" that coordinates all components.
"""

from strategies.categories.stateful_strategy import StatefulStrategy
from strategies.base_strategy import StrategyResult, StrategyAction
from .config import FundingArbConfig
from .models import FundingArbPosition, TransferOperation
from .position_manager import FundingArbPositionManager
from .rebalancer import Rebalancer
from .operations.fund_transfer import FundTransferManager
from .api_client import FundingRateAPIClient
from typing import Dict, Any, List
from decimal import Decimal

class FundingArbitrageStrategy(StatefulStrategy):
    """
    Delta-neutral funding rate arbitrage strategy.
    
    Strategy:
    1. Long on DEX with high funding rate
    2. Short on DEX with low funding rate
    3. Collect funding rate divergence (3x per day)
    4. Rebalance when divergence shrinks or better opportunity exists
    5. Auto-transfer funds between DEXs to maintain balance
    
    Complexity: Multi-DEX, stateful, requires fund management
    """
    
    def __init__(self, config: FundingArbConfig, exchange_clients: Dict[str, Any]):
        """
        Args:
            config: Funding arbitrage configuration
            exchange_clients: Dict of {dex_name: exchange_client}
        """
        super().__init__(config, exchange_clients)
        
        # Validate we have required exchanges
        for dex in config.exchanges:
            if dex not in exchange_clients:
                raise ValueError(f"Missing exchange client for: {dex}")
        
        # COMPOSITION: Strategy-specific components
        self.rebalancer = Rebalancer(
            position_manager=self.position_manager,
            rebalance_strategy_name=config.rebalance_config.strategy,
            rebalance_config=config.rebalance_config
        )
        
        self.fund_manager = FundTransferManager(
            exchange_clients=exchange_clients,
            bridge_config=config.bridge_config
        )
        
        self.funding_api = FundingRateAPIClient(
            base_url=str(config.funding_api_url)
        )
    
    def _create_position_manager(self):
        """Override factory to use funding-specific position manager"""
        from .position_manager import FundingArbPositionManager
        return FundingArbPositionManager(
            database_url=self.config.database_url
        )
    
    def get_strategy_name(self) -> str:
        return "Funding Rate Arbitrage"
    
    async def execute_cycle(self) -> StrategyResult:
        """
        Main 4-phase execution loop.
        
        Called every minute by trading_bot.py.
        
        Returns:
            StrategyResult with actions taken
        """
        actions_taken = []
        
        try:
            # Phase 1: Monitor existing positions
            self.logger.log("Phase 1: Monitoring positions", "INFO")
            await self._monitor_positions()
            
            # Phase 2: Execute rebalancing
            self.logger.log("Phase 2: Checking rebalancing", "INFO")
            rebalanced = await self._execute_rebalancing()
            actions_taken.extend(rebalanced)
            
            # Phase 3: Open new positions (if capacity available)
            if self._has_capacity():
                self.logger.log("Phase 3: Checking new opportunities", "INFO")
                opened = await self._open_new_positions()
                actions_taken.extend(opened)
            
            # Phase 4: Process fund transfers
            self.logger.log("Phase 4: Processing fund transfers", "INFO")
            await self._process_fund_transfers()
            
            return StrategyResult(
                action=StrategyAction.REBALANCE if actions_taken else StrategyAction.WAIT,
                message=f"Cycle complete: {len(actions_taken)} actions taken",
                wait_time=self.config.rebalance_config.check_interval_seconds
            )
            
        except Exception as e:
            self.logger.log(f"Error in execute_cycle: {e}", "ERROR")
            return StrategyResult(
                action=StrategyAction.WAIT,
                message=f"Error: {e}",
                wait_time=60
            )
    
    # Phase 1: Monitor
    
    async def _monitor_positions(self):
        """
        Check all open positions for rebalancing signals.
        
        For each position:
        1. Fetch current funding rates
        2. Update position state
        3. Check if rebalancing needed
        4. Flag for rebalancing if signal triggered
        """
        positions = await self.position_manager.get_open_positions()
        
        for position in positions:
            try:
                # Get current rates from funding service
                current_rates = await self.funding_api.compare_rates(
                    symbol=position.symbol,
                    dex1=position.long_dex,
                    dex2=position.short_dex
                )
                
                # Update position
                position.current_divergence = current_rates['divergence']
                position.last_check = datetime.now()
                await self.position_manager.update_position(position)
                
                # Check rebalancing
                should_rebalance, reason = self.rebalancer.should_rebalance(
                    position, current_rates
                )
                
                if should_rebalance:
                    self.logger.log(
                        f"Rebalance signal for {position.id}: {reason}",
                        "WARNING"
                    )
                    position.rebalance_pending = True
                    position.rebalance_reason = reason
                    await self.position_manager.update_position(position)
                    
            except Exception as e:
                self.logger.log(
                    f"Error monitoring position {position.id}: {e}",
                    "ERROR"
                )
    
    # Phase 2: Rebalance
    
    async def _execute_rebalancing(self) -> List[str]:
        """
        Execute rebalancing for flagged positions.
        
        Returns:
            List of action descriptions
        """
        actions = []
        pending = await self.position_manager.get_pending_rebalance()
        
        for position in pending:
            try:
                action = await self._rebalance_position(position)
                actions.append(action)
            except Exception as e:
                self.logger.log(
                    f"Rebalance failed for {position.id}: {e}",
                    "ERROR"
                )
        
        return actions
    
    async def _rebalance_position(self, position: FundingArbPosition) -> str:
        """
        Close position and optionally reopen.
        
        Steps:
        1. Close existing position (both sides)
        2. Check if should reopen immediately
        3. Initiate fund rebalancing if needed
        """
        # Close both sides
        await self._close_position(position)
        
        # Check if should open new position
        if position.rebalance_reason == "BETTER_OPPORTUNITY":
            # Get best opportunity for this symbol
            new_opp = await self.funding_api.get_best_opportunity(
                symbol=position.symbol
            )
            if new_opp:
                await self._open_position_from_opportunity(new_opp)
        
        # Initiate fund transfer if needed
        if self.config.auto_rebalance_funds:
            await self._initiate_fund_transfer(position)
        
        return f"Rebalanced {position.symbol}: {position.rebalance_reason}"
    
    async def _close_position(self, position: FundingArbPosition):
        """Close both sides of delta-neutral position"""
        # Close long side
        long_client = self.exchange_clients[position.long_dex]
        await long_client.close_position(position.symbol)
        
        # Close short side
        short_client = self.exchange_clients[position.short_dex]
        await short_client.close_position(position.symbol)
        
        # Calculate PnL (simplified)
        pnl = position.current_divergence * position.size_usd * 24  # 24h estimate
        
        # Mark as closed
        await self.position_manager.close_position(position.id, pnl_usd=pnl)
        
        self.logger.log(
            f"Closed position {position.id}: PnL ${pnl}",
            "INFO"
        )
    
    # Phase 3: New Opportunities
    
    async def _open_new_positions(self) -> List[str]:
        """
        Query opportunities and open new positions.
        
        Returns:
            List of action descriptions
        """
        actions = []
        
        # Get opportunities from funding service
        opportunities = await self.funding_api.get_opportunities(
            min_profit=self.config.min_profit,
            max_oi_usd=self.config.max_oi_usd,
            dexes=self.config.exchanges
        )
        
        # Take top opportunities up to limit
        for opp in opportunities[:self.config.max_new_positions_per_cycle]:
            if self._should_take_opportunity(opp):
                await self._open_position_from_opportunity(opp)
                actions.append(f"Opened {opp['symbol']} on {opp['long_dex']}/{opp['short_dex']}")
        
        return actions
    
    def _should_take_opportunity(self, opportunity: Dict[str, Any]) -> bool:
        """Apply client-side filters to opportunity"""
        # Check position size limits
        if opportunity['size_usd'] > self.config.max_position_size_usd:
            return False
        
        # Check total exposure
        current_exposure = self._calculate_total_exposure()
        if current_exposure + opportunity['size_usd'] > self.config.max_total_exposure_usd:
            return False
        
        # Add more filters as needed
        return True
    
    async def _open_position_from_opportunity(self, opportunity: Dict[str, Any]):
        """Open delta-neutral position from opportunity"""
        # Open long side
        long_client = self.exchange_clients[opportunity['long_dex']]
        await long_client.open_long(
            symbol=opportunity['symbol'],
            size_usd=opportunity['size_usd']
        )
        
        # Open short side
        short_client = self.exchange_clients[opportunity['short_dex']]
        await short_client.open_short(
            symbol=opportunity['symbol'],
            size_usd=opportunity['size_usd']
        )
        
        # Create position record
        position = FundingArbPosition(
            id=uuid4(),
            symbol=opportunity['symbol'],
            long_dex=opportunity['long_dex'],
            short_dex=opportunity['short_dex'],
            size_usd=opportunity['size_usd'],
            entry_long_rate=opportunity['long_rate'],
            entry_short_rate=opportunity['short_rate'],
            entry_divergence=opportunity['divergence'],
            opened_at=datetime.now()
        )
        
        await self.position_manager.add_position(position)
        
        self.logger.log(
            f"Opened position: {position.symbol} @ ${position.size_usd}",
            "INFO"
        )
    
    # Phase 4: Fund Transfers
    
    async def _process_fund_transfers(self):
        """Process pending fund transfers"""
        await self.fund_manager.process_pending_transfers()
    
    async def _initiate_fund_transfer(self, position: FundingArbPosition):
        """Initiate fund transfer after closing position"""
        # Determine which DEX won/lost
        # Transfer funds from winning DEX to losing DEX
        # This is complex - see operations/fund_transfer.py
        pass
    
    # Helper methods
    
    def _has_capacity(self) -> bool:
        """Check if can open new positions"""
        open_count = len(self.position_manager.get_open_positions())
        return open_count < self.config.max_positions
    
    def _calculate_total_exposure(self) -> Decimal:
        """Calculate total exposure across all positions"""
        positions = self.position_manager.get_open_positions()
        return sum(p.size_usd for p in positions)
```

**Rationale:**
- Clear 4-phase structure
- Each phase is self-contained
- Error handling per position
- Logging at every step

#### Deliverables:
- ✅ Config, models, main strategy created
- ✅ 4-phase loop implemented
- ✅ Position lifecycle managed
- ✅ Funding API client stubbed

---

### **Phase 6: Rebalancing System (Days 13-15)**

Build the pluggable rebalancing sub-strategy system.

#### 6.1 Create Rebalance Base Interface

**File:** `/strategies/implementations/funding_arbitrage/rebalance_strategies/base.py`

```python
"""
Base interface for rebalancing sub-strategies.

Pluggable pattern: Different strategies can be swapped easily.
"""

from abc import ABC, abstractmethod
from typing import Tuple
from ..models import FundingArbPosition, RebalanceAction

class BaseRebalanceStrategy(ABC):
    """
    Interface for rebalancing decision logic.
    
    Child classes implement different rebalancing approaches:
    - Profit erosion based
    - Divergence flip based
    - Better opportunity based
    - Time based
    - Combined (composition of multiple)
    """
    
    def __init__(self, config: dict):
        """
        Args:
            config: Rebalance config dict
        """
        self.config = config
    
    @abstractmethod
    def should_rebalance(
        self,
        position: FundingArbPosition,
        current_rates: dict
    ) -> Tuple[bool, str]:
        """
        Determine if position should be rebalanced.
        
        Args:
            position: Current position
            current_rates: Latest funding rates
                {
                    'divergence': Decimal,
                    'long_rate': Decimal,
                    'short_rate': Decimal
                }
        
        Returns:
            (should_rebalance: bool, reason: str)
        """
        pass
    
    @abstractmethod
    def generate_rebalance_actions(
        self,
        position: FundingArbPosition
    ) -> list[RebalanceAction]:
        """
        Generate list of actions to execute.
        
        Returns:
            List of RebalanceAction objects
        """
        pass
```

#### 6.2 Create Sub-Strategy Implementations

**File:** `/strategies/implementations/funding_arbitrage/rebalance_strategies/profit_erosion.py`

```python
"""
Profit Erosion Rebalancing Strategy.

Exits when divergence drops below X% of entry divergence.
"""

from .base import BaseRebalanceStrategy
from ..models import FundingArbPosition

class ProfitErosionStrategy(BaseRebalanceStrategy):
    """Exit before all profit disappears"""
    
    def should_rebalance(self, position, current_rates):
        erosion = current_rates['divergence'] / position.entry_divergence
        
        threshold = self.config.get('min_erosion_threshold', 0.5)
        
        if erosion < threshold:
            return True, "PROFIT_EROSION"
        
        return False, None
    
    def generate_rebalance_actions(self, position):
        return [
            RebalanceAction(
                action_type="close_position",
                position_id=position.id,
                reason="PROFIT_EROSION",
                details={}
            )
        ]
```

**File:** `/strategies/implementations/funding_arbitrage/rebalance_strategies/divergence_flip.py`

```python
"""
Divergence Flip Rebalancing Strategy.

Exits when funding rate divergence becomes negative (losing money).
"""

from .base import BaseRebalanceStrategy

class DivergenceFlipStrategy(BaseRebalanceStrategy):
    """Mandatory exit when divergence flips"""
    
    def should_rebalance(self, position, current_rates):
        if current_rates['divergence'] < 0:
            return True, "DIVERGENCE_FLIPPED"
        return False, None
    
    def generate_rebalance_actions(self, position):
        return [
            RebalanceAction(
                action_type="close_position",
                position_id=position.id,
                reason="DIVERGENCE_FLIPPED",
                details={'urgent': True}
            )
        ]
```

**File:** `/strategies/implementations/funding_arbitrage/rebalance_strategies/combined.py`

```python
"""
Combined Rebalancing Strategy.

Combines multiple sub-strategies with priority ordering.
"""

from .base import BaseRebalanceStrategy
from .profit_erosion import ProfitErosionStrategy
from .divergence_flip import DivergenceFlipStrategy
from datetime import datetime

class CombinedRebalanceStrategy(BaseRebalanceStrategy):
    """
    Multi-rule rebalancing with priorities.
    
    Priority order:
    1. Critical: Divergence flip (immediate exit)
    2. High: Severe profit erosion
    3. Medium: Better opportunity available
    4. Low: Time-based exit
    """
    
    def __init__(self, config):
        super().__init__(config)
        
        # Initialize sub-strategies
        self.flip_checker = DivergenceFlipStrategy(config)
        self.erosion_checker = ProfitErosionStrategy(config)
    
    def should_rebalance(self, position, current_rates):
        """Check strategies in priority order"""
        
        # Priority 1: Divergence flip (critical)
        if current_rates['divergence'] < 0:
            return True, "DIVERGENCE_FLIPPED"
        
        # Priority 2: Severe profit erosion
        erosion = current_rates['divergence'] / position.entry_divergence
        if erosion < 0.2:  # Lost 80% of edge
            return True, "SEVERE_EROSION"
        
        # Priority 3: Normal profit erosion
        should, reason = self.erosion_checker.should_rebalance(
            position, current_rates
        )
        if should:
            return True, reason
        
        # Priority 4: Time-based (fallback)
        hours_held = (datetime.now() - position.opened_at).total_seconds() / 3600
        max_age = self.config.get('max_position_age_hours', 168)
        if hours_held >= max_age:
            return True, "TIME_LIMIT"
        
        return False, None
    
    def generate_rebalance_actions(self, position):
        # Generate appropriate actions based on reason
        return [
            RebalanceAction(
                action_type="close_position",
                position_id=position.id,
                reason=position.rebalance_reason,
                details={}
            )
        ]
```

**File:** `/strategies/implementations/funding_arbitrage/rebalance_strategies/__init__.py`

```python
"""
Factory for rebalancing sub-strategies.
"""

from .base import BaseRebalanceStrategy
from .profit_erosion import ProfitErosionStrategy
from .divergence_flip import DivergenceFlipStrategy
from .combined import CombinedRebalanceStrategy

def get_rebalance_strategy(strategy_name: str, config: dict) -> BaseRebalanceStrategy:
    """
    Factory function for creating rebalance strategies.
    
    Args:
        strategy_name: Name of strategy ('profit_erosion', 'combined', etc.)
        config: Configuration dict
    
    Returns:
        BaseRebalanceStrategy instance
    """
    strategies = {
        'profit_erosion': ProfitErosionStrategy,
        'divergence_flip': DivergenceFlipStrategy,
        'combined': CombinedRebalanceStrategy,
    }
    
    if strategy_name not in strategies:
        raise ValueError(f"Unknown rebalance strategy: {strategy_name}")
    
    return strategies[strategy_name](config)
```

**Rationale:**
- Factory pattern = easy to add new strategies
- Each strategy is self-contained
- Combined strategy uses composition (not inheritance)

#### 6.3 Create Rebalancer Orchestrator

**File:** `/strategies/implementations/funding_arbitrage/rebalancer.py`

```python
"""
Rebalancer - Orchestrates rebalancing decisions.

Uses pluggable sub-strategies for decision logic.
"""

from .rebalance_strategies import get_rebalance_strategy
from .rebalance_strategies.base import BaseRebalanceStrategy
from .models import FundingArbPosition
from typing import Tuple

class Rebalancer:
    """
    Coordinates rebalancing using pluggable sub-strategies.
    
    Responsibilities:
    - Delegate decisions to sub-strategy
    - Execute rebalancing actions
    - Track rebalancing metrics
    """
    
    def __init__(
        self,
        position_manager,
        rebalance_strategy_name: str,
        rebalance_config: dict
    ):
        """
        Args:
            position_manager: Position manager instance
            rebalance_strategy_name: Name of sub-strategy to use
            rebalance_config: Config for sub-strategy
        """
        self.position_manager = position_manager
        self.config = rebalance_config
        
        # Load pluggable sub-strategy
        self.strategy = get_rebalance_strategy(
            rebalance_strategy_name,
            rebalance_config
        )
    
    def should_rebalance(
        self,
        position: FundingArbPosition,
        current_rates: dict
    ) -> Tuple[bool, str]:
        """
        Delegate decision to sub-strategy.
        
        Returns:
            (should_rebalance: bool, reason: str)
        """
        return self.strategy.should_rebalance(position, current_rates)
    
    def generate_actions(self, position: FundingArbPosition):
        """Generate rebalancing actions"""
        return self.strategy.generate_rebalance_actions(position)
```

**Rationale:**
- Thin orchestrator = delegates to strategy
- Easy to swap strategies at runtime
- Testable (mock strategy)

#### Deliverables:
- ✅ Base rebalance interface created
- ✅ 3+ sub-strategies implemented
- ✅ Combined strategy with priority rules
- ✅ Rebalancer orchestrator
- ✅ Factory for strategy creation
- ✅ Unit tests for each strategy

---

### **Phase 7: Fund Transfer Operations (Days 16-18)**

Build the complex fund transfer and bridging system.

#### 7.1 Create Fund Transfer Manager

**File:** `/strategies/implementations/funding_arbitrage/operations/fund_transfer.py`

```python
"""
Fund Transfer Manager - Handles cross-DEX fund movements.

Complex multi-step operations:
1. Withdraw from source DEX
2. Bridge between chains (if needed)
3. Deposit to destination DEX
4. Error handling and retries
"""

from ..models import TransferOperation, FundingArbPosition
from ..config import BridgeConfig
from typing import List, Dict, Any
from decimal import Decimal
from uuid import uuid4
import asyncio

class FundTransferManager:
    """
    Manages fund transfers between DEXs.
    
    Handles:
    - Withdrawals
    - Cross-chain bridging
    - Deposits
    - Retry logic
    - Error recovery
    """
    
    def __init__(
        self,
        exchange_clients: Dict[str, Any],
        bridge_config: BridgeConfig
    ):
        """
        Args:
            exchange_clients: Dict of exchange clients
            bridge_config: Bridge configuration
        """
        self.clients = exchange_clients
        self.bridge_config = bridge_config
        
        # Queue of pending transfers
        self.pending_transfers: List[TransferOperation] = []
        
        # Initialize bridge manager
        self.bridge_manager = None
        if bridge_config.enabled:
            from .bridge_manager import BridgeManager
            self.bridge_manager = BridgeManager(bridge_config)
    
    async def initiate_transfer(
        self,
        from_dex: str,
        to_dex: str,
        amount_usd: Decimal,
        reason: str,
        position_id: Optional[UUID] = None
    ) -> TransferOperation:
        """
        Create transfer operation and add to queue.
        
        Args:
            from_dex: Source DEX
            to_dex: Destination DEX
            amount_usd: Amount to transfer
            reason: Reason for transfer
            position_id: Related position (optional)
        
        Returns:
            TransferOperation instance
        """
        operation = TransferOperation(
            id=uuid4(),
            position_id=position_id,
            from_dex=from_dex,
            to_dex=to_dex,
            amount_usd=amount_usd,
            reason=reason,
            status="pending"
        )
        
        self.pending_transfers.append(operation)
        
        logger.info(
            f"Transfer queued: ${amount_usd} from {from_dex} to {to_dex} ({reason})"
        )
        
        return operation
    
    async def process_pending_transfers(self):
        """
        Process all pending transfers with proper error handling.
        
        Called every cycle by main strategy.
        """
        for transfer in self.pending_transfers[:]:  # Copy list to allow removal
            try:
                await self._execute_transfer(transfer)
                
                # Remove if completed or permanently failed
                if transfer.status in ['completed', 'failed']:
                    self.pending_transfers.remove(transfer)
                    
            except Exception as e:
                logger.error(f"Transfer error for {transfer.id}: {e}")
                transfer.error_message = str(e)
                transfer.retry_count += 1
                
                # Fail after 3 retries
                if transfer.retry_count > 3:
                    transfer.status = 'failed'
                    self.pending_transfers.remove(transfer)
    
    async def _execute_transfer(self, transfer: TransferOperation):
        """
        Execute multi-step transfer process.
        
        Steps:
        1. Withdraw from source DEX
        2. Bridge if chains differ
        3. Deposit to destination DEX
        
        Each step updates transfer.status for tracking.
        """
        # Step 1: Withdraw
        if transfer.status == "pending":
            await self._withdraw(transfer)
        
        # Step 2: Bridge (if needed)
        if transfer.status == "withdrawing" and self._needs_bridge(transfer):
            await self._bridge(transfer)
        
        # Step 3: Deposit
        if transfer.status in ["withdrawing", "bridging"]:
            await self._deposit(transfer)
        
        # Mark complete
        if transfer.status == "depositing":
            transfer.status = "completed"
            transfer.completed_at = datetime.now()
            logger.info(f"Transfer {transfer.id} completed")
    
    async def _withdraw(self, transfer: TransferOperation):
        """Withdraw funds from source DEX"""
        transfer.status = "withdrawing"
        
        client = self.clients[transfer.from_dex]
        
        # Execute withdrawal
        tx_hash = await client.withdraw(
            amount_usd=transfer.amount_usd
        )
        
        transfer.withdrawal_tx = tx_hash
        logger.info(f"Withdrawal initiated: {tx_hash}")
        
        # Wait for confirmation
        await self._wait_for_withdrawal_confirmation(tx_hash)
    
    async def _bridge(self, transfer: TransferOperation):
        """Bridge funds between chains"""
        if not self.bridge_manager:
            raise Exception("Bridge not configured")
        
        transfer.status = "bridging"
        
        from_chain = self._get_chain(transfer.from_dex)
        to_chain = self._get_chain(transfer.to_dex)
        
        # Execute bridge
        bridge_tx = await self.bridge_manager.bridge_funds(
            from_chain=from_chain,
            to_chain=to_chain,
            amount=transfer.amount_usd
        )
        
        transfer.bridge_tx = bridge_tx
        logger.info(f"Bridge initiated: {bridge_tx}")
        
        # Wait for bridge completion (can take 5-30 minutes)
        await self._wait_for_bridge_completion(bridge_tx)
    
    async def _deposit(self, transfer: TransferOperation):
        """Deposit funds to destination DEX"""
        transfer.status = "depositing"
        
        client = self.clients[transfer.to_dex]
        
        # Execute deposit
        tx_hash = await client.deposit(
            amount_usd=transfer.amount_usd
        )
        
        transfer.deposit_tx = tx_hash
        logger.info(f"Deposit initiated: {tx_hash}")
        
        # Wait for confirmation
        await self._wait_for_deposit_confirmation(tx_hash)
    
    def _needs_bridge(self, transfer: TransferOperation) -> bool:
        """Check if DEXs are on different chains"""
        from_chain = self._get_chain(transfer.from_dex)
        to_chain = self._get_chain(transfer.to_dex)
        return from_chain != to_chain
    
    def _get_chain(self, dex: str) -> str:
        """Map DEX to chain"""
        # Mapping of DEX to chain
        chain_map = {
            'lighter': 'arbitrum',
            'grvt': 'ethereum',
            'edgex': 'arbitrum',
            'aster': 'base',
            'backpack': 'solana',
            'paradex': 'ethereum'
        }
        return chain_map.get(dex, 'unknown')
    
    async def _wait_for_withdrawal_confirmation(self, tx_hash: str):
        """Wait for withdrawal to be confirmed"""
        # Poll transaction status
        # Timeout after X minutes
        await asyncio.sleep(30)  # Placeholder
    
    async def _wait_for_bridge_completion(self, bridge_tx: str):
        """Wait for bridge to complete (can be slow)"""
        # Poll bridge status
        # Timeout after max_bridge_time_minutes
        timeout = self.bridge_config.max_bridge_time_minutes * 60
        await asyncio.sleep(timeout)  # Placeholder
    
    async def _wait_for_deposit_confirmation(self, tx_hash: str):
        """Wait for deposit to be confirmed"""
        await asyncio.sleep(30)  # Placeholder
```

**Rationale:**
- State machine pattern for tracking progress
- Retry logic with exponential backoff
- Async operations don't block main loop
- Detailed logging at each step

#### 7.2 Create Bridge Manager

**File:** `/strategies/implementations/funding_arbitrage/operations/bridge_manager.py`

```python
"""
Bridge Manager - Handles cross-chain bridging.

Supports multiple bridge protocols:
- LayerZero
- Axelar
- Wormhole
"""

from ..config import BridgeConfig
from decimal import Decimal

class BridgeManager:
    """
    Abstract bridge operations across protocols.
    
    Provides unified interface regardless of bridge used.
    """
    
    def __init__(self, config: BridgeConfig):
        self.config = config
        
        # Initialize bridge client based on config
        self.client = self._initialize_bridge_client()
    
    def _initialize_bridge_client(self):
        """Load appropriate bridge client"""
        bridge_name = self.config.preferred_bridge
        
        if bridge_name == "layerzero":
            from bridges.layerzero_client import LayerZeroClient
            return LayerZeroClient(api_key=self.config.layerzero_api_key)
        elif bridge_name == "axelar":
            from bridges.axelar_client import AxelarClient
            return AxelarClient(rpc_url=self.config.axelar_rpc_url)
        else:
            raise ValueError(f"Unknown bridge: {bridge_name}")
    
    async def bridge_funds(
        self,
        from_chain: str,
        to_chain: str,
        amount: Decimal
    ) -> str:
        """
        Bridge funds between chains.
        
        Args:
            from_chain: Source chain
            to_chain: Destination chain
            amount: Amount to bridge
        
        Returns:
            Bridge transaction hash
        """
        # Calculate fees
        fee = amount * (self.config.bridge_fee_bps / 10000)
        net_amount = amount - fee
        
        # Execute bridge
        tx_hash = await self.client.bridge(
            from_chain=from_chain,
            to_chain=to_chain,
            amount=net_amount
        )
        
        return tx_hash
    
    async def get_bridge_status(self, tx_hash: str) -> dict:
        """
        Check bridge transaction status.
        
        Returns:
            {
                'status': 'pending' | 'completed' | 'failed',
                'confirmations': int,
                'estimated_time_remaining': int (seconds)
            }
        """
        return await self.client.get_status(tx_hash)
```

**Rationale:**
- Abstraction layer for multiple bridges
- Easy to add new bridge protocols
- Fee calculation built-in

#### Deliverables:
- ✅ Fund transfer manager with retry logic
- ✅ Bridge manager with protocol abstraction
- ✅ Multi-step state machine
- ✅ Error handling and recovery
- ✅ Integration tests with mocked exchanges

---

### **Phase 8: API Client & Testing (Days 19-20)**

Build the funding rate service client and comprehensive tests.

#### 8.1 Create Funding Rate API Client

**File:** `/strategies/implementations/funding_arbitrage/api_client.py`

```python
"""
Funding Rate API Client - Communicates with funding_rate_service.

Wraps all API calls with error handling and retries.
"""

import aiohttp
from typing import List, Dict, Any, Optional
from decimal import Decimal

class FundingRateAPIClient:
    """
    Client for funding_rate_service API.
    
    Endpoints:
    - GET /api/v1/opportunities
    - GET /api/v1/opportunities/best
    - GET /api/v1/funding-rates/compare
    """
    
    def __init__(self, base_url: str):
        """
        Args:
            base_url: Base URL of funding service (e.g., http://localhost:8000)
        """
        self.base_url = base_url.rstrip('/')
        self.session = None
    
    async def _get_session(self):
        """Lazy session creation"""
        if self.session is None:
            self.session = aiohttp.ClientSession()
        return self.session
    
    async def get_opportunities(
        self,
        min_profit: float = 0.001,
        max_oi_usd: Optional[float] = None,
        dexes: Optional[List[str]] = None,
        symbols: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """
        Get arbitrage opportunities.
        
        Args:
            min_profit: Minimum profit threshold
            max_oi_usd: Max OI filter (for point farming)
            dexes: Filter by DEXs
            symbols: Filter by symbols
        
        Returns:
            List of opportunities
        """
        session = await self._get_session()
        
        params = {
            'min_profit': min_profit,
        }
        if max_oi_usd:
            params['max_oi_usd'] = max_oi_usd
        if dexes:
            params['dexes'] = ','.join(dexes)
        if symbols:
            params['symbols'] = ','.join(symbols)
        
        async with session.get(
            f"{self.base_url}/api/v1/opportunities",
            params=params
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
            return data.get('opportunities', [])
    
    async def get_best_opportunity(
        self,
        symbol: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Get single best opportunity.
        
        Args:
            symbol: Filter by symbol
        
        Returns:
            Best opportunity or None
        """
        session = await self._get_session()
        
        params = {}
        if symbol:
            params['symbol'] = symbol
        
        async with session.get(
            f"{self.base_url}/api/v1/opportunities/best",
            params=params
        ) as resp:
            if resp.status == 404:
                return None
            resp.raise_for_status()
            return await resp.json()
    
    async def compare_rates(
        self,
        symbol: str,
        dex1: str,
        dex2: str
    ) -> Dict[str, Decimal]:
        """
        Compare funding rates between two DEXs.
        
        Args:
            symbol: Trading pair
            dex1: First DEX
            dex2: Second DEX
        
        Returns:
            {
                'divergence': Decimal,
                'long_rate': Decimal,
                'short_rate': Decimal,
                'dex1_rate': Decimal,
                'dex2_rate': Decimal
            }
        """
        session = await self._get_session()
        
        async with session.get(
            f"{self.base_url}/api/v1/funding-rates/compare",
            params={'symbol': symbol, 'dex1': dex1, 'dex2': dex2}
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
            
            # Convert to Decimal for precision
            return {
                'divergence': Decimal(str(data['divergence'])),
                'long_rate': Decimal(str(data.get('long_rate', 0))),
                'short_rate': Decimal(str(data.get('short_rate', 0)))
            }
    
    async def close(self):
        """Close HTTP session"""
        if self.session:
            await self.session.close()
```

**Rationale:**
- Async client for non-blocking calls
- Error handling built-in
- Type conversion for Decimal precision

#### 8.2 Create Unit Tests

**File:** `/strategies/tests/test_funding_arbitrage.py`

```python
"""
Unit tests for Funding Arbitrage Strategy.

Tests:
- Position lifecycle
- Rebalancing decisions
- Fund transfers
- Component integration
"""

import pytest
from unittest.mock import Mock, AsyncMock
from strategies.implementations.funding_arbitrage.strategy import FundingArbitrageStrategy
from strategies.implementations.funding_arbitrage.config import FundingArbConfig
from strategies.components.state_manager import InMemoryStateManager

@pytest.fixture
def mock_exchange_clients():
    """Create mock exchange clients"""
    return {
        'lighter': Mock(),
        'grvt': Mock()
    }

@pytest.fixture
def config():
    """Create test configuration"""
    return FundingArbConfig(
        exchanges=['lighter', 'grvt'],
        max_positions=10,
        min_profit=0.001,
        database_url="sqlite:///:memory:",
        funding_api_url="http://localhost:8000"
    )

@pytest.fixture
async def strategy(config, mock_exchange_clients):
    """Create strategy instance with mocks"""
    strategy = FundingArbitrageStrategy(config, mock_exchange_clients)
    
    # Inject in-memory state manager for testing
    strategy.state_manager = InMemoryStateManager()
    
    await strategy.initialize()
    return strategy

@pytest.mark.asyncio
async def test_execute_cycle_with_no_positions(strategy):
    """Test execution cycle with no open positions"""
    result = await strategy.execute_cycle()
    
    assert result.action == StrategyAction.WAIT
    assert "0 actions taken" in result.message

@pytest.mark.asyncio
async def test_position_monitoring(strategy):
    """Test position monitoring phase"""
    # Add mock position
    position = FundingArbPosition(
        id=uuid4(),
        symbol='BTC',
        long_dex='lighter',
        short_dex='grvt',
        size_usd=Decimal('1000'),
        entry_divergence=Decimal('0.01'),
        # ... other fields
    )
    
    await strategy.position_manager.add_position(position)
    
    # Mock API response
    strategy.funding_api.compare_rates = AsyncMock(return_value={
        'divergence': Decimal('0.005'),  # 50% erosion
        'long_rate': Decimal('0.01'),
        'short_rate': Decimal('0.005')
    })
    
    await strategy._monitor_positions()
    
    # Check if rebalance was flagged
    positions = await strategy.position_manager.get_open_positions()
    assert positions[0].rebalance_pending == True

@pytest.mark.asyncio
async def test_rebalancing_execution(strategy):
    """Test rebalancing execution"""
    # Test rebalancing logic
    pass

# More tests...
```

**Rationale:**
- Test each phase independently
- Mock external dependencies
- In-memory state for speed

#### Deliverables:
- ✅ API client with error handling
- ✅ Unit tests for all components
- ✅ Integration tests for full cycle
- ✅ Mock factories for testing
- ✅ Test coverage >80%

---

### **Phase 9: Integration & Documentation (Days 21-22)**

Final integration and documentation.

#### 9.1 Update trading_bot.py

**File:** `/trading_bot.py` (Updated sections)

```python
"""
Update trading_bot.py to support new strategy architecture.

Changes:
- Handle both stateless and stateful strategies
- Pass exchange_clients dict for multi-DEX strategies
- Use execute_cycle() instead of execute_strategy()
"""

async def main():
    # ... existing setup ...
    
    # Create strategy with new factory
    if strategy_name == 'funding_arbitrage':
        # Multi-DEX strategy - create multiple clients
        exchange_clients = {}
        for dex_name in config.exchanges:
            exchange_clients[dex_name] = ExchangeFactory.create_client(
                dex_name, dex_configs[dex_name]
            )
        
        strategy = StrategyFactory.create_strategy(
            strategy_name=strategy_name,
            config_dict=config_dict,
            exchange_clients=exchange_clients
        )
    else:
        # Single-DEX strategy
        exchange_client = ExchangeFactory.create_client(
            config.exchange, exchange_config
        )
        
        strategy = StrategyFactory.create_strategy(
            strategy_name=strategy_name,
            config_dict=config_dict,
            exchange_client=exchange_client
        )
    
    # Initialize strategy
    await strategy.initialize()
    
    # Main loop
    while True:
        try:
            # Execute cycle (renamed from execute_strategy)
            result = await strategy.execute_cycle()
            
            # Handle result
            if result.action != StrategyAction.WAIT:
                logger.info(f"Action: {result.action}, {result.message}")
            
            # Wait before next cycle
            await asyncio.sleep(result.wait_time or 60)
            
        except KeyboardInterrupt:
            break
        except Exception as e:
            logger.error(f"Error in main loop: {e}")
            await asyncio.sleep(60)
    
    # Cleanup
    await strategy.cleanup()
```

#### 9.2 Create Migration Guide

**File:** `/docs/STRATEGY_MIGRATION_GUIDE.md`

```markdown
# Strategy Migration Guide

## For Existing Strategy Developers

### Grid Strategy Migration Example

**Before (Old Architecture):**
```python
class GridStrategy(BaseStrategy):
    async def execute_strategy(self, market_data):
        # Implementation
        pass
```

**After (New Architecture):**
```python
from strategies.categories.stateless_strategy import StatelessStrategy

class GridStrategy(StatelessStrategy):
    async def execute_cycle(self):
        # Implementation (same logic)
        pass
```

### Key Changes:
1. Import from `strategies.categories.stateless_strategy`
2. Rename `execute_strategy()` → `execute_cycle()`
3. Use Pydantic config models
4. Remove manual parameter validation

### Benefits:
- Automatic config validation
- Better error messages
- Type safety
- Easier testing
```

#### 9.3 Create Architecture Documentation

**File:** `/docs/STRATEGY_ARCHITECTURE.md`

```markdown
# Strategy Architecture Documentation

## Overview

3-Level Hierarchy + Composition Hybrid pattern.

## Levels

### Level 1: BaseStrategy
Minimal interface all strategies implement.

### Level 2: Categories
- **StatelessStrategy**: For simple strategies (Grid, TWAP)
- **StatefulStrategy**: For complex strategies (Funding Arb, Market Making)

### Level 3: Implementations
Concrete strategy packages with full functionality.

## Adding a New Strategy

### Simple Strategy (Stateless)

1. Create package: `/strategies/implementations/my_strategy/`
2. Extend `StatelessStrategy`
3. Implement `should_execute()` and `execute_strategy()`
4. Register in factory

### Complex Strategy (Stateful)

1. Create package with sub-components
2. Extend `StatefulStrategy`
3. Override factory methods if needed
4. Implement full `execute_cycle()`
5. Register in factory

## Testing

Use in-memory state manager for unit tests.
```

#### Deliverables:
- ✅ trading_bot.py updated
- ✅ Migration guide written
- ✅ Architecture documentation
- ✅ Example configs for both strategies
- ✅ Deployment guide

---

### **Phase 10: Deployment & Validation (Days 23-25)**

Deploy and validate the new system.

#### 10.1 Create Deployment Checklist

```markdown
# Deployment Checklist

## Pre-Deployment
- [ ] All tests passing
- [ ] Database migrations run
- [ ] Configs validated
- [ ] API keys configured
- [ ] Bridge configuration tested

## Deployment Steps
1. [ ] Backup current database
2. [ ] Run database migrations
3. [ ] Deploy new code
4. [ ] Start with Grid strategy (validate backward compat)
5. [ ] Test funding service integration
6. [ ] Start funding arbitrage with small positions
7. [ ] Monitor for 24 hours

## Rollback Plan
- Database rollback script ready
- Old code version tagged
- Rollback procedure documented

## Validation
- [ ] Grid strategy still works
- [ ] Funding arbitrage opens positions
- [ ] Rebalancing triggers correctly
- [ ] Fund transfers execute
- [ ] Logging comprehensive
- [ ] No errors in 24h
```

#### 10.2 Create Monitoring Dashboard

**Metrics to Track:**
- Open positions per strategy
- Rebalancing frequency
- Fund transfer success rate
- API response times
- Error rates
- PnL per position

#### Deliverables:
- ✅ Deployment checklist
- ✅ Monitoring setup
- ✅ Validation completed
- ✅ Documentation finalized
- ✅ Team training completed

---

## 📊 Summary Timeline

| Phase | Days | Key Deliverables | Status |
|-------|------|------------------|--------|
| 0: Preparation | 1 | Backup, migrations ready | ⏳ |
| 1: Foundation | 2-3 | Base classes, components | ⏳ |
| 2: Categories | 4-5 | Stateless/Stateful categories | ⏳ |
| 3: Components | 6-7 | Position/State managers | ⏳ |
| 4: Grid Migration | 8 | Grid strategy working | ⏳ |
| 5: Funding Arb Base | 9-12 | Main strategy scaffold | ⏳ |
| 6: Rebalancing | 13-15 | Pluggable rebalance strategies | ⏳ |
| 7: Fund Transfers | 16-18 | Cross-DEX transfers working | ⏳ |
| 8: Testing | 19-20 | Comprehensive test suite | ⏳ |
| 9: Integration | 21-22 | Full system integration | ⏳ |
| 10: Deployment | 23-25 | Production deployment | ⏳ |

**Total Duration:** 25 days (~5 weeks)

---

## 🎯 Success Criteria

### Phase 1-4 Success:
- ✅ Grid strategy works with zero changes to behavior
- ✅ All existing tests pass
- ✅ New architecture is backward compatible

### Phase 5-8 Success:
- ✅ Funding arbitrage can open positions
- ✅ Rebalancing triggers correctly
- ✅ Fund transfers execute without errors
- ✅ All components have >80% test coverage

### Phase 9-10 Success:
- ✅ Both strategies run simultaneously
- ✅ No performance degradation
- ✅ Error rate <1%
- ✅ Documentation complete
- ✅ Team can add new strategies independently

---

## 🚨 Risk Mitigation

### Risk: Database Migration Fails
**Mitigation:** Test migrations on copy of production DB first

### Risk: Backward Compatibility Breaks
**Mitigation:** Grid strategy validation in Phase 4

### Risk: Fund Transfers Lose Funds
**Mitigation:** Test with small amounts first, comprehensive error handling

### Risk: Performance Degradation
**Mitigation:** Load testing before production deployment

### Risk: Complex System Too Hard to Maintain
**Mitigation:** Comprehensive documentation, clear component boundaries

---

## 📝 Notes

### Database Choice Justification
**Chosen:** PostgreSQL (extend funding_rate_service DB)

**Reasons:**
1. Already have PostgreSQL infrastructure
2. Single source of truth
3. Easy to query positions + funding rates together
4. Better for multi-instance deployment (vs SQLite)

### Why Not Microservices for Strategies?
**Answer:** Latency is critical for trading decisions (milliseconds matter). Microservices add 10-100ms+ overhead. Strategies need instant access to positions and account state. Monolith is correct choice for execution layer.

### Component Reusability
All components in `/strategies/components/` can be reused by future strategies:
- PositionManager
- StateManager
- API clients
- Fund transfer logic

---