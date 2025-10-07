"""
Database Migration Manager

Automatically checks and runs pending migrations on startup.
"""

import asyncio
from pathlib import Path
from typing import List
from databases import Database
from utils.logger import logger


class MigrationManager:
    """Manages database migrations"""
    
    def __init__(self, database: Database):
        self.database = database
        self.migrations_dir = Path(__file__).parent / "migrations"
    
    async def ensure_migration_table(self):
        """Create migrations tracking table if it doesn't exist"""
        await self.database.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                id SERIAL PRIMARY KEY,
                filename VARCHAR(255) UNIQUE NOT NULL,
                executed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
    
    async def get_executed_migrations(self) -> List[str]:
        """Get list of already executed migrations"""
        try:
            rows = await self.database.fetch_all(
                "SELECT filename FROM schema_migrations ORDER BY filename"
            )
            return [row['filename'] for row in rows]
        except Exception:
            # Table might not exist yet
            return []
    
    async def get_pending_migrations(self) -> List[Path]:
        """Get list of pending migration files"""
        if not self.migrations_dir.exists():
            logger.warning(f"Migrations directory not found: {self.migrations_dir}")
            return []
        
        # Get all .sql files
        all_migrations = sorted(self.migrations_dir.glob("*.sql"))
        
        # Get executed migrations
        executed = await self.get_executed_migrations()
        
        # Filter to pending only
        pending = [m for m in all_migrations if m.name not in executed]
        
        return pending
    
    async def run_migration(self, migration_file: Path) -> bool:
        """Run a single migration file"""
        try:
            logger.info(f"Running migration: {migration_file.name}")
            
            # Read migration SQL
            migration_sql = migration_file.read_text()
            
            # Execute migration using raw connection (supports multiple statements)
            async with self.database.connection() as conn:
                raw_conn = conn.raw_connection
                await raw_conn.execute(migration_sql)
            
            # Record migration as executed
            await self.database.execute(
                "INSERT INTO schema_migrations (filename) VALUES (:filename)",
                {"filename": migration_file.name}
            )
            
            logger.info(f"✅ Migration completed: {migration_file.name}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Migration failed: {migration_file.name} - {e}")
            return False
    
    async def run_pending_migrations(self) -> bool:
        """Run all pending migrations"""
        try:
            # Ensure migration tracking table exists
            await self.ensure_migration_table()
            
            # Get pending migrations
            pending = await self.get_pending_migrations()
            
            if not pending:
                logger.info("✅ No pending migrations")
                return True
            
            logger.info(f"🔄 Running {len(pending)} pending migrations...")
            
            # Run each migration
            success_count = 0
            for migration_file in pending:
                if await self.run_migration(migration_file):
                    success_count += 1
                else:
                    logger.error(f"Migration failed, stopping: {migration_file.name}")
                    return False
            
            logger.info(f"✅ Successfully ran {success_count} migrations")
            return True
            
        except Exception as e:
            logger.error(f"❌ Migration process failed: {e}")
            return False
    
    async def check_schema_health(self) -> bool:
        """Check if database schema is healthy (all expected columns exist)"""
        try:
            # Check for the specific column that was causing issues
            result = await self.database.fetch_one("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'dex_symbols' 
                AND column_name IN ('updated_at', 'last_updated')
            """)
            
            if not result:
                logger.warning("⚠️ dex_symbols table missing timestamp columns")
                return False
            
            # Check if it's the old column name
            if result['column_name'] == 'last_updated':
                logger.warning("⚠️ dex_symbols still uses old 'last_updated' column name")
                return False
            
            logger.debug("✅ Schema health check passed")
            return True
            
        except Exception as e:
            logger.error(f"Schema health check failed: {e}")
            return False


# Global instance
migration_manager = None


async def initialize_migration_manager(database: Database) -> MigrationManager:
    """Initialize global migration manager"""
    global migration_manager
    migration_manager = MigrationManager(database)
    return migration_manager


async def run_startup_migrations(database: Database) -> bool:
    """Run migrations on startup (called from main.py)"""
    try:
        logger.info("🔍 Checking for pending database migrations...")
        
        manager = await initialize_migration_manager(database)
        
        # Check schema health first
        is_healthy = await manager.check_schema_health()
        
        if not is_healthy:
            logger.info("🔧 Schema issues detected, running migrations...")
            success = await manager.run_pending_migrations()
            if not success:
                logger.error("❌ Failed to run migrations")
                return False
        
        # Final health check
        final_health = await manager.check_schema_health()
        if not final_health:
            logger.error("❌ Schema still unhealthy after migrations")
            return False
        
        logger.info("✅ Database schema is up to date")
        return True
        
    except Exception as e:
        logger.error(f"❌ Startup migration check failed: {e}")
        return False
