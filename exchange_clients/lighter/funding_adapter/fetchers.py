"""
Data fetching logic for Lighter funding adapter.

Handles fetching funding rates and market data from Lighter API.
"""

from datetime import datetime, timezone
from typing import Dict, Optional, Callable
from decimal import Decimal

from exchange_clients.base_models import FundingRateSample


class LighterFundingFetchers:
    """Handles data fetching from Lighter funding API."""

    # Open Interest multiplier for two-sided calculation
    # Lighter API returns one-sided OI (longs or shorts), but total OI
    # shown in their UI is long + short (two-sided), hence × 2
    OI_TWO_SIDED_MULTIPLIER = 2

    def __init__(
        self,
        funding_client: 'LighterFundingClient',
        timeout: int,
        normalize_symbol_fn: Callable[[str], str],
    ):
        """
        Initialize fetchers.
        
        Args:
            funding_client: LighterFundingClient instance
            timeout: Request timeout in seconds
            normalize_symbol_fn: Function to normalize symbols
        """
        self.funding_client = funding_client
        self.timeout = timeout
        self.normalize_symbol = normalize_symbol_fn

    @staticmethod
    def parse_next_funding_time(value: Optional[object]) -> Optional[datetime]:
        """Parse next funding time from various formats."""
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

    async def fetch_funding_rates(
        self, canonical_interval_hours: Decimal
    ) -> Dict[str, FundingRateSample]:
        """
        Fetch all funding rates from Lighter.
        
        Uses the FundingApi.funding_rates() endpoint which returns funding rates
        for all markets including Lighter's own rates and aggregated rates from
        other exchanges.
        
        Args:
            canonical_interval_hours: Canonical funding interval (typically 8 hours)
        
        Returns:
            Dictionary mapping normalized symbols to FundingRateSample entries
            
        Raises:
            Exception: If fetching fails after retries
        """
        await self.funding_client.ensure_client()
        
        try:
            # Call Lighter SDK
            funding_rates_response = await self.funding_client.funding_api.funding_rates(
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
                    next_funding_time = self.parse_next_funding_time(
                        getattr(rate, 'next_funding_time', None)
                    )
                    rates_dict[normalized_symbol] = FundingRateSample(
                        normalized_rate=funding_rate,
                        raw_rate=funding_rate,
                        interval_hours=canonical_interval_hours,
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
        Fetch complete market data (volume + OI) from Lighter.
        
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
        await self.funding_client.ensure_client()
        
        try:
            # Use order_book_details endpoint which includes both volume AND OI
            order_book_details_response = await self.funding_client.order_api.order_book_details(
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

