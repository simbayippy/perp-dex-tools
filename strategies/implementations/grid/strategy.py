"""
Grid Trading Strategy

Migrated from legacy grid_strategy.py to new architecture.
Uses StatelessStrategy base for single-exchange grid trading.
"""

import time
import random
import asyncio
from decimal import Decimal
from typing import List, Dict, Any, Optional

from strategies.categories.stateless_strategy import StatelessStrategy
from strategies.components import OrderType, TradeType, PositionAction
from .config import GridConfig
from .models import GridState, GridOrder, GridCycleState


class GridStrategy(StatelessStrategy):
    """
    Grid trading strategy implementation.
    
    This strategy:
    1. Places orders at regular price intervals (grid levels)
    2. Takes profit when price moves to the next grid level
    3. Maintains a maximum number of active orders
    4. Uses dynamic cooldown based on order density
    5. Supports optional safety features (stop/pause prices)
    """
    
    def __init__(
        self,
        config: GridConfig,
        exchange_client,
        logger=None
    ):
        """
        Initialize grid strategy.
        
        Args:
            config: GridConfig instance with strategy parameters
            exchange_client: Exchange client for trading
            logger: Optional logger instance
        """
        super().__init__(
            config=config,
            exchange_client=exchange_client,
            logger=logger
        )
        
        # Initialize grid state
        self.grid_state = GridState()
        
        self.logger.log("Grid strategy initialized with parameters:", "INFO")
        self.logger.log(f"  - Take Profit: {config.take_profit}%", "INFO")
        self.logger.log(f"  - Grid Step: {config.grid_step}%", "INFO")
        self.logger.log(f"  - Direction: {config.direction}", "INFO")
        self.logger.log(f"  - Max Orders: {config.max_orders}", "INFO")
        self.logger.log(f"  - Wait Time: {config.wait_time}s", "INFO")
        
        # Log safety parameters if set
        if config.stop_price is not None:
            self.logger.log(
                f"  - Stop Price: {config.stop_price} "
                f"(will stop if {'below' if config.direction == 'buy' else 'above'})", 
                "WARNING"
            )
        
        if config.pause_price is not None:
            self.logger.log(
                f"  - Pause Price: {config.pause_price} "
                f"(will pause if {'above' if config.direction == 'buy' else 'below'})", 
                "INFO"
            )
    
    async def initialize(self) -> None:
        """Initialize strategy (called by base class)."""
        # Load persisted state if available
        # (In future, this would load from state_manager)
        pass
    
    async def should_execute(self) -> bool:
        """Determine if grid strategy should execute."""
        try:
            # Get current market data
            best_bid, best_ask = await self.exchange_client.fetch_bbo_prices(
                self.exchange_client.config.contract_id
            )
            current_price = (best_bid + best_ask) / 2
            
            # Check stop price - critical safety check first
            if self.config.stop_price is not None:
                if (self.config.direction == 'buy' and current_price < self.config.stop_price) or \
                   (self.config.direction == 'sell' and current_price > self.config.stop_price):
                    self.logger.log(
                        f"⚠️ STOP PRICE TRIGGERED! Current: {current_price}, Stop: {self.config.stop_price}", 
                        "WARNING"
                    )
                    self.logger.log("Canceling all orders and stopping strategy...", "WARNING")
                    await self._cancel_all_orders()
                    return False
            
            # Check pause price - temporary pause
            if self.config.pause_price is not None:
                if (self.config.direction == 'buy' and current_price > self.config.pause_price) or \
                   (self.config.direction == 'sell' and current_price < self.config.pause_price):
                    self.logger.log(
                        f"⏸️ PAUSE PRICE REACHED! Current: {current_price}, Pause: {self.config.pause_price}", 
                        "INFO"
                    )
                    return False
            
            # Update active orders
            await self._update_active_orders()
            
            # Check if we should wait based on cooldown
            wait_time = self._calculate_wait_time()
            if wait_time > 0:
                return False
            
            # Check grid step condition
            return await self._meet_grid_step_condition(best_bid, best_ask)
            
        except Exception as e:
            self.logger.log(f"Error in should_execute: {e}", "ERROR")
            return False
    
    async def execute(self) -> Dict[str, Any]:
        """
        Execute grid strategy using state machine pattern.
        
        Grid cycle states:
        1. 'READY' → Place open order
        2. 'WAITING_FOR_FILL' → Check if filled, then place close order  
        3. 'COMPLETE' → Reset state
        
        Returns:
            Dictionary with execution result
        """
        try:
            # State 1: Ready to place open order
            if self.grid_state.cycle_state == GridCycleState.READY:
                return await self._place_open_order()
            
            # State 2: Waiting for fill, then place close order
            elif self.grid_state.cycle_state == GridCycleState.WAITING_FOR_FILL:
                return await self._handle_filled_order()
            
            else:
                # Unknown state, reset
                self.grid_state.cycle_state = GridCycleState.READY
                return {
                    'action': 'wait',
                    'message': 'Grid: Resetting cycle state',
                    'wait_time': 1
                }
                
        except Exception as e:
            self.logger.log(f"Error executing grid strategy: {e}", "ERROR")
            # Reset state on error
            self.grid_state.cycle_state = GridCycleState.READY
            return {
                'action': 'error',
                'message': f'Grid strategy error: {e}',
                'wait_time': 5
            }
    
    async def _place_open_order(self) -> Dict[str, Any]:
        """Place an open order to enter a new grid level."""
        try:
            # Place order using exchange client
            quantity = self.exchange_client.config.quantity
            contract_id = self.exchange_client.config.contract_id
            
            order_result = await self.exchange_client.place_open_order(
                contract_id=contract_id,
                quantity=quantity,
                direction=self.config.direction
            )
            
            if order_result.success:
                # Update state to waiting for fill
                self.grid_state.cycle_state = GridCycleState.WAITING_FOR_FILL
                
                # Record filled price and quantity from result
                if order_result.status == 'FILLED':
                    self.grid_state.filled_price = order_result.price
                    self.grid_state.filled_quantity = order_result.size
                
                self.logger.log(
                    f"Grid: Placed {self.config.direction} order for {quantity} @ {order_result.price}",
                    "INFO"
                )
                
                return {
                    'action': 'order_placed',
                    'order_id': order_result.order_id,
                    'side': self.config.direction,
                    'quantity': quantity,
                    'price': order_result.price,
                    'status': order_result.status
                }
            else:
                self.logger.log(f"Grid: Failed to place open order: {order_result.error_message}", "ERROR")
                return {
                    'action': 'error',
                    'message': order_result.error_message,
                    'wait_time': 5
                }
                
        except Exception as e:
            self.logger.log(f"Error placing open order: {e}", "ERROR")
            return {
                'action': 'error',
                'message': str(e),
                'wait_time': 5
            }
    
    async def _handle_filled_order(self) -> Dict[str, Any]:
        """Handle a filled open order by placing corresponding close order."""
        # Check if we have filled price (may be set by notify_order_filled callback)
        if self.grid_state.filled_price and self.grid_state.filled_quantity:
            try:
                # Calculate close order parameters
                close_side = 'sell' if self.config.direction == 'buy' else 'buy'
                close_price = self._calculate_close_price(self.grid_state.filled_price)
                
                # Place close order
                if self.config.boost_mode:
                    # Boost mode: use market order for faster execution
                    order_result = await self.exchange_client.place_market_order(
                        contract_id=self.exchange_client.config.contract_id,
                        quantity=self.grid_state.filled_quantity,
                        side=close_side
                    )
                else:
                    # Normal mode: use limit order
                    order_result = await self.exchange_client.place_close_order(
                        contract_id=self.exchange_client.config.contract_id,
                        quantity=self.grid_state.filled_quantity,
                        price=close_price,
                        side=close_side
                    )
                
                if order_result.success:
                    # Reset state for next cycle
                    self.grid_state.cycle_state = GridCycleState.READY
                    self.grid_state.filled_price = None
                    self.grid_state.filled_quantity = None
                    self.grid_state.last_open_order_time = time.time()
                    
                    self.logger.log(
                        f"Grid: Placed close order at {close_price}",
                        "INFO"
                    )
                    
                    return {
                        'action': 'order_placed',
                        'order_id': order_result.order_id,
                        'side': close_side,
                        'quantity': self.grid_state.filled_quantity,
                        'price': close_price,
                        'wait_time': 1 if self.exchange_client.get_exchange_name() == "lighter" else 0
                    }
                else:
                    self.logger.log(f"Grid: Failed to place close order: {order_result.error_message}", "ERROR")
                    return {
                        'action': 'error',
                        'message': order_result.error_message,
                        'wait_time': 5
                    }
                    
            except Exception as e:
                self.logger.log(f"Error placing close order: {e}", "ERROR")
                return {
                    'action': 'error',
                    'message': str(e),
                    'wait_time': 5
                }
        else:
            # Still waiting for fill notification
            return {
                'action': 'wait',
                'message': 'Grid: Waiting for open order to fill',
                'wait_time': 0.5
            }
    
    def notify_order_filled(self, filled_price: Decimal, filled_quantity: Decimal):
        """
        Notify strategy that an order was filled.
        
        This is called by the trading bot after successful order execution.
        """
        self.grid_state.filled_price = filled_price
        self.grid_state.filled_quantity = filled_quantity
        self.logger.log(
            f"Grid: Order filled at {filled_price} for {filled_quantity}",
            "INFO"
        )
    
    def _calculate_close_price(self, filled_price: Decimal) -> Decimal:
        """Calculate the close price based on take-profit settings."""
        # Calculate dynamic take-profit if enabled
        take_profit_pct = self.config.take_profit
        if self.config.dynamic_profit:
            base_profit = float(take_profit_pct)
            variation = base_profit * float(self.config.profit_range)
            random_offset = random.uniform(-variation, variation)
            take_profit_pct = Decimal(max(0, base_profit + random_offset))
            
            self.logger.log(
                f"Dynamic profit: base={self.config.take_profit}%, "
                f"actual={take_profit_pct:.4f}% (±{variation:.4f}%)", 
                "DEBUG"
            )
        
        # Calculate close price
        if self.config.direction == 'buy':
            # Long position: sell higher
            close_price = filled_price * (1 + take_profit_pct / 100)
        else:
            # Short position: buy back lower
            close_price = filled_price * (1 - take_profit_pct / 100)
        
        return close_price
    
    async def _update_active_orders(self):
        """Update active close orders."""
        try:
            active_orders = await self.exchange_client.get_active_orders(
                self.exchange_client.config.contract_id
            )
            
            # Filter close orders (opposite side of direction)
            close_side = 'sell' if self.config.direction == 'buy' else 'buy'
            
            self.grid_state.active_close_orders = [
                GridOrder(
                    order_id=order.order_id,
                    price=order.price,
                    size=order.size,
                    side=order.side
                )
                for order in active_orders
                if order.side == close_side
            ]
            
        except Exception as e:
            self.logger.log(f"Error updating active orders: {e}", "ERROR")
    
    def _calculate_wait_time(self) -> float:
        """Calculate wait time between orders with optional randomization."""
        active_count = len(self.grid_state.active_close_orders)
        last_count = self.grid_state.last_close_orders_count
        
        # If orders decreased (one filled), allow immediate new order
        if active_count < last_count:
            self.grid_state.last_close_orders_count = active_count
            return 0
        
        self.grid_state.last_close_orders_count = active_count
        
        # If at max capacity, wait
        if active_count >= self.config.max_orders:
            return 1
        
        # Dynamic cooldown based on order density
        order_ratio = active_count / self.config.max_orders
        if order_ratio >= 2/3:
            cool_down_time = 2 * self.config.wait_time
        elif order_ratio >= 1/3:
            cool_down_time = self.config.wait_time
        elif order_ratio >= 1/6:
            cool_down_time = self.config.wait_time / 2
        else:
            cool_down_time = self.config.wait_time / 4
        
        # Apply random timing if enabled
        if self.config.random_timing:
            variation = float(cool_down_time) * float(self.config.timing_range)
            random_offset = random.uniform(-variation, variation)
            cool_down_time = max(1, cool_down_time + random_offset)
            
            self.logger.log(
                f"Random timing: base={self.config.wait_time}s, "
                f"calculated={cool_down_time:.1f}s (±{variation:.1f}s)", 
                "DEBUG"
            )
        
        # Handle startup with existing orders
        if self.grid_state.last_open_order_time == 0 and active_count > 0:
            self.grid_state.last_open_order_time = time.time()
        
        # Check if cooldown period has passed
        current_time = time.time()
        if current_time - self.grid_state.last_open_order_time > cool_down_time:
            return 0
        else:
            return 1
    
    async def _meet_grid_step_condition(self, best_bid: Decimal, best_ask: Decimal) -> bool:
        """Check if grid step condition is met."""
        if not self.grid_state.active_close_orders:
            return True
        
        try:
            # Find the next close order price
            if self.config.direction == "buy":
                # Long: find lowest close (sell) order
                next_close_order = min(
                    self.grid_state.active_close_orders, 
                    key=lambda o: o.price
                )
                next_close_price = next_close_order.price
                
                # Calculate new order close price
                new_order_close_price = best_ask * (1 + self.config.take_profit / 100)
                
                # Check if there's enough gap
                return next_close_price / new_order_close_price > 1 + self.config.grid_step / 100
                
            else:  # sell direction
                # Short: find highest close (buy) order
                next_close_order = max(
                    self.grid_state.active_close_orders, 
                    key=lambda o: o.price
                )
                next_close_price = next_close_order.price
                
                # Calculate new order close price
                new_order_close_price = best_bid * (1 - self.config.take_profit / 100)
                
                # Check if there's enough gap
                return new_order_close_price / next_close_price > 1 + self.config.grid_step / 100
                
        except Exception as e:
            self.logger.log(f"Error in grid step condition: {e}", "ERROR")
            return False
    
    async def _cancel_all_orders(self):
        """Cancel all active orders (used when stop price is triggered)."""
        try:
            self.logger.log(
                f"Canceling {len(self.grid_state.active_close_orders)} active orders...", 
                "INFO"
            )
            
            for order in self.grid_state.active_close_orders:
                try:
                    await self.exchange_client.cancel_order(order.order_id)
                    self.logger.log(f"Canceled order {order.order_id}", "INFO")
                except Exception as e:
                    self.logger.log(f"Error canceling order {order.order_id}: {e}", "ERROR")
            
            # Clear active close orders from state
            self.grid_state.active_close_orders = []
            self.logger.log("All orders canceled", "INFO")
            
        except Exception as e:
            self.logger.log(f"Error in _cancel_all_orders: {e}", "ERROR")
    
    async def get_status(self) -> Dict[str, Any]:
        """Get current strategy status."""
        try:
            position = await self.exchange_client.get_account_positions()
            active_close_amount = sum(order.size for order in self.grid_state.active_close_orders)
            
            return {
                "strategy": "grid",
                "cycle_state": self.grid_state.cycle_state.value,
                "active_orders": len(self.grid_state.active_close_orders),
                "position": float(position),
                "active_close_amount": float(active_close_amount),
                "last_order_time": self.grid_state.last_open_order_time,
                "parameters": {
                    "take_profit": float(self.config.take_profit),
                    "grid_step": float(self.config.grid_step),
                    "direction": self.config.direction,
                    "max_orders": self.config.max_orders
                }
            }
        except Exception as e:
            return {
                "strategy": "grid",
                "error": str(e)
            }

