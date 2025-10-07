"""
EdgeX DEX Adapter

Fetches funding rates from EdgeX using their public Funding API.
API Docs: https://pro.edgex.exchange/docs/api

Key endpoints:
- GET /api/v1/public/funding/getLatestFundingRate - Get latest funding rate by contract ID
- Public API, no authentication required
"""

import asyncio
from typing import Dict, Optional
from decimal import Decimal
import re

from collection.base_adapter import BaseDEXAdapter
from utils.logger import logger


class EdgeXAdapter(BaseDEXAdapter):
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
        
        logger.info(
            f"EdgeX adapter initialized (max_concurrent={max_concurrent_requests}, "
            f"batch_delay={delay_between_batches}s)"
        )
    
    async def fetch_funding_rates(self) -> Dict[str, Decimal]:
        """
        Fetch all funding rates from EdgeX
        
        Process:
        1. GET /api/v1/public/metadata - get all contracts
        2. For each contract: GET /api/v1/public/funding/getLatestFundingRate
        3. Parse and normalize symbols
        
        Returns:
            Dict mapping normalized symbols to funding rates
        """
        try:
            logger.debug(f"{self.dex_name}: Fetching contract metadata...")
            
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
                f"fetching funding rates in batches (max {self.max_concurrent_requests} concurrent, "
                f"{self.delay_between_batches}s delay between batches)..."
            )
            
            # Step 2: Fetch funding rates in batches with delays to avoid rate limiting
            results = await self._fetch_in_batches(perpetual_contracts)
            
            # Step 3: Collect results
            rates_dict = {}
            successful = 0
            failed = 0
            
            for result in results:
                if isinstance(result, Exception):
                    failed += 1
                    logger.debug(f"{self.dex_name}: Funding rate fetch failed: {result}")
                elif result is not None:
                    symbol, rate = result
                    rates_dict[symbol] = rate
                    successful += 1
                else:
                    failed += 1
            
            logger.info(
                f"{self.dex_name}: Successfully fetched {successful} funding rates "
                f"({failed} failed) from {len(perpetual_contracts)} contracts"
            )
            
            return rates_dict
        
        except Exception as e:
            logger.error(f"{self.dex_name}: Failed to fetch funding rates: {e}")
            raise
    
    async def _fetch_in_batches(self, contracts: list) -> list:
        """
        Fetch funding rates in batches with delays to avoid rate limiting
        
        Args:
            contracts: List of contract dicts with 'contract_id' and 'contract_name'
            
        Returns:
            List of results (tuples or exceptions)
        """
        all_results = []
        semaphore = asyncio.Semaphore(self.max_concurrent_requests)
        
        # Process contracts in batches
        for i in range(0, len(contracts), self.max_concurrent_requests):
            batch = contracts[i:i + self.max_concurrent_requests]
            
            logger.debug(
                f"{self.dex_name}: Processing batch {i // self.max_concurrent_requests + 1} "
                f"({len(batch)} contracts)"
            )
            
            # Fetch this batch
            tasks = [
                self._fetch_single_funding_rate(
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
    
    async def _fetch_single_funding_rate(
        self, 
        contract_id: str, 
        contract_name: str,
        semaphore: asyncio.Semaphore
    ) -> Optional[tuple[str, Decimal]]:
        """
        Fetch funding rate for a single contract
        
        Args:
            contract_id: EdgeX contract ID
            contract_name: EdgeX contract name (e.g., "BTCUSD")
            semaphore: Concurrency limiter
            
        Returns:
            Tuple of (normalized_symbol, funding_rate) or None if failed
        """
        async with semaphore:
            try:
                # Fetch latest funding rate for this contract
                response = await self._make_request(
                    "/api/v1/public/funding/getLatestFundingRate",
                    params={'contractId': contract_id}
                )
                
                if response.get('code') != 'SUCCESS':
                    logger.debug(
                        f"{self.dex_name}: Failed to fetch funding rate for {contract_name}: "
                        f"{response.get('msg', 'Unknown error')}"
                    )
                    return None
                
                # Extract funding rate data
                data_list = response.get('data', [])
                if not data_list or len(data_list) == 0:
                    logger.debug(
                        f"{self.dex_name}: No funding rate data for {contract_name}"
                    )
                    return None
                
                funding_data = data_list[0]
                
                # Extract funding rate
                funding_rate_str = funding_data.get('fundingRate')
                if funding_rate_str is None:
                    logger.debug(
                        f"{self.dex_name}: No funding rate field for {contract_name}"
                    )
                    return None
                
                # Convert to Decimal
                funding_rate = Decimal(str(funding_rate_str))
                
                # Normalize symbol
                normalized_symbol = self.normalize_symbol(contract_name)
                
                logger.debug(
                    f"{self.dex_name}: {contract_name} (ID: {contract_id}) -> "
                    f"{normalized_symbol}: {funding_rate}"
                )
                
                return (normalized_symbol, funding_rate)
            
            except Exception as e:
                logger.debug(
                    f"{self.dex_name}: Error fetching funding rate for {contract_name}: {e}"
                )
                return None
    
    async def fetch_market_data(self) -> Dict[str, Dict[str, Decimal]]:
        """
        Fetch market data (volume, OI) from EdgeX
        
        Uses the getTicker endpoint to fetch 24h volume and open interest.
        Fetches in batches with delays to avoid rate limiting (same as funding rates).
        
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
            logger.debug(f"{self.dex_name}: Fetching contract metadata for market data...")
            
            # Step 1: Fetch all contracts (reuse same logic as funding rates)
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
                f"fetching market data in batches (max {self.max_concurrent_requests} concurrent)..."
            )
            
            # Step 2: Fetch market data in batches
            results = await self._fetch_market_data_in_batches(perpetual_contracts)
            
            # Step 3: Collect results
            market_data_dict = {}
            successful = 0
            failed = 0
            
            for result in results:
                if isinstance(result, Exception):
                    failed += 1
                    logger.debug(f"{self.dex_name}: Market data fetch failed: {result}")
                elif result is not None:
                    symbol, data = result
                    market_data_dict[symbol] = data
                    successful += 1
                else:
                    failed += 1
            
            logger.info(
                f"{self.dex_name}: Successfully fetched market data for {successful} symbols "
                f"({failed} failed) from {len(perpetual_contracts)} contracts"
            )
            
            return market_data_dict
        
        except Exception as e:
            logger.error(f"{self.dex_name}: Failed to fetch market data: {e}")
            raise
    
    async def _fetch_market_data_in_batches(self, contracts: list) -> list:
        """
        Fetch market data in batches with delays to avoid rate limiting
        
        Args:
            contracts: List of contract dicts with 'contract_id' and 'contract_name'
            
        Returns:
            List of results (tuples or exceptions)
        """
        all_results = []
        semaphore = asyncio.Semaphore(self.max_concurrent_requests)
        
        # Process contracts in batches
        for i in range(0, len(contracts), self.max_concurrent_requests):
            batch = contracts[i:i + self.max_concurrent_requests]
            
            logger.debug(
                f"{self.dex_name}: Processing market data batch "
                f"{i // self.max_concurrent_requests + 1} ({len(batch)} contracts)"
            )
            
            # Fetch this batch
            tasks = [
                self._fetch_single_market_data(
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
                await asyncio.sleep(self.delay_between_batches)
        
        return all_results
    
    async def _fetch_single_market_data(
        self, 
        contract_id: str, 
        contract_name: str,
        semaphore: asyncio.Semaphore
    ) -> Optional[tuple[str, Dict[str, Decimal]]]:
        """
        Fetch market data for a single contract
        
        Args:
            contract_id: EdgeX contract ID
            contract_name: EdgeX contract name (e.g., "BTCUSDT")
            semaphore: Concurrency limiter
            
        Returns:
            Tuple of (normalized_symbol, market_data_dict) or None if failed
        """
        async with semaphore:
            try:
                # Fetch ticker data for this contract
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
                
                # Extract 24h trading value (already in USD)
                volume_24h_str = ticker.get('value')
                if volume_24h_str is None:
                    logger.debug(
                        f"{self.dex_name}: No volume data for {contract_name}"
                    )
                    volume_24h = None
                else:
                    volume_24h = Decimal(str(volume_24h_str))
                
                # Extract open interest (in contracts)
                open_interest_contracts = ticker.get('openInterest')
                index_price = ticker.get('indexPrice') or ticker.get('lastPrice')
                
                if open_interest_contracts is None or index_price is None:
                    logger.debug(
                        f"{self.dex_name}: Missing OI or price for {contract_name}"
                    )
                    open_interest_usd = None
                else:
                    # Convert OI to USD: contracts * index_price
                    open_interest_usd = Decimal(str(open_interest_contracts)) * Decimal(str(index_price))
                
                # Normalize symbol
                normalized_symbol = self.normalize_symbol(contract_name)
                
                market_data = {
                    "volume_24h": volume_24h,
                    "open_interest": open_interest_usd
                }
                
                vol_str = f"${volume_24h:,.2f}" if volume_24h else "N/A"
                oi_str = f"${open_interest_usd:,.2f}" if open_interest_usd else "N/A"
                logger.debug(
                    f"{self.dex_name}: {contract_name} -> {normalized_symbol}: "
                    f"Volume={vol_str}, OI={oi_str}"
                )
                
                return (normalized_symbol, market_data)
            
            except Exception as e:
                logger.debug(
                    f"{self.dex_name}: Error fetching market data for {contract_name}: {e}"
                )
                return None
    
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

