# Paradex Exchange Client Refactoring Plan

## Current State Analysis

### Legacy Structure (Current)
```
exchange_clients/paradex/
├── __init__.py              # Basic exports
├── client.py                # 550 lines - Monolithic implementation
├── common.py                # 39 lines - Basic symbol normalization
├── funding_adapter.py       # 47 lines - Placeholder (not implemented)
└── funding_adapter.md       # 313 lines - Documentation only (not used)
```

### Issues with Current Implementation

**Client (`client.py`):**
- ❌ Monolithic file (550 lines) - violates single responsibility
- ❌ Missing proper package structure (no `client/` package)
- ❌ No manager classes (market_data, order_manager, position_manager, account_manager)
- ❌ No utility modules (caching, converters, helpers)
- ❌ Missing many `BaseExchangeClient` methods:
  - `get_order_book_depth()` - partially implemented (only BBO)
  - `place_market_order()` - not implemented
  - `get_position_snapshot()` - not implemented
  - `get_account_pnl()` - not implemented
  - `get_total_asset_value()` - not implemented
- ❌ WebSocket integration incomplete (no `websocket/` package)
- ❌ No WebSocket manager following `BaseWebSocketManager` interface
- ❌ Missing liquidation stream support
- ❌ Incomplete error handling and retry logic
- ❌ Missing proper symbol/contract ID caching

**Funding Adapter (`funding_adapter.py`):**
- ❌ Placeholder implementation (all methods are `pass`)
- ❌ No package structure (`funding_adapter/` directory)
- ❌ No SDK client management (`funding_client.py`)
- ❌ No data fetching logic (`fetchers.py`)
- ❌ Not integrated with funding rate service

**Common Utilities (`common.py`):**
- ✅ Basic symbol normalization exists
- ⚠️ Missing quantity multiplier logic (if Paradex has any)
- ⚠️ Missing error formatting helpers
- ⚠️ Missing order response parsers

## Target Structure (Following Lighter Pattern)

### Modern Package Structure
```
exchange_clients/paradex/
├── __init__.py                    # Public API exports
├── common.py                      # Shared utilities (enhanced)
│
├── client/                        # NEW: Client package
│   ├── __init__.py              # Exports: from .core import ParadexClient
│   ├── core.py                  # Main ParadexClient class (~400-500 lines)
│   │
│   ├── managers/                # Manager classes
│   │   ├── __init__.py
│   │   ├── market_data.py      # Market data & configuration (~300-400 lines)
│   │   ├── order_manager.py    # Order management (~400-500 lines)
│   │   ├── position_manager.py # Position management (~300-400 lines)
│   │   ├── account_manager.py  # Account management (~200-300 lines)
│   │   └── websocket_handlers.py # WebSocket callbacks (~200-300 lines)
│   │
│   └── utils/                   # Shared utilities
│       ├── __init__.py
│       ├── caching.py          # Market/symbol cache, position cache
│       ├── converters.py      # Order info builders, snapshot builders
│       └── helpers.py         # Decimal helpers, validation helpers
│
├── websocket/                     # NEW: WebSocket package
│   ├── __init__.py             # Exports: from .manager import ParadexWebSocketManager
│   ├── manager.py              # Main orchestrator (~300-400 lines)
│   ├── connection.py           # Connection & reconnection (~150-200 lines)
│   ├── message_handler.py      # Message parsing & routing (~200-300 lines)
│   └── order_book.py           # Order book state management (~200-300 lines, if applicable)
│
└── funding_adapter/                # NEW: Funding adapter package
    ├── __init__.py             # Exports: from .adapter import ParadexFundingAdapter
    ├── adapter.py              # Main orchestrator (~80-130 lines)
    ├── funding_client.py        # API client management (~40-60 lines)
    └── fetchers.py             # Data fetching logic (~150-200 lines)
```

## Phase 1: Funding Adapter Refactoring (Priority 1)

### Goal
Implement a fully functional funding adapter following the Lighter pattern, enabling Paradex to be used in funding arbitrage strategies.

### Steps

#### Step 1.1: Create Funding Adapter Package Structure
- [ ] Create `funding_adapter/` directory
- [ ] Create `funding_adapter/__init__.py` (exports `ParadexFundingAdapter`)
- [ ] Create `funding_adapter/adapter.py` (main orchestrator)
- [ ] Create `funding_adapter/funding_client.py` (SDK client management)
- [ ] Create `funding_adapter/fetchers.py` (data fetching logic)

#### Step 1.2: Implement Funding Client (`funding_client.py`)
**Responsibilities:**
- Initialize Paradex SDK client (read-only, no credentials needed)
- Manage client lifecycle (ensure_client, close)
- Handle SDK availability checks

**Implementation Notes:**
- Use `paradex_py` SDK (already compatible via your fork)
- No authentication required (public endpoints)
- Support both PROD and TESTNET environments
- Follow pattern from `lighter/funding_adapter/funding_client.py`

**Key Methods:**
```python
class ParadexFundingClient:
    def __init__(self, api_base_url: str, environment: str = "prod")
    async def ensure_client(self) -> None
    async def close(self) -> None
```

#### Step 1.3: Implement Data Fetchers (`fetchers.py`)
**Responsibilities:**
- Fetch funding rates from Paradex API
- Fetch market data (volume, open interest)
- Parse and normalize API responses
- Handle symbol normalization

**API Endpoints to Use:**
- Funding rates: `fetch_markets_summary()` - includes funding_rate field
- Market data: `fetch_markets()` + `fetch_markets_summary()` - includes volume_24h, open_interest

**Implementation Notes:**
- Paradex uses `-USD-PERP` suffix for perpetual markets
- Funding rate is in `markets_summary` response
- Volume and OI are in separate endpoints (may need to combine)
- Follow pattern from `lighter/funding_adapter/fetchers.py`

**Key Methods:**
```python
class ParadexFundingFetchers:
    async def fetch_funding_rates(self, canonical_interval_hours: Decimal) -> Dict[str, FundingRateSample]
    async def fetch_market_data(self) -> Dict[str, Dict[str, Decimal]]
    @staticmethod
    def parse_next_funding_time(value: Optional[object]) -> Optional[datetime]
```

#### Step 1.4: Implement Main Adapter (`adapter.py`)
**Responsibilities:**
- Compose funding_client and fetchers
- Implement `BaseFundingAdapter` interface
- Delegate to components
- Provide symbol normalization wrappers

**Implementation Notes:**
- Use `common.py` functions for symbol normalization
- Delegate `fetch_funding_rates()` to fetchers
- Delegate `fetch_market_data()` to fetchers
- Follow pattern from `lighter/funding_adapter/adapter.py`

**Key Methods:**
```python
class ParadexFundingAdapter(BaseFundingAdapter):
    async def fetch_funding_rates(self) -> Dict[str, FundingRateSample]
    async def fetch_market_data(self) -> Dict[str, Dict[str, Decimal]]
    def normalize_symbol(self, dex_symbol: str) -> str
    def get_dex_symbol_format(self, normalized_symbol: str) -> str
    async def close(self) -> None
```

#### Step 1.5: Update Common Utilities (`common.py`)
**Enhancements Needed:**
- [ ] Ensure `normalize_symbol()` handles all Paradex symbol formats correctly
- [ ] Ensure `get_paradex_symbol_format()` handles edge cases
- [ ] Add any Paradex-specific helpers (if needed)

#### Step 1.6: Update Module Exports (`__init__.py`)
- [ ] Update to export from `funding_adapter.adapter` instead of `funding_adapter`
- [ ] Ensure backward compatibility

#### Step 1.7: Testing & Integration
- [ ] Test funding rate fetching
- [ ] Test market data fetching
- [ ] Test symbol normalization
- [ ] Verify integration with funding_rate_service
- [ ] Test with funding arbitrage strategy

### Success Criteria
- ✅ Funding adapter follows Lighter package structure
- ✅ All `BaseFundingAdapter` methods implemented
- ✅ Successfully fetches funding rates from Paradex
- ✅ Successfully fetches market data (volume + OI)
- ✅ Proper symbol normalization
- ✅ Integrated with funding_rate_service
- ✅ Works with funding arbitrage strategy

## Phase 2: Client Refactoring (Priority 2 - Future)

### Goal
Refactor the monolithic `client.py` into a modular package structure following the Lighter pattern, implementing all missing `BaseExchangeClient` methods.

### Missing BaseExchangeClient Methods

**Critical (Required for Trading):**
- [ ] `get_order_book_depth()` - Currently only BBO, need full depth
- [ ] `place_market_order()` - Not implemented
- [ ] `get_position_snapshot()` - Not implemented (required for funding arb)
- [ ] `get_account_balance()` - Placeholder only
- [ ] `get_leverage_info()` - Returns defaults, needs real API query

**Important (Required for Strategies):**
- [ ] `get_account_pnl()` - Not implemented
- [ ] `get_total_asset_value()` - Not implemented
- [ ] `get_min_order_notional()` - May need implementation
- [ ] `round_to_step()` - May need implementation

**WebSocket Integration:**
- [ ] `BaseWebSocketManager` implementation
- [ ] `prepare_market_feed()` - Market switching
- [ ] `get_order_book()` - WebSocket order book
- [ ] Order update callbacks
- [ ] Position update callbacks
- [ ] Liquidation stream support

### Refactoring Steps (Future)

#### Step 2.1: Extract Utilities
- Extract helper functions to `client/utils/helpers.py`
- Extract caching logic to `client/utils/caching.py`
- Extract converters to `client/utils/converters.py`

#### Step 2.2: Extract Market Data Manager
- Move market data fetching to `client/managers/market_data.py`
- Implement `get_order_book_depth()` properly
- Implement market metadata caching

#### Step 2.3: Extract Order Manager
- Move order operations to `client/managers/order_manager.py`
- Implement `place_market_order()`
- Improve order tracking and callbacks

#### Step 2.4: Extract Position Manager
- Move position operations to `client/managers/position_manager.py`
- Implement `get_position_snapshot()` properly
- Add position enrichment with live data

#### Step 2.5: Extract Account Manager
- Move account operations to `client/managers/account_manager.py`
- Implement `get_account_balance()` properly
- Implement `get_account_pnl()` and `get_total_asset_value()`
- Implement `get_leverage_info()` with real API queries

#### Step 2.6: Extract WebSocket Handlers
- Move WebSocket callbacks to `client/managers/websocket_handlers.py`
- Implement proper order update handling
- Implement position update handling

#### Step 2.7: Create WebSocket Package
- Create `websocket/` package
- Implement `ParadexWebSocketManager` following `BaseWebSocketManager`
- Extract connection management
- Extract message handling
- Extract order book management (if applicable)

#### Step 2.8: Refactor Core Client
- Create `client/core.py` with main `ParadexClient` class
- Compose managers
- Implement thin wrapper methods
- Maintain backward compatibility

### Success Criteria (Future)
- ✅ Client follows Lighter package structure
- ✅ All `BaseExchangeClient` methods implemented
- ✅ WebSocket manager follows `BaseWebSocketManager` interface
- ✅ Works with grid strategy
- ✅ Works with funding arbitrage strategy
- ✅ Proper error handling and retry logic
- ✅ Comprehensive testing

## Implementation Priority

### Immediate (Phase 1)
1. **Funding Adapter** - Required for funding arbitrage strategy
   - Estimated time: 1-2 days
   - Blocks: None
   - Dependencies: Paradex SDK (already compatible via your fork)

### Future (Phase 2)
2. **Client Refactoring** - Required for full trading support
   - Estimated time: 8-12 days (following Lighter patterns)
   - Blocks: Need API documentation + SDK details
   - Dependencies: User will provide API docs + SDK details

## Key Differences: Paradex vs Lighter

### SDK Patterns
- **Lighter**: Uses `ApiClient`, `FundingApi`, `OrderApi` (separate API classes)
- **Paradex**: Uses `Paradex` client with `api_client` attribute (unified client)

### Symbol Format
- **Lighter**: `"BTC"`, `"1000TOSHI"` (base asset, optional 1000-prefix)
- **Paradex**: `"BTC-USD-PERP"` (base-quote-perp format)

### Authentication
- **Lighter**: API key-based (for trading)
- **Paradex**: L1/L2 Starknet keys (for trading), no auth needed for funding data

### WebSocket
- **Lighter**: Custom WebSocket implementation with order book management
- **Paradex**: Uses `paradex_py` WebSocket client (needs investigation)

## Notes for Implementation

### Funding Adapter (Phase 1)
- Use `paradex_py` SDK's `fetch_markets_summary()` for funding rates
- Use `fetch_markets()` + `fetch_markets_summary()` for market data
- No authentication needed (public endpoints)
- Support both PROD and TESTNET environments
- Follow Lighter's pattern exactly (it's the reference implementation)

### Client (Phase 2 - Future)
- Will need API documentation for:
  - Order placement (limit, market)
  - Order cancellation
  - Position queries
  - Account balance queries
  - Leverage information
  - WebSocket subscriptions
- Will need SDK details for:
  - WebSocket message formats
  - Order update callbacks
  - Position update callbacks
  - Error handling patterns

## Testing Strategy

### Funding Adapter Testing
- Unit tests for `funding_client.py`
- Unit tests for `fetchers.py`
- Integration tests for `adapter.py`
- End-to-end test with funding_rate_service
- Test with funding arbitrage strategy

### Client Testing (Future)
- Unit tests for each manager
- Integration tests for core client
- WebSocket tests
- End-to-end tests with strategies

## References

- **Lighter Reference**: `exchange_clients/lighter/` (completed refactoring)
- **Refactoring Plan**: `docs/EXCHANGE_CLIENT_REFACTOR_PLAN.md`
- **Base Interfaces**: 
  - `exchange_clients/base_client.py`
  - `exchange_clients/base_funding_adapter.py`
  - `exchange_clients/base_websocket.py`
- **Paradex SDK**: `https://github.com/simbayippy/paradex-py` (your fork)

---

**Status**: Phase 1 (Funding Adapter) - Ready to start  
**Next Steps**: Implement funding adapter following Lighter pattern  
**Blockers**: None for Phase 1  
**Dependencies**: Paradex SDK (already compatible via your fork)

