#!/usr/bin/env python3
"""
Link accounts to users

Usage:
    # Link a single account to a user
    python database/scripts/link_account_to_user.py --username simba --account acc1
    
    # Link multiple accounts
    python database/scripts/link_account_to_user.py --username simba --account acc1 acc2 acc3
    
    # Interactive mode
    python database/scripts/link_account_to_user.py --username simba --interactive
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


async def link_accounts_to_user(
    username: str,
    account_names: list[str]
) -> bool:
    """Link accounts to a user"""
    
    database_url = os.getenv('DATABASE_URL', 'postgresql://postgres:postgres@localhost:5432/perp_dex')
    db = Database(database_url)
    
    try:
        await db.connect()
        user_repo = UserRepository(db)
        
        # Find user
        user = await user_repo.get_by_username(username)
        if not user:
            logger.error(f"❌ User '{username}' not found")
            logger.info("   Create user first: python database/scripts/users/create_user.py")
            return False
        
        if not user['is_active']:
            logger.error(f"❌ User '{username}' is inactive")
            return False
        
        user_id = user['id']
        
        # Check which accounts exist and which are already linked
        for account_name in account_names:
            account = await db.fetch_one("""
                SELECT id, account_name, user_id::text
                FROM accounts
                WHERE account_name = :account_name
            """, {"account_name": account_name})
            
            if not account:
                logger.warning(f"⚠️  Account '{account_name}' not found (skipping)")
                continue
            
            current_user_id = account['user_id']
            
            if current_user_id and str(current_user_id) == str(user_id):
                logger.info(f"✓ Account '{account_name}' is already linked to user '{username}'")
                continue
            
            if current_user_id:
                current_user = await user_repo.get_by_id(current_user_id)
                current_username = current_user['username'] if current_user else "unknown"
                logger.warning(
                    f"⚠️  Account '{account_name}' is already linked to user '{current_username}'"
                )
                response = input(f"   Overwrite and link to '{username}'? (y/n): ").strip().lower()
                if response != 'y':
                    logger.info(f"   Skipping account '{account_name}'")
                    continue
            
            # Link account to user
            await db.execute("""
                UPDATE accounts
                SET user_id = :user_id, updated_at = NOW()
                WHERE account_name = :account_name
            """, {
                "user_id": str(user_id),
                "account_name": account_name
            })
            
            logger.info(f"✅ Linked account '{account_name}' to user '{username}'")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Error linking accounts: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        await db.disconnect()


async def interactive_mode(username: str):
    """Interactive account linking"""
    print("\n" + "="*70)
    print(f"🔗 Link Accounts to User: {username}")
    print("="*70)
    
    # List all accounts
    database_url = os.getenv('DATABASE_URL', 'postgresql://postgres:postgres@localhost:5432/perp_dex')
    db = Database(database_url)
    await db.connect()
    
    try:
        accounts = await db.fetch_all("""
            SELECT a.account_name, a.user_id::text, u.username
            FROM accounts a
            LEFT JOIN users u ON a.user_id = u.id
            ORDER BY a.account_name
        """)
        
        if not accounts:
            print("No accounts found in database")
            return False
        
        print("\nAvailable accounts:")
        for i, acc in enumerate(accounts, 1):
            user_info = acc['username'] if acc['username'] else "(unlinked)"
            print(f"  {i}. {acc['account_name']} - {user_info}")
        
        print("\nEnter account names to link (comma-separated, or 'all' for all unlinked accounts):")
        user_input = input("> ").strip()
        
        if user_input.lower() == 'all':
            # Link all unlinked accounts
            account_names = [acc['account_name'] for acc in accounts if not acc['user_id']]
            if not account_names:
                print("No unlinked accounts found")
                return False
        else:
            account_names = [name.strip() for name in user_input.split(',') if name.strip()]
        
        if not account_names:
            print("No accounts specified")
            return False
        
        return await link_accounts_to_user(username, account_names)
        
    finally:
        await db.disconnect()


async def main():
    parser = argparse.ArgumentParser(description="Link accounts to a user")
    parser.add_argument('--username', required=True, help='Username to link accounts to')
    parser.add_argument('--account', nargs='+', help='Account name(s) to link')
    parser.add_argument('--interactive', action='store_true', help='Interactive mode')
    
    args = parser.parse_args()
    
    if args.interactive:
        success = await interactive_mode(args.username)
    elif args.account:
        success = await link_accounts_to_user(args.username, args.account)
    else:
        parser.print_help()
        return 1
    
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

