# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Perp DEX Tools** is an advanced algorithmic trading system for perpetual futures across multiple decentralized exchanges. It consists of two main components:

1. **Trading Client** (`/perp-dex-tools` root) - In-process trading execution with multiple strategies
2. **Funding Rate Service** (`/funding_rate_service`) - FastAPI microservice for funding rate collection and arbitrage opportunity detection

## Development Commands

### Environment Setup
```bash
# Install all dependencies
make install
# OR manually:
python3 -m venv venv
source venv/bin/activate
pip install -r funding_rate_service/requirements.txt
pip install -e './exchange_clients[all]'

# Copy and configure environment variables
cp env_example.txt .env
# Edit .env with your API keys
```

### Running Trading Strategies

**Config File Mode (Recommended):**
```bash
# Create config interactively
python -m trading_config.config_builder

# Run with config
python runbot.py --config configs/your_config.yml
```

**CLI Mode (Quick testing):**
```bash
# Grid strategy
python runbot.py --strategy grid --exchange lighter --ticker BTC \
  --quantity 0.001 --take-profit 0.008 --direction buy --max-orders 10

# Funding arbitrage
python runbot.py --strategy funding_arbitrage --exchange lighter --ticker BTC \
  --target-exposure 1000 --min-profit-rate 0.001 --exchanges lighter,backpack,edgex
```

### Funding Rate Service

```bash
cd funding_rate_service

# Start PostgreSQL (Docker)
docker-compose up -d

# Initialize database
python scripts/init_db.py
python scripts/seed_dexes.py

# Run migrations
cd database/migrations
./RUN_ALL_MIGRATIONS.sh

# Start API service
cd ../..
uvicorn main:app --reload
```

### Testing

```bash
# Run all tests
pytest

# Run specific test suites
pytest tests/strategies/funding_arbitrage/
pytest tests/strategies/funding_arbitrage/test_funding_analyzer_isolated.py -v

# Run with markers
pytest -m unit
pytest -m integration

# Run single test
pytest tests/path/test_module.py::test_function_name
```

### Code Quality

```bash
# Format code (if black is configured)
black funding_rate_service

# Lint
flake8
```

## Architecture

### Core Design Principles

1. **Shared Exchange Library** (`/exchange_clients/`)
   - Single source of truth for each DEX implementation
   - Dual interfaces: `BaseExchangeClient` (trading) + `BaseFundingAdapter` (funding data)
   - Factory pattern for dynamic loading via `ExchangeFactory`
   - Isolated dependencies per exchange via `pyproject.toml`

2. **3-Level Strategy Hierarchy** (`/strategies/`)
   - **Level 1:** `BaseStrategy` - Core interface with event-driven lifecycle
   - **Level 2:** Strategy categories (`stateless_strategy.py`, `stateful_strategy.py`)
   - **Level 3:** Concrete implementations in `/implementations/` (grid, funding_arbitrage)

3. **Modular Execution Layer** (`/strategies/execution/`)
   - **Core utilities:** `order_executor.py`, `liquidity_analyzer.py`, `position_sizer.py`, `slippage_calculator.py`
   - **Patterns:** `atomic_multi_order.py` (critical for delta-neutral execution), `partial_fill_handler.py`
   - Shared across all strategies to ensure consistent execution quality

4. **Database-Backed Persistence**
   - PostgreSQL for positions (`strategy_positions`), funding payments (`funding_payments`), and state (`strategy_state`)
   - Migrations in `funding_rate_service/database/migrations/`
   - Run `RUN_ALL_MIGRATIONS.sh` to apply all migrations

### Key Data Flow

**Trading Client:**
```
runbot.py → trading_bot.py → strategy.execute_strategy() → exchange_client.place_order() → DEX API
```

**Funding Rate Service:**
```
Orchestrator → Collection Adapters (parallel) → PostgreSQL → FastAPI → Client
```

**Funding Arbitrage Strategy (uses both):**
```
FundingArbitrageStrategy → OpportunityFinder (direct internal call, no HTTP) →
AtomicMultiOrderExecutor → Multiple exchange_clients → PostgreSQL (position tracking)
```

### Exchange Client Interface

All exchange implementations must provide:
- `client.py` - Inherits `BaseExchangeClient`, implements trading operations
- `funding_adapter.py` - Inherits `BaseFundingAdapter`, implements funding rate collection
- `common.py` - Symbol normalization and shared utilities

Located in: `/exchange_clients/{lighter,grvt,edgex,aster,backpack,paradex}/`

### Strategy Implementation Pattern

New strategies should:
1. Inherit from `BaseStrategy` or category classes (`StatelessStrategy`, `StatefulStrategy`)
2. Define config using Pydantic models in `config.py`
3. Implement `get_strategy_name()`, `execute_strategy()`, `cleanup()`
4. Use shared components from `/strategies/components/` (position manager, fee calculator, tracked orders)
5. Use execution layer utilities from `/strategies/execution/` for order placement
6. Register in `StrategyFactory` in `/strategies/factory.py`

### Funding Arbitrage Strategy Architecture

3-phase execution loop:
1. **Monitor** - Track existing positions via `PositionMonitor`
2. **Exit Check** - Evaluate risk conditions and close positions via `PositionCloser`
3. **Entry Scan** - Find new opportunities via `OpportunityScanner` and open via `PositionOpener`

Critical components:
- `atomic_multi_order.py` - Ensures delta-neutral execution (NEVER place one-sided orders)
- `position_manager.py` - PostgreSQL-backed position tracking
- `risk_management/` - Pluggable rebalancing strategies (profit erosion, divergence flip, combined)

## Important Conventions

### Logging
**Always use the unified logger from `helpers/unified_logger.py`:**
```python
from helpers.unified_logger import get_strategy_logger, get_exchange_logger

# For strategies
logger = get_strategy_logger('funding_arbitrage', exchange='lighter', ticker='BTC')

# For exchange clients
logger = get_exchange_logger('aster', account_id='0x123...')

# Usage
logger.info("Position opened", extra={"position_id": pos_id, "size": size})
```

### Funding Rate Intervals ⚠️ CRITICAL
**Different exchanges have different funding payment intervals:**
- **Lighter:** 1 hour (pays/receives funding every hour)
- **All others (GRVT, EdgeX, Backpack, Aster, Paradex):** 8 hours

**Why this matters:**
- Without normalization, a 0.01% rate on Lighter (per 1h) looks the same as 0.01% on GRVT (per 8h)
- But Lighter's effective 8h rate is actually 0.08% (8x higher!)
- This affects opportunity detection, APY calculations, and profitability analysis

**How normalization works:**
1. Each exchange adapter specifies `funding_interval_hours` in `__init__()`
2. Adapters call `normalize_funding_rate_to_8h()` before returning rates
3. All rates in the database and calculations are in 8-hour intervals
4. Formula: `rate_8h = rate_native * (8 / native_interval_hours)`

**Implementation:**
```python
# In exchange adapter __init__:
super().__init__(
    dex_name="lighter",
    funding_interval_hours=1  # Specify native interval
)

# In fetch_funding_rates():
rate_native = Decimal(str(raw_rate))
rate_8h = self.normalize_funding_rate_to_8h(rate_native)  # Normalize before returning
```

### Configuration Files
- Strategy configs are YAML files in `/configs/`
- Use `trading_config/config_builder.py` for interactive config creation
- Schema validation via Pydantic models in strategy `config.py` files
- Never commit real API keys or secrets to configs

### Code Style
- Python 3.10+ required
- 4-space indentation, PEP 8 conventions
- Line length: 129 chars (per `.flake8`)
- Type hints required for public APIs (exchange_clients ships `py.typed`)
- Classes: PascalCase, modules/files: lowercase_with_underscores

### Testing Guidelines
- Tests mirror package structure in `/tests/`
- Use `@pytest.mark.asyncio` for async tests
- Use `@pytest.mark.unit` or `@pytest.mark.integration` markers
- Test file pattern: `test_*.py`
- Fixtures in `conftest.py`

### Database Migrations
When adding new database features:
1. Create migration SQL in `funding_rate_service/database/migrations/`
2. Name: `00X_descriptive_name.sql`
3. Test with `python scripts/run_migration.py migrations/00X_*.sql`
4. Update `RUN_ALL_MIGRATIONS.sh` to include new migration

## Supported Exchanges

| Exchange | Trading | Funding Data | Client Path |
|----------|---------|--------------|-------------|
| Lighter | ✅ | ✅ | `/exchange_clients/lighter/` |
| GRVT | ✅ | ✅ | `/exchange_clients/grvt/` |
| EdgeX | ✅ | ✅ | `/exchange_clients/edgex/` |
| Backpack | ✅ | ✅ | `/exchange_clients/backpack/` |
| Aster | ✅ | ✅ | `/exchange_clients/aster/` |
| Paradex | ⚠️ | ✅ | `/exchange_clients/paradex/` (dependency conflicts) |

## Key Files & Responsibilities

**Entry Points:**
- `runbot.py` - Trading bot CLI entry point
- `trading_bot.py` - Main trading orchestrator, manages strategy lifecycle
- `funding_rate_service/main.py` - FastAPI application entry point

**Strategy Framework:**
- `strategies/base_strategy.py` - Base strategy interface
- `strategies/factory.py` - Strategy factory for dynamic loading
- `strategies/execution/patterns/atomic_multi_order.py` - Critical for multi-DEX strategies

**Exchange Integration:**
- `exchange_clients/base.py` - Base interfaces (`BaseExchangeClient`, `BaseFundingAdapter`)
- `exchange_clients/factory.py` - Exchange factory for dynamic loading
- `exchange_clients/events.py` - Event system for liquidations

**Configuration:**
- `trading_config/config_builder.py` - Interactive config wizard
- `trading_config/config_yaml.py` - YAML config loading/validation

**Database:**
- `funding_rate_service/database/schema.sql` - Main schema definition
- `funding_rate_service/database/repositories/` - Data access layer
- `funding_rate_service/database/migrations/` - Migration scripts

**Logging:**
- `helpers/unified_logger.py` - Unified logging system (use this for all logging)

## Common Pitfalls

1. **Never place one-sided orders in funding arbitrage** - Always use `AtomicMultiOrderExecutor` to ensure delta neutrality
2. **Use unified logger** - Don't use raw `print()` or `logging` module directly
3. **Validate credentials** - Check for placeholder values in environment variables using `validate_credentials()` from `exchange_clients/base.py`
4. **Database connection** - Ensure PostgreSQL is running and migrations are applied before testing funding arbitrage
5. **Async context** - Most exchange operations are async, ensure proper `await` usage
6. **Symbol normalization** - Use `common.py` utilities in each exchange for consistent symbol formatting
7. **Funding rate intervals** - Different exchanges have different funding intervals (Lighter: 1h, Others: 8h). All rates MUST be normalized to 8h using `normalize_funding_rate_to_8h()` before comparison or calculation

## Related Documentation

- `README.md` - Project overview and quick start
- `docs/PROJECT_STRUCTURE.md` - Detailed architecture (667 lines)
- `docs/CLI_COMMANDS.md` - Complete CLI reference
- `docs/ADDING_EXCHANGES.md` - Guide for adding new DEX connectors
- `funding_rate_service/docs/API_ENDPOINTS.md` - REST API documentation (531 lines)
- `AGENTS.md` - Repository coding guidelines and conventions

## Recent Major Changes (v2.0 → v2.5)

- **v2.0:** Unified exchange library, eliminated code duplication across 6 DEXs
- **v2.1:** Modular strategy architecture with Hummingbot-inspired patterns
- **v2.5:** Interactive configuration system with YAML support, multi-exchange trading bot

The codebase has grown 88% from strategic refactoring with improved modularity, testability, and production-ready funding arbitrage with database persistence.
