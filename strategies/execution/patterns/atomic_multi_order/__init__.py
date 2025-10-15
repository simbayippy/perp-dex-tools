"""
Atomic multi-order execution helpers.

Re-export the public API so existing imports from
`strategies.execution.patterns.atomic_multi_order` keep working.
"""

from .executor import AtomicMultiOrderExecutor, OrderSpec, AtomicExecutionResult

__all__ = [
    "AtomicMultiOrderExecutor",
    "OrderSpec",
    "AtomicExecutionResult",
]
