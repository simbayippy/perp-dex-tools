"""
DEX-related data models
"""

from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from decimal import Decimal
from datetime import datetime
from enum import Enum


class FeeType(str, Enum):
    """Fee type enumeration"""
    MAKER = "maker"
    TAKER = "taker"


class DEXFeeStructure(BaseModel):
    """Fee structure for a DEX"""
    maker_fee_percent: Decimal = Field(..., description="Maker fee as decimal (0.0002 = 0.02%)")
    taker_fee_percent: Decimal = Field(..., description="Taker fee as decimal")
    has_fee_tiers: bool = Field(default=False)
    fee_tiers: Optional[List[Dict[str, Any]]] = None
    
    def get_fee(self, fee_type: FeeType, volume_30d: Decimal = Decimal('0')) -> Decimal:
        """
        Get fee based on type and volume (for tiered fees)
        
        Args:
            fee_type: MAKER or TAKER
            volume_30d: 30-day trading volume (for tiered fee calculation)
            
        Returns:
            Fee as decimal
        """
        if not self.has_fee_tiers or not self.fee_tiers:
            return self.maker_fee_percent if fee_type == FeeType.MAKER else self.taker_fee_percent
        
        # Find appropriate tier based on volume
        for tier in self.fee_tiers:
            min_vol = Decimal(str(tier.get('min_volume_30d', 0)))
            max_vol = tier.get('max_volume_30d')
            max_vol = Decimal(str(max_vol)) if max_vol else Decimal('inf')
            
            if min_vol <= volume_30d < max_vol:
                if fee_type == FeeType.MAKER:
                    return Decimal(str(tier.get('maker_fee', self.maker_fee_percent)))
                else:
                    return Decimal(str(tier.get('taker_fee', self.taker_fee_percent)))
        
        # Default to base fees
        return self.maker_fee_percent if fee_type == FeeType.MAKER else self.taker_fee_percent


class DEXMetadata(BaseModel):
    """Metadata about a DEX"""
    id: int
    name: str = Field(..., description="Internal name (lowercase, no spaces)")
    display_name: str = Field(..., description="Display name")
    api_base_url: Optional[str] = None
    websocket_url: Optional[str] = None
    is_active: bool = True
    supports_websocket: bool = False
    
    fee_structure: DEXFeeStructure
    
    collection_interval_seconds: int = 60
    rate_limit_per_minute: int = 60
    
    last_successful_fetch: Optional[datetime] = None
    last_error: Optional[datetime] = None
    consecutive_errors: int = 0
    
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True  # Updated from orm_mode for Pydantic v2


class DEXHealth(BaseModel):
    """Health status of a DEX"""
    dex_name: str
    is_healthy: bool
    last_successful_fetch: Optional[datetime] = None
    consecutive_errors: int
    error_rate_percent: float
    avg_collection_latency_ms: float
    
    class Config:
        from_attributes = True

