"""
Profit-taking operations for funding arbitrage.

This module handles opportunistic profit capture through cross-exchange
basis spread trading (mean-reversion). Completely independent from risk
management and position closing logic.
"""

from .profit_evaluator import ProfitEvaluator
from .real_time_monitor import RealTimeProfitMonitor
from .profit_taker import ProfitTaker

__all__ = [
    "ProfitEvaluator",
    "RealTimeProfitMonitor",
    "ProfitTaker",
]
