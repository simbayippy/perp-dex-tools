"""
Grid Trading Strategy

Single-exchange grid trading implementation.
Inherits directly from BaseStrategy and composes what it needs.
"""

import time
from decimal import Decimal
from typing import Any, Dict, List, Optional

from strategies.base_strategy import BaseStrategy
from exchange_clients.market_data import PriceStream
from .config import GridConfig
from .models import GridCycleState, GridState
from .operations import (
    GridOpenPositionOperator,
    GridOrderCloser,
    GridRecoveryOperator,
)
from .position_manager import GridPositionManager
from .risk_controller import GridRiskController
from helpers.event_notifier import GridEventNotifier


class GridStrategy(BaseStrategy):
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
        exchange_client
    ):
        """
        Initialize grid strategy.
        
        Args:
            config: GridConfig instance with strategy parameters
            exchange_client: Exchange client for trading
        """
        super().__init__(
            config=config,
            exchange_client=exchange_client
        )
        
        # Initialize grid state
        self.grid_state = GridState()
        
        exchange_name = "unknown"
        if hasattr(exchange_client, "get_exchange_name"):
            try:
                exchange_name = exchange_client.get_exchange_name()
            except Exception:
                exchange_name = "unknown"
        
        ticker = "unknown"
        if hasattr(exchange_client, "config"):
            ticker = getattr(exchange_client.config, "ticker", ticker)
        elif hasattr(config, "ticker"):
            ticker = getattr(config, "ticker")
        
        self.event_notifier = GridEventNotifier(
            strategy="grid",
            exchange=str(exchange_name),
            ticker=str(ticker or "unknown"),
        )

        self.order_notional_usd: Optional[Decimal] = getattr(config, "order_notional_usd", None)
        target_leverage: Optional[Decimal] = getattr(config, "target_leverage", None)

        stream_symbol = str(getattr(config, "ticker", getattr(exchange_client.config, "ticker", "")))
        fetch_symbol = getattr(exchange_client.config, "contract_id", stream_symbol)
        self.price_stream = PriceStream(
            exchange_client=exchange_client,
            stream_symbol=stream_symbol,
            fetch_symbol=fetch_symbol,
        )

        # Compose helper components
        self.position_manager = GridPositionManager(self.grid_state)
        self.risk_controller = GridRiskController(
            config=config,
            exchange_client=exchange_client,
            grid_state=self.grid_state,
            logger=self.logger,
            log_event=self._log_event,
            requested_leverage=target_leverage,
        )
        self.order_closer = GridOrderCloser(
            config=config,
            exchange_client=exchange_client,
            grid_state=self.grid_state,
            logger=self.logger,
            log_event=self._log_event,
            position_manager=self.position_manager,
        )
        self.open_operator = GridOpenPositionOperator(
            config=config,
            exchange_client=exchange_client,
            grid_state=self.grid_state,
            logger=self.logger,
            risk_controller=self.risk_controller,
            price_stream=self.price_stream,
            order_notional_usd=self.order_notional_usd,
        )
        self.recovery_operator = GridRecoveryOperator(
            config=config,
            exchange_client=exchange_client,
            grid_state=self.grid_state,
            logger=self.logger,
            log_event=self._log_event,
            position_manager=self.position_manager,
            order_closer=self.order_closer,
        )
        
        self.logger.log("Grid strategy initialized with parameters:", "INFO")
        self.logger.log(f"  - Take Profit: {config.take_profit}%", "INFO")
        self.logger.log(f"  - Grid Step: {config.grid_step}%", "INFO")
        self.logger.log(f"  - Direction: {config.direction}", "INFO")
        if self.order_notional_usd is not None:
            self.logger.log(f"  - Order Notional (USD): {self.order_notional_usd}", "INFO")
        else:
            qty_display = getattr(self.exchange_client.config, "quantity", None)
            self.logger.log(f"  - Order Quantity (base): {qty_display}", "INFO")
        self.logger.log(f"  - Max Orders: {config.max_orders}", "INFO")
        self.logger.log(f"  - Wait Time: {config.wait_time}s", "INFO")
        self.logger.log(f"  - Max Margin (USD): {config.max_margin_usd}", "INFO")
        self.logger.log(
            f"  - Stop Loss: {'enabled' if config.stop_loss_enabled else 'disabled'} "
            f"(threshold {config.stop_loss_percentage}%)",
            "INFO",
        )
        if target_leverage is not None:
            self.logger.log(f"  - Target Leverage: {target_leverage}x", "INFO")
        self.logger.log(
            f"  - Position Timeout: {config.position_timeout_minutes} minutes",
            "INFO",
        )
        self.logger.log(f"  - Recovery Mode: {config.recovery_mode}", "INFO")
        
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
    
    def _serialize_value(self, value: Any) -> Any:
        """Serialize payload values for structured logging."""
        if isinstance(value, Decimal):
            return float(value)
        if isinstance(value, (list, tuple)):
            return [self._serialize_value(item) for item in value]
        if isinstance(value, dict):
            return {key: self._serialize_value(val) for key, val in value.items()}
        return value
    
    def _log_event(self, event_type: str, message: str, level: str = "INFO", **context: Any) -> None:
        """Emit structured grid strategy event."""
        payload = {
            "event_type": event_type,
            "grid_direction": self.config.direction,
            **context,
        }
        serialized_payload = {key: self._serialize_value(val) for key, val in payload.items()}
        self.logger.log(message, level.upper(), **serialized_payload)

        if self.event_notifier:
            self.event_notifier.notify(
                event_type=event_type,
                level=level.upper(),
                message=message,
                payload=serialized_payload,
            )
    
    async def _initialize_strategy(self):
        """Initialize strategy (called by base class)."""
        await self.risk_controller.prepare_leverage_settings()
        stream_symbol = getattr(self.config, "ticker", None)
        if stream_symbol:
            try:
                await self.exchange_client.ensure_market_feed(stream_symbol)
            except Exception as exc:
                self.logger.log(
                    f"Grid: Failed to align websocket market feed for {stream_symbol}: {exc}",
                    "WARNING",
                )
    
    async def should_execute(self) -> bool:
        """Determine if grid strategy should execute."""
        try:
            # Get current market data
            bbo = await self.price_stream.latest()
            best_bid = bbo.bid
            best_ask = bbo.ask
            current_price = (best_bid + best_ask) / Decimal("2")
            
            # Check stop price - critical safety check first
            if self.config.stop_price is not None:
                if (self.config.direction == 'buy' and current_price < self.config.stop_price) or \
                   (self.config.direction == 'sell' and current_price > self.config.stop_price):
                    message = (
                        f"⚠️ STOP PRICE TRIGGERED! Current: {current_price}, Stop: {self.config.stop_price}"
                    )
                    self._log_event(
                        "stop_price_triggered",
                        message,
                        level="WARNING",
                        current_price=current_price,
                        stop_price=self.config.stop_price,
                    )
                    self._log_event(
                        "stop_price_shutdown",
                        "Canceling all orders and stopping strategy...",
                        level="WARNING",
                    )
                    await self.order_closer.cancel_all_orders()
                    return False
            
            # Check pause price - temporary pause
            if self.config.pause_price is not None:
                if (self.config.direction == 'buy' and current_price > self.config.pause_price) or \
                   (self.config.direction == 'sell' and current_price < self.config.pause_price):
                    self._log_event(
                        "pause_price_triggered",
                        f"⏸️ PAUSE PRICE REACHED! Current: {current_price}, Pause: {self.config.pause_price}",
                        level="INFO",
                        current_price=current_price,
                        pause_price=self.config.pause_price,
                    )
                    return False
            
            current_position, snapshot = await self.risk_controller.refresh_risk_snapshot(current_price)
            if await self.risk_controller.enforce_stop_loss(
                snapshot,
                current_price,
                current_position,
                self.order_closer.market_close,
            ):
                return False
            
            # Update active orders
            await self.order_closer.update_active_orders()
            await self.recovery_operator.run_recovery_checks(current_price)
            
            # Check if we should wait based on cooldown
            wait_time = self._calculate_wait_time()
            if wait_time > 0:
                return False
            
            # Check grid step condition
            return await self._meet_grid_step_condition(best_bid, best_ask)
            
        except Exception as e:
            self._log_event(
                "should_execute_error",
                f"Error in should_execute: {e}",
                level="ERROR",
                error=str(e),
            )
            return False
    
    async def execute_strategy(self, market_data=None) -> Dict[str, Any]:
        """
        Execute grid strategy using state machine pattern.
        
        Grid cycle states:
        1. 'READY' → Place open order
        2. 'WAITING_FOR_FILL' → Check if filled, then place close order  
        3. 'COMPLETE' → Reset state
        
        Args:
            market_data: Optional market data (not used, grid fetches its own)
        
        Returns:
            Dictionary with execution result
        """
        try:
            # State 1: Ready to place open order
            if self.grid_state.cycle_state == GridCycleState.READY:
                return await self.open_operator.place_open_order()
            
            # State 2: Waiting for fill, then place close order
            elif self.grid_state.cycle_state == GridCycleState.WAITING_FOR_FILL:
                result = await self.order_closer.handle_filled_order()
                if result.get("action") == "wait":
                    if await self._recover_from_canceled_entry():
                        return {
                            "action": "wait",
                            "message": "Grid: Entry order canceled; retrying",
                            "wait_time": 0,
                        }
                return result
            
            else:
                # Unknown state, reset
                self.grid_state.cycle_state = GridCycleState.READY
                return {
                    'action': 'wait',
                    'message': 'Grid: Resetting cycle state',
                    'wait_time': 1
                }
                
        except Exception as e:
            self._log_event(
                "execute_strategy_error",
                f"Error executing grid strategy: {e}",
                level="ERROR",
                error=str(e),
            )
            # Reset state on error
            self.grid_state.cycle_state = GridCycleState.READY
            return {
                'action': 'error',
                'message': f'Grid strategy error: {e}',
                'wait_time': 5
            }

    def notify_order_filled(self, filled_price: Decimal, filled_quantity: Decimal) -> None:
        """
        Notify strategy that an order was filled.

        This is called by the trading bot after successful order execution.
        """
        self.order_closer.notify_order_filled(filled_price, filled_quantity)

    async def _recover_from_canceled_entry(self) -> bool:
        """Reset cycle if the pending entry order is no longer active."""
        pending_id = self.grid_state.pending_open_order_id
        if not pending_id:
            return False

        contract_id = getattr(self.exchange_client.config, "contract_id", None)
        if not contract_id:
            return False

        try:
            active_orders = await self.exchange_client.get_active_orders(contract_id)
        except Exception as exc:  # pragma: no cover - defensive logging
            self.logger.log(
                f"Grid: Failed to fetch active orders while checking for canceled entry: {exc}",
                "DEBUG",
            )
            return False

        pending_str = str(pending_id)
        for order in active_orders:
            order_id = getattr(order, "order_id", None)
            if order_id is not None and str(order_id) == pending_str:
                return False

        self._log_event(
            "entry_order_canceled",
            "Grid: Entry order canceled before fill; scheduling retry",
            level="INFO",
            order_id=pending_id,
        )
        self.grid_state.pending_open_order_id = None
        self.grid_state.pending_open_quantity = None
        self.grid_state.filled_price = None
        self.grid_state.filled_quantity = None
        self.grid_state.filled_position_id = None
        self.grid_state.pending_position_id = None
        self.grid_state.cycle_state = GridCycleState.READY
        self.grid_state.last_open_order_time = time.time()
        return True

    def _calculate_wait_time(self) -> float:
        """Calculate wait time between orders based on order density."""
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
                take_profit_multiplier = self.order_closer.get_take_profit_multiplier()
                new_order_close_price = best_ask * take_profit_multiplier
                
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
                take_profit_multiplier = self.order_closer.get_take_profit_multiplier()
                new_order_close_price = best_bid * take_profit_multiplier
                
                # Check if there's enough gap
                return new_order_close_price / next_close_price > 1 + self.config.grid_step / 100
                
        except Exception as e:
            self.logger.log(f"Error in grid step condition: {e}", "ERROR")
            return False
    
    
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
                "margin_used": float(self.grid_state.last_known_margin),
                "margin_limit": float(self.config.max_margin_usd),
                "margin_ratio": float(self.grid_state.margin_ratio) if self.grid_state.margin_ratio is not None else None,
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
    
    def get_strategy_name(self) -> str:
        """Get the strategy name."""
        return "Grid Trading"
    
    def get_required_parameters(self) -> List[str]:
        """Get list of required strategy parameters."""
        return [
            "take_profit",
            "grid_step",
            "direction",
            "max_orders",
            "wait_time"
        ]
