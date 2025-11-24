# Gemini Code Assistant Context

This document provides a comprehensive overview of the `perp-dex-tools` project, designed to help Gemini understand the codebase for effective assistance.

## Project Overview

`perp-dex-tools` is a modular trading bot framework for perpetual futures trading on decentralized exchanges (DEXes). Its primary focus is on delta-neutral strategies, particularly funding rate arbitrage. The bot is designed to be extensible, allowing for the addition of new exchanges and trading strategies.

The architecture emphasizes safety through an atomic execution pattern, performance via internal service calls, and reliability with a PostgreSQL database for state persistence. It supports multi-account trading with encrypted credential management.

### Key Technologies

*   **Language:** Python 3
*   **Database:** PostgreSQL
*   **Configuration:** YAML
*   **CLI:** `argparse`, `questionary`
*   **Core Dependencies:** `asyncio`, `dotenv`, `pytz`, `cryptography`

## Building and Running the Project

The project uses a `Makefile` for common tasks.

### Installation

To install all dependencies, including Python packages and Supervisor for process management, run:

```bash
make install
```

This will create a Python virtual environment in the `venv` directory, install required packages from `requirements.txt` and `funding_rate_service/requirements.txt`, and set up Supervisor.

### Running Tests

To run the pytest suite located in the `tests/` directory:

```bash
make test
```

### Running the Bot

The bot is launched using the `runbot.py` script, which requires a configuration file.

1.  **Generate a Configuration:**

    An interactive configuration builder helps create strategy configurations.

    ```bash
    python -m trading_config.config_builder
    ```

    This will guide you through creating a YAML configuration file (e.g., `configs/grid/grid_example.yml`).

2.  **Run the Bot:**

    Once you have a configuration file, you can run the bot:

    ```bash
    python runbot.py --config configs/your_strategy.yml --account your_account_name
    ```

    *   `--config`: Path to the strategy's YAML configuration file.
    *   `--account`: (Optional) The name of the account to load credentials from the database. If not provided, credentials are taken from environment variables.

## Development Conventions

### Architecture

The project follows a strict 3-layer architecture:

1.  **Layer 3: Strategy Orchestration (`/strategies/implementations/`)**
    *   Defines *what* to trade (the business logic).
    *   Makes high-level decisions about entering or exiting positions.
    *   Is agnostic to the underlying exchange specifics.

2.  **Layer 2: Execution Utilities (`/strategies/execution/`)**
    *   Defines *how* to trade safely.
    *   Provides tools for order execution, liquidity analysis, and slippage control.
    *   The `AtomicMultiOrderExecutor` is a key component, ensuring that multiple orders for a delta-neutral position are filled together or not at all.

3.  **Layer 1: Exchange Clients (`/exchange_clients/`)**
    *   Defines *where* to trade.
    *   Provides a standardized interface for interacting with different DEX APIs.
    *   Handles the specifics of authentication, order placement, and data fetching for each exchange.

### Database

The project relies on a PostgreSQL database for several key functions:

*   **Funding Rate Storage:** The `funding_rate_service` collects and stores funding rate data from various exchanges.
*   **Strategy State:** Strategies persist their state (e.g., open positions, historical trades) to the database, allowing the bot to be resilient to restarts.
*   **Multi-Account Credentials:** Encrypted API keys and other credentials for multiple trading accounts are stored securely in the database.

Database migrations are managed in the `database/migrations/` directory.

### Configuration

*   Strategy configurations are defined in YAML files located in the `configs/` directory.
*   The `trading_config/config_builder.py` script provides an interactive way to create these files.
*   The `trading_config/config_yaml.py` module handles loading and validating the YAML files.

### Logging

The project uses a `UnifiedLogger` for consistent logging across different modules. The log level can be set via the `--log-level` command-line argument when running `runbot.py`.

### Code Style

The codebase uses `flake8` for linting. The configuration is in the `.flake8` file.

## Key Files and Directories

*   `runbot.py`: The main entry point for starting a trading bot instance.
*   `trading_bot.py`: Contains the core `TradingBot` class that orchestrates the strategy and exchange clients.
*   `Makefile`: Defines common development tasks like `install`, `test`, and `clean`.
*   `requirements.txt`: Lists the main Python dependencies.
*   `configs/`: Directory for strategy configuration files.
*   `database/`: Contains database-related code, including the schema, migrations, and repositories.
*   `docs/`: Project documentation, including the detailed `ARCHITECTURE.md`.
*   `exchange_clients/`: Home to the exchange client implementations.
*   `funding_rate_service/`: A service for collecting, storing, and analyzing funding rate data.
*   `strategies/`: Contains the strategy implementations, execution utilities, and the strategy factory.
*   `trading_config/`: Code for building and loading strategy configurations.
*   `tests/`: The project's test suite.
