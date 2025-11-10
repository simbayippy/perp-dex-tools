"""
Audit Logger Module

Logs important user actions for compliance and debugging.
"""

from typing import Dict, Any, Optional
from datetime import datetime
from databases import Database
from helpers.unified_logger import get_logger


logger = get_logger("core", "audit_logger")


class AuditLogger:
    """Logs important actions to audit_log table"""
    
    def __init__(self, database: Database):
        """
        Initialize AuditLogger.
        
        Args:
            database: Database connection instance
        """
        self.database = database
    
    async def log_action(
        self,
        user_id: str,
        action: str,
        details: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Log an action to audit_log table.
        
        Args:
            user_id: User UUID (must be string)
            action: Action type (e.g., 'start_strategy', 'stop_strategy', 'create_account')
            details: Optional action-specific details
        """
        query = """
            INSERT INTO audit_log (user_id, action, details, created_at)
            VALUES (:user_id, :action, :details, :created_at)
        """
        
        try:
            # Ensure user_id is a string
            user_id_str = str(user_id) if user_id else None
            
            await self.database.execute(
                query,
                {
                    "user_id": user_id_str,
                    "action": action,
                    "details": details or {},
                    "created_at": datetime.now()
                }
            )
        except Exception as e:
            error_msg = "Failed to log audit action: " + str(e)
            logger.error(error_msg, exc_info=True)
    
    async def log_strategy_start(
        self,
        user_id: str,
        run_id: str,
        account_id: str,
        config_id: str,
        is_admin: bool = False
    ) -> None:
        """Log strategy start action."""
        await self.log_action(
            user_id,
            "start_strategy",
            {
                "run_id": run_id,
                "account_id": account_id,
                "config_id": config_id,
                "admin_bypass_proxy": is_admin
            }
        )
    
    async def log_strategy_stop(
        self,
        user_id: str,
        run_id: str
    ) -> None:
        """Log strategy stop action."""
        await self.log_action(
            user_id,
            "stop_strategy",
            {"run_id": run_id}
        )
    
    async def log_account_creation(
        self,
        user_id: str,
        account_id: str,
        account_name: str
    ) -> None:
        """Log account creation."""
        await self.log_action(
            user_id,
            "create_account",
            {
                "account_id": account_id,
                "account_name": account_name
            }
        )
    
    async def log_config_creation(
        self,
        user_id: str,
        config_id: str,
        config_name: str,
        strategy_type: str
    ) -> None:
        """Log config creation."""
        await self.log_action(
            user_id,
            "create_config",
            {
                "config_id": config_id,
                "config_name": config_name,
                "strategy_type": strategy_type
            }
        )
    
    async def log_credential_update(
        self,
        user_id: str,
        account_id: str,
        exchange: str
    ) -> None:
        """Log credential update."""
        await self.log_action(
            user_id,
            "update_credentials",
            {
                "account_id": account_id,
                "exchange": exchange
            }
        )
    
    async def log_proxy_assignment(
        self,
        user_id: str,
        account_id: str,
        proxy_id: str
    ) -> None:
        """Log proxy assignment."""
        await self.log_action(
            user_id,
            "assign_proxy",
            {
                "account_id": account_id,
                "proxy_id": proxy_id
            }
        )

