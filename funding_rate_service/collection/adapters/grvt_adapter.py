"""
GRVT DEX Adapter

Fetches funding rates from GRVT using the GRVT CCXT SDK.
This is a read-only adapter (no trading) focused solely on data collection.
"""

from typing import Dict, Optional
from decimal import Decimal
import re
import asyncio

from collection.base_adapter import BaseDEXAdapter
from utils.logger import logger

# Import GRVT SDK
try:
    from pysdk.grvt_ccxt import GrvtCcxt
    from pysdk.grvt_ccxt_env import GrvtEnv
    GRVT_SDK_AVAILABLE = True
except ImportError:
    GRVT_SDK_AVAILABLE = False
    logger.warning("GRVT SDK not available. Install with: pip install grvt-pysdk")


class GrvtAdapter(BaseDEXAdapter):
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
        
        logger.info(
            f"GRVT adapter initialized ({environment}, "
            f"max_concurrent={max_concurrent_requests})"
        )
    
    async def fetch_funding_rates(self) -> Dict[str, Decimal]:
        """
        Fetch all funding rates from GRVT (with parallel fetching)
        
        GRVT provides funding rates through ticker data for each market.
        We fetch all perpetual markets then query tickers in PARALLEL for speed.
        
        Returns:
            Dictionary mapping normalized symbols to funding rates
            Example: {"BTC": Decimal("0.0001"), "ETH": Decimal("0.00008")}
            
        Raises:
            Exception: If fetching fails after retries
        """
        try:
            logger.debug(f"{self.dex_name}: Fetching funding rates...")
            
            # Fetch all markets to get perpetuals
            markets = self.rest_client.fetch_markets()
            
            if not markets:
                logger.warning(f"{self.dex_name}: No markets data returned")
                return {}
            
            # Filter for USDT perpetuals and prepare tasks
            perpetual_markets = []
            for market in markets:
                if market.get('kind') == 'PERPETUAL' and market.get('quote') == 'USDT':
                    perpetual_markets.append(market)
            
            if not perpetual_markets:
                logger.warning(f"{self.dex_name}: No USDT perpetuals found")
                return {}
            
            logger.info(
                f"{self.dex_name}: Found {len(perpetual_markets)} USDT perpetuals, "
                f"fetching tickers in parallel (max {self.max_concurrent_requests} concurrent)..."
            )
            
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
            rates_dict = {}
            successful = 0
            failed = 0
            
            for result in results:
                if isinstance(result, Exception):
                    failed += 1
                    logger.debug(f"{self.dex_name}: Ticker fetch failed: {result}")
                elif result is not None:
                    symbol, rate = result
                    rates_dict[symbol] = rate
                    successful += 1
                else:
                    failed += 1
            
            logger.info(
                f"{self.dex_name}: Successfully fetched {successful} funding rates "
                f"({failed} failed) from {len(perpetual_markets)} markets"
            )
            
            return rates_dict
        
        except Exception as e:
            logger.error(f"{self.dex_name}: Failed to fetch funding rates: {e}")
            raise
    
    async def _fetch_single_ticker(
        self, 
        instrument: str, 
        base: str,
        semaphore: asyncio.Semaphore
    ) -> Optional[tuple[str, Decimal]]:
        """
        Fetch funding rate for a single instrument (with concurrency limit)
        
        This method runs in parallel with other ticker fetches, but respects
        the semaphore limit to avoid overwhelming the API or system.
        
        Args:
            instrument: Instrument name (e.g., "BTC_USDT_Perp")
            base: Base currency (e.g., "BTC")
            semaphore: Asyncio semaphore to limit concurrent requests
            
        Returns:
            Tuple of (normalized_symbol, funding_rate) or None if failed
        """
        async with semaphore:  # Limit concurrent requests
            try:
                # Run the synchronous fetch_ticker in a thread pool
                # to avoid blocking the event loop
                loop = asyncio.get_event_loop()
                
                logger.debug(f"{self.dex_name}: Fetching ticker for {instrument}...")
                
                ticker_response = await loop.run_in_executor(
                    None, 
                    self.rest_client.fetch_ticker, 
                    instrument
                )
                
                logger.debug(f"{self.dex_name}: Got response type: {type(ticker_response)}, keys: {ticker_response.keys() if isinstance(ticker_response, dict) else 'N/A'}")
                
                # GRVT returns data nested under 'result' key
                ticker = ticker_response.get('result', {})
                
                logger.debug(f"{self.dex_name}: Ticker data type: {type(ticker)}, has funding_rate_8h_curr: {'funding_rate_8h_curr' in ticker if isinstance(ticker, dict) else 'N/A'}")
                
                # Get funding_rate_8h_curr (8-hour funding rate)
                funding_rate_8h = ticker.get('funding_rate_8h_curr')
                
                logger.debug(f"{self.dex_name}: funding_rate_8h value: {funding_rate_8h} (type: {type(funding_rate_8h)})")
                
                if funding_rate_8h is None:
                    logger.warning(
                        f"{self.dex_name}: No funding rate for {instrument}, ticker keys: {list(ticker.keys()) if isinstance(ticker, dict) else 'N/A'}"
                    )
                    return None
                
                # Convert from percentage string to decimal rate
                # GRVT returns as percentage: '0.1248' = 0.1248% = 0.001248 as decimal
                funding_rate = Decimal(str(funding_rate_8h)) / Decimal('100')
                
                # Normalize symbol
                normalized_symbol = self.normalize_symbol(base)
                
                logger.info(
                    f"{self.dex_name}: SUCCESS {instrument} ({base}) -> "
                    f"{normalized_symbol}: {funding_rate} (raw: {funding_rate_8h}%)"
                )
                
                return (normalized_symbol, funding_rate)
            
            except Exception as e:
                logger.error(
                    f"{self.dex_name}: EXCEPTION for {instrument}: {type(e).__name__}: {e}",
                    exc_info=True  # This will log the full traceback
                )
                return None
    
    def normalize_symbol(self, dex_symbol: str) -> str:
        """
        Normalize GRVT symbol format to standard format
        
        GRVT typically uses clean base symbols like "BTC", "ETH", etc.
        But we still normalize to handle any edge cases.
        
        Args:
            dex_symbol: GRVT-specific symbol format
            
        Returns:
            Normalized symbol (e.g., "BTC")
        """
        normalized = dex_symbol.upper()
        
        # Remove common suffixes (just in case)
        normalized = normalized.replace('_PERP', '')
        normalized = normalized.replace('PERP', '')
        normalized = normalized.replace('_USDT', '')
        normalized = normalized.replace('USDT', '')
        
        # Handle multipliers (e.g., "1000PEPE" -> "PEPE")
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
        Convert normalized symbol back to GRVT-specific format
        
        Args:
            normalized_symbol: Normalized symbol (e.g., "BTC")
            
        Returns:
            GRVT-specific format
            
        Note: GRVT instrument names are like "BTC_USDT_Perp" but the base
        symbol is just "BTC". We return the base symbol.
        """
        # GRVT uses clean base symbols
        return normalized_symbol.upper()
    
    async def close(self) -> None:
        """Close the API client"""
        # GRVT SDK doesn't require explicit cleanup for REST client
        logger.debug(f"{self.dex_name}: Adapter closed")
        await super().close()


# Example usage (for testing)
async def test_grvt_adapter():
    """Test the GRVT adapter"""
    adapter = GrvtAdapter()
    
    try:
        rates, latency_ms = await adapter.fetch_with_metrics()
        
        print(f"\n✅ GRVT Adapter Test")
        print(f"Latency: {latency_ms}ms")
        print(f"Fetched {len(rates)} rates:\n")
        
        for symbol, rate in sorted(rates.items())[:10]:  # Show first 10
            annualized_apy = float(rate) * 365 * 3 * 100  # Assuming 8h periods
            print(f"  {symbol:10s}: {rate:>12} ({annualized_apy:>8.2f}% APY)")
        
        if len(rates) > 10:
            print(f"  ... and {len(rates) - 10} more")
    
    except Exception as e:
        print(f"\n❌ GRVT Adapter Test Failed: {e}")
        raise
    
    finally:
        await adapter.close()


if __name__ == "__main__":
    import asyncio
    asyncio.run(test_grvt_adapter())

