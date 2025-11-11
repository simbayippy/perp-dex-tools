#!/usr/bin/env python3
"""
Migration Script: Convert target_exposure to target_margin

Converts existing strategy configs that use target_exposure to use target_margin instead.
Uses a conservative 10x leverage assumption for conversion: margin = exposure / 10

Usage:
    python database/scripts/migrations/migrate_target_exposure_to_target_margin.py
"""

import asyncio
import sys
import os
import json
from pathlib import Path
from decimal import Decimal

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import dotenv
from databases import Database
from helpers.unified_logger import get_logger

dotenv.load_dotenv()

logger = get_logger("scripts", "migrate_target_exposure")


async def migrate_configs():
    """Migrate target_exposure to target_margin in existing configs."""
    
    logger.info("Starting migration: target_exposure -> target_margin")
    
    # Get database URL from environment
    database_url = os.getenv('DATABASE_URL')
    if not database_url:
        logger.error("DATABASE_URL environment variable is not set")
        sys.exit(1)
    
    db = Database(database_url)
    await db.connect()
    
    try:
        # Find all configs with target_exposure but no target_margin
        configs_to_migrate = await db.fetch_all("""
            SELECT id, config_name, strategy_type, config_data
            FROM strategy_configs
            WHERE strategy_type = 'funding_arbitrage'
            AND config_data::text LIKE '%target_exposure%'
            AND (config_data::text NOT LIKE '%target_margin%' OR config_data->>'target_margin' IS NULL)
        """)
        
        if not configs_to_migrate:
            logger.info("No configs found that need migration")
            return
        
        logger.info(f"Found {len(configs_to_migrate)} config(s) to migrate")
        
        migrated_count = 0
        skipped_count = 0
        
        for config_row in configs_to_migrate:
            config_id = config_row['id']
            config_name = config_row['config_name']
            config_data = config_row['config_data']
            
            # Handle JSONB - might be dict or string
            if isinstance(config_data, str):
                config_dict = json.loads(config_data)
            else:
                config_dict = config_data
            
            # Check if target_exposure exists
            target_exposure = config_dict.get('target_exposure')
            target_margin = config_dict.get('target_margin')
            
            if target_exposure is None:
                logger.debug(f"Skipping {config_name} (id: {config_id}): no target_exposure found")
                skipped_count += 1
                continue
            
            if target_margin is not None:
                logger.debug(f"Skipping {config_name} (id: {config_id}): already has target_margin")
                skipped_count += 1
                continue
            
            # Convert target_exposure to target_margin
            # Using 10x leverage assumption: margin = exposure / 10
            target_exposure_value = float(target_exposure)
            target_margin_value = target_exposure_value / 10.0
            
            # Update config_dict
            config_dict['target_margin'] = target_margin_value
            # Remove target_exposure (or keep it for backward compatibility - we'll remove it)
            # Actually, let's keep it for now but mark it as deprecated
            # The strategy code will handle the conversion
            
            logger.info(
                f"Migrating {config_name} (id: {config_id}): "
                f"target_exposure=${target_exposure_value:.2f} -> target_margin=${target_margin_value:.2f}"
            )
            
            # Update database
            await db.execute(
                """
                UPDATE strategy_configs
                SET config_data = CAST(:config_data AS jsonb),
                    updated_at = NOW()
                WHERE id = :id
                """,
                {
                    'id': config_id,
                    'config_data': json.dumps(config_dict)
                }
            )
            
            migrated_count += 1
        
        logger.info(f"Migration complete: {migrated_count} migrated, {skipped_count} skipped")
        
        # Show summary
        print("\n" + "="*70)
        print("Migration Summary:")
        print("="*70)
        print(f"  ✅ Migrated: {migrated_count}")
        print(f"  ⏭️  Skipped: {skipped_count}")
        print("="*70 + "\n")
        
    except Exception as e:
        logger.error(f"Error during migration: {e}", exc_info=True)
        raise
    finally:
        await db.disconnect()


if __name__ == "__main__":
    try:
        asyncio.run(migrate_configs())
        sys.exit(0)
    except Exception as e:
        logger.error(f"Migration failed: {e}")
        sys.exit(1)

