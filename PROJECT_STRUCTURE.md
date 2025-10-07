# 📁 Project Structure

## Overview

This document outlines the complete structure of the `perp-dex-tools` repository, which contains both a **Trading Client** and a **Funding Rate Service**.

---

## 🏗️ Top-Level Structure

```
/perp-dex-tools/
├── ARCHITECTURE.md                    # Trading bot architecture documentation
├── PROJECT_STRUCTURE.md               # This file
├── README.md                          # Main project README
├── LICENSE                            # Project license
├── requirements.txt                   # Trading client dependencies
├── para_requirements.txt              # Paradex-specific dependencies
├── env_example.txt                    # Environment variables template
│
├── runbot.py                          # Trading bot entry point (CLI)
├── trading_bot.py                     # Main trading orchestrator
│
├── /docs/                             # Project documentation
│   ├── telegram-bot-setup.md
│   ├── telegram-bot-setup-en.md
│   ├── ADDING_EXCHANGES.md
│   └── /tasks/                        # Task planning documents
│       ├── funding_arb_client_server_design.md
│       └── some_questions.md
│
├── /exchange_clients/                 # 🔥 SHARED EXCHANGE LIBRARY
│   ├── __init__.py
│   ├── base.py                        # BaseExchangeClient & BaseFundingAdapter interfaces
│   ├── pyproject.toml                 # Dependency management
│   │
│   ├── /lighter/                      # Lighter DEX implementation
│   │   ├── __init__.py
│   │   ├── client.py                  # Trading execution client
│   │   ├── funding_adapter.py         # Funding rate collection adapter
│   │   ├── lighter_custom_websocket.py # Custom WebSocket manager
│   │   └── common.py                  # Shared utilities
│   │
│   ├── /grvt/                         # GRVT DEX implementation
│   │   ├── __init__.py
│   │   ├── client.py                  # Trading execution client
│   │   ├── funding_adapter.py         # Funding rate collection adapter
│   │   └── common.py                  # Shared utilities
│   │
│   └── /edgex/                        # EdgeX DEX implementation
│       ├── __init__.py
│       ├── client.py                  # Trading execution client
│       ├── funding_adapter.py         # Funding rate collection adapter
│       └── common.py                  # Shared utilities
│
├── /exchanges/                        # 🎯 LEGACY EXCHANGE CLIENTS (Non-migrated)
│   ├── __init__.py
│   ├── factory.py                     # ExchangeFactory (dynamic loading)
│   ├── paradex.py                     # Paradex DEX client
│   ├── backpack.py                    # Backpack client
│   └── aster.py                       # Aster client
│
├── /strategies/                       # 🧠 TRADING STRATEGY LAYER
│   ├── __init__.py
│   ├── base_strategy.py               # BaseStrategy interface
│   ├── factory.py                     # StrategyFactory
│   ├── grid_strategy.py               # Grid trading strategy
│   └── funding_arbitrage_strategy.py  # Funding rate arbitrage strategy
│
├── /helpers/                          # 🛠️ SHARED UTILITIES
│   ├── __init__.py
│   ├── logger.py                      # Trading logger
│   ├── telegram_bot.py                # Telegram notifications
│   ├── lark_bot.py                    # Lark (Feishu) notifications
│   └── risk_manager.py                # Risk management (account protection)
│
├── /tests/                            # Trading client tests
│   └── test_query_retry.py
│
└── /funding_rate_service/             # 📊 FUNDING RATE MICROSERVICE
    └── (See detailed structure below)
```

---

## 📊 Funding Rate Service Structure

**Purpose:** Independent microservice for collecting, storing, and serving funding rate data from multiple DEXs.

```
/funding_rate_service/
├── README.md                          # Service documentation
├── PROGRESS.md                        # Development progress tracker
├── requirements.txt                   # Service dependencies
├── docker-compose.yml                 # Docker setup for PostgreSQL
│
├── main.py                            # 🚀 FastAPI application entry point
├── config.py                          # Configuration management
│
├── /docs/                             # Service documentation
│   ├── QUICKSTART.md                  # Getting started guide
│   ├── API_ENDPOINTS.md               # API documentation (531 lines)
│   └── OPPORTUNITIES_FLOW.md          # Opportunity calculation flow
│
├── /api/                              # 🌐 REST API LAYER
│   ├── __init__.py
│   └── /routes/                       # API route handlers
│       ├── __init__.py
│       ├── health.py                  # Health check endpoints
│       ├── dexes.py                   # DEX management endpoints
│       ├── funding_rates.py           # Funding rate endpoints
│       └── opportunities.py           # Arbitrage opportunity endpoints
│
├── /collection/                       # 📡 DATA COLLECTION LAYER
│   ├── __init__.py
│   ├── orchestrator.py                # Orchestrates all adapters (480 lines)
│   ├── base_adapter.py                # BaseDEXAdapter interface
│   │
│   └── /adapters/                     # Adapter import layer (uses exchange_clients)
│       ├── __init__.py                # Imports from exchange_clients
│       ├── README.md                  # Adapter documentation
│       └── paradex_adapter.md         # Paradex adapter (commented out)
│
├── /core/                             # 🧮 BUSINESS LOGIC LAYER
│   ├── __init__.py
│   ├── dependencies.py                # FastAPI dependencies
│   ├── fee_calculator.py              # Fee calculation logic
│   ├── historical_analyzer.py         # Historical data analysis
│   ├── mappers.py                     # Symbol mapping utilities
│   └── opportunity_finder.py          # Arbitrage opportunity finder
│
├── /models/                           # 📦 DATA MODELS
│   ├── __init__.py
│   ├── dex.py                         # DEX model
│   ├── symbol.py                      # Symbol model
│   ├── funding_rate.py                # Funding rate model
│   ├── opportunity.py                 # Arbitrage opportunity model
│   ├── filters.py                     # Filter models
│   ├── history.py                     # Historical data models
│   └── system.py                      # System status models
│
├── /database/                         # 🗄️ DATABASE LAYER
│   ├── __init__.py
│   ├── connection.py                  # Database connection management
│   ├── schema.sql                     # Database schema definition
│   │
│   ├── /repositories/                 # Data access layer
│   │   ├── __init__.py
│   │   ├── dex_repository.py
│   │   ├── symbol_repository.py
│   │   ├── funding_rate_repository.py
│   │   └── opportunity_repository.py
│   │
│   └── /migrations/                   # Database migrations
│       ├── 001_add_dex_symbols_updated_at.sql
│       ├── 002_add_missing_opportunity_columns.sql
│       ├── 003_rename_opportunity_dex_fields.sql
│       └── RUN_ALL_MIGRATIONS.sh
│
├── /utils/                            # 🔧 UTILITIES
│   ├── __init__.py
│   └── logger.py                      # Service logger
│
├── /scripts/                          # 🛠️ UTILITY SCRIPTS
│   ├── __init__.py
│   ├── init_db.py                     # Database initialization
│   ├── seed_dexes.py                  # Seed DEX data
│   ├── run_migration.py               # Run migrations
│   ├── test_collection_system.py      # Test collection system
│   ├── test_all_adapters.py           # Test all adapters
│   ├── test_lighter_adapter.py        # Test Lighter adapter
│   ├── test_phase3.py                 # Phase 3 testing
│   └── run_api_tests.py               # API testing
│
├── /tests/                            # 🧪 TEST SUITE
│   ├── /test_api/                     # API endpoint tests
│   │   ├── __init__.py
│   │   ├── conftest.py                # Test fixtures
│   │   ├── test_health.py
│   │   ├── test_dexes.py
│   │   ├── test_funding_rates.py
│   │   └── test_opportunities.py
│   ├── /test_collection/              # Collection system tests
│   └── /test_core/                    # Core logic tests
│
├── /sdks/                             # 📚 EXTERNAL SDKs (local clones)
│   ├── /lighter-python/               # Lighter SDK
│   ├── /grvt-pysdk/                   # GRVT SDK
│   └── /edgex-sdk/                    # EdgeX SDK (if applicable)
│
├── /cache/                            # 📦 CACHE DIRECTORY (runtime)
│
├── /tasks/                            # 📝 TASK TRACKING
│
└── /venv/                             # Python virtual environment
```

---

## 🔑 Key Architectural Components

### **Trading Client (`/perp-dex-tools` root)**

| Component | Purpose | Key Files |
|-----------|---------|-----------|
| **Entry Point** | CLI for running trading strategies | `runbot.py` |
| **Orchestrator** | Coordinates strategies + exchanges | `trading_bot.py` |
| **Exchanges** | Execute trades on DEXs | `/exchanges/lighter.py`, etc. |
| **Strategies** | Trading decision logic | `/strategies/grid_strategy.py`, etc. |
| **Helpers** | Logging, notifications, risk mgmt | `/helpers/logger.py`, etc. |

**Key Trait:** **In-process execution** (fast, low-latency trading)

---

### **Funding Rate Service (`/funding_rate_service`)**

| Layer | Purpose | Key Components |
|-------|---------|----------------|
| **API Layer** | REST endpoints for querying data | `/api/routes/*.py` |
| **Collection Layer** | Fetch funding rates from DEXs | `/collection/adapters/*.py` |
| **Core Layer** | Business logic (opportunity finding) | `/core/opportunity_finder.py` |
| **Database Layer** | PostgreSQL data persistence | `/database/repositories/*.py` |
| **Models** | Pydantic data models | `/models/*.py` |

**Key Trait:** **Microservice architecture** (isolated adapters, shared DB, REST API)

---

## 📊 Data Flow

### **Trading Client Flow:**
```
User → runbot.py
     → trading_bot.py (orchestrator)
     → strategy.execute_strategy()
     → exchange_client.place_order()
     → DEX API (Lighter/GRVT/etc.)
```

### **Funding Rate Service Flow:**
```
Orchestrator (cron/scheduler)
  ↓
Collection Adapters (parallel)
  ├─> Lighter Adapter → Lighter API
  ├─> GRVT Adapter → GRVT API
  └─> EdgeX Adapter → EdgeX API
  ↓
PostgreSQL Database
  ↓
FastAPI REST API
  ↓
Client (trading bot or external)
```

---

## 🔄 Interaction Between Services

```
┌─────────────────────────────────┐
│   Trading Client (runbot.py)    │
│                                 │
│  ┌──────────────────────────┐  │
│  │ Funding Arbitrage        │  │
│  │ Strategy                 │  │
│  └───────────┬──────────────┘  │
│              │                  │
│              │ Query funding    │
│              │ rates            │
└──────────────┼──────────────────┘
               │
               │ HTTP GET /api/v1/opportunities
               ↓
┌─────────────────────────────────┐
│   Funding Rate Service API      │
│   (localhost:8000)              │
│                                 │
│   Returns cached funding data   │
└─────────────────────────────────┘
```

---

## 🚀 Running the System

### **1. Trading Client**
```bash
# From /perp-dex-tools
python runbot.py --strategy grid --exchange lighter --ticker BTC ...
```

### **2. Funding Rate Service**
```bash
# From /perp-dex-tools/funding_rate_service
uvicorn main:app --reload
```

### **3. Database**
```bash
# From /perp-dex-tools/funding_rate_service
docker-compose up -d  # Start PostgreSQL
python scripts/init_db.py  # Initialize schema
python scripts/seed_dexes.py  # Seed DEX data
```

---

## 📝 File Count Summary

**Total Repository:**
- **Trading Client Core:** ~15 Python files
- **Exchanges:** 7 exchange implementations
- **Strategies:** 2 strategies (grid, funding arb)
- **Funding Rate Service:** ~50+ Python files
- **Tests:** ~15 test files
- **Documentation:** ~10 markdown files

**Total Lines of Code (estimated):**
- Trading Client: ~3,000 lines
- Funding Rate Service: ~5,000 lines
- **Total: ~8,000 lines**

---

## 🎯 Design Philosophy

### **Shared Exchange Library** (NEW!)
- **Single source of truth** for each exchange implementation
- **Dual interfaces:** `BaseExchangeClient` (trading) + `BaseFundingAdapter` (data collection)
- **Isolated dependencies** per exchange via `pyproject.toml`
- **Shared utilities** in `common.py` to eliminate duplication

### **Trading Client**
- **Monolithic in-process execution** for low latency
- **Strategy-Exchange separation** via clean interfaces
- **Exchange-agnostic** strategy layer
- **Uses exchange_clients library** for execution

### **Funding Rate Service**
- **Uses exchange_clients library** for data collection
- **Shared database** for centralized storage
- **REST API** for querying cached data
- **Two-phase filtering** (discovery vs. execution)

---

## 📦 Dependencies

### **Shared Exchange Library** (`exchange_clients/pyproject.toml`)
- **Core:** `asyncio`, `aiohttp`, `websockets`, `tenacity`
- **Optional dependencies per exchange:**
  - `lighter`: `lighter-python>=0.5.0`, `eth-account>=0.8.0`
  - `grvt`: `grvt-pysdk` (CCXT-based)
  - `edgex`: `edgex-sdk`, `httpx>=0.24.0`
  - `all`: Installs all exchange dependencies

### **Trading Client** (`requirements.txt`)
- `paradex-py` (Paradex SDK - not yet migrated)
- `bpx` (Backpack SDK - not yet migrated)
- WebSocket libraries
- Starknet libraries
- **Note:** Lighter, GRVT, EdgeX now via `exchange_clients[all]`

### **Funding Rate Service** (`funding_rate_service/requirements.txt`)
- `fastapi` - REST API framework
- `uvicorn` - ASGI server
- `databases[asyncpg]` - Async PostgreSQL
- `pydantic` - Data validation
- **Note:** Exchange adapters now via `exchange_clients[all]`

---

## 🔮 Future Evolution

**Phase 1 (Current):**
- ✅ Trading client as monolith
- ✅ Funding rate service with adapter microservices

**Phase 2 (Planned):**
- Evaluate selective trading microservices
- Gateway layer for routing
- Independent scaling per exchange

**Phase 3 (Future):**
- Full microservices if needed
- gRPC for low-latency trading
- Service mesh for orchestration

---

**Last Updated:** 2025-10-07  
**Version:** 2.0 (Shared Exchange Library Architecture)  
**Status:** Active Development

---

## 🔄 Recent Major Refactoring (v2.0)

**Completed:** Shared Exchange Library Architecture

We successfully refactored the codebase to eliminate code duplication between the trading client and funding rate service:

### What Changed:
- ✅ **Created `/exchange_clients/`** - Shared library for all exchange implementations
- ✅ **Migrated 3 exchanges:** Lighter, GRVT, EdgeX
- ✅ **Dual interfaces:** Each exchange now has both `client.py` (trading) and `funding_adapter.py` (data collection)
- ✅ **Eliminated duplication:** Single implementation per exchange instead of 2
- ✅ **Isolated dependencies:** Per-exchange dependencies via `pyproject.toml`
- ✅ **Updated all imports:** Factory, adapters, and test files now use new structure

### Benefits:
- **50% less code** for migrated exchanges (no duplication)
- **Consistent behavior** between trading and data collection
- **Easier maintenance** - update once, works everywhere
- **Better dependency management** - install only what you need

