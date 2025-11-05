"""
Data fetching logic for Backpack funding adapter.

Handles fetching funding rates and market data from Backpack API.
"""

from datetime import datetime, timezone
from typing import Dict, Optional, Callable
from decimal import Decimal

import aiohttp

from exchange_clients.base_models import FundingRateSample
from funding_rate_service.utils.logger import logger


class BackpackFundingFetchers:
    """Handles data fetching from Backpack funding API."""

    def __init__(
        self,
        funding_client: 'BackpackFundingClient',
        timeout: int,
        normalize_symbol_fn: Callable[[str], str],
        dex_name: str = "backpack",
    ):
        """
        Initialize fetchers.
        
        Args:
            funding_client: BackpackFundingClient instance
            timeout: Request timeout in seconds
            normalize_symbol_fn: Function to normalize symbols
            dex_name: Exchange name for logging
        """
        self.funding_client = funding_client
        self.timeout = timeout
        self.normalize_symbol = normalize_symbol_fn
        self.dex_name = dex_name

    @staticmethod
    def parse_timestamp(value: Optional[object]) -> Optional[datetime]:
        """
        Parse timestamp from various formats.
        
        Args:
            value: Timestamp value (int, datetime, or None)
            
        Returns:
            UTC datetime without timezone info, or None
        """
        if value is None:
            return None
        if isinstance(value, datetime):
            dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).replace(tzinfo=None)
        try:
            numeric = int(value)
        except (TypeError, ValueError):
            return None
        if numeric > 10**16:
            dt = datetime.fromtimestamp(numeric / 1_000_000_000, tz=timezone.utc)
        elif numeric > 10**12:
            dt = datetime.fromtimestamp(numeric / 1000, tz=timezone.utc)
        else:
            dt = datetime.fromtimestamp(numeric, tz=timezone.utc)
        return dt.replace(tzinfo=None)

    async def _make_request(self, endpoint: str, params: Optional[Dict] = None) -> Dict:
        """
        Make HTTP request to Backpack API.
        
        Args:
            endpoint: API endpoint (relative to base URL)
            params: Optional query parameters
            
        Returns:
            JSON response dictionary
        """
        session = await self.funding_client.ensure_client()
        url = f"{self.funding_client.api_base_url}/{endpoint}"
        
        try:
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    error_text = await response.text()
                    raise Exception(f"HTTP {response.status}: {error_text}")
        except Exception as e:
            logger.error(f"{self.dex_name}: Request failed for {endpoint}: {e}")
            raise

    async def fetch_funding_rates(
        self, canonical_interval_hours: Decimal
    ) -> Dict[str, FundingRateSample]:
        """
        Fetch all funding rates from Backpack.
        
        Uses the /api/v1/markPrices endpoint to get ALL funding rates in a single call.
        This is much faster than calling individual endpoints per symbol.
        
        Args:
            canonical_interval_hours: Canonical funding interval (typically 8 hours)
        
        Returns:
            Dictionary mapping normalized symbols to FundingRateSample entries
            
        Raises:
            Exception: If fetching fails after retries
        """
        try:
            # Get ALL mark prices (including funding rates) in one call
            mark_prices_data = await self._make_request("api/v1/markPrices")
            
            if not mark_prices_data:
                return {}
            
            # Ensure we have a list
            if not isinstance(mark_prices_data, list):
                return {}
            
            rates_dict: Dict[str, FundingRateSample] = {}
            
            # Extract funding rates from mark prices data
            for mark_data in mark_prices_data:
                try:
                    symbol = mark_data.get('symbol', '')
                    
                    # Only process perpetual markets (ending with _PERP)
                    if not symbol.endswith('_PERP'):
                        continue
                    
                    # Get funding rate from mark prices response
                    funding_rate = mark_data.get('fundingRate')
                    
                    if funding_rate is None:
                        continue
                    
                    # Normalize symbol (e.g., "BTC_USDC_PERP" -> "BTC")
                    normalized_symbol = self.normalize_symbol(symbol)
                    
                    raw_rate = Decimal(str(funding_rate))
                    interval_hours = Decimal('1')
                    normalized_rate = raw_rate * (canonical_interval_hours / interval_hours)
                    next_funding_time = self.parse_timestamp(mark_data.get('nextFundingTime'))
                    
                    rates_dict[normalized_symbol] = FundingRateSample(
                        normalized_rate=normalized_rate,
                        raw_rate=raw_rate,
                        interval_hours=interval_hours,
                        next_funding_time=next_funding_time,
                        metadata={'symbol': symbol}
                    )
                
                except Exception as e:
                    logger.error(
                        f"{self.dex_name}: Error parsing mark price data for {mark_data.get('symbol', 'unknown')}: {e}"
                    )
                    continue
            
            return rates_dict
        
        except Exception as e:
            logger.error(f"{self.dex_name}: Failed to fetch funding rates: {e}")
            raise

    async def fetch_market_data(self) -> Dict[str, Dict[str, Decimal]]:
        """
        Fetch market data (volume, open interest) from Backpack.
        
        Returns:
            Dictionary mapping normalized symbols to market data
            Example: {
                "BTC": {
                    "volume_24h": Decimal("1000000.0"),
                    "open_interest": Decimal("5000000.0")
                }
            }
        """
        try:
            # Fetch tickers for volume data (based on API documentation)
            tickers_data = await self._make_request("api/v1/tickers")
            
            # Fetch open interest data (separate endpoint)
            open_interest_data = await self._make_request("api/v1/openInterest")
            
            if not tickers_data:
                return {}
            
            # Create lookup for open interest data
            oi_lookup = {}
            if open_interest_data:
                if isinstance(open_interest_data, list):
                    for oi_item in open_interest_data:
                        symbol = oi_item.get('symbol', '')
                        if symbol:
                            oi_lookup[symbol] = oi_item
                elif isinstance(open_interest_data, dict):
                    # Single symbol response
                    symbol = open_interest_data.get('symbol', '')
                    if symbol:
                        oi_lookup[symbol] = open_interest_data
            
            # Extract market data
            market_data = {}
            
            # Handle both single dict and list of dicts response for tickers
            if isinstance(tickers_data, dict):
                tickers_list = [tickers_data]
            elif isinstance(tickers_data, list):
                tickers_list = tickers_data
            else:
                return {}
            
            for ticker in tickers_list:
                try:
                    symbol = ticker.get('symbol', '')
                    
                    # Only process perpetual markets
                    if not symbol.endswith('_PERP'):
                        continue
                    
                    # Normalize symbol
                    normalized_symbol = self.normalize_symbol(symbol)
                    
                    # Get volume (24h) - based on API docs, field is "quoteVolume"
                    volume_24h = ticker.get('quoteVolume')
                    
                    # Get open interest from lookup - based on API docs, field is "openInterest"
                    oi_data = oi_lookup.get(symbol, {})
                    open_interest = oi_data.get('openInterest')
                    
                    # Create market data entry
                    data = {}
                    
                    if volume_24h is not None:
                        data['volume_24h'] = Decimal(str(volume_24h))
                    
                    if open_interest is not None:
                        data['open_interest'] = Decimal(str(open_interest))
                    
                    if data:  # Only add if we have some data
                        market_data[normalized_symbol] = data
                
                except Exception as e:
                    logger.error(
                        f"{self.dex_name}: Error parsing market data for {ticker.get('symbol', 'unknown')}: {e}"
                    )
                    continue
            
            return market_data
        
        except Exception as e:
            logger.error(f"{self.dex_name}: Failed to fetch market data: {e}")
            return {}

