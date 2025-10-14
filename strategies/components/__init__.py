"""
Shared components for trading strategies.

These components are reusable across different strategy implementations.
"""

from .base_components import (
    BasePositionManager,
    Position,
    InMemoryPositionManager,
)

__all__ = [
    # Base interfaces
    'BasePositionManager',
    'Position',
    
    # Implementations
    'InMemoryPositionManager',
]

