"""
Internal hedge implementation components for atomic multi-order execution.

This package contains internal implementation details for hedge execution:
- HedgeTargetCalculator: Calculates hedge targets with multiplier adjustments
- HedgePricer: Calculates hedge prices using various strategies
- OrderReconciler: Handles order polling and reconciliation
- HedgeResultTracker: Tracks hedge execution results and updates context
- Strategies: MarketHedgeStrategy, AggressiveLimitHedgeStrategy

These are internal implementation details used by HedgeManager.
Do not import these directly - use HedgeManager from components package.
"""

from .hedge_target_calculator import HedgeTargetCalculator, HedgeTarget
from .hedge_pricer import HedgePricer, HedgePriceResult
from .order_reconciler import OrderReconciler, ReconciliationResult
from .hedge_result_tracker import HedgeResultTracker
from .strategies import (
    HedgeStrategy,
    HedgeResult,
    MarketHedgeStrategy,
    AggressiveLimitHedgeStrategy,
)

# Internal implementation - not exported
__all__ = []

