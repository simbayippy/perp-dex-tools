"""
Paradex Exchange Client Module

Provides both trading execution and funding rate collection for Paradex DEX.
"""

from .client.core import ParadexClient
from .funding_adapter import ParadexFundingAdapter  # Now imports from funding_adapter/ package
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

