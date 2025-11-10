#!/usr/bin/env python3
"""
Run database migration

Usage:
    python database/scripts/migrations/run_migration.py <migration_file>
    
Example:
    python database/scripts/migrations/run_migration.py database/migrations/001_add_dex_symbols_updated_at.sql
"""

import asyncio
import sys
from pathlib import Path

# Add project root to sys.path so imports work correctly
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database.connection import database

try:
    from funding_rate_service.utils.logger import logger
except ImportError:
    # Fallback if funding_rate_service logger not available
    import logging
    logger = logging.getLogger(__name__)


async def run_migration(migration_file: str):
    """Run a single migration file"""
    migration_path = Path(migration_file)
    
    if not migration_path.exists():
        print(f"❌ Migration file not found: {migration_file}")
        sys.exit(1)
    
    print(f"🔄 Running migration: {migration_path.name}")
    print("="*70)
    
    try:
        # Connect to database
        print("🔌 Connecting to database...")
        await database.connect()
        print("✅ Database connected\n")
        
        # Read migration SQL
        print(f"📄 Reading migration file...")
        migration_sql = migration_path.read_text()
        
        # Execute migration using raw connection (supports multiple statements)
        print(f"⚙️  Executing migration...\n")
        async with database.connection() as conn:
            # Get the raw asyncpg connection
            raw_conn = conn.raw_connection
            await raw_conn.execute(migration_sql)
        
        print("\n✅ Migration completed successfully!")
        
    except Exception as e:
        print(f"\n❌ Migration failed: {e}")
        logger.exception("Migration failed")
        sys.exit(1)
    
    finally:
        await database.disconnect()
        print("="*70)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python database/scripts/migrations/run_migration.py <migration_file>")
        print("\nExample:")
        print("  python database/scripts/migrations/run_migration.py database/migrations/001_add_dex_symbols_updated_at.sql")
        sys.exit(1)
    
    migration_file = sys.argv[1]
    asyncio.run(run_migration(migration_file))

