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
        
        Uses fetch_markets() to get market metadata (including funding_period_hours)
        and fetch_markets_summary() to get current funding rates.
        Normalizes rates to canonical 8h interval using each market's funding_period_hours.
        
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
            # Fetch markets (for funding_period_hours) and markets_summary (for current rates) in parallel
            # SDK is synchronous, so use run_in_executor
            paradex_client = self.funding_client.paradex
            loop = asyncio.get_event_loop()
            markets_task = loop.run_in_executor(
                None,
                lambda: paradex_client.api_client.fetch_markets()
            )
            markets_summary_task = loop.run_in_executor(
                None,
                lambda: paradex_client.api_client.fetch_markets_summary({"market": "ALL"})
            )
            
            markets_info, markets_summary = await asyncio.gather(
                markets_task,
                markets_summary_task
            )
            
            if not markets_info or 'results' not in markets_info:
                return {}
            
            if not markets_summary or 'results' not in markets_summary:
                return {}
            
            # Build lookup for market metadata (funding_period_hours)
            markets_lookup = {}
            for market in markets_info['results']:
                market_symbol = market.get('symbol', '')
                if market_symbol and market_symbol.endswith('-USD-PERP'):
                    funding_period_hours = market.get('funding_period_hours')
                    if funding_period_hours:
                        markets_lookup[market_symbol] = Decimal(str(funding_period_hours))
            
            # Build lookup for summary data (current funding rates)
            summary_lookup = {}
            for market in markets_summary['results']:
                market_symbol = market.get('market', '') or market.get('symbol', '')
                if market_symbol:
                    summary_lookup[market_symbol] = market
            
            # Convert to our format
            rates_dict: Dict[str, FundingRateSample] = {}
            for market_symbol, summary_data in summary_lookup.items():
                try:
                    # Only process perpetual markets (ending with -USD-PERP)
                    if not market_symbol.endswith('-USD-PERP'):
                        continue
                    
                    # Get funding rate from summary
                    funding_rate = summary_data.get('funding_rate')
                    
                    if funding_rate is None:
                        continue
                    
                    # Get funding_period_hours from markets metadata
                    market_interval_hours = markets_lookup.get(market_symbol, canonical_interval_hours)
                    
                    # Normalize symbol (e.g., "BTC-USD-PERP" -> "BTC")
                    normalized_symbol = self.normalize_symbol(market_symbol)
                    
                    # Convert to Decimal
                    raw_rate = Decimal(str(funding_rate))
                    
                    # Normalize rate to canonical interval (8h)
                    # Formula: normalized_rate = raw_rate * (canonical_interval / market_interval)
                    if market_interval_hours > 0:
                        normalized_rate = raw_rate * (canonical_interval_hours / market_interval_hours)
                    else:
                        normalized_rate = raw_rate
                    
                    # Parse next funding time if available
                    next_funding_time = self.parse_next_funding_time(
                        summary_data.get('next_funding_time')
                    )
                    
                    rates_dict[normalized_symbol] = FundingRateSample(
                        normalized_rate=normalized_rate,
                        raw_rate=raw_rate,
                        interval_hours=market_interval_hours,
                        next_funding_time=next_funding_time,
                        metadata={
                            'symbol': market_symbol,
                            'market': market_symbol,
                            'funding_period_hours': str(market_interval_hours),
                        },
                    )
                
                except Exception as e:
                    continue
            
            return rates_dict
        
        except Exception as e:
            raise

    async def fetch_market_data(self) -> Dict[str, Dict[str, Decimal]]:
        """
        Fetch complete market data (volume + OI) from Paradex.
        
        Uses fetch_markets_summary() endpoint to get volume and open interest
        data for all perpetual markets.
        
        Note: Open interest is returned in base currency by the API, so we convert
        it to USD by multiplying by mark_price (similar to Lighter adapter).
        
        Returns:
            Dictionary mapping normalized symbols to market data
            Example: {
                "BTC": {
                    "volume_24h": Decimal("1500000.0"),  # Already in USD
                    "open_interest": Decimal("5000000.0")  # Converted to USD
                }
            }
            
        Raises:
            Exception: If fetching fails after retries
        """
        await self.funding_client.ensure_client()
        
        if self.funding_client.paradex is None:
            raise RuntimeError("Paradex client not initialized")
        
        try:
            # Fetch markets_summary which includes volume and open_interest
            # SDK is synchronous, so use run_in_executor
            # Note: Must pass {"market": "ALL"} to get all markets
            paradex_client = self.funding_client.paradex
            markets_summary = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: paradex_client.api_client.fetch_markets_summary({"market": "ALL"})
            )
            
            if not markets_summary or 'results' not in markets_summary:
                return {}
            
            # Parse response
            market_data = {}
            for market in markets_summary['results']:
                try:
                    market_symbol = market.get('market', '') or market.get('symbol', '')
                    
                    # Only process perpetual markets
                    if not market_symbol or not market_symbol.endswith('-USD-PERP'):
                        continue
                    
                    # Normalize symbol
                    normalized_symbol = self.normalize_symbol(market_symbol)
                    
                    # Get volume (already in USD)
                    volume_24h = market.get('volume_24h') or market.get('volume') or market.get('total_volume')
                    
                    # Get open interest - API returns in base currency, convert to USD
                    # Check if already in USD first
                    open_interest_usd = market.get('open_interest_usd')
                    if open_interest_usd is None:
                        # Convert from base currency to USD using mark_price
                        open_interest_base = market.get('open_interest')
                        mark_price = market.get('mark_price') or market.get('last_traded_price')
                        
                        if open_interest_base is not None and mark_price is not None:
                            try:
                                open_interest_usd = Decimal(str(open_interest_base)) * Decimal(str(mark_price))
                            except (ValueError, TypeError):
                                open_interest_usd = None
                        else:
                            open_interest_usd = None
                    
                    # Create market data entry
                    data = {}
                    
                    if volume_24h is not None:
                        data['volume_24h'] = Decimal(str(volume_24h))
                    
                    if open_interest_usd is not None:
                        data['open_interest'] = open_interest_usd
                    
                    if data:  # Only add if we have some data
                        market_data[normalized_symbol] = data
                    
                except Exception as e:
                    continue
            
            return market_data
        
        except Exception as e:
            raise

