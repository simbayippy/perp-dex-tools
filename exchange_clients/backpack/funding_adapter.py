"""
Backpack DEX Funding Adapter

Fetches funding rates and market data from Backpack using direct API calls.
This adapter is read-only and focused solely on data collection.
"""

from typing import Dict, Optional
from decimal import Decimal
import aiohttp
import asyncio

from exchange_clients.base import BaseFundingAdapter
from utils.logger import logger


class BackpackFundingAdapter(BaseFundingAdapter):
    """
    Backpack funding rate adapter
    
    This adapter uses direct API calls to fetch funding rates and market data 
    for all available perpetual markets on Backpack.
    
    Key features:
    - Uses direct HTTP calls to Backpack API (no SDK dependency)
    - Single API call to get ALL funding rates at once
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
        super().__init__(
            dex_name="backpack",
            api_base_url=api_base_url,
            timeout=timeout
        )
        
        # HTTP session for API calls
        self.session: Optional[aiohttp.ClientSession] = None
        
        logger.info(f"Backpack adapter initialized with direct API calls")
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create HTTP session"""
        if self.session is None or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            self.session = aiohttp.ClientSession(timeout=timeout)
        return self.session
    
    async def _make_request(self, endpoint: str, params: Optional[Dict] = None) -> Dict:
        """Make HTTP request to Backpack API"""
        session = await self._get_session()
        url = f"{self.api_base_url}/{endpoint}"
        
        try:
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    error_text = await response.text()
                    raise Exception(f"HTTP {response.status}: {error_text}")
        except Exception as e:
            logger.error(f"{self.dex_name}: Request failed for {endpoint}: {e}")
            raise
    
    async def fetch_funding_rates(self) -> Dict[str, Decimal]:
        """
        Fetch all funding rates from Backpack
        
        Uses the /api/v1/markPrices endpoint to get ALL funding rates in a single call.
        This is much faster than calling individual endpoints per symbol.
        
        Returns:
            Dictionary mapping normalized symbols to funding rates
            Example: {"BTC": Decimal("0.0001"), "ETH": Decimal("0.00008")}
            
        Raises:
            Exception: If fetching fails after retries
        """
        try:
            logger.debug(f"{self.dex_name}: Fetching funding rates...")
            
            # Get ALL mark prices (including funding rates) in one call
            mark_prices_data = await self._make_request("api/v1/markPrices")
            
            if not mark_prices_data:
                logger.warning(f"{self.dex_name}: No mark prices data returned")
                return {}
            
            # Ensure we have a list
            if not isinstance(mark_prices_data, list):
                logger.warning(f"{self.dex_name}: Expected list, got {type(mark_prices_data)}")
                return {}
            
            rates_dict = {}
            
            # Extract funding rates from mark prices data
            for mark_data in mark_prices_data:
                try:
                    symbol = mark_data.get('symbol', '')
                    
                    # Only process perpetual markets (ending with _PERP)
                    if not symbol.endswith('_PERP'):
                        continue
                    
                    # Get funding rate from mark prices response
                    funding_rate = mark_data.get('fundingRate')
                    
                    if funding_rate is None:
                        # Debug: log available fields for first few symbols only
                        if len(rates_dict) < 2:
                            available_fields = list(mark_data.keys())
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
                        f"{self.dex_name}: Error parsing mark price data for {mark_data.get('symbol', 'unknown')}: {e}"
                    )
                    continue
            
            # If no funding rates found, log a helpful message
            if not rates_dict:
                logger.warning(
                    f"{self.dex_name}: No funding rates found from {len(mark_prices_data)} mark price entries. "
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
            
            # Fetch tickers for volume data (based on API documentation)
            tickers_data = await self._make_request("api/v1/tickers")
            
            # Fetch open interest data (separate endpoint)
            open_interest_data = await self._make_request("api/v1/openInterest")
            
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
                    volume_24h =  ticker.get('quoteVolume')
                    
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
        - "kPEPE_USDC_PERP" -> "PEPE" (removes k prefix for 1000x tokens)
        
        Args:
            dex_symbol: Backpack-specific symbol format
            
        Returns:
            Normalized symbol (e.g., "BTC")
        """
        # Remove "_USDC_PERP" suffix
        normalized = dex_symbol.upper()
        normalized = normalized.replace('_USDC_PERP', '')
        normalized = normalized.replace('_PERP', '')
        normalized = normalized.replace('_USDC', '')
        normalized = normalized.replace('_USD', '')  # fallback
        
        # Handle 1000x tokens (e.g., "kPEPE" -> "PEPE", "kSHIB" -> "SHIB")
        if normalized.startswith('K') and len(normalized) > 1:
            # Check if it's a known 1000x token pattern
            base_symbol = normalized[1:]  # Remove 'k' prefix
            if base_symbol in ['PEPE', 'SHIB', 'BONK']:  # Known 1000x tokens
                logger.debug(
                    f"{self.dex_name}: Converting 1000x token: {dex_symbol} -> "
                    f"{base_symbol} (removed k prefix)"
                )
                normalized = base_symbol
        
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
        # Handle special cases for 1000x tokens
        if normalized_symbol.upper() in ['PEPE', 'SHIB', 'BONK']:
            return f"k{normalized_symbol.upper()}_USDC_PERP"
        
        # Standard format: {SYMBOL}_USDC_PERP
        return f"{normalized_symbol.upper()}_USDC_PERP"
    
    async def close(self) -> None:
        """Close the HTTP session"""
        if self.session and not self.session.closed:
            await self.session.close()
            logger.debug(f"{self.dex_name}: HTTP session closed")
        await super().close()