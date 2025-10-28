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
from typing import Dict, Optional, List
import argparse
import json

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cryptography.fernet import Fernet
from databases import Database
from dotenv import load_dotenv

# Load environment variables (can be overridden with --env-file)
# Default load for imports/initialization
load_dotenv()

try:
    from funding_rate_service.utils.logger import logger
from database.scripts.proxy_utils import assign_proxy, parse_proxy_line, upsert_proxy

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
            logger.info(f"ℹ️  Account '{account_name}' already exists (id: {existing['id']})")
            logger.info("   Credentials will be updated/overridden")
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
            logger.info(f"   Previous credentials have been overridden")
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


async def add_account_from_env(
    account_name: str,
    env_file: str = ".env",
    proxies: Optional[List[str]] = None,
    *,
    proxy_scheme: str = "http",
    proxy_label_prefix: Optional[str] = None,
    proxy_start_index: int = 1,
    proxy_priority_base: int = 0,
    proxy_auth_type: str = "basic",
):
    """Add account using credentials from specified env file"""
    
    # Check if env file exists
    env_path = Path(env_file)
    if not env_path.exists():
        logger.error(f"❌ Environment file not found: {env_file}")
        logger.error(f"   Searched at: {env_path.absolute()}")
        logger.warning("⚠️  Falling back to default .env file")
        env_file = ".env"
        env_path = Path(env_file)
        if not env_path.exists():
            logger.error(f"❌ Default .env file also not found!")
            return False
    
    logger.info(f"✅ Found environment file: {env_path.absolute()}")
    
    # Reload environment from specified file
    load_dotenv(dotenv_path=env_file, override=True)
    
    logger.info(f"📋 Adding account '{account_name}' from {env_file}...")
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
            logger.warning(f"⚠️  No credentials found in {env_file}")
            logger.info("Make sure you have set: LIGHTER_*, ASTER_*, BACKPACK_* variables")
            return False
        
        # Log what was loaded for verification
        logger.info(f"\n📦 Loaded credentials from {env_file}:")
        for exchange, creds in credentials.items():
            logger.info(f"   - {exchange}:")
            for key, value in creds.items():
                if key and value:
                    # Show first 4 chars for verification without exposing full keys
                    masked_value = f"{str(value)[:4]}..." if len(str(value)) > 4 else "***"
                    logger.info(f"      {key}: {masked_value}")
        
        # Add credentials for each exchange
        for exchange_name, creds in credentials.items():
            logger.info(f"\n🔐 Adding {exchange_name} credentials...")
            await manager.add_exchange_credentials(
                account_id=account_id,
                exchange_name=exchange_name,
                credentials=creds
            )
        
        proxy_lines = proxies or []
        if proxy_lines:
            label_prefix = proxy_label_prefix or f"{account_name}_proxy"
            logger.info(f"\n🌐 Attaching {len(proxy_lines)} proxies (label prefix: {label_prefix})")

            successes = 0
            for idx, raw_entry in enumerate(proxy_lines, start=proxy_start_index):
                try:
                    endpoint_url, username, password = parse_proxy_line(
                        raw_entry, scheme=proxy_scheme
                    )
                except ValueError as exc:
                    logger.warning(f"   ⚠️ Skipping proxy '{raw_entry}': {exc}")
                    continue

                creds_payload = None
                if username or password:
                    payload = {
                        "username": manager.encryptor.encrypt(username) if username else None,
                        "password": manager.encryptor.encrypt(password) if password else None,
                    }
                    creds_payload = json.dumps(payload)

                label = f"{label_prefix}_{idx}"
                proxy_id = await upsert_proxy(
                    db,
                    label=label,
                    endpoint_url=endpoint_url,
                    auth_type=proxy_auth_type,
                    encrypted_credentials=creds_payload,
                )
                await assign_proxy(
                    db,
                    account_name=account_name,
                    proxy_id=proxy_id,
                    priority=proxy_priority_base + (idx - proxy_start_index),
                    status="active",
                )
                logger.info(f"   ✓ Proxy '{label}' linked (endpoint: {endpoint_url})")
                successes += 1

            logger.info(f"   Proxies attached: {successes}/{len(proxy_lines)}")

        logger.info("\n" + "="*70)
        logger.info(f"✅ Account '{account_name}' setup complete!")
        logger.info(f"   Account ID: {account_id}")
        logger.info(f"   Exchanges configured: {', '.join(credentials.keys())}")
        if proxy_lines:
            logger.info(f"   Proxies configured: {len(proxy_lines)} (prefix: {label_prefix})")
        
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
    parser.add_argument('--from-env', action='store_true', help='Read credentials from env file')
    parser.add_argument('--env-file', default='.env', help='Path to env file (default: .env)')
    parser.add_argument('--interactive', action='store_true', help='Interactive mode')
    parser.add_argument('--proxy', action='append', help='Proxy definition host:port[:user:pass] (repeatable)')
    parser.add_argument('--proxy-file', help='Path to newline-delimited proxy list')
    parser.add_argument('--proxy-scheme', default='http', choices=['http', 'https', 'socks5'], help='Proxy scheme (default: http)')
    parser.add_argument('--proxy-label-prefix', help='Label prefix for proxies (default: <account>_proxy)')
    parser.add_argument('--proxy-start-index', type=int, default=1, help='Starting index for proxy labels (default: 1)')
    parser.add_argument('--proxy-priority-base', type=int, default=0, help='Base priority when assigning proxies (default: 0)')
    parser.add_argument('--proxy-auth-type', default='basic', choices=['none', 'basic', 'token', 'custom'], help='Proxy authentication type (default: basic)')
    
    args = parser.parse_args()
    
    proxy_entries: List[str] = []
    if args.proxy:
        proxy_entries.extend(args.proxy)

    if args.proxy_file:
        proxy_path = Path(args.proxy_file)
        if not proxy_path.exists():
            parser.error(f"Proxy file not found: {proxy_path}")
        file_lines = [
            line.strip()
            for line in proxy_path.read_text().splitlines()
            if line.strip() and not line.strip().startswith('#')
        ]
        proxy_entries.extend(file_lines)

    if args.interactive or (not args.account_name and not args.from_env):
        # Interactive mode
        success = await interactive_mode()
    elif args.from_env and args.account_name:
        # From specified env file
        success = await add_account_from_env(
            args.account_name,
            args.env_file,
            proxy_entries,
            proxy_scheme=args.proxy_scheme,
            proxy_label_prefix=args.proxy_label_prefix,
            proxy_start_index=args.proxy_start_index,
            proxy_priority_base=args.proxy_priority_base,
            proxy_auth_type=args.proxy_auth_type,
        )
    else:
        parser.print_help()
        return 1
    
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
