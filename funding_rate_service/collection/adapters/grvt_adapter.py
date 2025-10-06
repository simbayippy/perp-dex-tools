"""
GRVT DEX Adapter

Fetches funding rates from GRVT using the GRVT CCXT SDK.
This is a read-only adapter (no trading) focused solely on data collection.
"""

from typing import Dict, Optional
from decimal import Decimal
import re

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
        timeout: int = 10
    ):
        """
        Initialize GRVT adapter
        
        Args:
            api_base_url: GRVT API base URL (optional, determined by environment)
            environment: "prod", "testnet", "staging", or "dev"
            timeout: Request timeout in seconds
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
        
        # Initialize GRVT client (read-only, minimal config)
        # For public data, we don't need credentials
        self.rest_client = GrvtCcxt(
            env=self.env,
            parameters={}  # No auth needed for public endpoints
        )
        
        logger.info(f"GRVT adapter initialized ({environment})")
    
    async def fetch_funding_rates(self) -> Dict[str, Decimal]:
        """
        Fetch all funding rates from GRVT
        
        GRVT provides funding rates through ticker data for each market.
        We fetch all perpetual markets then query ticker for each to get funding rate.
        
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
            
            # Extract funding rates from perpetual markets
            rates_dict = {}
            for market in markets:
                try:
                    # Only process perpetual markets
                    if market.get('kind') != 'PERPETUAL':
                        continue
                    
                    instrument = market.get('instrument', '')
                    base = market.get('base', '')
                    quote = market.get('quote', '')
                    
                    # Skip if not USDT perpetual
                    if quote != 'USDT':
                        continue
                    
                    # Fetch ticker data which includes funding rate
                    # GRVT returns funding_rate_curr in ticker response
                    try:
                        ticker = self.rest_client.fetch_ticker(instrument)
                        
                        # Get funding_rate_curr (current funding rate)
                        # This is typically in basis points or scaled format
                        funding_rate_curr = ticker.get('funding_rate_curr')
                        
                        if funding_rate_curr is None:
                            logger.debug(
                                f"{self.dex_name}: No funding rate for {instrument}"
                            )
                            continue
                        
                        # Convert from scaled value to decimal rate
                        # GRVT uses basis points: divide by 1,000,000
                        # (1 basis point = 0.0001, so 10000 basis points = 0.01 = 1%)
                        funding_rate = Decimal(str(funding_rate_curr)) / Decimal('1000000')
                        
                        # Use base symbol directly (already clean)
                        normalized_symbol = self.normalize_symbol(base)
                        
                        rates_dict[normalized_symbol] = funding_rate
                        
                        logger.debug(
                            f"{self.dex_name}: {instrument} ({base}) -> "
                            f"{normalized_symbol}: {funding_rate} "
                            f"(raw: {funding_rate_curr})"
                        )
                    
                    except Exception as e:
                        # If ticker fetch fails for this market, log and continue
                        logger.debug(
                            f"{self.dex_name}: Could not fetch ticker for {instrument}: {e}"
                        )
                        continue
                
                except Exception as e:
                    logger.error(
                        f"{self.dex_name}: Error processing market "
                        f"{market.get('instrument', 'unknown')}: {e}"
                    )
                    continue
            
            logger.info(
                f"{self.dex_name}: Successfully fetched {len(rates_dict)} funding rates"
            )
            
            return rates_dict
        
        except Exception as e:
            logger.error(f"{self.dex_name}: Failed to fetch funding rates: {e}")
            raise
    
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

