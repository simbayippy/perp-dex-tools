"""
Trading Strategies Module
Provides strategy abstraction and implementations for multi-strategy trading.

Simplified Architecture:
- BaseStrategy: Minimal abstract interface that all strategies implement
- Concrete Strategies: GridStrategy, FundingArbitrageStrategy, etc.
  - Each strategy inherits from BaseStrategy
  - Each strategy composes what it needs (position managers, state managers, etc.)
  - No forced intermediate layers - simple and flexible

Philosophy: Composition over Inheritance
"""

from .base_strategy import BaseStrategy
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
    'StrategyFactory',
    
    # Grid strategy
    'GridStrategy',
    'GridConfig',
    
    # Funding arbitrage strategy
    'FundingArbitrageStrategy',
    'FundingArbConfig',
    'FundingArbPosition',
]
