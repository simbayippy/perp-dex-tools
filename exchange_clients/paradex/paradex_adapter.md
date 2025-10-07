# THIS CANT BE RUN DUE TO DEPENDENCE ISSUES
"""
Paradex DEX Adapter

Fetches funding rates from Paradex using the official Paradex Python SDK.
This is a read-only adapter (no trading) focused solely on data collection.
"""

from typing import Dict, Optional
from decimal import Decimal
import re

from collection.base_adapter import BaseDEXAdapter
from utils.logger import logger

# Import Paradex SDK
try:
    from paradex_py import Paradex
    from paradex_py.environment import PROD, TESTNET
    PARADEX_SDK_AVAILABLE = True
except ImportError:
    PARADEX_SDK_AVAILABLE = False
    logger.warning("Paradex SDK not available. Install with: pip install paradex-py")


class ParadexAdapter(BaseDEXAdapter):
    """
    Paradex adapter for fetching funding rates
    
    This adapter uses the official Paradex Python SDK to fetch funding rates
    for all available perpetual markets on Paradex.
    
    Key features:
    - Uses Paradex API to fetch funding rates
    - Normalizes symbols from Paradex format to standard format
    - No authentication required (public endpoint)
    - Returns all available funding rates
    """
    
    def __init__(
        self, 
        api_base_url: Optional[str] = None,
        environment: str = "prod",
        timeout: int = 10
    ):
        """
        Initialize Paradex adapter
        
        Args:
            api_base_url: Paradex API base URL (optional, determined by environment)
            environment: "prod" or "testnet"
            timeout: Request timeout in seconds
        """
        if not PARADEX_SDK_AVAILABLE:
            raise ImportError(
                "Paradex SDK is required. Install with: pip install paradex-py"
            )
        
        # Determine API URL based on environment
        if api_base_url is None:
            env_map = {
                'prod': 'https://api.prod.paradex.trade/v1',
                'testnet': 'https://api.testnet.paradex.trade/v1'
            }
            api_base_url = env_map.get(environment.lower(), env_map['prod'])
        
        super().__init__(
            dex_name="paradex",
            api_base_url=api_base_url,
            timeout=timeout
        )
        
        self.environment = environment
        
        # Initialize Paradex client (read-only, no credentials needed)
        env = PROD if environment.lower() == 'prod' else TESTNET
        self.paradex = Paradex(env=env, logger=None)
        
        logger.info(f"Paradex adapter initialized ({environment})")
    
    async def fetch_funding_rates(self) -> Dict[str, Decimal]:
        """
        Fetch all funding rates from Paradex
        
        Paradex provides funding rates through their markets summary endpoint
        which includes funding rate information for each perpetual market.
        
        Returns:
            Dictionary mapping normalized symbols to funding rates
            Example: {"BTC": Decimal("0.0001"), "ETH": Decimal("0.00008")}
            
        Raises:
            Exception: If fetching fails after retries
        """
        try:
            logger.debug(f"{self.dex_name}: Fetching funding rates...")
            
            # Fetch markets summary which includes funding rates
            markets_summary = self.paradex.api_client.fetch_markets_summary()
            
            if not markets_summary or 'results' not in markets_summary:
                logger.warning(f"{self.dex_name}: No markets data returned")
                return {}
            
            markets = markets_summary['results']
            
            if not markets:
                logger.warning(f"{self.dex_name}: No markets found")
                return {}
            
            # Extract funding rates
            rates_dict = {}
            for market in markets:
                try:
                    market_symbol = market.get('market', '')
                    
                    # Only process perpetual markets (ending with -USD-PERP)
                    if not market_symbol.endswith('-USD-PERP'):
                        continue
                    
                    # Get funding rate
                    # Paradex provides funding_rate in the market summary
                    funding_rate = market.get('funding_rate')
                    
                    if funding_rate is None:
                        logger.debug(
                            f"{self.dex_name}: No funding rate for {market_symbol}"
                        )
                        continue
                    
                    # Normalize symbol (e.g., "BTC-USD-PERP" -> "BTC")
                    normalized_symbol = self.normalize_symbol(market_symbol)
                    
                    # Convert to Decimal
                    funding_rate_decimal = Decimal(str(funding_rate))
                    
                    rates_dict[normalized_symbol] = funding_rate_decimal
                    
                    logger.debug(
                        f"{self.dex_name}: {market_symbol} -> {normalized_symbol}: "
                        f"{funding_rate_decimal}"
                    )
                
                except Exception as e:
                    logger.error(
                        f"{self.dex_name}: Error parsing rate for {market.get('market', 'unknown')}: {e}"
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
        Normalize Paradex symbol format to standard format
        
        Paradex symbols follow the pattern:
        - "BTC-USD-PERP" -> "BTC"
        - "ETH-USD-PERP" -> "ETH"
        - "SOL-USD-PERP" -> "SOL"
        - "PEPE-USD-PERP" -> "PEPE"
        
        Args:
            dex_symbol: Paradex-specific symbol format
            
        Returns:
            Normalized symbol (e.g., "BTC")
        """
        # Remove "-USD-PERP" suffix
        normalized = dex_symbol.upper()
        
        # Remove perpetual suffixes
        normalized = normalized.replace('-USD-PERP', '')
        normalized = normalized.replace('-PERP', '')
        normalized = normalized.replace('-USD', '')
        
        # Handle any edge cases with multipliers (similar to Lighter)
        # Match pattern: starts with digits followed by letters
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
        Convert normalized symbol back to Paradex-specific format
        
        Args:
            normalized_symbol: Normalized symbol (e.g., "BTC")
            
        Returns:
            Paradex-specific format (e.g., "BTC-USD-PERP")
        """
        # Paradex uses "{SYMBOL}-USD-PERP" format
        return f"{normalized_symbol.upper()}-USD-PERP"
    
    async def close(self) -> None:
        """Close the API client"""
        # Paradex SDK doesn't require explicit cleanup
        logger.debug(f"{self.dex_name}: Adapter closed")
        await super().close()


# Example usage (for testing)
async def test_paradex_adapter():
    """Test the Paradex adapter"""
    adapter = ParadexAdapter()
    
    try:
        rates, latency_ms = await adapter.fetch_with_metrics()
        
        print(f"\n✅ Paradex Adapter Test")
        print(f"Latency: {latency_ms}ms")
        print(f"Fetched {len(rates)} rates:\n")
        
        for symbol, rate in sorted(rates.items())[:10]:  # Show first 10
            annualized_apy = float(rate) * 365 * 3 * 100  # Assuming 8h periods
            print(f"  {symbol:10s}: {rate:>12} ({annualized_apy:>8.2f}% APY)")
        
        if len(rates) > 10:
            print(f"  ... and {len(rates) - 10} more")
    
    except Exception as e:
        print(f"\n❌ Paradex Adapter Test Failed: {e}")
        raise
    
    finally:
        await adapter.close()


if __name__ == "__main__":
    import asyncio
    asyncio.run(test_paradex_adapter())

