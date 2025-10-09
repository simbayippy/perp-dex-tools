"""
Core execution utilities.

Fundamental components for order execution:
- OrderExecutor: Smart limit/market execution
- LiquidityAnalyzer: Pre-flight depth checks
- PositionSizer: USD↔Quantity conversion
- SlippageCalculator: Slippage tracking
"""

from strategies.execution.core.order_executor import OrderExecutor, ExecutionMode, ExecutionResult
from strategies.execution.core.liquidity_analyzer import LiquidityAnalyzer, LiquidityReport
from strategies.execution.core.position_sizer import PositionSizer
from strategies.execution.core.slippage_calculator import SlippageCalculator

__all__ = [
    "OrderExecutor",
    "ExecutionMode",
    "ExecutionResult",
    "LiquidityAnalyzer",
    "LiquidityReport",
    "PositionSizer",
    "SlippageCalculator",
]

