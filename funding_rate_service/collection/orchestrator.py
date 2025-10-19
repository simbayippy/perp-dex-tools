"""
Collection Orchestrator

Coordinates data collection from all DEX adapters, stores results in the database,
and handles errors gracefully. This is the main component that ties together
adapters, repositories, and mappers.
"""

import asyncio
from typing import Dict, List, Optional, Tuple
from decimal import Decimal
from datetime import datetime

from databases import Database

from exchange_clients.base import BaseFundingAdapter
from funding_rate_service.database.repositories import (
    DEXRepository,
    SymbolRepository,
    FundingRateRepository,
)
from funding_rate_service.core.mappers import dex_mapper, symbol_mapper
from funding_rate_service.models.system import CollectionStatus
from funding_rate_service.utils.logger import logger


class CollectionOrchestrator:
    """
    Orchestrates funding rate collection from multiple DEXs
    
    Responsibilities:
    1. Coordinate multiple DEX adapters
    2. Fetch rates in parallel
    3. Store results in database
    4. Update mappers with new symbols
    5. Handle partial failures gracefully
    6. Log collection runs for monitoring
    
    Usage:
        orchestrator = CollectionOrchestrator(db, adapters=[lighter_adapter])
        await orchestrator.collect_all_rates()
    """
    
    def __init__(
        self,
        db: Database,
        adapters: Optional[List[BaseFundingAdapter]] = None
    ):
        """
        Initialize orchestrator
        
        Args:
            db: Database connection
            adapters: List of DEX adapters (can be empty initially)
        """
        self.db = db
        self.adapters = adapters or []
        
        # Initialize repositories
        self.dex_repo = DEXRepository(db)
        self.symbol_repo = SymbolRepository(db)
        self.funding_rate_repo = FundingRateRepository(db)
        
        logger.info(f"CollectionOrchestrator initialized with {len(self.adapters)} adapters")
    
    def add_adapter(self, adapter: BaseFundingAdapter) -> None:
        """Add a DEX adapter"""
        self.adapters.append(adapter)
        logger.info(f"Added adapter: {adapter.dex_name}")
    
    async def collect_all_rates(self, include_market_data: bool = True) -> Dict[str, any]:
        """
        Collect funding rates from all adapters
        
        This is the main method that:
        1. Runs all adapters in parallel
        2. Stores results in database
        3. Handles failures gracefully
        4. Optionally fetches market data (volume, OI)
        
        Args:
            include_market_data: If True, also fetch volume/OI (default: True)
        
        Returns:
            Dictionary with collection summary:
            {
                'total_adapters': int,
                'successful': int,
                'failed': int,
                'total_rates': int,
                'results': Dict[str, dict]
            }
        """
        if not self.adapters:
            logger.warning("No adapters configured")
            return {
                'total_adapters': 0,
                'successful': 0,
                'failed': 0,
                'total_rates': 0,
                'results': {}
            }
        
        logger.info(f"Starting collection from {len(self.adapters)} DEXs...")
        start_time = datetime.utcnow()
        
        # Collect from all adapters in parallel
        tasks = {
            adapter.dex_name: self._collect_from_adapter(adapter, include_market_data)
            for adapter in self.adapters
        }
        
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        
        # Process results
        collection_summary = {
            'total_adapters': len(self.adapters),
            'successful': 0,
            'failed': 0,
            'total_rates': 0,
            'results': {},
            'duration_seconds': 0
        }
        
        for dex_name, result in zip(tasks.keys(), results):
            if isinstance(result, Exception):
                logger.error(f"❌ {dex_name}: Collection failed: {result}")
                collection_summary['failed'] += 1
                collection_summary['results'][dex_name] = {
                    'success': False,
                    'error': str(result)
                }
            else:
                logger.info(
                    f"✅ {dex_name}: Collected {result['rates_count']} rates "
                    f"in {result['latency_ms']}ms"
                )
                collection_summary['successful'] += 1
                collection_summary['total_rates'] += result['rates_count']
                collection_summary['results'][dex_name] = result
        
        duration = (datetime.utcnow() - start_time).total_seconds()
        collection_summary['duration_seconds'] = duration
        
        # Log overall summary
        logger.info(
            f"Collection complete: {collection_summary['successful']}/{collection_summary['total_adapters']} "
            f"DEXs successful, {collection_summary['total_rates']} total rates in {duration:.2f}s"
        )
        
        if collection_summary['failed'] > 0:
            logger.warning(
                f"⚠️  {collection_summary['failed']} DEX(s) failed to collect"
            )
        
        return collection_summary
    
    async def _collect_from_adapter(
        self,
        adapter: BaseFundingAdapter,
        include_market_data: bool = True
    ) -> Dict[str, any]:
        """
        Collect funding rates and market data from a single adapter
        
        Args:
            adapter: DEX adapter instance
            include_market_data: If True, also fetch volume/OI
            
        Returns:
            Dictionary with collection result
        """
        dex_name = adapter.dex_name
        collection_start = datetime.utcnow()
        
        try:
            # Log collection start
            log_id = await self._log_collection_start(dex_name)
            
            # Fetch rates with metrics
            rates, latency_ms = await adapter.fetch_with_metrics()
            
            if not rates:
                logger.warning(f"{dex_name}: No rates returned")
                await self._log_collection_end(
                    log_id,
                    CollectionStatus.SUCCESS,
                    0,
                    0
                )
                return {
                    'success': True,
                    'rates_count': 0,
                    'latency_ms': latency_ms,
                    'new_symbols': 0
                }
            
            # Get DEX ID
            dex_id = dex_mapper.get_id(dex_name)
            if dex_id is None:
                raise ValueError(f"DEX '{dex_name}' not found in mapper")
            
            # Process and store rates
            new_symbols_count = 0
            stored_rates = 0
            
            for normalized_symbol, funding_rate in rates.items():
                try:
                    # Get or create symbol
                    symbol_id = await self.symbol_repo.get_or_create(
                        normalized_symbol
                    )
                    
                    # Check if this is a new symbol
                    if symbol_mapper.get_id(normalized_symbol) is None:
                        symbol_mapper.add(symbol_id, normalized_symbol)
                        new_symbols_count += 1
                        logger.info(
                            f"📍 New symbol discovered: {normalized_symbol} "
                            f"(ID: {symbol_id}) on {dex_name}"
                        )
                    
                    # Get or create dex_symbol mapping
                    dex_symbol_format = adapter.get_dex_symbol_format(
                        normalized_symbol
                    )
                    await self.symbol_repo.get_or_create_dex_symbol(
                        dex_id,
                        symbol_id,
                        dex_symbol_format
                    )
                    
                    # Insert funding rate
                    await self.funding_rate_repo.insert(
                        dex_id=dex_id,
                        symbol_id=symbol_id,
                        funding_rate=funding_rate,
                        collection_latency_ms=latency_ms
                    )
                    
                    # Also update latest_funding_rates for fast API responses
                    await self.funding_rate_repo.upsert_latest(
                        dex_id=dex_id,
                        symbol_id=symbol_id,
                        funding_rate=funding_rate
                    )
                    
                    stored_rates += 1
                    
                except Exception as e:
                    logger.error(
                        f"{dex_name}: Error storing rate for {normalized_symbol}: {e}"
                    )
                    continue
            
            # Store symbol-specific funding intervals (if exchange provides them)
            try:
                symbol_intervals = adapter.get_symbol_intervals()
                if symbol_intervals:
                    await self._store_symbol_intervals(
                        dex_id,
                        symbol_intervals,
                        adapter
                    )
                    non_standard = sum(1 for i in symbol_intervals.values() if i != 8)
                    logger.info(
                        f"{dex_name}: Updated funding intervals for {len(symbol_intervals)} symbols "
                        f"({non_standard} non-standard)"
                    )
            except Exception as e:
                # Symbol interval failure shouldn't fail the whole collection
                logger.warning(
                    f"{dex_name}: Failed to store symbol intervals (non-critical): {e}"
                )

            # Collect market data (volume, OI) if enabled
            if include_market_data:
                try:
                    logger.debug(f"{dex_name}: Fetching market data (volume, OI)...")
                    market_data = await adapter.fetch_market_data()

                    if market_data:
                        await self._store_market_data(
                            dex_id,
                            market_data,
                            adapter
                        )
                        logger.info(
                            f"{dex_name}: Updated market data for {len(market_data)} symbols"
                        )
                except Exception as e:
                    # Market data failure shouldn't fail the whole collection
                    logger.warning(
                        f"{dex_name}: Failed to fetch/store market data (non-critical): {e}"
                    )

            # Update DEX last fetch status
            await self.dex_repo.update_last_fetch(dex_id, success=True)
            
            # Log collection success
            await self._log_collection_end(
                log_id,
                CollectionStatus.SUCCESS,
                stored_rates,
                0
            )
            
            return {
                'success': True,
                'rates_count': stored_rates,
                'latency_ms': latency_ms,
                'new_symbols': new_symbols_count
            }
        
        except Exception as e:
            logger.error(f"{dex_name}: Collection failed: {e}")
            
            # Update DEX error status
            dex_id = dex_mapper.get_id(dex_name)
            if dex_id:
                await self.dex_repo.update_last_fetch(
                    dex_id,
                    success=False,
                    error_message=str(e)
                )
            
            # Log collection failure
            if 'log_id' in locals():
                await self._log_collection_end(
                    log_id,
                    CollectionStatus.FAILED,
                    0,
                    0,
                    error_message=str(e)
                )
            
            raise
    
    async def _store_symbol_intervals(
        self,
        dex_id: int,
        symbol_intervals: Dict[str, int],
        adapter: BaseFundingAdapter
    ) -> None:
        """
        Store symbol-specific funding intervals in dex_symbols table

        Args:
            dex_id: DEX ID
            symbol_intervals: Dictionary mapping symbols to intervals in hours
            adapter: Adapter instance (for symbol normalization)
        """
        for normalized_symbol, interval_hours in symbol_intervals.items():
            try:
                # Get symbol ID (should exist from funding rate collection)
                symbol_id = symbol_mapper.get_id(normalized_symbol)
                if symbol_id is None:
                    # Symbol doesn't exist yet, create it
                    symbol_id = await self.symbol_repo.get_or_create(normalized_symbol)
                    symbol_mapper.add(symbol_id, normalized_symbol)

                # Update dex_symbols with funding interval
                query = """
                    UPDATE dex_symbols
                    SET
                        funding_interval_hours = :interval_hours,
                        updated_at = NOW()
                    WHERE dex_id = :dex_id AND symbol_id = :symbol_id
                """

                await self.db.execute(
                    query,
                    values={
                        "dex_id": dex_id,
                        "symbol_id": symbol_id,
                        "interval_hours": interval_hours
                    }
                )

                # Log non-standard intervals
                if interval_hours != 8:
                    logger.debug(
                        f"Updated funding interval for {normalized_symbol}: {interval_hours}h"
                    )

            except Exception as e:
                logger.error(
                    f"Error storing funding interval for {normalized_symbol}: {e}"
                )
                continue

    async def _store_market_data(
        self,
        dex_id: int,
        market_data: Dict[str, Dict[str, Decimal]],
        adapter: BaseFundingAdapter
    ) -> None:
        """
        Store market data (volume, OI) in dex_symbols table

        Args:
            dex_id: DEX ID
            market_data: Dictionary mapping symbols to market data
            adapter: Adapter instance (for symbol normalization)
        """
        for normalized_symbol, data in market_data.items():
            try:
                # Get symbol ID (should exist from funding rate collection)
                symbol_id = symbol_mapper.get_id(normalized_symbol)
                if symbol_id is None:
                    # Symbol doesn't exist yet, create it
                    symbol_id = await self.symbol_repo.get_or_create(normalized_symbol)
                    symbol_mapper.add(symbol_id, normalized_symbol)

                # Update dex_symbols with market data
                volume_24h = data.get('volume_24h')
                open_interest = data.get('open_interest')

                query = """
                    UPDATE dex_symbols
                    SET
                        volume_24h = :volume_24h,
                        open_interest_usd = :open_interest,
                        updated_at = NOW()
                    WHERE dex_id = :dex_id AND symbol_id = :symbol_id
                """

                await self.db.execute(
                    query,
                    values={
                        "dex_id": dex_id,
                        "symbol_id": symbol_id,
                        "volume_24h": volume_24h,
                        "open_interest": open_interest
                    }
                )

                logger.debug(
                    f"Updated market data for {normalized_symbol}: "
                    f"Volume=${volume_24h}, OI=${open_interest}"
                )

            except Exception as e:
                logger.error(
                    f"Error storing market data for {normalized_symbol}: {e}"
                )
                continue
    
    async def _log_collection_start(self, dex_name: str) -> int:
        """
        Log the start of a collection run
        
        Args:
            dex_name: DEX name
            
        Returns:
            Collection log ID
        """
        dex_id = dex_mapper.get_id(dex_name)
        
        query = """
            INSERT INTO collection_logs (dex_id, started_at, status)
            VALUES (:dex_id, NOW(), 'in_progress')
            RETURNING id
        """
        
        log_id = await self.db.fetch_val(query, {"dex_id": dex_id})
        return log_id
    
    async def _log_collection_end(
        self,
        log_id: int,
        status: CollectionStatus,
        symbols_fetched: int,
        symbols_failed: int,
        error_message: Optional[str] = None
    ) -> None:
        """
        Log the end of a collection run
        
        Args:
            log_id: Collection log ID
            status: Collection status
            symbols_fetched: Number of symbols successfully fetched
            symbols_failed: Number of symbols that failed
            error_message: Error message if failed
        """
        query = """
            UPDATE collection_logs
            SET completed_at = NOW(),
                status = :status,
                symbols_fetched = :symbols_fetched,
                symbols_failed = :symbols_failed,
                error_message = :error_message
            WHERE id = :log_id
        """
        
        await self.db.execute(
            query,
            {
                "log_id": log_id,
                "status": status.value,
                "symbols_fetched": symbols_fetched,
                "symbols_failed": symbols_failed,
                "error_message": error_message
            }
        )
    
    async def get_collection_stats(self, hours: int = 24) -> Dict[str, any]:
        """
        Get collection statistics for the last N hours
        
        Args:
            hours: Number of hours to look back
            
        Returns:
            Statistics dictionary
        """
        query = """
            SELECT 
                d.name as dex_name,
                COUNT(*) as total_collections,
                SUM(CASE WHEN cl.status = 'success' THEN 1 ELSE 0 END) as successful,
                SUM(CASE WHEN cl.status = 'failed' THEN 1 ELSE 0 END) as failed,
                AVG(
                    CASE WHEN cl.status = 'success' 
                    THEN EXTRACT(EPOCH FROM (cl.completed_at - cl.started_at)) * 1000 
                    ELSE NULL END
                ) as avg_duration_ms,
                MAX(cl.completed_at) as last_collection
            FROM collection_logs cl
            JOIN dexes d ON cl.dex_id = d.id
            WHERE cl.started_at >= NOW() - INTERVAL ':hours hours'
            GROUP BY d.name
            ORDER BY d.name
        """
        
        results = await self.db.fetch_all(query, {"hours": hours})
        
        return {
            'period_hours': hours,
            'dex_stats': [dict(row) for row in results]
        }
    
    async def close(self) -> None:
        """Close all adapter connections"""
        for adapter in self.adapters:
            try:
                await adapter.close()
            except Exception as e:
                logger.error(f"Error closing adapter {adapter.dex_name}: {e}")

