"""
Historical data analysis models
"""

from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
from decimal import Decimal
from datetime import datetime


class FundingRateHistory(BaseModel):
    """Historical funding rates for a symbol on a DEX"""
    dex_name: str
    symbol: str
    data_points: List[Dict[str, Any]] = Field(
        ..., 
        description="List of {time, rate} dictionaries"
    )
    
    # Statistics
    avg_rate: Decimal
    median_rate: Decimal
    std_dev: Decimal
    min_rate: Decimal
    max_rate: Decimal
    
    period_start: datetime
    period_end: datetime


class FundingRateStats(BaseModel):
    """Statistical analysis of funding rates over a period"""
    symbol: str
    dex_name: Optional[str] = Field(None, description="None for all DEXs combined")
    
    # Time period
    period_days: int
    period_start: datetime
    period_end: datetime
    
    # Basic statistics
    avg_funding_rate: Decimal
    median_funding_rate: Decimal
    std_dev: Decimal
    volatility: Decimal = Field(..., description="Standard deviation / mean")
    
    # Distribution
    min_rate: Decimal
    max_rate: Decimal
    percentile_25: Decimal
    percentile_75: Decimal
    
    # Profitability metrics
    avg_annualized_apy: Decimal
    positive_rate_frequency: float = Field(
        ..., 
        description="Percentage of time rate was positive"
    )

