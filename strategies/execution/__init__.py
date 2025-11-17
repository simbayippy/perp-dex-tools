"""
Shared Execution Layer.

Provides reusable execution utilities for all strategies:
- Smart order placement (limit/market fallback)
- Liquidity analysis & pre-flight checks
- Atomic multi-order execution (delta-neutral)
- Slippage tracking & position sizing

⭐ Inspired by Hummingbot's battle-tested execution patterns ⭐

Usage:
    from strategies.execution.core import OrderExecutor, LiquidityAnalyzer
    from strategies.execution.patterns import AtomicMultiOrderExecutor, OrderSpec
"""

# Core utilities
from strategies.execution.core.order_executor import OrderExecutor
from strategies.execution.core.execution_types import ExecutionMode, ExecutionResult
from strategies.execution.core.liquidity_analyzer import LiquidityAnalyzer, LiquidityReport
from strategies.execution.core.position_sizer import PositionSizer
from strategies.execution.core.slippage_calculator import SlippageCalculator

# Advanced patterns
from strategies.execution.patterns.atomic_multi_order import (
    AtomicMultiOrderExecutor,
    OrderSpec,
    AtomicExecutionResult
)

# Monitoring

__all__ = [
    # Core utilities
    "OrderExecutor",
    "ExecutionMode",
    "ExecutionResult",
    "LiquidityAnalyzer",
    "LiquidityReport",
    "PositionSizer",
    "SlippageCalculator",
    
    # Advanced patterns
    "AtomicMultiOrderExecutor",
    "OrderSpec",
    "AtomicExecutionResult",
]

