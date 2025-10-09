"""
Risk Management System for Funding Arbitrage.

Factory pattern for creating risk managers.

Available Strategies:
- 'profit_erosion': Exit based on profit erosion threshold
- 'divergence_flip': Exit when divergence goes negative
- 'combined': Multi-rule system with priorities (RECOMMENDED)

Usage:
    >>> from strategies.implementations.funding_arbitrage.risk_management import get_risk_manager
    >>> 
    >>> config = {
    >>>     'min_erosion_ratio': 0.5,
    >>>     'max_position_age_hours': 168
    >>> }
    >>> 
    >>> risk_mgr = get_risk_manager('combined', config)
    >>> 
    >>> # In strategy execute loop:
    >>> should_exit, reason = risk_mgr.should_exit(position, current_rates)
    >>> if should_exit:
    >>>     actions = risk_mgr.generate_actions(position, reason)
    >>>     await execute_actions(actions)
"""

from typing import Dict, Any
from .base import BaseRiskManager
from .profit_erosion import ProfitErosionRiskManager
from .divergence_flip import DivergenceFlipRiskManager
from .combined import CombinedRiskManager


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Factory Function
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def get_risk_manager(
    strategy_name: str,
    config: Dict[str, Any]
) -> BaseRiskManager:
    """
    Factory function for creating risk management strategies.
    
    ⭐ Recommended: Use 'combined' for production ⭐
    
    Args:
        strategy_name: Name of risk management strategy
            - 'profit_erosion': Simple erosion-based exit
            - 'divergence_flip': Flip protection only
            - 'combined': Multi-layered protection (RECOMMENDED)
        
        config: Configuration dict with strategy-specific params
            Common params:
                - min_erosion_ratio: float (0-1)
                - severe_erosion_ratio: float (0-1)
                - max_position_age_hours: float
                - flip_margin: Decimal
    
    Returns:
        BaseRiskManager instance configured with params
    
    Raises:
        ValueError: If strategy_name is unknown
    
    Example:
        >>> # Production config
        >>> config = {
        >>>     'min_erosion_ratio': 0.5,        # Exit at 50% erosion
        >>>     'severe_erosion_ratio': 0.2,     # Exit at 80% erosion
        >>>     'max_position_age_hours': 168,   # 1 week max
        >>>     'flip_margin': 0                 # No margin
        >>> }
        >>> 
        >>> risk_mgr = get_risk_manager('combined', config)
        >>> 
        >>> # Check exit condition
        >>> current_rates = {
        >>>     'divergence': Decimal('0.08'),  # 8% APY
        >>>     'long_rate': Decimal('-0.05'),  # Paying 5%
        >>>     'short_rate': Decimal('0.13')   # Receiving 13%
        >>> }
        >>> 
        >>> should_exit, reason = risk_mgr.should_exit(position, current_rates)
        >>> if should_exit:
        >>>     actions = risk_mgr.generate_actions(position, reason)
        >>>     for action in actions:
        >>>         print(f"Action: {action.action_type}, Reason: {action.reason}")
    """
    
    # Registry of available strategies
    STRATEGIES = {
        'profit_erosion': ProfitErosionRiskManager,
        'divergence_flip': DivergenceFlipRiskManager,
        'combined': CombinedRiskManager,
    }
    
    # Validate strategy name
    if strategy_name not in STRATEGIES:
        available = ', '.join(STRATEGIES.keys())
        raise ValueError(
            f"Unknown risk management strategy: '{strategy_name}'. "
            f"Available strategies: {available}"
        )
    
    # Instantiate and return
    strategy_class = STRATEGIES[strategy_name]
    return strategy_class(config)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Exports
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

__all__ = [
    # Factory
    'get_risk_manager',
    
    # Base interface
    'BaseRiskManager',
    
    # Concrete strategies
    'ProfitErosionRiskManager',
    'DivergenceFlipRiskManager',
    'CombinedRiskManager',
]

