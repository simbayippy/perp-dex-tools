# Perp DEX Tools

A comprehensive trading application for executing strategies across multiple centralized and decentralized exchanges (DEXs). The platform enables automated trading with support for various strategies including grid trading and funding rate arbitrage, with a modular architecture that allows easy extension to new exchanges and strategies.

## Features

- **Multi-Exchange Support**: Trade across multiple DEXs (Lighter, Aster, Backpack, Paradex, EdgeX, GRVT) with a unified interface
- **Strategy Framework**: Modular strategy system with base classes for easy implementation of new strategies
- **Funding Rate Service**: Real-time market data collection (funding rates, volume, open interest) updated every minute
- **Telegram Bot Interface**: User-friendly Telegram bot for managing strategies, positions, and accounts
- **Proxy Management**: Built-in proxy rotation to prevent IP rate limiting and enable distributed execution
- **Multi-Account Support**: Secure credential management with encryption for multiple trading accounts
- **Database-Driven**: PostgreSQL database for storing market data, positions, accounts, and strategy state

## Project Structure

```
perp-dex-tools/
├── exchange_clients/          # Exchange client implementations
│   ├── base_client.py         # Base exchange client interface
│   ├── base_websocket.py      # Base WebSocket handler
│   ├── base_funding_adapter.py # Base funding rate adapter
│   ├── factory.py             # Exchange factory for dynamic loading
│   ├── lighter/               # Lighter Network implementation
│   ├── aster/                 # Aster implementation
│   ├── backpack/              # Backpack implementation
│   ├── paradex/               # Paradex implementation
│   ├── edgex/                 # EdgeX implementation
│   └── grvt/                  # GRVT implementation
│
├── strategies/                # Trading strategy implementations
│   ├── base_strategy.py       # Base strategy class
│   ├── factory.py             # Strategy factory
│   ├── implementations/
│   │   ├── grid/              # Grid trading strategy
│   │   └── funding_arbitrage/ # Funding rate arbitrage strategy
│   ├── execution/             # Execution layer (order placement, liquidity analysis)
│   └── control/               # Strategy control API
│
├── funding_rate_service/      # Market data collection microservice
│   ├── main.py                # FastAPI application entry point
│   ├── api/                   # REST API endpoints
│   ├── collection/            # Data collection from exchanges
│   ├── core/                  # Business logic (opportunity finder, fee calculator)
│   ├── models/                 # Data models
│   ├── tasks/                 # Background tasks
│   └── scripts/               # Utility scripts
│
├── database/                  # Database layer
│   ├── connection.py          # Database connection management
│   ├── credential_loader.py   # Account credential loader & decryption
│   ├── migration_manager.py   # Migration tracking
│   ├── schema.sql             # Base database schema
│   ├── migrations/            # Database migration files
│   ├── repositories/          # Data access layer
│   └── scripts/               # Database management scripts
│       ├── setup/             # Initialization and seeding
│       ├── accounts/          # Account management
│       ├── users/             # User management
│       ├── proxies/           # Proxy management
│       ├── migrations/        # Migration runners
│       └── funding_rates/     # Funding rate management
│
├── telegram_bot_service/     # Telegram bot interface
│   ├── main.py                # Bot entry point
│   ├── core/                  # Core bot functionality
│   ├── handlers/              # Command handlers
│   ├── managers/              # Strategy and position managers
│   └── verification/          # User verification
│
├── networking/                # Proxy and networking utilities
│   ├── selector.py             # Proxy selection logic
│   ├── session_proxy.py        # Session proxy manager
│   └── repository.py          # Proxy repository
│
├── trading_config/            # Configuration management
│   ├── config_builder.py      # Interactive config builder
│   └── config_yaml.py         # YAML config loader
│
├── scripts/                   # Utility scripts
│   ├── monitor_positions.py   # Position monitoring
│   ├── start_control_api.py   # Control API server
│   └── strategies/            # Strategy management scripts
│
├── configs/                   # Strategy configuration files (YAML)
├── runbot.py                  # Trading bot entry point
├── trading_bot.py             # Main trading orchestrator
├── Makefile                   # Installation and setup automation
└── requirements.txt           # Python dependencies
```

## Installation

### Prerequisites

- Python 3.8+ (Python 3.11 recommended)
- PostgreSQL 12+ (with TimescaleDB extension recommended for time-series data)
- Supervisor (for process management, installed via Makefile)
- Git

### Quick Installation

The easiest way to install all dependencies is using the Makefile:

```bash
# Install all dependencies (Python + Supervisor)
make install
```

This will:
1. Create a virtual environment
2. Install Python dependencies (funding service + exchange clients)
3. Install and configure Supervisor with XML-RPC

### Manual Installation

If you prefer manual installation:

```bash
# 1. Create virtual environment
python3 -m venv venv
source venv/bin/activate  # On macOS/Linux

# 2. Install funding service dependencies
pip install -r funding_rate_service/requirements.txt

# 3. Install exchange clients with all SDKs
pip install -e './exchange_clients[all]'

# 4. Install Supervisor (Linux)
sudo apt-get update
sudo apt-get install -y supervisor
```

See [docs/INSTALLATION.md](docs/INSTALLATION.md) for detailed installation instructions.

## Database Setup

### 1. Initialize Database

First, ensure PostgreSQL is running and create a database:

```bash
# Connect to PostgreSQL
psql -U postgres

# Create database
CREATE DATABASE funding_rates;
\q
```

### 2. Initialize Schema

```bash
# Initialize base schema
python database/scripts/setup/init_db.py
```

### 3. Seed Initial Data

```bash
# Seed DEX reference data
python database/scripts/setup/seed_dexes.py

# Seed strategy config templates (optional)
python database/scripts/setup/seed_strategy_configs.py
```

### 4. Run Migrations

```bash
# Run a specific migration
python database/scripts/migrations/run_migration.py database/migrations/006_add_multi_account_support.sql

# Or run all pending migrations
python database/scripts/migrations/run_all_migrations.py
```

### 5. Set Up Encryption Key

Generate an encryption key for credential storage:

```bash
python -c "from cryptography.fernet import Fernet; print('CREDENTIAL_ENCRYPTION_KEY=' + Fernet.generate_key().decode())"
```

Add the output to your `.env` file:

```bash
CREDENTIAL_ENCRYPTION_KEY=your_generated_key_here
```

## Database Management

### User Management

Create and manage users for REST API access:

```bash
# Create a new user (interactive)
python database/scripts/users/create_user.py

# Create user with command line args
python database/scripts/users/create_user.py --username alice --email alice@example.com

# Create admin user
python database/scripts/users/create_user.py --username admin --admin

# Create API key for user
python database/scripts/users/create_api_key.py --username alice --name "Telegram Bot"

# Get stored API key
python database/scripts/users/get_api_key.py --username alice

# Revoke API key
python database/scripts/users/revoke_api_key.py --username alice --all
```

### Account Management

Manage trading accounts with encrypted credentials:

```bash
# Add account from .env file
python database/scripts/accounts/add_account.py --from-env --account-name main_bot

# Add account from custom env file
python database/scripts/accounts/add_account.py --from-env --account-name acc1 --env-file .env.acc1

# List all accounts (credentials hidden)
python database/scripts/accounts/list_accounts.py

# List accounts with masked credentials
python database/scripts/accounts/list_accounts.py --show-credentials

# List accounts with full credentials (use with caution!)
python database/scripts/accounts/list_accounts.py --show-full

# Delete an account
python database/scripts/accounts/delete_account.py --account-name acc1

# Link account to user
python database/scripts/accounts/link_account_to_user.py
```

See [database/MULTI_ACCOUNT_SETUP.md](database/MULTI_ACCOUNT_SETUP.md) for detailed multi-account setup guide.

### Proxy Management

Add and manage proxies for IP rotation:

```bash
# Add a single proxy
python database/scripts/proxies/add_proxy.py \
    --label primed_sg_1 \
    --endpoint http://proxyas.primedproxies.com:8888 \
    --username PRIM_USER \
    --password SECRET \
    --account acc1 \
    --priority 0

# Add proxies from batch file
python database/scripts/proxies/add_proxy.py \
    --batch-file proxies.txt \
    --account acc1 \
    --label-prefix primed_sg \
    --priority 0 \
    --scheme http

# Delete proxies
python database/scripts/proxies/delete_proxies.py --account acc1
```

### Funding Rate Management

Manage funding rate data:

```bash
# Remove funding exchange
python database/scripts/funding_rates/remove_funding_exchange.py

# Remove funding symbol for a specific DEX
python database/scripts/funding_rates/remove_funding_symbol_dex.py
```

For more details on database scripts, see [database/scripts/README.md](database/scripts/README.md).

## Configuration

### Environment Variables

Copy the example environment file and configure it:

```bash
cp env_example.txt .env
```

Edit `.env` with your credentials:

```bash
# Database
DATABASE_URL=postgresql://user:password@localhost:5432/funding_rates
CREDENTIAL_ENCRYPTION_KEY=your_encryption_key_here

# Exchange Credentials
API_KEY_PRIVATE_KEY=your_lighter_private_key
LIGHTER_ACCOUNT_INDEX=213803
LIGHTER_API_KEY_INDEX=2

ASTER_API_KEY=your_aster_api_key
ASTER_SECRET_KEY=your_aster_secret

BACKPACK_PUBLIC_KEY=your_backpack_public_key
BACKPACK_SECRET_KEY=your_backpack_secret

# Telegram Bot (optional)
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

### Strategy Configuration

Create strategy configurations using the interactive builder:

```bash
# Interactive config builder
python -m trading_config.config_builder
```

This will guide you through creating a configuration file for your chosen strategy (Grid or Funding Arbitrage).

Alternatively, generate example configs:

```bash
# Generate example configs
python -m trading_config.config_yaml
```

Edit the generated files in `configs/` directory and use them with `runbot.py`.

## Running the Application

### Trading Bot

Run a trading strategy using a configuration file:

```bash
# Using config file
python runbot.py --config configs/my_strategy.yml

# Using config file with specific account
python runbot.py --config configs/my_strategy.yml --account main_bot

# Enable proxy rotation
python runbot.py --config configs/my_strategy.yml --enable-proxy

# Enable control API
python runbot.py --config configs/my_strategy.yml --enable-control-api
```

### Funding Rate Service

Start the funding rate collection service:

```bash
cd funding_rate_service

# Run directly
python main.py

# Or using uvicorn
uvicorn main:app --reload --port 8000
```

The API will be available at `http://localhost:8000`:
- Swagger UI: http://localhost:8000/docs
- Health check: http://localhost:8000/api/v1/health

See [funding_rate_service/README.md](funding_rate_service/README.md) for detailed API documentation.

### Telegram Bot Service

Start the Telegram bot interface:

```bash
python telegram_bot_service/main.py
```

The bot will connect to Telegram and allow users to:
- Start/stop strategies
- Monitor positions
- Manage accounts
- View market data

Each strategy started via Telegram runs in its own Supervisor-managed process.

## Quick Start

Here's a quick workflow to get started:

```bash
# 1. Install dependencies
make install

# 2. Set up database
python database/scripts/setup/init_db.py
python database/scripts/setup/seed_dexes.py
python database/scripts/migrations/run_all_migrations.py

# 3. Add your account
python database/scripts/accounts/add_account.py --from-env --account-name main_bot

# 4. Create a strategy config
python -m trading_config.config_builder

# 5. Run the bot
python runbot.py --config configs/your_config.yml
```

For more detailed quick start instructions, see [docs/QUICK_START.md](docs/QUICK_START.md).

## Architecture Overview

### Base Pattern Design

The application follows a modular architecture with base classes that define interfaces:

- **Base Strategy** (`strategies/base_strategy.py`): All strategies inherit from this class, implementing `should_execute()` and `execute_strategy()` methods
- **Base Client** (`exchange_clients/base_client.py`): Exchange clients implement trading operations (place orders, get positions, etc.)
- **Base WebSocket** (`exchange_clients/base_websocket.py`): WebSocket handlers for real-time market data
- **Base Funding Adapter** (`exchange_clients/base_funding_adapter.py`): Adapters for collecting funding rate data

### Factory Pattern

- **Strategy Factory** (`strategies/factory.py`): Dynamically loads and instantiates strategies
- **Exchange Factory** (`exchange_clients/factory.py`): Creates exchange client instances based on exchange name

### Multi-Exchange Support

Strategies can operate across multiple exchanges:
- Single-exchange strategies (e.g., Grid) use one exchange client
- Multi-exchange strategies (e.g., Funding Arbitrage) use multiple exchange clients simultaneously

### Strategy Execution

Each strategy runs independently:
- Strategies are started via `runbot.py` or Telegram bot
- Each strategy instance runs in its own process (managed by Supervisor when started via Telegram)
- Strategies can be controlled via REST API (when enabled)

### Data Flow

1. **Market Data Collection**: Funding rate service collects data every minute from all exchanges
2. **Strategy Execution**: Strategies read market data and execute trades based on their logic
3. **Position Management**: Positions are tracked in the database and can be monitored via Telegram or API
4. **Proxy Rotation**: Networking layer manages proxy rotation to prevent IP rate limiting

## Documentation

- [Quick Start Guide](docs/QUICK_START.md) - Get started quickly
- [Installation Guide](docs/INSTALLATION.md) - Detailed installation instructions
- [Project Structure](docs/PROJECT_STRUCTURE.md) - Detailed project structure
- [Architecture](docs/ARCHITECTURE.md) - System architecture documentation
- [Adding New Exchanges](docs/ADDING_NEW_EXCHANGES.md) - Guide for adding exchange support
- [Multi-Account Setup](database/MULTI_ACCOUNT_SETUP.md) - Multi-account configuration guide
- [Database Scripts](database/scripts/README.md) - Database management scripts documentation
- [Funding Rate Service](funding_rate_service/README.md) - Funding service API documentation

## License

See [LICENSE](LICENSE) file for details.

