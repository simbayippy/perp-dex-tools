"""
Filter models for queries
"""

from pydantic import BaseModel, Field
from typing import Optional, List
from decimal import Decimal


class OpportunityFilter(BaseModel):
    """
    Filter parameters for opportunity queries
    
    This allows users to find opportunities based on specific criteria,
    especially useful for low OI farming strategies.
    """
    # Symbol filter
    symbol: Optional[str] = None
    
    # DEX filters (position-agnostic)
    dex: Optional[str] = Field(
        default=None,
        description="Show opportunities involving this DEX (long or short side)"
    )
    dex_pair: Optional[List[str]] = Field(
        default=None,
        description="Show only opportunities between these two DEXs (any direction)"
    )
    dexes: Optional[List[str]] = Field(
        default=None,
        description="Show opportunities involving any of these DEXs"
    )
    whitelist_dexes: Optional[List[str]] = Field(
        default=None, 
        description="Only show opportunities where BOTH sides are from this list"
    )
    exclude_dexes: Optional[List[str]] = Field(
        default=None, 
        description="Exclude opportunities involving any of these DEXs"
    )
    required_dex: Optional[str] = Field(
        default=None,
        description="Require this DEX to appear on at least one leg"
    )
    
    # Profitability filters
    min_divergence: Optional[Decimal] = Field(
        default=Decimal('0.0001'), 
        description="Minimum divergence (0.01% = 0.0001)"
    )
    min_profit_percent: Optional[Decimal] = Field(
        default=Decimal('0'), 
        description="Minimum net profit after fees"
    )
    min_apy: Optional[Decimal] = Field(
        default=None,
        description="Minimum annualized APY (%)"
    )
    
    # Volume filters
    min_volume_24h: Optional[Decimal] = Field(
        default=None, 
        description="Minimum 24h volume in USD"
    )
    max_volume_24h: Optional[Decimal] = Field(
        default=None, 
        description="Maximum 24h volume (for niche/low volume pairs)"
    )
    
    # OPEN INTEREST FILTERS (key for low OI farming strategies!)
    min_oi_usd: Optional[Decimal] = Field(
        default=None, 
        description="Minimum open interest (e.g., 1000000 for $1M+)"
    )
    max_oi_usd: Optional[Decimal] = Field(
        default=None, 
        description="Maximum open interest (e.g., 2000000 for low OI farming < $2M)"
    )
    oi_ratio_min: Optional[Decimal] = Field(
        default=None, 
        description="Min OI ratio (long/short)"
    )
    oi_ratio_max: Optional[Decimal] = Field(
        default=None, 
        description="Max OI ratio"
    )
    oi_imbalance: Optional[str] = Field(
        default=None,
        description="Filter by imbalance: 'long_heavy', 'short_heavy', or 'balanced'"
    )
    
    # Liquidity filters
    max_spread_bps: Optional[int] = Field(
        default=None, 
        description="Maximum spread in basis points (e.g., 10 = 0.1%)"
    )
    
    # Response control
    limit: int = Field(default=10, ge=1, le=100, description="Number of results")
    sort_by: str = Field(
        default="net_profit_percent", 
        description="Sort field: net_profit_percent, divergence, min_oi_usd, annualized_apy"
    )
    sort_desc: bool = Field(default=True, description="Sort descending")
    
    class Config:
        from_attributes = True
