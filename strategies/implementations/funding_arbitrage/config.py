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
    
    class Config:
        use_enum_values = True
        validate_assignment = True


# ============================================================================
# Atomic Execution Retry Configuration
# ============================================================================

class AtomicRetryConfig(BaseModel):
    """Settings for re-attempting partially filled atomic legs."""

    enabled: bool = Field(
        default=True,
        description="Enable retry attempts for partially filled atomic orders.",
    )
    max_attempts: int = Field(
        default=2,
        description="Maximum number of retry passes to attempt after the initial batch.",
    )
    per_attempt_timeout_seconds: float = Field(
        default=20.0,
        description="Timeout applied to each retry order (seconds).",
    )
    retry_delay_seconds: float = Field(
        default=1.0,
        description="Delay between retry passes (seconds).",
    )
    max_retry_duration_seconds: float = Field(
        default=40.0,
        description="Maximum cumulative time spent retrying before falling back to hedge.",
    )
    min_retry_quantity: Decimal = Field(
        default=Decimal("0"),
        description="Minimum remaining quantity required to trigger a retry attempt.",
    )
    limit_price_offset_pct_override: Optional[Decimal] = Field(
        default=None,
        description="Optional override for limit price offset during retries.",
    )


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
    
    default_position_size_usd: Decimal = Field(
        default=Decimal("1000"),
        description="Default size for each position in USD"
    )
    
    max_position_size_usd: Decimal = Field(
        default=Decimal("10000"),
        description="Max size per position in USD"
    )
    
    max_total_exposure_usd: Decimal = Field(
        default=Decimal("50000"),
        description="Max total exposure across all positions"
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

    atomic_retry: AtomicRetryConfig = Field(
        default_factory=AtomicRetryConfig,
        description="Retry policy for atomic execution legs.",
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
