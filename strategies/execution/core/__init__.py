"""
Core execution utilities.

Fundamental components for order execution:
- OrderExecutor: Smart limit/market execution
- LiquidityAnalyzer: Pre-flight depth checks
- PositionSizer: USD↔Quantity conversion
- SlippageCalculator: Slippage tracking
- Spread utilities: calculate_spread_pct, is_spread_acceptable, MAX_*_SPREAD_PCT constants
"""

from strategies.execution.core.order_executor import OrderExecutor
from strategies.execution.core.execution_types import ExecutionMode, ExecutionResult
from strategies.execution.core.liquidity_analyzer import LiquidityAnalyzer, LiquidityReport
from strategies.execution.core.position_sizer import PositionSizer
from strategies.execution.core.slippage_calculator import SlippageCalculator
from strategies.execution.core.execution_strategies import (
    ExecutionStrategy,
    SimpleLimitExecutionStrategy,
    AggressiveLimitExecutionStrategy,
    MarketExecutionStrategy,
)
from strategies.execution.core.spread_utils import (
    calculate_spread_pct,
    is_spread_acceptable,
    MAX_ENTRY_SPREAD_PCT,
    MAX_EXIT_SPREAD_PCT,
    MAX_EMERGENCY_CLOSE_SPREAD_PCT,
)

__all__ = [
    "OrderExecutor",
    "ExecutionMode",
    "ExecutionResult",
    "LiquidityAnalyzer",
    "LiquidityReport",
    "PositionSizer",
    "SlippageCalculator",
    "ExecutionStrategy",
    "SimpleLimitExecutionStrategy",
    "AggressiveLimitExecutionStrategy",
    "MarketExecutionStrategy",
    "calculate_spread_pct",
    "is_spread_acceptable",
    "MAX_ENTRY_SPREAD_PCT",
    "MAX_EXIT_SPREAD_PCT",
    "MAX_EMERGENCY_CLOSE_SPREAD_PCT",
]

