#!/usr/bin/env python3
"""
Delete a strategy from database by run_id.

Usage:
    python scripts/strategies/delete_strategy.py <run_id>
"""

import asyncio
import sys
import os
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import dotenv
from databases import Database

dotenv.load_dotenv()


async def delete_strategy(run_id: str):
    """Delete strategy from database."""
    # Build database URL from environment or use provided credentials
    database_url = os.getenv('DATABASE_URL')
    
    if not database_url:
        # Try to construct from common defaults
        db_user = os.getenv('DB_USER', 'funding_user')
        db_password = os.getenv('DB_PASSWORD', 'simba2001###')
        db_host = os.getenv('DB_HOST', 'localhost')
        db_port = os.getenv('DB_PORT', '5432')
        db_name = os.getenv('DB_NAME', 'perp_dex')
        database_url = f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
    
    db = Database(database_url)
    await db.connect()
    
    try:
        # Handle short run_id (8 chars) - find matching UUID
        if len(run_id) < 32:
            # Query by partial UUID match (first 8 characters)
            query = """
                DELETE FROM strategy_runs 
                WHERE id::text LIKE :run_id_pattern
            """
            result = await db.execute(query, {"run_id_pattern": f"{run_id}%"})
        else:
            # Full UUID provided
            query = """
                DELETE FROM strategy_runs 
                WHERE id = :run_id
            """
            result = await db.execute(query, {"run_id": run_id})
        
        print(f"✅ Deleted strategy {run_id} from database")
        return True
        
    except Exception as e:
        print(f"❌ Error deleting strategy: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        await db.disconnect()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/strategies/delete_strategy.py <run_id>")
        sys.exit(1)
    
    run_id = sys.argv[1]
    success = asyncio.run(delete_strategy(run_id))
    sys.exit(0 if success else 1)

