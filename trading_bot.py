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

from exchanges import ExchangeFactory
from helpers import TradingLogger
from helpers.lark_bot import LarkBot
from helpers.telegram_bot import TelegramBot
from helpers.risk_manager import RiskManager, RiskAction, RiskThresholds
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
        self.logger = TradingLogger(config.exchange, config.ticker, log_to_console=True)

        # Create exchange client
        try:
            self.exchange_client = ExchangeFactory.create_exchange(
                config.exchange,
                config
            )
        except ValueError as e:
            raise ValueError(f"Failed to create exchange client: {e}")

        # Trading state
        self.last_log_time = 0
        self.shutdown_requested = False
        self.loop = None

        # Register order callback
        self._setup_websocket_handlers()
        
        # Initialize strategy
        try:
            self.strategy = StrategyFactory.create_strategy(
                self.config.strategy,
                self.config,
                self.exchange_client
            )
            self.logger.log(f"Strategy '{self.config.strategy}' created successfully", "INFO")
        except ValueError as e:
            raise ValueError(f"Failed to create strategy: {e}")
        
        # Initialize risk manager (only for supported exchanges)
        self.risk_manager = None
        if self.exchange_client.supports_risk_management():
            self.risk_manager = RiskManager(self.exchange_client, self.config)
            self.logger.log("Risk management enabled", "INFO")
        else:
            self.logger.log("Risk management not supported by exchange", "INFO")

    async def graceful_shutdown(self, reason: str = "Unknown"):
        """Perform graceful shutdown of the trading bot."""
        self.logger.log(f"Starting graceful shutdown: {reason}", "INFO")
        self.shutdown_requested = True

        try:
            # Cleanup strategy
            if hasattr(self, 'strategy') and self.strategy:
                await self.strategy.cleanup()
            
            # Disconnect from exchange
            await self.exchange_client.disconnect()
            self.logger.log("Graceful shutdown completed", "INFO")

        except Exception as e:
            self.logger.log(f"Error during graceful shutdown: {e}", "ERROR")

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
                    self.logger.log(f"[{order_type}] [{order_id}] {status} "
                                    f"{message.get('size')} @ {message.get('price')}", "INFO")
                    self.logger.log_transaction(order_id, side, message.get('size'), message.get('price'), status)
                    
                    # Notify strategy of successful order
                    if self.risk_manager:
                        self.risk_manager.record_successful_order()
                        
                elif status == "CANCELED":
                    self.logger.log(f"[{order_type}] [{order_id}] {status} "
                                    f"{message.get('size')} @ {message.get('price')}", "INFO")
                    
                    if filled_size > 0:
                        self.logger.log_transaction(order_id, side, filled_size, message.get('price'), status)
                        
                elif status == "PARTIALLY_FILLED":
                    self.logger.log(f"[{order_type}] [{order_id}] {status} "
                                    f"{filled_size} @ {message.get('price')}", "INFO")
                else:
                    self.logger.log(f"[{order_type}] [{order_id}] {status} "
                                    f"{message.get('size')} @ {message.get('price')}", "INFO")

            except Exception as e:
                self.logger.log(f"Error handling order update: {e}", "ERROR")
                self.logger.log(f"Traceback: {traceback.format_exc()}", "ERROR")

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
            self.config.contract_id, self.config.tick_size = await self.exchange_client.get_contract_attributes()

            # Log current configuration
            self.logger.log("=== Trading Configuration ===", "INFO")
            self.logger.log(f"Ticker: {self.config.ticker}", "INFO")
            self.logger.log(f"Contract ID: {self.config.contract_id}", "INFO")
            self.logger.log(f"Quantity: {self.config.quantity}", "INFO")
            self.logger.log(f"Exchange: {self.config.exchange}", "INFO")
            self.logger.log(f"Strategy: {self.config.strategy}", "INFO")
            
            # Log strategy parameters
            if self.config.strategy_params:
                self.logger.log("Strategy Parameters:", "INFO")
                for key, value in self.config.strategy_params.items():
                    self.logger.log(f"  {key}: {value}", "INFO")
                
            self.logger.log("=============================", "INFO")

            # Capture the running event loop for thread-safe callbacks
            self.loop = asyncio.get_running_loop()
            # Connect to exchange
            await self.exchange_client.connect()

            # wait for connection to establish
            await asyncio.sleep(5)
            
            # Initialize strategy after connection
            await self.strategy.initialize()
            
            # Initialize risk manager after connection
            if self.risk_manager:
                await self.risk_manager.initialize()

            # Main trading loop
            while not self.shutdown_requested:
                # Check risk conditions first
                if self.risk_manager:
                    risk_action = await self.risk_manager.check_risk_conditions()
                    if risk_action != RiskAction.NONE:
                        await self._handle_risk_action(risk_action)
                        if risk_action == RiskAction.EMERGENCY_CLOSE_ALL:
                            await self.graceful_shutdown("Emergency risk management triggered")
                            break

                # Strategy-based execution
                try:
                    market_data = await self.strategy.get_market_data()
                    
                    if await self.strategy.should_execute(market_data):
                        # For grid strategy, use the complete cycle method
                        if self.config.strategy == 'grid' and hasattr(self.strategy, 'execute_grid_cycle'):
                            success, error_message = await self.strategy.execute_grid_cycle()
                            if not success and self.risk_manager and error_message:
                                # Check if this was a margin failure
                                if 'margin' in error_message.lower():
                                    self.risk_manager.record_margin_failure()
                            elif success and self.risk_manager:
                                # Record successful order
                                self.risk_manager.record_successful_order()
                        else:
                            # For other strategies, use the standard flow
                            strategy_result = await self.strategy.execute_strategy(market_data)
                            await self._handle_strategy_result(strategy_result)
                    else:
                        await asyncio.sleep(1)  # Brief wait if strategy says not to execute
                        
                except Exception as e:
                    self.logger.log(f"Strategy execution error: {e}", "ERROR")
                    await asyncio.sleep(5)  # Wait longer on error

        except KeyboardInterrupt:
            self.logger.log("Bot stopped by user")
            # Emergency close all positions on Ctrl+C
            if self.risk_manager:
                await self._emergency_close_all_positions()
            await self.graceful_shutdown("User interruption (Ctrl+C)")
        except Exception as e:
            self.logger.log(f"Critical error: {e}", "ERROR")
            self.logger.log(f"Traceback: {traceback.format_exc()}", "ERROR")
            await self.graceful_shutdown(f"Critical error: {e}")
            raise
        finally:
            # Ensure all connections are closed even if graceful shutdown fails
            try:
                await self.exchange_client.disconnect()
            except Exception as e:
                self.logger.log(f"Error disconnecting from exchange: {e}", "ERROR")

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
            self.logger.log("Strategy rebalancing requested", "INFO")
            for order_params in strategy_result.orders:
                await self._execute_order(order_params)
        
        if strategy_result.message:
            self.logger.log(f"Strategy: {strategy_result.message}", "INFO")

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
                
                result = await self.exchange_client.place_close_order(
                    contract_id=order_params.contract_id or self.config.contract_id,
                    quantity=order_params.quantity,
                    price=order_params.price,
                    side=order_params.side
                )
            
            if result.success:
                self.logger.log(f"Order executed: {order_params.side} {order_params.quantity} @ {order_params.price or 'market'}", "INFO")
                return True
            else:
                self.logger.log(f"Order failed: {result.error_message}", "ERROR")
                return False
                
        except Exception as e:
            self.logger.log(f"Error executing order: {e}", "ERROR")
            return False

    async def _handle_risk_action(self, risk_action: RiskAction):
        """Handle risk management actions."""
        if risk_action == RiskAction.CLOSE_WORST_POSITIONS:
            await self._close_worst_positions()
        elif risk_action == RiskAction.EMERGENCY_CLOSE_ALL:
            await self._emergency_close_all_positions()
        elif risk_action == RiskAction.PAUSE_TRADING:
            self.logger.log("Risk management: Pausing trading", "WARNING")
            await asyncio.sleep(30)  # Pause for 30 seconds

    async def _close_worst_positions(self):
        """Close worst performing positions."""
        if not self.risk_manager:
            return
            
        try:
            worst_positions = await self.risk_manager.get_worst_positions()
            if not worst_positions:
                self.logger.log("No worst positions to close", "INFO")
                return
            
            self.logger.log(f"Closing {len(worst_positions)} worst positions", "WARNING")
            
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
                        self.logger.log(
                            f"Closed position {position['symbol']}: {quantity} @ market ({side})",
                            "INFO"
                        )
                    else:
                        self.logger.log(
                            f"Failed to close position {position['symbol']}: {result.error_message}",
                            "ERROR"
                        )
                        
                except Exception as e:
                    self.logger.log(f"Error closing position {position.get('symbol', 'unknown')}: {e}", "ERROR")
            
            # Reset risk counters after successful action
            if self.risk_manager:
                self.risk_manager.record_successful_order()
                
        except Exception as e:
            self.logger.log(f"Error in _close_worst_positions: {e}", "ERROR")

    async def _emergency_close_all_positions(self):
        """Emergency close all positions."""
        if not self.risk_manager:
            return
            
        try:
            all_positions = await self.risk_manager.get_all_positions()
            if not all_positions:
                self.logger.log("No positions to close", "INFO")
                return
            
            self.logger.log(f"EMERGENCY: Closing ALL {len(all_positions)} positions", "ERROR")
            
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
                        self.logger.log(
                            f"EMERGENCY CLOSED: {position['symbol']}: {quantity} @ market ({side})",
                            "WARNING"
                        )
                    else:
                        self.logger.log(
                            f"FAILED EMERGENCY CLOSE: {position['symbol']}: {result.error_message}",
                            "ERROR"
                        )
                        
                except Exception as e:
                    self.logger.log(f"Error in emergency close {position.get('symbol', 'unknown')}: {e}", "ERROR")
                    
        except Exception as e:
            self.logger.log(f"Error in _emergency_close_all_positions: {e}", "ERROR")
