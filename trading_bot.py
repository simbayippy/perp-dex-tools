"""
Modular Trading Bot - Supports multiple exchanges
"""

import os
import time
import asyncio
import traceback
import random
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Dict, Any

from exchange_clients.factory import ExchangeFactory
from helpers.unified_logger import get_logger
from helpers.lark_bot import LarkBot
from helpers.telegram_bot import TelegramBot
from strategies import StrategyFactory


@dataclass
class TradingConfig:
    """Configuration class for trading parameters."""
    # Universal parameters
    ticker: str
    contract_id: str
    quantity: Decimal
    tick_size: Decimal
    exchange: str
    strategy: str
    
    # Strategy-specific parameters
    strategy_params: Dict[str, Any] = None

    def __post_init__(self):
        """Post-initialization to handle strategy parameters."""
        if self.strategy_params is None:
            self.strategy_params = {}

class TradingBot:
    """Modular Trading Bot - Main trading logic supporting multiple exchanges."""

    def __init__(self, config: TradingConfig, account_credentials: Optional[Dict[str, Dict[str, Any]]] = None):
        """
        Initialize Trading Bot.
        
        Args:
            config: Trading configuration
            account_credentials: Optional credentials dict mapping exchange names to credentials.
                               If provided, credentials will be used instead of environment variables.
        """
        self.config = config
        self.account_credentials = account_credentials
        self.logger = get_logger("bot", config.strategy, context={"exchange": config.exchange, "ticker": config.ticker}, log_to_console=True)

        # Log account info if credentials provided
        if account_credentials:
            account_name = config.strategy_params.get('_account_name', 'unknown')
            self.logger.info(f"Using database credentials for account: {account_name}")
            self.logger.info(f"Available exchanges: {list(account_credentials.keys())}")

        # Determine if strategy needs multiple exchanges
        multi_exchange_strategies = ['funding_arbitrage']
        is_multi_exchange = config.strategy in multi_exchange_strategies
        
        # Create exchange client(s)
        try:
            if is_multi_exchange:
                # Multi-exchange mode (for funding arbitrage, etc.)
                # Get list of exchanges from strategy params
                raw_exchange_list = config.strategy_params.get('scan_exchanges')

                if isinstance(raw_exchange_list, str):
                    exchange_list = [ex.strip().lower() for ex in raw_exchange_list.split(',') if ex.strip()]
                elif raw_exchange_list:
                    exchange_list = [str(ex).strip().lower() for ex in raw_exchange_list if str(ex).strip()]
                else:
                    exchange_list = []

                mandatory_exchange = (
                    config.strategy_params.get('mandatory_exchange')
                    or config.strategy_params.get('primary_exchange')
                )
                if isinstance(mandatory_exchange, str):
                    mandatory_exchange = mandatory_exchange.strip().lower() or None
                else:
                    mandatory_exchange = None

                if not exchange_list:
                    raise ValueError(
                        "Funding arbitrage requires at least one exchange in 'scan_exchanges'."
                    )

                if mandatory_exchange and mandatory_exchange not in exchange_list:
                    exchange_list.append(mandatory_exchange)

                self.logger.info(f"Creating clients for exchanges: {exchange_list}")

                # Create multiple exchange clients (with or without credentials)
                self.exchange_clients = ExchangeFactory.create_multiple_exchanges(
                    exchange_names=exchange_list,
                    config=config,
                    primary_exchange=mandatory_exchange,
                    account_credentials=account_credentials,  # Pass credentials to factory
                )

                if not self.exchange_clients:
                    raise ValueError("Failed to instantiate any exchange clients.")

                # Set a representative exchange client for backward compatibility
                self.exchange_client = next(iter(self.exchange_clients.values()))

                self.logger.info(
                    f"Created {len(self.exchange_clients)} exchange clients: {list(self.exchange_clients.keys())}"
                )
            else:
                # Single exchange mode (for grid strategy, etc.)
                # Get credentials for this specific exchange
                exchange_creds = None
                if account_credentials and config.exchange in account_credentials:
                    exchange_creds = account_credentials[config.exchange]
                
                self.exchange_client = ExchangeFactory.create_exchange(
                    config.exchange,
                    config,
                    exchange_creds  # Pass credentials to factory
                )
                self.exchange_clients = None  # Not used for single-exchange strategies
                
        except ValueError as e:
            raise ValueError(f"Failed to create exchange client: {e}")

        # Trading state
        self.last_log_time = 0
        self.shutdown_requested = False
        self.loop = None

        # Initialize strategy
        try:
            if is_multi_exchange:
                # Pass all exchange clients to multi-exchange strategies
                self.strategy = StrategyFactory.create_strategy(
                    self.config.strategy,
                    self.config,
                    exchange_client=self.exchange_client,  # Primary for backward compat
                    exchange_clients=self.exchange_clients  # All clients
                )
            else:
                # Pass single exchange client to single-exchange strategies
                self.strategy = StrategyFactory.create_strategy(
                    self.config.strategy,
                    self.config,
                    self.exchange_client
                )
            self.logger.info(f"Strategy '{self.config.strategy}' created successfully")
        except ValueError as e:
            raise ValueError(f"Failed to create strategy: {e}")
        
    async def _setup_contract_attributes(self):
        """Setup contract attributes based on strategy type."""
        multi_symbol_strategies = ['funding_arbitrage']
        
        if self.config.strategy not in multi_symbol_strategies:
            self.config.contract_id, self.config.tick_size = await self.exchange_client.get_contract_attributes()
        else:
            # Multi-symbol strategy - set placeholder values
            self.config.contract_id = "MULTI_SYMBOL"
            self.config.tick_size = Decimal("0.01")  # Placeholder
            self.logger.info("Multi-symbol strategy: Contract attributes will be fetched per-symbol")

    def _log_configuration(self):
        """Log the current trading configuration."""
        multi_symbol_strategies = ['funding_arbitrage']
        
        self.logger.info("=== Trading Configuration ===")
        self.logger.info(f"Ticker: {self.config.ticker}")
        if self.config.strategy not in multi_symbol_strategies:
            self.logger.info(f"Contract ID: {self.config.contract_id}")
        self.logger.info(f"Quantity: {self.config.quantity}")
        self.logger.info(f"Exchange: {self.config.exchange}")
        self.logger.info(f"Strategy: {self.config.strategy}")
        
        # Log strategy parameters
        if self.config.strategy_params:
            self.logger.info("Strategy Parameters:")
            for key, value in self.config.strategy_params.items():
                self.logger.info(f"  {key}: {value}")
            
        self.logger.info("=============================")

    async def _connect_exchanges(self):
        """Connect to exchange(s) based on mode."""
        if self.exchange_clients:
            # Multi-exchange mode: connect all clients
            for exchange_name, client in self.exchange_clients.items():
                self.logger.info(f"Connecting to {exchange_name}...")
                await client.connect()
                self.logger.info(f"Connected to {exchange_name}")
        else:
            # Single exchange mode
            await self.exchange_client.connect()

    async def _run_trading_loop(self):
        """Execute the main trading loop."""
        while not self.shutdown_requested:
            try:
                if await self.strategy.should_execute():
                    await self.strategy.execute_strategy()
                else:
                    await asyncio.sleep(1)  # Brief wait if strategy says not to execute
                    
            except Exception as e:
                self.logger.error(f"Strategy execution error: {e}")
                await asyncio.sleep(5)  # Wait longer on error

    async def graceful_shutdown(self, reason: str = "Unknown"):
        """Perform graceful shutdown of the trading bot."""
        self.logger.info(f"Starting graceful shutdown: {reason}")
        self.shutdown_requested = True

        try:
            # Cleanup strategy
            if hasattr(self, 'strategy') and self.strategy:
                await self.strategy.cleanup()
            
            # Disconnect from exchange(s)
            if hasattr(self, 'exchange_clients') and self.exchange_clients:
                # Multi-exchange mode: disconnect all clients
                for exchange_name, client in self.exchange_clients.items():
                    try:
                        await client.disconnect()
                        self.logger.info(f"Disconnected from {exchange_name}")
                    except Exception as e:
                        self.logger.error(f"Error disconnecting from {exchange_name}: {e}")
            else:
                # Single exchange mode
                await self.exchange_client.disconnect()
                
            self.logger.info("Graceful shutdown completed")

        except Exception as e:
            self.logger.error(f"Error during graceful shutdown: {e}")

    async def run(self):
        """Main trading loop."""
        try:
            # Setup phase
            await self._setup_contract_attributes()
            self._log_configuration()
            
            # Capture the running event loop for thread-safe callbacks
            self.loop = asyncio.get_running_loop()
            
            # Connection phase
            await self._connect_exchanges()
            await self.strategy.initialize()
            
            # Execution phase
            await self._run_trading_loop()

        except KeyboardInterrupt:
            self.logger.info("Bot stopped by user")
            await self.graceful_shutdown("User interruption (Ctrl+C)")
        except Exception as e:
            self.logger.error(f"Critical error: {e}")
            self.logger.error(f"Traceback: {traceback.format_exc()}")
            await self.graceful_shutdown(f"Critical error: {e}")
            raise
