"""
Database Credential Loader

Loads encrypted exchange credentials from the database for multi-account support.
Used by trading bots to retrieve account-specific API keys/secrets.
"""

import os
import json
from typing import Dict, Optional, Any
from uuid import UUID
from pathlib import Path
import sys

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cryptography.fernet import Fernet
from databases import Database
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


class CredentialDecryptor:
    """Handles decryption of credentials using Fernet encryption."""
    
    def __init__(self):
        self.key = os.getenv('CREDENTIAL_ENCRYPTION_KEY')
        
        if not self.key:
            raise ValueError(
                "CREDENTIAL_ENCRYPTION_KEY not found in environment. "
                "This key is required to decrypt exchange credentials from the database."
            )
        
        try:
            self.cipher = Fernet(self.key.encode() if isinstance(self.key, str) else self.key)
        except Exception as e:
            raise ValueError(f"Invalid CREDENTIAL_ENCRYPTION_KEY format: {e}")
    
    def decrypt(self, encrypted_value: str) -> str:
        """Decrypt an encrypted value."""
        if not encrypted_value:
            return None
        try:
            return self.cipher.decrypt(encrypted_value.encode()).decode()
        except Exception as e:
            raise ValueError(f"Failed to decrypt credential: {e}")


class DatabaseCredentialLoader:
    """
    Loads account-specific credentials from the database.
    
    Usage:
        loader = DatabaseCredentialLoader()
        credentials = await loader.load_account_credentials('acc1')
        
        # credentials = {
        #     'lighter': {
        #         'private_key': '6b667422...',
        #         'account_index': '213803',
        #         'api_key_index': '2'
        #     },
        #     'aster': {
        #         'api_key': '2b4fb053...',
        #         'secret_key': 'b6e690df...'
        #     },
        #     'backpack': {
        #         'api_key': '7pGXGgpJ...',
        #         'secret_key': 'SYqtA6BbE...'
        #     }
        # }
    """
    
    def __init__(self, database: Optional[Database] = None):
        """
        Initialize credential loader.
        
        Args:
            database: Database instance (optional, will create if not provided)
        """
        self.db = database
        self.decryptor = CredentialDecryptor()
        self._should_disconnect = False
    
    async def _ensure_connection(self):
        """Ensure database connection is established."""
        if self.db is None:
            database_url = os.getenv('DATABASE_URL')
            if not database_url:
                raise ValueError("DATABASE_URL not found in environment")
            
            self.db = Database(database_url)
            await self.db.connect()
            self._should_disconnect = True
    
    async def close(self):
        """Close database connection if we created it."""
        if self._should_disconnect and self.db:
            await self.db.disconnect()
            self._should_disconnect = False
    
    async def load_account_credentials(self, account_name: str) -> Dict[str, Dict[str, str]]:
        """
        Load all exchange credentials for an account.
        
        Args:
            account_name: Name of the account (e.g., 'acc1', 'main_bot')
        
        Returns:
            Dictionary mapping exchange names to their credentials:
            {
                'lighter': {'private_key': '...', 'account_index': '213803', ...},
                'aster': {'api_key': '...', 'secret_key': '...'},
                'backpack': {'api_key': '...', 'secret_key': '...'}
            }
        
        Raises:
            ValueError: If account not found or no credentials configured
        """
        await self._ensure_connection()
        
        # Get account ID
        account = await self.db.fetch_one(
            "SELECT id, is_active FROM accounts WHERE account_name = :name",
            {"name": account_name}
        )
        
        if not account:
            raise ValueError(f"Account '{account_name}' not found in database")
        
        if not account['is_active']:
            raise ValueError(f"Account '{account_name}' is inactive")
        
        account_id = account['id']
        
        # Get all credentials for this account
        credentials_rows = await self.db.fetch_all("""
            SELECT 
                d.name as exchange_name,
                aec.api_key_encrypted,
                aec.secret_key_encrypted,
                aec.additional_credentials_encrypted,
                aec.subaccount_index,
                aec.is_active
            FROM account_exchange_credentials aec
            JOIN dexes d ON aec.exchange_id = d.id
            WHERE aec.account_id = :account_id
              AND aec.is_active = TRUE
        """, {"account_id": account_id})
        
        if not credentials_rows:
            raise ValueError(
                f"No active exchange credentials found for account '{account_name}'. "
                f"Run: python database/scripts/add_account.py --from-env --account-name {account_name}"
            )
        
        # Decrypt and organize credentials
        credentials = {}
        
        for row in credentials_rows:
            exchange_name = row['exchange_name']
            exchange_creds = {}
            
            # Decrypt API key if present
            if row['api_key_encrypted']:
                exchange_creds['api_key'] = self.decryptor.decrypt(row['api_key_encrypted'])
            
            # Decrypt secret key if present
            if row['secret_key_encrypted']:
                exchange_creds['secret_key'] = self.decryptor.decrypt(row['secret_key_encrypted'])
            
            # Include subaccount_index if present (keep as integer)
            if row['subaccount_index'] is not None:
                exchange_creds['subaccount_index'] = row['subaccount_index']
            
            # Decrypt additional credentials if present
            if row['additional_credentials_encrypted']:
                try:
                    additional = row['additional_credentials_encrypted']
                    if isinstance(additional, str):
                        additional = json.loads(additional)
                    
                    # Decrypt each additional credential
                    for key, encrypted_value in additional.items():
                        exchange_creds[key] = self.decryptor.decrypt(encrypted_value)
                except Exception as e:
                    raise ValueError(f"Failed to decrypt additional credentials for {exchange_name}: {e}")
            
            credentials[exchange_name] = exchange_creds
        
        return credentials
    
    async def load_account_id(self, account_name: str) -> UUID:
        """
        Get account ID from account name.
        
        Args:
            account_name: Name of the account
        
        Returns:
            Account UUID
        
        Raises:
            ValueError: If account not found
        """
        await self._ensure_connection()
        
        account = await self.db.fetch_one(
            "SELECT id FROM accounts WHERE account_name = :name AND is_active = TRUE",
            {"name": account_name}
        )
        
        if not account:
            raise ValueError(f"Account '{account_name}' not found or inactive")
        
        return account['id']
    
    async def get_account_info(self, account_name: str) -> Dict[str, Any]:
        """
        Get account information including metadata.
        
        Args:
            account_name: Name of the account
        
        Returns:
            Dictionary with account details
        """
        await self._ensure_connection()
        
        account = await self.db.fetch_one("""
            SELECT 
                id,
                account_name,
                description,
                wallet_address,
                is_active,
                metadata,
                created_at
            FROM accounts
            WHERE account_name = :name
        """, {"name": account_name})
        
        if not account:
            raise ValueError(f"Account '{account_name}' not found")
        
        return dict(account)
    
    async def list_account_exchanges(self, account_name: str) -> list[str]:
        """
        List all exchanges configured for an account.
        
        Args:
            account_name: Name of the account
        
        Returns:
            List of exchange names
        """
        await self._ensure_connection()
        
        account_id = await self.load_account_id(account_name)
        
        exchanges = await self.db.fetch_all("""
            SELECT d.name
            FROM account_exchange_credentials aec
            JOIN dexes d ON aec.exchange_id = d.id
            WHERE aec.account_id = :account_id AND aec.is_active = TRUE
        """, {"account_id": account_id})
        
        return [ex['name'] for ex in exchanges]


# Convenience function for quick usage
async def load_credentials_for_account(account_name: str) -> Dict[str, Dict[str, str]]:
    """
    Convenience function to load credentials for an account.
    
    Usage:
        credentials = await load_credentials_for_account('acc1')
    
    Args:
        account_name: Name of the account
    
    Returns:
        Dictionary of exchange credentials
    """
    loader = DatabaseCredentialLoader()
    try:
        return await loader.load_account_credentials(account_name)
    finally:
        await loader.close()


async def get_account_id(account_name: str) -> UUID:
    """
    Convenience function to get account ID.
    
    Usage:
        account_id = await get_account_id('acc1')
    
    Args:
        account_name: Name of the account
    
    Returns:
        Account UUID
    """
    loader = DatabaseCredentialLoader()
    try:
        return await loader.load_account_id(account_name)
    finally:
        await loader.close()

