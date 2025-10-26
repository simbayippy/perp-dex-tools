"""
Shared Exchange Clients Library

This library provides unified interfaces for both trading execution and funding rate collection
across multiple perpetual DEXs.

Modules:
    - base_client: Trading execution interface (BaseExchangeClient)
    - base_funding_adapter: Funding data interface (BaseFundingAdapter)
    - base_models: Shared dataclasses/utilities
"""

from .base_client import BaseExchangeClient
from .base_funding_adapter import BaseFundingAdapter
from .base_models import (
    ExchangePositionSnapshot,
    FundingRateSample,
    MissingCredentialsError,
    OrderInfo,
    OrderResult,
    query_retry,
    validate_credentials,
)
from .base_websocket import BaseWebSocketManager, BBOData
from .events import LiquidationEvent, LiquidationEventDispatcher

__all__ = [
    "BaseExchangeClient",
    "BaseFundingAdapter",
    "ExchangePositionSnapshot",
    "FundingRateSample",
    "MissingCredentialsError",
    "OrderInfo",
    "OrderResult",
    "query_retry",
    "validate_credentials",
    "BaseWebSocketManager",
    "BBOData",
    "LiquidationEvent",
    "LiquidationEventDispatcher",
]

__version__ = "1.0.0"
