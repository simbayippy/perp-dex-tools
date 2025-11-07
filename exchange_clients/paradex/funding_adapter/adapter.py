"""
Paradex DEX Funding Rate Adapter

Fetches funding rates from Paradex using the official Paradex Python SDK.
This is a read-only adapter (no trading) focused solely on data collection.
"""

from typing import Dict
from decimal import Decimal

from exchange_clients.base_funding_adapter import BaseFundingAdapter
from exchange_clients.base_models import FundingRateSample
from exchange_clients.paradex.common import (
    normalize_symbol as normalize_paradex_symbol,
    get_paradex_symbol_format
)

from .funding_client import ParadexFundingClient
from .fetchers import ParadexFundingFetchers


class ParadexFundingAdapter(BaseFundingAdapter):
    """
    Paradex adapter for fetching funding rates.
    
    This adapter uses the official Paradex Python SDK to fetch funding rates
    for all available perpetual markets on Paradex.
    
    Key features:
    - Uses Paradex API to fetch funding rates and market data
    - Normalizes symbols from Paradex format to standard format
    - No authentication required (public endpoints)
    - Returns all available funding rates in one API call
    """
    
    def __init__(
        self, 
        api_base_url: str = None,
        environment: str = "prod",
        timeout: int = 10
    ):
        """
        Initialize Paradex adapter.
        
        Args:
            api_base_url: Paradex API base URL (optional, determined by environment)
            environment: "prod" or "testnet"
            timeout: Request timeout in seconds
        """
        # Determine API URL based on environment
        if api_base_url is None:
            env_map = {
                'prod': 'https://api.prod.paradex.trade/v1',
                'testnet': 'https://api.testnet.paradex.trade/v1'
            }
            api_base_url = env_map.get(environment.lower(), env_map['prod'])
        
        super().__init__(
            dex_name="paradex",
            api_base_url=api_base_url,
            timeout=timeout
        )
        
        self.environment = environment
        
        # Initialize components
        self.funding_client = ParadexFundingClient(api_base_url, environment)
        self.fetchers = ParadexFundingFetchers(
            funding_client=self.funding_client,
            timeout=timeout,
            normalize_symbol_fn=normalize_paradex_symbol,  # Use function directly from common.py
        )

    async def fetch_funding_rates(self) -> Dict[str, FundingRateSample]:
        """
        Fetch all funding rates from Paradex.
        
        Returns:
            Dictionary mapping normalized symbols to FundingRateSample entries
        """
        return await self.fetchers.fetch_funding_rates(self.CANONICAL_INTERVAL_HOURS)

    async def fetch_market_data(self) -> Dict[str, Dict[str, Decimal]]:
        """
        Fetch complete market data (volume + OI) from Paradex.
        
        Returns:
            Dictionary mapping normalized symbols to market data
        """
        return await self.fetchers.fetch_market_data()

    def normalize_symbol(self, dex_symbol: str) -> str:
        """
        Normalize Paradex symbol format to standard format.
        
        Uses shared normalization logic from common.py which handles:
        - "BTC-USD-PERP" -> "BTC"
        - "ETH-USD-PERP" -> "ETH"
        
        Args:
            dex_symbol: Paradex-specific symbol format
            
        Returns:
            Normalized symbol (e.g., "BTC", "ETH")
        """
        return normalize_paradex_symbol(dex_symbol)
    
    def get_dex_symbol_format(self, normalized_symbol: str) -> str:
        """
        Convert normalized symbol back to Paradex-specific format.
        
        Uses shared logic from common.py which handles:
        - "BTC" -> "BTC-USD-PERP"
        - "ETH" -> "ETH-USD-PERP"
        
        Args:
            normalized_symbol: Normalized symbol (e.g., "BTC", "ETH")
            
        Returns:
            Paradex-specific format (e.g., "BTC-USD-PERP")
        """
        return get_paradex_symbol_format(normalized_symbol)
    
    async def close(self) -> None:
        """Close the API client."""
        await self.funding_client.close()
        await super().close()

