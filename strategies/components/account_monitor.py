"""
Account Health Monitoring Component

Monitors exchange account health and triggers actions when thresholds are breached.

Key Features:
- Account balance/value tracking
- Margin failure detection
- Emergency position closure triggers
- Time-based stall detection

Note: This is separate from strategy-level risk management (e.g. funding_arbitrage/risk_management)
      which handles position-specific exit logic.

Usage:
    from strategies.components.account_monitor import AccountMonitor, AccountAction, AccountThresholds
    
    monitor = AccountMonitor(exchange_client, config)
    await monitor.initialize()
    
    action = await monitor.check_account_conditions()
    if action == AccountAction.EMERGENCY_CLOSE_ALL:
        # Handle emergency shutdown
"""

import time
import asyncio
from decimal import Decimal
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass
from enum import Enum

from helpers.unified_logger import get_core_logger


class AccountAction(Enum):
    """Account monitoring actions."""
    NONE = "none"
    CLOSE_WORST_POSITIONS = "close_worst_positions"
    EMERGENCY_CLOSE_ALL = "emergency_close_all"
    PAUSE_TRADING = "pause_trading"


@dataclass
class AccountThresholds:
    """Account monitoring thresholds configuration."""
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
class AccountState:
    """Current account monitoring state."""
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


class AccountMonitor:
    """
    Exchange account health monitoring system.
    
    Monitors account-level conditions and triggers protective actions:
    - Balance/value tracking
    - Margin failure detection
    - Emergency shutdown triggers
    - Time-based stall detection
    
    Requirements:
        Exchange client should implement (optional, gracefully degrades if missing):
        - get_total_asset_value() -> Optional[Decimal]
        - get_account_pnl() -> Optional[Decimal]
        - get_detailed_positions() -> List[Dict[str, Any]]
    
    Note:
        This handles ACCOUNT-LEVEL risk (system health).
        For STRATEGY-LEVEL risk (position exits), see strategy-specific risk managers
        (e.g. funding_arbitrage/risk_management).
    """
    
    def __init__(self, exchange_client, config, thresholds: Optional[AccountThresholds] = None):
        """
        Initialize account monitor.
        
        Args:
            exchange_client: Exchange client instance
            config: Trading config with exchange/ticker info
            thresholds: Optional custom thresholds
        """
        self.exchange_client = exchange_client
        self.config = config
        self.thresholds = thresholds or AccountThresholds()
        self.state = AccountState()
        
        # Check if exchange supports monitoring methods (duck typing)
        self.enabled = self._check_exchange_support()
        
        # Initialize logger
        self.logger = get_core_logger(
            "account_monitor", 
            context={"exchange": config.exchange, "ticker": config.ticker}
        )
        
        if self.enabled:
            self.logger.info(
                f"Account monitoring enabled with thresholds: "
                f"Margin failures: {self.thresholds.margin_failure_threshold}, "
                f"Time stall: {self.thresholds.time_stall_threshold}s, "
                f"Account loss: {self.thresholds.account_loss_threshold * 100}%, "
                f"Position closure: {self.thresholds.position_closure_percent * 100}%"
            )
        else:
            self.logger.info("Account monitoring disabled (exchange methods not available)")
    
    def _check_exchange_support(self) -> bool:
        """
        Check if exchange supports required monitoring methods.
        
        Uses duck typing - checks if methods exist and are callable.
        
        Returns:
            True if exchange supports monitoring, False otherwise
        """
        required_methods = ['get_total_asset_value', 'get_detailed_positions']
        
        for method_name in required_methods:
            if not hasattr(self.exchange_client, method_name):
                self.logger.debug(f"Exchange missing method: {method_name}")
                return False
            
            method = getattr(self.exchange_client, method_name)
            if not callable(method):
                self.logger.debug(f"Exchange method not callable: {method_name}")
                return False
        
        return True
    
    async def initialize(self):
        """Initialize account monitor with baseline data."""
        if not self.enabled:
            return
            
        try:
            # Get initial account value for baseline
            initial_value = await self.exchange_client.get_total_asset_value()
            if initial_value:
                self.state.initial_account_value = initial_value
                self.logger.info(f"Initial account value: ${initial_value}")
            
            # Set initial successful order time
            self.state.last_successful_order_time = time.time()
            
        except Exception as e:
            self.logger.error(f"Error initializing account monitor: {e}")
    
    async def check_account_conditions(self) -> AccountAction:
        """
        Check all account conditions and return required action.
        
        Returns:
            AccountAction enum indicating what action to take
        """
        if not self.enabled:
            return AccountAction.NONE
            
        try:
            # Check emergency conditions first
            emergency_action = await self._check_emergency_conditions()
            if emergency_action != AccountAction.NONE:
                return emergency_action
            
            # Check standard conditions
            return await self._check_standard_conditions()
            
        except Exception as e:
            self.logger.error(f"Error checking account conditions: {e}")
            return AccountAction.NONE
    
    async def _check_emergency_conditions(self) -> AccountAction:
        """Check emergency conditions that require immediate action."""
        if not self.state.initial_account_value:
            return AccountAction.NONE
            
        # Get current account value
        current_value = await self.exchange_client.get_total_asset_value()
        if not current_value:
            return AccountAction.NONE
        
        # Calculate account loss percentage
        loss_percent = (self.state.initial_account_value - current_value) / self.state.initial_account_value
        
        if loss_percent >= self.thresholds.emergency_loss_threshold:
            self.logger.error(
                f"EMERGENCY: Account loss {loss_percent * 100:.2f}% >= {self.thresholds.emergency_loss_threshold * 100}%"
            )
            return AccountAction.EMERGENCY_CLOSE_ALL
        
        return AccountAction.NONE
    
    async def _check_standard_conditions(self) -> AccountAction:
        """Check standard account conditions."""
        current_time = time.time()
        
        # Check margin failure + time stall combination
        if (self.state.consecutive_margin_failures >= self.thresholds.margin_failure_threshold and
            self.state.margin_failure_start_time and
            current_time - self.state.margin_failure_start_time >= self.thresholds.time_stall_threshold):
            
            self.logger.warning(
                f"Account threshold met: {self.state.consecutive_margin_failures} margin failures "
                f"+ {current_time - self.state.margin_failure_start_time:.0f}s stall"
            )
            
            # Check account loss threshold
            if await self._check_account_loss_threshold():
                return AccountAction.CLOSE_WORST_POSITIONS
        
        return AccountAction.NONE
    
    async def _check_account_loss_threshold(self) -> bool:
        """Check if account loss threshold is exceeded."""
        if not self.state.initial_account_value:
            return False
            
        current_value = await self.exchange_client.get_total_asset_value()
        if not current_value:
            return False
        
        loss_percent = (self.state.initial_account_value - current_value) / self.state.initial_account_value
        
        if loss_percent >= self.thresholds.account_loss_threshold:
            self.logger.warning(
                f"Account loss threshold exceeded: {loss_percent * 100:.2f}% >= {self.thresholds.account_loss_threshold * 100}%"
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
        
        self.logger.warning(
            f"Margin failure #{self.state.consecutive_margin_failures} "
            f"(threshold: {self.thresholds.margin_failure_threshold})"
        )
    
    def record_successful_order(self):
        """Record a successful order."""
        if not self.enabled:
            return
            
        self.state.record_successful_order()
        self.logger.info("Successful order recorded, account counters reset")
    
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
            
            self.logger.info(
                f"Identified {len(worst_positions)} worst positions for closure "
                f"(out of {len(losing_positions)} losing positions)"
            )
            
            return worst_positions
            
        except Exception as e:
            self.logger.error(f"Error getting worst positions: {e}")
            return []
    
    async def get_all_positions(self) -> List[Dict[str, Any]]:
        """Get all positions for emergency closure."""
        if not self.enabled:
            return []
            
        try:
            positions = await self.exchange_client.get_detailed_positions()
            # Filter to positions with actual size
            active_positions = [pos for pos in positions if abs(pos['position']) > 0]
            
            self.logger.warning(f"Found {len(active_positions)} active positions for emergency closure")
            return active_positions
            
        except Exception as e:
            self.logger.error(f"Error getting all positions: {e}")
            return []
    
    async def get_account_summary(self) -> Dict[str, Any]:
        """Get current account status summary."""
        if not self.enabled:
            return {"enabled": False}
        
        try:
            current_value = await self.exchange_client.get_total_asset_value()
            account_pnl = None
            if hasattr(self.exchange_client, 'get_account_pnl'):
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
            self.logger.error(f"Error getting account summary: {e}")
            return {"enabled": True, "error": str(e)}

