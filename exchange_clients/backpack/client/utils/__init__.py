"""
Backpack client utilities package.

This package contains utility functions and classes:
- helpers: Decimal helpers, precision inference, symbol formatting
- converters: Order info builders, snapshot builders
- caching: Symbol precision cache, market symbol map cache
"""

from .helpers import (
    to_decimal,
    get_decimal_places,
    infer_precision_from_prices,
    get_symbol_precision,
    to_internal_symbol,
    ensure_exchange_symbol,
    quantize_quantity,
    format_decimal,
    enforce_max_decimals,
    quantize_to_tick,
    compute_post_only_price,
)
from .converters import build_order_info_from_raw
from .caching import SymbolPrecisionCache, MarketSymbolMapCache

__all__ = [
    'to_decimal',
    'get_decimal_places',
    'infer_precision_from_prices',
    'get_symbol_precision',
    'to_internal_symbol',
    'ensure_exchange_symbol',
    'quantize_quantity',
    'format_decimal',
    'enforce_max_decimals',
    'quantize_to_tick',
    'compute_post_only_price',
    'build_order_info_from_raw',
    'SymbolPrecisionCache',
    'MarketSymbolMapCache',
]

