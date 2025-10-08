"""
System and health monitoring models
"""

from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from enum import Enum

from funding_rate_service.models.dex import DEXHealth


class CollectionStatus(str, Enum):
    """Status of a data collection run"""
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class CollectionLog(BaseModel):
    """Log of a data collection run"""
    id: int
    dex_id: Optional[int]
    dex_name: Optional[str]
    
    started_at: datetime
    completed_at: Optional[datetime]
    status: CollectionStatus
    
    symbols_fetched: int
    symbols_failed: int
    
    error_message: Optional[str] = None
    
    class Config:
        from_attributes = True


class ServiceHealth(BaseModel):
    """Overall service health status"""
    status: str = Field(..., description="healthy, degraded, or unhealthy")
    timestamp: datetime
    
    dex_health: List[DEXHealth]
    
    # System metrics
    uptime_seconds: int
    total_requests: Optional[int] = 0
    cache_hit_rate: Optional[float] = 0.0
    
    # Data freshness
    oldest_data_age_seconds: int
    last_collection_time: Optional[datetime] = None

