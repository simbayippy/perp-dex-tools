"""
Data fetching logic for Aster funding adapter.

Handles fetching funding rates and market data from Aster API.
"""

from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Callable
from decimal import Decimal, InvalidOperation

from exchange_clients.base_models import FundingRateSample


class AsterFundingFetchers:
    """Handles data fetching from Aster funding API."""

    def __init__(
        self,
        funding_client: 'AsterFundingClient',
        timeout: int,
        normalize_symbol_fn: Callable[[str], str],
    ):
        """
        Initialize fetchers.
        
        Args:
            funding_client: AsterFundingClient instance
            timeout: Request timeout in seconds
            normalize_symbol_fn: Function to normalize symbols
        """
        self.funding_client = funding_client
        self.timeout = timeout
        self.normalize_symbol = normalize_symbol_fn
        
        # Funding interval cache
        self._funding_interval_cache: Dict[str, Decimal] = {}
        self._funding_interval_last_refresh: Optional[datetime] = None
        self._funding_interval_ttl = timedelta(minutes=10)

    @staticmethod
    def parse_next_funding_time(value: Optional[object]) -> Optional[datetime]:
        """
        Convert API timestamps (ms/ns) to aware UTC datetime.
        
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

    async def _refresh_funding_intervals(self, make_request_fn: Callable) -> None:
        """
        Refresh cached funding interval hours per symbol.
        
        Args:
            make_request_fn: Function to make HTTP requests
        """
        now = datetime.now(timezone.utc)
        if (
            self._funding_interval_cache
            and self._funding_interval_last_refresh
            and now - self._funding_interval_last_refresh < self._funding_interval_ttl
        ):
            return
        
        try:
            response = await make_request_fn("/fapi/v1/fundingInfo")
        except Exception:
            # Silently fail - will use default interval
            self._funding_interval_last_refresh = now
            return
        
        updated_cache: Dict[str, Decimal] = {}
        data_iter = response if isinstance(response, list) else response or []
        for entry in data_iter:
            symbol = entry.get("symbol")
            interval_value = entry.get("fundingIntervalHours")
            if not symbol or interval_value is None:
                continue
            try:
                interval_decimal = Decimal(str(interval_value))
            except (InvalidOperation, TypeError, ValueError):
                continue
            if interval_decimal > 0:
                updated_cache[symbol] = interval_decimal
        
        if updated_cache:
            self._funding_interval_cache = updated_cache
        self._funding_interval_last_refresh = now

    async def fetch_funding_rates(
        self,
        canonical_interval_hours: Decimal,
        make_request_fn: Callable,
    ) -> Dict[str, FundingRateSample]:
        """
        Fetch all funding rates from Aster.
        
        Aster provides funding rates through their mark_price endpoint which includes
        current funding rate information for each perpetual market.
        
        Args:
            canonical_interval_hours: Canonical funding interval (typically 8 hours)
            make_request_fn: Function to make HTTP requests (for funding interval refresh)
        
        Returns:
            Dictionary mapping normalized symbols to FundingRateSample entries
            
        Raises:
            Exception: If fetching fails
        """
        # Refresh funding intervals cache
        await self._refresh_funding_intervals(make_request_fn)
        
        # Ensure client is initialized
        aster_client = self.funding_client.ensure_client()
        
        # Fetch mark prices for all symbols which includes funding rates
        mark_prices_data = aster_client.mark_price()
        
        if not mark_prices_data:
            return {}
        
        # Extract funding rates
        rates_dict: Dict[str, FundingRateSample] = {}
        
        # Handle both single dict and list of dicts response
        if isinstance(mark_prices_data, dict):
            mark_prices_list = [mark_prices_data]
        elif isinstance(mark_prices_data, list):
            mark_prices_list = mark_prices_data
        else:
            return {}
        
        for market_data in mark_prices_list:
            try:
                symbol = market_data.get('symbol', '')
                
                # Only process perpetual markets (ending with USDT)
                if not symbol.endswith('USDT'):
                    continue
                
                # Get funding rate - try multiple possible field names
                funding_rate = (
                    market_data.get('lastFundingRate') or 
                    market_data.get('fundingRate') or 
                    market_data.get('funding_rate')
                )
                
                if funding_rate is None:
                    continue
                
                normalized_symbol = self.normalize_symbol(symbol)
                raw_rate = Decimal(str(funding_rate))
                
                # Get interval from cache or use default
                interval_hours = self._funding_interval_cache.get(
                    symbol,
                    canonical_interval_hours,
                )
                if interval_hours <= 0:
                    interval_hours = canonical_interval_hours
                
                # Normalize rate to canonical interval
                normalized_rate = raw_rate * (canonical_interval_hours / interval_hours)
                
                next_funding_time = self.parse_next_funding_time(
                    market_data.get('nextFundingTime')
                )
                
                rates_dict[normalized_symbol] = FundingRateSample(
                    normalized_rate=normalized_rate,
                    raw_rate=raw_rate,
                    interval_hours=interval_hours,
                    next_funding_time=next_funding_time,
                    metadata={'symbol': symbol},
                )
            
            except Exception:
                # Skip invalid entries
                continue
        
        return rates_dict

    async def fetch_market_data(self) -> Dict[str, Dict[str, Decimal]]:
        """
        Fetch market data (volume, open interest) from Aster.
        
        Returns:
            Dictionary mapping normalized symbols to market data
            Example: {
                "BTC": {
                    "volume_24h": Decimal("1000000.0"),
                    "open_interest": Decimal("5000000.0")
                }
            }
        """
        # Ensure client is initialized
        aster_client = self.funding_client.ensure_client()
        
        try:
            # Fetch 24hr ticker data for volume and mark prices for open interest
            ticker_data = aster_client.ticker_24hr_price_change()
            mark_prices_data = aster_client.mark_price()
            
            if not ticker_data:
                return {}
            
            # Create lookup for mark prices data (for open interest if available)
            mark_prices_lookup = {}
            if mark_prices_data:
                if isinstance(mark_prices_data, list):
                    for mark_item in mark_prices_data:
                        symbol = mark_item.get('symbol', '')
                        if symbol:
                            mark_prices_lookup[symbol] = mark_item
                elif isinstance(mark_prices_data, dict):
                    # Single symbol response
                    symbol = mark_prices_data.get('symbol', '')
                    if symbol:
                        mark_prices_lookup[symbol] = mark_prices_data
            
            # Extract market data
            market_data = {}
            
            # Handle both single dict and list of dicts response for ticker
            if isinstance(ticker_data, dict):
                ticker_list = [ticker_data]
            elif isinstance(ticker_data, list):
                ticker_list = ticker_data
            else:
                return {}
            
            for ticker in ticker_list:
                try:
                    symbol = ticker.get('symbol', '')
                    
                    # Only process perpetual markets (ending with USDT)
                    if not symbol.endswith('USDT'):
                        continue
                    
                    # Normalize symbol
                    normalized_symbol = self.normalize_symbol(symbol)
                    
                    # Get volume (24h) - try multiple possible field names
                    volume_24h = (
                        ticker.get('volume') or 
                        ticker.get('baseVolume') or 
                        ticker.get('quoteVolume')
                    )
                    
                    # Get open interest from mark prices if available
                    mark_data = mark_prices_lookup.get(symbol, {})
                    open_interest = (
                        mark_data.get('openInterest') or 
                        mark_data.get('openInterestValue') or 
                        mark_data.get('open_interest')
                    )
                    
                    # Create market data entry
                    data = {}
                    
                    if volume_24h is not None:
                        data['volume_24h'] = Decimal(str(volume_24h))
                    
                    if open_interest is not None:
                        data['open_interest'] = Decimal(str(open_interest))
                    
                    if data:  # Only add if we have some data
                        market_data[normalized_symbol] = data
                
                except Exception:
                    # Skip invalid entries
                    continue
            
            return market_data
        
        except Exception:
            return {}

