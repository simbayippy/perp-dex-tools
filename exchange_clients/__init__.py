"""
Shared Exchange Clients Library

This library provides unified interfaces for both trading execution and funding rate collection
across multiple perpetual DEXs.

Modules:
    - base: Base interfaces (BaseExchangeClient, BaseFundingAdapter)
    - lighter: Lighter DEX implementation
    - grvt: GRVT DEX implementation
    - edgex: EdgeX DEX implementation
"""

from .base import (
    BaseExchangeClient,
    BaseFundingAdapter,
    OrderResult,
    OrderInfo,
    query_retry
)
from .events import LiquidationEvent, LiquidationEventDispatcher

__all__ = [
    'BaseExchangeClient',
    'BaseFundingAdapter',
    'OrderResult',
    'OrderInfo',
    'query_retry',
    'LiquidationEvent',
    'LiquidationEventDispatcher',
]

__version__ = '1.0.0'
