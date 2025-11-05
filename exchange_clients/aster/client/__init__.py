"""
Aster client package.

This package contains the modular Aster exchange client implementation:
- core: Main AsterClient class implementing BaseExchangeClient
- managers: Specialized manager classes (market_data, order_manager, etc.)
- utils: Utility functions and helpers
"""

from .core import AsterClient

__all__ = ["AsterClient"]

