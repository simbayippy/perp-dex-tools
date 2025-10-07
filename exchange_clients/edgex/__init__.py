"""
EdgeX Exchange Client Module

Provides both trading execution and funding rate collection for EdgeX DEX.
"""

from .client import EdgeXClient
from .funding_adapter import EdgeXFundingAdapter
from .common import (
    normalize_symbol,
    get_edgex_symbol_format,
    parse_edgex_order,
    calculate_open_interest_usd
)

__all__ = [
    'EdgeXClient',
    'EdgeXFundingAdapter',
    'normalize_symbol',
    'get_edgex_symbol_format',
    'parse_edgex_order',
    'calculate_open_interest_usd',
]

