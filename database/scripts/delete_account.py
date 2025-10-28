#!/usr/bin/env python3
"""
Delete a trading account and its credentials from the database

Usage:
    # Interactive mode with confirmation
    python database/scripts/delete_account.py
    
    # Delete specific account
    python database/scripts/delete_account.py --account-name acc2
    
    # Force delete without confirmation
    python database/scripts/delete_account.py --account-name acc2 --force
"""

import asyncio
import sys
import os
from pathlib import Path
from typing import Optional
import argparse

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from databases import Database
from dotenv import load_dotenv

load_dotenv()

try:
    from funding_rate_service.utils.logger import logger
except ImportError:
    import logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)


class AccountDeleter:
    """Handle account deletion with safety checks"""
    
    def __init__(self):
        db_url = os.getenv("FUNDING_DB_URL")
        if not db_url:
            raise ValueError("FUNDING_DB_URL environment variable not set")
        self.db = Database(db_url)
    
    async def connect(self):
        """Connect to database"""
        await self.db.connect()
        logger.info("✅ Connected to database")
    
    async def disconnect(self):
        """Disconnect from database"""
        await self.db.disconnect()
        logger.info("✅ Disconnected from database")
    
    async def list_accounts(self) -> list:
        """List all accounts"""
        query = """
            SELECT 
                id, 
                account_name, 
                description,
                wallet_address,
                is_active,
                created_at
            FROM accounts
            ORDER BY created_at DESC
        """
        return await self.db.fetch_all(query)
    
    async def get_account_details(self, account_name: str) -> Optional[dict]:
        """Get account details including linked data"""
        # Get account
        account = await self.db.fetch_one(
            "SELECT * FROM accounts WHERE account_name = :name",
            {"name": account_name}
        )
        
        if not account:
            return None
        
        account_id = account['id']
        
        # Count credentials
        creds_count = await self.db.fetch_one(
            "SELECT COUNT(*) as count FROM account_exchange_credentials WHERE account_id = :id",
            {"id": account_id}
        )
        
        # Count positions
        positions_count = await self.db.fetch_one(
            "SELECT COUNT(*) as count FROM strategy_positions WHERE account_id = :id",
            {"id": account_id}
        )
        
        # Count open positions
        open_positions_count = await self.db.fetch_one(
            "SELECT COUNT(*) as count FROM strategy_positions WHERE account_id = :id AND status = 'open'",
            {"id": account_id}
        )
        
        return {
            "account": dict(account),
            "credentials_count": creds_count['count'] if creds_count else 0,
            "positions_count": positions_count['count'] if positions_count else 0,
            "open_positions_count": open_positions_count['count'] if open_positions_count else 0,
        }
    
    async def delete_account(self, account_name: str, force: bool = False) -> bool:
        """
        Delete an account and its associated data.
        
        Args:
            account_name: Name of account to delete
            force: If True, skip confirmation prompts
            
        Returns:
            True if deleted, False if cancelled
        """
        # Get account details
        details = await self.get_account_details(account_name)
        
        if not details:
            logger.error(f"❌ Account '{account_name}' not found")
            return False
        
        account = details['account']
        account_id = account['id']
        
        # Display summary
        logger.info("\n" + "="*70)
        logger.info(f"📋 Account to Delete: {account_name}")
        logger.info("="*70)
        logger.info(f"ID: {account_id}")
        logger.info(f"Description: {account.get('description', 'N/A')}")
        logger.info(f"Created: {account['created_at']}")
        logger.info(f"Status: {'✅ Active' if account['is_active'] else '❌ Inactive'}")
        logger.info("")
        logger.info(f"📊 Associated Data:")
        logger.info(f"   - Exchange credentials: {details['credentials_count']}")
        logger.info(f"   - Total positions: {details['positions_count']}")
        logger.info(f"   - Open positions: {details['open_positions_count']}")
        logger.info("="*70)
        
        # Warn about open positions
        if details['open_positions_count'] > 0:
            logger.warning("")
            logger.warning(f"⚠️  WARNING: This account has {details['open_positions_count']} OPEN positions!")
            logger.warning("   Deleting the account will NOT close exchange positions.")
            logger.warning("   Position records will remain in DB with account_id set to NULL.")
            logger.warning("")
        
        # Warn about what will be deleted
        logger.info("")
        logger.info("🗑️  What will be deleted:")
        logger.info("   ✓ Account record")
        logger.info("   ✓ All exchange credentials (CASCADE)")
        logger.info("   ✓ Account sharing relationships (CASCADE)")
        logger.info("")
        logger.info("📝 What will be preserved:")
        logger.info("   ✓ Position records (account_id will be set to NULL)")
        logger.info("   ✓ Position history and metadata")
        logger.info("")
        
        # Confirmation
        if not force:
            try:
                confirmation = input(f"⚠️  Type '{account_name}' to confirm deletion: ")
            except (EOFError, KeyboardInterrupt):
                logger.info("\n❌ Deletion cancelled")
                return False
            
            if confirmation != account_name:
                logger.error("❌ Name mismatch. Deletion cancelled.")
                return False
        
        # Perform deletion
        try:
            logger.info("")
            logger.info("🗑️  Deleting account...")
            
            result = await self.db.execute(
                "DELETE FROM accounts WHERE id = :id",
                {"id": account_id}
            )
            
            logger.info(f"✅ Successfully deleted account '{account_name}'")
            logger.info(f"   - Removed {details['credentials_count']} credential records")
            logger.info(f"   - Unlinked {details['positions_count']} position records")
            return True
            
        except Exception as exc:
            logger.error(f"❌ Error deleting account: {exc}")
            raise


async def interactive_mode(deleter: AccountDeleter):
    """Interactive account selection and deletion"""
    accounts = await deleter.list_accounts()
    
    if not accounts:
        logger.info("ℹ️  No accounts found in database")
        return
    
    # Display accounts
    logger.info("\n" + "="*70)
    logger.info("📋 Available Accounts")
    logger.info("="*70)
    
    for idx, account in enumerate(accounts, 1):
        status = "✅ Active" if account['is_active'] else "❌ Inactive"
        logger.info(f"\n{idx}. {account['account_name']} ({status})")
        logger.info(f"   ID: {account['id']}")
        logger.info(f"   Description: {account.get('description', 'N/A')}")
        logger.info(f"   Created: {account['created_at']}")
    
    logger.info("\n" + "="*70)
    
    # Get selection
    try:
        choice = input("\nEnter account number to delete (or 'q' to quit): ")
    except (EOFError, KeyboardInterrupt):
        logger.info("\n❌ Cancelled")
        return
    
    if choice.lower() == 'q':
        logger.info("❌ Cancelled")
        return
    
    try:
        idx = int(choice) - 1
        if idx < 0 or idx >= len(accounts):
            logger.error("❌ Invalid selection")
            return
        
        selected_account = accounts[idx]
        await deleter.delete_account(selected_account['account_name'], force=False)
        
    except ValueError:
        logger.error("❌ Invalid input")


async def main():
    parser = argparse.ArgumentParser(
        description="Delete a trading account from the database"
    )
    parser.add_argument(
        '--account-name',
        help='Name of account to delete'
    )
    parser.add_argument(
        '--force',
        action='store_true',
        help='Skip confirmation prompts (DANGEROUS)'
    )
    
    args = parser.parse_args()
    
    deleter = AccountDeleter()
    
    try:
        await deleter.connect()
        
        if args.account_name:
            # Direct deletion
            await deleter.delete_account(args.account_name, force=args.force)
        else:
            # Interactive mode
            await interactive_mode(deleter)
        
    except Exception as exc:
        logger.error(f"❌ Error: {exc}")
        raise
    finally:
        await deleter.disconnect()


if __name__ == "__main__":
    asyncio.run(main())

