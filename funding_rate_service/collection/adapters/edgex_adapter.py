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
        max_concurrent_requests: int = 20
    ):
        """
        Initialize EdgeX adapter
        
        Args:
            api_base_url: Base API URL (default: https://pro.edgex.exchange)
            timeout: Request timeout in seconds
            max_concurrent_requests: Max parallel requests for funding rate fetching
        """
        if api_base_url is None:
            api_base_url = "https://pro.edgex.exchange"
        
        super().__init__(
            dex_name="edgex",
            api_base_url=api_base_url,
            timeout=timeout
        )
        
        self.max_concurrent_requests = max_concurrent_requests
        
        logger.info(
            f"EdgeX adapter initialized (max_concurrent={max_concurrent_requests})"
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
            metadata_response = await self._make_request("/api/v1/public/metadata")
            
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
            
            # Filter to perpetual contracts only (exclude options, etc.)
            perpetual_contracts = []
            for contract in contract_list:
                contract_name = contract.get('contractName', '')
                # EdgeX perpetuals typically end with 'USD' (e.g., BTCUSD, ETHUSD)
                # and have contractType = 'PERPETUAL' or similar
                if contract_name.endswith('USD'):
                    perpetual_contracts.append({
                        'contract_id': contract.get('contractId'),
                        'contract_name': contract_name,
                        'base_currency': contract.get('baseCurrency', ''),
                    })
            
            if not perpetual_contracts:
                logger.warning(f"{self.dex_name}: No perpetual contracts found")
                return {}
            
            logger.info(
                f"{self.dex_name}: Found {len(perpetual_contracts)} perpetual contracts, "
                f"fetching funding rates in parallel (max {self.max_concurrent_requests} concurrent)..."
            )
            
            # Step 2: Fetch funding rates for all contracts in parallel
            semaphore = asyncio.Semaphore(self.max_concurrent_requests)
            
            tasks = [
                self._fetch_single_funding_rate(
                    contract['contract_id'],
                    contract['contract_name'],
                    semaphore
                )
                for contract in perpetual_contracts
            ]
            
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
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
    
    def normalize_symbol(self, dex_symbol: str) -> str:
        """
        Normalize EdgeX symbol format to standard format
        
        EdgeX format examples:
        - BTCUSD -> BTC
        - ETHUSD -> ETH
        - SOLUSD -> SOL
        - 1000PEPE USD -> PEPE (handle multiplier prefix)
        
        Args:
            dex_symbol: EdgeX symbol format (e.g., "BTCUSD")
            
        Returns:
            Normalized symbol (e.g., "BTC")
        """
        normalized = dex_symbol.upper()
        
        # Remove USD suffix
        normalized = normalized.replace('USD', '')
        
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
            EdgeX format (e.g., "BTCUSD")
        """
        return f"{normalized_symbol.upper()}USD"
    
    async def close(self) -> None:
        """Close the adapter and cleanup resources"""
        logger.debug(f"{self.dex_name}: Adapter closed")
        await super().close()

