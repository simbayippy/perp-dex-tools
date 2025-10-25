"""
Lighter DEX Funding Rate Adapter

Fetches funding rates from Lighter using the official Lighter Python SDK.
This is a read-only adapter (no trading) focused solely on data collection.
"""

from datetime import datetime, timezone
from typing import Dict, Optional
from decimal import Decimal

from exchange_clients.base_funding_adapter import BaseFundingAdapter
from exchange_clients.base_models import FundingRateSample
from exchange_clients.lighter.common import (
    normalize_symbol as normalize_lighter_symbol,
    get_lighter_symbol_format
)

# Import Lighter SDK
try:
    import lighter
    from lighter import ApiClient, Configuration, FundingApi, OrderApi
    LIGHTER_SDK_AVAILABLE = True
except ImportError:
    LIGHTER_SDK_AVAILABLE = False
    import logging
    # logging.warning("Lighter SDK not available. Install with: pip install lighter-python")  # commented as per log rule


class LighterFundingAdapter(BaseFundingAdapter):
    """
    Lighter adapter for fetching funding rates
    
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
    
    # Open Interest multiplier for two-sided calculation
    # Lighter API returns one-sided OI (longs or shorts), but total OI
    # shown in their UI is long + short (two-sided), hence × 2
    OI_TWO_SIDED_MULTIPLIER = 2
    
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
        self.order_api: Optional[OrderApi] = None
    
    async def _ensure_client(self) -> None:
        """Ensure API client is initialized"""
        if self.api_client is None or self.api_client.rest_client.pool_manager is None:
            configuration = Configuration(host=self.api_base_url)
            self.api_client = ApiClient(configuration=configuration)
            self.funding_api = FundingApi(self.api_client)
            self.order_api = OrderApi(self.api_client)
    
    @staticmethod
    def _parse_next_funding_time(value: Optional[object]) -> Optional[datetime]:
        if value is None:
            return None
        if isinstance(value, datetime):
            dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).replace(tzinfo=None)
        try:
            numeric = int(value)
        except (TypeError, ValueError):
            return None
        if numeric > 10**12:
            dt = datetime.fromtimestamp(numeric / 1000, tz=timezone.utc)
        else:
            dt = datetime.fromtimestamp(numeric, tz=timezone.utc)
        return dt.replace(tzinfo=None)

    async def fetch_funding_rates(self) -> Dict[str, FundingRateSample]:
        """
        Fetch all funding rates from Lighter
        
        Uses the FundingApi.funding_rates() endpoint which returns funding rates
        for all markets including Lighter's own rates and aggregated rates from
        other exchanges.
        
        Returns:
            Dictionary mapping normalized symbols to FundingRateSample entries
            
        Raises:
            Exception: If fetching fails after retries
        """
        await self._ensure_client()
        
        try:
            # Call Lighter SDK
            funding_rates_response = await self.funding_api.funding_rates(
                _request_timeout=self.timeout
            )
            
            # Parse response
            if not funding_rates_response or not funding_rates_response.funding_rates:
                return {}
            
            # Filter for Lighter exchange only (exclude aggregated data from other exchanges)
            lighter_rates = [
                rate for rate in funding_rates_response.funding_rates
                if rate.exchange.lower() == 'lighter'
            ]
            
            if not lighter_rates:
                return {}
            
            # Convert to our format
            rates_dict: Dict[str, FundingRateSample] = {}
            for rate in lighter_rates:
                try:
                    # Normalize symbol (e.g., "BTC-PERP" -> "BTC")
                    normalized_symbol = self.normalize_symbol(rate.symbol)
                    
                    funding_rate = Decimal(str(rate.rate))
                    next_funding_time = self._parse_next_funding_time(
                        getattr(rate, 'next_funding_time', None)
                    )
                    rates_dict[normalized_symbol] = FundingRateSample(
                        normalized_rate=funding_rate,
                        raw_rate=funding_rate,
                        interval_hours=self.CANONICAL_INTERVAL_HOURS,
                        next_funding_time=next_funding_time,
                        metadata={'symbol': rate.symbol},
                    )
                
                except Exception as e:
                    continue
            
            return rates_dict
        
        except Exception as e:
            raise
    
    async def fetch_market_data(self) -> Dict[str, Dict[str, Decimal]]:
        """
        Fetch complete market data (volume + OI) from Lighter
        
        Uses the OrderApi.order_book_details() endpoint which returns complete
        market statistics including 24h volume AND open interest for all markets.
        
        Note: Called once per minute (same as funding rates), so performance is fine.
        
        Returns:
            Dictionary mapping normalized symbols to market data
            Example: {
                "BTC": {
                    "volume_24h": Decimal("1500000.0"),
                    "open_interest": Decimal("5000000.0")
                }
            }
            
        Raises:
            Exception: If fetching fails after retries
        """
        await self._ensure_client()
        
        try:
            # Use order_book_details endpoint which includes both volume AND OI
            order_book_details_response = await self.order_api.order_book_details(
                _request_timeout=self.timeout
            )
            
            if not order_book_details_response or not order_book_details_response.order_book_details:
                return {}
            
            # Parse response
            market_data = {}
            for market in order_book_details_response.order_book_details:
                try:
                    # Normalize symbol
                    normalized_symbol = self.normalize_symbol(market.symbol)
                    
                    # Extract volume (already in USD)
                    volume_24h = Decimal(str(market.daily_quote_token_volume))
                    
                    # Extract OI - THIS IS IN BASE TOKEN UNITS, NOT USD!
                    # Need to multiply by current price to get USD value
                    open_interest_base = Decimal(str(market.open_interest))
                    last_trade_price = Decimal(str(market.last_trade_price))
                    
                    # Convert OI from base tokens to USD, then to two-sided total
                    # Step 1: Convert base tokens to USD
                    one_sided_oi_usd = open_interest_base * last_trade_price
                    
                    # Step 2: Convert to two-sided OI (matching Lighter UI definition)
                    # In perps: total longs = total shorts, so two-sided = one-sided × 2
                    open_interest_usd = one_sided_oi_usd * self.OI_TWO_SIDED_MULTIPLIER
                    
                    market_data[normalized_symbol] = {
                        "volume_24h": volume_24h,
                        "open_interest": open_interest_usd
                    }
                    
                except Exception as e:
                    continue
            
            return market_data
        
        except Exception as e:
            raise
    
    def normalize_symbol(self, dex_symbol: str) -> str:
        """
        Normalize Lighter symbol format to standard format
        
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
        Convert normalized symbol back to Lighter-specific format
        
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
        """Close the API client"""
        if self.api_client and self.api_client.rest_client.pool_manager:
            await self.api_client.close()
        
        # Call parent close
        await super().close()


# Example usage (for testing)
async def test_lighter_adapter():
    """Test the Lighter adapter"""
    adapter = LighterFundingAdapter()
    
    try:
        rates, latency_ms = await adapter.fetch_with_metrics()
        
        # print(f"\n✅ Lighter Adapter Test")
        # print(f"Latency: {latency_ms}ms")
        # print(f"Fetched {len(rates)} rates:\n")
        
        # for symbol, rate in sorted(rates.items())[:10]:  # Show first 10
        #     annualized_apy = float(rate) * 365 * 3 * 100  # Assuming 8h periods
        #     print(f"  {symbol:10s}: {rate:>12} ({annualized_apy:>8.2f}% APY)")
        
        # if len(rates) > 10:
        #     print(f"  ... and {len(rates) - 10} more")
    
    except Exception as e:
        print(f"\n❌ Lighter Adapter Test Failed: {e}")
        raise
    
    finally:
        await adapter.close()


if __name__ == "__main__":
    import asyncio
    asyncio.run(test_lighter_adapter())
