#!/usr/bin/env python3
"""
Seed initial DEX data into the database

Usage:
    python database/scripts/setup/seed_dexes.py
"""

import asyncio
import sys
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[3]
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


INITIAL_DEXES = [
    {
        'name': 'lighter',
        'display_name': 'Lighter Network',
        'api_base_url': 'https://mainnet.zklighter.elliot.ai',
        'websocket_url': 'wss://mainnet.zklighter.elliot.ai/stream',
        'maker_fee': 0.0000,  # Zero fees
        'taker_fee': 0.0000,
    },
    {
        'name': 'edgex',
        'display_name': 'EdgeX',
        'api_base_url': 'https://pro.edgex.exchange',
        'websocket_url': 'wss://quote.edgex.exchange',
        'maker_fee': 0.00015,  # 0.015%
        'taker_fee': 0.00038,  # 0.038%
    },
    {
        'name': 'paradex',
        'display_name': 'Paradex',
        'api_base_url': 'https://api.prod.paradex.trade',
        'websocket_url': 'wss://ws.prod.paradex.trade',
        'maker_fee': 0.00003,  # 0.003%
        'taker_fee': 0.0002,   # 0.02%
    },
    {
        'name': 'grvt',
        'display_name': 'GRVT',
        'api_base_url': 'https://trade.prod.grvt.io',
        'websocket_url': 'wss://trade.prod.grvt.io',
        'maker_fee': -0.0001,  # -0.01% (rebate!)
        'taker_fee': 0.00055,  # 0.055%
    },
    {
        'name': 'hyperliquid',
        'display_name': 'Hyperliquid',
        'api_base_url': 'https://api.hyperliquid.xyz',
        'websocket_url': 'wss://api.hyperliquid.xyz',
        'maker_fee': 0.00015,  # 0.015%
        'taker_fee': 0.00045,  # 0.045%
    },
    {
        'name': 'backpack',
        'display_name': 'Backpack',
        'api_base_url': 'https://api.backpack.exchange',
        'websocket_url': 'wss://ws.backpack.exchange',
        'maker_fee': 0.0002,   # 0.02%
        'taker_fee': 0.0005,   # 0.05%
    },
    {
        'name': 'aster',
        'display_name': 'Aster',
        'api_base_url': 'https://fapi.asterdex.com',
        'websocket_url': 'wss://fstream.asterdex.com',
        'maker_fee': 0.00005,   # 0.005%
        'taker_fee': 0.0004,   # 0.04%
    },
]


async def seed_dexes():
    """Seed DEX data"""
    
    logger.info("Seeding DEX data...")
    
    db = Database(settings.database_url)
    await db.connect()
    
    try:
        for dex in INITIAL_DEXES:
            # Check if exists
            existing = await db.fetch_one(
                "SELECT id FROM dexes WHERE name = :name",
                {'name': dex['name']}
            )
            
            if existing:
                logger.info(f"DEX '{dex['name']}' already exists, skipping")
                continue
            
            # Insert
            await db.execute("""
                INSERT INTO dexes (
                    name, display_name, api_base_url, websocket_url,
                    maker_fee_percent, taker_fee_percent, supports_websocket
                )
                VALUES (
                    :name, :display_name, :api_base_url, :websocket_url,
                    :maker_fee, :taker_fee, :supports_websocket
                )
            """, {
                'name': dex['name'],
                'display_name': dex['display_name'],
                'api_base_url': dex['api_base_url'],
                'websocket_url': dex['websocket_url'],
                'maker_fee': dex['maker_fee'],
                'taker_fee': dex['taker_fee'],
                'supports_websocket': True,  # All these DEXs support WebSocket
            })
            
            print(f"✅ Added DEX: {dex['display_name']}")
        
        # Show all DEXes
        dexes = await db.fetch_all("""
            SELECT id, name, display_name, maker_fee_percent, taker_fee_percent 
            FROM dexes ORDER BY id
        """)
        print("\nRegistered DEXes:")
        for dex in dexes:
            maker_fee_pct = float(dex['maker_fee_percent']) * 100
            taker_fee_pct = float(dex['taker_fee_percent']) * 100
            print(
                f"  {dex['id']}: {dex['name']} ({dex['display_name']}) - "
                f"Maker: {maker_fee_pct:+.3f}%, Taker: {taker_fee_pct:.3f}%"
            )
        
        return True
        
    except Exception as e:
        logger.error(f"Error seeding DEXes: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    finally:
        await db.disconnect()


if __name__ == "__main__":
    success = asyncio.run(seed_dexes())
    sys.exit(0 if success else 1)

