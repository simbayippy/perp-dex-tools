"""
API Client for communicating with strategy control API
"""

import httpx
from typing import Dict, Any, Optional
from decimal import Decimal


class ControlAPIClient:
    """HTTP client for strategy control API"""
    
    def __init__(self, base_url: str, api_key: str):
        """
        Initialize API client.
        
        Args:
            base_url: Base URL of control API (e.g., "http://localhost:8766")
            api_key: API key for authentication
        """
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.headers = {
            "X-API-Key": api_key,
            "Content-Type": "application/json"
        }
    
    async def get_status(self) -> Dict[str, Any]:
        """Get strategy status and account information."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self.base_url}/api/v1/status",
                headers=self.headers
            )
            response.raise_for_status()
            return response.json()
    
    async def get_accounts(self) -> Dict[str, Any]:
        """Get list of accessible accounts."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self.base_url}/api/v1/accounts",
                headers=self.headers
            )
            response.raise_for_status()
            return response.json()
    
    async def get_positions(self, account_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Get active positions.
        
        Args:
            account_name: Optional account name filter
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            params = {}
            if account_name:
                params["account_name"] = account_name
            
            response = await client.get(
                f"{self.base_url}/api/v1/positions",
                headers=self.headers,
                params=params
            )
            response.raise_for_status()
            return response.json()
    
    async def get_balances(self, account_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Get available margin balances across all exchanges.
        
        Args:
            account_name: Optional account name filter
        """
        async with httpx.AsyncClient(timeout=60.0) as client:
            params = {}
            if account_name:
                params["account_name"] = account_name
            
            response = await client.get(
                f"{self.base_url}/api/v1/balances",
                headers=self.headers,
                params=params
            )
            response.raise_for_status()
            return response.json()
    
    async def close_position(
        self,
        position_id: str,
        order_type: str = "market",
        reason: str = "manual_close"
    ) -> Dict[str, Any]:
        """
        Close a position.
        
        Args:
            position_id: Position ID (UUID)
            order_type: "market" or "limit"
            reason: Reason for closing
        """
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{self.base_url}/api/v1/positions/{position_id}/close",
                headers=self.headers,
                json={
                    "order_type": order_type,
                    "reason": reason
                }
            )
            response.raise_for_status()
            return response.json()
    
    async def health_check(self) -> bool:
        """
        Check if the control API server is running and accessible.
        
        Returns:
            True if server is accessible, False otherwise
        """
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                # Health endpoint doesn't require auth
                response = await client.get(f"{self.base_url}/health")
                response.raise_for_status()
                return True
        except Exception:
            return False
    
    async def reload_config(self) -> Dict[str, Any]:
        """
        Reload strategy configuration from the config file.
        
        Changes will take effect on the next execution cycle.
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self.base_url}/api/v1/config/reload",
                headers=self.headers
            )
            response.raise_for_status()
            return response.json()

