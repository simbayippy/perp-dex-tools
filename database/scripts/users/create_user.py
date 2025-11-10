#!/usr/bin/env python3
"""
Create a new user for REST API access

Usage:
    # Interactive mode
    python database/scripts/users/create_user.py
    
    # Command line mode
    python database/scripts/users/create_user.py --username alice --email alice@example.com
    
    # Create admin user
    python database/scripts/users/create_user.py --username admin --admin
"""

import asyncio
import sys
import os
from pathlib import Path
import argparse

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

from database.repositories import UserRepository


async def create_user(
    username: str,
    email: str = None,
    is_admin: bool = False
) -> bool:
    """Create a new user"""
    
    database_url = os.getenv('DATABASE_URL', 'postgresql://postgres:postgres@localhost:5432/perp_dex')
    db = Database(database_url)
    
    try:
        await db.connect()
        repo = UserRepository(db)
        
        logger.info(f"Creating user '{username}'...")
        
        user_id = await repo.create(
            username=username,
            email=email,
            is_admin=is_admin
        )
        
        logger.info(f"✅ Created user '{username}' (id: {user_id})")
        if is_admin:
            logger.info("   ⚠️  Admin privileges granted (can access all accounts)")
        
        return True
        
    except ValueError as e:
        logger.error(f"❌ Error: {e}")
        return False
    except Exception as e:
        logger.error(f"❌ Error creating user: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        await db.disconnect()


async def interactive_mode():
    """Interactive user creation"""
    print("\n" + "="*70)
    print("👤 Create User")
    print("="*70)
    
    username = input("\nUsername: ").strip()
    if not username:
        print("❌ Username is required")
        return False
    
    email = input("Email (optional): ").strip() or None
    
    admin_choice = input("Admin user? (y/n): ").strip().lower()
    is_admin = admin_choice == 'y'
    
    if is_admin:
        confirm = input("⚠️  Admin users can access ALL accounts. Confirm? (y/n): ").strip().lower()
        if confirm != 'y':
            print("❌ Cancelled")
            return False
    
    return await create_user(username, email, is_admin)


async def main():
    parser = argparse.ArgumentParser(description="Create a user for REST API access")
    parser.add_argument('--username', help='Username')
    parser.add_argument('--email', help='Email address (optional)')
    parser.add_argument('--admin', action='store_true', help='Create admin user (can access all accounts)')
    parser.add_argument('--interactive', action='store_true', help='Interactive mode')
    
    args = parser.parse_args()
    
    if args.interactive or (not args.username):
        success = await interactive_mode()
    elif args.username:
        success = await create_user(
            username=args.username,
            email=args.email,
            is_admin=args.admin
        )
    else:
        parser.print_help()
        return 1
    
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

