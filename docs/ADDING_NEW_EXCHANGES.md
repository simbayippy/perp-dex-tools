# Guide: Adding New Exchanges

> **Purpose**: Step-by-step guide for integrating new perpetual DEX exchanges into the trading bot system.

This guide provides a comprehensive walkthrough for adding support for a new exchange, following the established patterns from Lighter, Aster, Backpack, and Paradex.

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Exchange Structure Overview](#exchange-structure-overview)
3. [Phase 1: Funding Adapter](#phase-1-funding-adapter)
4. [Phase 2: Client Package](#phase-2-client-package)
5. [Phase 3: WebSocket Package](#phase-3-websocket-package)
6. [Integration & Testing](#integration--testing)
7. [Common Patterns & Best Practices](#common-patterns--best-practices)
8. [Reference Implementations](#reference-implementations)

---

## Prerequisites

Before starting, ensure you have:

1. **API Documentation**: 
   - REST API endpoints for funding rates, market data, orders, positions
   - WebSocket channels and message formats
   - Authentication requirements

2. **SDK or HTTP Client**:
   - Python SDK (preferred) or HTTP client library
   - Installation instructions and dependencies

3. **Exchange Account** (for trading features):
   - API credentials (API keys, private keys, etc.)
   - Testnet access (if available) for development

4. **Understanding of Exchange**:
   - Symbol format (e.g., "BTC-USD-PERP", "BTCUSDT")
   - Order types supported (limit, market)
   - Funding rate intervals (typically 8 hours)
   - Leverage system and margin requirements

---

## Exchange Structure Overview

Every exchange follows this standardized package structure:

```
exchange_clients/{exchange_name}/
├── __init__.py                    # Public API exports
├── common.py                      # Shared utilities (symbol normalization, etc.)
│
├── client/                        # Trading client package
│   ├── __init__.py              # Exports: from .core import {Exchange}Client
│   ├── core.py                  # Main client class (~400-600 lines)
│   │
│   ├── managers/                # Manager classes (specialized responsibilities)
│   │   ├── __init__.py
│   │   ├── market_data.py      # Market data & configuration (~300-500 lines)
│   │   ├── order_manager.py    # Order management (~400-600 lines)
│   │   ├── position_manager.py # Position management (~300-500 lines)
│   │   ├── account_manager.py  # Account management (~200-400 lines)
│   │   └── websocket_handlers.py # WebSocket callbacks (~200-300 lines)
│   │
│   └── utils/                   # Shared utilities
│       ├── __init__.py
│       ├── caching.py          # Market/symbol cache, position cache
│       ├── converters.py      # Order info builders, snapshot builders
│       └── helpers.py         # Decimal helpers, validation helpers
│
├── websocket/                     # WebSocket package
│   ├── __init__.py             # Exports: from .manager import {Exchange}WebSocketManager
│   ├── manager.py              # Main orchestrator (~300-400 lines)
│   ├── connection.py           # Connection & reconnection (~150-200 lines)
│   ├── message_handler.py      # Message parsing & routing (~200-300 lines)
│   ├── order_book.py           # Order book state management (~200-300 lines, if applicable)
│   └── market_switcher.py      # Market switching (~200-300 lines, if applicable)
│
└── funding_adapter/                # Funding adapter package
    ├── __init__.py             # Exports: from .adapter import {Exchange}FundingAdapter
    ├── adapter.py              # Main orchestrator (~80-130 lines)
    ├── funding_client.py        # API client management (~40-60 lines)
    └── fetchers.py             # Data fetching logic (~150-200 lines)
```

**Key Design Principles:**
- **Separation of Concerns**: Each package has a single responsibility
- **Modularity**: Managers handle specific domains (orders, positions, etc.)
- **Reusability**: Utilities are shared across managers
- **Testability**: Each component can be tested independently

---

## Phase 1: Funding Adapter

The funding adapter is the **first component** to implement as it's required for funding arbitrage strategies and doesn't require trading credentials.

### Step 1.1: Create Package Structure

```bash
mkdir -p exchange_clients/{exchange_name}/funding_adapter
touch exchange_clients/{exchange_name}/funding_adapter/__init__.py
touch exchange_clients/{exchange_name}/funding_adapter/adapter.py
touch exchange_clients/{exchange_name}/funding_adapter/funding_client.py
touch exchange_clients/{exchange_name}/funding_adapter/fetchers.py
```

### Step 1.2: Implement Funding Client (`funding_client.py`)

**Purpose**: Manage API client lifecycle (SDK or HTTP session).

**Reference**: `exchange_clients/lighter/funding_adapter/funding_client.py`

**Key Methods**:
```python
class {Exchange}FundingClient:
    def __init__(self, api_base_url: str, environment: str = "prod")
    async def ensure_client(self) -> None  # Initialize SDK/client
    async def close(self) -> None  # Cleanup
```

**Implementation Notes**:
- If using SDK: Initialize SDK client (read-only, no credentials needed)
- If using HTTP: Create aiohttp session
- Support both PROD and TESTNET environments
- Handle SDK availability checks (try/except ImportError)

### Step 1.3: Implement Data Fetchers (`fetchers.py`)

**Purpose**: Fetch funding rates and market data from exchange API.

**Reference**: `exchange_clients/lighter/funding_adapter/fetchers.py`

**Key Methods**:
```python
class {Exchange}FundingFetchers:
    async def fetch_funding_rates(
        self, canonical_interval_hours: Decimal
    ) -> Dict[str, FundingRateSample]
    
    async def fetch_market_data(
        self
    ) -> Dict[str, Dict[str, Decimal]]  # volume_24h, open_interest
    
    @staticmethod
    def parse_next_funding_time(value: Optional[object]) -> Optional[datetime]
```

**Implementation Notes**:
- **Funding Rates**:
  - Fetch from exchange API endpoint
  - Normalize to 8-hour interval (if exchange uses different interval)
  - Parse `next_funding_time` if available
  - Return `Dict[str, FundingRateSample]` with normalized symbols as keys

- **Market Data**:
  - Fetch `volume_24h` (in USD)
  - Fetch `open_interest` (in USD - convert from base currency if needed)
  - Return `Dict[str, Dict[str, Decimal]]` with normalized symbols as keys

- **Symbol Normalization**:
  - Convert exchange format (e.g., "BTC-USD-PERP") → normalized ("BTC")
  - Use `normalize_symbol_fn` passed from adapter

**Required Fields**:
- `FundingRateSample`:
  - `normalized_rate`: Decimal (normalized to 8h interval)
  - `raw_rate`: Decimal (original rate from exchange)
  - `interval_hours`: Decimal (canonical 8 hours)
  - `next_funding_time`: Optional[datetime]
  - `metadata`: Dict (store exchange-specific data)

### Step 1.4: Implement Main Adapter (`adapter.py`)

**Purpose**: Orchestrate funding_client and fetchers, implement `BaseFundingAdapter`.

**Reference**: `exchange_clients/lighter/funding_adapter/adapter.py`

**Key Methods**:
```python
class {Exchange}FundingAdapter(BaseFundingAdapter):
    async def fetch_funding_rates(self) -> Dict[str, FundingRateSample]
    async def fetch_market_data(self) -> Dict[str, Dict[str, Decimal]]
    def normalize_symbol(self, dex_symbol: str) -> str
    def get_dex_symbol_format(self, normalized_symbol: str) -> str
    async def close(self) -> None
```

**Implementation Notes**:
- Inherit from `BaseFundingAdapter`
- Initialize `funding_client` and `fetchers` in `__init__`
- Delegate `fetch_funding_rates()` and `fetch_market_data()` to fetchers
- Use `common.py` functions for symbol normalization (don't duplicate logic)
- Set `CANONICAL_INTERVAL_HOURS = Decimal("8")` (or override if different)

### Step 1.5: Create Common Utilities (`common.py`)

**Purpose**: Exchange-specific symbol normalization and utilities.

**Reference**: `exchange_clients/lighter/common.py`

**Key Functions**:
```python
def normalize_symbol(dex_symbol: str) -> str:
    """Convert exchange format to normalized format (e.g., 'BTC-USD-PERP' -> 'BTC')"""
    
def get_{exchange}_symbol_format(normalized_symbol: str) -> str:
    """Convert normalized format to exchange format (e.g., 'BTC' -> 'BTC-USD-PERP')"""
```

**Implementation Notes**:
- Handle exchange-specific formats (suffixes, prefixes, multipliers)
- Support edge cases (e.g., "1000TOSHI" → "TOSHI" with multiplier)
- Return uppercase normalized symbols

### Step 1.6: Update Module Exports (`__init__.py`)

**Purpose**: Export the main adapter class.

```python
# exchange_clients/{exchange_name}/__init__.py
from .client.core import {Exchange}Client
from .funding_adapter import {Exchange}FundingAdapter
from .common import normalize_symbol, get_{exchange}_symbol_format

__all__ = [
    '{Exchange}Client',
    '{Exchange}FundingAdapter',
    'normalize_symbol',
    'get_{exchange}_symbol_format',
]
```

### Step 1.7: Testing Funding Adapter

**Test Checklist**:
- [ ] Funding rates fetch successfully
- [ ] Rates are normalized to 8-hour interval
- [ ] Market data (volume, OI) fetch successfully
- [ ] OI is converted to USD (if needed)
- [ ] Symbol normalization works correctly
- [ ] Integration with `funding_rate_service` works
- [ ] Works with funding arbitrage strategy

---

## Phase 2: Client Package

The client package handles all trading operations. Implement this after the funding adapter.

### Step 2.1: Create Package Structure

```bash
mkdir -p exchange_clients/{exchange_name}/client/managers
mkdir -p exchange_clients/{exchange_name}/client/utils
touch exchange_clients/{exchange_name}/client/__init__.py
touch exchange_clients/{exchange_name}/client/core.py
touch exchange_clients/{exchange_name}/client/managers/__init__.py
touch exchange_clients/{exchange_name}/client/managers/market_data.py
touch exchange_clients/{exchange_name}/client/managers/order_manager.py
touch exchange_clients/{exchange_name}/client/managers/position_manager.py
touch exchange_clients/{exchange_name}/client/managers/account_manager.py
touch exchange_clients/{exchange_name}/client/managers/websocket_handlers.py
touch exchange_clients/{exchange_name}/client/utils/__init__.py
touch exchange_clients/{exchange_name}/client/utils/caching.py
touch exchange_clients/{exchange_name}/client/utils/converters.py
touch exchange_clients/{exchange_name}/client/utils/helpers.py
```

### Step 2.2: Implement Utilities (`utils/`)

**Purpose**: Shared helper functions used across managers.

#### `helpers.py`: Decimal conversion and validation

**Reference**: `exchange_clients/lighter/client/utils/helpers.py`

**Key Functions**:
```python
def to_decimal(value: Any, default: Decimal = Decimal("0")) -> Decimal:
    """Convert value to Decimal safely"""
    
def normalize_{exchange}_side(side: str) -> str:
    """Normalize order side (e.g., 'BUY' -> 'buy')"""
```

#### `caching.py`: Cache classes for market/symbol data

**Reference**: `exchange_clients/lighter/client/utils/caching.py`

**Key Classes**:
```python
class ContractIdCache:
    """Cache for contract IDs per symbol (supports dict-like access)"""
    def get(self, symbol: str) -> Optional[str]
    def __getitem__(self, symbol: str) -> str
    def __setitem__(self, symbol: str, contract_id: str) -> None

class TickSizeCache:
    """Cache for tick sizes per symbol"""
    # Similar pattern
```

#### `converters.py`: Data model converters

**Reference**: `exchange_clients/lighter/client/utils/converters.py`

**Key Functions**:
```python
def build_order_info_from_{exchange}(
    raw_order: Dict[str, Any]
) -> OrderInfo:
    """Convert exchange order format to OrderInfo"""
    
def build_snapshot_from_{exchange}(
    raw_position: Dict[str, Any],
    mark_price: Decimal,
    ...
) -> ExchangePositionSnapshot:
    """Convert exchange position format to ExchangePositionSnapshot"""
```

### Step 2.3: Implement Market Data Manager (`managers/market_data.py`)

**Purpose**: Market data fetching, order book depth, BBO prices, contract attributes.

**Reference**: `exchange_clients/lighter/client/managers/market_data.py`

**Key Methods**:
```python
class {Exchange}MarketData:
    async def get_order_book_depth(
        self, contract_id: str, levels: int = 10
    ) -> Dict[str, List[Dict[str, Decimal]]]
    
    async def fetch_bbo_prices(
        self, contract_id: str
    ) -> Tuple[Decimal, Decimal]
    
    async def get_contract_attributes(
        self, ticker: str
    ) -> Tuple[str, Decimal]  # (contract_id, tick_size)
    
    async def get_market_metadata(
        self, contract_id: str
    ) -> Optional[Dict[str, Any]]
```

**Implementation Notes**:
- **`get_order_book_depth()`**:
  - Try WebSocket order book first (if available)
  - Fall back to REST API
  - Return format: `{'bids': [{'price': Decimal, 'size': Decimal}, ...], 'asks': [...]}`
  - Handle symbol resolution (e.g., "BTC" → "BTC-USD-PERP")

- **`fetch_bbo_prices()`**:
  - Try WebSocket BBO stream first
  - Fall back to REST API `fetch_bbo()` or order book depth=1
  - Return `(best_bid, best_ask)` as Decimals

- **`get_contract_attributes()`**:
  - Fetch market metadata for ticker
  - Set `config.contract_id` and `config.tick_size`
  - Cache contract_id in `contract_id_cache`
  - Return `(contract_id, tick_size)`
  - **CRITICAL**: Raise `ValueError` if market not found/tradeable

- **`get_market_metadata()`**:
  - Fetch and cache market metadata (tick_size, order_size_increment, min_notional, etc.)
  - Extract leverage parameters (imf_base, mmf_factor, etc.) if available

### Step 2.4: Implement Order Manager (`managers/order_manager.py`)

**Purpose**: Order placement, cancellation, and tracking.

**Reference**: `exchange_clients/lighter/client/managers/order_manager.py`

**Key Methods**:
```python
class {Exchange}OrderManager:
    async def place_limit_order(
        self, contract_id: str, quantity: Decimal, price: Decimal,
        side: str, reduce_only: bool = False
    ) -> OrderResult
    
    async def place_market_order(
        self, contract_id: str, quantity: Decimal, side: str,
        reduce_only: bool = False
    ) -> OrderResult
    
    async def cancel_order(
        self, order_id: str
    ) -> OrderResult
    
    async def get_order_info(
        self, order_id: str, *, force_refresh: bool = False
    ) -> Optional[OrderInfo]
    
    async def get_active_orders(
        self, contract_id: str
    ) -> List[OrderInfo]
```

**Implementation Notes**:
- **`place_limit_order()`**:
  - Round price to tick size
  - Round quantity to order size increment
  - Validate min notional (unless `reduce_only=True`)
  - Call exchange API/SDK
  - Track order in cache (for WebSocket updates)
  - Return `OrderResult` with `order_id`, `success`, etc.

- **`place_market_order()`**:
  - If exchange supports market orders: Use market order API
  - If not: Use aggressive limit order (price far from market)
  - Handle `limit_price=Decimal("0")` pattern if SDK requires it

- **`cancel_order()`**:
  - **CRITICAL METHOD**: Must be implemented
  - Call exchange cancel API
  - Return `OrderResult` with filled_size if partially filled

- **`get_order_info()`**:
  - Try WebSocket cache first (if available)
  - Fall back to REST API if `force_refresh=True` or cache miss
  - Convert to `OrderInfo` using converter

- **`get_active_orders()`**:
  - Fetch all open orders for contract
  - Convert to `List[OrderInfo]`

### Step 2.5: Implement Position Manager (`managers/position_manager.py`)

**Purpose**: Position tracking and snapshots.

**Reference**: `exchange_clients/lighter/client/managers/position_manager.py`

**Key Methods**:
```python
class {Exchange}PositionManager:
    async def get_account_positions(
        self
    ) -> Decimal  # Position size for current ticker
    
    async def get_position_snapshot(
        self, symbol: str, position_opened_at: Optional[float] = None
    ) -> Optional[ExchangePositionSnapshot]
```

**Implementation Notes**:
- **`get_account_positions()`**:
  - Fetch position size for current ticker (from config)
  - Return absolute value (positive Decimal)

- **`get_position_snapshot()`**:
  - Fetch position data from exchange
  - Enrich with live market data (mark price, funding rate, etc.)
  - Calculate cumulative funding (if `position_opened_at` provided)
  - Convert to `ExchangePositionSnapshot` using converter
  - Return `None` if position not found

**Required Fields** (`ExchangePositionSnapshot`):
- `symbol`: str (normalized)
- `quantity`: Decimal (signed: positive=long, negative=short)
- `entry_price`: Decimal
- `mark_price`: Decimal
- `unrealized_pnl`: Decimal
- `cumulative_funding`: Decimal (if available)
- `margin_used`: Decimal (if available)
- `leverage`: Decimal (if available)

### Step 2.6: Implement Account Manager (`managers/account_manager.py`)

**Purpose**: Account balance, P&L, leverage information.

**Reference**: `exchange_clients/lighter/client/managers/account_manager.py`

**Key Methods**:
```python
class {Exchange}AccountManager:
    async def get_account_balance(
        self
    ) -> Optional[Decimal]  # Available balance (not total)
    
    async def get_account_pnl(
        self
    ) -> Optional[Decimal]  # Unrealized P&L
    
    async def get_total_asset_value(
        self
    ) -> Optional[Decimal]  # Balance + P&L
    
    async def get_leverage_info(
        self, symbol: str
    ) -> Dict[str, Any]
    
    async def get_min_order_notional(
        self, symbol: str
    ) -> Optional[Decimal]
```

**Implementation Notes**:
- **`get_account_balance()`**:
  - Return **available** balance (what can be used for new positions)
  - Not total wallet balance
  - Return `None` if exchange doesn't support

- **`get_leverage_info()`**:
  - **CRITICAL**: Required for delta-neutral strategies
  - Query exchange API for max leverage, margin requirements
  - Calculate `max_leverage = 1 / imf_base` if using IMF parameters
  - Return dict:
    ```python
    {
        'max_leverage': Decimal or None,
        'max_notional': Decimal or None,
        'margin_requirement': Decimal or None,
        'brackets': List or None,
        'error': str or None  # If symbol not found
    }
    ```
  - Set `error` field if symbol not listed or API fails

- **`get_min_order_notional()`**:
  - Fetch from market metadata
  - Return minimum USD value required for orders

### Step 2.7: Implement WebSocket Handlers (`managers/websocket_handlers.py`)

**Purpose**: Handle WebSocket callbacks (order updates, liquidations, etc.).

**Reference**: `exchange_clients/lighter/client/managers/websocket_handlers.py`

**Key Methods**:
```python
class {Exchange}WebSocketHandlers:
    async def handle_order_update(
        self, order_data: Dict[str, Any] | List[Dict[str, Any]]
    ) -> None
    
    async def handle_liquidation_notification(
        self, notifications: List[Dict[str, Any]]
    ) -> None
    
    async def handle_position_update(
        self, position_data: Dict[str, Any]
    ) -> None  # If exchange supports position streams
```

**Implementation Notes**:
- **`handle_order_update()`**:
  - Parse order data from WebSocket
  - Update order cache (for `get_order_info()`)
  - Trigger order fill callbacks if needed
  - Convert to `OrderInfo` using converter

- **`handle_liquidation_notification()`**:
  - Parse liquidation events from WebSocket
  - Create `LiquidationEvent` objects
  - Emit via `client.emit_liquidation_event()`

### Step 2.8: Implement Core Client (`core.py`)

**Purpose**: Main orchestrator that implements `BaseExchangeClient`.

**Reference**: `exchange_clients/lighter/client/core.py`

**Key Structure**:
```python
class {Exchange}Client(BaseExchangeClient):
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        # Initialize caches
        self._contract_id_cache = ContractIdCache()
        # Initialize managers (will be set in connect())
        self.market_data = None
        self.order_manager = None
        # ...
    
    async def connect(self) -> None:
        # Initialize SDK clients
        # Initialize managers with dependencies
        # Connect WebSocket manager
        # Subscribe to channels
    
    async def disconnect(self) -> None:
        # Disconnect WebSocket
        # Close SDK clients
    
    # Delegate methods to managers
    async def place_limit_order(self, ...):
        return await self.order_manager.place_limit_order(...)
    
    # ... etc
```

**Required `BaseExchangeClient` Methods**:
- ✅ `connect()` / `disconnect()`
- ✅ `get_exchange_name()` → return `"{exchange_name}"`
- ✅ `fetch_bbo_prices()` → delegate to `market_data.fetch_bbo_prices()`
- ✅ `get_order_book_depth()` → delegate to `market_data.get_order_book_depth()`
- ✅ `get_contract_attributes()` → delegate to `market_data.get_contract_attributes()`
- ✅ `place_limit_order()` → delegate to `order_manager.place_limit_order()`
- ✅ `place_market_order()` → delegate to `order_manager.place_market_order()`
- ✅ `cancel_order()` → delegate to `order_manager.cancel_order()`
- ✅ `get_order_info()` → delegate to `order_manager.get_order_info()`
- ✅ `get_active_orders()` → delegate to `order_manager.get_active_orders()`
- ✅ `get_account_positions()` → delegate to `position_manager.get_account_positions()`
- ✅ `get_account_balance()` → delegate to `account_manager.get_account_balance()`
- ✅ `get_position_snapshot()` → delegate to `position_manager.get_position_snapshot()`
- ✅ `get_leverage_info()` → delegate to `account_manager.get_leverage_info()`

**Implementation Notes**:
- Managers initialized in `connect()` after SDK clients are ready
- Use dependency injection pattern (managers receive client references)
- Thin wrapper methods (just delegate, no logic)
- Handle `_validate_config()` to check credentials

---

## Phase 3: WebSocket Package

The WebSocket package handles real-time data streams. Implement this after the client package.

### Step 3.1: Create Package Structure

```bash
mkdir -p exchange_clients/{exchange_name}/websocket
touch exchange_clients/{exchange_name}/websocket/__init__.py
touch exchange_clients/{exchange_name}/websocket/manager.py
touch exchange_clients/{exchange_name}/websocket/connection.py
touch exchange_clients/{exchange_name}/websocket/message_handler.py
touch exchange_clients/{exchange_name}/websocket/order_book.py  # If applicable
touch exchange_clients/{exchange_name}/websocket/market_switcher.py  # If applicable
```

### Step 3.2: Implement Connection Manager (`connection.py`)

**Purpose**: WebSocket connection lifecycle and reconnection.

**Reference**: `exchange_clients/lighter/websocket/connection.py`

**Key Methods**:
```python
class {Exchange}WebSocketConnection:
    async def open_connection(self) -> bool
    async def cleanup_current_ws(self) -> None
    async def reconnect(
        self, reset_order_book_fn: Callable, subscribe_channels_fn: Callable,
        running: bool
    ) -> None
```

**Implementation Notes**:
- Handle connection establishment (SDK or raw WebSocket)
- Implement exponential backoff reconnection
- Constants: `RECONNECT_BACKOFF_INITIAL`, `RECONNECT_BACKOFF_MAX`
- Update `is_connected` flag

### Step 3.3: Implement Message Handler (`message_handler.py`)

**Purpose**: Parse and route WebSocket messages.

**Reference**: `exchange_clients/lighter/websocket/message_handler.py`

**Key Methods**:
```python
class {Exchange}MessageHandler:
    async def handle_sdk_message(
        self, ws_channel: Any, message: Dict[str, Any]
    ) -> None
    
    # Route to appropriate handlers:
    # - Order updates → order_update_callback
    # - Order book → order_book_manager
    # - BBO → notify_bbo_update_fn
    # - Fills/liquidations → liquidation_callback
```

**Implementation Notes**:
- Parse raw WebSocket messages
- Detect message type (order update, order book, BBO, etc.)
- Route to appropriate handlers/callbacks
- Handle protocol-level ping/pong (if needed)

### Step 3.4: Implement Order Book Manager (`order_book.py`) - If Applicable

**Purpose**: Manage order book state from WebSocket updates.

**Reference**: `exchange_clients/lighter/websocket/order_book.py`

**Key Methods**:
```python
class {Exchange}OrderBook:
    def update_order_book(
        self, market: str, data: Dict[str, Any]
    ) -> None
    
    def get_order_book(
        self, levels: Optional[int] = None
    ) -> Optional[Dict[str, List[Dict[str, Decimal]]]]
    
    def get_best_levels(
        self, min_size_usd: float = 0
    ) -> Tuple[Tuple[Decimal, Decimal], Tuple[Decimal, Decimal]]
    
    def reset_order_book(self) -> None
```

**Implementation Notes**:
- Handle snapshot (`update_type='s'`) and delta updates
- Process inserts, updates, deletes
- Track best bid/ask
- Mark `order_book_ready` after first snapshot
- Use locks for thread-safety (if needed)

### Step 3.5: Implement Market Switcher (`market_switcher.py`) - If Applicable

**Purpose**: Handle market switching and subscriptions.

**Reference**: `exchange_clients/lighter/websocket/market_switcher.py`

**Key Methods**:
```python
class {Exchange}MarketSwitcher:
    async def lookup_contract_id(self, symbol: str) -> Optional[str]
    def validate_market_switch_needed(self, target_contract_id: str) -> bool
    async def unsubscribe_market(self, contract_id: str) -> None
    async def subscribe_channels(
        self, contract_id: str, order_callback, order_book_callback, ...
    ) -> None
    def update_market_config(self, contract_id: str) -> None
```

**Implementation Notes**:
- Lookup contract ID for symbol (may require API call)
- Validate if switch is needed (avoid unnecessary switches)
- Unsubscribe from old market, subscribe to new
- Update `config.contract_id` after switch
- Handle subscription parameters (depth, refresh_rate, etc.)

### Step 3.6: Implement Main Manager (`manager.py`)

**Purpose**: Orchestrate all WebSocket components, implement `BaseWebSocketManager`.

**Reference**: `exchange_clients/lighter/websocket/manager.py`

**Key Methods**:
```python
class {Exchange}WebSocketManager(BaseWebSocketManager):
    async def connect(self) -> None
    async def disconnect(self) -> None
    async def prepare_market_feed(self, symbol: Optional[str]) -> None
    def get_order_book(self, levels: Optional[int] = None) -> Optional[Dict]
    # Delegate to order_book, market_switcher, etc.
```

**Implementation Notes**:
- Inherit from `BaseWebSocketManager`
- Initialize all components in `__init__`
- **`prepare_market_feed()`**:
  - Lookup target contract_id
  - Validate if switch needed
  - Perform market switch (unsubscribe old, subscribe new)
  - Wait for order book ready (with timeout)
  - Update config state
- Delegate `get_order_book()` to `order_book` component
- Handle BBO updates via `_notify_bbo_update()`

**Required `BaseWebSocketManager` Methods**:
- ✅ `connect()` - Establish WebSocket connection
- ✅ `disconnect()` - Close connection
- ✅ `prepare_market_feed(symbol)` - Switch to target market
- ✅ `get_order_book(levels)` - Get formatted order book

---

## Integration & Testing

### Step 4.1: Update Dependencies (`pyproject.toml`)

Add exchange SDK to `exchange_clients/pyproject.toml`:

```toml
[project.optional-dependencies]
{exchange_name} = [
    "{exchange_sdk} @ git+https://github.com/user/repo.git@branch",
]

all = [
    # ... existing exchanges ...
    "{exchange_sdk} @ git+https://github.com/user/repo.git@branch",
]
```

### Step 4.2: Update Database Scripts

Add exchange support to credential management:

**File**: `database/scripts/add_account.py`

```python
# In read_credentials_from_env():
if exchange == "{Exchange}":
    return {
        '{EXCHANGE}_API_KEY': os.getenv('{EXCHANGE}_API_KEY'),
        '{EXCHANGE}_SECRET_KEY': os.getenv('{EXCHANGE}_SECRET_KEY'),
        # ... etc
    }

# In interactive_mode():
# Add "{Exchange}" to exchange options
```

### Step 4.3: Update Strategy Configs

Add exchange to strategy configuration schemas:

**File**: `strategies/implementations/funding_arbitrage/config_builder/schema.py`

```python
# Add "{exchange_name}" to DEX_NAME enum
DEX_NAME = Literal["lighter", "aster", "backpack", "{exchange_name}"]
```

### Step 4.4: Testing Checklist

**Funding Adapter**:
- [ ] Fetch funding rates successfully
- [ ] Rates normalized to 8-hour interval
- [ ] Market data (volume, OI) fetched correctly
- [ ] Symbol normalization works
- [ ] Integration with `funding_rate_service` works
- [ ] Works with funding arbitrage strategy

**Client Package**:
- [ ] `connect()` / `disconnect()` work
- [ ] `get_order_book_depth()` returns correct format
- [ ] `fetch_bbo_prices()` returns valid prices
- [ ] `get_contract_attributes()` resolves symbols correctly
- [ ] `place_limit_order()` places orders successfully
- [ ] `place_market_order()` executes immediately
- [ ] `cancel_order()` cancels orders
- [ ] `get_order_info()` returns order details
- [ ] `get_position_snapshot()` returns position data
- [ ] `get_leverage_info()` returns correct leverage limits
- [ ] `get_account_balance()` returns available balance

**WebSocket Package**:
- [ ] `connect()` establishes connection
- [ ] `prepare_market_feed()` switches markets correctly
- [ ] Order updates received via WebSocket
- [ ] Order book updates populate correctly
- [ ] BBO updates received (if supported)
- [ ] Liquidation events detected (if supported)
- [ ] Reconnection works after disconnect

**Integration**:
- [ ] Works with grid strategy
- [ ] Works with funding arbitrage strategy
- [ ] Multi-symbol trading works (contract ID caching)
- [ ] Error handling works (invalid symbols, API failures)

---

## Common Patterns & Best Practices

### Symbol Resolution Pattern

**Problem**: Exchanges use different symbol formats (e.g., "BTC" vs "BTC-USD-PERP").

**Solution**: Implement `get_contract_attributes()` to resolve and cache:

```python
async def get_contract_attributes(self, ticker: str) -> Tuple[str, Decimal]:
    # 1. Check cache first
    cached = self.contract_id_cache.get(ticker.upper())
    if cached:
        return cached, self.tick_size_cache.get(ticker.upper())
    
    # 2. Fetch from API
    contract_id = f"{ticker.upper()}-USD-PERP"  # Exchange-specific format
    metadata = await self.get_market_metadata(contract_id)
    
    # 3. Cache and return
    self.contract_id_cache[ticker.upper()] = contract_id
    self.tick_size_cache[ticker.upper()] = metadata['tick_size']
    return contract_id, metadata['tick_size']
```

### Order Book Depth Pattern

**Problem**: Need to fetch full depth (not just BBO) for liquidity analysis.

**Solution**: Try WebSocket first, fall back to REST:

```python
async def get_order_book_depth(self, contract_id: str, levels: int = 10):
    # 1. Try WebSocket (real-time, zero latency)
    if self.ws_manager and self.ws_manager.order_book_ready:
        order_book = self.ws_manager.get_order_book(levels=levels)
        if order_book:
            return order_book
    
    # 2. Fall back to REST API
    orderbook_data = self.api_client.fetch_orderbook(contract_id, {"depth": levels})
    # Parse and return
```

### Leverage Calculation Pattern

**Problem**: Exchanges use different leverage systems (IMF parameters vs direct max_leverage).

**Solution**: Extract from market metadata:

```python
async def get_leverage_info(self, symbol: str) -> Dict[str, Any]:
    metadata = await self.get_market_metadata(symbol)
    
    # If exchange provides max_leverage directly:
    max_leverage = metadata.get('max_leverage')
    
    # If exchange uses IMF parameters:
    imf_base = metadata.get('delta1_cross_margin_params', {}).get('imf_base')
    if imf_base:
        max_leverage = Decimal("1") / Decimal(str(imf_base))
    
    return {
        'max_leverage': max_leverage,
        'margin_requirement': Decimal("1") / max_leverage if max_leverage else None,
        # ...
    }
```

### Error Handling Pattern

**Problem**: Need consistent error handling across methods.

**Solution**: Use descriptive error messages:

```python
async def get_contract_attributes(self, ticker: str) -> Tuple[str, Decimal]:
    if not ticker:
        raise ValueError("Ticker is empty")
    
    # Check if market exists in trading metadata (not just funding data)
    markets = await self.api_client.fetch_markets({"market": contract_id})
    if not markets or not markets.get('results'):
        raise ValueError(
            f"Market {ticker} not found or not tradeable on {self.exchange_name}"
        )
    
    # ...
```

### Logging Pattern

**Problem**: Need consistent logging format across exchange.

**Solution**: Use unified logger with exchange prefix:

```python
from helpers.unified_logger import get_exchange_logger

class {Exchange}MarketData:
    def __init__(self, ...):
        self.logger = get_exchange_logger("{exchange_name}")
    
    async def get_order_book_depth(self, ...):
        self.logger.info(
            f"📚 [{EXCHANGE}] REST order book: {contract_id} "
            f"({len(bids)} bids, {len(asks)} asks)"
        )
```

---

## Reference Implementations

### Completed Exchanges (Use as References)

1. **Lighter** ✅ **COMPLETED** (Reference Implementation)
   - **Client**: `exchange_clients/lighter/client/`
   - **WebSocket**: `exchange_clients/lighter/websocket/`
   - **Funding Adapter**: `exchange_clients/lighter/funding_adapter/`
   - **Notes**: Most complete implementation, use as primary reference

2. **Paradex** ✅ **COMPLETED**
   - **Client**: `exchange_clients/paradex/client/`
   - **WebSocket**: `exchange_clients/paradex/websocket/`
   - **Funding Adapter**: `exchange_clients/paradex/funding_adapter/`
   - **Notes**: Recently refactored, follows Lighter pattern

3. **Aster** ✅ **COMPLETED**
   - **Client**: `exchange_clients/aster/client/`
   - **WebSocket**: `exchange_clients/aster/websocket/` (if refactored)
   - **Funding Adapter**: `exchange_clients/aster/funding_adapter/`
   - **Notes**: Good example of leverage management

4. **Backpack** ✅ **COMPLETED**
   - **Client**: `exchange_clients/backpack/client/`
   - **WebSocket**: `exchange_clients/backpack/websocket/` (if refactored)
   - **Funding Adapter**: `exchange_clients/backpack/funding_adapter/`
   - **Notes**: Good example of precision inference

### Base Interfaces (Required Reading)

- **`BaseExchangeClient`**: `exchange_clients/base_client.py`
  - Defines all required methods
  - Read docstrings for implementation details

- **`BaseWebSocketManager`**: `exchange_clients/base_websocket.py`
  - Defines WebSocket manager interface
  - Required methods: `connect()`, `disconnect()`, `prepare_market_feed()`, `get_order_book()`

- **`BaseFundingAdapter`**: `exchange_clients/base_funding_adapter.py`
  - Defines funding adapter interface
  - Required methods: `fetch_funding_rates()`, `fetch_market_data()`, `normalize_symbol()`

### Documentation References

- **Refactoring Plan**: `docs/EXCHANGE_CLIENT_REFACTOR_PLAN.md`
  - Detailed explanation of package structure
  - Design decisions and patterns

- **Paradex Refactoring**: `exchange_clients/paradex/REFACTORING_PLAN.md`
  - Step-by-step refactoring example
  - Common issues and solutions

---

## Quick Start Checklist

Use this checklist when adding a new exchange:

### Phase 1: Funding Adapter
- [ ] Create `funding_adapter/` package structure
- [ ] Implement `funding_client.py` (SDK/HTTP client management)
- [ ] Implement `fetchers.py` (funding rates + market data)
- [ ] Implement `adapter.py` (main orchestrator)
- [ ] Create/update `common.py` (symbol normalization)
- [ ] Update `__init__.py` exports
- [ ] Test funding rate fetching
- [ ] Test market data fetching
- [ ] Verify integration with `funding_rate_service`

### Phase 2: Client Package
- [ ] Create `client/` package structure
- [ ] Implement `utils/` (helpers, caching, converters)
- [ ] Implement `managers/market_data.py`
- [ ] Implement `managers/order_manager.py`
- [ ] Implement `managers/position_manager.py`
- [ ] Implement `managers/account_manager.py`
- [ ] Implement `managers/websocket_handlers.py`
- [ ] Implement `core.py` (main client)
- [ ] Update `__init__.py` exports
- [ ] Test all `BaseExchangeClient` methods
- [ ] Test with grid strategy
- [ ] Test with funding arbitrage strategy

### Phase 3: WebSocket Package
- [ ] Create `websocket/` package structure
- [ ] Implement `connection.py` (connection lifecycle)
- [ ] Implement `message_handler.py` (message parsing)
- [ ] Implement `order_book.py` (if applicable)
- [ ] Implement `market_switcher.py` (if applicable)
- [ ] Implement `manager.py` (main orchestrator)
- [ ] Update `__init__.py` exports
- [ ] Test WebSocket connection
- [ ] Test market switching
- [ ] Test order book updates
- [ ] Test order update callbacks

### Phase 4: Integration
- [ ] Update `pyproject.toml` dependencies
- [ ] Update `add_account.py` for credentials
- [ ] Update strategy config schemas
- [ ] Run full integration tests
- [ ] Document exchange-specific quirks

---

## Troubleshooting

### Common Issues

**Issue**: "Market not found" errors during opportunity validation
- **Solution**: Ensure `get_contract_attributes()` checks trading metadata (not just funding data)
- **Reference**: `exchange_clients/paradex/client/managers/market_data.py` (lines 244-290)

**Issue**: Order book prices are wrong (e.g., 0.00001 instead of 0.08)
- **Solution**: Check if exchange uses tick-grouped prices in order book stream
- **Solution**: Use BBO stream for exact prices, order book for depth
- **Reference**: `exchange_clients/paradex/client/managers/market_data.py` (lines 137-242)

**Issue**: WebSocket order book not populating
- **Solution**: Ensure snapshot is received before marking `order_book_ready`
- **Solution**: Handle `update_type='s'` (snapshot) vs `update_type='d'` (delta)
- **Reference**: `exchange_clients/paradex/websocket/order_book.py` (lines 52-131)

**Issue**: Contract ID cache not working for multi-symbol trading
- **Solution**: Ensure `ContractIdCache` supports dict-like access (`cache[key] = value`)
- **Solution**: Cache contract IDs in `get_contract_attributes()`
- **Reference**: `exchange_clients/paradex/client/utils/caching.py`

---

## Summary

Adding a new exchange requires:

1. **Funding Adapter** (1-2 days): Read-only, no credentials needed
2. **Client Package** (8-12 days): Trading operations, requires credentials
3. **WebSocket Package** (3-5 days): Real-time data streams
4. **Integration** (1-2 days): Testing and configuration

**Total Estimated Time**: 13-21 days (following established patterns)

**Key Success Factors**:
- ✅ Follow Lighter as reference implementation
- ✅ Implement all `BaseExchangeClient` methods
- ✅ Use unified logger for consistent logging
- ✅ Handle symbol resolution correctly
- ✅ Test with both grid and funding arbitrage strategies
- ✅ Document exchange-specific quirks

---

**Document Version**: 1.0  
**Last Updated**: 2025  
**Status**: Active Guide  
**Maintainer**: Exchange Integration Team

