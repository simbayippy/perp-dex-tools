"""
Config-builder helpers for the funding arbitrage strategy.

Exposes the interactive schema consumed by `trading_config/config_builder.py`.
"""

from .schema import (
    FUNDING_ARB_SCHEMA,
    get_funding_arb_schema,
    create_default_funding_config,
)

__all__ = [
    "FUNDING_ARB_SCHEMA",
    "get_funding_arb_schema",
    "create_default_funding_config",
]
