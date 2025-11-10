#!/usr/bin/env python3
"""
Create an API key for a user

Usage:
    # Interactive mode
    python database/scripts/users/create_api_key.py
    
    # Command line mode
    python database/scripts/users/create_api_key.py --username alice --name "Telegram Bot"
"""

import asyncio
import sys
import os
from pathlib import Path
import argparse
from uuid import UUID

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from databases import Database
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

try:
    from funding_rate_service.utils.logger import logger
except ImportError:
    import logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

from database.repositories import UserRepository, APIKeyRepository


async def create_api_key(
    username: str,
    name: str = None,
    prefix: str = "perp"
) -> bool:
    """Create an API key for a user"""
    
    database_url = os.getenv('DATABASE_URL', 'postgresql://postgres:postgres@localhost:5432/perp_dex')
    db = Database(database_url)
    
    try:
        await db.connect()
        user_repo = UserRepository(db)
        key_repo = APIKeyRepository(db)
        
        # Find user
        user = await user_repo.get_by_username(username)
        if not user:
            logger.error(f"❌ User '{username}' not found")
            logger.info("   Create user first: python database/scripts/users/create_user.py")
            return False
        
        if not user['is_active']:
            logger.error(f"❌ User '{username}' is inactive")
            return False
        
        logger.info(f"Creating API key for user '{username}'...")
        
        full_key, key_id = await key_repo.create(
            user_id=user['id'],
            name=name,
            prefix=prefix
        )
        
        logger.info("\n" + "="*70)
        logger.info("✅ API Key Created Successfully")
        logger.info("="*70)
        logger.info(f"User: {username}")
        logger.info(f"Key ID: {key_id}")
        if name:
            logger.info(f"Name: {name}")
        logger.info("\n⚠️  IMPORTANT: Save this key now - it won't be shown again!")
        logger.info("\n" + "-"*70)
        logger.info(f"API Key: {full_key}")
        logger.info("-"*70)
        logger.info("\nUsage:")
        logger.info(f'  curl -H "X-API-Key: {full_key}" http://localhost:8766/api/v1/positions')
        logger.info("="*70)
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Error creating API key: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        await db.disconnect()


async def interactive_mode():
    """Interactive API key creation"""
    print("\n" + "="*70)
    print("🔑 Create API Key")
    print("="*70)
    
    username = input("\nUsername: ").strip()
    if not username:
        print("❌ Username is required")
        return False
    
    name = input("Key name (optional, e.g., 'Telegram Bot'): ").strip() or None
    
    return await create_api_key(username, name)


async def main():
    parser = argparse.ArgumentParser(description="Create an API key for a user")
    parser.add_argument('--username', help='Username')
    parser.add_argument('--name', help='Descriptive name for the key (optional)')
    parser.add_argument('--prefix', default='perp', help='Key prefix (default: perp)')
    parser.add_argument('--interactive', action='store_true', help='Interactive mode')
    
    args = parser.parse_args()
    
    if args.interactive or (not args.username):
        success = await interactive_mode()
    elif args.username:
        success = await create_api_key(
            username=args.username,
            name=args.name,
            prefix=args.prefix
        )
    else:
        parser.print_help()
        return 1
    
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

