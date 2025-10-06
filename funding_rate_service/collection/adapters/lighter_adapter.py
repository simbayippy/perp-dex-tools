"""
Lighter DEX Adapter

Fetches funding rates from Lighter using the official Lighter Python SDK.
This is a read-only adapter (no trading) focused solely on data collection.
"""

from typing import Dict, Optional
from decimal import Decimal
import re

from collection.base_adapter import BaseDEXAdapter
from utils.logger import logger

# Import Lighter SDK
try:
    import lighter
    from lighter import ApiClient, Configuration, FundingApi
    LIGHTER_SDK_AVAILABLE = True
except ImportError:
    LIGHTER_SDK_AVAILABLE = False
    logger.warning("Lighter SDK not available. Install with: pip install lighter-python")


class LighterAdapter(BaseDEXAdapter):
    """
    Lighter adapter for fetching funding rates
    
    This adapter uses the official Lighter Python SDK to fetch funding rates
    for all available perpetual markets on Lighter.
    
    Key features:
    - Uses Lighter's FundingApi.funding_rates() endpoint
    - Normalizes symbols from Lighter format to standard format
    - No authentication required (public endpoint)
    - Returns all available funding rates in one API call
    """
    
    def __init__(
        self, 
        api_base_url: str = "https://mainnet.zklighter.elliot.ai",
        timeout: int = 10
    ):
        """
        Initialize Lighter adapter
        
        Args:
            api_base_url: Lighter API base URL (mainnet or testnet)
            timeout: Request timeout in seconds
        """
        if not LIGHTER_SDK_AVAILABLE:
            raise ImportError(
                "Lighter SDK is required. Install with: pip install lighter-python"
            )
        
        super().__init__(
            dex_name="lighter",
            api_base_url=api_base_url,
            timeout=timeout
        )
        
        # Initialize Lighter API client
        self.api_client: Optional[ApiClient] = None
        self.funding_api: Optional[FundingApi] = None
        
        logger.info(f"Lighter adapter initialized with URL: {api_base_url}")
    
    async def _ensure_client(self) -> None:
        """Ensure API client is initialized"""
        if self.api_client is None or self.api_client.rest_client.pool_manager is None:
            configuration = Configuration(host=self.api_base_url)
            self.api_client = ApiClient(configuration=configuration)
            self.funding_api = FundingApi(self.api_client)
            logger.debug(f"{self.dex_name}: API client initialized")
    
    async def fetch_funding_rates(self) -> Dict[str, Decimal]:
        """
        Fetch all funding rates from Lighter
        
        Uses the FundingApi.funding_rates() endpoint which returns funding rates
        for all markets including Lighter's own rates and aggregated rates from
        other exchanges.
        
        Returns:
            Dictionary mapping normalized symbols to funding rates
            Example: {"BTC": Decimal("0.0001"), "ETH": Decimal("0.00008")}
            
        Raises:
            Exception: If fetching fails after retries
        """
        await self._ensure_client()
        
        try:
            logger.debug(f"{self.dex_name}: Fetching funding rates...")
            
            # Call Lighter SDK
            funding_rates_response = await self.funding_api.funding_rates(
                _request_timeout=self.timeout
            )
            
            # Parse response
            if not funding_rates_response or not funding_rates_response.funding_rates:
                logger.warning(f"{self.dex_name}: No funding rates returned")
                return {}
            
            # Filter for Lighter exchange only (exclude aggregated data from other exchanges)
            lighter_rates = [
                rate for rate in funding_rates_response.funding_rates
                if rate.exchange.lower() == 'lighter'
            ]
            
            if not lighter_rates:
                logger.warning(
                    f"{self.dex_name}: No Lighter-specific rates found in response"
                )
                return {}
            
            # Convert to our format
            rates_dict = {}
            for rate in lighter_rates:
                try:
                    # Normalize symbol (e.g., "BTC-PERP" -> "BTC")
                    normalized_symbol = self.normalize_symbol(rate.symbol)
                    
                    # Convert rate to Decimal
                    funding_rate = Decimal(str(rate.rate))
                    
                    rates_dict[normalized_symbol] = funding_rate
                    
                    logger.debug(
                        f"{self.dex_name}: {rate.symbol} -> {normalized_symbol}: "
                        f"{funding_rate}"
                    )
                
                except Exception as e:
                    logger.error(
                        f"{self.dex_name}: Error parsing rate for {rate.symbol}: {e}"
                    )
                    continue
            
            logger.info(
                f"{self.dex_name}: Successfully fetched {len(rates_dict)} funding rates"
            )
            
            return rates_dict
        
        except Exception as e:
            logger.error(f"{self.dex_name}: Failed to fetch funding rates: {e}")
            raise
    
    def normalize_symbol(self, dex_symbol: str) -> str:
        """
        Normalize Lighter symbol format to standard format
        
        Lighter symbols typically follow patterns like:
        - "BTC-PERP" -> "BTC"
        - "ETH-PERP" -> "ETH"
        - "SOL-PERP" -> "SOL"
        - "PEPE-PERP" -> "PEPE"
        - "1000PEPE-PERP" -> "PEPE" (some have multipliers)
        
        Args:
            dex_symbol: Lighter-specific symbol format
            
        Returns:
            Normalized symbol (e.g., "BTC")
        """
        # Remove common suffixes
        normalized = dex_symbol.upper()
        
        # Remove "-PERP" suffix
        normalized = normalized.replace('-PERP', '')
        
        # Remove other common perpetual suffixes
        normalized = normalized.replace('-USD', '')
        normalized = normalized.replace('-USDC', '')
        normalized = normalized.replace('-USDT', '')
        normalized = normalized.replace('PERP', '')
        
        # Handle multipliers (e.g., "1000PEPE" -> "PEPE")
        # Match pattern: starts with digits followed by letters
        match = re.match(r'^(\d+)([A-Z]+)$', normalized)
        if match:
            multiplier, symbol = match.groups()
            logger.debug(
                f"{self.dex_name}: Symbol has multiplier: {dex_symbol} -> "
                f"{symbol} (multiplier: {multiplier})"
            )
            normalized = symbol
        
        # Clean up any remaining special characters
        normalized = normalized.strip('-_/')
        
        return normalized
    
    def get_dex_symbol_format(self, normalized_symbol: str) -> str:
        """
        Convert normalized symbol back to Lighter-specific format
        
        Args:
            normalized_symbol: Normalized symbol (e.g., "BTC")
            
        Returns:
            Lighter-specific format (e.g., "BTC-PERP")
        """
        # Lighter typically uses "{SYMBOL}-PERP" format
        return f"{normalized_symbol.upper()}-PERP"
    
    async def close(self) -> None:
        """Close the API client"""
        if self.api_client and self.api_client.rest_client.pool_manager:
            await self.api_client.close()
            logger.debug(f"{self.dex_name}: API client closed")
        
        # Call parent close
        await super().close()


# Example usage (for testing)
async def test_lighter_adapter():
    """Test the Lighter adapter"""
    adapter = LighterAdapter()
    
    try:
        rates, latency_ms = await adapter.fetch_with_metrics()
        
        print(f"\n✅ Lighter Adapter Test")
        print(f"Latency: {latency_ms}ms")
        print(f"Fetched {len(rates)} rates:\n")
        
        for symbol, rate in sorted(rates.items())[:10]:  # Show first 10
            annualized_apy = float(rate) * 365 * 3 * 100  # Assuming 8h periods
            print(f"  {symbol:10s}: {rate:>12} ({annualized_apy:>8.2f}% APY)")
        
        if len(rates) > 10:
            print(f"  ... and {len(rates) - 10} more")
    
    except Exception as e:
        print(f"\n❌ Lighter Adapter Test Failed: {e}")
        raise
    
    finally:
        await adapter.close()


if __name__ == "__main__":
    import asyncio
    asyncio.run(test_lighter_adapter())

