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

    # Suppress websockets debug logs unless DEBUG level is explicitly requested
    if log_level.upper() != 'DEBUG':
        logging.getLogger('websockets').setLevel(logging.WARNING)

    # Suppress other noisy loggers
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('requests').setLevel(logging.WARNING)
    logging.getLogger('aiohttp').setLevel(logging.WARNING)
    
    # Suppress funding service verbose logs (keep only warnings/errors)
    logging.getLogger('funding_rate_service').setLevel(logging.WARNING)
    logging.getLogger('databases').setLevel(logging.WARNING)

    # Suppress Lighter SDK debug logs
    logging.getLogger('lighter').setLevel(logging.WARNING)
    # Note: We keep the root logger at the requested level (INFO) so atomic_multi_order.py logs show up


async def main():
    """Main entry point."""
    args = parse_arguments()

    # Setup logging first
    setup_logging("INFO")

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
    strategy_config["_config_path"] = str(config_path)

    print(f"\n✓ Loaded configuration from: {config_path}")
    print(f"  Strategy: {strategy_name}")
    print(f"  Created: {loaded['metadata'].get('created_at', 'unknown')}\n")

    # Load env
    env_path = Path(args.env_file)
    if not env_path.exists():
        print(f"Env file not found: {env_path.resolve()}")
        sys.exit(1)
    dotenv.load_dotenv(args.env_file)

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
    print("="*70 + "\n")
    
    bot = TradingBot(config)
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
        quantity = config_dict.get("quantity", Decimal("100"))
    elif strategy_name == "funding_arbitrage":
        primary_exchange = config_dict.get("primary_exchange")
        if isinstance(primary_exchange, str) and primary_exchange.strip():
            exchange = primary_exchange.strip()
        else:
            exchange = "multi"
        ticker = "ALL"  # Funding arb scans all tickers (not limited to one)
        quantity = Decimal("1")  # Placeholder, not used
    else:
        exchange = "lighter"
        ticker = "BTC"
        quantity = Decimal("100")
    
    return TradingConfig(
        ticker=ticker.upper() if isinstance(ticker, str) else "BTC",
        contract_id='',  # will be set in the bot's run method
        tick_size=Decimal(0),
        quantity=quantity,
        exchange=exchange.lower(),
        strategy=strategy_name.lower(),
        strategy_params=config_dict
    )

if __name__ == "__main__":
    asyncio.run(main())
