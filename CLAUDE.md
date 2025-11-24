# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Python-based trading application for executing automated strategies across multiple decentralized perpetual exchanges (DEXs). The system supports grid trading, funding rate arbitrage, and other strategies with a modular architecture that enables multi-exchange operations, proxy rotation, and Telegram bot control.

## Build and Development Commands

### Installation
```bash
# Install all dependencies (Python + Supervisor)
make install

# Install Python dependencies only
pip install -r funding_rate_service/requirements.txt
pip install -e './exchange_clients[all]'

# Clean virtual environment
make clean
```

### Database Setup
```bash
# Initialize database schema
python database/scripts/setup/init_db.py

# Seed reference data
python database/scripts/setup/seed_dexes.py

# Run migrations
python database/scripts/migrations/run_all_migrations.py

# Add trading account from .env
python database/scripts/accounts/add_account.py --from-env --account-name main_bot
```

### Running the Bot
```bash
# Run trading bot with config file
python runbot.py --config configs/my_strategy.yml

# Run with specific account (credentials from database)
python runbot.py --config configs/my_strategy.yml --account acc1

# Enable proxy rotation
python runbot.py --config configs/my_strategy.yml --enable-proxy

# Enable control API for live management
python runbot.py --config configs/my_strategy.yml --enable-control-api

# Set log level
python runbot.py --config configs/my_strategy.yml --log-level DEBUG
```

### Services
```bash
# Run funding rate service
cd funding_rate_service && python main.py
# or
uvicorn funding_rate_service.main:app --reload --port 8000

# Run Telegram bot service
python telegram_bot_service/main.py

# Start control API server (standalone)
python scripts/start_control_api.py
```

### Testing
```bash
# Run all tests
make test
# or
pytest tests

# Run specific test file
pytest tests/strategies/funding_arbitrage/test_position_opener.py

# Run with verbose output
pytest -v tests/

# Run integration tests
pytest tests/integration/

# Run unit tests
pytest tests/strategies/
```

## Architecture Overview

### Core Design Patterns

**Factory Pattern**: Strategies and exchange clients are instantiated via factory classes:
- `strategies/factory.py` - Dynamically loads strategy classes
- `exchange_clients/factory.py` - Creates exchange client instances

**Base Class Pattern**: All major components inherit from base classes:
- `strategies/base_strategy.py` - All strategies must implement `should_execute()` and `execute_strategy()`
- `exchange_clients/base_client.py` - All exchange clients implement trading operations (place_order, get_positions, etc.)
- `exchange_clients/base_websocket.py` - WebSocket handlers for real-time market data
- `strategies/execution/core/execution_strategies/base.py` - Execution strategies inherit websocket infrastructure for event-driven order tracking

**Multi-Exchange Support**: The system supports both single-exchange and multi-exchange strategies:
- Single-exchange (e.g., Grid): Uses one `exchange_client`
- Multi-exchange (e.g., Funding Arbitrage): Uses `exchange_clients` dict with multiple clients

### Key Architectural Components

**Trading Bot Entry Point** (`runbot.py` -> `trading_bot.py`):
- Loads YAML configuration files from `configs/` directory
- Supports database-backed credentials via `--account` flag
- Initializes proxy rotation if enabled
- Creates exchange client(s) via factory
- Instantiates strategy via factory
- Runs main trading loop: `while not shutdown_requested: await strategy.execute_strategy()`

**Strategy Execution Layer** (`strategies/execution/`):
The execution layer is a facade pattern providing multiple execution strategies:
- `core/order_executor.py` - **OrderExecutor** is the main facade (unified execution interface)
- `core/execution_strategies/` - Execution strategy implementations:
  - `base.py` - ExecutionStrategy ABC with websocket infrastructure
  - `aggressive_limit.py` - AggressiveLimitExecutionStrategy (retries with adaptive pricing)
  - `simple_limit.py` - SimpleLimitExecutionStrategy (single limit order attempt)
  - `market.py` - MarketExecutionStrategy (immediate market execution)
- `core/execution_components/` - Supporting components:
  - `pricer.py` - AggressiveLimitPricer (inside-spread pricing)
  - `reconciler.py` - OrderReconciler (polling-based order reconciliation)
  - `event_reconciler.py` - EventBasedReconciler (websocket-based order tracking)
  - `order_tracker.py` - OrderTracker (order state tracking)

**Atomic Multi-Order Pattern** (`strategies/execution/patterns/atomic_multi_order/`):
Used for strategies requiring coordinated multi-leg execution (e.g., funding arbitrage):
- `executor.py` - AtomicMultiOrderExecutor orchestrates multi-leg orders
- `contexts.py` - OrderContext tracks individual order state
- `components/hedge_manager.py` - Manages hedge execution to neutralize exposure
- `components/rollback_manager.py` - Handles rollback on partial fills
- Pattern ensures atomicity: all legs succeed or all are rolled back

**Proxy System** (`networking/`):
- `selector.py` - ProxySelector manages proxy rotation
- `session_proxy.py` - SessionProxyManager applies proxies to aiohttp sessions
- Database stores proxy assignments per account with priority levels
- Health monitoring rotates to backup proxies on failures

**Unified Logging** (`helpers/unified_logger.py`):
- **Always use `get_logger(component_type, component_name, context)` for logging**
- Provides colored console output with source location
- File logging with rotation
- Component-specific context (exchange, ticker, strategy)
- Set via `.cursor/rules/adding-unified-logs.mdc`: "always use the /helper/unified-logs.py for adding logs"

### Critical Multi-Symbol Trading Pattern

When implementing multi-symbol strategies (like funding arbitrage), use the per-symbol contract ID cache pattern:

```python
# CORRECT: Get contract_id for each symbol dynamically
contract_id = await exchange_client.get_contract_id(symbol="BTC")
tick_size = await exchange_client.get_tick_size(symbol="BTC")

# INCORRECT: Using self.config.contract_id for multi-symbol strategies
# This only works for single-symbol strategies like grid trading
```

The base client maintains `_contract_id_cache: Dict[str, str]` to cache symbol->contract_id mappings.

### Strategy Implementation

All strategies must inherit from `BaseStrategy` and implement:

```python
class MyStrategy(BaseStrategy):
    async def should_execute(self) -> bool:
        """Return True if strategy should execute."""
        pass

    async def execute_strategy(self) -> None:
        """Execute strategy logic."""
        pass

    async def cleanup(self) -> None:
        """Cleanup resources on shutdown."""
        pass
```

**Strategy Types:**
- **Grid** (`strategies/implementations/grid/`) - Single-exchange grid trading
- **Funding Arbitrage** (`strategies/implementations/funding_arbitrage/`) - Multi-exchange funding rate arbitrage

### Exchange Client Implementation

When adding new exchange support (see `docs/ADDING_NEW_EXCHANGES.md`), implement:

1. **Client** (`exchange_clients/{exchange}/client.py`):
   - Inherit from `BaseExchangeClient`
   - Implement trading operations (place_order, cancel_order, get_positions)
   - Manage WebSocket connections for real-time data

2. **WebSocket** (`exchange_clients/{exchange}/websocket.py`):
   - Inherit from `BaseWebSocketManager`
   - Handle real-time order fills, position updates, price feeds

3. **Funding Adapter** (`exchange_clients/{exchange}/funding_adapter.py`):
   - Inherit from `BaseFundingAdapter`
   - Fetch funding rates for the funding rate service

### Database-Backed Credentials

The system supports encrypted multi-account credentials:
- Accounts stored in `accounts` table with encrypted credentials
- Proxy assignments in `account_proxy_assignments` table
- Encryption key required in `.env` as `CREDENTIAL_ENCRYPTION_KEY`
- Use `--account` flag in `runbot.py` to load credentials from database instead of env vars

### Control API

The control API allows runtime management of strategies:
- Embedded server runs in funding_arbitrage strategies when `--enable-control-api` is set
- Endpoints: `/api/v1/status`, `/api/v1/positions`, `/api/v1/config/reload`
- Implemented in `strategies/control/server.py` using FastAPI
- Controllers in `strategies/control/{strategy}_controller.py`

## Common Development Patterns

### Adding Logs
**Always use UnifiedLogger from helpers/unified_logger.py** (per .cursor/rules/adding-unified-logs.mdc):
```python
from helpers.unified_logger import get_logger

logger = get_logger("strategy", "funding_arbitrage", context={"ticker": "BTC"})
logger.info("Position opened")
logger.error("Failed to execute", exc_info=True)
```

### Creating a New Strategy
1. Create strategy class in `strategies/implementations/{name}/`
2. Inherit from `BaseStrategy`
3. Implement `should_execute()`, `execute_strategy()`, `cleanup()`
4. Register in `strategies/factory.py`
5. Add strategy config schema to `strategies/base_schema.py`

### Using Execution Strategies
```python
from strategies.execution.core.order_executor import OrderExecutor
from strategies.execution.core.execution_types import ExecutionMode

executor = OrderExecutor(exchange_client)

# Market execution
result = await executor.execute(
    symbol="BTC",
    side="buy",
    size_usd=1000,
    mode=ExecutionMode.MARKET
)

# Aggressive limit with retries
result = await executor.execute(
    symbol="BTC",
    side="buy",
    size_usd=1000,
    mode=ExecutionMode.AGGRESSIVE_LIMIT,
    max_retries=3
)
```

### Atomic Multi-Order Execution
```python
from strategies.execution.patterns.atomic_multi_order.executor import AtomicMultiOrderExecutor

executor = AtomicMultiOrderExecutor(
    exchange_clients={"aster": aster_client, "lighter": lighter_client},
    logger=logger
)

result = await executor.execute_atomic(
    legs=[
        {"exchange": "aster", "symbol": "BTC", "side": "buy", "size_usd": 1000},
        {"exchange": "lighter", "symbol": "BTC", "side": "sell", "size_usd": 1000}
    ],
    hedge_mode="aggressive_limit"
)
```

## Important Notes

### Configuration Files
- Strategy configs stored as YAML in `configs/` directory
- Generate via `python -m trading_config.config_builder` (interactive)
- Config structure defined in `trading_config/config_yaml.py`

### Supervisor Integration
- Telegram bot launches strategies as Supervisor processes
- Supervisor must have XML-RPC enabled on port 9001
- Use `make install-supervisor` to configure

### Shutdown Handling
- Graceful shutdown via CTRL+C triggers `bot.graceful_shutdown()`
- Double CTRL+C within 2 seconds forces immediate exit
- Cleanup order: strategy cleanup -> control server stop -> exchange disconnect

### WebSocket Order Tracking
- All execution strategies inherit websocket infrastructure from `ExecutionStrategy` base class
- Event-driven order fill detection reduces reliance on polling
- Reconciler pattern supports both polling (`OrderReconciler`) and event-based (`EventBasedReconciler`) approaches

### Testing
- Unit tests in `tests/strategies/` and `tests/exchange_clients/`
- Integration tests in `tests/integration/`
- Use `pytest.mark.asyncio` for async tests
- Mock exchange clients using `unittest.mock.AsyncMock`

## Key File Locations

- **Bot entry point**: `runbot.py` (CLI) -> `trading_bot.py` (core logic)
- **Strategy base**: `strategies/base_strategy.py`
- **Exchange base**: `exchange_clients/base_client.py`
- **Execution facade**: `strategies/execution/core/order_executor.py`
- **Atomic pattern**: `strategies/execution/patterns/atomic_multi_order/executor.py`
- **Config builder**: `trading_config/config_builder.py`
- **Unified logging**: `helpers/unified_logger.py`
- **Database scripts**: `database/scripts/` (accounts, proxies, migrations)

## Documentation References

Consult these docs for detailed information:
- `docs/ARCHITECTURE.md` - Detailed system architecture
- `docs/ADDING_NEW_EXCHANGES.md` - Guide for adding exchange support
- `docs/ATOMIC_EXECUTOR_PATTERN.md` - Atomic multi-order execution pattern
- `docs/HEDGE_MANAGER_FLOW.md` - Hedge manager execution flow
- `docs/PRICING_MECHANISMS.md` - Pricing strategies for limit orders
- `database/MULTI_ACCOUNT_SETUP.md` - Multi-account configuration
