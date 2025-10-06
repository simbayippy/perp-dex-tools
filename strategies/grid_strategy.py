"""
Grid Trading Strategy
Implements the original grid trading logic as a modular strategy.
"""

import time
import random
from decimal import Decimal
from typing import List, Dict, Any

from .base_strategy import BaseStrategy, StrategyResult, StrategyAction, OrderParams, MarketData


class GridStrategy(BaseStrategy):
    """Grid trading strategy implementation."""
    
    def get_strategy_name(self) -> str:
        """Get the strategy name."""
        return "grid"
    
    def get_required_parameters(self) -> List[str]:
        """Get list of required strategy parameters."""
        return [
            "take_profit",
            "grid_step", 
            "direction",
            "max_orders",
            "wait_time"
        ]
    
    async def _initialize_strategy(self):
        """Initialize grid strategy."""
        # Validate parameters
        if not self.validate_parameters():
            raise ValueError("Grid strategy missing required parameters")
        
        # Initialize grid state
        self.update_strategy_state("active_close_orders", [])
        self.update_strategy_state("last_close_orders", 0)
        self.update_strategy_state("last_open_order_time", 0)
        self.update_strategy_state("order_filled_amount", Decimal('0'))
        
        self.logger.log("Grid strategy initialized with parameters:", "INFO")
        self.logger.log(f"  - Take Profit: {self.get_parameter('take_profit')}%", "INFO")
        self.logger.log(f"  - Grid Step: {self.get_parameter('grid_step')}%", "INFO")
        self.logger.log(f"  - Direction: {self.get_parameter('direction')}", "INFO")
        self.logger.log(f"  - Max Orders: {self.get_parameter('max_orders')}", "INFO")
        self.logger.log(f"  - Wait Time: {self.get_parameter('wait_time')}s", "INFO")
    
    async def should_execute(self, market_data: MarketData) -> bool:
        """Determine if grid strategy should execute."""
        try:
            # Update active orders
            await self._update_active_orders()
            
            # Check if we should wait based on cooldown
            wait_time = self._calculate_wait_time()
            if wait_time > 0:
                return False
            
            # Check grid step condition
            return await self._meet_grid_step_condition(market_data)
            
        except Exception as e:
            self.logger.log(f"Error in should_execute: {e}", "ERROR")
            return False
    
    async def execute_strategy(self, market_data: MarketData) -> StrategyResult:
        """Execute grid strategy."""
        try:
            # Create order parameters for grid trading
            direction = self.get_parameter('direction')
            quantity = self.config.quantity
            
            # Create the open order
            order_params = OrderParams(
                side=direction,
                quantity=quantity,
                order_type="limit",  # Grid uses limit orders
                exchange=self.config.exchange,
                contract_id=self.config.contract_id,
                metadata={
                    "strategy": "grid",
                    "order_type": "open",
                    "direction": direction
                }
            )
            
            return StrategyResult(
                action=StrategyAction.PLACE_ORDER,
                orders=[order_params],
                message=f"Grid {direction} order: {quantity} @ market",
                wait_time=1  # Brief wait after order placement
            )
            
        except Exception as e:
            self.logger.log(f"Error executing grid strategy: {e}", "ERROR")
            return StrategyResult(
                action=StrategyAction.WAIT,
                message=f"Grid strategy error: {e}",
                wait_time=5
            )
    
    async def _update_active_orders(self):
        """Update active close orders."""
        try:
            active_orders = await self.exchange_client.get_active_orders(self.config.contract_id)
            
            # Filter close orders
            close_order_side = self._get_close_order_side()
            active_close_orders = []
            
            for order in active_orders:
                if order.side == close_order_side:
                    active_close_orders.append({
                        'id': order.order_id,
                        'price': order.price,
                        'size': order.size
                    })
            
            self.update_strategy_state("active_close_orders", active_close_orders)
            
        except Exception as e:
            self.logger.log(f"Error updating active orders: {e}", "ERROR")
    
    def _calculate_wait_time(self) -> float:
        """Calculate wait time between orders with optional randomization."""
        active_close_orders = self.get_strategy_state("active_close_orders", [])
        last_close_orders = self.get_strategy_state("last_close_orders", 0)
        last_open_order_time = self.get_strategy_state("last_open_order_time", 0)
        
        wait_time_base = self.get_parameter('wait_time')
        max_orders = self.get_parameter('max_orders')
        
        cool_down_time = wait_time_base
        
        if len(active_close_orders) < last_close_orders:
            self.update_strategy_state("last_close_orders", len(active_close_orders))
            return 0
        
        self.update_strategy_state("last_close_orders", len(active_close_orders))
        
        if len(active_close_orders) >= max_orders:
            return 1
        
        # Dynamic cooldown based on order density
        order_ratio = len(active_close_orders) / max_orders
        if order_ratio >= 2/3:
            cool_down_time = 2 * wait_time_base
        elif order_ratio >= 1/3:
            cool_down_time = wait_time_base
        elif order_ratio >= 1/6:
            cool_down_time = wait_time_base / 2
        else:
            cool_down_time = wait_time_base / 4
        
        # Apply random timing if enabled
        if self.get_parameter('random_timing', False):
            timing_range = self.get_parameter('timing_range', Decimal('0.5'))
            variation = float(cool_down_time) * float(timing_range)
            random_offset = random.uniform(-variation, variation)
            cool_down_time = max(1, cool_down_time + random_offset)
            
            self.logger.log(f"Random timing: base={wait_time_base}s, "
                          f"calculated={cool_down_time:.1f}s (±{variation:.1f}s)", "DEBUG")
        
        # Handle startup with existing orders
        if last_open_order_time == 0 and len(active_close_orders) > 0:
            self.update_strategy_state("last_open_order_time", time.time())
        
        current_time = time.time()
        if current_time - last_open_order_time > cool_down_time:
            return 0
        else:
            return 1
    
    async def _meet_grid_step_condition(self, market_data: MarketData) -> bool:
        """Check if grid step condition is met."""
        active_close_orders = self.get_strategy_state("active_close_orders", [])
        
        if not active_close_orders:
            return True
        
        try:
            direction = self.get_parameter('direction')
            take_profit = self.get_parameter('take_profit')
            grid_step = self.get_parameter('grid_step')
            
            # Find the next close order price
            picker = min if direction == "buy" else max
            next_close_order = picker(active_close_orders, key=lambda o: o["price"])
            next_close_price = next_close_order["price"]
            
            # Calculate new order close price
            if direction == "buy":
                new_order_close_price = market_data.best_ask * (1 + take_profit/100)
                return next_close_price / new_order_close_price > 1 + grid_step/100
            elif direction == "sell":
                new_order_close_price = market_data.best_bid * (1 - take_profit/100)
                return new_order_close_price / next_close_price > 1 + grid_step/100
            else:
                raise ValueError(f"Invalid direction: {direction}")
                
        except Exception as e:
            self.logger.log(f"Error in grid step condition: {e}", "ERROR")
            return False
    
    def _get_close_order_side(self) -> str:
        """Get the close order side based on direction."""
        direction = self.get_parameter('direction')
        return 'buy' if direction == "sell" else 'sell'
    
    async def create_close_order(self, filled_price: Decimal, filled_quantity: Decimal) -> OrderParams:
        """Create a close order for a filled open order."""
        close_side = self._get_close_order_side()
        
        # Calculate dynamic take-profit if enabled
        take_profit_pct = self.get_parameter('take_profit')
        if self.get_parameter('dynamic_profit', False):
            profit_range = self.get_parameter('profit_range', Decimal('0.5'))
            base_profit = float(take_profit_pct)
            variation = base_profit * float(profit_range)
            random_offset = random.uniform(-variation, variation)
            take_profit_pct = Decimal(max(0, base_profit + random_offset))
            
            self.logger.log(f"Dynamic profit: base={self.get_parameter('take_profit')}%, "
                          f"actual={take_profit_pct:.4f}% (±{variation:.4f}%)", "DEBUG")
        
        # Calculate close price
        if close_side == 'sell':
            close_price = filled_price * (1 + take_profit_pct/100)
        else:
            close_price = filled_price * (1 - take_profit_pct/100)
        
        return OrderParams(
            side=close_side,
            quantity=filled_quantity,
            price=close_price,
            order_type="limit",
            exchange=self.config.exchange,
            contract_id=self.config.contract_id,
            metadata={
                "strategy": "grid",
                "order_type": "close",
                "direction": close_side,
                "take_profit_pct": float(take_profit_pct)
            }
        )
    
    def record_successful_order(self):
        """Record successful order execution."""
        self.update_strategy_state("last_open_order_time", time.time())
    
    async def get_strategy_status(self) -> Dict[str, Any]:
        """Get current strategy status."""
        active_close_orders = self.get_strategy_state("active_close_orders", [])
        
        try:
            position = await self.get_current_position()
            active_close_amount = sum(
                Decimal(order.get('size', 0)) for order in active_close_orders
            )
            
            return {
                "strategy": "grid",
                "active_orders": len(active_close_orders),
                "position": float(position),
                "active_close_amount": float(active_close_amount),
                "last_order_time": self.get_strategy_state("last_open_order_time", 0),
                "parameters": {
                    "take_profit": self.get_parameter('take_profit'),
                    "grid_step": self.get_parameter('grid_step'),
                    "direction": self.get_parameter('direction'),
                    "max_orders": self.get_parameter('max_orders')
                }
            }
        except Exception as e:
            return {
                "strategy": "grid",
                "error": str(e)
            }
