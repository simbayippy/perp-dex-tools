"""
Stale Market Data Cleanup Task

Periodic task to clean up stale market data (volume/OI) from dex_symbols table.
Runs every 10 minutes to remove data older than 10 minutes, keeping the database
clean and ensuring filters work correctly.

This is separate from the daily cleanup task which handles historical data retention.
"""

from typing import Dict, Any

from funding_rate_service.tasks.base_task import BaseTask
from database.connection import database
from funding_rate_service.utils.logger import logger

# Exchanges to exclude from cleanup (e.g., EdgeX which is no longer collected)
EXCLUDED_EXCHANGES = {"edgex"}


class StaleMarketDataCleanupTask(BaseTask):
    """
    Background task for cleaning stale market data (volume/OI)
    
    This task:
    1. Finds market data records older than threshold (default: 10 minutes)
    2. Sets volume_24h, open_interest_usd, and updated_at to NULL
    3. Excludes specific exchanges (e.g., EdgeX)
    4. Reports cleanup statistics
    
    Designed for frequent execution (every 10 minutes) to keep market data fresh.
    """
    
    def __init__(self, max_retries: int = 1, age_minutes: int = 10):
        """
        Initialize stale market data cleanup task
        
        Args:
            max_retries: Max retries (lower for maintenance tasks)
            age_minutes: Minimum age in minutes to consider stale (default: 10)
        """
        super().__init__("stale_market_data_cleanup", max_retries)
        self.age_minutes = age_minutes
    
    async def execute(self) -> Dict[str, Any]:
        """
        Execute stale market data cleanup
        
        Returns:
            Dictionary with cleanup results and statistics
        """
        logger.info(f"Starting stale market data cleanup (age threshold: {self.age_minutes} minutes)...")
        
        cleanup_results = {
            'records_cleaned': 0,
            'age_threshold_minutes': self.age_minutes,
            'excluded_exchanges': list(EXCLUDED_EXCHANGES),
            'cleanup_timestamp': None
        }
        
        # Build cleanup query using PostgreSQL INTERVAL syntax
        # This avoids timezone issues by letting PostgreSQL handle the comparison
        query = f"""
            UPDATE dex_symbols ds
            SET 
                volume_24h = NULL,
                open_interest_usd = NULL,
                updated_at = NULL
            FROM dexes d
            WHERE ds.dex_id = d.id
            AND ds.updated_at IS NOT NULL
            AND ds.updated_at < NOW() - INTERVAL '{self.age_minutes} minutes'
            AND d.is_active = TRUE
        """
        
        # Exclude EdgeX and other excluded exchanges
        params = {}
        excluded_list = list(EXCLUDED_EXCHANGES)
        if excluded_list:
            placeholders = ','.join([f":excluded_{i}" for i in range(len(excluded_list))])
            query += f" AND d.name NOT IN ({placeholders})"
            for i, dex in enumerate(excluded_list):
                params[f"excluded_{i}"] = dex.lower()
        
        try:
            # Execute cleanup
            records_cleaned = await database.execute(query, values=params if params else None)
            cleanup_results['records_cleaned'] = records_cleaned
            
            if records_cleaned > 0:
                logger.info(
                    f"✅ Stale market data cleanup complete: {records_cleaned} records cleaned "
                    f"(age threshold: {self.age_minutes} minutes)"
                )
            else:
                logger.debug(
                    f"✅ Stale market data cleanup complete: No stale records found "
                    f"(age threshold: {self.age_minutes} minutes)"
                )
            
            cleanup_results['cleanup_timestamp'] = None  # Will be set by base task
            
        except Exception as e:
            logger.error(f"❌ Failed to clean up stale market data: {e}", exc_info=True)
            raise
        
        return cleanup_results
    
    async def force_cleanup(self) -> Dict[str, Any]:
        """
        Force an immediate cleanup operation
        
        Returns:
            Cleanup results
        """
        logger.info("🔄 Force stale market data cleanup triggered")
        return await self.run()

