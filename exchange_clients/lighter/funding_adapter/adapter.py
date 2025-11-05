"""
Lighter DEX Funding Rate Adapter

Fetches funding rates from Lighter using the official Lighter Python SDK.
This is a read-only adapter (no trading) focused solely on data collection.
"""

from typing import Dict
from decimal import Decimal

from exchange_clients.base_funding_adapter import BaseFundingAdapter
from exchange_clients.base_models import FundingRateSample
from exchange_clients.lighter.common import (
    normalize_symbol as normalize_lighter_symbol,
    get_lighter_symbol_format
)

from .funding_client import LighterFundingClient
from .fetchers import LighterFundingFetchers


class LighterFundingAdapter(BaseFundingAdapter):
    """
    Lighter adapter for fetching funding rates.
    
    This adapter uses the official Lighter Python SDK to fetch funding rates
    for all available perpetual markets on Lighter.
    
    Key features:
    - Uses Lighter's FundingApi.funding_rates() endpoint
    - Normalizes symbols from Lighter format to standard format
    - No authentication required (public endpoint)
    - Returns all available funding rates in one API call
    
    Open Interest Calculation:
    - Lighter's API returns one-sided OI (sum of longs OR shorts) in base tokens
    - In perpetual markets, total longs always equals total shorts
    - UI displays "two-sided OI" = long OI + short OI = 2 × one-sided OI
    - We convert to USD and apply the 2× multiplier to match UI representation
    """
    
    def __init__(
        self, 
        api_base_url: str = "https://mainnet.zklighter.elliot.ai",
        timeout: int = 10
    ):
        """
        Initialize Lighter adapter.
        
        Args:
            api_base_url: Lighter API base URL (mainnet or testnet)
            timeout: Request timeout in seconds
        """
        super().__init__(
            dex_name="lighter",
            api_base_url=api_base_url,
            timeout=timeout
        )
        
        # Initialize components
        self.funding_client = LighterFundingClient(api_base_url)
        self.fetchers = LighterFundingFetchers(
            funding_client=self.funding_client,
            timeout=timeout,
            normalize_symbol_fn=normalize_lighter_symbol,  # Use function directly from common.py
        )

    async def fetch_funding_rates(self) -> Dict[str, FundingRateSample]:
        """
        Fetch all funding rates from Lighter.
        
        Returns:
            Dictionary mapping normalized symbols to FundingRateSample entries
        """
        return await self.fetchers.fetch_funding_rates(self.CANONICAL_INTERVAL_HOURS)

    async def fetch_market_data(self) -> Dict[str, Dict[str, Decimal]]:
        """
        Fetch complete market data (volume + OI) from Lighter.
        
        Returns:
            Dictionary mapping normalized symbols to market data
        """
        return await self.fetchers.fetch_market_data()

    def normalize_symbol(self, dex_symbol: str) -> str:
        """
        Normalize Lighter symbol format to standard format.
        
        Uses shared normalization logic from common.py which handles:
        - "BTC" -> "BTC"
        - "1000FLOKI" -> "FLOKI" (1000-prefix)
        - "1000TOSHI" -> "TOSHI" (1000-prefix)
        - "1000PEPE" -> "PEPE" (1000-prefix)
        
        Args:
            dex_symbol: Lighter-specific symbol format
            
        Returns:
            Normalized symbol (e.g., "BTC", "FLOKI", "TOSHI")
        """
        return normalize_lighter_symbol(dex_symbol)
    
    def get_dex_symbol_format(self, normalized_symbol: str) -> str:
        """
        Convert normalized symbol back to Lighter-specific format.
        
        Uses shared logic from common.py which handles:
        - "BTC" -> "BTC"
        - "FLOKI" -> "1000FLOKI" (1000-prefix)
        - "TOSHI" -> "1000TOSHI" (1000-prefix)
        
        Args:
            normalized_symbol: Normalized symbol (e.g., "BTC", "FLOKI", "TOSHI")
            
        Returns:
            Lighter-specific format (e.g., "BTC", "1000FLOKI", "1000TOSHI")
        """
        return get_lighter_symbol_format(normalized_symbol)
    
    async def close(self) -> None:
        """Close the API client."""
        await self.funding_client.close()
        await super().close()

