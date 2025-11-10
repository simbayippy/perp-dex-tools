"""
Safety Manager Module

Enforces safety limits and rate limiting for user actions.
"""

from typing import Dict, Any, Optional, Tuple
from datetime import datetime, timedelta
from databases import Database
from helpers.unified_logger import get_logger


logger = get_logger("core", "safety_manager")


class SafetyManager:
    """Enforces safety limits and rate limiting"""
    
    def __init__(self, database: Database):
        """
        Initialize SafetyManager.
        
        Args:
            database: Database connection instance
        """
        self.database = database
    
    async def can_start_strategy(self, user_id: str) -> Tuple[bool, Optional[str]]:
        """
        Check if user can start a new strategy.
        
        Args:
            user_id: User UUID
            
        Returns:
            Tuple of (allowed: bool, reason: Optional[str])
        """
        # Check daily start limit
        starts_today = await self.count_starts_today(user_id)
        daily_limit = await self.get_daily_limit(user_id)
        
        if starts_today >= daily_limit:
            return False, f"Daily start limit reached ({starts_today}/{daily_limit})"
        
        # Check cooldown
        cooldown_ok, cooldown_msg = await self.check_cooldown(user_id)
        if not cooldown_ok:
            return False, cooldown_msg
        
        # Check error rate
        error_rate = await self.calculate_error_rate(user_id)
        max_error_rate = await self.get_max_error_rate(user_id)
        
        if error_rate > max_error_rate:
            return False, f"Error rate too high ({error_rate:.1%} > {max_error_rate:.1%})"
        
        return True, None
    
    async def count_starts_today(self, user_id: str) -> int:
        """Count strategy starts in last 24 hours."""
        query = """
            SELECT COUNT(*) as count
            FROM audit_log
            WHERE user_id = :user_id
            AND action = 'start_strategy'
            AND created_at >= NOW() - INTERVAL '24 hours'
        """
        row = await self.database.fetch_one(query, {"user_id": user_id})
        return row["count"] if row else 0
    
    async def calculate_error_rate(self, user_id: str) -> float:
        """
        Calculate error rate for user's strategies.
        
        Returns:
            Error rate as float (0.0 to 1.0)
        """
        query = """
            SELECT 
                COUNT(*) FILTER (WHERE status = 'error') as error_count,
                COUNT(*) FILTER (WHERE status IN ('running', 'error', 'stopped')) as total_count
            FROM strategy_runs
            WHERE user_id = :user_id
            AND started_at >= NOW() - INTERVAL '7 days'
        """
        row = await self.database.fetch_one(query, {"user_id": user_id})
        
        if not row or row["total_count"] == 0:
            return 0.0
        
        error_count = row["error_count"] or 0
        total_count = row["total_count"] or 1
        
        return float(error_count) / float(total_count)
    
    async def check_cooldown(self, user_id: str) -> Tuple[bool, Optional[str]]:
        """
        Check if cooldown period has passed since last strategy start.
        
        Returns:
            Tuple of (ok: bool, message: Optional[str])
        """
        cooldown_minutes = await self.get_cooldown_minutes(user_id)
        
        query = """
            SELECT MAX(created_at) as last_start
            FROM audit_log
            WHERE user_id = :user_id
            AND action = 'start_strategy'
        """
        row = await self.database.fetch_one(query, {"user_id": user_id})
        
        if not row or not row["last_start"]:
            return True, None
        
        last_start = row["last_start"]
        if isinstance(last_start, str):
            last_start = datetime.fromisoformat(last_start.replace('Z', '+00:00'))
        
        time_since_start = datetime.now() - last_start.replace(tzinfo=None)
        cooldown_delta = timedelta(minutes=cooldown_minutes)
        
        if time_since_start < cooldown_delta:
            remaining = cooldown_delta - time_since_start
            remaining_minutes = int(remaining.total_seconds() / 60) + 1
            return False, f"Cooldown active: {remaining_minutes} minutes remaining"
        
        return True, None
    
    async def get_daily_limit(self, user_id: str) -> int:
        """Get daily start limit for user."""
        query = """
            SELECT daily_start_limit
            FROM safety_limits
            WHERE user_id = :user_id
        """
        row = await self.database.fetch_one(query, {"user_id": user_id})
        
        if row:
            return row["daily_start_limit"]
        
        # Default limit
        return 10
    
    async def get_max_error_rate(self, user_id: str) -> float:
        """Get max error rate for user."""
        query = """
            SELECT max_error_rate
            FROM safety_limits
            WHERE user_id = :user_id
        """
        row = await self.database.fetch_one(query, {"user_id": user_id})
        
        if row:
            return float(row["max_error_rate"])
        
        # Default error rate
        return 0.5
    
    async def get_cooldown_minutes(self, user_id: str) -> int:
        """Get cooldown minutes for user."""
        query = """
            SELECT cooldown_minutes
            FROM safety_limits
            WHERE user_id = :user_id
        """
        row = await self.database.fetch_one(query, {"user_id": user_id})
        
        if row:
            return row["cooldown_minutes"]
        
        # Default cooldown
        return 5
    
    async def initialize_user_limits(self, user_id: str) -> None:
        """Initialize default safety limits for a new user."""
        query = """
            INSERT INTO safety_limits (user_id, daily_start_limit, max_error_rate, cooldown_minutes)
            VALUES (:user_id, 10, 0.5, 5)
            ON CONFLICT (user_id) DO NOTHING
        """
        await self.database.execute(query, {"user_id": user_id})

