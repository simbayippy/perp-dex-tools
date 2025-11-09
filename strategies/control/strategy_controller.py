"""
Strategy Controller - Abstract base for strategy-specific control operations
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from uuid import UUID


class BaseStrategyController(ABC):
    """Abstract base class for strategy-specific control operations"""
    
    @abstractmethod
    async def get_positions(
        self,
        account_ids: List[str],
        account_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get active positions for specified accounts.
        
        Args:
            account_ids: List of account IDs (UUIDs as strings) that user can access
            account_name: Optional account name filter
            
        Returns:
            Dict with positions grouped by account:
            {
                "accounts": [
                    {
                        "account_name": str,
                        "account_id": str,
                        "strategy": str,
                        "positions": [...]
                    }
                ]
            }
        """
        pass
    
    @abstractmethod
    async def close_position(
        self,
        position_id: str,
        account_ids: List[str],
        order_type: str = "market",
        reason: str = "manual_close"
    ) -> Dict[str, Any]:
        """
        Close a position.
        
        Args:
            position_id: Position ID (UUID as string)
            account_ids: List of account IDs user can access (for validation)
            order_type: "market" or "limit"
            reason: Reason for closing
            
        Returns:
            Dict with close operation result:
            {
                "success": bool,
                "position_id": str,
                "message": str
            }
            
        Raises:
            ValueError: If position doesn't belong to accessible accounts
        """
        pass
    
    @abstractmethod
    def get_strategy_name(self) -> str:
        """Get the strategy name."""
        pass

