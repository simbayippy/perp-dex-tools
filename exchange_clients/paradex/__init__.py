"""
Paradex Exchange Client Module

Provides both trading execution and funding rate collection for Paradex DEX.
"""

from .client import ParadexClient
from .funding_adapter import ParadexFundingAdapter
from .common import (
    normalize_symbol,
    get_paradex_symbol_format
)

__all__ = [
    'ParadexClient',
    'ParadexFundingAdapter',
    'normalize_symbol',
    'get_paradex_symbol_format',
]

