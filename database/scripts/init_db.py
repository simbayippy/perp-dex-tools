#!/usr/bin/env python3
"""
Initialize the database schema

Usage:
    python database/scripts/init_db.py
"""

import asyncio
import sys
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from databases import Database

try:
    from funding_rate_service.config import settings
    from funding_rate_service.utils.logger import logger
except ImportError:
    # Fallback if funding_rate_service not available
    import os
    from dotenv import load_dotenv
    
    load_dotenv()
    
    class Settings:
        database_url = os.getenv('DATABASE_URL', 'postgresql://postgres:postgres@localhost:5432/perp_dex')
    
    settings = Settings()
    
    import logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)


async def init_database():
    """Initialize database with schema"""
    
    logger.info("Initializing database...")
    logger.info(f"Database URL: {settings.database_url.split('@')[1] if '@' in settings.database_url else 'localhost'}")
    
    # Connect to database
    db = Database(settings.database_url)
    await db.connect()
    
    try:
        # Read schema file from database/schema.sql
        schema_path = PROJECT_ROOT / "database" / "schema.sql"
        
        if not schema_path.exists():
            logger.error(f"Schema file not found: {schema_path}")
            logger.info("Please create the schema.sql file first")
            return False
        
        logger.info(f"Reading schema from: {schema_path}")
        schema_sql = schema_path.read_text()
        
        # Execute schema
        logger.info("Executing schema...")
        
        # Split by semicolon and execute each statement
        statements = [s.strip() for s in schema_sql.split(';') if s.strip()]
        
        for i, statement in enumerate(statements, 1):
            if statement:
                try:
                    await db.execute(statement)
                    logger.debug(f"Executed statement {i}/{len(statements)}")
                except Exception as e:
                    logger.warning(f"Statement {i} failed (may already exist): {e}")
        
        print("✅ Database schema initialized successfully!")
        
        # Verify tables created
        tables = await db.fetch_all("""
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'public'
            ORDER BY table_name
        """)
        
        logger.info(f"Created tables: {', '.join([t['table_name'] for t in tables])}")
        
        return True
        
    except Exception as e:
        logger.error(f"Error initializing database: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    finally:
        await db.disconnect()


if __name__ == "__main__":
    success = asyncio.run(init_database())
    sys.exit(0 if success else 1)

