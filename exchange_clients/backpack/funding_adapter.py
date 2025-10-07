"""
Backpack DEX Funding Adapter

Fetches funding rates and market data from Backpack using the official bpx-py SDK.
This adapter is read-only and focused solely on data collection.
"""

from typing import Dict, Optional
from decimal import Decimal
import re
import asyncio

from exchange_clients.base import BaseFundingAdapter
from utils.logger import logger

# Import Backpack SDK
try:
    from bpx.public import Public
    BPX_SDK_AVAILABLE = True
except ImportError:
    BPX_SDK_AVAILABLE = False
    logger.warning("Backpack SDK (bpx-py) not available. Install with: pip install bpx-py")


class BackpackFundingAdapter(BaseFundingAdapter):
    """
    Backpack funding rate adapter
    
    This adapter uses the official bpx-py SDK to fetch funding rates
    and market data for all available perpetual markets on Backpack.
    
    Key features:
    - Uses Backpack API to fetch funding rates and market data
    - Normalizes symbols from Backpack format to standard format
    - No authentication required (public endpoints)
    - Returns funding rates and volume/OI data
    """
    
    def __init__(
        self, 
        api_base_url: str = "https://api.backpack.exchange",
        timeout: int = 10
    ):
        """
        Initialize Backpack adapter
        
        Args:
            api_base_url: Backpack API base URL
            timeout: Request timeout in seconds
        """
        if not BPX_SDK_AVAILABLE:
            raise ImportError(
                "Backpack SDK (bpx-py) is required. Install with: pip install bpx-py"
            )
        
        super().__init__(
            dex_name="backpack",
            api_base_url=api_base_url,
            timeout=timeout
        )
        
        # Initialize Backpack public client (read-only, no credentials needed)
        self.public_client = Public()
        
        logger.info(f"Backpack adapter initialized")
    
    async def fetch_funding_rates(self) -> Dict[str, Decimal]:
        """
        Fetch all funding rates from Backpack
        
        Backpack provides funding rates through their get_all_mark_prices endpoint
        which includes current funding rate information for each perpetual market.
        
        Returns:
            Dictionary mapping normalized symbols to funding rates
            Example: {"BTC": Decimal("0.0001"), "ETH": Decimal("0.00008")}
            
        Raises:
            Exception: If fetching fails after retries
        """
        try:
            logger.debug(f"{self.dex_name}: Fetching funding rates...")
            
            # First, get all markets to find perpetual symbols
            markets_data = self.public_client.get_markets()
            if not markets_data:
                logger.warning(f"{self.dex_name}: No markets data returned")
                return {}
            
            # Find all perpetual markets
            perp_symbols = []
            for market in markets_data:
                symbol = market.get('symbol', '')
                if symbol.endswith('_PERP'):
                    perp_symbols.append(symbol)
            
            if not perp_symbols:
                logger.warning(f"{self.dex_name}: No perpetual markets found")
                return {}
            
            logger.debug(f"{self.dex_name}: Found {len(perp_symbols)} perpetual markets: {perp_symbols[:5]}...")
            
            rates_dict = {}
            
            # Use Backpack's funding interval rates endpoint to get the most recent funding rates
            # This provides historical funding rates, with the most recent being effectively current
            
            # Get funding rates for all perpetual symbols
            for symbol in perp_symbols:
                try:
                    # Use the funding interval rates endpoint (historical data)
                    funding_data = self.public_client.get_funding_interval_rates(symbol, limit=1)
                    
                    if not funding_data:
                        logger.debug(f"{self.dex_name}: No funding data for {symbol}")
                        continue
                    
                    # Handle both single dict and list response
                    if isinstance(funding_data, list) and len(funding_data) > 0:
                        latest_funding = funding_data[0]  # Get the most recent funding rate
                    elif isinstance(funding_data, dict):
                        latest_funding = funding_data
                    else:
                        logger.debug(f"{self.dex_name}: Unexpected funding data format for {symbol}: {type(funding_data)}")
                        continue
                    
                    # Debug: log the structure of the funding data for first few symbols only
                    if len(rates_dict) < 2:  # Only log for first 2 symbols to avoid spam
                        logger.debug(f"{self.dex_name}: Funding data structure for {symbol}: {latest_funding}")
                    
                    # Get funding rate - try multiple possible field names
                    funding_rate = (latest_funding.get('fundingRate') or 
                                  latest_funding.get('rate') or 
                                  latest_funding.get('funding_rate') or
                                  latest_funding.get('r') or
                                  latest_funding.get('f'))  # WebSocket uses 'f' for funding rate
                    
                    if funding_rate is None:
                        # Debug: log available fields for first few symbols only
                        if len(rates_dict) < 2:
                            available_fields = list(latest_funding.keys())
                            logger.debug(
                                f"{self.dex_name}: No funding rate for {symbol}. Available fields: {available_fields}"
                            )
                        continue
                    
                    # Normalize symbol (e.g., "BTC_USDC_PERP" -> "BTC")
                    normalized_symbol = self.normalize_symbol(symbol)
                    
                    # Convert to Decimal
                    funding_rate_decimal = Decimal(str(funding_rate))
                    
                    rates_dict[normalized_symbol] = funding_rate_decimal
                    
                    # Log details for first few symbols only to avoid spam
                    if len(rates_dict) <= 3:
                        logger.debug(
                            f"{self.dex_name}: {symbol} -> {normalized_symbol}: "
                            f"{funding_rate_decimal}"
                        )
                
                except Exception as e:
                    logger.error(
                        f"{self.dex_name}: Error fetching funding rate for {symbol}: {e}"
                    )
                    continue
            
            # If no funding rates found, log a helpful message
            if not rates_dict:
                logger.warning(
                    f"{self.dex_name}: No funding rates found from {len(perp_symbols)} perpetual markets. "
                    f"This may indicate an API issue or change in data format."
                )
            
            logger.info(
                f"{self.dex_name}: Successfully fetched {len(rates_dict)} funding rates"
            )
            
            return rates_dict
        
        except Exception as e:
            logger.error(f"{self.dex_name}: Failed to fetch funding rates: {e}")
            raise
    
    async def fetch_market_data(self) -> Dict[str, Dict[str, Decimal]]:
        """
        Fetch market data (volume, open interest) from Backpack
        
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
            
            # Fetch tickers for volume data and open interest for OI data
            tickers_data = self.public_client.get_tickers()
            
            open_interest_data = self.public_client.get_open_interest()
            
            if not tickers_data:
                logger.warning(f"{self.dex_name}: No tickers data returned")
                return {}
            
            # Create lookup for open interest data
            oi_lookup = {}
            if open_interest_data:
                if isinstance(open_interest_data, list):
                    for oi_item in open_interest_data:
                        symbol = oi_item.get('symbol', '')
                        if symbol:
                            oi_lookup[symbol] = oi_item
                elif isinstance(open_interest_data, dict):
                    # Single symbol response
                    symbol = open_interest_data.get('symbol', '')
                    if symbol:
                        oi_lookup[symbol] = open_interest_data
            
            # Extract market data
            market_data = {}
            
            # Handle both single dict and list of dicts response for tickers
            if isinstance(tickers_data, dict):
                tickers_list = [tickers_data]
            elif isinstance(tickers_data, list):
                tickers_list = tickers_data
            else:
                logger.warning(f"{self.dex_name}: Unexpected tickers data format: {type(tickers_data)}")
                return {}
            
            for ticker in tickers_list:
                try:
                    symbol = ticker.get('symbol', '')
                    
                    # Only process perpetual markets
                    if not symbol.endswith('_PERP'):
                        continue
                    
                    # Normalize symbol
                    normalized_symbol = self.normalize_symbol(symbol)
                    
                    # Get volume (24h) - based on API docs, field is "volume"
                    volume_24h = ticker.get('volume') or ticker.get('quoteVolume')
                    
                    # Get open interest from lookup - based on API docs, field is "openInterest"
                    oi_data = oi_lookup.get(symbol, {})
                    open_interest = oi_data.get('openInterest')
                    
                    # Create market data entry
                    data = {}
                    
                    if volume_24h is not None:
                        data['volume_24h'] = Decimal(str(volume_24h))
                    
                    if open_interest is not None:
                        data['open_interest'] = Decimal(str(open_interest))
                    
                    if data:  # Only add if we have some data
                        market_data[normalized_symbol] = data
                        
                        # Log details for first few symbols only to avoid spam
                        if len(market_data) <= 3:
                            logger.debug(
                                f"{self.dex_name}: {symbol} -> {normalized_symbol}: "
                                f"volume={data.get('volume_24h', 'N/A')}, "
                                f"oi={data.get('open_interest', 'N/A')}"
                            )
                
                except Exception as e:
                    logger.error(
                        f"{self.dex_name}: Error parsing market data for {ticker.get('symbol', 'unknown')}: {e}"
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
        Normalize Backpack symbol format to standard format
        
        Backpack symbols follow the pattern (confirmed from examples):
        - "BTC_USDC_PERP" -> "BTC"  (note: uses USDC, not USD)
        - "ETH_USDC_PERP" -> "ETH"
        - "SOL_USDC_PERP" -> "SOL"
        - "PEPE_USDC_PERP" -> "PEPE"
        
        Args:
            dex_symbol: Backpack-specific symbol format
            
        Returns:
            Normalized symbol (e.g., "BTC")
        """
        # Remove "_USDC_PERP" suffix (Backpack uses underscores and USDC)
        normalized = dex_symbol.upper()
        
        # Remove perpetual suffixes in order of specificity
        normalized = normalized.replace('_USDC_PERP', '')
        normalized = normalized.replace('_PERP', '')
        normalized = normalized.replace('_USDC', '')
        
        # Handle any edge cases with multipliers (similar to other exchanges)
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
        Convert normalized symbol back to Backpack-specific format
        
        Args:
            normalized_symbol: Normalized symbol (e.g., "BTC")
            
        Returns:
            Backpack-specific format (e.g., "BTC_USDC_PERP")
        """
        # Backpack uses "{SYMBOL}_USDC_PERP" format
        return f"{normalized_symbol.upper()}_USDC_PERP"
    
    async def close(self) -> None:
        """Close the API client"""
        # bpx-py SDK doesn't require explicit cleanup
        logger.debug(f"{self.dex_name}: Adapter closed")
        await super().close()

