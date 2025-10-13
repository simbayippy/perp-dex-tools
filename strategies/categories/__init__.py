"""
Strategy Categories - Level 2 of the hierarchy

Provides strategy archetypes:
- StatelessStrategy: For simple strategies (Grid, TWAP)
- StatefulStrategy: For complex strategies (Funding Arb, Market Making)
"""

# from .stateless_strategy import StatelessStrategy
from .stateful_strategy import StatefulStrategy

__all__ = [
    # 'StatelessStrategy',
    'StatefulStrategy',
]

