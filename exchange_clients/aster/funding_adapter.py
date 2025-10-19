"""
Aster DEX Funding Adapter

Fetches funding rates and market data from Aster using the official aster-connector-python SDK.
This adapter is read-only and focused solely on data collection.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Dict, Optional
import re

from exchange_clients.base import BaseFundingAdapter, FundingRateSample
from funding_rate_service.utils.logger import logger, clamp_external_logger_levels

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
            timeout=timeout
        )
        
        # Initialize Aster public client (read-only, no credentials needed)
        self.aster_client = AsterClient(base_url=api_base_url, timeout=timeout)
        clamp_external_logger_levels()
        
        self._funding_interval_cache: Dict[str, Decimal] = {}
        self._funding_interval_last_refresh: Optional[datetime] = None
        self._funding_interval_ttl = timedelta(minutes=10)
    
    async def _refresh_funding_intervals(self) -> None:
        """Refresh cached funding interval hours per symbol."""
        now = datetime.now(timezone.utc)
        if (
            self._funding_interval_cache
            and self._funding_interval_last_refresh
            and now - self._funding_interval_last_refresh < self._funding_interval_ttl
        ):
            return
        
        try:
            response = await self._make_request("/fapi/v1/fundingInfo")
        except Exception as exc:
            logger.warning(f"{self.dex_name}: Failed to refresh funding intervals: {exc}")
            self._funding_interval_last_refresh = now
            return
        
        updated_cache: Dict[str, Decimal] = {}
        data_iter = response if isinstance(response, list) else response or []
        for entry in data_iter:
            symbol = entry.get("symbol")
            interval_value = entry.get("fundingIntervalHours")
            if not symbol or interval_value is None:
                continue
            try:
                interval_decimal = Decimal(str(interval_value))
            except (InvalidOperation, TypeError, ValueError):
                continue
            if interval_decimal > 0:
                updated_cache[symbol] = interval_decimal
        
        if updated_cache:
            self._funding_interval_cache = updated_cache
        self._funding_interval_last_refresh = now
    
    @staticmethod
    def _parse_next_funding_time(value: Optional[object]) -> Optional[datetime]:
        """Convert API timestamps (ms/ns) to aware UTC datetime."""
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
    
    async def fetch_funding_rates(self) -> Dict[str, FundingRateSample]:
        """
        Fetch all funding rates from Aster
        
        Aster provides funding rates through their mark_price endpoint (/fapi/v1/premiumIndex)
        which includes current funding rate information for each perpetual market.
        
        Returns:
            Dictionary mapping normalized symbols to FundingRateSample entries
            
        Raises:
            Exception: If fetching fails after retries
        """
        try:
            # logger.debug(f"{self.dex_name}: Fetching funding rates...")
            
            await self._refresh_funding_intervals()
            
            # Fetch mark prices for all symbols which includes funding rates
            mark_prices_data = self.aster_client.mark_price()
            
            if not mark_prices_data:
                # logger.warning(f"{self.dex_name}: No mark prices data returned")
                return {}
            
            # Extract funding rates
            rates_dict: Dict[str, FundingRateSample] = {}
            
            # Handle both single dict and list of dicts response
            if isinstance(mark_prices_data, dict):
                mark_prices_list = [mark_prices_data]
            elif isinstance(mark_prices_data, list):
                mark_prices_list = mark_prices_data
            else:
                # logger.warning(f"{self.dex_name}: Unexpected mark prices data format: {type(mark_prices_data)}")
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
                        # if len(rates_dict) < 2:
                        #     available_fields = list(market_data.keys())
                        #     logger.debug(
                        #         f"{self.dex_name}: No funding rate for {symbol}. Available fields: {available_fields}"
                        #     )
                        continue
                    
                    normalized_symbol = self.normalize_symbol(symbol)
                    raw_rate = Decimal(str(funding_rate))
                    interval_hours = self._funding_interval_cache.get(
                        symbol,
                        self.CANONICAL_INTERVAL_HOURS,
                    )
                    if interval_hours <= 0:
                        interval_hours = self.CANONICAL_INTERVAL_HOURS
                    normalized_rate = raw_rate * (self.CANONICAL_INTERVAL_HOURS / interval_hours)
                    
                    next_funding_time = self._parse_next_funding_time(
                        market_data.get('nextFundingTime')
                    )
                    
                    rates_dict[normalized_symbol] = FundingRateSample(
                        normalized_rate=normalized_rate,
                        raw_rate=raw_rate,
                        interval_hours=interval_hours,
                        next_funding_time=next_funding_time,
                        metadata={'symbol': symbol},
                    )
                    
                    # # Log details for first few symbols only to avoid spam
                    # if len(rates_dict) <= 3:
                    #     logger.debug(
                    #         f"{self.dex_name}: {symbol} -> {normalized_symbol}: "
                    #         f"{normalized_rate} (interval={interval_hours}h)"
                    #     )
                
                except Exception as e:
                    logger.error(
                        f"{self.dex_name}: Error parsing rate for {market_data.get('symbol', 'unknown')}: {e}"
                    )
                    continue
            
            # logger.info(
            #     f"{self.dex_name}: Successfully fetched {len(rates_dict)} funding rates"
            # )
            
            return rates_dict
        
        except Exception as e:
            logger.error(f"{self.dex_name}: Failed to fetch funding rates: {e}")
            raise
    
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
            # logger.debug(f"{self.dex_name}: Fetching market data...")
            
            # Fetch 24hr ticker data for volume and mark prices for open interest
            ticker_data = self.aster_client.ticker_24hr_price_change()
            
            mark_prices_data = self.aster_client.mark_price()
            
            if not ticker_data:
                # logger.warning(f"{self.dex_name}: No ticker data returned")
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
                # logger.warning(f"{self.dex_name}: Unexpected ticker data format: {type(ticker_data)}")
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
                    # if len(market_data) < 3:  # Only log for first few to avoid spam
                    #     ticker_fields = list(ticker.keys())
                    #     logger.debug(f"{self.dex_name}: Ticker fields for {symbol}: {ticker_fields}")
                    
                    # Get open interest from mark prices if available
                    # Note: Aster may not provide open interest data
                    mark_data = mark_prices_lookup.get(symbol, {})
                    open_interest = (mark_data.get('openInterest') or 
                                   mark_data.get('openInterestValue') or 
                                   mark_data.get('open_interest'))
                    
                    # Debug: log mark price fields for first few symbols
                    # if len(market_data) < 3 and mark_data:
                    #     mark_fields = list(mark_data.keys())
                    #     logger.debug(f"{self.dex_name}: Mark price fields for {symbol}: {mark_fields}")
                    
                    # Create market data entry
                    data = {}
                    
                    if volume_24h is not None:
                        data['volume_24h'] = Decimal(str(volume_24h))
                    
                    if open_interest is not None:
                        data['open_interest'] = Decimal(str(open_interest))
                    
                    if data:  # Only add if we have some data
                        market_data[normalized_symbol] = data
                        
                        # Log details for first few symbols only to avoid spam
                        # if len(market_data) <= 3:
                        #     logger.debug(
                        #         f"{self.dex_name}: {symbol} -> {normalized_symbol}: "
                        #         f"volume={data.get('volume_24h', 'N/A')}, "
                        #         f"oi={data.get('open_interest', 'N/A')}"
                        #     )
                
                except Exception as e:
                    logger.error(
                        f"{self.dex_name}: Error parsing market data for {ticker.get('symbol', 'unknown')}: {e}"
                    )
                    continue
            
            # logger.info(
            #     f"{self.dex_name}: Successfully fetched market data for {len(market_data)} symbols"
            # )
            
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
            # logger.debug(
            #     f"{self.dex_name}: Symbol has multiplier: {dex_symbol} -> "
            #     f"{symbol} (multiplier: {multiplier})"
            # )
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
    
    async def close(self) -> None:
        """Close the API client"""
        # Aster SDK doesn't require explicit cleanup
        # logger.debug(f"{self.dex_name}: Adapter closed")
        await super().close()
