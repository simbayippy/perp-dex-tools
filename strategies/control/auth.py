"""
API Key Authentication Middleware for Strategy Control API
"""

from typing import Optional, Dict, Any, Callable
from fastapi import Request, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from databases import Database
from database.repositories import APIKeyRepository


class APIKeyAuth:
    """API Key authentication middleware"""
    
    def __init__(self, database: Database):
        self.database = database
        self.key_repo = APIKeyRepository(database)
        self.security = HTTPBearer(auto_error=False)
    
    async def __call__(self, request: Request) -> Dict[str, Any]:
        """
        Validate API key from request headers.
        
        Returns:
            Dict with user info:
            {
                "user_id": UUID,
                "username": str,
                "is_admin": bool,
                "key_id": UUID
            }
            
        Raises:
            HTTPException: 401 if authentication fails
        """
        # Try Authorization header first (Bearer token)
        auth_header = request.headers.get("Authorization")
        api_key = None
        
        if auth_header and auth_header.startswith("Bearer "):
            api_key = auth_header[7:]  # Remove "Bearer " prefix
        else:
            # Fall back to X-API-Key header
            api_key = request.headers.get("X-API-Key")
        
        if not api_key:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing API key. Provide 'X-API-Key' header or 'Authorization: Bearer <key>'"
            )
        
        # Validate key
        user_info = await self.key_repo.validate_key(api_key)
        
        if not user_info:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired API key"
            )
        
        return user_info
    
    async def get_accessible_account_ids(self, user_id: str, is_admin: bool) -> list[str]:
        """
        Get list of account IDs accessible to the user.
        
        Args:
            user_id: User ID
            is_admin: Whether user is admin (no longer used - always filters by user_id)
            
        Returns:
            List of account IDs (UUIDs as strings)
        """
        # Always filter by user_id, even for admins
        # Admins can only access their own accounts
        results = await self.database.fetch_all("""
            SELECT id::text FROM accounts 
            WHERE user_id = :user_id AND is_active = TRUE
        """, {"user_id": user_id})
        
        return [row['id'] for row in results]
    
    async def validate_account_access(
        self,
        user_id: str,
        is_admin: bool,
        account_name: Optional[str] = None
    ) -> Optional[str]:
        """
        Validate that user can access the specified account.
        
        Args:
            user_id: User ID
            is_admin: Whether user is admin (no longer used - always filters by user_id)
            account_name: Optional account name to validate
            
        Returns:
            Account ID if accessible, None otherwise
            
        Raises:
            HTTPException: 403 if account not accessible, 404 if not found
        """
        if not account_name:
            return None
        
        # Always filter by user_id, even for admins
        # Admins can only access their own accounts
        account = await self.database.fetch_one("""
            SELECT id::text FROM accounts 
            WHERE account_name = :account_name 
              AND user_id = :user_id 
              AND is_active = TRUE
        """, {
            "account_name": account_name,
            "user_id": user_id
        })
        
        if not account:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Account '{account_name}' not found or not accessible"
            )
        
        return account['id']

