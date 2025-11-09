"""
Authentication module for linking Telegram users to database users
"""

from typing import Optional, Dict, Any
from databases import Database
from database.repositories import APIKeyRepository, UserRepository
from cryptography.fernet import Fernet
import os
import json


class TelegramAuth:
    """Handle Telegram user authentication and linking"""
    
    def __init__(self, database: Database):
        self.database = database
        self.api_key_repo = APIKeyRepository(database)
        self.user_repo = UserRepository(database)
        
        # Get encryption key for storing API keys
        encryption_key = os.getenv('CREDENTIAL_ENCRYPTION_KEY')
        if encryption_key:
            self.cipher = Fernet(encryption_key.encode() if isinstance(encryption_key, str) else encryption_key)
        else:
            self.cipher = None
    
    def _encrypt_api_key(self, api_key: str) -> Optional[str]:
        """Encrypt API key for storage."""
        if not self.cipher:
            return None
        try:
            return self.cipher.encrypt(api_key.encode()).decode()
        except Exception:
            return None
    
    def _decrypt_api_key(self, encrypted_key: str) -> Optional[str]:
        """Decrypt stored API key."""
        if not self.cipher:
            return None
        try:
            return self.cipher.decrypt(encrypted_key.encode()).decode()
        except Exception:
            return None
    
    async def authenticate_with_api_key(
        self,
        api_key: str,
        telegram_user_id: int
    ) -> Dict[str, Any]:
        """
        Authenticate API key and link Telegram user to database user.
        
        Args:
            api_key: API key from user
            telegram_user_id: Telegram user ID
            
        Returns:
            Dict with result:
            {
                "success": bool,
                "user_id": UUID (if successful),
                "username": str (if successful),
                "message": str
            }
        """
        # Validate API key
        user_info = await self.api_key_repo.validate_key(api_key)
        
        if not user_info:
            return {
                "success": False,
                "message": "Invalid or expired API key"
            }
        
        user_id = user_info["user_id"]
        username = user_info["username"]
        
        # Check if Telegram user ID is already linked to another user
        existing_user = await self.database.fetch_one("""
            SELECT id, username, telegram_user_id, metadata
            FROM users
            WHERE telegram_user_id = :telegram_user_id
        """, {"telegram_user_id": telegram_user_id})
        
        if existing_user:
            if str(existing_user['id']) == str(user_id):
                return {
                    "success": True,
                    "user_id": user_id,
                    "username": username,
                    "message": f"Already authenticated as {username}"
                }
            else:
                return {
                    "success": False,
                    "message": f"Telegram account already linked to user '{existing_user['username']}'. Use /logout first."
                }
        
        # Encrypt and store API key in user metadata
        encrypted_key = self._encrypt_api_key(api_key)
        metadata = existing_user.get('metadata', {}) if existing_user else {}
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        elif metadata is None:
            metadata = {}
        
        if encrypted_key:
            metadata['telegram_api_key_encrypted'] = encrypted_key
        
        metadata_json = json.dumps(metadata)
        
        # Link Telegram user ID to database user
        await self.database.execute("""
            UPDATE users
            SET telegram_user_id = :telegram_user_id, 
                metadata = CAST(:metadata AS jsonb),
                updated_at = NOW()
            WHERE id = :user_id
        """, {
            "telegram_user_id": telegram_user_id,
            "user_id": str(user_id),
            "metadata": metadata_json
        })
        
        return {
            "success": True,
            "user_id": user_id,
            "username": username,
            "message": f"Successfully authenticated as {username}"
        }
    
    async def get_user_by_telegram_id(
        self,
        telegram_user_id: int
    ) -> Optional[Dict[str, Any]]:
        """
        Get database user by Telegram user ID.
        
        Args:
            telegram_user_id: Telegram user ID
            
        Returns:
            User dict if found, None otherwise
        """
        user = await self.database.fetch_one("""
            SELECT id, username, email, is_admin, is_active, telegram_user_id, metadata
            FROM users
            WHERE telegram_user_id = :telegram_user_id
        """, {"telegram_user_id": telegram_user_id})
        
        if not user:
            return None
        
        return dict(user)
    
    async def get_api_key_for_user(self, user: Dict[str, Any]) -> Optional[str]:
        """
        Get stored API key for a user (decrypted).
        
        Args:
            user: User dict from database
            
        Returns:
            API key if found and decrypted, None otherwise
        """
        metadata = user.get('metadata')
        if not metadata:
            return None
        
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except Exception:
                return None
        
        encrypted_key = metadata.get('telegram_api_key_encrypted')
        if not encrypted_key:
            return None
        
        return self._decrypt_api_key(encrypted_key)
    
    async def logout(self, telegram_user_id: int) -> bool:
        """
        Unlink Telegram user from database user and clear stored API key.
        
        Args:
            telegram_user_id: Telegram user ID
            
        Returns:
            True if unlinked, False if not linked
        """
        # Get current metadata
        user = await self.database.fetch_one("""
            SELECT metadata
            FROM users
            WHERE telegram_user_id = :telegram_user_id
        """, {"telegram_user_id": telegram_user_id})
        
        if not user:
            return False
        
        # Remove API key from metadata
        metadata = user.get('metadata')
        if metadata:
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except Exception:
                    metadata = {}
            elif metadata is None:
                metadata = {}
        else:
            metadata = {}
        
        metadata.pop('telegram_api_key_encrypted', None)
        metadata_json = json.dumps(metadata)
        
        # Unlink Telegram user
        result = await self.database.execute("""
            UPDATE users
            SET telegram_user_id = NULL,
                metadata = CAST(:metadata AS jsonb),
                updated_at = NOW()
            WHERE telegram_user_id = :telegram_user_id
        """, {
            "telegram_user_id": telegram_user_id,
            "metadata": metadata_json
        })
        
        return result > 0

