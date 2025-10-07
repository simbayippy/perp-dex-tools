"""
Base Task Class

Abstract base class for all background tasks with common functionality
like error handling, metrics tracking, and logging.
"""

import asyncio
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
from dataclasses import dataclass

from utils.logger import logger


@dataclass
class TaskMetrics:
    """Metrics for a background task"""
    task_name: str
    total_runs: int = 0
    successful_runs: int = 0
    failed_runs: int = 0
    last_run_time: Optional[datetime] = None
    last_success_time: Optional[datetime] = None
    last_error_time: Optional[datetime] = None
    last_error_message: Optional[str] = None
    avg_duration_ms: float = 0.0
    total_duration_ms: float = 0.0
    
    @property
    def success_rate(self) -> float:
        """Calculate success rate as percentage"""
        if self.total_runs == 0:
            return 0.0
        return (self.successful_runs / self.total_runs) * 100
    
    @property
    def is_healthy(self) -> bool:
        """Determine if task is healthy based on recent success"""
        if self.total_runs == 0:
            return True  # No runs yet, assume healthy
        
        # Healthy if:
        # 1. Success rate > 80%
        # 2. Last run was successful OR last error was more than 10 minutes ago
        if self.success_rate < 80:
            return False
        
        if self.last_success_time is None:
            return False
        
        if self.last_error_time is None:
            return True
        
        # If last error was recent but we've had success since, we're healthy
        if self.last_success_time > self.last_error_time:
            return True
        
        # If last error was more than 10 minutes ago, consider healthy
        if datetime.utcnow() - self.last_error_time > timedelta(minutes=10):
            return True
        
        return False


class BaseTask(ABC):
    """
    Abstract base class for background tasks
    
    Provides common functionality:
    - Error handling and retries
    - Metrics tracking
    - Logging
    - Health monitoring
    """
    
    def __init__(self, task_name: str, max_retries: int = 3):
        """
        Initialize base task
        
        Args:
            task_name: Name of the task (for logging/metrics)
            max_retries: Maximum number of retries on failure
        """
        self.task_name = task_name
        self.max_retries = max_retries
        self.metrics = TaskMetrics(task_name=task_name)
        self._running = False
        
        logger.info(f"Initialized task: {task_name}")
    
    @abstractmethod
    async def execute(self) -> Dict[str, Any]:
        """
        Execute the main task logic
        
        Returns:
            Dictionary with execution results/metrics
            
        Raises:
            Exception: If task execution fails
        """
        pass
    
    async def run(self) -> Dict[str, Any]:
        """
        Run the task with error handling and metrics tracking
        
        Returns:
            Dictionary with execution results and metrics
        """
        if self._running:
            logger.warning(f"Task {self.task_name} is already running, skipping")
            return {
                "status": "skipped",
                "reason": "already_running",
                "timestamp": datetime.utcnow().isoformat()
            }
        
        self._running = True
        start_time = datetime.utcnow()
        
        try:
            logger.info(f"🚀 Starting task: {self.task_name}")
            
            # Execute with retries
            result = await self._execute_with_retries()
            
            # Update success metrics
            duration_ms = (datetime.utcnow() - start_time).total_seconds() * 1000
            self._update_success_metrics(duration_ms)
            
            logger.info(
                f"✅ Task {self.task_name} completed successfully in {duration_ms:.1f}ms"
            )
            
            return {
                "status": "success",
                "result": result,
                "duration_ms": duration_ms,
                "timestamp": datetime.utcnow().isoformat()
            }
        
        except Exception as e:
            # Update failure metrics
            duration_ms = (datetime.utcnow() - start_time).total_seconds() * 1000
            self._update_failure_metrics(str(e), duration_ms)
            
            logger.error(
                f"❌ Task {self.task_name} failed after {duration_ms:.1f}ms: {e}",
                exc_info=True
            )
            
            return {
                "status": "failed",
                "error": str(e),
                "duration_ms": duration_ms,
                "timestamp": datetime.utcnow().isoformat()
            }
        
        finally:
            self._running = False
    
    async def _execute_with_retries(self) -> Dict[str, Any]:
        """Execute task with retry logic"""
        last_exception = None
        
        for attempt in range(self.max_retries + 1):
            try:
                if attempt > 0:
                    # Exponential backoff: 2^attempt seconds
                    delay = 2 ** attempt
                    logger.info(
                        f"Retrying task {self.task_name} (attempt {attempt + 1}/{self.max_retries + 1}) "
                        f"after {delay}s delay"
                    )
                    await asyncio.sleep(delay)
                
                return await self.execute()
            
            except Exception as e:
                last_exception = e
                if attempt < self.max_retries:
                    logger.warning(
                        f"Task {self.task_name} attempt {attempt + 1} failed: {e}"
                    )
                else:
                    logger.error(
                        f"Task {self.task_name} failed after {self.max_retries + 1} attempts"
                    )
        
        # All retries exhausted
        raise last_exception
    
    def _update_success_metrics(self, duration_ms: float) -> None:
        """Update metrics after successful execution"""
        self.metrics.total_runs += 1
        self.metrics.successful_runs += 1
        self.metrics.last_run_time = datetime.utcnow()
        self.metrics.last_success_time = datetime.utcnow()
        
        # Update average duration
        self.metrics.total_duration_ms += duration_ms
        self.metrics.avg_duration_ms = self.metrics.total_duration_ms / self.metrics.total_runs
    
    def _update_failure_metrics(self, error_message: str, duration_ms: float) -> None:
        """Update metrics after failed execution"""
        self.metrics.total_runs += 1
        self.metrics.failed_runs += 1
        self.metrics.last_run_time = datetime.utcnow()
        self.metrics.last_error_time = datetime.utcnow()
        self.metrics.last_error_message = error_message
        
        # Update average duration (include failed runs)
        self.metrics.total_duration_ms += duration_ms
        self.metrics.avg_duration_ms = self.metrics.total_duration_ms / self.metrics.total_runs
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get task metrics as dictionary"""
        return {
            "task_name": self.metrics.task_name,
            "total_runs": self.metrics.total_runs,
            "successful_runs": self.metrics.successful_runs,
            "failed_runs": self.metrics.failed_runs,
            "success_rate": round(self.metrics.success_rate, 2),
            "is_healthy": self.metrics.is_healthy,
            "last_run_time": self.metrics.last_run_time.isoformat() if self.metrics.last_run_time else None,
            "last_success_time": self.metrics.last_success_time.isoformat() if self.metrics.last_success_time else None,
            "last_error_time": self.metrics.last_error_time.isoformat() if self.metrics.last_error_time else None,
            "last_error_message": self.metrics.last_error_message,
            "avg_duration_ms": round(self.metrics.avg_duration_ms, 1),
            "is_running": self._running
        }
    
    def is_running(self) -> bool:
        """Check if task is currently running"""
        return self._running
    
    def is_healthy(self) -> bool:
        """Check if task is healthy"""
        return self.metrics.is_healthy
