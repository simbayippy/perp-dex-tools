"""
Arbitrage opportunity data models
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Dict, Any
from decimal import Decimal
from datetime import datetime


class ArbitrageOpportunity(BaseModel):
    """
    Funding rate arbitrage opportunity with comprehensive market data
    
    This model contains all information needed to evaluate a funding arb opportunity,
    including OI metrics for low OI farming strategies.
    """
    id: Optional[int] = None
    
    # Core opportunity data
    symbol: str
    long_dex: str = Field(..., description="DEX to go long on (lower funding rate)")
    short_dex: str = Field(..., description="DEX to go short on (higher funding rate)")
    
    # Funding rates
    long_rate: Decimal
    short_rate: Decimal
    divergence: Decimal = Field(..., description="short_rate - long_rate")
    
    # Profitability (after fees!)
    estimated_fees: Decimal
    net_profit_percent: Decimal = Field(..., description="Profit after fees")
    annualized_apy: Optional[Decimal] = Field(
        None, 
        description="Annualized APY assuming 8h funding periods"
    )
    
    # Volume metrics
    long_volume_24h: Optional[Decimal] = None
    short_volume_24h: Optional[Decimal] = None
    min_volume_24h: Optional[Decimal] = Field(
        None, 
        description="Minimum volume between the two DEXs"
    )
    
    # OPEN INTEREST METRICS (key for low OI farming strategy!)
    long_oi_usd: Optional[Decimal] = Field(None, description="Long DEX open interest in USD")
    short_oi_usd: Optional[Decimal] = Field(None, description="Short DEX open interest in USD")
    min_oi_usd: Optional[Decimal] = Field(
        None, 
        description="Minimum OI (for low OI detection - use this for filtering!)"
    )
    max_oi_usd: Optional[Decimal] = Field(None, description="Maximum OI")
    oi_ratio: Optional[Decimal] = Field(
        None, 
        description="OI ratio (long/short) - detects imbalances"
    )
    oi_imbalance: Optional[str] = Field(
        None, 
        description="'long_heavy', 'short_heavy', or 'balanced'"
    )
    
    # Liquidity metrics
    long_spread_bps: Optional[int] = Field(None, description="Long DEX spread in basis points")
    short_spread_bps: Optional[int] = Field(None, description="Short DEX spread in basis points")
    avg_spread_bps: Optional[int] = Field(None, description="Average spread")
    
    # Timestamps
    discovered_at: datetime
    valid_until: Optional[datetime] = None
    
    # Additional metadata
    metadata: Optional[Dict[str, Any]] = None
    
    @field_validator('divergence', mode='before')
    @classmethod
    def calculate_divergence(cls, v, info):
        """Calculate divergence if not provided"""
        if v is None and 'short_rate' in info.data and 'long_rate' in info.data:
            return info.data['short_rate'] - info.data['long_rate']
        return v
    
    @field_validator('annualized_apy', mode='before')
    @classmethod
    def calculate_apy(cls, v, info):
        """Calculate annualized APY if not provided"""
        if v is None and 'net_profit_percent' in info.data:
            # Assuming 8-hour funding periods (3 per day)
            # APY = net_profit * 3 * 365 * 100
            return info.data['net_profit_percent'] * Decimal('1095')
        return v
    
    @field_validator('oi_imbalance', mode='before')
    @classmethod
    def determine_oi_imbalance(cls, v, info):
        """Determine OI imbalance if not provided"""
        if v is None and 'oi_ratio' in info.data and info.data.get('oi_ratio'):
            ratio = info.data['oi_ratio']
            if ratio > Decimal('1.2'):
                return 'long_heavy'
            elif ratio < Decimal('0.8'):
                return 'short_heavy'
            else:
                return 'balanced'
        return v
    
    class Config:
        from_attributes = True


class OpportunityResponse(BaseModel):
    """API response for opportunities"""
    opportunities: List[ArbitrageOpportunity]
    total_count: int
    filters_applied: Optional[Dict[str, Any]] = None
    generated_at: datetime

