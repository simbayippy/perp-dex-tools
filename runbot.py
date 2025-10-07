#!/usr/bin/env python3
"""
Modular Trading Bot - Supports multiple exchanges
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
    parser = argparse.ArgumentParser(description='Modular Trading Bot - Supports multiple exchanges')

    # Exchange selection
    parser.add_argument('--exchange', type=str, default='edgex',
                        choices=ExchangeFactory.get_supported_exchanges(),
                        help='Exchange to use (default: edgex). '
                             f'Available: {", ".join(ExchangeFactory.get_supported_exchanges())}')
    
    # Strategy selection
    parser.add_argument('--strategy', type=str, default='grid',
                        choices=StrategyFactory.get_supported_strategies(),
                        help='Trading strategy to use (default: grid). '
                             f'Available: {", ".join(StrategyFactory.get_supported_strategies())}')

    # Universal trading parameters
    parser.add_argument('--ticker', type=str, required=True,
                        help='Trading ticker (e.g., BTC, ETH, HYPE)')
    parser.add_argument('--quantity', type=Decimal, required=True,
                        help='Order quantity')
    parser.add_argument('--env-file', type=str, default=".env",
                        help=".env file path (default: .env)")
    
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
                        help='Funding: Comma-separated list of exchanges (e.g., lighter,extended)')

    return parser.parse_args()


def setup_logging(log_level: str):
    """Setup global logging configuration."""
    # Convert string level to logging constant
    level = getattr(logging, log_level.upper(), logging.INFO)

    # Clear any existing handlers to prevent duplicates
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Configure root logger WITHOUT adding a console handler
    # This prevents duplicate logs when TradingLogger adds its own console handler
    root_logger.setLevel(level)

    # Suppress websockets debug logs unless DEBUG level is explicitly requested
    if log_level.upper() != 'DEBUG':
        logging.getLogger('websockets').setLevel(logging.WARNING)

    # Suppress other noisy loggers
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('requests').setLevel(logging.WARNING)

    # Suppress Lighter SDK debug logs
    logging.getLogger('lighter').setLevel(logging.WARNING)
    # Also suppress any root logger DEBUG messages that might be coming from Lighter
    if log_level.upper() != 'DEBUG':
        # Set root logger to WARNING to suppress DEBUG messages from Lighter SDK
        root_logger.setLevel(logging.WARNING)


async def main():
    """Main entry point."""
    args = parse_arguments()

    # Setup logging first
    setup_logging("WARNING")

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
        print(f"Env file not find: {env_path.resolve()}")
        sys.exit(1)
    dotenv.load_dotenv(args.env_file)

    # Build strategy parameters
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
    config = TradingConfig(
        ticker=args.ticker.upper(),
        contract_id='',  # will be set in the bot's run method
        tick_size=Decimal(0),
        quantity=args.quantity,
        exchange=args.exchange.lower(),
        strategy=args.strategy.lower(),
        strategy_params=strategy_params
    )

    # Create and run the bot
    bot = TradingBot(config)
    try:
        await bot.run()
    except Exception as e:
        print(f"Bot execution failed: {e}")
        # The bot's run method already handles graceful shutdown
        return


if __name__ == "__main__":
    asyncio.run(main())
