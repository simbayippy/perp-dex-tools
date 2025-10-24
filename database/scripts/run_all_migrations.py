#!/usr/bin/env python3
"""
Run all pending database migrations

This is a standalone script that can be run manually to apply migrations.
The migrations will also run automatically on startup of main.py or run_tasks.py.

Usage:
    python database/scripts/run_all_migrations.py
"""

import asyncio
import sys
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database.connection import database
from database.migration_manager import run_startup_migrations

try:
    from funding_rate_service.utils.logger import logger
except ImportError:
    # Fallback if funding_rate_service logger not available
    import logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)


async def main():
    """Run all pending migrations"""
    logger.info("🔄 Running all pending database migrations...")
    logger.info("="*70)
    
    try:
        # Connect to database
        logger.info("🔌 Connecting to database...")
        await database.connect()
        logger.info("✅ Database connected")
        
        # Run migrations
        success = await run_startup_migrations(database)
        
        if success:
            logger.info("✅ All migrations completed successfully!")
            logger.info("="*70)
            return True
        else:
            logger.error("❌ Migration process failed!")
            logger.info("="*70)
            return False
        
    except Exception as e:
        logger.error(f"❌ Error running migrations: {e}")
        return False
    
    finally:
        await database.disconnect()


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)

