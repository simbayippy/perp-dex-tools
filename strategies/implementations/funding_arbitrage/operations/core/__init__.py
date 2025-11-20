"""Shared utilities for position operations."""

from .trade_aggregator import aggregate_trades_by_order
from .decimal_utils import to_decimal, add_decimal
from .contract_preparer import ContractPreparer
from .websocket_manager import WebSocketManager
from .price_utils import (
    extract_snapshot_price,
    fetch_mid_price,
    calculate_spread_pct,
    MAX_EXIT_SPREAD_PCT,
    MAX_EMERGENCY_CLOSE_SPREAD_PCT,
)

__all__ = [
    "aggregate_trades_by_order",
    "to_decimal",
    "add_decimal",
    "ContractPreparer",
    "WebSocketManager",
    "extract_snapshot_price",
    "fetch_mid_price",
    "calculate_spread_pct",
    "MAX_EXIT_SPREAD_PCT",
    "MAX_EMERGENCY_CLOSE_SPREAD_PCT",
]

