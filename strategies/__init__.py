"""
Trading Strategies Module
Provides strategy abstraction and implementations for multi-strategy trading.

Architecture:
- Level 1: BaseStrategy (minimal interface)
- Level 2: StatelessStrategy, StatefulStrategy (categories)
- Level 3: GridStrategy, FundingArbitrageStrategy (implementations)
"""

from .base_strategy import BaseStrategy, OrderParams, StrategyAction, RunnableStatus
from .factory import StrategyFactory

# Strategy implementations
from .implementations.grid import GridStrategy, GridConfig
from .implementations.funding_arbitrage import (
    FundingArbitrageStrategy,
    FundingArbConfig,
    FundingArbPosition
)

__all__ = [
    # Core classes
    'BaseStrategy',
    'OrderParams',
    'StrategyAction',
    'RunnableStatus',
    'StrategyFactory',
    
    # Grid strategy
    'GridStrategy',
    'GridConfig',
    
    # Funding arbitrage strategy
    'FundingArbitrageStrategy',
    'FundingArbConfig',
    'FundingArbPosition',
]
