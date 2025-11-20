"""
Internal hedge implementation components for atomic multi-order execution.

This package contains internal implementation details for hedge execution:
- HedgeTargetCalculator: Calculates hedge targets with multiplier adjustments
- HedgeResultTracker: Tracks hedge execution results and updates context
- Strategies: MarketHedgeStrategy, AggressiveLimitHedgeStrategy

Note: Pricing and reconciliation are handled by AggressiveLimitExecutionStrategy
(not separate hedge-specific components).

These are internal implementation details used by HedgeManager.
Do not import these directly - use HedgeManager from components package.
"""

from .hedge_target_calculator import HedgeTargetCalculator, HedgeTarget
from .hedge_result_tracker import HedgeResultTracker
from .strategies import (
    HedgeStrategy,
    HedgeResult,
    MarketHedgeStrategy,
    AggressiveLimitHedgeStrategy,
)

# Internal implementation - not exported
__all__ = []

