#!/usr/bin/env python3
"""
Update Strategy Config Target Margin

Updates the target_margin value in a strategy config.

Usage:
    python scripts/update_config_target_margin.py <config_name> <target_margin>
    python scripts/update_config_target_margin.py "Funding Arbitrage" 40
"""

import asyncio
import sys
import os
import json
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import dotenv
from databases import Database
from helpers.unified_logger import get_logger

dotenv.load_dotenv()

logger = get_logger("scripts", "update_config_target_margin")


async def update_target_margin(config_name: str, target_margin: float):
    """Update target_margin in a config."""
    
    database_url = os.getenv('DATABASE_URL')
    if not database_url:
        logger.error("DATABASE_URL environment variable is not set")
        sys.exit(1)
    
    db = Database(database_url)
    await db.connect()
    
    try:
        # Get current config
        config_row = await db.fetch_one(
            """
            SELECT id, config_name, config_data
            FROM strategy_configs
            WHERE config_name = :config_name
            """,
            {"config_name": config_name}
        )
        
        if not config_row:
            logger.error(f"Config '{config_name}' not found")
            sys.exit(1)
        
        config_id = config_row['id']
        config_data_raw = config_row['config_data']
        
        # Parse config data
        if isinstance(config_data_raw, str):
            config_dict = json.loads(config_data_raw)
        else:
            config_dict = config_data_raw
        
        old_target_margin = config_dict.get('target_margin')
        old_target_exposure = config_dict.get('target_exposure')
        
        # Update target_margin
        config_dict['target_margin'] = target_margin
        
        # Remove deprecated target_exposure if present
        if 'target_exposure' in config_dict:
            del config_dict['target_exposure']
            logger.info(f"Removed deprecated target_exposure: ${old_target_exposure:.2f}")
        
        # Update database
        updated_config_data = json.dumps(config_dict)
        
        await db.execute(
            """
            UPDATE strategy_configs
            SET config_data = CAST(:config_data AS jsonb),
                updated_at = NOW()
            WHERE id = :id
            """,
            {"config_data": updated_config_data, "id": config_id}
        )
        
        logger.info(f"✅ Updated config '{config_name}'")
        if old_target_margin:
            logger.info(f"   target_margin: ${old_target_margin:.2f} → ${target_margin:.2f}")
        else:
            logger.info(f"   target_margin: (none) → ${target_margin:.2f}")
        
        print(f"\n✅ Config updated successfully!")
        print(f"   Config: {config_name}")
        print(f"   target_margin: ${target_margin:.2f}")
        
    except Exception as e:
        logger.error(f"Error updating config: {e}", exc_info=True)
        raise
    finally:
        await db.disconnect()


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python scripts/update_config_target_margin.py <config_name> <target_margin>")
        print('Example: python scripts/update_config_target_margin.py "Funding Arbitrage" 40')
        sys.exit(1)
    
    config_name = sys.argv[1]
    try:
        target_margin = float(sys.argv[2])
    except ValueError:
        print(f"Error: target_margin must be a number, got: {sys.argv[2]}")
        sys.exit(1)
    
    try:
        asyncio.run(update_target_margin(config_name, target_margin))
        sys.exit(0)
    except Exception as e:
        logger.error(f"Failed to update config: {e}")
        sys.exit(1)

