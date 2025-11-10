#!/usr/bin/env python3
"""
Retrieve a stored API key for a user

This script retrieves the API key that was stored when the user authenticated
via Telegram bot (/auth command). It can also list all API keys for a user.

Usage:
    # Interactive mode
    python database/scripts/get_api_key.py
    
    # Command line mode - by username
    python database/scripts/get_api_key.py --username alice
    
    # Command line mode - by Telegram user ID
    python database/scripts/get_api_key.py --telegram-user-id 123456789
    
    # List all API keys for a user (shows prefixes, names, creation dates)
    python database/scripts/get_api_key.py --username alice --list-all
"""

import asyncio
import sys
import os
from pathlib import Path
import argparse
from uuid import UUID

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
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
from telegram_bot_service.utils.auth import TelegramAuth


async def get_stored_api_key(
    username: str = None,
    telegram_user_id: int = None,
    list_all: bool = False
) -> bool:
    """Retrieve stored API key for a user"""
    
    database_url = os.getenv('DATABASE_URL', 'postgresql://postgres:postgres@localhost:5432/perp_dex')
    db = Database(database_url)
    
    try:
        await db.connect()
        user_repo = UserRepository(db)
        key_repo = APIKeyRepository(db)
        auth = TelegramAuth(db)
        
        # Find user
        user = None
        if telegram_user_id:
            user = await auth.get_user_by_telegram_id(telegram_user_id)
            if not user:
                logger.error(f"❌ No user found with Telegram user ID '{telegram_user_id}'")
                return False
        elif username:
            user = await user_repo.get_by_username(username)
            if not user:
                logger.error(f"❌ User '{username}' not found")
                return False
        else:
            logger.error("❌ Either --username or --telegram-user-id must be provided")
            return False
        
        if not user['is_active']:
            logger.error(f"❌ User '{user['username']}' is inactive")
            return False
        
        logger.info("\n" + "="*70)
        logger.info("🔑 Retrieve API Key")
        logger.info("="*70)
        logger.info(f"User: {user['username']}")
        if user.get('telegram_user_id'):
            logger.info(f"Telegram User ID: {user['telegram_user_id']}")
        logger.info("")
        
        # List all API keys if requested
        if list_all:
            logger.info("📋 All API Keys for this user:")
            logger.info("-" * 70)
            keys = await key_repo.list_by_user(user['id'], include_inactive=False)
            if not keys:
                logger.info("   No active API keys found")
            else:
                for key in keys:
                    logger.info(f"   Key ID: {key['id']}")
                    logger.info(f"   Prefix: {key['key_prefix']}")
                    if key['name']:
                        logger.info(f"   Name: {key['name']}")
                    logger.info(f"   Created: {key['created_at']}")
                    if key['last_used_at']:
                        logger.info(f"   Last Used: {key['last_used_at']}")
                    if key['expires_at']:
                        logger.info(f"   Expires: {key['expires_at']}")
                    logger.info("")
            logger.info("-" * 70)
            logger.info("")
        
        # Try to retrieve stored encrypted API key
        stored_key = await auth.get_api_key_for_user(user)
        
        if stored_key:
            logger.info("✅ Stored API Key Found (from Telegram authentication):")
            logger.info("\n" + "-"*70)
            logger.info(f"API Key: {stored_key}")
            logger.info("-"*70)
            logger.info("\nUsage:")
            logger.info(f'  curl -H "X-API-Key: {stored_key}" http://localhost:8766/api/v1/positions')
            logger.info("")
            logger.info("💡 Note: This is the API key stored when you authenticated via Telegram bot.")
            logger.info("   If you need a new key, run: python database/scripts/create_api_key.py")
            logger.info("="*70)
        else:
            logger.warning("⚠️  No stored API key found in user metadata.")
            logger.info("")
            logger.info("This could mean:")
            logger.info("  1. User hasn't authenticated via Telegram bot (/auth command)")
            logger.info("  2. API key was cleared (e.g., via /logout)")
            logger.info("  3. Encryption key (CREDENTIAL_ENCRYPTION_KEY) is not set or different")
            logger.info("")
            if not list_all:
                logger.info("💡 Tip: Use --list-all to see all API keys for this user")
            logger.info("="*70)
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Error retrieving API key: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        await db.disconnect()


async def interactive_mode():
    """Interactive API key retrieval"""
    print("\n" + "="*70)
    print("🔑 Retrieve API Key")
    print("="*70)
    
    print("\nHow do you want to find the user?")
    print("  1. By username")
    print("  2. By Telegram user ID")
    
    choice = input("\nChoice (1 or 2): ").strip()
    
    username = None
    telegram_user_id = None
    
    if choice == "1":
        username = input("\nUsername: ").strip()
        if not username:
            print("❌ Username is required")
            return False
    elif choice == "2":
        try:
            telegram_user_id = int(input("\nTelegram User ID: ").strip())
        except ValueError:
            print("❌ Invalid Telegram user ID (must be a number)")
            return False
    else:
        print("❌ Invalid choice")
        return False
    
    list_all = input("\nList all API keys? (y/n): ").strip().lower() == 'y'
    
    return await get_stored_api_key(
        username=username,
        telegram_user_id=telegram_user_id,
        list_all=list_all
    )


async def main():
    parser = argparse.ArgumentParser(description="Retrieve stored API key for a user")
    parser.add_argument('--username', help='Username')
    parser.add_argument('--telegram-user-id', type=int, help='Telegram user ID')
    parser.add_argument('--list-all', action='store_true', help='List all API keys for the user')
    parser.add_argument('--interactive', action='store_true', help='Interactive mode')
    
    args = parser.parse_args()
    
    if args.interactive or (not args.username and not args.telegram_user_id):
        success = await interactive_mode()
    elif args.username or args.telegram_user_id:
        success = await get_stored_api_key(
            username=args.username,
            telegram_user_id=args.telegram_user_id,
            list_all=args.list_all
        )
    else:
        parser.print_help()
        return 1
    
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

