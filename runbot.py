#!/usr/bin/env python3
"""
Modular Trading Bot - Config-driven launcher.

Usage:
    python runbot.py --config configs/my_strategy.yml [--env-file .env]

Generate configs via:
    python -m trading_config.config_builder
"""

import argparse
import asyncio
import logging
from pathlib import Path
import sys
import dotenv
from decimal import Decimal
from trading_bot import TradingBot, TradingConfig
import os


def parse_arguments():
    """Parse command line arguments (config-only workflow)."""
    parser = argparse.ArgumentParser(
        description="Run a trading strategy using a generated YAML configuration."
    )

    parser.add_argument(
        "--config",
        "-c",
        type=str,
        required=True,
        help="Path to the YAML configuration produced by the config builder.",
    )

    parser.add_argument(
        "--env-file",
        type=str,
        default=".env",
        help="Path to the environment file with API credentials (default: .env).",
    )
    
    parser.add_argument(
        "--account",
        "-a",
        type=str,
        default=None,
        help="Account name to load credentials from database (e.g., 'acc1'). "
             "If provided, credentials will be loaded from the database instead of env vars.",
    )
    
    parser.add_argument(
        "--log-level",
        "-l",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO). Use DEBUG to see detailed logs.",
    )

    return parser.parse_args()


def setup_logging(log_level: str):
    """Setup global logging configuration."""
    # Convert string level to logging constant
    level = getattr(logging, log_level.upper(), logging.INFO)

    # Clear any existing handlers to prevent duplicates
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Configure root logger WITH a console handler for standard Python loggers
    # (UnifiedLogger handles its own console output)
    root_logger.setLevel(level)
    
    # Add console handler for standard Python loggers (like atomic_multi_order.py)
    if not any(isinstance(h, logging.StreamHandler) for h in root_logger.handlers):
        console_handler = logging.StreamHandler()
        console_handler.setLevel(level)
        # Use cleaner format matching UnifiedLogger style
        formatter = logging.Formatter(
            '%(asctime)s.%(msecs)03d - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)

    # Always suppress noisy library debug logs (even in DEBUG mode)
    # These libraries are too verbose and don't provide useful trading info
    logging.getLogger('websockets').setLevel(logging.WARNING)
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('requests').setLevel(logging.WARNING)
    logging.getLogger('aiohttp').setLevel(logging.WARNING)
    logging.getLogger('asyncio').setLevel(logging.WARNING)
    
    # Suppress funding service verbose logs (keep only warnings/errors)
    logging.getLogger('funding_rate_service').setLevel(logging.WARNING)
    logging.getLogger('databases').setLevel(logging.WARNING)

    # Suppress Lighter SDK debug logs
    logging.getLogger('lighter').setLevel(logging.WARNING)
    # Note: We keep the root logger at the requested level (INFO) so atomic_multi_order.py logs show up


async def load_account_credentials(account_name: str) -> dict:
    """
    Load encrypted credentials for an account from the database.
    
    Args:
        account_name: Name of the account to load credentials for
        
    Returns:
        Dictionary mapping exchange names to their credentials
        
    Raises:
        SystemExit: If credentials cannot be loaded
    """
    try:
        from databases import Database
        from database.credential_loader import DatabaseCredentialLoader
        
        # Get database URL from environment
        database_url = os.getenv('DATABASE_URL')
        
        if not database_url:
            print("Error: DATABASE_URL not set in environment")
            print("Required for loading account credentials from database")
            sys.exit(1)
        
        # Connect to database
        db = Database(database_url)
        await db.connect()
        
        try:
            # Load credentials (encryption_key is read from env by the loader)
            loader = DatabaseCredentialLoader(db)
            credentials = await loader.load_account_credentials(account_name)
            
            if not credentials:
                print(f"Error: No credentials found for account '{account_name}'")
                print(f"\nAvailable accounts:")
                print(f"  Run: python database/scripts/list_accounts.py")
                sys.exit(1)
            
            print(f"✓ Loaded credentials for account: {account_name}")
            print(f"  Exchanges: {', '.join(credentials.keys())}\n")
            
            return credentials
        finally:
            await db.disconnect()
            
    except ImportError as e:
        print(f"Error: Failed to import database modules: {e}")
        print("Ensure 'databases' and 'cryptography' are installed")
        sys.exit(1)
    except Exception as e:
        print(f"Error loading credentials from database: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


async def main():
    """Main entry point."""
    args = parse_arguments()

    # Set LOG_LEVEL environment variable for UnifiedLogger
    os.environ['LOG_LEVEL'] = args.log_level

    # Setup logging first
    setup_logging(args.log_level)

    from trading_config.config_yaml import load_config_from_yaml, validate_config_file

    config_path = Path(args.config)
    if not config_path.exists():
        print(f"Error: Config file not found: {config_path}")
        sys.exit(1)

    # Validate config file
    is_valid, error = validate_config_file(config_path)
    if not is_valid:
        print(f"Error: Invalid config file: {error}")
        sys.exit(1)

    # Load config
    loaded = load_config_from_yaml(config_path)
    strategy_name = loaded["strategy"]
    strategy_config = loaded["config"]
    if "primary_exchange" in strategy_config and "mandatory_exchange" not in strategy_config:
        strategy_config["mandatory_exchange"] = strategy_config.pop("primary_exchange")
    if not strategy_config.get("mandatory_exchange"):
        strategy_config["mandatory_exchange"] = None
        strategy_config["max_oi_usd"] = None
    strategy_config.pop("primary_exchange", None)
    strategy_config["_config_path"] = str(config_path)

    print(f"\n✓ Loaded configuration from: {config_path}")
    print(f"  Strategy: {strategy_name}")
    print(f"  Created: {loaded['metadata'].get('created_at', 'unknown')}\n")

    # Load env (for DATABASE_URL, ENCRYPTION_KEY, etc.)
    env_path = Path(args.env_file)
    if not env_path.exists():
        print(f"Env file not found: {env_path.resolve()}")
        sys.exit(1)
    dotenv.load_dotenv(args.env_file)

    # Load account credentials from database if --account is provided
    account_credentials = None
    account_name = None
    if args.account:
        account_name = args.account
        print(f"Loading credentials for account: {account_name}")
        account_credentials = await load_account_credentials(account_name)
    
    # Store account info in strategy config for later use
    strategy_config["_account_name"] = account_name
    strategy_config["_account_credentials"] = account_credentials

    # Convert to TradingConfig
    config = _config_dict_to_trading_config(strategy_name, strategy_config)

    # ========================================================================
    # Launch Bot (Common for All Modes)
    # ========================================================================
    print("\n" + "="*70)
    print(f"  Starting Trading Bot")
    print("="*70)
    print(f"  Strategy: {config.strategy}")
    print(f"  Exchange: {config.exchange}")
    print(f"  Ticker:   {config.ticker}")
    if account_name:
        print(f"  Account:  {account_name}")
    print("="*70 + "\n")
    
    # Create bot with optional account credentials
    bot = TradingBot(config, account_credentials=account_credentials)
    try:
        await bot.run()
    except Exception as e:
        print(f"Bot execution failed: {e}")
        # The bot's run method already handles graceful shutdown
        return


def _config_dict_to_trading_config(strategy_name: str, config_dict: dict) -> TradingConfig:
    """
    Convert a strategy config dict (from YAML or interactive) to TradingConfig.
    
    Args:
        strategy_name: Name of the strategy
        config_dict: Configuration dictionary
        
    Returns:
        TradingConfig object
    """
    # Extract common fields
    if strategy_name == "grid":
        exchange = config_dict.get("exchange", "lighter")
        ticker = config_dict.get("ticker", "BTC")
        quantity_raw = config_dict.get("quantity")
        if quantity_raw is None:
            quantity_raw = config_dict.get("order_notional_usd", Decimal("1"))
        quantity = Decimal(str(quantity_raw))
        order_notional_raw = config_dict.get("order_notional_usd")
        order_notional_decimal = (
            Decimal(str(order_notional_raw)) if order_notional_raw is not None else None
        )
        target_leverage_raw = config_dict.get("target_leverage")
        target_leverage_decimal = (
            Decimal(str(target_leverage_raw)) if target_leverage_raw is not None else None
        )
    elif strategy_name == "funding_arbitrage":
        mandatory_exchange = config_dict.get("mandatory_exchange") or config_dict.get("primary_exchange")
        if isinstance(mandatory_exchange, str) and mandatory_exchange.strip():
            exchange = mandatory_exchange.strip()
        else:
            exchange = "multi"
        ticker = "ALL"  # Funding arb scans all tickers (not limited to one)
        quantity = Decimal("1")  # Placeholder, not used
        order_notional_decimal = None
        target_leverage_decimal = None
    else:
        exchange = "lighter"
        ticker = "BTC"
        quantity = Decimal("100")
        order_notional_decimal = None
        target_leverage_decimal = None
    
    return TradingConfig(
        ticker=ticker.upper() if isinstance(ticker, str) else "BTC",
        contract_id='',  # will be set in the bot's run method
        tick_size=Decimal(0),
        quantity=quantity,
        exchange=exchange.lower(),
        strategy=strategy_name.lower(),
        order_notional_usd=order_notional_decimal,
        target_leverage=target_leverage_decimal,
        strategy_params=config_dict
    )

if __name__ == "__main__":
    asyncio.run(main())