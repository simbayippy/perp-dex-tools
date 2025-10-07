"""
EdgeX DEX Adapter

Fetches funding rates from EdgeX using their public Funding API.
API Docs: https://pro.edgex.exchange/docs/api

Key endpoints:
- GET /api/v1/public/funding/getLatestFundingRate - Get latest funding rate by contract ID
- Public API, no authentication required
"""

import asyncio
import logging
from typing import Dict, Optional
from decimal import Decimal
import re

from exchange_clients.base import BaseFundingAdapter

# Initialize logger for this module
logger = logging.getLogger(__name__)


class EdgeXFundingAdapter(BaseFundingAdapter):
    """
    EdgeX funding rate adapter
    
    Uses EdgeX's public Funding API to fetch latest funding rates.
    Requires two-step process:
    1. Fetch metadata to get all contract IDs and names
    2. Fetch funding rate for each contract (with parallel execution)
    """
    
    def __init__(
        self, 
        api_base_url: Optional[str] = None,
        timeout: int = 10,
        max_concurrent_requests: int = 5,
        delay_between_batches: float = 0.5
    ):
        """
        Initialize EdgeX adapter
        
        Args:
            api_base_url: Base API URL (default: https://pro.edgex.exchange)
            timeout: Request timeout in seconds
            max_concurrent_requests: Max parallel requests (default: 5 to avoid rate limits)
            delay_between_batches: Delay in seconds between request batches (default: 0.5s)
        """
        if api_base_url is None:
            api_base_url = "https://pro.edgex.exchange"
        
        super().__init__(
            dex_name="edgex",
            api_base_url=api_base_url,
            timeout=timeout
        )
        
        self.max_concurrent_requests = max_concurrent_requests
        self.delay_between_batches = delay_between_batches
        
        # Cache for ticker data to avoid duplicate API calls
        # When fetch_funding_rates() is called, it caches tickers
        # Then fetch_market_data() can reuse the cached data
        self._ticker_cache: Dict[str, dict] = {}
        
        logger.info(
            f"EdgeX adapter initialized (max_concurrent={max_concurrent_requests}, "
            f"batch_delay={delay_between_batches}s)"
        )
    
    async def fetch_funding_rates(self) -> Dict[str, Decimal]:
        """
        Fetch all funding rates from EdgeX
        
        OPTIMIZED: Uses getTicker endpoint which provides BOTH funding rates and market data.
        Caches ticker responses for later use by fetch_market_data() to avoid duplicate API calls.
        
        Process:
        1. GET /api/v1/public/metadata - get all contracts
        2. For each contract: GET /api/v1/public/quote/getTicker (has funding rate + volume + OI)
        3. Extract funding rates and cache full ticker data
        
        Returns:
            Dict mapping normalized symbols to funding rates
        """
        try:
            logger.debug(f"{self.dex_name}: Fetching contract metadata...")
            
            # Clear ticker cache for fresh data
            self._ticker_cache = {}
            
            # Step 1: Fetch all contracts
            metadata_response = await self._make_request("/api/v1/public/meta/getMetaData")
            
            if metadata_response.get('code') != 'SUCCESS':
                logger.error(
                    f"{self.dex_name}: Metadata fetch failed: "
                    f"{metadata_response.get('msg', 'Unknown error')}"
                )
                return {}
            
            contract_list = metadata_response.get('data', {}).get('contractList', [])
            
            if not contract_list:
                logger.warning(f"{self.dex_name}: No contracts found in metadata")
                return {}
            
            # Filter to perpetual contracts only (enableTrade = true)
            perpetual_contracts = []
            for contract in contract_list:
                contract_name = contract.get('contractName', '')
                enable_trade = contract.get('enableTrade', False)
                
                # EdgeX perpetuals have names like BTCUSDT, ETHUSDT
                if enable_trade and ('USDT' in contract_name or 'USD' in contract_name):
                    perpetual_contracts.append({
                        'contract_id': contract.get('contractId'),
                        'contract_name': contract_name,
                    })
            
            if not perpetual_contracts:
                logger.warning(f"{self.dex_name}: No perpetual contracts found")
                return {}
            
            logger.info(
                f"{self.dex_name}: Found {len(perpetual_contracts)} perpetual contracts, "
                f"fetching tickers (funding + market data) in batches "
                f"(max {self.max_concurrent_requests} concurrent, {self.delay_between_batches}s delay)..."
            )
            
            # Step 2: Fetch tickers (which have both funding rates AND market data) in batches
            results = await self._fetch_tickers_in_batches(perpetual_contracts)
            
            # Step 3: Extract funding rates and cache full ticker data
            rates_dict = {}
            successful = 0
            failed = 0
            
            for result in results:
                if isinstance(result, Exception):
                    failed += 1
                    logger.debug(f"{self.dex_name}: Ticker fetch failed: {result}")
                elif result is not None:
                    symbol, rate, ticker_data = result
                    rates_dict[symbol] = rate
                    self._ticker_cache[symbol] = ticker_data  # Cache for market data reuse
                    successful += 1
                else:
                    failed += 1
            
            logger.info(
                f"{self.dex_name}: Successfully fetched {successful} funding rates "
                f"({failed} failed) from {len(perpetual_contracts)} contracts. "
                f"Ticker data cached for market data extraction."
            )
            
            return rates_dict
        
        except Exception as e:
            logger.error(f"{self.dex_name}: Failed to fetch funding rates: {e}")
            raise
    
    async def _fetch_tickers_in_batches(self, contracts: list) -> list:
        """
        Fetch tickers (with funding rates + market data) in batches with delays
        
        Args:
            contracts: List of contract dicts with 'contract_id' and 'contract_name'
            
        Returns:
            List of results (tuples of symbol, rate, ticker_data or exceptions)
        """
        all_results = []
        semaphore = asyncio.Semaphore(self.max_concurrent_requests)
        
        # Process contracts in batches
        for i in range(0, len(contracts), self.max_concurrent_requests):
            batch = contracts[i:i + self.max_concurrent_requests]
            
            logger.debug(
                f"{self.dex_name}: Processing ticker batch {i // self.max_concurrent_requests + 1} "
                f"({len(batch)} contracts)"
            )
            
            # Fetch this batch
            tasks = [
                self._fetch_single_ticker(
                    contract['contract_id'],
                    contract['contract_name'],
                    semaphore
                )
                for contract in batch
            ]
            
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)
            all_results.extend(batch_results)
            
            # Add delay between batches (except for the last batch)
            if i + self.max_concurrent_requests < len(contracts):
                logger.debug(
                    f"{self.dex_name}: Waiting {self.delay_between_batches}s "
                    f"before next batch..."
                )
                await asyncio.sleep(self.delay_between_batches)
        
        return all_results
    
    async def _fetch_single_ticker(
        self, 
        contract_id: str, 
        contract_name: str,
        semaphore: asyncio.Semaphore
    ) -> Optional[tuple[str, Decimal, dict]]:
        """
        Fetch ticker (funding rate + market data) for a single contract
        
        Args:
            contract_id: EdgeX contract ID
            contract_name: EdgeX contract name (e.g., "BTCUSDT")
            semaphore: Concurrency limiter
            
        Returns:
            Tuple of (normalized_symbol, funding_rate, ticker_data) or None if failed
        """
        async with semaphore:
            try:
                # Fetch ticker for this contract (has funding rate, volume, OI, etc.)
                response = await self._make_request(
                    "/api/v1/public/quote/getTicker",
                    params={'contractId': contract_id}
                )
                
                if response.get('code') != 'SUCCESS':
                    logger.debug(
                        f"{self.dex_name}: Failed to fetch ticker for {contract_name}: "
                        f"{response.get('msg', 'Unknown error')}"
                    )
                    return None
                
                # Extract ticker data
                data_list = response.get('data', [])
                if not data_list or len(data_list) == 0:
                    logger.debug(
                        f"{self.dex_name}: No ticker data for {contract_name}"
                    )
                    return None
                
                ticker = data_list[0]
                
                # Extract funding rate
                funding_rate_str = ticker.get('fundingRate')
                if funding_rate_str is None:
                    logger.debug(
                        f"{self.dex_name}: No funding rate in ticker for {contract_name}"
                    )
                    return None
                
                funding_rate = Decimal(str(funding_rate_str))
                
                # Normalize symbol (BTCUSDT -> BTC)
                normalized_symbol = self.normalize_symbol(contract_name)
                
                logger.debug(
                    f"{self.dex_name}: {contract_name} -> {normalized_symbol}: "
                    f"funding_rate={funding_rate}, volume=${ticker.get('value', 'N/A')}, "
                    f"OI={ticker.get('openInterest', 'N/A')}"
                )
                
                return (normalized_symbol, funding_rate, ticker)
            
            except Exception as e:
                logger.debug(
                    f"{self.dex_name}: Error fetching ticker for {contract_name}: {e}"
                )
                return None
    
    async def fetch_market_data(self) -> Dict[str, Dict[str, Decimal]]:
        """
        Fetch market data (volume, OI) from EdgeX
        
        OPTIMIZED: Prefers cached ticker data from fetch_funding_rates() to avoid duplicate API calls.
        If cache is empty, falls back to fetching tickers directly (independent operation).
        
        Returns:
            Dictionary mapping normalized symbols to market data
            Example: {
                "BTC": {
                    "volume_24h": Decimal("50821443.74"),
                    "open_interest": Decimal("10683.72")
                }
            }
            
        Raises:
            Exception: If fetching fails after retries
        """
        try:
            # Fast path: Use cached tickers if available
            if self._ticker_cache:
                logger.debug(
                    f"{self.dex_name}: Using cached tickers for market data "
                    f"({len(self._ticker_cache)} symbols, no API calls needed)..."
                )
                return self._extract_market_data_from_cache()
            
            # Slow path: Cache is empty, fetch tickers directly
            logger.info(
                f"{self.dex_name}: Ticker cache is empty. "
                f"Fetching tickers directly for market data (this will be slower)..."
            )
            
            # Step 1: Fetch all contracts
            metadata_response = await self._make_request("/api/v1/public/meta/getMetaData")
            
            if metadata_response.get('code') != 'SUCCESS':
                logger.error(
                    f"{self.dex_name}: Metadata fetch failed: "
                    f"{metadata_response.get('msg', 'Unknown error')}"
                )
                return {}
            
            contract_list = metadata_response.get('data', {}).get('contractList', [])
            
            if not contract_list:
                logger.warning(f"{self.dex_name}: No contracts found in metadata")
                return {}
            
            # Filter to perpetual contracts
            perpetual_contracts = []
            for contract in contract_list:
                contract_name = contract.get('contractName', '')
                enable_trade = contract.get('enableTrade', False)
                
                if enable_trade and ('USDT' in contract_name or 'USD' in contract_name):
                    perpetual_contracts.append({
                        'contract_id': contract.get('contractId'),
                        'contract_name': contract_name,
                    })
            
            if not perpetual_contracts:
                logger.warning(f"{self.dex_name}: No perpetual contracts found")
                return {}
            
            logger.info(
                f"{self.dex_name}: Found {len(perpetual_contracts)} perpetual contracts, "
                f"fetching tickers in batches for market data..."
            )
            
            # Step 2: Fetch tickers in batches
            results = await self._fetch_tickers_in_batches(perpetual_contracts)
            
            # Step 3: Extract market data and populate cache
            market_data_dict = {}
            successful = 0
            failed = 0
            
            for result in results:
                if isinstance(result, Exception):
                    failed += 1
                    logger.debug(f"{self.dex_name}: Ticker fetch failed: {result}")
                elif result is not None:
                    symbol, rate, ticker = result
                    # Cache the ticker for future use
                    self._ticker_cache[symbol] = ticker
                    # Extract market data
                    market_data_dict[symbol] = self._extract_market_data_from_ticker(ticker)
                    successful += 1
                else:
                    failed += 1
            
            logger.info(
                f"{self.dex_name}: Successfully fetched market data for {successful} symbols "
                f"({failed} failed). Tickers cached for future calls."
            )
            
            return market_data_dict
        
        except Exception as e:
            logger.error(f"{self.dex_name}: Failed to fetch market data: {e}")
            raise
    
    def _extract_market_data_from_cache(self) -> Dict[str, Dict[str, Decimal]]:
        """
        Extract market data from cached tickers
        
        Returns:
            Dictionary mapping normalized symbols to market data
        """
        market_data_dict = {}
        successful = 0
        
        for normalized_symbol, ticker in self._ticker_cache.items():
            try:
                market_data_dict[normalized_symbol] = self._extract_market_data_from_ticker(ticker)
                
                vol = market_data_dict[normalized_symbol].get('volume_24h')
                oi = market_data_dict[normalized_symbol].get('open_interest')
                vol_str = f"${vol:,.2f}" if vol else "N/A"
                oi_str = f"${oi:,.2f}" if oi else "N/A"
                logger.debug(
                    f"{self.dex_name}: {normalized_symbol}: Volume={vol_str}, OI={oi_str}"
                )
                
                successful += 1
            
            except Exception as e:
                logger.error(
                    f"{self.dex_name}: Error extracting market data for {normalized_symbol}: {e}"
                )
                continue
        
        logger.info(
            f"{self.dex_name}: Successfully extracted market data for {successful} symbols from cache"
        )
        
        return market_data_dict
    
    def _extract_market_data_from_ticker(self, ticker: dict) -> Dict[str, Decimal]:
        """
        Extract volume and OI from a single ticker response
        
        Args:
            ticker: Ticker data dict from EdgeX API
            
        Returns:
            Dict with volume_24h and open_interest
        """
        # Extract 24h trading value (already in USD)
        volume_24h_str = ticker.get('value')
        if volume_24h_str is None:
            volume_24h = None
        else:
            volume_24h = Decimal(str(volume_24h_str))
        
        # Extract open interest (in contracts)
        open_interest_contracts = ticker.get('openInterest')
        index_price = ticker.get('indexPrice') or ticker.get('lastPrice')
        
        if open_interest_contracts is None or index_price is None:
            open_interest_usd = None
        else:
            # Convert OI to USD: contracts * index_price
            open_interest_usd = Decimal(str(open_interest_contracts)) * Decimal(str(index_price))
        
        return {
            "volume_24h": volume_24h,
            "open_interest": open_interest_usd
        }
    
    def normalize_symbol(self, dex_symbol: str) -> str:
        """
        Normalize EdgeX symbol format to standard format
        
        EdgeX format examples:
        - BTCUSDT -> BTC
        - ETHUSDT -> ETH
        - SOLUSDT -> SOL
        - 1000PEPEUSDT -> PEPE (handle multiplier prefix)
        
        Args:
            dex_symbol: EdgeX symbol format (e.g., "BTCUSDT")
            
        Returns:
            Normalized symbol (e.g., "BTC")
        """
        normalized = dex_symbol.upper()
        
        # Remove USDT/USD suffix
        normalized = normalized.replace('USDT', '').replace('USD', '')
        
        # Handle multiplier prefixes (e.g., 1000PEPE -> PEPE)
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
        Convert normalized symbol back to EdgeX format
        
        Args:
            normalized_symbol: Normalized symbol (e.g., "BTC")
            
        Returns:
            EdgeX format (e.g., "BTCUSDT")
        """
        return f"{normalized_symbol.upper()}USDT"
    
    async def close(self) -> None:
        """Close the adapter and cleanup resources"""
        logger.debug(f"{self.dex_name}: Adapter closed")
        await super().close()

