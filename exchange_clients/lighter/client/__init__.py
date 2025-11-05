"""
Lighter client package.

This package contains the modular Lighter exchange client implementation:
- core: Main LighterClient class implementing BaseExchangeClient
- managers: Specialized manager classes (market_data, order_manager, etc.)
- utils: Utility functions and helpers
"""

from .core import LighterClient

__all__ = ["LighterClient"]
