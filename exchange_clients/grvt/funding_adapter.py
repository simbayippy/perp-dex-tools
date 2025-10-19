"""
GRVT DEX Funding Rate Adapter

Fetches funding rates from GRVT using the GRVT CCXT SDK.
This is a read-only adapter (no trading) focused solely on data collection.
"""

from datetime import datetime, timezone
from typing import Dict, Optional
from decimal import Decimal
import re
import asyncio

from exchange_clients.base_funding_adapter import BaseFundingAdapter
from exchange_clients.base_models import FundingRateSample

# Import GRVT SDK
try:
    from pysdk.grvt_ccxt import GrvtCcxt
    from pysdk.grvt_ccxt_env import GrvtEnv
    GRVT_SDK_AVAILABLE = True
except ImportError:
    GRVT_SDK_AVAILABLE = False
    import logging
    # logging.warning("GRVT SDK not available. Install with: pip install grvt-pysdk")


class GrvtFundingAdapter(BaseFundingAdapter):
    """
    GRVT adapter for fetching funding rates
    
    This adapter uses the GRVT CCXT SDK to fetch funding rates
    for all available perpetual markets on GRVT.
    
    Key features:
    - Uses GRVT CCXT API (standard CCXT interface)
    - Normalizes symbols from GRVT format to standard format
    - No authentication required (public endpoint)
    - Returns all available funding rates
    
    Note: GRVT uses CCXT-compatible interface for public data
    """
    
    def __init__(
        self, 
        api_base_url: Optional[str] = None,
        environment: str = "prod",
        timeout: int = 10,
        max_concurrent_requests: int = 10
    ):
        """
        Initialize GRVT adapter
        
        Args:
            api_base_url: GRVT API base URL (optional, determined by environment)
            environment: "prod", "testnet", "staging", or "dev"
            timeout: Request timeout in seconds
            max_concurrent_requests: Maximum number of parallel ticker fetches (default: 10)
        """
        if not GRVT_SDK_AVAILABLE:
            raise ImportError(
                "GRVT SDK is required. Install with: pip install grvt-pysdk"
            )
        
        # Map environment to GRVT enum
        env_map = {
            'prod': GrvtEnv.PROD,
            'testnet': GrvtEnv.TESTNET,
            'staging': GrvtEnv.STAGING,
            'dev': GrvtEnv.DEV
        }
        self.env = env_map.get(environment.lower(), GrvtEnv.PROD)
        
        # Determine API URL if not provided
        if api_base_url is None:
            api_base_url = "https://trade.grvt.io"  # Default prod URL
        
        super().__init__(
            dex_name="grvt",
            api_base_url=api_base_url,
            timeout=timeout
        )
        
        self.environment = environment
        self.max_concurrent_requests = max_concurrent_requests
        
        # Initialize GRVT client (read-only, minimal config)
        # For public data, we don't need credentials
        self.rest_client = GrvtCcxt(
            env=self.env,
            parameters={}  # No auth needed for public endpoints
        )
    
    async def fetch_funding_rates(self) -> Dict[str, FundingRateSample]:
        """
        Fetch all funding rates from GRVT (with parallel fetching)
        
        GRVT provides funding rates through ticker data for each market.
        We fetch all perpetual markets then query tickers in PARALLEL for speed.
        
        Returns:
            Dictionary mapping normalized symbols to FundingRateSample entries
            Example: {"BTC": Decimal("0.0001"), "ETH": Decimal("0.00008")}
            
        Raises:
            Exception: If fetching fails after retries
        """
        try:
            # Fetch all markets to get perpetuals
            markets = self.rest_client.fetch_markets()
            
            if not markets:
                return {}
            
            # Filter for USDT perpetuals and prepare tasks
            perpetual_markets = []
            for market in markets:
                if market.get('kind') == 'PERPETUAL' and market.get('quote') == 'USDT':
                    perpetual_markets.append(market)
            
            if not perpetual_markets:
                return {}
            
            # Create semaphore to limit concurrent requests
            semaphore = asyncio.Semaphore(self.max_concurrent_requests)
            
            # Fetch all tickers in parallel using asyncio with concurrency limit
            tasks = [
                self._fetch_single_ticker(
                    market.get('instrument', ''),
                    market.get('base', ''),
                    semaphore
                )
                for market in perpetual_markets
            ]
            
            # Execute all tasks in parallel (but limited by semaphore)
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Collect successful results
            rates_dict: Dict[str, FundingRateSample] = {}
            for result in results:
                if isinstance(result, Exception):
                    continue
                elif result is not None:
                    symbol, sample = result
                    rates_dict[symbol] = sample
            
            return rates_dict
        
        except Exception as e:
            raise
    
    @staticmethod
    def _parse_timestamp(value: Optional[object]) -> Optional[datetime]:
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

    async def _fetch_single_ticker(
        self,
        instrument: str,
        base: str,
        semaphore: asyncio.Semaphore
    ) -> Optional[tuple[str, FundingRateSample]]:
        """
        Fetch funding rate for a single instrument (with concurrency limit)
        
        Args:
            instrument: Instrument name (e.g., "BTC_USDT_Perp")
            base: Base currency (e.g., "BTC")
            semaphore: Asyncio semaphore to limit concurrent requests
            
        Returns:
            Tuple of (normalized_symbol, FundingRateSample) or None if failed
        """
        async with semaphore:
            try:
                # Run the synchronous fetch_ticker in a thread pool
                loop = asyncio.get_event_loop()
                
                ticker_response = await loop.run_in_executor(
                    None, 
                    self.rest_client.fetch_ticker, 
                    instrument
                )
                
                ticker = ticker_response
                
                # Get funding_rate_8h_curr (8-hour funding rate)
                funding_rate_8h = ticker.get('funding_rate_8h_curr')
                
                if funding_rate_8h is None:
                    return None
                
                raw_rate = Decimal(str(funding_rate_8h)) / Decimal('100')
                normalized_symbol = self.normalize_symbol(base)
                next_funding_time = self._parse_timestamp(
                    ticker.get('next_funding_time') or ticker.get('nextFundingTime')
                )
                sample = FundingRateSample(
                    normalized_rate=raw_rate,
                    raw_rate=raw_rate,
                    interval_hours=self.CANONICAL_INTERVAL_HOURS,
                    next_funding_time=next_funding_time,
                    metadata={'instrument': instrument},
                )
                
                return (normalized_symbol, sample)
            
            except Exception as e:
                return None
    
    async def fetch_market_data(self) -> Dict[str, Dict[str, Decimal]]:
        """
        Fetch market data (volume, OI) from GRVT (with parallel fetching)
        
        Returns:
            Dictionary mapping normalized symbols to market data
            Example: {
                "BTC": {
                    "volume_24h": Decimal("1500000.0"),
                    "open_interest": Decimal("5000000.0")
                }
            }
        """
        try:
            # Fetch all markets to get perpetuals
            markets = self.rest_client.fetch_markets()
            
            if not markets:
                return {}
            
            # Filter for USDT perpetuals
            perpetual_markets = []
            for market in markets:
                if market.get('kind') == 'PERPETUAL' and market.get('quote') == 'USDT':
                    perpetual_markets.append(market)
            
            if not perpetual_markets:
                return {}
            
            # Create semaphore to limit concurrent requests
            semaphore = asyncio.Semaphore(self.max_concurrent_requests)
            
            # Fetch all tickers in parallel
            tasks = [
                self._fetch_single_market_data(
                    market.get('instrument', ''),
                    market.get('base', ''),
                    semaphore
                )
                for market in perpetual_markets
            ]
            
            # Execute all tasks in parallel
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Collect successful results
            market_data_dict = {}
            for result in results:
                if isinstance(result, Exception):
                    continue
                elif result is not None:
                    symbol, data = result
                    market_data_dict[symbol] = data
            
            return market_data_dict
        
        except Exception as e:
            raise
    
    async def _fetch_single_market_data(
        self, 
        instrument: str, 
        base: str,
        semaphore: asyncio.Semaphore
    ) -> Optional[tuple[str, Dict[str, Decimal]]]:
        """Fetch market data for a single instrument"""
        async with semaphore:
            try:
                loop = asyncio.get_event_loop()
                
                ticker_response = await loop.run_in_executor(
                    None, 
                    self.rest_client.fetch_ticker, 
                    instrument
                )
                
                ticker = ticker_response
                
                # Extract open interest and volume
                open_interest_contracts = ticker.get('open_interest')
                mark_price = ticker.get('mark_price')
                
                if open_interest_contracts is None or mark_price is None:
                    open_interest_usd = None
                else:
                    open_interest_usd = Decimal(str(open_interest_contracts)) * Decimal(str(mark_price))
                
                buy_volume_q = ticker.get('buy_volume_24h_q', '0')
                sell_volume_q = ticker.get('sell_volume_24h_q', '0')
                volume_24h = Decimal(str(buy_volume_q)) + Decimal(str(sell_volume_q))
                
                normalized_symbol = self.normalize_symbol(base)
                
                market_data = {
                    "volume_24h": volume_24h,
                    "open_interest": open_interest_usd
                }
                
                return (normalized_symbol, market_data)
            
            except Exception as e:
                return None
    
    def normalize_symbol(self, dex_symbol: str) -> str:
        """
        Normalize GRVT symbol format to standard format
        
        GRVT typically uses clean base symbols like "BTC", "ETH", etc.
        
        Args:
            dex_symbol: GRVT-specific symbol format
            
        Returns:
            Normalized symbol (e.g., "BTC")
        """
        normalized = dex_symbol.upper()
        
        # Remove common suffixes
        normalized = normalized.replace('_PERP', '')
        normalized = normalized.replace('PERP', '')
        normalized = normalized.replace('_USDT', '')
        normalized = normalized.replace('USDT', '')
        
        # Handle multipliers
        match = re.match(r'^(\d+)([A-Z]+)$', normalized)
        if match:
            _, symbol = match.groups()
            normalized = symbol
        
        normalized = normalized.strip('-_/')
        
        return normalized
    
    def get_dex_symbol_format(self, normalized_symbol: str) -> str:
        """
        Convert normalized symbol back to GRVT-specific format
        
        Args:
            normalized_symbol: Normalized symbol (e.g., "BTC")
            
        Returns:
            GRVT-specific format
        """
        return normalized_symbol.upper()
    
    async def close(self) -> None:
        """Close the API client"""
        await super().close()
