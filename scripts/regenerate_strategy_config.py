#!/usr/bin/env python3
"""
Regenerate Strategy Config File

Regenerates the config file for a strategy from the database.
Useful when database config has been updated but the temp file is stale.

Usage:
    python scripts/regenerate_strategy_config.py <run_id>  # Short (8 chars) or full UUID
"""

import asyncio
import sys
import os
import tempfile
from pathlib import Path
from typing import Optional

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import dotenv
from databases import Database
from helpers.unified_logger import get_logger
import yaml
from decimal import Decimal
from datetime import datetime

dotenv.load_dotenv()

logger = get_logger("scripts", "regenerate_strategy_config")


async def regenerate_config(run_id: str):
    """Regenerate config file from database."""
    
    database_url = os.getenv('DATABASE_URL')
    if not database_url:
        logger.error("DATABASE_URL environment variable is not set")
        sys.exit(1)
    
    db = Database(database_url)
    await db.connect()
    
    try:
        # Handle short run_id (8 chars) - find matching UUID
        if len(run_id) < 32:
            query = """
                SELECT id, config_id
                FROM strategy_runs
                WHERE id::text LIKE :run_id_pattern
                ORDER BY started_at DESC
                LIMIT 1
            """
            row = await db.fetch_one(query, {"run_id_pattern": f"{run_id}%"})
        else:
            query = """
                SELECT id, config_id
                FROM strategy_runs
                WHERE id = :run_id
            """
            row = await db.fetch_one(query, {"run_id": run_id})
        
        if not row:
            logger.error(f"Strategy {run_id} not found")
            sys.exit(1)
        
        full_run_id = str(row['id'])
        config_id = row['config_id']
        
        if not config_id:
            logger.error(f"Strategy {full_run_id[:8]} has no config_id")
            sys.exit(1)
        
        # Get config from database
        config_row = await db.fetch_one(
            """
            SELECT config_name, config_data, strategy_type
            FROM strategy_configs
            WHERE id = :config_id
            """,
            {"config_id": config_id}
        )
        
        if not config_row:
            logger.error(f"Config {config_id} not found")
            sys.exit(1)
        
        # Parse config data
        import json
        config_data_raw = config_row['config_data']
        if isinstance(config_data_raw, str):
            config_dict = json.loads(config_data_raw)
        else:
            config_dict = config_data_raw
        
        strategy_type = config_row['strategy_type']
        
        # Build full config structure
        full_config = {
            "strategy": strategy_type,
            "created_at": datetime.now().isoformat(),
            "version": "1.0",
            "config": config_dict
        }
        
        # Write config file
        config_file = Path(tempfile.gettempdir()) / f"strategy_{full_run_id}.yml"
        
        # Register Decimal representer for YAML
        def decimal_representer(dumper, data):
            return dumper.represent_scalar('tag:yaml.org,2002:float', str(data))
        yaml.add_representer(Decimal, decimal_representer)
        
        with open(config_file, 'w') as f:
            yaml.dump(
                full_config,
                f,
                default_flow_style=False,
                sort_keys=False,
                allow_unicode=True,
                indent=2
            )
        
        logger.info(f"✅ Regenerated config file: {config_file}")
        logger.info(f"   Config: {config_row['config_name']}")
        logger.info(f"   Strategy: {full_run_id[:8]}")
        
        # Show key info
        if 'target_margin' in config_dict:
            logger.info(f"   ✅ target_margin: ${config_dict['target_margin']:.2f}")
        elif 'target_exposure' in config_dict:
            logger.warning(f"   ⚠️  target_exposure: ${config_dict['target_exposure']:.2f} (DEPRECATED)")
        
        print(f"\n✅ Config file regenerated: {config_file}")
        print(f"   Strategy: {full_run_id[:8]}")
        print(f"   Config: {config_row['config_name']}")
        
    except Exception as e:
        logger.error(f"Error regenerating config: {e}", exc_info=True)
        raise
    finally:
        await db.disconnect()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/regenerate_strategy_config.py <run_id>")
        sys.exit(1)
    
    run_id = sys.argv[1]
    try:
        asyncio.run(regenerate_config(run_id))
        sys.exit(0)
    except Exception as e:
        logger.error(f"Failed to regenerate config: {e}")
        sys.exit(1)

