"""
Base DEX Adapter Interface

All DEX adapters must inherit from this base class and implement
the abstract methods. This ensures consistency across all DEX integrations.
"""

from abc import ABC, abstractmethod
from typing import Dict, Optional
from decimal import Decimal
from datetime import datetime
import aiohttp
import asyncio
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type
)

from utils.logger import logger


class BaseDEXAdapter(ABC):
    """
    Base class for all DEX adapters
    
    Each DEX adapter is responsible for:
    1. Fetching funding rates from the DEX API
    2. Parsing the API response into a standard format
    3. Handling DEX-specific API quirks
    4. Error handling and retries
    """
    
    def __init__(self, dex_name: str, api_base_url: str, timeout: int = 10):
        """
        Initialize base adapter
        
        Args:
            dex_name: Name of the DEX (e.g., "lighter", "edgex")
            api_base_url: Base URL for the DEX API
            timeout: Request timeout in seconds
        """
        self.dex_name = dex_name
        self.api_base_url = api_base_url
        self.timeout = timeout
        self._session: Optional[aiohttp.ClientSession] = None
    
    @abstractmethod
    async def fetch_funding_rates(self) -> Dict[str, Decimal]:
        """
        Fetch all funding rates from this DEX
        
        Returns:
            Dictionary mapping normalized symbols to funding rates
            Example: {"BTC": Decimal("0.0001"), "ETH": Decimal("0.00008")}
            
        Raises:
            Exception: If fetching fails after retries
        """
        pass
    
    @abstractmethod
    def normalize_symbol(self, dex_symbol: str) -> str:
        """
        Normalize DEX-specific symbol format to standard format
        
        Args:
            dex_symbol: DEX-specific format (e.g., "BTC-PERP", "PERP_BTC_USDC")
            
        Returns:
            Normalized symbol (e.g., "BTC")
        """
        pass
    
    @abstractmethod
    def get_dex_symbol_format(self, normalized_symbol: str) -> str:
        """
        Convert normalized symbol back to DEX-specific format
        
        Args:
            normalized_symbol: Normalized symbol (e.g., "BTC")
            
        Returns:
            DEX-specific format (e.g., "BTC-PERP")
        """
        pass
    
    async def get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout)
            )
        return self._session
    
    async def close(self) -> None:
        """Close the HTTP session"""
        if self._session and not self._session.closed:
            await self._session.close()
            logger.debug(f"{self.dex_name}: Session closed")
    
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
        Make HTTP request with retry logic
        
        Args:
            endpoint: API endpoint (will be appended to base_url)
            method: HTTP method
            params: Query parameters
            json_data: JSON body data
            
        Returns:
            Response JSON as dictionary
            
        Raises:
            aiohttp.ClientError: On connection/HTTP errors
            asyncio.TimeoutError: On timeout
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
                    logger.error(
                        f"{self.dex_name}: API returned {response.status}: {error_text}"
                    )
                    raise aiohttp.ClientError(
                        f"API returned {response.status}: {error_text}"
                    )
                
                return await response.json()
        
        except asyncio.TimeoutError:
            logger.error(f"{self.dex_name}: Request timeout for {url}")
            raise
        
        except aiohttp.ClientError as e:
            logger.error(f"{self.dex_name}: Request failed for {url}: {e}")
            raise
    
    async def fetch_with_metrics(self) -> tuple[Dict[str, Decimal], int]:
        """
        Fetch funding rates with collection latency metrics
        
        Returns:
            Tuple of (rates_dict, latency_ms)
        """
        start_time = asyncio.get_event_loop().time()
        
        try:
            rates = await self.fetch_funding_rates()
            latency_ms = int((asyncio.get_event_loop().time() - start_time) * 1000)
            
            logger.info(
                f"{self.dex_name}: Fetched {len(rates)} rates in {latency_ms}ms"
            )
            
            return rates, latency_ms
        
        except Exception as e:
            latency_ms = int((asyncio.get_event_loop().time() - start_time) * 1000)
            logger.error(
                f"{self.dex_name}: Fetch failed after {latency_ms}ms: {e}"
            )
            raise
    
    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} dex={self.dex_name}>"

