"""
Advanced execution patterns.

Specialized execution patterns for complex strategies:
- AtomicMultiOrderExecutor: Delta-neutral execution
"""

from strategies.execution.patterns.atomic_multi_order import (
    AtomicMultiOrderExecutor,
    OrderSpec,
    AtomicExecutionResult
)

__all__ = [
    "AtomicMultiOrderExecutor",
    "OrderSpec",
    "AtomicExecutionResult",
]

