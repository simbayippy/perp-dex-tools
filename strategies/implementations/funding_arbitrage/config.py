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
        description="Exit when divergence drops to X% of entry (0.4 = 40% remains = 60% erosion)"
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
    
    # Liquidation prevention
    enable_liquidation_prevention: bool = Field(
        default=True,
        description="Enable proactive liquidation prevention"
    )
    
    min_liquidation_distance_pct: Decimal = Field(
        default=Decimal("0.10"),  # 10%
        description="Minimum distance to liquidation before forced close"
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
    
    # Break-even price alignment
    enable_break_even_alignment: bool = Field(
        default=True,
        description="Enable break-even price alignment for initial entry (ensures long_entry < short_entry)"
    )
    
    max_spread_threshold_pct: Decimal = Field(
        default=Decimal("0.005"),
        description="Max spread % between exchanges to use aligned pricing (0.005 = 0.5%)"
    )
    
    hedge_break_even_max_deviation_pct: Decimal = Field(
        default=Decimal("0.005"),
        description="Max market movement % to attempt break-even hedge (0.005 = 0.5%)"
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
    
    # Entry validation
    max_entry_price_divergence_pct: Decimal = Field(
        default=Decimal("0.005"),  # 0.5%
        description="Maximum price divergence between exchanges to allow entry"
    )
    
    # Cooldown
    wide_spread_cooldown_minutes: int = Field(
        default=10,
        description="Cooldown period for symbols with wide spreads"
    )

    # Spread protection thresholds (aligned with spread_utils.py)
    max_entry_spread_pct: Decimal = Field(
        default=Decimal("0.001"),  # 0.1%
        description="Max spread % allowed for opening positions (0.001 = 0.1%)"
    )
    max_exit_spread_pct: Decimal = Field(
        default=Decimal("0.001"),  # 0.1%
        description="Max spread % before deferring non-critical exits (0.001 = 0.1%)"
    )
    max_emergency_close_spread_pct: Decimal = Field(
        default=Decimal("0.002"),  # 0.2%
        description="Max spread % for emergency closes (0.002 = 0.2%)"
    )
    enable_wide_spread_protection: bool = Field(
        default=True,
        description="Enable spread validation before closing"
    )
    
    # Immediate profit-taking (cross-exchange basis spread opportunities)
    enable_immediate_profit_taking: bool = Field(
        default=True,
        description=(
            "Enable immediate profit-taking when cross-exchange price divergence "
            "creates profit opportunity. Captures basis spread profits by closing "
            "when net_profit > min_immediate_profit_taking_pct * position_size."
        )
    )
    min_immediate_profit_taking_pct: Decimal = Field(
        default=Decimal("0.002"),  # 0.2%
        description=(
            "Minimum profit percentage (of position notional value) required to trigger "
            "immediate profit-taking. For example, 0.002 = 0.2%, so a $2000 position needs "
            "$4 profit minimum to close. This ensures profit opportunity is worth the execution."
        )
    )
    realtime_profit_check_interval: float = Field(
        default=1.0,
        description=(
            "Minimum seconds between profit checks per position (throttle). "
            "Prevents excessive checks on high-frequency BBO updates. "
            "Recommended: 1.0-2.0 seconds for balance between responsiveness and overhead."
        )
    )

    # Progressive price walking for wide spread markets
    max_aggressive_hedge_spread_pct: Decimal = Field(
        default=Decimal("0.002"),  # 0.2%
        description=(
            "Max spread % for aggressive limit retries (inside spread pricing). "
            "If spread exceeds this threshold, aggressive limit orders are skipped "
            "and progressive price walking begins. Default 0.15% vs hardcoded 0.05%."
        )
    )
    wide_spread_fallback_threshold: int = Field(
        default=3,
        description=(
            "Number of consecutive wide spread failures before switching to progressive "
            "price walking. Once this threshold is reached, execution transitions from "
            "aggressive inside-spread pricing to progressive mid-price walking."
        )
    )
    progressive_walk_max_attempts: int = Field(
        default=5,
        description=(
            "Maximum attempts during progressive price walking phase. Each attempt moves "
            "the limit price progressively closer to the aggressive side of the book, "
            "starting from mid-price. After exhausting these attempts, falls back to market order."
        )
    )
    progressive_walk_step_ticks: int = Field(
        default=1,
        description=(
            "Number of ticks to move per progressive walking attempt. Each subsequent "
            "attempt moves this many ticks closer to the aggressive side (bid for sells, "
            "ask for buys). Higher values = more aggressive progression."
        )
    )
    progressive_walk_min_spread_pct: Decimal = Field(
        default=Decimal("0.10"),  # 10% of spread
        description=(
            "Stop progressive walking when this close to aggressive side (as % of spread). "
            "Prevents walking too close to bid/ask where execution becomes taker-like. "
            "For example, 0.10 = stop at 10% of spread from aggressive side."
        )
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
