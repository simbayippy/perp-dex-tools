"""
Paradex DEX Funding Adapter

Fetches funding rates and market data from Paradex using the official Paradex Python SDK.
This adapter is read-only and focused solely on data collection.
"""

from typing import Dict, Optional
from decimal import Decimal
import re
import asyncio

from exchange_clients.base import BaseFundingAdapter
from utils.logger import logger

# Import Paradex SDK
try:
    from paradex_py import Paradex
    from paradex_py.environment import PROD, TESTNET
    PARADEX_SDK_AVAILABLE = True
except ImportError:
    PARADEX_SDK_AVAILABLE = False
    logger.warning("Paradex SDK not available. Install with: pip install paradex-py")


class ParadexFundingAdapter(BaseFundingAdapter):
    """
    Paradex funding rate adapter
    
    This adapter uses the official Paradex Python SDK to fetch funding rates
    and market data for all available perpetual markets on Paradex.
    
    Key features:
    - Uses Paradex API to fetch funding rates and market data
    - Normalizes symbols from Paradex format to standard format
    - No authentication required (public endpoints)
    - Returns funding rates and volume/OI data
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
            markets_summary = await asyncio.get_event_loop().run_in_executor(
                None, self.paradex.api_client.fetch_markets_summary
            )
            
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
                    
                    # Get funding rate - check multiple possible fields
                    funding_rate = market.get('funding_rate') or market.get('funding_rate_8h')
                    
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
    
    async def fetch_market_data(self) -> Dict[str, Dict[str, Decimal]]:
        """
        Fetch market data (volume, open interest) from Paradex
        
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
            logger.debug(f"{self.dex_name}: Fetching market data...")
            
            # Fetch markets info and summary
            markets_info = await asyncio.get_event_loop().run_in_executor(
                None, self.paradex.api_client.fetch_markets
            )
            
            markets_summary = await asyncio.get_event_loop().run_in_executor(
                None, self.paradex.api_client.fetch_markets_summary
            )
            
            if not markets_info or 'results' not in markets_info:
                logger.warning(f"{self.dex_name}: No markets info returned")
                return {}
            
            if not markets_summary or 'results' not in markets_summary:
                logger.warning(f"{self.dex_name}: No markets summary returned")
                return {}
            
            # Create lookup for summary data
            summary_lookup = {}
            for market in markets_summary['results']:
                market_symbol = market.get('market', '')
                if market_symbol:
                    summary_lookup[market_symbol] = market
            
            # Extract market data
            market_data = {}
            for market in markets_info['results']:
                try:
                    market_symbol = market.get('symbol', '')
                    
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
                        
                        logger.debug(
                            f"{self.dex_name}: {market_symbol} -> {normalized_symbol}: "
                            f"volume={data.get('volume_24h', 'N/A')}, "
                            f"oi={data.get('open_interest', 'N/A')}"
                        )
                
                except Exception as e:
                    logger.error(
                        f"{self.dex_name}: Error parsing market data for {market.get('symbol', 'unknown')}: {e}"
                    )
                    continue
            
            logger.info(
                f"{self.dex_name}: Successfully fetched market data for {len(market_data)} symbols"
            )
            
            return market_data
        
        except Exception as e:
            logger.error(f"{self.dex_name}: Failed to fetch market data: {e}")
            return {}
    
    def normalize_symbol(self, dex_symbol: str) -> str:
        """
        Normalize Paradex symbol format to standard format
        
        Paradex symbols follow the pattern (confirmed from examples):
        - "BTC-USD-PERP" -> "BTC"
        - "ETH-USD-PERP" -> "ETH"
        - "SOL-USD-PERP" -> "SOL"
        - "PEPE-USD-PERP" -> "PEPE"
        
        Args:
            dex_symbol: Paradex-specific symbol format
            
        Returns:
            Normalized symbol (e.g., "BTC")
        """
        # Remove "-USD-PERP" suffix (Paradex uses hyphens)
        normalized = dex_symbol.upper()
        
        # Remove perpetual suffixes in order of specificity
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

