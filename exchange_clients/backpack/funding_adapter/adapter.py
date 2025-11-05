"""
Backpack DEX Funding Rate Adapter

Fetches funding rates and market data from Backpack using direct API calls.
This adapter is read-only and focused solely on data collection.
"""

from typing import Dict
from decimal import Decimal

from exchange_clients.base_funding_adapter import BaseFundingAdapter
from exchange_clients.base_models import FundingRateSample
from exchange_clients.backpack.common import (
    normalize_symbol as normalize_backpack_symbol,
    get_backpack_symbol_format
)

from .funding_client import BackpackFundingClient
from .fetchers import BackpackFundingFetchers


class BackpackFundingAdapter(BaseFundingAdapter):
    """
    Backpack funding rate adapter
    
    This adapter uses direct API calls to fetch funding rates and market data 
    for all available perpetual markets on Backpack.
    
    Key features:
    - Uses direct HTTP calls to Backpack API (no SDK dependency)
    - Single API call to get ALL funding rates at once
    - Normalizes symbols from Backpack format to standard format
    - No authentication required (public endpoints)
    - Returns funding rates and volume/OI data
    """
    
    def __init__(
        self, 
        api_base_url: str = "https://api.backpack.exchange",
        timeout: int = 10
    ):
        """
        Initialize Backpack adapter
        
        Args:
            api_base_url: Backpack API base URL
            timeout: Request timeout in seconds
        """
        super().__init__(
            dex_name="backpack",
            api_base_url=api_base_url,
            timeout=timeout
        )
        
        # Initialize components
        self.funding_client = BackpackFundingClient(api_base_url, timeout)
        self.fetchers = BackpackFundingFetchers(
            funding_client=self.funding_client,
            timeout=timeout,
            normalize_symbol_fn=normalize_backpack_symbol,  # Use function directly from common.py
            dex_name=self.dex_name,
        )

    async def fetch_funding_rates(self) -> Dict[str, FundingRateSample]:
        """
        Fetch all funding rates from Backpack
        
        Uses the /api/v1/markPrices endpoint to get ALL funding rates in a single call.
        This is much faster than calling individual endpoints per symbol.
        
        Returns:
            Dictionary mapping normalized symbols to FundingRateSample entries
            
        Raises:
            Exception: If fetching fails after retries
        """
        return await self.fetchers.fetch_funding_rates(self.CANONICAL_INTERVAL_HOURS)

    async def fetch_market_data(self) -> Dict[str, Dict[str, Decimal]]:
        """
        Fetch market data (volume, open interest) from Backpack
        
        Returns:
            Dictionary mapping normalized symbols to market data
            Example: {
                "BTC": {
                    "volume_24h": Decimal("1000000.0"),
                    "open_interest": Decimal("5000000.0")
                }
            }
        """
        return await self.fetchers.fetch_market_data()

    def normalize_symbol(self, dex_symbol: str) -> str:
        """
        Normalize Backpack symbol format to standard format
        
        Uses shared logic from common.py which handles:
        - "BTC_USDC_PERP" -> "BTC"
        - "kPEPE_USDC_PERP" -> "PEPE" (removes k-prefix for 1000x tokens)
        
        Args:
            dex_symbol: Backpack-specific symbol format
            
        Returns:
            Normalized symbol (e.g., "BTC", "PEPE")
        """
        return normalize_backpack_symbol(dex_symbol)
    
    def get_dex_symbol_format(self, normalized_symbol: str) -> str:
        """
        Convert normalized symbol back to Backpack-specific format
        
        Uses shared logic from common.py which handles:
        - "BTC" -> "BTC_USDC_PERP"
        - "PEPE" -> "kPEPE_USDC_PERP" (adds k-prefix for 1000x tokens)
        
        Args:
            normalized_symbol: Normalized symbol (e.g., "BTC", "PEPE")
            
        Returns:
            Backpack-specific format (e.g., "BTC_USDC_PERP", "kPEPE_USDC_PERP")
        """
        return get_backpack_symbol_format(normalized_symbol)
    
    async def close(self) -> None:
        """Close the HTTP session"""
        await self.funding_client.close()
        await super().close()

