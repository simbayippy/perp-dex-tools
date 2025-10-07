"""
Backpack DEX Funding Adapter (Not Implemented)

This is a placeholder for future Backpack funding rate collection implementation.
"""

from typing import Dict
from decimal import Decimal

from exchange_clients.base import BaseFundingAdapter


class BackpackFundingAdapter(BaseFundingAdapter):
    """
    Backpack funding rate adapter (not implemented)
    
    This is a placeholder. Backpack funding rate collection is not yet implemented.
    """
    
    def __init__(
        self, 
        api_base_url: str = "https://api.backpack.exchange",
        timeout: int = 10
    ):
        super().__init__(
            dex_name="backpack",
            api_base_url=api_base_url,
            timeout=timeout
        )
    
    async def fetch_funding_rates(self) -> Dict[str, Decimal]:
        """Fetch funding rates from Backpack (not implemented)"""
        pass
    
    async def fetch_market_data(self) -> Dict[str, Dict[str, Decimal]]:
        """Fetch market data from Backpack (not implemented)"""
        pass
    
    def normalize_symbol(self, dex_symbol: str) -> str:
        """Normalize Backpack symbol format (not implemented)"""
        pass
    
    def get_dex_symbol_format(self, normalized_symbol: str) -> str:
        """Convert normalized symbol to Backpack format (not implemented)"""
        pass

