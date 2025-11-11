#!/usr/bin/env python3
"""
View Running Strategy Config

Shows the config file path and contents for a running strategy.

Usage:
    python scripts/view_running_strategy_config.py <run_id>
    python scripts/view_running_strategy_config.py  # Shows all running strategies
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

dotenv.load_dotenv()

logger = get_logger("scripts", "view_running_strategy_config")


async def view_strategy_config(run_id: Optional[str] = None):
    """View config for a running strategy."""
    
    database_url = os.getenv('DATABASE_URL')
    if not database_url:
        logger.error("DATABASE_URL environment variable is not set")
        sys.exit(1)
    
    db = Database(database_url)
    await db.connect()
    
    try:
        if run_id:
            # Get specific strategy
            query = """
                SELECT id, supervisor_program_name, status, config_id
                FROM strategy_runs
                WHERE id = :run_id
            """
            row = await db.fetch_one(query, {"run_id": run_id})
            
            if not row:
                logger.error(f"Strategy {run_id} not found")
                sys.exit(1)
            
            strategies = [row]
        else:
            # Get all running strategies
            query = """
                SELECT id, supervisor_program_name, status, config_id
                FROM strategy_runs
                WHERE status IN ('running', 'starting', 'paused')
                ORDER BY started_at DESC
            """
            rows = await db.fetch_all(query)
            strategies = [dict(row) for row in rows]
        
        if not strategies:
            logger.info("No running strategies found")
            return
        
        temp_dir = Path(tempfile.gettempdir())
        
        for strat in strategies:
            run_id = str(strat['id'])
            supervisor_name = strat['supervisor_program_name']
            status = strat['status']
            config_id = strat.get('config_id')
            
            print(f"\n{'='*70}")
            print(f"Strategy: {run_id[:8]}")
            print(f"Status: {status}")
            print(f"Supervisor: {supervisor_name}")
            print(f"{'='*70}")
            
            # Config file path (where supervisor reads it from)
            config_file = temp_dir / f"strategy_{run_id}.yml"
            
            print(f"\nConfig File Path: {config_file}")
            
            if config_file.exists():
                print(f"✅ Config file exists")
                print(f"\nConfig Contents:")
                print("-" * 70)
                with open(config_file, 'r') as f:
                    config_content = f.read()
                    print(config_content)
                
                # Parse and show key info
                try:
                    config_dict = yaml.safe_load(config_content)
                    actual_config = config_dict.get('config', config_dict)
                    
                    print("\n" + "-" * 70)
                    print("Key Parameters:")
                    print("-" * 70)
                    
                    if 'target_margin' in actual_config:
                        print(f"  ✅ target_margin: ${actual_config['target_margin']:.2f}")
                    elif 'target_exposure' in actual_config:
                        print(f"  ⚠️  target_exposure: ${actual_config['target_exposure']:.2f} (DEPRECATED - needs migration)")
                    
                    if 'scan_exchanges' in actual_config:
                        exchanges = actual_config['scan_exchanges']
                        print(f"  Exchanges: {', '.join(exchanges)}")
                    
                except Exception as e:
                    logger.warning(f"Could not parse config: {e}")
            else:
                print(f"❌ Config file NOT found at expected path")
                print(f"   (File may have been cleaned up or strategy was started differently)")
            
            # Also show config from database
            if config_id:
                config_row = await db.fetch_one(
                    """
                    SELECT config_name, config_data, strategy_type
                    FROM strategy_configs
                    WHERE id = :config_id
                    """,
                    {"config_id": config_id}
                )
                
                if config_row:
                    print(f"\n{'='*70}")
                    print(f"Database Config:")
                    print(f"{'='*70}")
                    print(f"Config Name: {config_row['config_name']}")
                    print(f"Strategy Type: {config_row['strategy_type']}")
                    
                    import json
                    db_config = config_row['config_data']
                    if isinstance(db_config, str):
                        db_config = json.loads(db_config)
                    
                    if 'target_margin' in db_config:
                        print(f"  ✅ target_margin: ${db_config['target_margin']:.2f}")
                    elif 'target_exposure' in db_config:
                        print(f"  ⚠️  target_exposure: ${db_config['target_exposure']:.2f} (DEPRECATED)")
                        print(f"     → Should be migrated to target_margin: ${db_config['target_exposure'] / 10:.2f}")
        
        print(f"\n{'='*70}\n")
        
    except Exception as e:
        logger.error(f"Error viewing strategy config: {e}", exc_info=True)
        raise
    finally:
        await db.disconnect()


if __name__ == "__main__":
    run_id = sys.argv[1] if len(sys.argv) > 1 else None
    try:
        asyncio.run(view_strategy_config(run_id))
        sys.exit(0)
    except Exception as e:
        logger.error(f"Failed to view strategy config: {e}")
        sys.exit(1)

