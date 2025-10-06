"""
Risk Management Module for Trading Bot
Provides grid-specific stop-loss and margin monitoring capabilities.
"""

import time
import asyncio
from decimal import Decimal
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass
from enum import Enum

from helpers.logger import TradingLogger


class RiskAction(Enum):
    """Risk management actions."""
    NONE = "none"
    CLOSE_WORST_POSITIONS = "close_worst_positions"
    EMERGENCY_CLOSE_ALL = "emergency_close_all"
    PAUSE_TRADING = "pause_trading"


@dataclass
class RiskThresholds:
    """Risk management thresholds configuration."""
    # Margin failure monitoring
    margin_failure_threshold: int = 15  # consecutive margin failures
    
    # Time-based monitoring
    time_stall_threshold: int = 600  # 10 minutes in seconds
    
    # P&L monitoring
    account_loss_threshold: Decimal = Decimal('0.10')  # -10% of account
    
    # Position closure settings
    position_closure_percent: Decimal = Decimal('0.20')  # Close worst 20% of positions
    
    # Emergency thresholds (more aggressive)
    emergency_loss_threshold: Decimal = Decimal('0.15')  # -15% emergency close all


@dataclass
class RiskState:
    """Current risk monitoring state."""
    consecutive_margin_failures: int = 0
    last_successful_order_time: float = 0
    initial_account_value: Optional[Decimal] = None
    margin_failure_start_time: Optional[float] = None
    
    def reset_margin_failures(self):
        """Reset margin failure tracking."""
        self.consecutive_margin_failures = 0
        self.margin_failure_start_time = None
    
    def record_successful_order(self):
        """Record a successful order."""
        self.last_successful_order_time = time.time()
        self.reset_margin_failures()


class RiskManager:
    """Grid-specific risk management system."""
    
    def __init__(self, exchange_client, config, thresholds: Optional[RiskThresholds] = None):
        """Initialize risk manager."""
        self.exchange_client = exchange_client
        self.config = config
        self.thresholds = thresholds or RiskThresholds()
        self.state = RiskState()
        
        # Only enable if exchange supports risk management
        self.enabled = exchange_client.supports_risk_management()
        
        # Initialize logger
        self.logger = TradingLogger(
            exchange=config.exchange, 
            ticker=config.ticker, 
            log_to_console=False
        )
        
        if self.enabled:
            self.logger.log("Risk management enabled with thresholds:", "INFO")
            self.logger.log(f"  - Margin failures: {self.thresholds.margin_failure_threshold}", "INFO")
            self.logger.log(f"  - Time stall: {self.thresholds.time_stall_threshold}s", "INFO")
            self.logger.log(f"  - Account loss: {self.thresholds.account_loss_threshold * 100}%", "INFO")
            self.logger.log(f"  - Position closure: {self.thresholds.position_closure_percent * 100}%", "INFO")
        else:
            self.logger.log("Risk management disabled (exchange not supported)", "INFO")
    
    async def initialize(self):
        """Initialize risk manager with baseline data."""
        if not self.enabled:
            return
            
        try:
            # Get initial account value for baseline
            initial_value = await self.exchange_client.get_total_asset_value()
            if initial_value:
                self.state.initial_account_value = initial_value
                self.logger.log(f"Initial account value: ${initial_value}", "INFO")
            
            # Set initial successful order time
            self.state.last_successful_order_time = time.time()
            
        except Exception as e:
            self.logger.log(f"Error initializing risk manager: {e}", "ERROR")
    
    async def check_risk_conditions(self) -> RiskAction:
        """Check all risk conditions and return required action."""
        if not self.enabled:
            return RiskAction.NONE
            
        try:
            # Check emergency conditions first
            emergency_action = await self._check_emergency_conditions()
            if emergency_action != RiskAction.NONE:
                return emergency_action
            
            # Check standard risk conditions
            return await self._check_standard_conditions()
            
        except Exception as e:
            self.logger.log(f"Error checking risk conditions: {e}", "ERROR")
            return RiskAction.NONE
    
    async def _check_emergency_conditions(self) -> RiskAction:
        """Check emergency conditions that require immediate action."""
        if not self.state.initial_account_value:
            return RiskAction.NONE
            
        # Get current account value
        current_value = await self.exchange_client.get_total_asset_value()
        if not current_value:
            return RiskAction.NONE
        
        # Calculate account loss percentage
        loss_percent = (self.state.initial_account_value - current_value) / self.state.initial_account_value
        
        if loss_percent >= self.thresholds.emergency_loss_threshold:
            self.logger.log(
                f"EMERGENCY: Account loss {loss_percent * 100:.2f}% >= {self.thresholds.emergency_loss_threshold * 100}%",
                "ERROR"
            )
            return RiskAction.EMERGENCY_CLOSE_ALL
        
        return RiskAction.NONE
    
    async def _check_standard_conditions(self) -> RiskAction:
        """Check standard risk conditions."""
        current_time = time.time()
        
        # Check margin failure + time stall combination
        if (self.state.consecutive_margin_failures >= self.thresholds.margin_failure_threshold and
            self.state.margin_failure_start_time and
            current_time - self.state.margin_failure_start_time >= self.thresholds.time_stall_threshold):
            
            self.logger.log(
                f"Risk threshold met: {self.state.consecutive_margin_failures} margin failures "
                f"+ {current_time - self.state.margin_failure_start_time:.0f}s stall",
                "WARNING"
            )
            
            # Check account loss threshold
            if await self._check_account_loss_threshold():
                return RiskAction.CLOSE_WORST_POSITIONS
        
        return RiskAction.NONE
    
    async def _check_account_loss_threshold(self) -> bool:
        """Check if account loss threshold is exceeded."""
        if not self.state.initial_account_value:
            return False
            
        current_value = await self.exchange_client.get_total_asset_value()
        if not current_value:
            return False
        
        loss_percent = (self.state.initial_account_value - current_value) / self.state.initial_account_value
        
        if loss_percent >= self.thresholds.account_loss_threshold:
            self.logger.log(
                f"Account loss threshold exceeded: {loss_percent * 100:.2f}% >= {self.thresholds.account_loss_threshold * 100}%",
                "WARNING"
            )
            return True
        
        return False
    
    def record_margin_failure(self):
        """Record a margin failure event."""
        if not self.enabled:
            return
            
        self.state.consecutive_margin_failures += 1
        
        # Start timing if this is the first failure
        if self.state.margin_failure_start_time is None:
            self.state.margin_failure_start_time = time.time()
        
        self.logger.log(
            f"Margin failure #{self.state.consecutive_margin_failures} "
            f"(threshold: {self.thresholds.margin_failure_threshold})",
            "WARNING"
        )
    
    def record_successful_order(self):
        """Record a successful order."""
        if not self.enabled:
            return
            
        self.state.record_successful_order()
        self.logger.log("Successful order recorded, risk counters reset", "INFO")
    
    async def get_worst_positions(self) -> List[Dict[str, Any]]:
        """Get worst performing positions for closure."""
        if not self.enabled:
            return []
            
        try:
            positions = await self.exchange_client.get_detailed_positions()
            if not positions:
                return []
            
            # Filter to positions with negative P&L
            losing_positions = [pos for pos in positions if pos['unrealized_pnl'] < 0]
            
            if not losing_positions:
                return []
            
            # Sort by P&L percentage (worst first)
            losing_positions.sort(key=lambda p: p['unrealized_pnl'] / abs(p['position_value']) if p['position_value'] != 0 else 0)
            
            # Return worst N% of positions
            num_to_close = max(1, int(len(losing_positions) * self.thresholds.position_closure_percent))
            worst_positions = losing_positions[:num_to_close]
            
            self.logger.log(
                f"Identified {len(worst_positions)} worst positions for closure "
                f"(out of {len(losing_positions)} losing positions)",
                "INFO"
            )
            
            return worst_positions
            
        except Exception as e:
            self.logger.log(f"Error getting worst positions: {e}", "ERROR")
            return []
    
    async def get_all_positions(self) -> List[Dict[str, Any]]:
        """Get all positions for emergency closure."""
        if not self.enabled:
            return []
            
        try:
            positions = await self.exchange_client.get_detailed_positions()
            # Filter to positions with actual size
            active_positions = [pos for pos in positions if abs(pos['position']) > 0]
            
            self.logger.log(f"Found {len(active_positions)} active positions for emergency closure", "WARNING")
            return active_positions
            
        except Exception as e:
            self.logger.log(f"Error getting all positions: {e}", "ERROR")
            return []
    
    async def get_risk_summary(self) -> Dict[str, Any]:
        """Get current risk status summary."""
        if not self.enabled:
            return {"enabled": False}
        
        try:
            current_value = await self.exchange_client.get_total_asset_value()
            account_pnl = await self.exchange_client.get_account_pnl()
            positions = await self.exchange_client.get_detailed_positions()
            
            summary = {
                "enabled": True,
                "consecutive_margin_failures": self.state.consecutive_margin_failures,
                "time_since_last_success": time.time() - self.state.last_successful_order_time if self.state.last_successful_order_time else 0,
                "current_account_value": float(current_value) if current_value else None,
                "initial_account_value": float(self.state.initial_account_value) if self.state.initial_account_value else None,
                "account_pnl": float(account_pnl) if account_pnl else None,
                "active_positions": len([p for p in positions if abs(p['position']) > 0]),
                "losing_positions": len([p for p in positions if p['unrealized_pnl'] < 0]),
            }
            
            if self.state.initial_account_value and current_value:
                loss_percent = (self.state.initial_account_value - current_value) / self.state.initial_account_value
                summary["account_loss_percent"] = float(loss_percent * 100)
            
            return summary
            
        except Exception as e:
            self.logger.log(f"Error getting risk summary: {e}", "ERROR")
            return {"enabled": True, "error": str(e)}
