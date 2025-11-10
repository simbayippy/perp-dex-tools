#!/usr/bin/env python3
"""
Revoke API key(s) for a user

This script allows you to revoke (deactivate) API keys, effectively banning
users from accessing the API. Revoked keys will immediately fail validation
on all API requests and Telegram bot authentication attempts.

Usage:
    # Interactive mode
    python database/scripts/revoke_api_key.py
    
    # Revoke all keys for a user (ban user)
    python database/scripts/revoke_api_key.py --username alice --all
    
    # Revoke a specific key by key ID
    python database/scripts/revoke_api_key.py --key-id <uuid>
    
    # Revoke by username and key prefix (to identify specific key)
    python database/scripts/revoke_api_key.py --username alice --prefix perp_a1b2
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


async def revoke_api_key(
    username: str = None,
    key_id: UUID = None,
    key_prefix: str = None,
    revoke_all: bool = False
) -> bool:
    """Revoke API key(s) for a user"""
    
    database_url = os.getenv('DATABASE_URL', 'postgresql://postgres:postgres@localhost:5432/perp_dex')
    db = Database(database_url)
    
    try:
        await db.connect()
        user_repo = UserRepository(db)
        key_repo = APIKeyRepository(db)
        
        # If key_id is provided, revoke that specific key
        if key_id:
            key = await db.fetch_one("""
                SELECT ak.id, ak.key_prefix, ak.name, ak.is_active, ak.created_at,
                       u.username, u.id as user_id
                FROM api_keys ak
                JOIN users u ON ak.user_id = u.id
                WHERE ak.id = :key_id
            """, {"key_id": str(key_id)})
            
            if not key:
                logger.error(f"❌ API key with ID '{key_id}' not found")
                return False
            
            if not key['is_active']:
                logger.warning(f"⚠️  API key '{key['key_prefix']}' is already revoked")
                return True
            
            await key_repo.revoke(key_id)
            logger.info("\n" + "="*70)
            logger.info("✅ API Key Revoked Successfully")
            logger.info("="*70)
            logger.info(f"Key ID: {key_id}")
            logger.info(f"Key Prefix: {key['key_prefix']}")
            if key['name']:
                logger.info(f"Name: {key['name']}")
            logger.info(f"User: {key['username']}")
            logger.info(f"Created: {key['created_at']}")
            logger.info("\n⚠️  This key will no longer work for API requests or Telegram bot authentication.")
            logger.info("="*70)
            return True
        
        # Otherwise, need username
        if not username:
            logger.error("❌ Either --key-id or --username must be provided")
            return False
        
        # Find user
        user = await user_repo.get_by_username(username)
        if not user:
            logger.error(f"❌ User '{username}' not found")
            return False
        
        # List all active keys for the user
        keys = await key_repo.list_by_user(user['id'], include_inactive=False)
        
        if not keys:
            logger.warning(f"⚠️  No active API keys found for user '{username}'")
            return True
        
        # Filter by prefix if provided
        if key_prefix:
            keys = [k for k in keys if k['key_prefix'].startswith(key_prefix)]
            if not keys:
                logger.warning(f"⚠️  No active API keys found matching prefix '{key_prefix}'")
                return True
        
        # Show keys that will be revoked
        logger.info("\n" + "="*70)
        logger.info("🔒 Revoke API Key(s)")
        logger.info("="*70)
        logger.info(f"User: {username}")
        logger.info(f"User ID: {user['id']}")
        logger.info(f"\nActive API Keys ({len(keys)}):")
        logger.info("-" * 70)
        for key in keys:
            logger.info(f"  Key ID: {key['id']}")
            logger.info(f"  Prefix: {key['key_prefix']}")
            if key['name']:
                logger.info(f"  Name: {key['name']}")
            logger.info(f"  Created: {key['created_at']}")
            if key['last_used_at']:
                logger.info(f"  Last Used: {key['last_used_at']}")
            logger.info("")
        
        # Confirm revocation
        if not revoke_all and len(keys) > 1:
            logger.warning("⚠️  Multiple keys found. Use --all to revoke all keys, or --key-id to revoke a specific one.")
            logger.info("\nTo revoke all keys, run:")
            logger.info(f"  python database/scripts/revoke_api_key.py --username {username} --all")
            return False
        
        # Revoke keys
        revoked_count = 0
        for key in keys:
            await key_repo.revoke(key['id'])
            revoked_count += 1
        
        logger.info("="*70)
        logger.info(f"✅ Revoked {revoked_count} API key(s)")
        logger.info("="*70)
        logger.info("\n⚠️  IMPORTANT:")
        logger.info("   - Revoked keys will immediately fail API authentication")
        logger.info("   - Users cannot re-authenticate with revoked keys via Telegram bot")
        logger.info("   - Users will need to create new API keys to regain access")
        logger.info("="*70)
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Error revoking API key: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        await db.disconnect()


async def interactive_mode():
    """Interactive API key revocation"""
    print("\n" + "="*70)
    print("🔒 Revoke API Key")
    print("="*70)
    
    print("\nHow do you want to identify the key(s)?")
    print("  1. By username (revoke all keys for user)")
    print("  2. By key ID (revoke specific key)")
    print("  3. By username and key prefix")
    
    choice = input("\nChoice (1, 2, or 3): ").strip()
    
    username = None
    key_id = None
    key_prefix = None
    revoke_all = False
    
    if not choice:
        print("❌ No choice provided")
        return False
    
    if choice[0] == "1":
        username = input("\nUsername: ").strip()
        if not username:
            print("❌ Username is required")
            return False
        revoke_all = True
    elif choice[0] == "2":
        key_id_str = input("\nKey ID (UUID): ").strip()
        if not key_id_str:
            print("❌ Key ID is required")
            return False
        try:
            key_id = UUID(key_id_str)
        except ValueError:
            print("❌ Invalid UUID format")
            return False
    elif choice[0] == "3":
        username = input("\nUsername: ").strip()
        if not username:
            print("❌ Username is required")
            return False
        key_prefix = input("Key prefix (e.g., 'perp_a1b2'): ").strip()
        if not key_prefix:
            print("❌ Key prefix is required")
            return False
    else:
        print(f"❌ Invalid choice: '{choice}' (expected '1', '2', or '3')")
        return False
    
    return await revoke_api_key(
        username=username,
        key_id=key_id,
        key_prefix=key_prefix,
        revoke_all=revoke_all
    )


async def main():
    parser = argparse.ArgumentParser(description="Revoke API key(s) for a user")
    parser.add_argument('--username', help='Username')
    parser.add_argument('--key-id', type=str, help='Specific key ID (UUID) to revoke')
    parser.add_argument('--prefix', help='Key prefix to match (e.g., "perp_a1b2")')
    parser.add_argument('--all', action='store_true', help='Revoke all keys for the user')
    parser.add_argument('--interactive', action='store_true', help='Interactive mode')
    
    args = parser.parse_args()
    
    key_id = None
    if args.key_id:
        try:
            key_id = UUID(args.key_id)
        except ValueError:
            logger.error(f"❌ Invalid key ID format: {args.key_id}")
            return 1
    
    if args.interactive or (not args.username and not args.key_id):
        success = await interactive_mode()
    elif args.key_id:
        success = await revoke_api_key(key_id=key_id)
    elif args.username:
        # If prefix is specified, only revoke matching keys (not all)
        # If --all is specified, revoke all keys
        # If neither, only revoke if there's exactly one key
        success = await revoke_api_key(
            username=args.username,
            key_prefix=args.prefix,
            revoke_all=args.all  # Only revoke all if explicitly requested
        )
    else:
        parser.print_help()
        return 1
    
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

