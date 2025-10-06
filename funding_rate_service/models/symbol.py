"""
Symbol-related data models
"""

from pydantic import BaseModel, Field
from typing import Optional
from decimal import Decimal
from datetime import datetime


class Symbol(BaseModel):
    """Trading symbol"""
    id: int
    symbol: str = Field(..., description="Normalized symbol (e.g., BTC, ETH)")
    display_name: Optional[str] = None
    category: Optional[str] = None
    first_seen: Optional[datetime] = None
    is_active: bool = True
    
    class Config:
        from_attributes = True


class DEXSymbol(BaseModel):
    """Symbol availability on a specific DEX"""
    id: int
    dex_id: int
    symbol_id: int
    dex_symbol_format: str = Field(..., description="DEX-specific format (e.g., 'BTC-PERP')")
    
    is_active: bool = True
    min_order_size: Optional[Decimal] = None
    max_order_size: Optional[Decimal] = None
    tick_size: Optional[Decimal] = None
    
    # Volume metrics
    volume_24h: Optional[Decimal] = None
    volume_24h_base: Optional[Decimal] = None
    
    # Open Interest metrics (critical for low OI farming!)
    open_interest_usd: Optional[Decimal] = None
    open_interest_base: Optional[Decimal] = None
    
    # Liquidity metrics
    best_bid: Optional[Decimal] = None
    best_ask: Optional[Decimal] = None
    spread_bps: Optional[int] = Field(None, description="Spread in basis points")
    
    last_updated: datetime
    
    class Config:
        from_attributes = True

