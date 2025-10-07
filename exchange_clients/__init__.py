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

__all__ = [
    'BaseExchangeClient',
    'BaseFundingAdapter',
    'OrderResult',
    'OrderInfo',
    'query_retry',
]

__version__ = '1.0.0'

