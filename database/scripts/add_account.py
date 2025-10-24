#!/usr/bin/env python3
"""
Add a new trading account with encrypted credentials to the database

Usage:
    # Interactive mode
    python database/scripts/add_account.py
    
    # From .env file
    python database/scripts/add_account.py --from-env --account-name main_bot
    
    # Manual mode
    python database/scripts/add_account.py --account-name test_bot --exchanges lighter,aster,backpack
"""

import asyncio
import sys
import os
from pathlib import Path
from typing import Dict, Optional
import argparse
import json

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cryptography.fernet import Fernet
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


class CredentialEncryptor:
    """Handle credential encryption/decryption using Fernet"""
    
    def __init__(self):
        # Get or generate encryption key
        self.key = os.getenv('CREDENTIAL_ENCRYPTION_KEY')
        
        if not self.key:
            logger.warning("⚠️  CREDENTIAL_ENCRYPTION_KEY not found in .env")
            logger.info("Generating new encryption key...")
            self.key = Fernet.generate_key().decode()
            logger.info(f"🔑 Generated key: {self.key}")
            logger.info("⚠️  IMPORTANT: Add this to your .env file:")
            logger.info(f"CREDENTIAL_ENCRYPTION_KEY={self.key}")
            
        self.cipher = Fernet(self.key.encode() if isinstance(self.key, str) else self.key)
    
    def encrypt(self, value: str) -> str:
        """Encrypt a string value"""
        if not value:
            return None
        return self.cipher.encrypt(value.encode()).decode()
    
    def decrypt(self, encrypted_value: str) -> str:
        """Decrypt an encrypted value"""
        if not encrypted_value:
            return None
        return self.cipher.decrypt(encrypted_value.encode()).decode()


class AccountManager:
    """Manage trading accounts in the database"""
    
    def __init__(self, db: Database):
        self.db = db
        self.encryptor = CredentialEncryptor()
    
    async def create_account(
        self, 
        account_name: str,
        description: str = "",
        wallet_address: str = None,
        metadata: Dict = None
    ) -> str:
        """Create a new account"""
        
        # Check if account already exists
        existing = await self.db.fetch_one(
            "SELECT id FROM accounts WHERE account_name = :name",
            {"name": account_name}
        )
        
        if existing:
            logger.info(f"Account '{account_name}' already exists (id: {existing['id']})")
            return existing['id']
        
        # Create account
        metadata_json = json.dumps(metadata or {})
        result = await self.db.fetch_one("""
            INSERT INTO accounts (account_name, description, wallet_address, metadata)
            VALUES (:name, :description, :wallet, CAST(:metadata AS jsonb))
            RETURNING id
        """, {
            "name": account_name,
            "description": description or f"Trading account: {account_name}",
            "wallet": wallet_address,
            "metadata": metadata_json
        })
        
        account_id = result['id'] if result else None
        
        logger.info(f"✅ Created account '{account_name}' (id: {account_id})")
        return account_id
    
    async def add_exchange_credentials(
        self,
        account_id: str,
        exchange_name: str,
        credentials: Dict[str, str],
        subaccount_index: int = 0
    ):
        """Add encrypted credentials for an exchange"""
        
        # Get exchange_id
        exchange = await self.db.fetch_one(
            "SELECT id FROM dexes WHERE name = :name",
            {"name": exchange_name.lower()}
        )
        
        if not exchange:
            logger.error(f"❌ Exchange '{exchange_name}' not found in database")
            logger.info("Run: python database/scripts/seed_dexes.py")
            return False
        
        exchange_id = exchange['id']
        
        # Encrypt credentials
        api_key_encrypted = None
        secret_key_encrypted = None
        additional_creds = {}
        
        if 'api_key' in credentials:
            api_key_encrypted = self.encryptor.encrypt(credentials['api_key'])
        
        if 'secret_key' in credentials:
            secret_key_encrypted = self.encryptor.encrypt(credentials['secret_key'])
        
        # Store additional credentials (private_key, account_index, etc.)
        for key, value in credentials.items():
            if key not in ['api_key', 'secret_key'] and value:
                additional_creds[key] = self.encryptor.encrypt(str(value))
        
        # Check if credentials already exist
        existing = await self.db.fetch_one("""
            SELECT id FROM account_exchange_credentials 
            WHERE account_id = :account_id 
              AND exchange_id = :exchange_id 
              AND subaccount_index = :subaccount
        """, {
            "account_id": account_id,
            "exchange_id": exchange_id,
            "subaccount": subaccount_index
        })
        
        if existing:
            # Update existing credentials
            additional_json = json.dumps(additional_creds) if additional_creds else None
            await self.db.execute("""
                UPDATE account_exchange_credentials
                SET api_key_encrypted = :api_key,
                    secret_key_encrypted = :secret_key,
                    additional_credentials_encrypted = CAST(:additional AS jsonb),
                    updated_at = NOW()
                WHERE id = :id
            """, {
                "id": existing['id'],
                "api_key": api_key_encrypted,
                "secret_key": secret_key_encrypted,
                "additional": additional_json
            })
            logger.info(f"✅ Updated credentials for {exchange_name} (account_id: {account_id})")
        else:
            # Insert new credentials
            additional_json = json.dumps(additional_creds) if additional_creds else None
            await self.db.execute("""
                INSERT INTO account_exchange_credentials (
                    account_id, exchange_id, subaccount_index,
                    api_key_encrypted, secret_key_encrypted, 
                    additional_credentials_encrypted
                )
                VALUES (
                    :account_id, :exchange_id, :subaccount,
                    :api_key, :secret_key, CAST(:additional AS jsonb)
                )
            """, {
                "account_id": account_id,
                "exchange_id": exchange_id,
                "subaccount": subaccount_index,
                "api_key": api_key_encrypted,
                "secret_key": secret_key_encrypted,
                "additional": additional_json
            })
            logger.info(f"✅ Added credentials for {exchange_name} (account_id: {account_id})")
        
        return True


async def read_credentials_from_env() -> Dict[str, Dict]:
    """Read exchange credentials from .env file"""
    credentials = {}
    
    # Lighter credentials
    lighter_private_key = os.getenv('API_KEY_PRIVATE_KEY')
    lighter_account_index = os.getenv('LIGHTER_ACCOUNT_INDEX')
    lighter_api_key_index = os.getenv('LIGHTER_API_KEY_INDEX')
    
    if lighter_private_key:
        credentials['lighter'] = {
            'private_key': lighter_private_key,
            'account_index': lighter_account_index,
            'api_key_index': lighter_api_key_index
        }
    
    # Aster credentials
    aster_api_key = os.getenv('ASTER_API_KEY')
    aster_secret = os.getenv('ASTER_SECRET_KEY')
    
    if aster_api_key and aster_secret:
        credentials['aster'] = {
            'api_key': aster_api_key,
            'secret_key': aster_secret
        }
    
    # Backpack credentials
    backpack_public = os.getenv('BACKPACK_PUBLIC_KEY')
    backpack_secret = os.getenv('BACKPACK_SECRET_KEY')
    
    if backpack_public and backpack_secret:
        credentials['backpack'] = {
            'public_key': backpack_public,
            'secret_key': backpack_secret
        }
    
    return credentials


async def add_account_from_env(account_name: str):
    """Add account using credentials from .env file"""
    
    logger.info(f"📋 Adding account '{account_name}' from .env file...")
    logger.info("="*70)
    
    # Get database URL
    database_url = os.getenv('DATABASE_URL', 'postgresql://postgres:postgres@localhost:5432/perp_dex')
    db = Database(database_url)
    
    try:
        await db.connect()
        manager = AccountManager(db)
        
        # Create account
        account_id = await manager.create_account(
            account_name=account_name,
            description=f"Account from .env: {account_name}",
            metadata={
                "source": "env_file",
                "max_positions": 10,
                "max_exposure_usd": 100000
            }
        )
        
        # Read credentials from .env
        credentials = await read_credentials_from_env()
        
        if not credentials:
            logger.warning("⚠️  No credentials found in .env file")
            logger.info("Make sure you have set: LIGHTER_*, ASTER_*, BACKPACK_* variables")
            return False
        
        # Add credentials for each exchange
        for exchange_name, creds in credentials.items():
            logger.info(f"\n🔐 Adding {exchange_name} credentials...")
            await manager.add_exchange_credentials(
                account_id=account_id,
                exchange_name=exchange_name,
                credentials=creds
            )
        
        logger.info("\n" + "="*70)
        logger.info(f"✅ Account '{account_name}' setup complete!")
        logger.info(f"   Account ID: {account_id}")
        logger.info(f"   Exchanges configured: {', '.join(credentials.keys())}")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Error adding account: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    finally:
        await db.disconnect()


async def interactive_mode():
    """Interactive account creation"""
    print("\n" + "="*70)
    print("🎯 Interactive Account Creation")
    print("="*70)
    
    account_name = input("\nAccount name (e.g., 'main_bot', 'test_account'): ").strip()
    if not account_name:
        print("❌ Account name is required")
        return False
    
    description = input("Description (optional): ").strip()
    
    print("\n📋 Which exchanges do you want to configure?")
    print("1. Lighter")
    print("2. Aster")
    print("3. Backpack")
    print("4. All of the above")
    
    choice = input("\nChoice (1-4): ").strip()
    
    exchanges = []
    if choice == '1':
        exchanges = ['lighter']
    elif choice == '2':
        exchanges = ['aster']
    elif choice == '3':
        exchanges = ['backpack']
    elif choice == '4':
        exchanges = ['lighter', 'aster', 'backpack']
    else:
        print("❌ Invalid choice")
        return False
    
    print(f"\n🔐 Will read credentials from .env for: {', '.join(exchanges)}")
    confirm = input("Proceed? (y/n): ").strip().lower()
    
    if confirm != 'y':
        print("❌ Cancelled")
        return False
    
    return await add_account_from_env(account_name)


async def main():
    parser = argparse.ArgumentParser(description="Add trading account to database")
    parser.add_argument('--account-name', help='Name of the account to create')
    parser.add_argument('--from-env', action='store_true', help='Read credentials from .env file')
    parser.add_argument('--interactive', action='store_true', help='Interactive mode')
    
    args = parser.parse_args()
    
    if args.interactive or (not args.account_name and not args.from_env):
        # Interactive mode
        success = await interactive_mode()
    elif args.from_env and args.account_name:
        # From .env mode
        success = await add_account_from_env(args.account_name)
    else:
        parser.print_help()
        return 1
    
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

