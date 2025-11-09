"""
API Key Repository - handles API key database operations
"""

from typing import Optional, List, Dict, Any
from uuid import UUID
from databases import Database
from datetime import datetime
import bcrypt
import secrets


class APIKeyRepository:
    """Repository for API Key data access"""
    
    def __init__(self, db: Database):
        self.db = db
    
    @staticmethod
    def generate_key(prefix: str = "perp") -> tuple[str, str]:
        """
        Generate a new API key.
        
        Args:
            prefix: Prefix for the key (default: "perp")
            
        Returns:
            Tuple of (full_key, key_prefix) where:
            - full_key: Complete API key (e.g., "perp_a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6")
            - key_prefix: First 8-16 chars for display
        """
        # Generate 32 random hex characters
        random_part = secrets.token_hex(16)  # 16 bytes = 32 hex chars
        full_key = f"{prefix}_{random_part}"
        key_prefix = full_key[:16]  # First 16 chars for display
        
        return full_key, key_prefix
    
    @staticmethod
    def hash_key(key: str) -> str:
        """
        Hash an API key using bcrypt.
        
        Args:
            key: Plaintext API key
            
        Returns:
            Hashed key (bcrypt hash)
        """
        return bcrypt.hashpw(key.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    
    @staticmethod
    def verify_key(key: str, key_hash: str) -> bool:
        """
        Verify an API key against its hash.
        
        Args:
            key: Plaintext API key
            key_hash: Hashed key from database
            
        Returns:
            True if key matches, False otherwise
        """
        try:
            return bcrypt.checkpw(key.encode('utf-8'), key_hash.encode('utf-8'))
        except Exception:
            return False
    
    async def create(
        self,
        user_id: UUID,
        name: Optional[str] = None,
        expires_at: Optional[datetime] = None,
        prefix: str = "perp"
    ) -> tuple[str, UUID]:
        """
        Create a new API key for a user.
        
        Args:
            user_id: User ID
            name: Optional descriptive name
            expires_at: Optional expiration timestamp
            prefix: Key prefix (default: "perp")
            
        Returns:
            Tuple of (full_key, key_id) where:
            - full_key: Complete API key (show to user once)
            - key_id: Database ID of the key
        """
        full_key, key_prefix = self.generate_key(prefix)
        key_hash = self.hash_key(full_key)
        
        result = await self.db.fetch_one("""
            INSERT INTO api_keys (user_id, key_hash, key_prefix, name, expires_at)
            VALUES (:user_id, :key_hash, :key_prefix, :name, :expires_at)
            RETURNING id
        """, {
            "user_id": str(user_id),
            "key_hash": key_hash,
            "key_prefix": key_prefix,
            "name": name,
            "expires_at": expires_at
        })
        
        if not result:
            raise ValueError("Failed to create API key")
        
        return full_key, result['id']
    
    async def validate_key(self, api_key: str) -> Optional[Dict[str, Any]]:
        """
        Validate an API key and return user information.
        
        Args:
            api_key: Plaintext API key
            
        Returns:
            Dict with user info if valid, None otherwise:
            {
                "user_id": UUID,
                "username": str,
                "is_admin": bool,
                "key_id": UUID,
                "key_name": str
            }
        """
        # Extract prefix for faster lookup
        if '_' not in api_key:
            return None
        
        key_prefix = api_key[:16]
        
        # Find keys with matching prefix
        keys = await self.db.fetch_all("""
            SELECT 
                ak.id as key_id,
                ak.key_hash,
                ak.name as key_name,
                ak.is_active,
                ak.expires_at,
                u.id as user_id,
                u.username,
                u.is_admin,
                u.is_active as user_active
            FROM api_keys ak
            JOIN users u ON ak.user_id = u.id
            WHERE ak.key_prefix = :prefix
              AND ak.is_active = TRUE
              AND u.is_active = TRUE
        """, {"prefix": key_prefix})
        
        # Check each key (bcrypt is slow, so we filter by prefix first)
        for key_row in keys:
            if self.verify_key(api_key, key_row['key_hash']):
                # Check expiration
                if key_row['expires_at'] and key_row['expires_at'] < datetime.now():
                    continue
                
                # Update last_used_at
                await self.db.execute("""
                    UPDATE api_keys
                    SET last_used_at = NOW()
                    WHERE id = :key_id
                """, {"key_id": str(key_row['key_id'])})
                
                return {
                    "user_id": key_row['user_id'],
                    "username": key_row['username'],
                    "is_admin": key_row['is_admin'],
                    "key_id": key_row['key_id'],
                    "key_name": key_row['key_name']
                }
        
        return None
    
    async def list_by_user(self, user_id: UUID, include_inactive: bool = False) -> List[Dict[str, Any]]:
        """List all API keys for a user."""
        query = """
            SELECT id, key_prefix, name, is_active, created_at, last_used_at, expires_at
            FROM api_keys
            WHERE user_id = :user_id
        """
        
        if not include_inactive:
            query += " AND is_active = TRUE"
        
        query += " ORDER BY created_at DESC"
        
        results = await self.db.fetch_all(query, {"user_id": str(user_id)})
        return [dict(row) for row in results]
    
    async def revoke(self, key_id: UUID) -> bool:
        """Revoke (deactivate) an API key."""
        await self.db.execute("""
            UPDATE api_keys
            SET is_active = FALSE
            WHERE id = :key_id
        """, {"key_id": str(key_id)})
        return True
    
    async def delete(self, key_id: UUID) -> bool:
        """Delete an API key."""
        await self.db.execute("""
            DELETE FROM api_keys
            WHERE id = :key_id
        """, {"key_id": str(key_id)})
        return True

