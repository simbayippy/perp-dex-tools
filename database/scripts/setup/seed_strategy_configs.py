#!/usr/bin/env python3
"""
Seed Strategy Config Templates

Inserts public template configurations into the database.
These templates are available to all users when creating/configuring strategies.

Usage:
    python database/scripts/setup/seed_strategy_configs.py
"""

import asyncio
import sys
import os
import yaml
import json
from pathlib import Path
from decimal import Decimal
from datetime import datetime

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import dotenv
from databases import Database
from helpers.unified_logger import get_logger

dotenv.load_dotenv()

logger = get_logger("scripts", "seed_strategy_configs")


# Template configurations - hardcoded from config files
TEMPLATES = [
    {
        'config_name': 'Funding Arbitrage',
        'strategy_type': 'funding_arbitrage',
        'config_data': {
            'scan_exchanges': ['aster', 'lighter', 'paradex'],
            'mandatory_exchange': 'lighter',
            'target_margin': 40.0,
            'max_positions': 1,
            'max_total_exposure_usd': 200.0,
            'min_profit_rate': 0.0002283105022831050228310502283,
            'risk_strategy': 'combined',
            'profit_erosion_threshold': 0.4,
            'min_hold_hours': 1,
            'max_position_age_hours': 12,
            'max_new_positions_per_cycle': 1,
            'limit_order_offset_pct': 0.0002,
            'check_interval_seconds': 60,
            'dry_run': False,
            'max_oi_usd': 1500000.0,
            'min_volume_24h': 350000.0,
            'min_oi_usd': 100000.0,
            'max_entry_price_divergence_pct': 0.01,
            'wide_spread_cooldown_minutes': 60,
            'enable_liquidation_prevention': True,
            'min_liquidation_distance_pct': 0.10,
        }
    },
    {
        'config_name': 'Grid',
        'strategy_type': 'grid',
        'config_data': {
            'exchange': 'lighter',
            'ticker': 'BTC',
            'direction': 'buy',
            'order_notional_usd': 300,
            'target_leverage': 20,
            'take_profit': 0.1,
            'grid_step': 0.002,
            'max_orders': 5,
            'max_margin_usd': 40.0,
            'post_only_tick_multiplier': 10,
            'wait_time': 40,
            'stop_loss_enabled': True,
            'stop_loss_percentage': 2.5,
            'position_timeout_minutes': 3,
            'recovery_mode': 'aggressive',
            'stop_price': None,
            'pause_price': None,
            'max_oi_usd': None,
            'scan_exchanges': []
        }
    }
]


async def seed_strategy_configs():
    """Seed strategy config templates"""
    
    logger.info("Seeding strategy config templates...")
    
    # Get database URL from environment
    database_url = os.getenv('DATABASE_URL')
    if not database_url:
        logger.error("DATABASE_URL environment variable not set")
        sys.exit(1)
    
    db = Database(database_url)
    await db.connect()
    
    try:
        for template in TEMPLATES:
            config_name = template['config_name']
            strategy_type = template['strategy_type']
            config_data = template['config_data']
            
            # Check if template already exists
            existing = await db.fetch_one(
                """
                SELECT id FROM strategy_configs 
                WHERE config_name = :name AND is_template = TRUE
                """,
                {'name': config_name}
            )
            
            if existing:
                logger.info(f"Template '{config_name}' already exists, checking if update needed...")
                
                # Check if template needs migration (has target_exposure but no target_margin)
                existing_config = await db.fetch_one(
                    """
                    SELECT config_data FROM strategy_configs 
                    WHERE config_name = :name AND is_template = TRUE
                    """,
                    {'name': config_name}
                )
                
                if existing_config:
                    existing_data = existing_config['config_data']
                    if isinstance(existing_data, str):
                        existing_dict = json.loads(existing_data)
                    else:
                        existing_dict = existing_data
                    
                    # Check if template needs update
                    needs_update = False
                    update_reason = []
                    
                    # Migration: target_exposure -> target_margin
                    if existing_dict.get('target_exposure') and not existing_dict.get('target_margin'):
                        needs_update = True
                        update_reason.append("target_exposure -> target_margin migration")
                    
                    # Add new default filters if missing
                    if strategy_type == 'funding_arbitrage':
                        # Check min_volume_24h
                        existing_min_vol = existing_dict.get('min_volume_24h')
                        expected_min_vol = config_data.get('min_volume_24h')
                        
                        # Update if field is missing, None, or different value
                        if existing_min_vol is None or existing_min_vol != expected_min_vol:
                            logger.info(f"  Found min_volume_24h: existing={existing_min_vol}, expected={expected_min_vol}")
                            needs_update = True
                            update_reason.append("add/update min_volume_24h default")
                        
                        # Check min_oi_usd
                        existing_min_oi = existing_dict.get('min_oi_usd')
                        expected_min_oi = config_data.get('min_oi_usd')
                        
                        # Update if field is missing, None, or different value
                        if existing_min_oi is None or existing_min_oi != expected_min_oi:
                            logger.info(f"  Found min_oi_usd: existing={existing_min_oi}, expected={expected_min_oi}")
                            needs_update = True
                            update_reason.append("add/update min_oi_usd default")
                        
                        # Check new entry validation fields
                        new_fields = [
                            ('max_entry_price_divergence_pct', 0.01),
                            ('wide_spread_cooldown_minutes', 60),
                            ('enable_liquidation_prevention', True),
                            ('min_liquidation_distance_pct', 0.10),
                        ]
                        
                        for field_name, expected_value in new_fields:
                            existing_value = existing_dict.get(field_name)
                            if existing_value is None or existing_value != expected_value:
                                logger.info(f"  Found {field_name}: existing={existing_value}, expected={expected_value}")
                                needs_update = True
                                update_reason.append(f"add/update {field_name} default")
                    
                    if needs_update:
                        logger.info(f"Updating template '{config_name}': {', '.join(update_reason)}")
                        await db.execute(
                            """
                            UPDATE strategy_configs
                            SET config_data = CAST(:config_data AS jsonb),
                                updated_at = NOW()
                            WHERE config_name = :name AND is_template = TRUE
                            """,
                            {
                                'name': config_name,
                                'config_data': json.dumps(config_data)
                            }
                        )
                        logger.info(f"✅ Updated template: {config_name}")
                    else:
                        logger.info(f"Template '{config_name}' already up to date, skipping")
                continue
            
            # Insert template (user_id is NULL for templates)
            await db.execute(
                """
                INSERT INTO strategy_configs (
                    user_id, config_name, strategy_type, config_data,
                    is_template, is_active, created_at, updated_at
                )
                VALUES (
                    NULL, :config_name, :strategy_type, CAST(:config_data AS jsonb),
                    TRUE, TRUE, NOW(), NOW()
                )
                """,
                {
                    'config_name': config_name,
                    'strategy_type': strategy_type,
                    'config_data': json.dumps(config_data)
                }
            )
            
            logger.info(f"✅ Added template: {config_name}")
        
        # Show all templates
        templates = await db.fetch_all("""
            SELECT config_name, strategy_type, created_at
            FROM strategy_configs
            WHERE is_template = TRUE
            ORDER BY config_name
        """)
        
        print("\n" + "="*70)
        print("Registered Strategy Config Templates:")
        print("="*70)
        for tpl in templates:
            print(f"  📄 {tpl['config_name']} ({tpl['strategy_type']})")
        print("="*70 + "\n")
        
        logger.info(f"Successfully seeded {len(templates)} template(s)")
        
    except Exception as e:
        logger.error(f"Error seeding strategy configs: {e}", exc_info=True)
        raise
    finally:
        await db.disconnect()


if __name__ == "__main__":
    try:
        asyncio.run(seed_strategy_configs())
        sys.exit(0)
    except Exception as e:
        logger.error(f"Failed to seed strategy configs: {e}")
        sys.exit(1)

