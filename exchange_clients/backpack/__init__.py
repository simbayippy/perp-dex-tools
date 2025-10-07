"""
Backpack Exchange Client Module

Provides both trading execution and funding rate collection for Backpack DEX.
"""

from .client import BackpackClient
from .funding_adapter import BackpackFundingAdapter
from .common import (
    normalize_symbol,
    get_backpack_symbol_format
)

__all__ = [
    'BackpackClient',
    'BackpackFundingAdapter',
    'normalize_symbol',
    'get_backpack_symbol_format',
]

