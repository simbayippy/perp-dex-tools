"""
Aster DEX Funding Adapter

Fetches funding rates and market data from Aster using the official aster-connector-python SDK.
This adapter is read-only and focused solely on data collection.
"""

from typing import Dict, Optional
from decimal import Decimal
import re

from exchange_clients.base import BaseFundingAdapter
from funding_rate_service.utils.logger import logger

# Import Aster SDK
try:
    from aster.rest_api import Client as AsterClient
    ASTER_SDK_AVAILABLE = True
except ImportError:
    ASTER_SDK_AVAILABLE = False
    logger.warning("Aster SDK not available. Install with: pip install aster-connector-python")


class AsterFundingAdapter(BaseFundingAdapter):
    """
    Aster funding rate adapter
    
    This adapter uses the official aster-connector-python SDK to fetch funding rates
    and market data for all available perpetual markets on Aster.
    
    Key features:
    - Uses Aster API to fetch funding rates and market data
    - Normalizes symbols from Aster format to standard format
    - No authentication required (public endpoints)
    - Returns funding rates and volume/OI data
    """
    
    def __init__(
        self, 
        api_base_url: str = "https://fapi.asterdex.com",
        timeout: int = 10
    ):
        """
        Initialize Aster adapter
        
        Args:
            api_base_url: Aster API base URL
            timeout: Request timeout in seconds
        """
        if not ASTER_SDK_AVAILABLE:
            raise ImportError(
                "Aster SDK is required. Install with: pip install aster-connector-python"
            )
        
        super().__init__(
            dex_name="aster",
            api_base_url=api_base_url,
            timeout=timeout,
            funding_interval_hours=8  # Aster uses 8-hour funding intervals
        )
        
        # Initialize Aster public client (read-only, no credentials needed)
        self.aster_client = AsterClient(base_url=api_base_url, timeout=timeout)

        # Cache for symbol-specific funding intervals
        self._symbol_intervals: Optional[Dict[str, int]] = None

        logger.info(f"Aster adapter initialized")
    
    async def fetch_funding_interval_configs(self) -> Dict[str, int]:
        """
        Fetch symbol-specific funding interval configurations from Aster

        Aster provides per-symbol funding intervals via /fapi/v1/fundingInfo endpoint.
        Different symbols can have different intervals (e.g., INJUSDT=8h, ZORAUSDT=4h).

        Returns:
            Dictionary mapping normalized symbols to funding intervals in hours
            Example: {"BTC": 8, "ZORA": 4, "INJ": 8}
        """
        try:
            logger.debug(f"{self.dex_name}: Fetching funding interval configs...")

            # Fetch funding info which includes interval configs
            # Note: This endpoint might be called 'funding_info' or similar in the SDK
            # Based on API docs: GET /fapi/v1/fundingInfo
            funding_info = self.aster_client.funding_info()

            if not funding_info:
                logger.warning(f"{self.dex_name}: No funding info returned, using defaults")
                return {}

            intervals_dict = {}

            # Handle both single dict and list of dicts response
            if isinstance(funding_info, dict):
                funding_info_list = [funding_info]
            elif isinstance(funding_info, list):
                funding_info_list = funding_info
            else:
                logger.warning(f"{self.dex_name}: Unexpected funding info format: {type(funding_info)}")
                return {}

            for info in funding_info_list:
                try:
                    symbol = info.get('symbol', '')

                    # Only process perpetual markets (ending with USDT)
                    if not symbol.endswith('USDT'):
                        continue

                    # Get funding interval - field is 'fundingIntervalHours' per API docs
                    interval_hours = info.get('fundingIntervalHours')

                    if interval_hours is None:
                        continue

                    # Normalize symbol
                    normalized_symbol = self.normalize_symbol(symbol)

                    # Convert to int
                    intervals_dict[normalized_symbol] = int(interval_hours)

                    # Log non-standard intervals
                    if int(interval_hours) != 8:
                        logger.info(
                            f"{self.dex_name}: Non-standard interval detected: "
                            f"{symbol} ({normalized_symbol}) = {interval_hours}h"
                        )

                except Exception as e:
                    logger.error(
                        f"{self.dex_name}: Error parsing funding info for {info.get('symbol', 'unknown')}: {e}"
                    )
                    continue

            logger.info(
                f"{self.dex_name}: Fetched funding intervals for {len(intervals_dict)} symbols "
                f"({sum(1 for i in intervals_dict.values() if i != 8)} non-standard)"
            )

            return intervals_dict

        except AttributeError:
            # SDK doesn't have funding_info method
            logger.warning(
                f"{self.dex_name}: funding_info() method not available in SDK, using defaults"
            )
            return {}
        except Exception as e:
            logger.warning(
                f"{self.dex_name}: Failed to fetch funding interval configs: {e}. Using defaults."
            )
            return {}

    async def fetch_funding_rates(self) -> Dict[str, Decimal]:
        """
        Fetch all funding rates from Aster

        Aster provides funding rates through their mark_price endpoint (/fapi/v1/premiumIndex)
        which includes current funding rate information for each perpetual market.

        NOTE: Aster symbols have different funding intervals (e.g., INJUSDT=8h, ZORAUSDT=4h).
        We fetch these intervals and normalize all rates to 8-hour standard.

        Returns:
            Dictionary mapping normalized symbols to funding rates (normalized to 8h)
            Example: {"BTC": Decimal("0.0001"), "ETH": Decimal("0.00008")}

        Raises:
            Exception: If fetching fails after retries
        """
        try:
            logger.debug(f"{self.dex_name}: Fetching funding rates...")

            # Fetch symbol-specific funding intervals (cached)
            if self._symbol_intervals is None:
                self._symbol_intervals = await self.fetch_funding_interval_configs()

            # Fetch mark prices for all symbols which includes funding rates
            mark_prices_data = self.aster_client.mark_price()

            if not mark_prices_data:
                logger.warning(f"{self.dex_name}: No mark prices data returned")
                return {}

            # Extract funding rates
            rates_dict = {}

            # Handle both single dict and list of dicts response
            if isinstance(mark_prices_data, dict):
                mark_prices_list = [mark_prices_data]
            elif isinstance(mark_prices_data, list):
                mark_prices_list = mark_prices_data
            else:
                logger.warning(f"{self.dex_name}: Unexpected mark prices data format: {type(mark_prices_data)}")
                return {}

            for market_data in mark_prices_list:
                try:
                    symbol = market_data.get('symbol', '')

                    # Only process perpetual markets (ending with USDT)
                    if not symbol.endswith('USDT'):
                        continue

                    # Get funding rate - try multiple possible field names
                    funding_rate = (market_data.get('lastFundingRate') or
                                  market_data.get('fundingRate') or
                                  market_data.get('funding_rate'))

                    if funding_rate is None:
                        # Debug: log available fields for first few symbols only
                        if len(rates_dict) < 2:
                            available_fields = list(market_data.keys())
                            logger.debug(
                                f"{self.dex_name}: No funding rate for {symbol}. Available fields: {available_fields}"
                            )
                        continue

                    # Normalize symbol (e.g., "BTCUSDT" -> "BTC")
                    normalized_symbol = self.normalize_symbol(symbol)

                    # Convert to Decimal (this is the native rate for this symbol's interval)
                    funding_rate_native = Decimal(str(funding_rate))

                    # Get symbol-specific interval (or use exchange default)
                    symbol_interval = self._symbol_intervals.get(normalized_symbol, self.funding_interval_hours)

                    # CRITICAL: Normalize to 8-hour standard
                    # If symbol has 4h interval, multiply by 2 to get 8h equivalent
                    # If symbol has 8h interval, no change
                    funding_rate_8h = self._normalize_rate(funding_rate_native, symbol_interval)

                    rates_dict[normalized_symbol] = funding_rate_8h

                    # Log details for first few symbols or non-standard intervals
                    if len(rates_dict) <= 3 or symbol_interval != 8:
                        logger.debug(
                            f"{self.dex_name}: {symbol} -> {normalized_symbol} ({symbol_interval}h): "
                            f"{funding_rate_native} → {funding_rate_8h} (normalized to 8h)"
                        )

                except Exception as e:
                    logger.error(
                        f"{self.dex_name}: Error parsing rate for {market_data.get('symbol', 'unknown')}: {e}"
                    )
                    continue

            logger.info(
                f"{self.dex_name}: Successfully fetched {len(rates_dict)} funding rates (normalized to 8h)"
            )

            return rates_dict

        except Exception as e:
            logger.error(f"{self.dex_name}: Failed to fetch funding rates: {e}")
            raise

    def _normalize_rate(self, rate: Decimal, interval_hours: int) -> Decimal:
        """
        Normalize funding rate from native interval to 8-hour standard

        Args:
            rate: Native funding rate (per symbol's interval)
            interval_hours: Symbol's funding interval in hours

        Returns:
            Rate normalized to 8-hour interval
        """
        if interval_hours == 8:
            return rate
        else:
            multiplier = Decimal('8') / Decimal(str(interval_hours))
            return rate * multiplier
    
    async def fetch_market_data(self) -> Dict[str, Dict[str, Decimal]]:
        """
        Fetch market data (volume, open interest) from Aster
        
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
            
            # Fetch 24hr ticker data for volume and mark prices for open interest
            ticker_data = self.aster_client.ticker_24hr_price_change()
            
            mark_prices_data = self.aster_client.mark_price()
            
            if not ticker_data:
                logger.warning(f"{self.dex_name}: No ticker data returned")
                return {}
            
            # Create lookup for mark prices data (for open interest if available)
            mark_prices_lookup = {}
            if mark_prices_data:
                if isinstance(mark_prices_data, list):
                    for mark_item in mark_prices_data:
                        symbol = mark_item.get('symbol', '')
                        if symbol:
                            mark_prices_lookup[symbol] = mark_item
                elif isinstance(mark_prices_data, dict):
                    # Single symbol response
                    symbol = mark_prices_data.get('symbol', '')
                    if symbol:
                        mark_prices_lookup[symbol] = mark_prices_data
            
            # Extract market data
            market_data = {}
            
            # Handle both single dict and list of dicts response for ticker
            if isinstance(ticker_data, dict):
                ticker_list = [ticker_data]
            elif isinstance(ticker_data, list):
                ticker_list = ticker_data
            else:
                logger.warning(f"{self.dex_name}: Unexpected ticker data format: {type(ticker_data)}")
                return {}
            
            for ticker in ticker_list:
                try:
                    symbol = ticker.get('symbol', '')
                    
                    # Only process perpetual markets (ending with USDT)
                    if not symbol.endswith('USDT'):
                        continue
                    
                    # Normalize symbol
                    normalized_symbol = self.normalize_symbol(symbol)
                    
                    # Get volume (24h) - try multiple possible field names
                    volume_24h = (ticker.get('volume') or 
                                ticker.get('baseVolume') or 
                                ticker.get('quoteVolume'))
                    
                    # Debug: log available ticker fields for first few symbols
                    if len(market_data) < 3:  # Only log for first few to avoid spam
                        ticker_fields = list(ticker.keys())
                        logger.debug(f"{self.dex_name}: Ticker fields for {symbol}: {ticker_fields}")
                    
                    # Get open interest from mark prices if available
                    # Note: Aster may not provide open interest data
                    mark_data = mark_prices_lookup.get(symbol, {})
                    open_interest = (mark_data.get('openInterest') or 
                                   mark_data.get('openInterestValue') or 
                                   mark_data.get('open_interest'))
                    
                    # Debug: log mark price fields for first few symbols
                    if len(market_data) < 3 and mark_data:
                        mark_fields = list(mark_data.keys())
                        logger.debug(f"{self.dex_name}: Mark price fields for {symbol}: {mark_fields}")
                    
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
        Normalize Aster symbol format to standard format
        
        Aster symbols follow the pattern (confirmed from examples):
        - "BTCUSDT" -> "BTC"  (similar to Binance format)
        - "ETHUSDT" -> "ETH"
        - "SOLUSDT" -> "SOL"
        - "PEPEUSDT" -> "PEPE"
        
        Args:
            dex_symbol: Aster-specific symbol format
            
        Returns:
            Normalized symbol (e.g., "BTC")
        """
        # Remove "USDT" suffix (Aster uses no separators, similar to Binance)
        normalized = dex_symbol.upper()
        
        # Remove perpetual suffixes in order of specificity
        normalized = normalized.replace('USDT', '')
        normalized = normalized.replace('USDC', '')  # fallback
        
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
        Convert normalized symbol back to Aster-specific format
        
        Args:
            normalized_symbol: Normalized symbol (e.g., "BTC")
            
        Returns:
            Aster-specific format (e.g., "BTCUSDT")
        """
        # Aster uses "{SYMBOL}USDT" format (no separators)
        return f"{normalized_symbol.upper()}USDT"
    
    def get_symbol_intervals(self) -> Dict[str, int]:
        """
        Get cached symbol-specific funding intervals

        Returns:
            Dictionary mapping normalized symbols to intervals in hours
            Returns empty dict if intervals haven't been fetched yet
        """
        return self._symbol_intervals or {}

    async def close(self) -> None:
        """Close the API client"""
        # Aster SDK doesn't require explicit cleanup
        logger.debug(f"{self.dex_name}: Adapter closed")
        await super().close()

