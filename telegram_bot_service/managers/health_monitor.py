"""
Health Monitor Module

Monitors strategy health and system resources.
"""

import psutil
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta
from databases import Database
from helpers.unified_logger import get_logger


logger = get_logger("core", "health_monitor")


class HealthMonitor:
    """Monitors strategy health and system resources"""
    
    # Resource limits
    RESOURCE_LIMITS = {
        'max_strategies_total': 15,
        'max_strategies_per_user': 3,
        'min_free_memory_mb': 500,
        'strategy_memory_estimate_mb': 100
    }
    
    # Health check thresholds
    HEARTBEAT_TIMEOUT_MINUTES = 5
    
    def __init__(self, database: Database):
        """
        Initialize HealthMonitor.
        
        Args:
            database: Database connection instance
        """
        self.database = database
    
    async def check_all_strategies(self) -> Dict[str, int]:
        """
        Check health of all running strategies.
        
        Returns:
            Dict with health check statistics
        """
        stats = {
            "checked": 0,
            "healthy": 0,
            "degraded": 0,
            "unhealthy": 0,
            "alerts_sent": 0
        }
        
        query = """
            SELECT id, user_id, last_heartbeat, health_status, status
            FROM strategy_runs
            WHERE status IN ('starting', 'running', 'paused')
        """
        rows = await self.database.fetch_all(query)
        
        for row in rows:
            stats["checked"] += 1
            run_id = row["id"]
            last_heartbeat = row["last_heartbeat"]
            current_status = row["health_status"]
            
            # Check heartbeat
            if last_heartbeat:
                if isinstance(last_heartbeat, str):
                    last_heartbeat = datetime.fromisoformat(last_heartbeat.replace('Z', '+00:00'))
                
                time_since_heartbeat = datetime.now() - last_heartbeat.replace(tzinfo=None)
                timeout_delta = timedelta(minutes=self.HEARTBEAT_TIMEOUT_MINUTES)
                
                if time_since_heartbeat > timeout_delta:
                    # No heartbeat for too long - mark as unhealthy
                    await self._update_health_status(run_id, "unhealthy")
                    stats["unhealthy"] += 1
                    # TODO: Send alert to user
                    stats["alerts_sent"] += 1
                elif time_since_heartbeat > timeout_delta / 2:
                    # Approaching timeout - mark as degraded
                    await self._update_health_status(run_id, "degraded")
                    stats["degraded"] += 1
                else:
                    # Healthy
                    await self._update_health_status(run_id, "healthy")
                    stats["healthy"] += 1
            else:
                # No heartbeat recorded yet
                if current_status == "running":
                    # Running but no heartbeat - mark as degraded
                    await self._update_health_status(run_id, "degraded")
                    stats["degraded"] += 1
                else:
                    # Still starting - unknown
                    await self._update_health_status(run_id, "unknown")
        
        return stats
    
    async def before_spawn_check(self) -> Tuple[bool, Optional[str]]:
        """
        Check system resources before spawning a new strategy.
        
        Returns:
            Tuple of (ok: bool, error_message: Optional[str])
        """
        # Check memory
        memory_ok, memory_msg = self.check_memory()
        if not memory_ok:
            return False, memory_msg
        
        # Check running count
        count_ok, count_msg = await self.check_running_count()
        if not count_ok:
            return False, count_msg
        
        return True, None
    
    def check_memory(self) -> Tuple[bool, Optional[str]]:
        """
        Check available system memory.
        
        Returns:
            Tuple of (ok: bool, error_message: Optional[str])
        """
        try:
            memory = psutil.virtual_memory()
            free_mb = memory.available / (1024 * 1024)
            min_required = self.RESOURCE_LIMITS['min_free_memory_mb']
            
            if free_mb < min_required:
                return False, f"Insufficient memory: {free_mb:.0f}MB free (need {min_required}MB)"
            
            return True, None
            
        except Exception as e:
            logger.error(f"Error checking memory: {e}")
            # Don't block on memory check failure
            return True, None
    
    async def check_running_count(self) -> Tuple[bool, Optional[str]]:
        """
        Check total number of running strategies.
        
        Returns:
            Tuple of (ok: bool, error_message: Optional[str])
        """
        query = """
            SELECT COUNT(*) as count
            FROM strategy_runs
            WHERE status IN ('starting', 'running', 'paused')
        """
        row = await self.database.fetch_one(query)
        running_count = row["count"] if row else 0
        max_total = self.RESOURCE_LIMITS['max_strategies_total']
        
        if running_count >= max_total:
            return False, f"System limit reached: {running_count}/{max_total} strategies running"
        
        return True, None
    
    async def check_user_running_count(self, user_id: str) -> Tuple[bool, Optional[str]]:
        """
        Check number of running strategies for a specific user.
        
        Returns:
            Tuple of (ok: bool, error_message: Optional[str])
        """
        query = """
            SELECT COUNT(*) as count
            FROM strategy_runs
            WHERE user_id = :user_id
            AND status IN ('starting', 'running', 'paused')
        """
        row = await self.database.fetch_one(query, {"user_id": user_id})
        running_count = row["count"] if row else 0
        max_per_user = self.RESOURCE_LIMITS['max_strategies_per_user']
        
        if running_count >= max_per_user:
            return False, f"User limit reached: {running_count}/{max_per_user} strategies running"
        
        return True, None
    
    async def _update_health_status(
        self,
        run_id: str,
        health_status: str
    ) -> None:
        """Update health status in database."""
        query = """
            UPDATE strategy_runs
            SET health_status = :health_status
            WHERE id = :run_id
        """
        await self.database.execute(
            query,
            {"run_id": run_id, "health_status": health_status}
        )

