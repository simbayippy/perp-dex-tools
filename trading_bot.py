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

    def __init__(self, config: TradingConfig):
        self.config = config
        self.logger = get_logger("bot", config.strategy, context={"exchange": config.exchange, "ticker": config.ticker}, log_to_console=True)

        # Determine if strategy needs multiple exchanges
        multi_exchange_strategies = ['funding_arbitrage']
        is_multi_exchange = config.strategy in multi_exchange_strategies
        
        # Create exchange client(s)
        try:
            if is_multi_exchange:
                # Multi-exchange mode (for funding arbitrage, etc.)
                # Get list of exchanges from strategy params
                # Check both 'scan_exchanges' (from YAML) and 'exchanges' (from CLI) for backward compat
                exchange_list = config.strategy_params.get('scan_exchanges') or config.strategy_params.get('exchanges', [config.exchange])
                if isinstance(exchange_list, str):
                    exchange_list = [ex.strip() for ex in exchange_list.split(',')]
                
                self.logger.info(f"Creating clients for exchanges: {exchange_list}")
                
                # Create multiple exchange clients
                self.exchange_clients = ExchangeFactory.create_multiple_exchanges(
                    exchange_names=exchange_list,
                    config=config,
                    primary_exchange=config.exchange
                )
                
                # Set primary exchange client for backward compatibility
                self.exchange_client = self.exchange_clients[config.exchange]
                
                self.logger.info(f"Created {len(self.exchange_clients)} exchange clients")
            else:
                # Single exchange mode (for grid strategy, etc.)
                self.exchange_client = ExchangeFactory.create_exchange(
                    config.exchange,
                    config
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
            # For single-symbol strategies (grid), get contract attributes
            # For multi-symbol strategies (funding_arbitrage), skip this - they handle symbols dynamically
            multi_symbol_strategies = ['funding_arbitrage']
            
            if self.config.strategy not in multi_symbol_strategies:
                self.config.contract_id, self.config.tick_size = await self.exchange_client.get_contract_attributes()
            else:
                # Multi-symbol strategy - set placeholder values
                self.config.contract_id = "MULTI_SYMBOL"
                self.config.tick_size = Decimal("0.01")  # Placeholder
                self.logger.info("Multi-symbol strategy: Contract attributes will be fetched per-symbol")

            # Log current configuration
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

            # Capture the running event loop for thread-safe callbacks
            self.loop = asyncio.get_running_loop()
            
            # Connect to exchange(s)
            # if self.exchange_clients:
            #     # Multi-exchange mode: connect all clients
            #     for exchange_name, client in self.exchange_clients.items():
            #         self.logger.info(f"Connecting to {exchange_name}...")
            #         await client.connect()
            #         self.logger.info(f"Connected to {exchange_name}")
            # else:
            #     # Single exchange mode
            #     await self.exchange_client.connect()

            # Initialize strategy after connection
            await self.strategy.initialize()
            
            # Main trading loop
            while not self.shutdown_requested:
                # Strategy-based execution (universal interface)
                try:
                    if await self.strategy.should_execute(None):
                        await self.strategy.execute_strategy(None)

                    else:
                        await asyncio.sleep(1)  # Brief wait if strategy says not to execute
                        
                except Exception as e:
                    self.logger.error(f"Strategy execution error: {e}")
                    await asyncio.sleep(5)  # Wait longer on error

        except KeyboardInterrupt:
            self.logger.info("Bot stopped by user")
            # Emergency close all positions on Ctrl+C
            await self._emergency_close_all_positions()
            await self.graceful_shutdown("User interruption (Ctrl+C)")
        except Exception as e:
            self.logger.error(f"Critical error: {e}")
            self.logger.error(f"Traceback: {traceback.format_exc()}")
            await self.graceful_shutdown(f"Critical error: {e}")
            raise
