"""Base interface for funding adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
from decimal import Decimal
from typing import Dict, Optional, Tuple

import aiohttp
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from .base_models import FundingRateSample


class BaseFundingAdapter(ABC):
    """
    Base interface for funding rate collection from perpetual DEXs.
    
    This interface is used by the funding rate service to collect funding rates
    and market data from exchanges. It's read-only and doesn't require authentication
    in most cases (uses public endpoints).
    
    Key Responsibilities:
        - Fetch funding rates for all available symbols
        - Fetch market data (volume, open interest)
        - Normalize symbol formats across exchanges
        - Handle exchange-specific API quirks
        - Retry logic and error handling
    
    Implementation Pattern:
        Each exchange should implement this interface in a funding_adapter.py file:
        
        ```python
        class AsterFundingAdapter(BaseFundingAdapter):
            def __init__(self, api_base_url: str = "https://fapi.asterdex.com", timeout: int = 10):
                super().__init__(dex_name="aster", api_base_url=api_base_url, timeout=timeout)
                
            async def fetch_funding_rates(self) -> Dict[str, FundingRateSample]:
                # Fetch from exchange API
                result = await self._make_request("/api/v1/fundingRate")
                # Parse and normalize
                return {
                    \"BTC\": FundingRateSample(
                        normalized_rate=Decimal(result[\"BTC\"][\"rate\"]),
                        raw_rate=Decimal(result[\"BTC\"][\"rate\"]),
                        interval_hours=self.CANONICAL_INTERVAL_HOURS,
                    )
                }
        ```
    """
    
    CANONICAL_INTERVAL_HOURS: Decimal = Decimal("8")
    
    def __init__(self, dex_name: str, api_base_url: str, timeout: int = 10):
        """
        Initialize base funding adapter.
        
        Args:
            dex_name: Name of the DEX (e.g., "lighter", "edgex", "aster")
            api_base_url: Base URL for the DEX API
            timeout: Request timeout in seconds
        """
        self.dex_name = dex_name
        self.api_base_url = api_base_url
        self.timeout = timeout
        self._session: Optional[aiohttp.ClientSession] = None
    
    # ========================================================================
    # CORE DATA FETCHING
    # ========================================================================
    
    @abstractmethod
    async def fetch_funding_rates(self) -> Dict[str, FundingRateSample]:
        """
        Fetch all funding rates from this DEX.
        
        Returns:
            Dictionary mapping normalized symbols to `FundingRateSample` entries.
            
        Example:
            {
                "BTC": FundingRateSample(
                    normalized_rate=Decimal("0.0001"),  # 0.01% per 8h
                    raw_rate=Decimal("0.0001"),
                    interval_hours=Decimal("8")
                )
            }
            
        Raises:
            Exception: If fetching fails after retries
        """
        pass
    
    @abstractmethod
    async def fetch_market_data(self) -> Dict[str, Dict[str, Decimal]]:
        """
        Fetch market data (volume, open interest) for all symbols.
        
        Returns:
            Dictionary mapping normalized symbols to market data
            
        Example:
            {
                "BTC": {
                    "volume_24h": Decimal("1500000.0"),      # $1.5M daily volume
                    "open_interest": Decimal("5000000.0")    # $5M open interest
                },
                "ETH": {
                    "volume_24h": Decimal("800000.0"),
                    "open_interest": Decimal("2000000.0")
                }
            }
            
        Note:
            - volume_24h should be in USD
            - open_interest should be in USD
            - Both fields are optional (can be None or omitted)
            - Spread is NOT included here (too volatile, fetch client-side)
            
        Raises:
            Exception: If fetching fails after retries
        """
        pass
    
    # ========================================================================
    # SYMBOL NORMALIZATION
    # ========================================================================
    
    @abstractmethod
    def normalize_symbol(self, dex_symbol: str) -> str:
        """
        Normalize DEX-specific symbol format to standard format.
        
        Standard format: Base asset only, uppercase (e.g., "BTC", "ETH", "ZORA")
        
        Args:
            dex_symbol: DEX-specific format
            
        Returns:
            Normalized symbol
            
        Examples:
            - "BTC-PERP" -> "BTC"
            - "PERP_BTC_USDC" -> "BTC"
            - "BTCUSDT" -> "BTC"
            - "1000PEPEUSDT" -> "PEPE" (handle multipliers)
        """
        pass
    
    @abstractmethod
    def get_dex_symbol_format(self, normalized_symbol: str) -> str:
        """
        Convert normalized symbol back to DEX-specific format.
        
        Args:
            normalized_symbol: Normalized symbol (e.g., "BTC")
            
        Returns:
            DEX-specific format
            
        Examples:
            - "BTC" -> "BTC-PERP"
            - "BTC" -> "PERP_BTC_USDC"
            - "BTC" -> "BTCUSDT"
        """
        pass
    
    # ========================================================================
    # HTTP SESSION MANAGEMENT
    # ========================================================================
    
    async def get_session(self) -> aiohttp.ClientSession:
        """
        Get or create aiohttp session for HTTP requests.
        
        Returns:
            Active aiohttp ClientSession
        """
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout)
            )
        return self._session
    
    async def close(self) -> None:
        """
        Close the HTTP session and cleanup resources.
        """
        if self._session and not self._session.closed:
            await self._session.close()
    
    # ========================================================================
    # HTTP REQUEST UTILITIES
    # ========================================================================
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((asyncio.TimeoutError, aiohttp.ClientError))
    )
    async def _make_request(
        self,
        endpoint: str,
        method: str = "GET",
        params: Optional[Dict] = None,
        json_data: Optional[Dict] = None
    ) -> Dict:
        """
        Make HTTP request with automatic retry logic.
        
        Args:
            endpoint: API endpoint (will be appended to base_url)
            method: HTTP method (GET, POST, etc.)
            params: Query parameters
            json_data: JSON body data
            
        Returns:
            Response JSON as dictionary
            
        Raises:
            aiohttp.ClientError: On connection/HTTP errors (after retries)
            asyncio.TimeoutError: On timeout (after retries)
        """
        session = await self.get_session()
        url = f"{self.api_base_url}{endpoint}"
        
        try:
            async with session.request(
                method,
                url,
                params=params,
                json=json_data
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    raise aiohttp.ClientError(
                        f"API returned {response.status}: {error_text}"
                    )
                
                return await response.json()
        
        except asyncio.TimeoutError:
            raise
        
        except aiohttp.ClientError as e:
            raise
    
    # ========================================================================
    # METRICS & MONITORING
    # ========================================================================
    
    async def fetch_with_metrics(self) -> tuple[Dict[str, FundingRateSample], int]:
        """
        Fetch funding rates with collection latency metrics.
        
        Returns:
            Tuple of (rates_dict, latency_ms)
            
        Example:
            >>> rates, latency = await adapter.fetch_with_metrics()
            >>> print(f"Fetched {len(rates)} rates in {latency}ms")
        """
        start_time = asyncio.get_event_loop().time()
        
        try:
            rates = await self.fetch_funding_rates()
            latency_ms = int((asyncio.get_event_loop().time() - start_time) * 1000)
            return rates, latency_ms
        
        except Exception as e:
            latency_ms = int((asyncio.get_event_loop().time() - start_time) * 1000)
            raise
    
    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} dex={self.dex_name}>"


__all__ = ["BaseFundingAdapter"]
