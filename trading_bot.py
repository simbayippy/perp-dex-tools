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
from strategies.components.account_monitor import AccountMonitor, AccountAction, AccountThresholds
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


@dataclass
class OrderMonitor:
    """Thread-safe order monitoring state."""
    order_id: Optional[str] = None
    filled: bool = False
    filled_price: Optional[Decimal] = None
    filled_qty: Decimal = 0.0

    def reset(self):
        """Reset the monitor state."""
        self.order_id = None
        self.filled = False
        self.filled_price = None
        self.filled_qty = 0.0


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

        # Register order callback (only for primary exchange in multi-exchange mode)
        self._setup_websocket_handlers()
        
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
        
        # Initialize account monitor (monitors account health)
        self.account_monitor = AccountMonitor(self.exchange_client, self.config)

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

    def _setup_websocket_handlers(self):
        """Setup WebSocket handlers for order updates."""
        def order_update_handler(message):
            """Handle order updates from WebSocket."""
            try:
                # Check if this is for our contract
                if message.get('contract_id') != self.config.contract_id:
                    return

                order_id = message.get('order_id')
                status = message.get('status')
                side = message.get('side', '')
                order_type = message.get('order_type', '')
                filled_size = Decimal(message.get('filled_size', 0))

                # Log order updates
                if status == 'FILLED':
                    self.logger.info(f"[{order_type}] [{order_id}] {status} "
                                    f"{message.get('size')} @ {message.get('price')}")
                    self.logger.log_transaction(order_id, side, message.get('size'), message.get('price'), status)
                    
                    # Notify strategy of successful order
                    if self.risk_manager:
                        self.risk_manager.record_successful_order()
                        
                elif status == "CANCELED":
                    self.logger.info(f"[{order_type}] [{order_id}] {status} "
                                    f"{message.get('size')} @ {message.get('price')}")
                    
                    if filled_size > 0:
                        self.logger.log_transaction(order_id, side, filled_size, message.get('price'), status)
                        
                elif status == "PARTIALLY_FILLED":
                    self.logger.info(f"[{order_type}] [{order_id}] {status} "
                                    f"{filled_size} @ {message.get('price')}")
                else:
                    self.logger.info(f"[{order_type}] [{order_id}] {status} "
                                    f"{message.get('size')} @ {message.get('price')}")

            except Exception as e:
                self.logger.error(f"Error handling order update: {e}")
                self.logger.error(f"Traceback: {traceback.format_exc()}")

        # Setup order update handler
        self.exchange_client.setup_order_update_handler(order_update_handler)

    async def send_notification(self, message: str):
        lark_token = os.getenv("LARK_TOKEN")
        if lark_token:
            async with LarkBot(lark_token) as lark_bot:
                await lark_bot.send_text(message)

        telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
        telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        if telegram_token and telegram_chat_id:
            with TelegramBot(telegram_token, telegram_chat_id) as tg_bot:
                tg_bot.send_text(message)

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
            if self.exchange_clients:
                # Multi-exchange mode: connect all clients
                for exchange_name, client in self.exchange_clients.items():
                    self.logger.info(f"Connecting to {exchange_name}...")
                    await client.connect()
                    self.logger.info(f"Connected to {exchange_name}")
            else:
                # Single exchange mode
                await self.exchange_client.connect()

            # wait for connection to establish
            await asyncio.sleep(5)
            
            # Initialize strategy after connection
            await self.strategy.initialize()
            
            # Initialize account monitor after connection
            await self.account_monitor.initialize()

            # Main trading loop
            while not self.shutdown_requested:
                # Check account conditions first
                account_action = await self.account_monitor.check_account_conditions()
                if account_action != AccountAction.NONE:
                    await self._handle_account_action(account_action)
                    if account_action == AccountAction.EMERGENCY_CLOSE_ALL:
                        # TODO: add notification (telegram or lark)
                        await self.graceful_shutdown("Emergency account action triggered")
                        break

                # Strategy-based execution (universal interface)
                try:
                    # For strategies that need market data, they can fetch it internally
                    # This simplifies the interface and allows strategies to get their own data
                    if await self.strategy.should_execute(None):
                        # All strategies use the same interface
                        strategy_result = await self.strategy.execute_strategy(None)
                        await self._handle_strategy_result(strategy_result)
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
        finally:
            # Ensure all connections are closed even if graceful shutdown fails
            try:
                if self.exchange_clients:
                    # Multi-exchange mode
                    for client in self.exchange_clients.values():
                        try:
                            await client.disconnect()
                        except:
                            pass
                else:
                    # Single exchange mode
                    await self.exchange_client.disconnect()
            except Exception as e:
                self.logger.error(f"Error disconnecting from exchange: {e}")

    async def _handle_strategy_result(self, strategy_result):
        """Handle the result from strategy execution."""
        from strategies.base_strategy import StrategyAction
        
        if strategy_result.action == StrategyAction.PLACE_ORDER:
            for order_params in strategy_result.orders:
                success = await self._execute_order(order_params)
                if success and hasattr(self.strategy, 'record_successful_order'):
                    self.strategy.record_successful_order()
                elif not success and self.risk_manager:
                    # This might be a margin failure
                    self.risk_manager.record_margin_failure()
        
        elif strategy_result.action == StrategyAction.WAIT:
            if strategy_result.wait_time > 0:
                await asyncio.sleep(strategy_result.wait_time)
        
        elif strategy_result.action == StrategyAction.CLOSE_POSITION:
            # Handle position closing
            for order_params in strategy_result.orders:
                await self._execute_order(order_params)
        
        elif strategy_result.action == StrategyAction.REBALANCE:
            # Handle rebalancing
            self.logger.info("Strategy rebalancing requested")
            for order_params in strategy_result.orders:
                await self._execute_order(order_params)
        
        if strategy_result.message:
            self.logger.info(f"Strategy: {strategy_result.message}")

    async def _execute_order(self, order_params) -> bool:
        """Execute an order based on order parameters."""
        try:
            if order_params.order_type == "market":
                result = await self.exchange_client.place_market_order(
                    contract_id=order_params.contract_id or self.config.contract_id,
                    quantity=order_params.quantity,
                    side=order_params.side
                )
            else:  # limit order
                if order_params.price is None:
                    # Get current market price for limit order
                    best_bid, best_ask = await self.exchange_client.fetch_bbo_prices(self.config.contract_id)
                    order_params.price = best_ask if order_params.side == 'buy' else best_bid
                
                # Use place_limit_order for all limit orders
                result = await self.exchange_client.place_limit_order(
                    contract_id=order_params.contract_id or self.config.contract_id,
                    quantity=order_params.quantity,
                    price=order_params.price,
                    side=order_params.side
                )
            
            if result.success:
                self.logger.info(f"Order executed: {order_params.side} {order_params.quantity} @ {order_params.price or result.price or 'market'}")
                
                # Notify strategy if order got filled (for state tracking)
                if result.price and hasattr(self.strategy, 'notify_order_filled'):
                    filled_quantity = result.filled_size or order_params.quantity
                    self.strategy.notify_order_filled(result.price, filled_quantity)
                
                return True
            else:
                self.logger.error(f"Order failed: {result.error_message}")
                return False
                
        except Exception as e:
            self.logger.error(f"Error executing order: {e}")
            return False

    async def _handle_account_action(self, account_action: AccountAction):
        """Handle account monitoring actions."""
        if account_action == AccountAction.CLOSE_WORST_POSITIONS:
            await self._close_worst_positions()
        elif account_action == AccountAction.EMERGENCY_CLOSE_ALL:
            await self._emergency_close_all_positions()
        elif account_action == AccountAction.PAUSE_TRADING:
            self.logger.warning("Account monitoring: Pausing trading")
            await asyncio.sleep(30)  # Pause for 30 seconds

    async def _close_worst_positions(self):
        """Close worst performing positions."""
        try:
            worst_positions = await self.account_monitor.get_worst_positions()
            if not worst_positions:
                self.logger.info("No worst positions to close")
                return
            
            self.logger.warning(f"Closing {len(worst_positions)} worst positions")
            
            for position in worst_positions:
                try:
                    # Determine order side (opposite of position)
                    side = 'sell' if position['sign'] > 0 else 'buy'
                    quantity = abs(position['position'])
                    
                    # Place market order to close position
                    result = await self.exchange_client.place_market_order(
                        contract_id=str(position['market_id']),
                        quantity=quantity,
                        side=side
                    )
                    
                    if result.success:
                        self.logger.info(
                            f"Closed position {position['symbol']}: {quantity} @ market ({side})"
                        )
                    else:
                        self.logger.error(
                            f"Failed to close position {position['symbol']}: {result.error_message}"
                        )
                        
                except Exception as e:
                    self.logger.error(f"Error closing position {position.get('symbol', 'unknown')}: {e}")
            
            # Reset account counters after successful action
            self.account_monitor.record_successful_order()
                
        except Exception as e:
            self.logger.error(f"Error in _close_worst_positions: {e}")

    async def _emergency_close_all_positions(self):
        """Emergency close all positions."""
        try:
            all_positions = await self.account_monitor.get_all_positions()
            if not all_positions:
                self.logger.info("No positions to close")
                return
            
            self.logger.error(f"EMERGENCY: Closing ALL {len(all_positions)} positions")
            
            for position in all_positions:
                try:
                    # Determine order side (opposite of position)
                    side = 'sell' if position['sign'] > 0 else 'buy'
                    quantity = abs(position['position'])
                    
                    # Place market order to close position
                    result = await self.exchange_client.place_market_order(
                        contract_id=str(position['market_id']),
                        quantity=quantity,
                        side=side
                    )
                    
                    if result.success:
                        self.logger.warning(
                            f"EMERGENCY CLOSED: {position['symbol']}: {quantity} @ market ({side})"
                        )
                    else:
                        self.logger.error(
                            f"FAILED EMERGENCY CLOSE: {position['symbol']}: {result.error_message}"
                        )
                        
                except Exception as e:
                    self.logger.error(f"Error in emergency close {position.get('symbol', 'unknown')}: {e}")
                    
        except Exception as e:
            self.logger.error(f"Error in _emergency_close_all_positions: {e}")
