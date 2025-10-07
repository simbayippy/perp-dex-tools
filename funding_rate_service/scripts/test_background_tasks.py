#!/usr/bin/env python3
"""
Test Background Tasks

Script to test the background task system independently.
Useful for debugging and verifying task functionality.
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "funding_rate_service"))

from tasks.collection_task import CollectionTask
from tasks.opportunity_task import OpportunityTask
from tasks.cleanup_task import CleanupTask
from tasks.scheduler import TaskScheduler
from database.connection import database
from core.mappers import dex_mapper, symbol_mapper
from utils.logger import logger


async def test_individual_tasks():
    """Test each task individually"""
    logger.info("🧪 Testing individual background tasks...")
    
    try:
        # Connect to database
        await database.connect()
        
        # Load mappers
        await dex_mapper.load_from_db(database)
        await symbol_mapper.load_from_db(database)
        
        # Test Collection Task
        logger.info("\n📊 Testing Collection Task...")
        collection_task = CollectionTask()
        collection_result = await collection_task.run()
        logger.info(f"Collection Result: {collection_result}")
        
        # Test Opportunity Task
        logger.info("\n📈 Testing Opportunity Task...")
        opportunity_task = OpportunityTask()
        opportunity_result = await opportunity_task.run()
        logger.info(f"Opportunity Result: {opportunity_result}")
        
        # Get cached opportunities
        cached = opportunity_task.get_cached_opportunities('best_overall')
        logger.info(f"Cached opportunities: {len(cached)}")
        
        # Test Cleanup Task (dry run - won't delete much in test)
        logger.info("\n🧹 Testing Cleanup Task...")
        cleanup_task = CleanupTask()
        cleanup_result = await cleanup_task.run()
        logger.info(f"Cleanup Result: {cleanup_result}")
        
        # Print task metrics
        logger.info("\n📊 Task Metrics:")
        logger.info(f"Collection Task: {collection_task.get_metrics()}")
        logger.info(f"Opportunity Task: {opportunity_task.get_metrics()}")
        logger.info(f"Cleanup Task: {cleanup_task.get_metrics()}")
        
        # Cleanup
        await collection_task.close()
        
    except Exception as e:
        logger.error(f"Error testing individual tasks: {e}", exc_info=True)
    finally:
        await database.disconnect()


async def test_scheduler():
    """Test the task scheduler"""
    logger.info("🕐 Testing Task Scheduler...")
    
    try:
        # Connect to database
        await database.connect()
        
        # Load mappers
        await dex_mapper.load_from_db(database)
        await symbol_mapper.load_from_db(database)
        
        # Create and start scheduler
        scheduler = TaskScheduler()
        await scheduler.start()
        
        logger.info("✅ Scheduler started successfully")
        
        # Get status
        status = scheduler.get_scheduler_status()
        logger.info(f"Scheduler Status: {status}")
        
        # Get task health
        health = scheduler.get_task_health()
        logger.info(f"Task Health: {health}")
        
        # Force run collection job
        logger.info("\n🔄 Force running collection job...")
        collection_result = await scheduler.force_run_job('collection_job')
        logger.info(f"Force Collection Result: {collection_result}")
        
        # Force run opportunity job
        logger.info("\n🔄 Force running opportunity job...")
        opportunity_result = await scheduler.force_run_job('opportunity_job')
        logger.info(f"Force Opportunity Result: {opportunity_result}")
        
        # Get cached opportunities
        cached = scheduler.get_cached_opportunities('best_overall')
        logger.info(f"Cached opportunities: {len(cached)}")
        if cached:
            logger.info(f"Top opportunity: {cached[0]}")
        
        # Let scheduler run for a bit (optional - comment out for quick test)
        # logger.info("⏰ Letting scheduler run for 2 minutes...")
        # await asyncio.sleep(120)
        
        # Shutdown
        await scheduler.shutdown()
        logger.info("✅ Scheduler shutdown complete")
        
    except Exception as e:
        logger.error(f"Error testing scheduler: {e}", exc_info=True)
    finally:
        await database.disconnect()


async def test_api_endpoints():
    """Test API endpoints (requires running server)"""
    import aiohttp
    
    logger.info("🌐 Testing API endpoints...")
    
    base_url = "http://localhost:8000/api/v1"
    
    endpoints_to_test = [
        "/tasks/status",
        "/tasks/health",
        "/tasks/metrics",
        "/tasks/opportunities/cached?cache_type=best_overall",
    ]
    
    async with aiohttp.ClientSession() as session:
        for endpoint in endpoints_to_test:
            try:
                url = f"{base_url}{endpoint}"
                logger.info(f"Testing: {url}")
                
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.json()
                        logger.info(f"✅ {endpoint}: {response.status}")
                        # logger.info(f"Response: {data}")  # Uncomment for full response
                    else:
                        logger.error(f"❌ {endpoint}: {response.status}")
                        
            except Exception as e:
                logger.error(f"❌ {endpoint}: {e}")


async def main():
    """Main test function"""
    logger.info("🚀 Starting Background Tasks Test Suite")
    
    import argparse
    parser = argparse.ArgumentParser(description="Test background tasks")
    parser.add_argument('--test', choices=['tasks', 'scheduler', 'api', 'all'], 
                       default='all', help='Which tests to run')
    args = parser.parse_args()
    
    if args.test in ['tasks', 'all']:
        await test_individual_tasks()
    
    if args.test in ['scheduler', 'all']:
        await test_scheduler()
    
    if args.test in ['api', 'all']:
        logger.info("\n⚠️ API tests require the server to be running on localhost:8000")
        logger.info("Start the server with: python main.py")
        logger.info("Then run: python scripts/test_background_tasks.py --test api")
        
        # Uncomment to run API tests (requires server running)
        # await test_api_endpoints()
    
    logger.info("🎉 Background Tasks Test Suite Complete!")


if __name__ == "__main__":
    asyncio.run(main())
