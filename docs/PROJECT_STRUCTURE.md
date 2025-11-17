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
├── /trading_config/                   # 🎨 INTERACTIVE CONFIGURATION SYSTEM
│   ├── __init__.py                    # Config module exports
│   ├── config_builder.py              # Interactive wizard for strategy configs
│   └── config_yaml.py                 # YAML file loading/saving/validation
│
├── /configs/                          # 📁 Saved configuration files (YAML)
│   ├── example_funding_arbitrage.yml  # Example funding arb config
│   └── example_grid.yml               # Example grid config
│
├── /docs/                             # 📚 PUBLIC DOCUMENTATION
│   ├── ARCHITECTURE.md                # System architecture (v2.6)
│   ├── PROJECT_STRUCTURE.md           # This file
│   ├── MULTI_ACCOUNT_DB_ARCHITECTURE.md  # Multi-account design spec
│   ├── QUICK_START.md                 # Getting started guide
│   ├── CLI_COMMANDS.md                # CLI usage guide
│   ├── telegram-bot-setup.md
│   ├── telegram-bot-setup-en.md
│   └── ADDING_EXCHANGES.md
│
├── /docs-internal/                    # 🔒 INTERNAL DEVELOPMENT DOCS (git-ignored)
│   ├── /strategies_refactor/          # Strategy refactor planning & documentation
│   │   ├── final_refactor_plan_with_hummingbot.md  # Master refactor plan
│   │   ├── WHATS_LEFT.md              # Remaining tasks tracker
│   │   └── HUMMINGBOT_EXECUTION_PATTERNS.md        # Execution layer design
│   │
│   ├── /hummingbot_reference/         # Extracted Hummingbot patterns (reference)
│   │   ├── EXTRACTION_SUMMARY.md
│   │   ├── /position_executor/
│   │   ├── /funding_payments/
│   │   └── /cli_display/
│   │
│   ├── /hummingbot_patterns/          # Simplified Hummingbot code patterns
│   │   ├── executor_base_pattern.py
│   │   ├── position_hold_pattern.py
│   │   ├── funding_rate_calcs.py
│   │   ├── tracked_order_pattern.py
│   │   └── fee_calculation_pattern.py
│   │
│   ├── /multi_account/                # 🔐 Multi-account implementation docs (v2.6)
│   │   ├── EXCHANGE_CLIENT_CREDENTIAL_REFACTOR.md
│   │   ├── EXCHANGE_FACTORY_CREDENTIAL_UPDATE.md
│   │   ├── RUNBOT_ACCOUNT_INTEGRATION.md
│   │   └── MULTI_ACCOUNT_IMPLEMENTATION_SUMMARY.md
│   │
│   └── /tasks/                        # Task planning documents
│       ├── funding_arb_client_server_design.md
│       ├── high_level_overview_strategies.md
│       ├── detailed_strategies_refactor.md
│       └── some_questions.md
│
├── /exchange_clients/                 # 🔥 SHARED EXCHANGE LIBRARY
│   ├── __init__.py
│   ├── base.py                        # BaseExchangeClient & BaseFundingAdapter interfaces
│   ├── factory.py                     # ExchangeFactory (dynamic loading)
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
│   ├── /edgex/                        # EdgeX DEX implementation
│   │   ├── __init__.py
│   │   ├── client.py                  # Trading execution client
│   │   ├── funding_adapter.py         # Funding rate collection adapter
│   │   └── common.py                  # Shared utilities
│   │
│   ├── /aster/                        # Aster DEX implementation
│   │   ├── __init__.py
│   │   ├── client.py                  # Trading execution client
│   │   ├── funding_adapter.py         # Funding rate collection adapter
│   │   └── common.py                  # Shared utilities
│   │
│   ├── /backpack/                     # Backpack DEX implementation
│   │   ├── __init__.py
│   │   ├── client.py                  # Trading execution client
│   │   ├── funding_adapter.py         # Funding rate collection adapter
│   │   └── common.py                  # Shared utilities
│   │
│   └── /paradex/                      # Paradex DEX implementation
│       ├── __init__.py
│       ├── client.py                  # Trading execution client
│       ├── funding_adapter.py         # Funding rate collection adapter
│       └── common.py                  # Shared utilities
│
├── /strategies/                       # 🧠 TRADING STRATEGY LAYER (REFACTORED v2.0)
│   ├── __init__.py                    # Strategy exports & RunnableStatus enum
│   ├── base_strategy.py               # Enhanced BaseStrategy with event-driven lifecycle
│   ├── factory.py                     # StrategyFactory for dynamic loading
│   ├── grid_strategy_LEGACY.py        # Legacy grid implementation (preserved)
│   │
│   ├── /categories/                   # Level 2: Strategy archetypes
│   │   ├── __init__.py
│   │   ├── stateless_strategy.py      # Simple, single-DEX strategies
│   │   └── stateful_strategy.py       # Complex, multi-DEX strategies
│   │
│   ├── /components/                   # Shared reusable components
│   │   ├── __init__.py
│   │   ├── base_components.py         # Component interfaces (BasePositionManager, etc.)
│   │   ├── tracked_order.py           # Lightweight order tracking
│   │   └── fee_calculator.py          # Trading fee calculation
│   │
│   ├── /execution/                    # Layer 2: Shared execution utilities
│   │   ├── __init__.py
│   │   ├── /core/                     # Fundamental execution utilities
│   │   │   ├── __init__.py
│   │   │   ├── order_executor.py      # Smart order placement (limit/market/fallback)
│   │   │   ├── liquidity_analyzer.py  # Pre-flight liquidity checks
│   │   │   ├── position_sizer.py      # USD ↔ contract quantity conversion
│   │   │   └── slippage_calculator.py # Slippage estimation & tracking
│   │   ├── /patterns/                 # Advanced execution patterns
│   │   │   ├── __init__.py
│   │   │   ├── atomic_multi_order.py  # 🔥 CRITICAL: Delta-neutral atomic execution
│   │   │   └── partial_fill_handler.py # Emergency one-sided fill management
│   │   └── /monitoring/               # Execution monitoring & analytics
│   │       ├── __init__.py
│   │       └── execution_tracker.py   # Execution quality metrics
│   │
│   └── /implementations/              # Level 3: Concrete strategies
│       ├── __init__.py
│       ├── /grid/                     # Grid trading strategy (migrated)
│       │   ├── __init__.py
│       │   ├── strategy.py            # GridStrategy implementation
│       │   ├── config.py              # Pydantic configuration
│       │   └── models.py              # Grid-specific data models
│       │
│       └── /funding_arbitrage/        # 🔥 Funding arbitrage strategy
│           ├── __init__.py
│           ├── strategy.py            # Main orchestrator (3-phase execution loop)
│           ├── config.py              # Pydantic configuration models
│           ├── models.py              # Position & opportunity data models
│           ├── funding_analyzer.py    # Core rate analysis (from Hummingbot)
│           ├── position_manager.py    # Position tracking with PostgreSQL persistence
│           ├── state_manager.py       # Strategy state with PostgreSQL persistence
│           │
│           └── /risk_management/      # Pluggable risk management system
│               ├── __init__.py        # Factory pattern
│               ├── base.py            # BaseRebalanceStrategy interface
│               ├── profit_erosion.py  # Profit erosion trigger
│               ├── divergence_flip.py # Divergence flip trigger (urgent)
│               └── combined.py        # Multi-strategy orchestrator
│
├── /database/                         # 🗄️ DATABASE LAYER (Shared with Funding Service)
│   ├── __init__.py
│   ├── connection.py                  # Database connection management
│   ├── credential_loader.py           # 🔐 Account credential loader & decryption
│   │
│   ├── /scripts/                      # Database management scripts
│   │   ├── __init__.py
│   │   ├── init_db.py                 # Database initialization
│   │   ├── seed_dexes.py              # Seed DEX reference data
│   │   ├── run_migration.py           # Run single migration
│   │   ├── run_all_migrations.py      # Run all migrations
│   │   ├── add_account.py             # 🔐 Add trading account with encrypted credentials
│   │   ├── list_accounts.py           # 🔐 List configured accounts
│   │   └── README.md                  # Database scripts documentation
│   │
│   ├── /migrations/                   # Database schema migrations
│   │   ├── 001_add_dex_symbols_updated_at.sql
│   │   ├── 002_add_missing_opportunity_columns.sql
│   │   ├── 003_rename_opportunity_dex_fields.sql
│   │   ├── 004_add_strategy_tables.sql         # Strategy position/state tables
│   │   ├── 005_create_dashboard_tables.sql     # Dashboard tables
│   │   ├── 006_add_multi_account_support.sql   # 🔐 Multi-account tables
│   │   ├── 006_add_multi_account_support_rollback.sql
│   │   ├── 006_run_migration.sh
│   │   ├── 006_MIGRATION_GUIDE.md
│   │   └── RUN_ALL_MIGRATIONS.sh
│   │
│   ├── /repositories/                 # Data access layer
│   │   ├── __init__.py
│   │   ├── dex_repository.py
│   │   ├── symbol_repository.py
│   │   ├── funding_rate_repository.py
│   │   ├── opportunity_repository.py
│   │   └── dashboard_repository.py
│   │
│   ├── /tests/                        # Database tests
│   │   └── test_credential_loader.py  # 🔐 Credential loader tests
│   │
│   ├── schema.sql                     # Base database schema
│   └── MULTI_ACCOUNT_SETUP.md         # 🔐 Multi-account setup guide
│
├── /helpers/                          # 🛠️ SHARED UTILITIES
│   ├── __init__.py
│   ├── unified_logger.py              # Unified logging system
│   ├── telegram_bot.py                # Telegram notifications
│   ├── lark_bot.py                    # Lark (Feishu) notifications
│   └── risk_manager.py                # Risk management (account protection)
│
├── /tests/                            # 🧪 COMPREHENSIVE TEST SUITE
│   ├── __init__.py
│   ├── conftest.py                    # Pytest configuration & fixtures
│   ├── test_query_retry.py            # Legacy test
│   │
│   └── /strategies/                   # Strategy test suite
│       ├── __init__.py
│       └── /funding_arbitrage/        # Funding arbitrage tests
│           ├── __init__.py
│           ├── README.md              # Test documentation
│           ├── test_funding_analyzer_isolated.py    # ✅ Core logic tests (isolated)
│           ├── test_risk_management_isolated.py     # ✅ Risk management tests (isolated)
│           └── test_basic_integration.py            # ✅ Integration tests (mocked)
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
│       ├── 004_add_strategy_tables.sql  # 🔥 Strategy position/state tables
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

