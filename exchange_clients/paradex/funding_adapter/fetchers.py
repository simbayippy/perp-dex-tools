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
        
        Uses the fetch_markets_summary() endpoint which includes funding rate
        information for each perpetual market.
        
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
            # Fetch markets summary which includes funding rates
            # SDK is synchronous, so use run_in_executor
            paradex_client = self.funding_client.paradex
            markets_summary = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: paradex_client.api_client.fetch_markets_summary()
            )
            
            if not markets_summary or 'results' not in markets_summary:
                return {}
            
            markets = markets_summary['results']
            
            if not markets:
                return {}
            
            # Convert to our format
            rates_dict: Dict[str, FundingRateSample] = {}
            for market in markets:
                try:
                    market_symbol = market.get('market', '')
                    
                    # Only process perpetual markets (ending with -USD-PERP)
                    if not market_symbol.endswith('-USD-PERP'):
                        continue
                    
                    # Get funding rate - check multiple possible fields
                    funding_rate = market.get('funding_rate') or market.get('funding_rate_8h')
                    
                    if funding_rate is None:
                        continue
                    
                    # Normalize symbol (e.g., "BTC-USD-PERP" -> "BTC")
                    normalized_symbol = self.normalize_symbol(market_symbol)
                    
                    # Convert to Decimal
                    funding_rate_decimal = Decimal(str(funding_rate))
                    
                    # Parse next funding time if available
                    next_funding_time = self.parse_next_funding_time(
                        market.get('next_funding_time')
                    )
                    
                    rates_dict[normalized_symbol] = FundingRateSample(
                        normalized_rate=funding_rate_decimal,
                        raw_rate=funding_rate_decimal,
                        interval_hours=canonical_interval_hours,
                        next_funding_time=next_funding_time,
                        metadata={'symbol': market_symbol, 'market': market_symbol},
                    )
                
                except Exception as e:
                    continue
            
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
            markets_summary_task = loop.run_in_executor(
                None,
                lambda: paradex_client.api_client.fetch_markets_summary()
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

