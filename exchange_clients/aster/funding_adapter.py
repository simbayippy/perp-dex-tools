"""
Aster DEX Funding Adapter (Not Implemented)

This is a placeholder for future Aster funding rate collection implementation.
"""

from typing import Dict
from decimal import Decimal

from exchange_clients.base import BaseFundingAdapter


class AsterFundingAdapter(BaseFundingAdapter):
    """
    Aster funding rate adapter (not implemented)
    
    This is a placeholder. Aster funding rate collection is not yet implemented.
    """
    
    def __init__(
        self, 
        api_base_url: str = "https://fapi.asterdex.com",
        timeout: int = 10
    ):
        super().__init__(
            dex_name="aster",
            api_base_url=api_base_url,
            timeout=timeout
        )
    
    async def fetch_funding_rates(self) -> Dict[str, Decimal]:
        """Fetch funding rates from Aster (not implemented)"""
        pass
    
    async def fetch_market_data(self) -> Dict[str, Dict[str, Decimal]]:
        """Fetch market data from Aster (not implemented)"""
        pass
    
    def normalize_symbol(self, dex_symbol: str) -> str:
        """Normalize Aster symbol format (not implemented)"""
        pass
    
    def get_dex_symbol_format(self, normalized_symbol: str) -> str:
        """Convert normalized symbol to Aster format (not implemented)"""
        pass

