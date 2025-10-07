"""
Aster Exchange Client Module

Provides both trading execution and funding rate collection for Aster DEX.
"""

from .client import AsterClient
from .funding_adapter import AsterFundingAdapter
from .common import (
    normalize_symbol,
    get_aster_symbol_format
)

__all__ = [
    'AsterClient',
    'AsterFundingAdapter',
    'normalize_symbol',
    'get_aster_symbol_format',
]

