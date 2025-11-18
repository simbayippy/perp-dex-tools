"""
Funding Arbitrage Configuration Models

Pydantic models for type-safe configuration with automatic validation.
Hierarchical config structure for organized settings.
"""

from pydantic import BaseModel, Field
from decimal import Decimal
from typing import List, Optional

# ============================================================================
# Risk Management Configuration
# ============================================================================

class RiskManagementConfig(BaseModel):
    """
    Risk management settings for exit conditions.
    
    Controls when to close positions to protect profit.
    """
    # Exit strategy
    strategy: str = Field(
        default="combined",
        description="Risk management strategy: 'combined', 'funding_flip', 'profit_target'"
    )
    
    # Profit erosion
    min_erosion_threshold: Decimal = Field(
        default=Decimal("0.5"),
        description="Exit when divergence drops to X% of entry (0.5 = 50% erosion)"
    )
    
    # Time limits
    max_position_age_hours: int = Field(
        default=168,  # 1 week
        description="Max hours to hold position before force close"
    )
    
    # Better opportunity switching
    enable_better_opportunity: bool = Field(
        default=True,
        description="Close position if better opportunity exists"
    )
    
    min_profit_improvement: Decimal = Field(
        default=Decimal("0.002"),
        description="Min improvement required to switch (0.002 = 0.2%)"
    )
    
    # Fees
    rebalance_cost_bps: int = Field(
        default=8,
        description="Total trading cost in basis points (8 = 0.08%)"
    )
    
    # Monitoring
    check_interval_seconds: int = Field(
        default=60,
        description="How often to check positions (seconds)"
    )
    
    min_hold_hours: float = Field(
        default=0.0,
        description="Minimum hours to hold a position before risk exits are considered (supports fractional hours, e.g., 1.5 = 1 hour 30 minutes)"
    )
    
    class Config:
        use_enum_values = True
        validate_assignment = True


# ============================================================================
# Main Configuration
# ============================================================================

class FundingArbConfig(BaseModel):
    """
    Main funding arbitrage configuration.
    
    All settings for the funding arbitrage strategy.
    """
    # Strategy identification
    strategy_name: str = Field(
        default="funding_arbitrage",
        description="Strategy identifier"
    )
    
    # Exchanges
    exchanges: List[str] = Field(
        ...,
        description="DEXes to use (e.g., ['lighter', 'grvt', 'backpack'])"
    )
    
    mandatory_exchange: Optional[str] = Field(
        default=None,
        description="Optional DEX that must participate in every trade"
    )
    
    # Trading pairs
    symbols: Optional[List[str]] = Field(
        default=None,
        description="Symbols to trade (None = all available)"
    )
    
    # Position limits
    max_positions: int = Field(
        default=10,
        description="Max concurrent positions"
    )
    
    max_new_positions_per_cycle: int = Field(
        default=2,
        description="Max new positions to open per cycle"
    )
    
    target_margin: Optional[Decimal] = Field(
        default=None,
        description="Target margin per position in USD. If set, exposure will be calculated dynamically based on leverage."
    )
    
    default_position_size_usd: Decimal = Field(
        default=Decimal("1000"),
        description="Default size for each position in USD (calculated from target_margin if set)"
    )
    
    max_position_size_usd: Decimal = Field(
        default=Decimal("10000"),
        description="Max size per position in USD"
    )
    
    max_total_exposure_usd: Decimal = Field(
        default=Decimal("50000"),
        description="Max total exposure per exchange (not across all exchanges). Each position contributes its size_usd to both the long_dex and short_dex exposure limits."
    )
    
    # Profitability thresholds
    min_profit: Decimal = Field(
        default=Decimal("0.001"),
        description="Min profit threshold (0.001 = 0.1% APY)"
    )

    limit_order_offset_pct: Decimal = Field(
        default=Decimal("0.0001"),
        description="Limit order price improvement (decimal pct, negative values cross the spread)"
    )
    
    profitability_horizon_hours: int = Field(
        default=24,
        description="Calculate profitability over N hours"
    )
    
    # Filtering (for point farming on low-OI DEXes)
    max_oi_usd: Optional[Decimal] = Field(
        default=None,
        description="Max open interest allowed on the mandatory exchange (if set)"
    )
    
    min_volume_24h: Optional[Decimal] = Field(
        default=None,
        description="Minimum 24h volume in USD across both exchanges (filters low-liquidity markets)"
    )
    
    min_oi_usd: Optional[Decimal] = Field(
        default=None,
        description="Minimum open interest in USD across both exchanges (filters low-liquidity markets)"
    )
    
    # Risk management
    risk_config: RiskManagementConfig = Field(
        default_factory=RiskManagementConfig,
        description="Risk management settings"
    )
    
    # Database (shared with funding_rate_service via direct imports)
    database_url: str = Field(
        ...,
        description="PostgreSQL connection string"
    )

    config_path: Optional[str] = Field(
        default=None,
        description="Source config path (populated when loaded from YAML)"
    )
    
    # Multi-account support
    account_name: Optional[str] = Field(
        default=None,
        description="Account name for multi-account support (populated from --account flag)"
    )
    
    # General settings
    exchange: str = Field(
        default="multi",
        description="Exchange identifier (for logging)"
    )
    
    ticker: str = Field(
        default="MULTI",
        description="Ticker identifier (for logging)"
    )
    
    class Config:
        use_enum_values = True
        validate_assignment = True
        json_encoders = {
            Decimal: str
        }
    
    # ========================================================================
    # Utility Methods
    # ========================================================================
    
    def get_dex_list(self) -> List[str]:
        """Get list of DEXes"""
        return self.exchanges
    
    def is_dex_enabled(self, dex_name: str) -> bool:
        """Check if DEX is enabled"""
        return dex_name in self.exchanges
    
    def get_risk_strategy(self) -> str:
        """Get risk management strategy name"""
        return self.risk_config.strategy
