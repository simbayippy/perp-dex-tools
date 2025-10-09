"""
Base interface for risk management / rebalancing sub-strategies.

⭐ Pluggable Pattern ⭐
Different risk management strategies can be swapped easily via factory.

Use Case:
- Profit erosion based exit
- Divergence flip protection
- Time-based exit
- Combined multi-rule system
"""

from abc import ABC, abstractmethod
from typing import Tuple, List, Dict, Any
from decimal import Decimal
from ..models import FundingArbPosition, RebalanceAction


class BaseRiskManager(ABC):
    """
    Interface for risk management decision logic.
    
    Child classes implement different exit/rebalancing approaches:
    - Profit erosion based (exit when edge erodes)
    - Divergence flip based (exit when rates flip)
    - Better opportunity based (swap to better trade)
    - Time based (max holding period)
    - Combined (composition of multiple rules)
    
    ⭐ Pattern from Hummingbot's executor framework ⭐
    Each risk manager is a self-contained decision unit.
    """
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize risk manager with configuration.
        
        Args:
            config: Risk management config dict with strategy-specific params
        """
        self.config = config
    
    @abstractmethod
    def should_exit(
        self,
        position: FundingArbPosition,
        current_rates: Dict[str, Decimal]
    ) -> Tuple[bool, str]:
        """
        Determine if position should be exited/rebalanced.
        
        This is the core decision method that each risk manager implements.
        
        Args:
            position: Current position being evaluated
            current_rates: Latest funding rates from service
                {
                    'divergence': Decimal,      # Current rate spread
                    'long_rate': Decimal,       # Long side funding rate
                    'short_rate': Decimal,      # Short side funding rate
                    'long_oi_usd': Decimal,     # Long side open interest
                    'short_oi_usd': Decimal     # Short side open interest
                }
        
        Returns:
            Tuple of:
                - bool: True if position should be exited
                - str: Reason code (e.g. 'PROFIT_EROSION', 'DIVERGENCE_FLIPPED')
        
        Example:
            >>> should_exit, reason = risk_mgr.should_exit(position, rates)
            >>> if should_exit:
            >>>     actions = risk_mgr.generate_actions(position, reason)
        """
        pass
    
    @abstractmethod
    def generate_actions(
        self,
        position: FundingArbPosition,
        reason: str
    ) -> List[RebalanceAction]:
        """
        Generate list of actions to execute for this exit.
        
        Most common action is 'close_position', but could also include:
        - 'transfer_funds' (move capital to different DEX)
        - 'open_position' (immediately reopen better opportunity)
        
        Args:
            position: Position being exited
            reason: Reason code from should_exit()
        
        Returns:
            List of RebalanceAction objects to execute
        
        Example:
            >>> actions = risk_mgr.generate_actions(position, "PROFIT_EROSION")
            >>> for action in actions:
            >>>     await execute_action(action)
        """
        pass
    
    def get_position_metrics(
        self,
        position: FundingArbPosition,
        current_rates: Dict[str, Decimal]
    ) -> Dict[str, Any]:
        """
        Helper method to calculate common position metrics.
        
        Useful for logging and decision making.
        
        Args:
            position: Current position
            current_rates: Latest rates
        
        Returns:
            Dict with calculated metrics:
                - profit_erosion: How much edge has eroded (0-1)
                - age_hours: How long position has been open
                - estimated_pnl_usd: Estimated profit/loss so far
        """
        from datetime import datetime
        
        # Profit erosion (how much edge has degraded)
        if position.entry_divergence > 0:
            erosion = float(current_rates['divergence'] / position.entry_divergence)
        else:
            erosion = 0.0
        
        # Age
        age_hours = (datetime.now() - position.opened_at).total_seconds() / 3600
        
        # Estimated PnL (simplified - actual PnL includes fees, funding payments, etc.)
        avg_divergence = (position.entry_divergence + current_rates['divergence']) / 2
        # APY to per-hour rate (assuming 8h funding interval)
        estimated_pnl = float(avg_divergence * position.size_usd * age_hours / 24)
        
        return {
            'profit_erosion': erosion,
            'age_hours': age_hours,
            'estimated_pnl_usd': estimated_pnl,
            'current_divergence_pct': float(current_rates['divergence'] * 100),
            'entry_divergence_pct': float(position.entry_divergence * 100)
        }

