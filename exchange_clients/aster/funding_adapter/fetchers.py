"""
Data fetching logic for Aster funding adapter.

Handles fetching funding rates and market data from Aster API.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Callable, List
from decimal import Decimal, InvalidOperation

from exchange_clients.base_models import FundingRateSample


class AsterFundingFetchers:
    """Handles data fetching from Aster funding API."""

    # Open Interest multiplier for two-sided calculation
    # Aster API returns one-sided OI (base currency), but total OI
    # shown on their website is long + short (two-sided), hence × 2
    OI_TWO_SIDED_MULTIPLIER = 2
    
    # Concurrency limit for OI fetching (optimal based on testing)
    OI_FETCH_CONCURRENCY = 10

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

    async def _fetch_oi_for_symbol(
        self,
        aster_client,
        symbol: str,
        semaphore: asyncio.Semaphore
    ) -> Optional[Dict[str, Decimal]]:
        """
        Fetch OI for a single symbol.
        
        Args:
            aster_client: Aster SDK client instance
            symbol: Symbol to fetch OI for (e.g., "BTCUSDT")
            semaphore: Semaphore for rate limiting
            
        Returns:
            Dictionary with 'open_interest_base' (base currency) or None if failed
        """
        async with semaphore:
            try:
                # Aster SDK is synchronous, so run in executor
                loop = asyncio.get_event_loop()
                response = await loop.run_in_executor(
                    None,
                    lambda: aster_client.query("/fapi/v1/openInterest", {"symbol": symbol})
                )
                
                if isinstance(response, dict) and "openInterest" in response:
                    oi_base = Decimal(str(response["openInterest"]))
                    return {"open_interest_base": oi_base}
            except Exception:
                # Silently fail - some symbols may not have OI data
                return None
        return None

    async def _fetch_all_oi(
        self,
        aster_client,
        symbols: List[str],
        mark_prices_lookup: Dict[str, Dict]
    ) -> Dict[str, Decimal]:
        """
        Fetch OI for all symbols concurrently and convert to USD.
        
        Args:
            aster_client: Aster SDK client instance
            symbols: List of symbols to fetch OI for
            mark_prices_lookup: Dictionary mapping symbol to mark price data
            
        Returns:
            Dictionary mapping normalized symbol to OI in USD (two-sided)
        """
        semaphore = asyncio.Semaphore(self.OI_FETCH_CONCURRENCY)
        
        # Fetch OI for all symbols concurrently
        tasks = [
            self._fetch_oi_for_symbol(aster_client, symbol, semaphore)
            for symbol in symbols
        ]
        oi_results = await asyncio.gather(*tasks)
        
        # Convert to USD and apply two-sided multiplier
        oi_usd_dict: Dict[str, Decimal] = {}
        
        for symbol, oi_result in zip(symbols, oi_results):
            if oi_result is None:
                continue
                
            oi_base = oi_result.get("open_interest_base")
            if oi_base is None:
                continue
            
            # Get mark price for USD conversion
            mark_data = mark_prices_lookup.get(symbol, {})
            mark_price = mark_data.get("markPrice") or mark_data.get("mark_price")
            
            if mark_price is None:
                continue
            
            try:
                mark_price_decimal = Decimal(str(mark_price))
                
                # Convert base currency OI to USD (one-sided)
                one_sided_oi_usd = oi_base * mark_price_decimal
                
                # Convert to two-sided OI (long + short)
                two_sided_oi_usd = one_sided_oi_usd * self.OI_TWO_SIDED_MULTIPLIER
                
                normalized_symbol = self.normalize_symbol(symbol)
                oi_usd_dict[normalized_symbol] = two_sided_oi_usd
                
            except (ValueError, TypeError, InvalidOperation):
                # Skip invalid conversions
                continue
        
        return oi_usd_dict

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
            # Fetch 24hr ticker data for volume and mark prices for OI conversion
            ticker_data = aster_client.ticker_24hr_price_change()
            mark_prices_data = aster_client.mark_price()
            
            if not ticker_data:
                return {}
            
            # Create lookup for mark prices data (needed for OI USD conversion)
            mark_prices_lookup: Dict[str, Dict] = {}
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
            
            # Extract symbols from ticker data for OI fetching
            if isinstance(ticker_data, dict):
                ticker_list = [ticker_data]
            elif isinstance(ticker_data, list):
                ticker_list = ticker_data
            else:
                return {}
            
            # Collect all USDT symbols for OI fetching
            symbols_for_oi = []
            for ticker in ticker_list:
                symbol = ticker.get('symbol', '')
                if symbol.endswith('USDT'):
                    symbols_for_oi.append(symbol)
            
            # Fetch OI for all symbols concurrently
            oi_usd_dict = await self._fetch_all_oi(
                aster_client,
                symbols_for_oi,
                mark_prices_lookup
            )
            
            # Extract market data from ticker and merge with OI
            market_data = {}
            
            for ticker in ticker_list:
                try:
                    symbol = ticker.get('symbol', '')
                    
                    # Only process perpetual markets (ending with USDT)
                    if not symbol.endswith('USDT'):
                        continue
                    
                    # Normalize symbol
                    normalized_symbol = self.normalize_symbol(symbol)
                    
                    # Get volume (24h) - try multiple possible field names
                    # According to API docs: 'volume' is base asset volume, 'quoteVolume' is quote asset volume (USD)
                    volume_24h = (
                        ticker.get('quoteVolume') or  # Prefer quoteVolume (USD) over base volume
                        ticker.get('volume') or 
                        ticker.get('baseVolume')
                    )
                    
                    # Get OI from fetched data
                    open_interest_usd = oi_usd_dict.get(normalized_symbol)
                    
                    # Create market data entry
                    data = {}
                    
                    if volume_24h is not None:
                        data['volume_24h'] = Decimal(str(volume_24h))
                    
                    if open_interest_usd is not None:
                        data['open_interest'] = open_interest_usd
                    
                    if data:  # Only add if we have some data
                        market_data[normalized_symbol] = data
                
                except Exception:
                    # Skip invalid entries
                    continue
            
            return market_data
        
        except Exception:
            return {}

