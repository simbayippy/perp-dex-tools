# Exchange Client Refactoring Plan

> **Note**: This plan applies to multiple exchanges (Lighter, Aster, Backpack) and provides a generalized template for refactoring large exchange client implementations.

## Overview

Multiple exchange clients have grown significantly in size, making them difficult to maintain, test, and extend:

- **Lighter**: 2,403 lines, 52 methods
- **Aster**: 1,772 lines, 39 methods  
- **Backpack**: 1,613 lines, 42 methods

This document outlines a **generalized refactoring plan** to split large exchange clients into modular package structures. The plan provides a standard template that can be applied to **any exchange**, with specific migration guidance for Lighter, Aster, and Backpack.

## Current State Analysis

### Exchange Statistics

| Exchange | Lines | Methods | Status |
|----------|-------|---------|--------|
| **Lighter** | 2,403 | 52 | 🔴 Needs refactoring |
| **Aster** | 1,772 | 39 | 🟡 Should refactor |
| **Backpack** | 1,613 | 42 | 🟡 Should refactor |

### Common Responsibilities (All Exchanges)

All exchange clients share similar responsibilities, making a generalized refactoring approach possible:

1. **Core Client & Connection** (~100-200 lines)
   - Initialization, validation, connection management
   - SDK/client setup
   - Credential handling

2. **Market Data & Configuration** (~300-500 lines)
   - Market ID/symbol lookup and caching
   - Order book depth fetching
   - BBO prices
   - Market metadata management
   - Contract attributes

3. **Order Management** (~400-600 lines)
   - Place limit/market orders
   - Cancel orders
   - Order status tracking
   - Order info queries
   - Inactive order lookup (some exchanges)

4. **Position Management** (~400-700 lines)
   - Position snapshots
   - Position streaming updates
   - Funding calculations
   - Position enrichment with live data
   - Position open time tracking

5. **Account Management** (~200-400 lines)
   - Balance queries
   - P&L calculations
   - Leverage info/queries
   - User stats handling
   - Min order notional

6. **WebSocket Integration** (~150-300 lines)
   - Order update callbacks
   - Liquidation notifications
   - Position stream updates
   - User stats updates

7. **Utilities** (~200-300 lines)
   - Symbol normalization
   - Quantity multipliers
   - Contract ID resolution
   - Decimal helpers
   - Price/quantity formatting

## Standard Package Structure Template

This structure can be applied to **any exchange**. Each exchange should follow this pattern:

```
exchange_clients/{exchange}/
├── __init__.py                 # Public API (exports main client)
├── common.py                   # ✅ Already exists (exchange-specific utilities)
├── funding_adapter.py          # ✅ Already exists
├── websocket_manager.py        # ✅ Already exists (if applicable)
│
├── client/                     # NEW: Client package (contains all client logic)
│   ├── __init__.py            # Exports: from .core import {Exchange}Client
│   ├── core.py                # Main client class (orchestrator, ~500-600 lines)
│   │
│   ├── managers/               # Manager classes (specialized responsibilities)
│   │   ├── __init__.py
│   │   ├── market_data.py     # Market data & configuration (~400-600 lines)
│   │   ├── order_manager.py   # Order management (~600-800 lines)
│   │   ├── position_manager.py # Position management (~400-500 lines)
│   │   ├── account_manager.py # Account management (~250-350 lines)
│   │   └── websocket_handlers.py # WebSocket callbacks (~250-300 lines)
│   │
│   └── utils/                  # Shared utilities for client components
│       ├── __init__.py
│       ├── caching.py         # Market/symbol cache, position cache
│       ├── converters.py      # Order info builders, snapshot builders
│       └── helpers.py         # Decimal helpers, validation helpers
```

**Key Design Decisions:**
- `client/` is a **package**, not a single file
- `client/core.py` contains the main `{Exchange}Client` class that implements `BaseExchangeClient`
- `client/__init__.py` is lightweight - just exports the main class
- Managers are in `client/managers/` subdirectory for clarity
- Utils are in `client/utils/` subdirectory
- No redundant caches - single source of truth (e.g., `_latest_orders` only, no `orders_cache`)

### Exchange-Specific Variations

**Lighter**:
- Largest codebase (2,403 lines)
- Most complex position management (funding calculations, position open time)
- Extensive market ID caching

**Aster**:
- Complex leverage management (get/set leverage)
- Custom signature generation
- Position open time tracking

**Backpack**:
- Precision inference from prices
- Complex post-only price computation
- Symbol precision caching

## Generalized Refactoring Strategy

This strategy applies to **all exchanges** (Lighter, Aster, Backpack). Each phase can be completed per exchange independently.

### Phase 1: Extract Utilities (Low Risk)
**Goal**: Move helper functions to a `utils/` package first

**Files to Create** (per exchange):
- `{exchange}/client/utils/__init__.py`
- `{exchange}/client/utils/caching.py` - Market/symbol cache, order cache, position cache
- `{exchange}/client/utils/converters.py` - Order info builders, snapshot builders
- `{exchange}/client/utils/helpers.py` - Decimal helpers, validation helpers, formatters

**Exchange-Specific Utilities**:

**Lighter**:
- `_decimal_or_none()` → `helpers.py`
- `_build_order_info_from_payload()` → `converters.py`
- `_build_snapshot_from_raw()` → `converters.py`
- Market ID cache logic → `caching.py`

**Aster**:
- `_to_decimal()` → `helpers.py`
- `_to_internal_symbol()` → `helpers.py`
- Symbol/contract ID resolution → `caching.py`

**Backpack**:
- `_to_decimal()`, `_get_decimal_places()` → `helpers.py`
- `_quantize_quantity()`, `_format_decimal()` → `helpers.py`
- `_infer_precision_from_prices()` → `helpers.py`
- Symbol precision cache → `caching.py`

**Benefits**:
- Reduces client.py by ~200-300 lines per exchange
- Makes utilities reusable and testable
- Low risk (no API changes)

### Phase 2: Extract Market Data Module (Medium Risk)
**Goal**: Move all market data fetching and configuration logic

**Files to Create** (per exchange):
- `{exchange}/client/client/market_data.py`

**Common Methods to Move** (all exchanges):
- `get_order_book_depth()` - Order book fetching
- `fetch_bbo_prices()` - Best bid/offer
- `get_order_price()` - Order price calculation (when present)
- `get_contract_attributes()` - Contract metadata

**Exchange-Specific Methods**:

**Lighter**:
- `_get_market_id_for_symbol()` - Market ID lookup with caching
- `_get_market_config()` - Market configuration
- `_cache_market_metadata()` - Metadata caching
- `_apply_market_metadata()` - Metadata application

**Aster**:
- `_ensure_exchange_symbol()` - Symbol normalization
- Market symbol mapping logic

**Backpack**:
- `_fetch_depth_snapshot()` - Depth snapshot fetching
- `_ensure_exchange_symbol()` - Symbol normalization
- Symbol precision management

**Dependencies**:
- Uses exchange-specific API clients (`api_client`, `public_client`, etc.)
- Uses `self.config`, `self.logger`
- Uses caching utilities from `utils/caching.py`
- May use `self.ws_manager` for WebSocket order book

**Design Pattern** (Generalized):
```python
class {Exchange}MarketData:
    def __init__(self, api_client, config, logger, cache_manager, ws_manager=None):
        self.api_client = api_client  # Exchange-specific client
        self.config = config
        self.logger = logger
        self.cache = cache_manager  # Market/symbol cache
        self.ws_manager = ws_manager  # Optional WebSocket manager
    
    async def get_order_book(self, contract_id: str, levels: int) -> Dict:
        # Try WebSocket first, fall back to REST
        pass
    
    async def fetch_bbo_prices(self, contract_id: str) -> Tuple[Decimal, Decimal]:
        # Implementation
        pass
```

### Phase 3: Extract Order Manager (Medium Risk)
**Goal**: Move all order-related operations

**Files to Create** (per exchange):
- `{exchange}/client/client/order_manager.py`

**Common Methods to Move** (all exchanges):
- `place_limit_order()` - Limit order placement
- `place_market_order()` - Market order placement
- `cancel_order()` - Order cancellation
- `get_order_info()` - Order status query
- `get_active_orders()` - Active orders query

**Exchange-Specific Methods**:

**Lighter**:
- `_build_order_info_from_payload()` - Order info conversion
- `_lookup_inactive_order()` - Historical order lookup
- `_await_order_update()` - Order update waiting
- `_notify_order_update()` - Order update notification
- `_fetch_orders_with_retry()` - REST order fetching
- `resolve_client_order_id()` - Order ID resolution

**Aster**:
- Order conversion logic (if present)
- Order retry logic

**Backpack**:
- `_compute_post_only_price()` - Post-only price computation
- Order quantity quantization logic

**Dependencies**:
- Uses exchange-specific clients (`lighter_client`, `account_client`, etc.)
- Uses `self.config`, `self.logger`
- Uses `self._latest_orders`, `self._order_update_events` (caching)
- Uses converters from `utils/converters.py`

**Design Pattern** (Generalized):
```python
class {Exchange}OrderManager:
    def __init__(self, exchange_client, config, logger, order_cache, converters):
        self.exchange_client = exchange_client  # Exchange-specific client
        self.config = config
        self.logger = logger
        self.cache = order_cache  # Order tracking cache
        self.converters = converters
    
    async def place_limit_order(self, contract_id, quantity, price, side, reduce_only):
        # Implementation
        pass
```

### Phase 4: Extract Position Manager (Medium Risk)
**Goal**: Move all position-related operations

**Files to Create** (per exchange):
- `{exchange}/client/client/position_manager.py`

**Common Methods to Move** (all exchanges):
- `get_account_positions()` - Position size query
- `get_position_snapshot()` - Position snapshot
- `_handle_positions_stream_update()` - WebSocket position updates (if applicable)

**Exchange-Specific Methods**:

**Lighter**:
- `_snapshot_from_cache()` - Cached snapshot retrieval
- `_build_snapshot_from_raw()` - Snapshot building
- `_enrich_snapshot_with_live_market_data()` - Live data enrichment
- `_refresh_positions_via_rest()` - REST position refresh
- `_get_detailed_positions()` - Detailed position fetching
- `_fetch_positions_with_retry()` - REST position fetching with retry
- `_get_cumulative_funding()` - Funding calculation
- `_get_position_open_time()` - Position open time estimation
- `_get_live_mark_price()` - Live mark price from WebSocket

**Aster**:
- `_get_position_open_time()` - Position open time estimation
- `_get_cumulative_funding()` - Funding calculation
- Position snapshot building logic

**Backpack**:
- Position snapshot building logic
- Position update handling

**Dependencies**:
- Uses exchange-specific API clients (`account_api`, `order_api`, etc.)
- Uses `self.ws_manager` (when available)
- Uses `self.config`, `self.logger`
- Uses `self._raw_positions`, `self._positions_lock` (caching)
- Uses converters from `utils/converters.py`

**Design Pattern** (Generalized):
```python
class {Exchange}PositionManager:
    def __init__(self, account_api, order_api, ws_manager, config, logger, position_cache, converters):
        self.account_api = account_api  # Exchange-specific API
        self.order_api = order_api  # May be None for some exchanges
        self.ws_manager = ws_manager  # Optional WebSocket manager
        self.config = config
        self.logger = logger
        self.cache = position_cache
        self.converters = converters
    
    async def get_position_snapshot(self, symbol: str) -> Optional[ExchangePositionSnapshot]:
        # Implementation
        pass
```

### Phase 5: Extract Account Manager (Low Risk)
**Goal**: Move account-related queries

**Files to Create** (per exchange):
- `{exchange}/client/client/account_manager.py`

**Common Methods to Move** (all exchanges):
- `get_account_balance()` - Balance query
- `get_account_pnl()` - P&L calculation (when present)
- `get_min_order_notional()` - Min notional query

**Exchange-Specific Methods**:

**Lighter**:
- `get_total_asset_value()` - Total asset value
- `get_leverage_info()` - Leverage information
- `_handle_user_stats_update()` - WebSocket user stats updates

**Aster**:
- `get_account_leverage()` - Get leverage
- `set_account_leverage()` - Set leverage
- `get_leverage_info()` - Leverage information with brackets

**Backpack**:
- `get_leverage_info()` - Leverage information
- `_extract_available_balance()` - Balance extraction helper

**Dependencies**:
- Uses exchange-specific API clients (`account_api`, `order_api`, etc.)
- Uses `self.config`, `self.logger`
- Uses `self._user_stats`, `self._user_stats_lock` (caching, when applicable)

**Design Pattern** (Generalized):
```python
class {Exchange}AccountManager:
    def __init__(self, account_api, order_api, config, logger, user_stats_cache=None):
        self.account_api = account_api  # Exchange-specific API
        self.order_api = order_api  # May be None for some exchanges
        self.config = config
        self.logger = logger
        self.cache = user_stats_cache  # Optional user stats cache
    
    async def get_account_balance(self) -> Optional[Decimal]:
        # Implementation
        pass
```

### Phase 6: Extract WebSocket Handlers (Low Risk)
**Goal**: Move WebSocket callback handlers

**Files to Create** (per exchange):
- `{exchange}/client/client/websocket_handlers.py`

**Common Methods to Move** (all exchanges):
- `_handle_websocket_order_update()` - Order update handler
- `handle_liquidation_notification()` - Liquidation handler (when supported)

**Note**: Position and user stats handlers are typically moved to respective managers, but some exchanges may have exchange-specific WebSocket logic here.

**Dependencies**:
- Delegates to order_manager, position_manager, account_manager
- Uses `self.logger`

**Design Pattern** (Generalized):
```python
class {Exchange}WebSocketHandlers:
    def __init__(self, order_manager, position_manager, account_manager, logger):
        self.order_manager = order_manager
        self.position_manager = position_manager
        self.account_manager = account_manager
        self.logger = logger
    
    def handle_order_update(self, order_data: Dict[str, Any] | List[Dict[str, Any]]):
        # Delegates to order_manager
        pass
    
    async def handle_liquidation(self, notifications: List[Dict[str, Any]]):
        # Implementation (when supported)
        pass
```

### Phase 7: Refactor Core Client (High Risk - Final Step)
**Goal**: Create lean orchestrator that delegates to managers

**New `client/core.py`** (per exchange):
- Contains the main `{Exchange}Client` class that inherits from `BaseExchangeClient`
- Connection management (`connect`, `disconnect`)
- Client initialization (exchange-specific, e.g., `_initialize_lighter_client`)
- Manager initialization and composition
- WebSocket manager setup
- Thin wrapper methods that delegate to managers (~500-600 lines)

**New `client/__init__.py`** (lightweight export):
- Just exports: `from .core import {Exchange}Client`
- Follows Python best practices (lightweight `__init__.py`)

**Design Pattern** (Generalized):
```python
class {Exchange}Client(BaseExchangeClient):
    def __init__(self, config, ...):
        super().__init__(config)
        # Initialize shared caches
        self._market_cache = {}
        self._order_cache = {}
        self._position_cache = {}
        
        # Initialize managers (will be fully initialized in connect())
        self.market_data = None
        self.order_manager = None
        self.position_manager = None
        self.account_manager = None
        self.ws_handlers = None
    
    async def connect(self) -> None:
        # Initialize exchange-specific clients
        # Then initialize managers with those clients
        self.market_data = {Exchange}MarketData(...)
        self.order_manager = {Exchange}OrderManager(...)
        # ... etc
    
    # Delegate methods
    async def place_limit_order(self, ...):
        return await self.order_manager.place_limit_order(...)
    
    async def get_position_snapshot(self, ...):
        return await self.position_manager.get_position_snapshot(...)
```

## Backward Compatibility Strategy

### Import Path Preservation
The main client class remains accessible via the same import path:

```python
# exchange_clients/lighter/__init__.py
from .client import LighterClient

__all__ = ["LighterClient"]
```

### Internal Structure
The `client/` package structure:

```python
# exchange_clients/lighter/client/__init__.py
# Lightweight export (follows Python best practices)
from .core import LighterClient

__all__ = ["LighterClient"]
```

The actual implementation is in `client/core.py`:
```python
# exchange_clients/lighter/client/core.py
class LighterClient(BaseExchangeClient):
    # Main client implementation that delegates to managers
    pass
```

**Why `core.py` instead of `client.py`?**
- `client/` is a package directory
- `client/__init__.py` should be lightweight (just exports)
- `client/core.py` contains the actual implementation
- This follows Python packaging best practices

## Dependency Injection Pattern

### Shared State Management
Managers share common dependencies via the main client:

```python
class LighterClient(BaseExchangeClient):
    def __init__(self, config, ...):
        super().__init__(config)
        
        # Shared SDK clients (initialized in connect())
        self.lighter_client = None
        self.api_client = None
        self.account_api = None
        self.order_api = None
        
        # Shared caches (single source of truth)
        self._market_id_cache = MarketIdCache()  # From utils/caching.py
        self._latest_orders: Dict[str, OrderInfo] = {}  # OrderInfo objects only
        self._raw_positions: Dict[str, Dict[str, Any]] = {}
        self._user_stats: Optional[Dict[str, Any]] = None
        
        # Manager references (initialized in connect())
        self.market_data: Optional[LighterMarketData] = None
        self.order_manager: Optional[LighterOrderManager] = None
        # ... etc
    
    async def connect(self) -> None:
        # Initialize SDK clients first
        self.api_client = ApiClient(...)
        await self._initialize_lighter_client()
        
        # Initialize managers with shared dependencies
        self.market_data = LighterMarketData(
            api_client=self.api_client,
            config=self.config,
            logger=self.logger,
            market_id_cache=self._market_id_cache,
            ...
        )
        self.market_data.set_client_references(...)  # Set client attribute references
        
        self.order_manager = LighterOrderManager(
            lighter_client=self.lighter_client,
            latest_orders=self._latest_orders,  # Single cache, not redundant
            ...
        )
        self.order_manager.set_client_references(...)  # Set client attribute references
        # ... initialize other managers
```

**Key Pattern:**
- Single cache per data type (e.g., `_latest_orders` only, no redundant `orders_cache`)
- Managers receive references to client caches (not copies)
- Managers use `set_client_references()` to access client attributes (e.g., multipliers)
- Managers initialized in `connect()` after SDK clients are ready

### Lazy Initialization
Some managers may need lazy initialization (e.g., after `connect()`):

```python
async def connect(self) -> None:
    # Initialize SDK clients
    self.api_client = ApiClient(...)
    self.lighter_client = await self._initialize_lighter_client()
    
    # Now initialize managers that need SDK clients
    self.order_manager.lighter_client = self.lighter_client
    self.position_manager.account_api = self.account_api
    # ...
```

## Testing Strategy

### Unit Tests Per Module
Each module will have its own test file:

```
tests/exchange_clients/lighter/
├── test_client.py              # Integration tests
├── test_market_data.py         # Market data tests
├── test_order_manager.py       # Order manager tests
├── test_position_manager.py    # Position manager tests
├── test_account_manager.py    # Account manager tests
└── test_websocket_handlers.py # WebSocket handler tests
```

### Mock Dependencies
Managers can be tested independently with mocked dependencies:

```python
async def test_order_manager_place_limit_order():
    mock_client = Mock()
    mock_config = Mock()
    mock_logger = Mock()
    manager = LighterOrderManager(mock_client, mock_config, mock_logger, ...)
    
    result = await manager.place_limit_order(...)
    assert result.success
```

## Migration Plan

### Per-Exchange Migration Strategy

Each exchange can be refactored **independently**. We recommend starting with **Lighter** (largest, most complex), then **Aster**, then **Backpack**.

### Step-by-Step Approach (Per Exchange)

1. **Phase 1: Utilities** (1-2 days per exchange)
   - Extract utility functions
   - Update imports in `client.py`
   - Run tests to verify

2. **Phase 2: Market Data** (2-3 days per exchange)
   - Extract market data module
   - Update `client.py` to use manager
   - Test market data functionality

3. **Phase 3: Order Manager** (3-4 days per exchange)
   - Extract order manager
   - Update `client.py` to delegate
   - Test order operations

4. **Phase 4: Position Manager** (3-4 days per exchange)
   - Extract position manager
   - Update `client.py` to delegate
   - Test position operations

5. **Phase 5: Account Manager** (1-2 days per exchange)
   - Extract account manager
   - Update `client.py` to delegate
   - Test account queries

6. **Phase 6: WebSocket Handlers** (1-2 days per exchange)
   - Extract WebSocket handlers
   - Update `client.py` to use handlers
   - Test WebSocket callbacks

7. **Phase 7: Core Refactor** (2-3 days per exchange)
   - Refactor main client to orchestrator
   - Final testing and cleanup
   - Documentation updates

### Recommended Migration Order

1. **Lighter** (2,403 lines) ✅ **COMPLETED**
   - **Actual Time**: ~2-3 days
   - **Result**: Reduced to 580 lines (`core.py`) + modular managers
   - **Key Learnings**: 
     - Use `client/core.py` + `client/__init__.py` pattern (not `client.py`)
     - Remove redundant caches (single source of truth)
     - Managers in `client/managers/` subdirectory
     - Utils in `client/utils/` subdirectory

2. **Aster** (1,772 lines) - Medium complexity
   - **Estimated Time**: 8-12 days (can reuse Lighter patterns)
   - **Rationale**: Similar structure to Lighter, can follow same pattern

3. **Backpack** (1,613 lines) - Medium complexity
   - **Estimated Time**: 8-12 days (can reuse Lighter patterns)
   - **Rationale**: Different SDK patterns, but similar refactoring approach

**Total Estimated Time**: 16-24 days remaining (Lighter patterns established)

### Parallelization Strategy

After completing **Lighter** refactoring:
- Use Lighter as reference implementation
- Aster and Backpack can be refactored in parallel by different developers
- Share common patterns and utilities discovered during Lighter refactoring

## Benefits of Refactoring

### Maintainability
- **Single Responsibility**: Each module has one clear purpose
- **Easier Navigation**: Find code by feature (orders, positions, etc.)
- **Reduced Cognitive Load**: Smaller files are easier to understand

### Testability
- **Isolated Testing**: Test each manager independently
- **Mock-Friendly**: Easy to mock dependencies
- **Faster Tests**: Run only relevant test suites

### Extensibility
- **Easy to Add Features**: Add new methods to appropriate manager
- **Reusable Components**: Managers can be reused in other contexts
- **Clear Interfaces**: Well-defined boundaries between components

### Performance
- **No Runtime Overhead**: Just code organization, no performance impact
- **Better Caching**: Centralized cache management
- **Optimization Opportunities**: Isolated modules easier to optimize

## Risks & Mitigation

### Risk 1: Breaking Changes
**Mitigation**: 
- Maintain backward compatibility
- Comprehensive integration tests
- Gradual migration with feature flags if needed

### Risk 2: Circular Dependencies
**Mitigation**:
- Clear dependency hierarchy (core → managers → utils)
- Dependency injection pattern
- Avoid managers depending on each other directly

### Risk 3: Increased Complexity
**Mitigation**:
- Clear documentation
- Simple delegation pattern
- Consistent naming conventions

## WebSocket Manager Refactoring

### Current State
Multiple exchanges have large WebSocket managers:
- **Lighter**: 982 lines ✅ **COMPLETED** (refactored to ~1,353 lines organized)
- **Aster**: 904 lines
- **Backpack**: 680 lines

All have mixed responsibilities:
- Connection management
- Order book management (when applicable)
- Message handling
- Market switching logic (when applicable)

### Standard WebSocket Package Structure Template

This structure can be applied to **any exchange**. Each exchange should follow this pattern:

```
exchange_clients/{exchange}/
└── websocket/                   # NEW: WebSocket package
    ├── __init__.py             # Exports: from .manager import {Exchange}WebSocketManager
    ├── manager.py              # Main orchestrator (~300-400 lines)
    ├── connection.py           # Connection, reconnection, session management (~150-200 lines)
    ├── message_handler.py      # Message parsing, routing, type detection (~200-300 lines)
    ├── order_book.py           # Order book state, validation, BBO extraction (~200-300 lines, when applicable)
    └── market_switcher.py      # Market switching, subscription management (~200-300 lines, when applicable)
```

**Key Design Decisions:**
- `websocket/` is a **package**, not a single file
- `websocket/manager.py` contains the main `{Exchange}WebSocketManager` class that inherits from `BaseWebSocketManager`
- `websocket/__init__.py` is lightweight - just exports the main manager class
- Components are separated by responsibility: connection, messages, order book, market switching
- Callback pattern maintained: `websocket_manager` detects events, `client/managers/websocket_handlers` contains business logic

### Exchange-Specific Considerations

**Lighter** ✅ **COMPLETED**:
- Complex order book state management (sequence validation, gap detection)
- Market switching with subscription management
- Application-level ping/pong (server handles, no protocol-level heartbeat needed)
- **Structure**: 6 files, ~1,353 lines total (from 982 lines monolithic)

**Aster**:
- Order book management (depth stream + book ticker)
- Market switching (separate book ticker and depth streams)
- Protocol-level ping/pong (websockets library handles)

**Backpack**:
- Simpler structure
- May not need separate market switcher
- Different WebSocket library patterns

### Refactoring Benefits
- **Separation of Concerns**: Connection vs state vs routing vs switching
- **Easier Testing**: Mock connection, test order book logic separately
- **Better Maintainability**: Smaller, focused files (each < 400 lines)
- **Reusable Patterns**: Common WebSocket patterns can be extracted
- **Clear Boundaries**: Transport layer (websocket/) vs business logic (client/managers/websocket_handlers)

### WebSocket Refactoring Strategy (Generalized)

This strategy applies to **all exchanges** (Lighter, Aster, Backpack). Each phase can be completed per exchange independently.

#### Phase 1: Extract Connection Management
**Goal**: Isolate connection lifecycle, reconnection, and session management

**Files to Create**:
- `{exchange}/websocket/connection.py`

**Common Methods to Move**:
- `_get_session()` / `_close_session()` - Session management
- `_proxy_kwargs()` - Proxy configuration
- `open_connection()` / `cleanup_current_ws()` - Connection lifecycle
- `reconnect()` - Reconnection with exponential backoff

**Constants to Extract**:
- `RECONNECT_BACKOFF_INITIAL` / `RECONNECT_BACKOFF_MAX` - Reconnection timing
- `RECEIVE_TIMEOUT` - Message receive timeout (if applicable)
- **Note**: `HEARTBEAT_INTERVAL` may not be needed if server handles ping/pong

#### Phase 2: Extract Order Book Management (When Applicable)
**Goal**: Isolate order book state, validation, and BBO extraction

**Files to Create**:
- `{exchange}/websocket/order_book.py`

**Common Methods to Move**:
- `update_order_book()` - Update order book state
- `validate_order_book_offset()` / `validate_order_book_integrity()` - Validation
- `get_best_levels()` / `get_order_book()` - BBO extraction
- `reset_order_book()` - State reset
- `cleanup_old_order_book_levels()` - Memory management

**State to Manage**:
- Order book dictionary (bids/asks)
- Best bid/ask tracking
- Snapshot loaded flag
- Sequence/offset tracking

#### Phase 3: Extract Message Handler
**Goal**: Isolate message parsing, routing, and type detection

**Files to Create**:
- `{exchange}/websocket/message_handler.py`

**Common Methods to Move**:
- `process_message()` - Main message processing loop
- `_handle_order_book_snapshot()` / `_handle_order_book_update()` - Order book messages
- `_handle_order_update()` - Order update messages
- `dispatch_liquidations()` / `dispatch_positions()` / `dispatch_user_stats()` - Callback dispatching

**Responsibilities**:
- Parse raw WebSocket messages
- Detect message types
- Route to appropriate handlers
- Extract payloads for callbacks
- Handle protocol-level ping/pong (if applicable)

#### Phase 4: Extract Market Switcher (When Applicable)
**Goal**: Isolate market switching and subscription management

**Files to Create**:
- `{exchange}/websocket/market_switcher.py`

**Common Methods to Move**:
- `lookup_market_id()` / `validate_market_switch_needed()` - Market validation
- `subscribe_market()` / `unsubscribe_market()` - Subscription management
- `subscribe_channels()` - Initial channel subscription
- `update_market_config()` - Config synchronization

**Responsibilities**:
- Market ID lookup (may require API calls)
- Subscription/unsubscription logic
- Auth token generation (when needed)
- Config synchronization

#### Phase 5: Create Main Manager
**Goal**: Create orchestrator that composes all components

**Files to Create**:
- `{exchange}/websocket/manager.py`
- `{exchange}/websocket/__init__.py`

**Main Manager Responsibilities**:
- Initialize all components
- Orchestrate connection lifecycle (`connect()` / `disconnect()`)
- Delegate to components for specialized tasks
- Maintain callback dispatching interface
- Update component references after reconnection

**Design Pattern**:
```python
class {Exchange}WebSocketManager(BaseWebSocketManager):
    def __init__(self, config, callbacks):
        super().__init__()
        self.connection = {Exchange}WebSocketConnection(config)
        self.order_book = {Exchange}OrderBook()  # If applicable
        self.message_handler = {Exchange}MessageHandler(config, callbacks, ...)
        self.market_switcher = {Exchange}MarketSwitcher(config, ...)  # If applicable
    
    async def connect(self):
        await self.connection.open_connection()
        await self.market_switcher.subscribe_channels(...)
        self._update_component_references()
        # Start listener loop
    
    # Delegate methods
    def get_order_book(self, levels):
        return self.order_book.get_order_book(levels)
```

### Backward Compatibility Strategy

**Import Path Preservation**:
```python
# exchange_clients/lighter/websocket/__init__.py
from .manager import LighterWebSocketManager

__all__ = ["LighterWebSocketManager"]
```

**Usage** (unchanged):
```python
from exchange_clients.lighter.websocket import LighterWebSocketManager
```

### Key Learnings from Lighter Refactoring

1. **Callback Pattern Maintained**: 
   - `websocket/` package handles transport (detects events)
   - `client/managers/websocket_handlers.py` handles business logic (processes events)
   - Clear separation: transport vs business logic

2. **Component Initialization**:
   - Components initialized in `manager.__init__()`
   - References updated after connection (`_update_component_references()`)
   - WebSocket connection passed to components that need it

3. **Removed Redundancy**:
   - Removed `HEARTBEAT_INTERVAL` (server handles ping/pong at application level)
   - Kept `RECEIVE_TIMEOUT` (useful for dead connection detection)
   - Kept reconnect backoff constants (essential for reconnection)

4. **Clean Separation**:
   - Connection: Transport layer (aiohttp/websockets)
   - Message Handler: Parsing and routing
   - Order Book: State management (when applicable)
   - Market Switcher: Subscription management (when applicable)
   - Manager: Orchestration and public API

## Success Criteria (Per Exchange)

1. ✅ **Main client file significantly reduced** (from 1,600-2,400)
2. ✅ **Each module under 900 lines** (manageable size)
3. ✅ **All existing tests pass** (no regressions)
4. ✅ **Backward compatibility maintained** (same public API)
5. ✅ **Performance unchanged** (no runtime overhead)
6. ✅ **Documentation updated** (module docs, docstrings)
7. ✅ **Code coverage maintained or improved** (same or better test coverage)
8. ✅ **Clean package structure** (follows Python best practices)

### Exchange-Specific Targets & Results

**Lighter** ✅ **COMPLETED**:
- **Main client (`core.py`)**: 580 lines (from 2,403) - **76% reduction** ✅
- **WebSocket manager (`manager.py`)**: 353 lines (from 982) - **64% reduction** ✅
- **Largest module**: 815 lines (`order_manager.py`) - manageable ✅
- **Structure**: Clean packages with `client/managers/`, `client/utils/`, and `websocket/` ✅
- **Removed redundancy**: Eliminated `orders_cache`, removed `HEARTBEAT_INTERVAL` ✅
- **Public API**: Unchanged (same import paths) ✅

**Aster**:
- Main client: < 250 lines (from 1,772)
- Largest module: < 500 lines

**Backpack**:
- Main client: < 250 lines (from 1,613)
- Largest module: < 500 lines

## Next Steps

1. ✅ **Lighter Refactoring**: **COMPLETED**
   - ✅ **Client Package**: `client/core.py` + `client/managers/` + `client/utils/`
     - Reduced from 2,403 lines to 580 lines (`core.py`) - **76% reduction**
     - Removed redundant `orders_cache` (using `_latest_orders` only)
   - ✅ **WebSocket Package**: `websocket/manager.py` + components
     - Reduced from 982 lines to 353 lines (`manager.py`) - **64% reduction**
     - Split into 6 focused modules (< 400 lines each)
   - Clean package structure following Python best practices
   - Backward compatible public API

2. **Apply to Aster**: Use Lighter as reference implementation
   - **Client Refactoring**: Follow same `client/` package structure
   - **WebSocket Refactoring**: Follow same `websocket/` package structure
   - Reuse patterns and learnings from Lighter
   - Estimated: 8-12 days (client) + 3-5 days (websocket) = **11-17 days total**

3. **Apply to Backpack**: Use Lighter as reference implementation
   - **Client Refactoring**: Follow same `client/` package structure
   - **WebSocket Refactoring**: Follow same `websocket/` package structure (may be simpler)
   - Reuse patterns and learnings from Lighter
   - Estimated: 8-12 days (client) + 2-4 days (websocket) = **10-16 days total**

4. **Documentation**: Update architecture docs with final patterns
5. **Consider Shared Base Classes**: After all exchanges refactored, consider extracting common manager patterns

## Shared Utilities & Patterns

After refactoring multiple exchanges, consider creating **shared base classes** in `exchange_clients/base/`:

```
exchange_clients/base/
├── __init__.py
├── market_data_base.py      # Base class for market data managers
├── order_manager_base.py   # Base class for order managers
├── position_manager_base.py # Base class for position managers
└── account_manager_base.py # Base class for account managers
```

These can provide common functionality like:
- Caching patterns
- Error handling
- Retry logic
- WebSocket fallback patterns

## Questions & Considerations

### Open Questions
1. Should managers be exposed publicly for advanced use cases?
2. Should we add type hints more strictly during refactor?
3. Should we adopt a dependency injection framework?
4. Should we add module-level docstrings explaining responsibilities?

### Future Enhancements
- Consider using dataclasses for configuration objects
- Add protocol interfaces for managers (typing.Protocol)
- Consider async context managers for connection lifecycle
- Add comprehensive error types per module

## Example: Lighter Client Structure (After Refactoring) ✅ COMPLETED

```
exchange_clients/lighter/
├── __init__.py                    # Public API: from .client import LighterClient
├── common.py                      # ✅ Existing
├── funding_adapter.py            # ✅ Existing
│
├── client/                        # Client package
│   ├── __init__.py              # Exports: from .core import LighterClient (12 lines)
│   ├── core.py                  # Main LighterClient class (580 lines)
│   │
│   ├── managers/                # Manager classes
│   │   ├── __init__.py         # Exports managers (25 lines)
│   │   ├── market_data.py      # Market data & configuration (517 lines)
│   │   ├── order_manager.py    # Order management (815 lines)
│   │   ├── position_manager.py # Position management (463 lines)
│   │   ├── account_manager.py  # Account management (312 lines)
│   │   └── websocket_handlers.py # WebSocket callbacks (294 lines)
│   │
│   └── utils/                   # Shared utilities
│       ├── __init__.py         # Exports utilities (21 lines)
│       ├── caching.py          # MarketIdCache class (73 lines)
│       ├── converters.py       # Order/snapshot converters (157 lines)
│       └── helpers.py          # Decimal helpers (30 lines)
│
└── websocket/                     # WebSocket package ✅ COMPLETED
    ├── __init__.py             # Exports: from .manager import LighterWebSocketManager (10 lines)
    ├── manager.py               # Main orchestrator (353 lines)
    ├── connection.py            # Connection & reconnection (173 lines)
    ├── message_handler.py       # Message parsing & routing (279 lines)
    ├── order_book.py            # Order book state management (265 lines)
    └── market_switcher.py       # Market switching (273 lines)

Total: ~4,652 lines (organized vs 3,385 lines monolithic: 2,403 client + 982 websocket)
```

**Key Improvements:**
- ✅ Main client (`core.py`): 580 lines (from 2,403) - **76% reduction**
- ✅ WebSocket manager (`manager.py`): 353 lines (from 982) - **64% reduction**
- ✅ Managers are focused and testable
- ✅ No redundant caches (removed `orders_cache`, using `_latest_orders` only)
- ✅ Clean package structure following Python best practices
- ✅ Backward compatible (same import paths)
- ✅ Clear separation: transport (`websocket/`) vs business logic (`client/managers/websocket_handlers`)

## Example: Aster Client Structure (After Refactoring)

```
exchange_clients/aster/
├── __init__.py                    # Public API: from .client import AsterClient
├── common.py                      # ✅ Existing
├── funding_adapter.py            # ✅ Existing
├── websocket_manager.py          # ✅ Existing (to be refactored)
│
├── client/                        # Client package
│   ├── __init__.py              # Exports: from .core import AsterClient
│   ├── core.py                  # Main AsterClient class (~400-500 lines)
│   │
│   ├── managers/                # Manager classes
│   │   ├── __init__.py
│   │   ├── market_data.py      # Market data (~350 lines)
│   │   ├── order_manager.py    # Orders (~450 lines)
│   │   ├── position_manager.py # Positions (~400 lines)
│   │   ├── account_manager.py  # Account (~300 lines)
│   │   └── websocket_handlers.py # WebSocket callbacks (~180 lines)
│   │
│   └── utils/                   # Shared utilities
│       ├── __init__.py
│       ├── caching.py          # Caches (~120 lines)
│       ├── converters.py      # Converters (~150 lines)
│       └── helpers.py         # Helpers (~80 lines)
```

## Example: Backpack Client Structure (After Refactoring)

```
exchange_clients/backpack/
├── __init__.py                    # Public API: from .client import BackpackClient
├── common.py                      # ✅ Existing
├── funding_adapter.py            # ✅ Existing
├── websocket_manager.py          # ✅ Existing (to be refactored)
│
├── client/                        # Client package
│   ├── __init__.py              # Exports: from .core import BackpackClient
│   ├── core.py                  # Main BackpackClient class (~350-450 lines)
│   │
│   ├── managers/                # Manager classes
│   │   ├── __init__.py
│   │   ├── market_data.py      # Market data (~300 lines)
│   │   ├── order_manager.py    # Orders (~400 lines)
│   │   ├── position_manager.py # Positions (~300 lines)
│   │   ├── account_manager.py  # Account (~250 lines)
│   │   └── websocket_handlers.py # WebSocket callbacks (~150 lines)
│   │
│   └── utils/                   # Shared utilities
│       ├── __init__.py
│       ├── caching.py          # Caches (~100 lines)
│       ├── converters.py      # Converters (~120 lines)
│       └── helpers.py         # Helpers (~150 lines - precision helpers)
```

---

## Lighter Refactoring Summary (COMPLETED)

### Actual Implementation Structure

**Client Package:**
```
exchange_clients/lighter/client/
├── __init__.py              # 12 lines - Exports: from .core import LighterClient
├── core.py                  # 580 lines - Main LighterClient class
│
├── managers/
│   ├── __init__.py         # 25 lines
│   ├── market_data.py      # 517 lines
│   ├── order_manager.py    # 815 lines
│   ├── position_manager.py # 463 lines
│   ├── account_manager.py  # 312 lines
│   └── websocket_handlers.py # 294 lines
│
└── utils/
    ├── __init__.py         # 21 lines
    ├── caching.py          # 73 lines (MarketIdCache)
    ├── converters.py       # 157 lines (build_order_info_from_payload, build_snapshot_from_raw)
    └── helpers.py          # 30 lines (decimal_or_none)
```

**WebSocket Package:**
```
exchange_clients/lighter/websocket/
├── __init__.py             # 10 lines - Exports: from .manager import LighterWebSocketManager
├── manager.py              # 353 lines - Main orchestrator
├── connection.py           # 173 lines - Connection & reconnection
├── message_handler.py      # 279 lines - Message parsing & routing
├── order_book.py           # 265 lines - Order book state management
└── market_switcher.py      # 273 lines - Market switching & subscriptions
```

### Key Design Decisions Made

**Client Package:**
1. **Package Structure**: `client/` is a package, not a file
   - `client/core.py` contains the main class
   - `client/__init__.py` is lightweight (just exports)

2. **Managers Subdirectory**: Managers in `client/managers/` for clarity
   - Better organization than flat structure
   - Clear separation of concerns

3. **Removed Redundancy**: Eliminated `orders_cache` dict
   - Only `_latest_orders` (OrderInfo objects) exists
   - Websocket handlers use `latest_orders` for deduplication
   - Order manager uses `latest_orders` for queries

4. **Thin Wrappers**: Public methods are thin wrappers
   - No repetitive None checks (managers initialized in `connect()`)
   - Direct delegation: `return await self.order_manager.place_limit_order(...)`

5. **Manager Initialization**: Managers initialized in `connect()`
   - After SDK clients are ready
   - Uses `set_client_references()` pattern for accessing client attributes

**WebSocket Package:**
1. **Package Structure**: `websocket/` is a package, not a file
   - `websocket/manager.py` contains the main manager class
   - `websocket/__init__.py` is lightweight (just exports)

2. **Component Separation**: Clear boundaries between responsibilities
   - `connection.py`: Transport layer (aiohttp session, reconnection)
   - `message_handler.py`: Message parsing and routing
   - `order_book.py`: Order book state and validation
   - `market_switcher.py`: Market switching and subscriptions
   - `manager.py`: Orchestration and public API

3. **Callback Pattern**: Maintained separation of transport vs business logic
   - `websocket/` package detects events (transport layer)
   - `client/managers/websocket_handlers.py` processes events (business logic)
   - Callbacks passed from handlers to websocket manager

### Lessons Learned

**Client Refactoring:**
- **Don't put implementation in `__init__.py`**: Use `core.py` for main class
- **Single source of truth for caches**: Avoid redundant caches with same data
- **Thin wrappers are fine**: No need for verbose checks if initialization is guaranteed
- **Manager subdirectory improves clarity**: Better than flat `client/` structure

**WebSocket Refactoring:**
- **Separate transport from business logic**: `websocket/` handles transport, `websocket_handlers.py` handles business
- **Component initialization order matters**: Update references after connection
- **Server protocols vary**: Some handle ping/pong at application level (Lighter), others at protocol level (Aster)
- **Package structure scales well**: Each component < 400 lines, easy to understand and test

---

**Document Version**: 3.0  
**Last Updated**: 2025  
**Author**: Refactoring Planning  
**Status**: Lighter Completed ✅ | Aster & Backpack Pending  
**Scope**: Generalized plan for Lighter, Aster, and Backpack exchanges

