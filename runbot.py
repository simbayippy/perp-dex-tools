#!/usr/bin/env python3
"""
Modular Trading Bot - Supports multiple exchanges

Two Launch Modes:
  1. Config File: python runbot.py --config configs/my_strategy.yml
  2. CLI Args:    python runbot.py --strategy grid --exchange lighter --ticker BTC ...

To create a config file, run: python config_builder.py
"""

import argparse
import asyncio
import logging
from pathlib import Path
import sys
import dotenv
from decimal import Decimal
from trading_bot import TradingBot, TradingConfig
from exchange_clients.factory import ExchangeFactory
from strategies import StrategyFactory


def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description='Modular Trading Bot - Supports multiple exchanges',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Launch Modes:
  Config File Mode (Recommended):
    python runbot.py --config configs/my_strategy.yml
    
  CLI Args Mode (Quick Tests):
    python runbot.py --strategy grid --exchange lighter --ticker BTC --quantity 100 --take-profit 0.008 --direction buy

Creating a Config File:
  # Run the interactive config builder
  python -m trading_config.config_builder
  
  # OR generate examples and edit
  python -m trading_config.config_yaml
  nano configs/example_funding_arbitrage.yml

Examples:
  # Use saved config (recommended)
  python runbot.py --config configs/funding_arb_20251009.yml
  
  # Quick CLI launch (grid strategy)
  python runbot.py --strategy grid --exchange lighter --ticker BTC --quantity 100 --take-profit 0.008 --direction buy
  
  # Quick CLI launch (funding arbitrage)
  python runbot.py --strategy funding_arbitrage --exchange lighter --ticker BTC --quantity 1 --target-exposure 100 --exchanges lighter,grvt
        """
    )

    # ========================================================================
    # Config File Mode
    # ========================================================================
    parser.add_argument('--config', '-c', type=str,
                        help='Load configuration from YAML file')

    # ========================================================================
    # Common Arguments
    # ========================================================================
    parser.add_argument('--env-file', type=str, default=".env",
                        help=".env file path (default: .env)")

    # ========================================================================
    # CLI Mode Arguments (only used if not --interactive or --config)
    # ========================================================================
    
    # Exchange selection
    parser.add_argument('--exchange', type=str,
                        choices=ExchangeFactory.get_supported_exchanges(),
                        help='Exchange to use. '
                             f'Available: {", ".join(ExchangeFactory.get_supported_exchanges())}')
    
    # Strategy selection
    parser.add_argument('--strategy', type=str,
                        choices=StrategyFactory.get_supported_strategies(),
                        help='Trading strategy to use. '
                             f'Available: {", ".join(StrategyFactory.get_supported_strategies())}')

    # Universal trading parameters
    parser.add_argument('--ticker', type=str,
                        help='Trading ticker (e.g., BTC, ETH, HYPE)')
    parser.add_argument('--quantity', type=Decimal,
                        help='Order quantity (may be unused by some strategies)')
    
    # Strategy-specific parameters (passed as key=value pairs)
    parser.add_argument('--strategy-params', type=str, nargs='*', default=[],
                        help='Strategy-specific parameters as key=value pairs '
                             '(e.g., --strategy-params take_profit=0.008 direction=buy max_orders=25)')
    
    # Grid strategy specific parameters (for convenience)
    parser.add_argument('--take-profit', type=Decimal,
                        help='Grid: Take profit percentage (e.g., 0.008 = 0.8%%)')
    parser.add_argument('--direction', type=str, choices=['buy', 'sell'],
                        help='Grid: Trading direction')
    parser.add_argument('--max-orders', type=int,
                        help='Grid: Maximum number of active orders')
    parser.add_argument('--wait-time', type=int,
                        help='Grid: Wait time between orders in seconds')
    parser.add_argument('--grid-step', type=Decimal,
                        help='Grid: Minimum distance percentage to next order')
    parser.add_argument('--random-timing', action='store_true',
                        help='Grid: Enable random timing variation')
    parser.add_argument('--dynamic-profit', action='store_true',
                        help='Grid: Enable dynamic profit-taking')
    parser.add_argument('--stop-price', type=Decimal,
                        help='Grid: Stop trading and close positions if price goes below this (for buy) or above this (for sell)')
    parser.add_argument('--pause-price', type=Decimal,
                        help='Grid: Pause trading (no new orders) if price reaches this level')
    
    # Funding arbitrage specific parameters (for convenience)
    parser.add_argument('--target-exposure', type=Decimal,
                        help='Funding: Target position size per side')
    parser.add_argument('--min-profit-rate', type=Decimal,
                        help='Funding: Minimum hourly profit rate to trade')
    parser.add_argument('--exchanges', type=str,
                        help='Funding: Comma-separated list of exchanges (e.g., lighter,grvt,backpack)')

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

    # ========================================================================
    # MODE 1: Config File Mode
    # ========================================================================
    if args.config:
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
    # MODE 2: CLI Args Mode (Direct)
    # ========================================================================
    else:
        # Validate required CLI arguments
        if not args.strategy:
            print("Error: --strategy is required (or use --config)")
            print("\nTo create a config file:")
            print("  python -m trading_config.config_builder")
            print("\nThen run:")
            print("  python runbot.py --config configs/your_config.yml")
            sys.exit(1)
        if not args.ticker:
            print("Error: --ticker is required (or use --config)")
            sys.exit(1)
        if not args.quantity:
            print("Error: --quantity is required (or use --config)")
            sys.exit(1)
        if not args.exchange:
            print("Error: --exchange is required (or use --config)")
            sys.exit(1)
        
        # Validate strategy-specific requirements
        if args.strategy == 'grid':
            if not args.take_profit and 'take_profit' not in [p.split('=')[0] for p in args.strategy_params]:
                print("Error: Grid strategy requires --take-profit parameter")
                sys.exit(1)
            if not args.direction and 'direction' not in [p.split('=')[0] for p in args.strategy_params]:
                print("Error: Grid strategy requires --direction parameter")
                sys.exit(1)
        elif args.strategy == 'funding_arbitrage':
            if not args.target_exposure and 'target_exposure' not in [p.split('=')[0] for p in args.strategy_params]:
                print("Error: Funding arbitrage strategy requires --target-exposure parameter")
                sys.exit(1)

        env_path = Path(args.env_file)
        if not env_path.exists():
            print(f"Env file not found: {env_path.resolve()}")
            sys.exit(1)
        dotenv.load_dotenv(args.env_file)

        # Build strategy parameters (legacy CLI parsing)
        config = _parse_cli_args_to_config(args)

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
        exchange = config_dict.get("primary_exchange", "lighter")
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


def _parse_cli_args_to_config(args) -> TradingConfig:
    """
    Parse CLI arguments into TradingConfig (legacy mode).
    
    Args:
        args: Parsed argparse arguments
        
    Returns:
        TradingConfig object
    """
    strategy_params = {}
    
    # Parse key=value strategy parameters
    for param in args.strategy_params:
        if '=' in param:
            key, value = param.split('=', 1)
            # Try to convert to appropriate type
            try:
                if '.' in value:
                    strategy_params[key] = Decimal(value)
                elif value.lower() in ['true', 'false']:
                    strategy_params[key] = value.lower() == 'true'
                elif value.isdigit():
                    strategy_params[key] = int(value)
                else:
                    strategy_params[key] = value
            except:
                strategy_params[key] = value
    
    # Add convenience parameters for grid strategy
    if args.strategy == 'grid':
        if args.take_profit is not None:
            strategy_params['take_profit'] = args.take_profit
        if args.direction:
            strategy_params['direction'] = args.direction.lower()
        if args.max_orders is not None:
            strategy_params['max_orders'] = args.max_orders
        if args.wait_time is not None:
            strategy_params['wait_time'] = args.wait_time
        if args.grid_step is not None:
            strategy_params['grid_step'] = args.grid_step
        if args.random_timing:
            strategy_params['random_timing'] = True
        if args.dynamic_profit:
            strategy_params['dynamic_profit'] = True
        if args.stop_price is not None:
            strategy_params['stop_price'] = args.stop_price
        if args.pause_price is not None:
            strategy_params['pause_price'] = args.pause_price
    
    # Add convenience parameters for funding arbitrage strategy
    elif args.strategy == 'funding_arbitrage':
        if args.target_exposure is not None:
            strategy_params['target_exposure'] = args.target_exposure
        if args.min_profit_rate is not None:
            strategy_params['min_profit_rate'] = args.min_profit_rate
        if args.exchanges:
            strategy_params['exchanges'] = args.exchanges.split(',')
        
        # Set defaults for funding arbitrage
        strategy_params.setdefault('rebalance_threshold', Decimal('0.05'))
        strategy_params.setdefault('funding_check_interval', 300)
    
    # Create clean configuration
    return TradingConfig(
        ticker=args.ticker.upper(),
        contract_id='',  # will be set in the bot's run method
        tick_size=Decimal(0),
        quantity=args.quantity,
        exchange=args.exchange.lower(),
        strategy=args.strategy.lower(),
        strategy_params=strategy_params
    )


if __name__ == "__main__":
    asyncio.run(main())
