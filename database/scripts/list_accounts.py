#!/usr/bin/env python3
"""
List all trading accounts and their configured exchanges

Usage:
    python database/scripts/list_accounts.py
    
    # Show credentials (decrypted, use carefully!)
    python database/scripts/list_accounts.py --show-credentials
"""

import asyncio
import sys
import os
from pathlib import Path
import argparse
import json
from typing import Dict

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


class CredentialDecryptor:
    """Handle credential decryption"""
    
    def __init__(self):
        self.key = os.getenv('CREDENTIAL_ENCRYPTION_KEY')
        
        if not self.key:
            logger.warning("⚠️  CREDENTIAL_ENCRYPTION_KEY not found in .env")
            self.cipher = None
        else:
            self.cipher = Fernet(self.key.encode() if isinstance(self.key, str) else self.key)
    
    def decrypt(self, encrypted_value: str) -> str:
        """Decrypt an encrypted value"""
        if not encrypted_value or not self.cipher:
            return "[ENCRYPTED]"
        try:
            return self.cipher.decrypt(encrypted_value.encode()).decode()
        except Exception:
            return "[DECRYPT_ERROR]"


async def list_accounts(show_credentials: bool = False, show_full: bool = False):
    """List all accounts and their configurations"""
    
    print("\n" + "="*70)
    print("📋 Trading Accounts")
    print("="*70)
    
    # Get database URL
    database_url = os.getenv('DATABASE_URL', 'postgresql://postgres:postgres@localhost:5432/perp_dex')
    db = Database(database_url)
    
    try:
        await db.connect()
        
        # Get all accounts
        accounts = await db.fetch_all("""
            SELECT id, account_name, description, wallet_address, 
                   is_active, created_at, metadata
            FROM accounts
            ORDER BY created_at DESC
        """)
        
        if not accounts:
            print("\n⚠️  No accounts found in database")
            print("\nCreate one with:")
            print("  python database/scripts/add_account.py")
            return
        
        decryptor = CredentialDecryptor() if show_credentials else None
        
        for i, account in enumerate(accounts, 1):
            status = "✅ Active" if account['is_active'] else "❌ Inactive"
            print(f"\n{i}. {account['account_name']} ({status})")
            print(f"   ID: {account['id']}")
            print(f"   Description: {account['description'] or 'N/A'}")
            print(f"   Wallet: {account['wallet_address'] or 'N/A'}")
            print(f"   Created: {account['created_at']}")
            
            # Show metadata if available
            if account['metadata']:
                metadata = account['metadata']
                if isinstance(metadata, str):
                    metadata = json.loads(metadata)
                print(f"   Metadata: {json.dumps(metadata, indent=14)}")
            
            # Get exchange credentials for this account
            credentials = await db.fetch_all("""
                SELECT 
                    aec.id,
                    d.name as exchange_name,
                    d.display_name,
                    aec.subaccount_index,
                    aec.api_key_encrypted,
                    aec.secret_key_encrypted,
                    aec.additional_credentials_encrypted,
                    aec.is_active,
                    aec.last_used,
                    aec.created_at
                FROM account_exchange_credentials aec
                JOIN dexes d ON aec.exchange_id = d.id
                WHERE aec.account_id = :account_id
                ORDER BY d.name
            """, {"account_id": account['id']})
            
            if credentials:
                print(f"\n   🔐 Configured Exchanges ({len(credentials)}):")
                for cred in credentials:
                    cred_status = "✅" if cred['is_active'] else "❌"
                    print(f"      {cred_status} {cred['display_name']} ({cred['exchange_name']})")
                    
                    if cred['subaccount_index'] and cred['subaccount_index'] > 0:
                        print(f"         Subaccount: {cred['subaccount_index']}")
                    
                    if cred['last_used']:
                        print(f"         Last used: {cred['last_used']}")
                    
                    # Show credentials if requested
                    if show_credentials and decryptor:
                        print(f"         Credentials:")
                        if cred['api_key_encrypted']:
                            api_key = decryptor.decrypt(cred['api_key_encrypted'])
                            # Mask most of the key for security (unless show_full)
                            if not show_full and len(api_key) > 8:
                                api_key = api_key[:4] + "..." + api_key[-4:]
                            print(f"           API Key: {api_key}")
                        
                        if cred['secret_key_encrypted']:
                            secret = decryptor.decrypt(cred['secret_key_encrypted'])
                            if not show_full and len(secret) > 8:
                                secret = secret[:4] + "..." + secret[-4:]
                            print(f"           Secret: {secret}")
                        
                        if cred['additional_credentials_encrypted']:
                            additional = cred['additional_credentials_encrypted']
                            if isinstance(additional, str):
                                additional = json.loads(additional)
                            for key, encrypted_val in additional.items():
                                val = decryptor.decrypt(encrypted_val)
                                if not show_full and len(val) > 8:
                                    val = val[:4] + "..." + val[-4:]
                                print(f"           {key}: {val}")
            else:
                print(f"\n   ⚠️  No exchange credentials configured")
            
            # Check for shared credentials
            shares = await db.fetch_all("""
                SELECT 
                    d.name as exchange_name,
                    a2.account_name as shared_from,
                    aes.sharing_type
                FROM account_exchange_sharing aes
                JOIN dexes d ON aes.exchange_id = d.id
                JOIN accounts a2 ON aes.shared_account_id = a2.id
                WHERE aes.primary_account_id = :account_id
            """, {"account_id": account['id']})
            
            if shares:
                print(f"\n   🔗 Shared Exchange Credentials ({len(shares)}):")
                for share in shares:
                    print(f"      • {share['exchange_name']} (from '{share['shared_from']}', type: {share['sharing_type']})")

            # Proxy assignments
            proxies = await db.fetch_all("""
                SELECT 
                    np.label,
                    np.endpoint_url,
                    np.auth_type,
                    np.is_active AS proxy_is_active,
                    apa.priority,
                    apa.status,
                    apa.last_checked_at
                FROM account_proxy_assignments apa
                JOIN network_proxies np ON apa.proxy_id = np.id
                WHERE apa.account_id = :account_id
                ORDER BY apa.priority ASC, np.label ASC
            """, {"account_id": account['id']})

            if proxies:
                print(f"\n   🌐 Proxy Assignments ({len(proxies)}):")
                for proxy in proxies:
                    status_icon = "✅" if proxy["status"] == "active" and proxy["proxy_is_active"] else "❌"
                    print(
                        f"      {status_icon} {proxy['label']} "
                        f"(priority {proxy['priority']}, status {proxy['status']})"
                    )
                    print(f"         Endpoint: {proxy['endpoint_url']}")
                    print(f"         Auth: {proxy['auth_type']}")
                    if proxy["last_checked_at"]:
                        print(f"         Last health check: {proxy['last_checked_at']}")
        
        print("\n" + "="*70)
        print(f"Total accounts: {len(accounts)}")
        print("="*70)
        
    except Exception as e:
        logger.error(f"❌ Error listing accounts: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        await db.disconnect()


async def main():
    parser = argparse.ArgumentParser(description="List trading accounts")
    parser.add_argument('--show-credentials', action='store_true', 
                       help='Show decrypted credentials (masked)')
    parser.add_argument('--show-full', action='store_true',
                       help='Show FULL unmasked credentials (⚠️  use with extreme caution!)')
    
    args = parser.parse_args()
    
    # If show_full is set, automatically enable show_credentials
    show_creds = args.show_credentials or args.show_full
    
    await list_accounts(show_credentials=show_creds, show_full=args.show_full)


if __name__ == "__main__":
    asyncio.run(main())
