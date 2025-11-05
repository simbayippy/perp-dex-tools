"""
Aster DEX Funding Rate Adapter

Fetches funding rates and market data from Aster using the official aster-connector-python SDK.
This adapter is read-only and focused solely on data collection.
"""

from typing import Dict
from decimal import Decimal

from exchange_clients.base_funding_adapter import BaseFundingAdapter
from exchange_clients.base_models import FundingRateSample
from exchange_clients.aster.common import (
    normalize_symbol as normalize_aster_symbol,
    get_aster_symbol_format
)
from funding_rate_service.utils.logger import clamp_external_logger_levels

from .funding_client import AsterFundingClient
from .fetchers import AsterFundingFetchers


class AsterFundingAdapter(BaseFundingAdapter):
    """
    Aster funding rate adapter
    
    This adapter uses the official aster-connector-python SDK to fetch funding rates
    and market data for all available perpetual markets on Aster.
    
    Key features:
    - Uses Aster API to fetch funding rates and market data
    - Normalizes symbols from Aster format to standard format
    - No authentication required (public endpoints)
    - Returns funding rates and volume/OI data
    """
    
    def __init__(
        self, 
        api_base_url: str = "https://fapi.asterdex.com",
        timeout: int = 10
    ):
        """
        Initialize Aster adapter
        
        Args:
            api_base_url: Aster API base URL
            timeout: Request timeout in seconds
        """
        super().__init__(
            dex_name="aster",
            api_base_url=api_base_url,
            timeout=timeout
        )
        
        # Initialize components
        self.funding_client = AsterFundingClient(api_base_url, timeout)
        self.fetchers = AsterFundingFetchers(
            funding_client=self.funding_client,
            timeout=timeout,
            normalize_symbol_fn=normalize_aster_symbol,  # Use function directly from common.py
        )
        
        # Clamp external logger levels (from Aster SDK)
        clamp_external_logger_levels()

    async def fetch_funding_rates(self) -> Dict[str, FundingRateSample]:
        """
        Fetch all funding rates from Aster
        
        Aster provides funding rates through their mark_price endpoint which includes
        current funding rate information for each perpetual market.
        
        Returns:
            Dictionary mapping normalized symbols to FundingRateSample entries
            
        Raises:
            Exception: If fetching fails after retries
        """
        return await self.fetchers.fetch_funding_rates(
            self.CANONICAL_INTERVAL_HOURS,
            self._make_request
        )

    async def fetch_market_data(self) -> Dict[str, Dict[str, Decimal]]:
        """
        Fetch market data (volume, open interest) from Aster
        
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
        Normalize Aster symbol format to standard format
        
        Uses shared logic from common.py which handles:
        - "BTCUSDT" -> "BTC"
        - "1000FLOKIUSDT" -> "FLOKI" (multipliers)
        
        Args:
            dex_symbol: Aster-specific symbol format
            
        Returns:
            Normalized symbol (e.g., "BTC", "FLOKI")
        """
        return normalize_aster_symbol(dex_symbol)
    
    def get_dex_symbol_format(self, normalized_symbol: str) -> str:
        """
        Convert normalized symbol back to Aster-specific format
        
        Uses shared logic from common.py.
        
        Args:
            normalized_symbol: Normalized symbol (e.g., "BTC")
            
        Returns:
            Aster-specific format (e.g., "BTCUSDT")
        """
        return get_aster_symbol_format(normalized_symbol)
    
    async def close(self) -> None:
        """Close the API client"""
        await self.funding_client.close()
        await super().close()

