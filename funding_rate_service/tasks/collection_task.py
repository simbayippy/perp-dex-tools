"""
Collection Task

Periodic task to collect funding rates from all configured DEX adapters.
Runs every 60 seconds to keep data fresh for the API.
"""

from typing import Dict, Any, List
from datetime import datetime

from funding_rate_service.tasks.base_task import BaseTask
from funding_rate_service.collection.orchestrator import CollectionOrchestrator
from funding_rate_service.database.connection import database
from exchange_clients.lighter import LighterFundingAdapter
from exchange_clients.grvt import GrvtFundingAdapter
from exchange_clients.edgex import EdgeXFundingAdapter
from exchange_clients.paradex import ParadexFundingAdapter
from exchange_clients.backpack import BackpackFundingAdapter
from exchange_clients.aster import AsterFundingAdapter
from funding_rate_service.utils.logger import logger


class CollectionTask(BaseTask):
    """
    Background task for periodic funding rate collection
    
    This task:
    1. Initializes all available DEX adapters
    2. Uses the CollectionOrchestrator to fetch rates in parallel
    3. Stores results in the database
    4. Updates mappers with new symbols
    5. Tracks collection metrics
    
    Designed for 24/7 operation on VPS with robust error handling.
    """
    
    def __init__(self, max_retries: int = 2):
        """
        Initialize collection task
        
        Args:
            max_retries: Max retries per collection cycle (lower for frequent runs)
        """
        super().__init__("funding_rate_collection", max_retries)
        self.orchestrator = None
        self._adapters_initialized = False
    
    async def _initialize_adapters(self) -> List:
        """
        Initialize all available DEX adapters
        
        Returns:
            List of initialized adapters
        """
        if self._adapters_initialized and self.orchestrator:
            return self.orchestrator.adapters
        
        logger.info("Initializing DEX adapters for collection...")
        
        adapters = []
        
        # Initialize each adapter with error handling
        adapter_configs = [
            ("Lighter", LighterFundingAdapter),
            ("GRVT", GrvtFundingAdapter), 
            ("EdgeX", EdgeXFundingAdapter),
            ("Paradex", ParadexFundingAdapter),
            ("Backpack", BackpackFundingAdapter),
            ("Aster", AsterFundingAdapter),
        ]
        
        for name, adapter_class in adapter_configs:
            try:
                adapter = adapter_class()
                adapters.append(adapter)
                logger.info(f"✅ Initialized {name} adapter")
            except Exception as e:
                logger.error(f"❌ Failed to initialize {name} adapter: {e}")
                # Continue with other adapters - don't fail the whole task
        
        if not adapters:
            raise RuntimeError("No DEX adapters could be initialized")
        
        # Initialize orchestrator
        self.orchestrator = CollectionOrchestrator(
            db=database,
            adapters=adapters
        )
        
        self._adapters_initialized = True
        logger.info(f"Collection orchestrator initialized with {len(adapters)} adapters")
        
        return adapters
    
    async def execute(self) -> Dict[str, Any]:
        """
        Execute funding rate collection from all DEXs
        
        Returns:
            Dictionary with collection results and metrics
        """
        # Ensure adapters are initialized
        adapters = await self._initialize_adapters()
        
        logger.info(f"Starting funding rate collection from {len(adapters)} DEXs...")
        
        # Run collection
        collection_summary = await self.orchestrator.collect_all_rates(
            include_market_data=True  # Also collect volume/OI data
        )
        
        # Log detailed results
        successful_dexes = []
        failed_dexes = []
        
        for dex_name, result in collection_summary['results'].items():
            if result.get('success', False):
                successful_dexes.append({
                    'dex': dex_name,
                    'rates_count': result.get('rates_count', 0),
                    'latency_ms': result.get('latency_ms', 0),
                    'new_symbols': result.get('new_symbols', 0)
                })
            else:
                failed_dexes.append({
                    'dex': dex_name,
                    'error': result.get('error', 'Unknown error')
                })
        
        # Log summary
        if successful_dexes:
            total_rates = sum(dex['rates_count'] for dex in successful_dexes)
            total_new_symbols = sum(dex['new_symbols'] for dex in successful_dexes)
            avg_latency = sum(dex['latency_ms'] for dex in successful_dexes) / len(successful_dexes)
            
            logger.info(
                f"📊 Collection Summary: {len(successful_dexes)}/{len(adapters)} DEXs successful, "
                f"{total_rates} rates collected, {total_new_symbols} new symbols discovered, "
                f"avg latency: {avg_latency:.1f}ms"
            )
        
        if failed_dexes:
            logger.warning(f"⚠️ {len(failed_dexes)} DEXs failed: {[d['dex'] for d in failed_dexes]}")
        
        # Return detailed results
        return {
            'total_adapters': collection_summary['total_adapters'],
            'successful_dexes': len(successful_dexes),
            'failed_dexes': len(failed_dexes),
            'total_rates_collected': collection_summary['total_rates'],
            'duration_seconds': collection_summary['duration_seconds'],
            'successful_dex_details': successful_dexes,
            'failed_dex_details': failed_dexes,
            'collection_timestamp': datetime.utcnow().isoformat()
        }
    
    async def get_adapter_health(self) -> Dict[str, Any]:
        """
        Get health status of all adapters
        
        Returns:
            Dictionary with adapter health information
        """
        if not self._adapters_initialized or not self.orchestrator:
            return {"status": "not_initialized", "adapters": []}
        
        adapter_health = []
        for adapter in self.orchestrator.adapters:
            # Basic health check - could be expanded
            adapter_health.append({
                "dex_name": adapter.dex_name,
                "class_name": adapter.__class__.__name__,
                "initialized": True,
                # Could add more health metrics here
            })
        
        return {
            "status": "initialized",
            "total_adapters": len(self.orchestrator.adapters),
            "adapters": adapter_health
        }
    
    async def force_collection(self) -> Dict[str, Any]:
        """
        Force an immediate collection (useful for testing/manual triggers)
        
        Returns:
            Collection results
        """
        logger.info("🔄 Force collection triggered")
        return await self.run()
    
    async def close(self) -> None:
        """Clean up resources"""
        if self.orchestrator:
            await self.orchestrator.close()
            logger.info("Collection orchestrator closed")
