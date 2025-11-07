"""
Data fetching logic for Paradex funding adapter.

Handles fetching funding rates and market data from Paradex API.
"""

import asyncio
from datetime import datetime, timezone
from typing import Dict, Optional, Callable
from decimal import Decimal

from exchange_clients.base_models import FundingRateSample


class ParadexFundingFetchers:
    """Handles data fetching from Paradex funding API."""

    def __init__(
        self,
        funding_client: 'ParadexFundingClient',
        timeout: int,
        normalize_symbol_fn: Callable[[str], str],
    ):
        """
        Initialize fetchers.
        
        Args:
            funding_client: ParadexFundingClient instance
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
        Fetch all funding rates from Paradex.
        
        Uses the /v1/funding/data endpoint with page_size=1 to get the most recent
        normalized 8h funding rate for each market. This provides current rates that
        are already normalized to 8-hour intervals.
        
        Note: The fetch_markets_summary() endpoint returns raw funding rates that are
        NOT normalized to 8h, so we use the funding/data endpoint instead.
        
        Args:
            canonical_interval_hours: Canonical funding interval (typically 8 hours)
        
        Returns:
            Dictionary mapping normalized symbols to FundingRateSample entries
            
        Raises:
            Exception: If fetching fails after retries
        """
        await self.funding_client.ensure_client()
        
        if self.funding_client.paradex is None:
            raise RuntimeError("Paradex client not initialized")
        
        try:
            # First, get all perpetual markets from markets summary
            paradex_client = self.funding_client.paradex
            markets_summary = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: paradex_client.api_client.fetch_markets_summary({"market": "ALL"})
            )
            
            if not markets_summary or 'results' not in markets_summary:
                return {}
            
            markets = markets_summary['results']
            
            if not markets:
                return {}
            
            # Extract all perpetual market symbols
            perpetual_markets = []
            for market in markets:
                market_symbol = market.get('market', '') or market.get('symbol', '') or market.get('name', '')
                if market_symbol and market_symbol.endswith('-USD-PERP'):
                    perpetual_markets.append(market_symbol)
            
            if not perpetual_markets:
                return {}
            
            # Fetch current normalized 8h funding rate for each market
            # Use funding/data endpoint with page_size=1 to get most recent entry
            rates_dict: Dict[str, FundingRateSample] = {}
            loop = asyncio.get_event_loop()
            
            # Fetch funding data for all markets concurrently
            async def fetch_single_market_funding(market_symbol: str) -> Optional[tuple[str, FundingRateSample]]:
                """Fetch funding rate for a single market."""
                try:
                    # Use api_client.get() to call /v1/funding/data endpoint
                    funding_data = await loop.run_in_executor(
                        None,
                        lambda: paradex_client.api_client.get(
                            paradex_client.api_client.api_url,
                            "funding/data",
                            params={"market": market_symbol, "page_size": 1}
                        )
                    )
                    
                    if not funding_data or 'results' not in funding_data:
                        return None
                    
                    results = funding_data['results']
                    if not results:
                        return None
                    
                    # Get the most recent entry (first result)
                    latest_entry = results[0]
                    
                    # Extract normalized 8h funding rate
                    funding_rate = latest_entry.get('funding_rate')
                    if funding_rate is None:
                        return None
                    
                    normalized_symbol = self.normalize_symbol(market_symbol)
                    funding_rate_decimal = Decimal(str(funding_rate))
                    
                    # Parse timestamp from funding data
                    next_funding_time = self.parse_next_funding_time(
                        latest_entry.get('created_at')
                    )
                    
                    sample = FundingRateSample(
                        normalized_rate=funding_rate_decimal,
                        raw_rate=funding_rate_decimal,
                        interval_hours=canonical_interval_hours,
                        next_funding_time=next_funding_time,
                        metadata={'symbol': market_symbol, 'market': market_symbol},
                    )
                    
                    return (normalized_symbol, sample)
                    
                except Exception:
                    return None
            
            # Fetch all markets concurrently (with reasonable concurrency limit)
            tasks = [fetch_single_market_funding(market) for market in perpetual_markets]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Process results
            for result in results:
                if isinstance(result, Exception):
                    continue
                if result is not None:
                    normalized_symbol, sample = result
                    rates_dict[normalized_symbol] = sample
            
            return rates_dict
        
        except Exception as e:
            raise

    async def fetch_market_data(self) -> Dict[str, Dict[str, Decimal]]:
        """
        Fetch complete market data (volume + OI) from Paradex.
        
        Uses fetch_markets() and fetch_markets_summary() endpoints to get
        volume and open interest data for all perpetual markets.
        
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
        
        if self.funding_client.paradex is None:
            raise RuntimeError("Paradex client not initialized")
        
        try:
            # Fetch markets info and summary in parallel
            # SDK is synchronous, so use run_in_executor
            paradex_client = self.funding_client.paradex
            loop = asyncio.get_event_loop()
            markets_info_task = loop.run_in_executor(
                None,
                lambda: paradex_client.api_client.fetch_markets()
            )
            # Note: Must pass {"market": "ALL"} to get all markets
            markets_summary_task = loop.run_in_executor(
                None,
                lambda: paradex_client.api_client.fetch_markets_summary({"market": "ALL"})
            )
            
            markets_info, markets_summary = await asyncio.gather(
                markets_info_task,
                markets_summary_task
            )
            
            if not markets_info or 'results' not in markets_info:
                return {}
            
            if not markets_summary or 'results' not in markets_summary:
                return {}
            
            # Create lookup for summary data
            summary_lookup = {}
            for market in markets_summary['results']:
                market_symbol = market.get('market', '')
                if market_symbol:
                    summary_lookup[market_symbol] = market
            
            # Parse response
            market_data = {}
            for market in markets_info['results']:
                try:
                    # Use 'symbol' field from markets, 'market' field from summary
                    market_symbol = market.get('symbol', '') or market.get('market', '')
                    
                    # Only process perpetual markets
                    if not market_symbol.endswith('-USD-PERP'):
                        continue
                    
                    # Normalize symbol
                    normalized_symbol = self.normalize_symbol(market_symbol)
                    
                    # Get volume from summary (24h volume)
                    summary_data = summary_lookup.get(market_symbol, {})
                    volume_24h = summary_data.get('volume_24h') or summary_data.get('volume')
                    
                    # Get open interest from market info
                    open_interest = market.get('open_interest') or market.get('open_interest_usd')
                    
                    # Create market data entry
                    data = {}
                    
                    if volume_24h is not None:
                        data['volume_24h'] = Decimal(str(volume_24h))
                    
                    if open_interest is not None:
                        data['open_interest'] = Decimal(str(open_interest))
                    
                    if data:  # Only add if we have some data
                        market_data[normalized_symbol] = data
                    
                except Exception as e:
                    continue
            
            return market_data
        
        except Exception as e:
            raise

