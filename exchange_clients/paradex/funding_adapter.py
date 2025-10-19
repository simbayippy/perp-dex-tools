"""
Paradex DEX Funding Adapter (Not Implemented)

This is a placeholder for future Paradex funding rate collection implementation.
"""

from typing import Dict
from decimal import Decimal

from exchange_clients.base_funding_adapter import BaseFundingAdapter
from exchange_clients.base_models import FundingRateSample


class ParadexFundingAdapter(BaseFundingAdapter):
    """
    Paradex funding rate adapter (not implemented)
    
    This is a placeholder. Paradex funding rate collection is not yet implemented.
    """
    
    def __init__(
        self, 
        api_base_url: str = "https://api.paradex.trade",
        timeout: int = 10
    ):
        super().__init__(
            dex_name="paradex",
            api_base_url=api_base_url,
            timeout=timeout
        )
    
    async def fetch_funding_rates(self) -> Dict[str, FundingRateSample]:
        """Fetch funding rates from Paradex (not implemented)"""
        pass
    
    async def fetch_market_data(self) -> Dict[str, Dict[str, Decimal]]:
        """Fetch market data from Paradex (not implemented)"""
        pass
    
    def normalize_symbol(self, dex_symbol: str) -> str:
        """Normalize Paradex symbol format (not implemented)"""
        pass
    
    def get_dex_symbol_format(self, normalized_symbol: str) -> str:
        """Convert normalized symbol to Paradex format (not implemented)"""
        pass
