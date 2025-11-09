"""
User Repository - handles user database operations
"""

from typing import Optional, List, Dict, Any
from uuid import UUID
from databases import Database
from datetime import datetime


class UserRepository:
    """Repository for User data access"""
    
    def __init__(self, db: Database):
        self.db = db
    
    async def create(
        self,
        username: str,
        email: Optional[str] = None,
        is_admin: bool = False,
        metadata: Optional[Dict[str, Any]] = None
    ) -> UUID:
        """
        Create a new user.
        
        Args:
            username: Unique username
            email: Optional email address
            is_admin: Whether user is an admin (can access all accounts)
            metadata: Optional metadata dict
            
        Returns:
            User ID (UUID)
            
        Raises:
            ValueError: If username already exists
        """
        import json
        
        metadata_json = json.dumps(metadata or {})
        
        try:
            result = await self.db.fetch_one("""
                INSERT INTO users (username, email, is_admin, metadata)
                VALUES (:username, :email, :is_admin, CAST(:metadata AS jsonb))
                RETURNING id
            """, {
                "username": username,
                "email": email,
                "is_admin": is_admin,
                "metadata": metadata_json
            })
            
            if not result:
                raise ValueError("Failed to create user")
                
            return result['id']
        except Exception as e:
            if 'unique' in str(e).lower() or 'duplicate' in str(e).lower():
                raise ValueError(f"Username '{username}' already exists")
            raise
    
    async def get_by_id(self, user_id: UUID) -> Optional[Dict[str, Any]]:
        """Get user by ID."""
        result = await self.db.fetch_one("""
            SELECT id, username, email, is_admin, is_active, created_at, updated_at, metadata
            FROM users
            WHERE id = :user_id
        """, {"user_id": str(user_id)})
        
        if not result:
            return None
            
        return dict(result)
    
    async def get_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        """Get user by username."""
        result = await self.db.fetch_one("""
            SELECT id, username, email, is_admin, is_active, created_at, updated_at, metadata
            FROM users
            WHERE username = :username
        """, {"username": username})
        
        if not result:
            return None
            
        return dict(result)
    
    async def list_all(self, include_inactive: bool = False) -> List[Dict[str, Any]]:
        """List all users."""
        query = """
            SELECT id, username, email, is_admin, is_active, created_at, updated_at
            FROM users
        """
        
        if not include_inactive:
            query += " WHERE is_active = TRUE"
        
        query += " ORDER BY created_at DESC"
        
        results = await self.db.fetch_all(query)
        return [dict(row) for row in results]
    
    async def update(
        self,
        user_id: UUID,
        email: Optional[str] = None,
        is_admin: Optional[bool] = None,
        is_active: Optional[bool] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Update user."""
        import json
        
        updates = []
        params = {"user_id": str(user_id)}
        
        if email is not None:
            updates.append("email = :email")
            params["email"] = email
        
        if is_admin is not None:
            updates.append("is_admin = :is_admin")
            params["is_admin"] = is_admin
        
        if is_active is not None:
            updates.append("is_active = :is_active")
            params["is_active"] = is_active
        
        if metadata is not None:
            updates.append("metadata = CAST(:metadata AS jsonb)")
            params["metadata"] = json.dumps(metadata)
        
        if not updates:
            return False
        
        query = f"""
            UPDATE users
            SET {', '.join(updates)}, updated_at = NOW()
            WHERE id = :user_id
        """
        
        await self.db.execute(query, params)
        return True
    
    async def delete(self, user_id: UUID) -> bool:
        """Delete user (cascade deletes API keys)."""
        await self.db.execute("""
            DELETE FROM users
            WHERE id = :user_id
        """, {"user_id": str(user_id)})
        return True

