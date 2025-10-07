"""
Funding rate data models
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional, Dict
from decimal import Decimal
from datetime import datetime


class FundingRate(BaseModel):
    """Funding rate at a specific time"""
    time: datetime
    dex_id: int
    symbol_id: int
    funding_rate: Decimal
    
    next_funding_time: Optional[datetime] = None
    predicted_rate: Optional[Decimal] = None
    index_price: Optional[Decimal] = None
    mark_price: Optional[Decimal] = None
    
    # Market snapshot
    open_interest_usd: Optional[Decimal] = None
    volume_24h: Optional[Decimal] = None
    
    collection_latency_ms: Optional[int] = None
    
    class Config:
        from_attributes = True


class FundingRateResponse(BaseModel):
    """API response for funding rate"""
    dex_name: str
    symbol: str
    funding_rate: Decimal
    next_funding_time: Optional[datetime] = None
    timestamp: datetime
    
    # Additional context
    annualized_rate: Optional[Decimal] = Field(
        None, 
        description="Annualized rate assuming 8h funding periods"
    )
    
    # Market context
    index_price: Optional[Decimal] = None
    mark_price: Optional[Decimal] = None
    open_interest_usd: Optional[Decimal] = None
    volume_24h: Optional[Decimal] = None
    
    @field_validator('annualized_rate', mode='before')
    @classmethod
    def calculate_annualized(cls, v, info):
        """Calculate annualized rate if not provided"""
        if v is None and 'funding_rate' in info.data:
            # Assuming 8-hour funding periods (3 per day)
            # Annualized = rate * 3 * 365 * 100 (as percentage)
            return info.data['funding_rate'] * Decimal('1095')
        return v


class LatestFundingRates(BaseModel):
    """Latest funding rates across all DEXs for a symbol"""
    symbol: str
    rates: Dict[str, FundingRateResponse]  # dex_name -> rate
    updated_at: datetime


class AllLatestFundingRates(BaseModel):
    """Latest funding rates for all symbols across all DEXs"""
    symbols: Dict[str, Dict[str, Decimal]]  # symbol -> dex_name -> rate
    dex_metadata: Optional[Dict[str, Dict]] = None
    updated_at: datetime

