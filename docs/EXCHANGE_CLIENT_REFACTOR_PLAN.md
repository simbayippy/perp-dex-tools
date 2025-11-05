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
├── client.py                   # Main client class (orchestrator, ~200-300 lines)
├── common.py                   # ✅ Already exists (exchange-specific utilities)
├── funding_adapter.py          # ✅ Already exists
├── websocket_manager.py        # ✅ Already exists (if applicable)
│
├── client/                     # NEW: Split client components
│   ├── __init__.py            # Re-exports for backward compatibility
│   ├── core.py                # Core client & connection (~150-250 lines)
│   ├── market_data.py         # Market data & configuration (~300-500 lines)
│   ├── order_manager.py       # Order management (~400-600 lines)
│   ├── position_manager.py    # Position management (~400-700 lines)
│   ├── account_manager.py     # Account management (~200-400 lines)
│   └── websocket_handlers.py # WebSocket callbacks (~150-300 lines)
│
└── utils/                      # NEW: Shared utilities for client components
    ├── __init__.py
    ├── caching.py             # Market/symbol cache, order cache, position cache
    ├── converters.py          # Order info builders, snapshot builders
    └── helpers.py             # Decimal helpers, validation helpers, formatters
```

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

**New `client/client/core.py`** (per exchange):
- Connection management (`connect`, `disconnect`)
- Client initialization (exchange-specific, e.g., `_initialize_lighter_client`)
- Manager initialization and composition
- WebSocket manager setup

**New `client.py`** (main orchestrator, ~200-300 lines):
- Inherits from `BaseExchangeClient`
- Initializes all managers
- Delegates method calls to appropriate managers
- Maintains backward compatibility

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
The main `client.py` will remain the public API:

```python
# exchange_clients/lighter/__init__.py
from .client import LighterClient

__all__ = ["LighterClient"]
```

### Internal Structure
Components will be internal to the package:

```python
# exchange_clients/lighter/client/__init__.py
# Only re-export if needed for advanced use cases
from .core import LighterCoreClient
from .market_data import LighterMarketData
# ... etc

__all__ = ["LighterCoreClient", "LighterMarketData", ...]
```

## Dependency Injection Pattern

### Shared State Management
Managers will share common dependencies via the main client:

```python
class LighterClient(BaseExchangeClient):
    def __init__(self, config, ...):
        super().__init__(config)
        
        # Shared SDK clients
        self.lighter_client = None  # Will be initialized in connect()
        self.api_client = None
        self.account_api = None
        self.order_api = None
        
        # Shared caches
        self._market_id_cache = {}
        self._order_cache = {}
        self._position_cache = {}
        
        # Initialize managers with shared dependencies
        self.market_data = LighterMarketData(
            api_client=self.api_client,  # Will be set in connect()
            config=self.config,
            logger=self.logger,
            cache=self._market_id_cache
        )
        # ... initialize other managers
```

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

1. **Lighter** (2,403 lines) - Most complex, establishes patterns
   - **Estimated Time**: 13-20 days
   - **Rationale**: Largest codebase, most features, will reveal common patterns

2. **Aster** (1,772 lines) - Medium complexity
   - **Estimated Time**: 10-15 days
   - **Rationale**: Similar to Lighter but simpler, can reuse patterns

3. **Backpack** (1,613 lines) - Medium complexity
   - **Estimated Time**: 10-15 days
   - **Rationale**: Different SDK patterns, but similar structure

**Total Estimated Time**: 33-50 days (can be parallelized across exchanges after Lighter establishes patterns)

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
- **Lighter**: 982 lines
- **Aster**: 904 lines
- **Backpack**: 680 lines

All have mixed responsibilities:
- Connection management
- Order book management (when applicable)
- Message handling
- Market switching logic (when applicable)

### Proposed Split (Generalized)

```
exchange_clients/{exchange}/
├── websocket_manager.py        # Main orchestrator (~200-300 lines)
│
└── websocket/                   # NEW: WebSocket components
    ├── __init__.py
    ├── connection.py           # Connection management (~150-200 lines)
    ├── order_book.py           # Order book state management (~200-400 lines, when applicable)
    ├── message_handler.py      # Message parsing & routing (~200-300 lines)
    └── market_switcher.py      # Market switching logic (~150-250 lines, when applicable)
```

### Exchange-Specific Considerations

**Lighter**:
- Complex order book state management
- Market switching with subscription management
- Sequence validation and gap detection

**Aster**:
- Order book management
- Market switching
- Event routing

**Backpack**:
- Simpler structure
- May not need separate market switcher

### Refactoring Benefits
- **Separation of Concerns**: Connection vs state vs routing
- **Easier Testing**: Mock connection, test order book logic separately
- **Better Maintainability**: Smaller, focused files
- **Reusable Patterns**: Common WebSocket patterns can be extracted

## Success Criteria (Per Exchange)

1. ✅ **Main client file under 300 lines** (from 1,600-2,400)
2. ✅ **Each module under 600 lines** (manageable size)
3. ✅ **All existing tests pass** (no regressions)
4. ✅ **Backward compatibility maintained** (same public API)
5. ✅ **Performance unchanged** (no runtime overhead)
6. ✅ **Documentation updated** (module docs, docstrings)
7. ✅ **Code coverage maintained or improved** (same or better test coverage)

### Exchange-Specific Targets

**Lighter**:
- Main client: < 300 lines (from 2,403)
- Largest module: < 600 lines

**Aster**:
- Main client: < 250 lines (from 1,772)
- Largest module: < 500 lines

**Backpack**:
- Main client: < 250 lines (from 1,613)
- Largest module: < 500 lines

## Next Steps

1. **Review & Approval**: Get team review on this generalized plan
2. **Start with Lighter**: Create feature branch `refactor/lighter-client-modular`
3. **Establish Patterns**: Complete Lighter refactoring to establish patterns
4. **Share Learnings**: Document common patterns, utilities, and best practices
5. **Parallel Refactoring**: Refactor Aster and Backpack in parallel (if resources allow)
6. **Documentation**: Update architecture docs after each exchange completion

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

## Example: Lighter Client Structure (After Refactoring)

```
exchange_clients/lighter/
├── __init__.py                    # Public API
├── client.py                      # Main orchestrator (~250 lines)
├── common.py                      # ✅ Existing
├── funding_adapter.py            # ✅ Existing
├── websocket_manager.py          # ✅ Existing (to be refactored)
│
├── client/
│   ├── __init__.py
│   ├── core.py                   # Connection & initialization (~200 lines)
│   ├── market_data.py            # Market data (~450 lines)
│   ├── order_manager.py          # Orders (~550 lines)
│   ├── position_manager.py       # Positions (~650 lines)
│   ├── account_manager.py        # Account (~350 lines)
│   └── websocket_handlers.py     # WebSocket callbacks (~200 lines)
│
└── utils/
    ├── __init__.py
    ├── caching.py                # Caches (~150 lines)
    ├── converters.py             # Converters (~200 lines)
    └── helpers.py                # Helpers (~100 lines)
```

## Example: Aster Client Structure (After Refactoring)

```
exchange_clients/aster/
├── __init__.py                    # Public API
├── client.py                      # Main orchestrator (~250 lines)
├── common.py                      # ✅ Existing
├── funding_adapter.py            # ✅ Existing
├── websocket_manager.py          # ✅ Existing (to be refactored)
│
├── client/
│   ├── __init__.py
│   ├── core.py                   # Connection & initialization (~180 lines)
│   ├── market_data.py            # Market data (~350 lines)
│   ├── order_manager.py          # Orders (~450 lines)
│   ├── position_manager.py       # Positions (~400 lines)
│   ├── account_manager.py        # Account (~300 lines)
│   └── websocket_handlers.py     # WebSocket callbacks (~180 lines)
│
└── utils/
    ├── __init__.py
    ├── caching.py                # Caches (~120 lines)
    ├── converters.py             # Converters (~150 lines)
    └── helpers.py                # Helpers (~80 lines)
```

## Example: Backpack Client Structure (After Refactoring)

```
exchange_clients/backpack/
├── __init__.py                    # Public API
├── client.py                      # Main orchestrator (~250 lines)
├── common.py                      # ✅ Existing
├── funding_adapter.py            # ✅ Existing
├── websocket_manager.py          # ✅ Existing (to be refactored)
│
├── client/
│   ├── __init__.py
│   ├── core.py                   # Connection & initialization (~150 lines)
│   ├── market_data.py            # Market data (~300 lines)
│   ├── order_manager.py          # Orders (~400 lines)
│   ├── position_manager.py       # Positions (~300 lines)
│   ├── account_manager.py        # Account (~250 lines)
│   └── websocket_handlers.py     # WebSocket callbacks (~150 lines)
│
└── utils/
    ├── __init__.py
    ├── caching.py                # Caches (~100 lines)
    ├── converters.py             # Converters (~120 lines)
    └── helpers.py                # Helpers (~150 lines - precision helpers)
```

---

**Document Version**: 2.0  
**Last Updated**: 2024  
**Author**: Refactoring Planning  
**Status**: Planning Phase  
**Scope**: Generalized plan for Lighter, Aster, and Backpack exchanges

