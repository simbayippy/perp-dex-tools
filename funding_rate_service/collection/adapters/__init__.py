"""
DEX adapters for data collection
"""

from exchange_clients.lighter import LighterFundingAdapter as LighterAdapter
from exchange_clients.grvt import GrvtFundingAdapter as GrvtAdapter
from exchange_clients.edgex import EdgeXFundingAdapter as EdgeXAdapter
# from collection.adapters.paradex_adapter import ParadexAdapter

__all__ = [
    "LighterAdapter",
    # "ParadexAdapter",
    "GrvtAdapter",
    "EdgeXAdapter",
]

