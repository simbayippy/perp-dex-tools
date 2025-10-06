"""
Trading Strategies Module
Provides strategy abstraction and implementations for multi-strategy trading.
"""

from .base_strategy import BaseStrategy, OrderParams, StrategyResult
from .factory import StrategyFactory

__all__ = ['BaseStrategy', 'OrderParams', 'StrategyResult', 'StrategyFactory']
