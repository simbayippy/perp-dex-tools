"""
Cleanup Task

Daily maintenance task to clean up old data and optimize database performance.
Runs once per day to keep the database size manageable on VPS.
"""

from typing import Dict, Any
from datetime import datetime, timedelta

from funding_rate_service.tasks.base_task import BaseTask
from funding_rate_service.database.connection import database
from funding_rate_service.utils.logger import logger


class CleanupTask(BaseTask):
    """
    Background task for database cleanup and maintenance
    
    This task:
    1. Removes old funding rate records (keep last 90 days)
    2. Removes old opportunity records (keep last 7 days)
    3. Removes old collection logs (keep last 30 days)
    4. Optimizes database performance
    5. Reports storage savings
    
    Designed for daily execution on VPS to maintain optimal performance.
    """
    
    def __init__(self, max_retries: int = 1):
        """
        Initialize cleanup task
        
        Args:
            max_retries: Max retries (lower for maintenance tasks)
        """
        super().__init__("database_cleanup", max_retries)
        
        # Retention policies (configurable)
        self.funding_rates_retention_days = 90  # Keep 90 days of funding rates
        self.opportunities_retention_days = 7   # Keep 7 days of opportunities
        self.collection_logs_retention_days = 30  # Keep 30 days of logs
    
    async def execute(self) -> Dict[str, Any]:
        """
        Execute database cleanup operations
        
        Returns:
            Dictionary with cleanup results and statistics
        """
        logger.info("Starting database cleanup...")
        
        cleanup_results = {
            'funding_rates_deleted': 0,
            'opportunities_deleted': 0,
            'collection_logs_deleted': 0,
            'total_records_deleted': 0,
            'cleanup_timestamp': datetime.utcnow().isoformat(),
            'operations_completed': []
        }
        
        # 1. Clean up old funding rates (keep last 90 days)
        logger.info(f"Cleaning up funding rates older than {self.funding_rates_retention_days} days...")
        
        funding_rates_cutoff = datetime.utcnow() - timedelta(days=self.funding_rates_retention_days)
        funding_rates_query = """
            DELETE FROM funding_rates 
            WHERE time < $1
        """
        
        try:
            funding_rates_deleted = await database.execute(funding_rates_query, funding_rates_cutoff)
            cleanup_results['funding_rates_deleted'] = funding_rates_deleted
            cleanup_results['operations_completed'].append('funding_rates_cleanup')
            logger.info(f"✅ Deleted {funding_rates_deleted} old funding rate records")
        except Exception as e:
            logger.error(f"❌ Failed to clean up funding rates: {e}")
            # Continue with other cleanup operations
        
        # 2. Clean up old opportunities (keep last 7 days)
        logger.info(f"Cleaning up opportunities older than {self.opportunities_retention_days} days...")
        
        opportunities_cutoff = datetime.utcnow() - timedelta(days=self.opportunities_retention_days)
        opportunities_query = """
            DELETE FROM opportunities 
            WHERE discovered_at < $1
        """
        
        try:
            opportunities_deleted = await database.execute(opportunities_query, opportunities_cutoff)
            cleanup_results['opportunities_deleted'] = opportunities_deleted
            cleanup_results['operations_completed'].append('opportunities_cleanup')
            logger.info(f"✅ Deleted {opportunities_deleted} old opportunity records")
        except Exception as e:
            logger.error(f"❌ Failed to clean up opportunities: {e}")
        
        # 3. Clean up old collection logs (keep last 30 days)
        logger.info(f"Cleaning up collection logs older than {self.collection_logs_retention_days} days...")
        
        logs_cutoff = datetime.utcnow() - timedelta(days=self.collection_logs_retention_days)
        logs_query = """
            DELETE FROM collection_logs 
            WHERE started_at < $1
        """
        
        try:
            logs_deleted = await database.execute(logs_query, logs_cutoff)
            cleanup_results['collection_logs_deleted'] = logs_deleted
            cleanup_results['operations_completed'].append('collection_logs_cleanup')
            logger.info(f"✅ Deleted {logs_deleted} old collection log records")
        except Exception as e:
            logger.error(f"❌ Failed to clean up collection logs: {e}")
        
        # 4. Database maintenance operations
        logger.info("Running database maintenance operations...")
        
        try:
            # Update table statistics for query optimization
            await database.execute("ANALYZE funding_rates")
            await database.execute("ANALYZE opportunities") 
            await database.execute("ANALYZE latest_funding_rates")
            await database.execute("ANALYZE collection_logs")
            
            cleanup_results['operations_completed'].append('analyze_tables')
            logger.info("✅ Updated table statistics")
        except Exception as e:
            logger.error(f"❌ Failed to analyze tables: {e}")
        
        try:
            # Vacuum to reclaim space (light vacuum, not full)
            await database.execute("VACUUM (ANALYZE)")
            cleanup_results['operations_completed'].append('vacuum_database')
            logger.info("✅ Vacuumed database")
        except Exception as e:
            logger.error(f"❌ Failed to vacuum database: {e}")
        
        # 5. Get database size statistics
        try:
            size_stats = await self._get_database_size_stats()
            cleanup_results['database_size_stats'] = size_stats
            cleanup_results['operations_completed'].append('size_statistics')
        except Exception as e:
            logger.error(f"❌ Failed to get database size stats: {e}")
        
        # Calculate totals
        cleanup_results['total_records_deleted'] = (
            cleanup_results['funding_rates_deleted'] + 
            cleanup_results['opportunities_deleted'] + 
            cleanup_results['collection_logs_deleted']
        )
        
        # Log summary
        logger.info(
            f"🧹 Database Cleanup Complete: "
            f"{cleanup_results['total_records_deleted']} total records deleted, "
            f"{len(cleanup_results['operations_completed'])} operations completed"
        )
        
        if cleanup_results['total_records_deleted'] > 0:
            logger.info(
                f"📊 Breakdown: {cleanup_results['funding_rates_deleted']} funding rates, "
                f"{cleanup_results['opportunities_deleted']} opportunities, "
                f"{cleanup_results['collection_logs_deleted']} logs"
            )
        
        return cleanup_results
    
    async def _get_database_size_stats(self) -> Dict[str, Any]:
        """
        Get database size statistics
        
        Returns:
            Dictionary with size statistics
        """
        # Get table sizes
        table_sizes_query = """
            SELECT 
                schemaname,
                tablename,
                pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) as size,
                pg_total_relation_size(schemaname||'.'||tablename) as size_bytes
            FROM pg_tables 
            WHERE schemaname = 'public'
            ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC
        """
        
        table_sizes = await database.fetch_all(table_sizes_query)
        
        # Get total database size
        db_size_query = "SELECT pg_size_pretty(pg_database_size(current_database())) as total_size"
        db_size_result = await database.fetch_one(db_size_query)
        
        # Get record counts for main tables
        record_counts_query = """
            SELECT 
                'funding_rates' as table_name,
                COUNT(*) as record_count
            FROM funding_rates
            UNION ALL
            SELECT 
                'latest_funding_rates' as table_name,
                COUNT(*) as record_count
            FROM latest_funding_rates
            UNION ALL
            SELECT 
                'opportunities' as table_name,
                COUNT(*) as record_count
            FROM opportunities
            UNION ALL
            SELECT 
                'collection_logs' as table_name,
                COUNT(*) as record_count
            FROM collection_logs
        """
        
        record_counts = await database.fetch_all(record_counts_query)
        
        return {
            'total_database_size': db_size_result['total_size'] if db_size_result else 'Unknown',
            'table_sizes': [
                {
                    'table_name': row['tablename'],
                    'size': row['size'],
                    'size_bytes': row['size_bytes']
                }
                for row in table_sizes
            ],
            'record_counts': [
                {
                    'table_name': row['table_name'],
                    'record_count': row['record_count']
                }
                for row in record_counts
            ]
        }
    
    async def get_retention_policy(self) -> Dict[str, int]:
        """
        Get current retention policy settings
        
        Returns:
            Dictionary with retention days for each data type
        """
        return {
            'funding_rates_retention_days': self.funding_rates_retention_days,
            'opportunities_retention_days': self.opportunities_retention_days,
            'collection_logs_retention_days': self.collection_logs_retention_days
        }
    
    def update_retention_policy(
        self,
        funding_rates_days: int = None,
        opportunities_days: int = None,
        collection_logs_days: int = None
    ) -> None:
        """
        Update retention policy settings
        
        Args:
            funding_rates_days: Days to keep funding rates
            opportunities_days: Days to keep opportunities
            collection_logs_days: Days to keep collection logs
        """
        if funding_rates_days is not None:
            self.funding_rates_retention_days = funding_rates_days
            logger.info(f"Updated funding rates retention to {funding_rates_days} days")
        
        if opportunities_days is not None:
            self.opportunities_retention_days = opportunities_days
            logger.info(f"Updated opportunities retention to {opportunities_days} days")
        
        if collection_logs_days is not None:
            self.collection_logs_retention_days = collection_logs_days
            logger.info(f"Updated collection logs retention to {collection_logs_days} days")
    
    async def force_cleanup(self) -> Dict[str, Any]:
        """
        Force an immediate cleanup operation
        
        Returns:
            Cleanup results
        """
        logger.info("🔄 Force cleanup triggered")
        return await self.run()
