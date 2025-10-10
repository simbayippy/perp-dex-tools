"""
Funding Arbitrage Strategy - Parameter Schema

Defines all configurable parameters for the funding arbitrage strategy
for use with the interactive configuration builder.
"""

from decimal import Decimal
from strategies.base_schema import (
    StrategySchema,
    ParameterSchema,
    ParameterType,
    create_exchange_choice_parameter,
    create_decimal_parameter,
    create_boolean_parameter
)
from exchange_clients.factory import ExchangeFactory


# ============================================================================
# Funding Arbitrage Strategy Schema
# ============================================================================

FUNDING_ARB_SCHEMA = StrategySchema(
    name="funding_arbitrage",
    display_name="Funding Rate Arbitrage",
    description="Delta-neutral funding rate arbitrage across multiple DEXs. "
                "Profit from funding rate differences by going long on one exchange "
                "and short on another.",
    
    parameters=[
        # ====================================================================
        # Exchange Configuration
        # ====================================================================
        create_exchange_choice_parameter(
            key="primary_exchange",
            prompt="Which exchange should be your PRIMARY exchange?",
            required=True,
            help_text="This exchange will handle the main connection and risk management"
        ),
        
        ParameterSchema(
            key="scan_exchanges",
            prompt="Which exchanges should we scan for opportunities?",
            param_type=ParameterType.MULTI_CHOICE,
            choices=ExchangeFactory.get_supported_exchanges(),
            default=["lighter", "grvt", "backpack"],  # List, not string!
            required=True,
            help_text="We'll look for funding rate divergences across these exchanges",
            show_default_in_prompt=True
        ),
        
        # ====================================================================
        # Position Sizing
        # ====================================================================
        create_decimal_parameter(
            key="target_exposure",
            prompt="What is your target position size per side (USD)?",
            default=Decimal("100"),
            min_value=Decimal("0.10"),
            max_value=Decimal("100000"),
            required=True,
            help_text="This is the USD value of each long/short position. "
                     "Example: $100 means $100 long + $100 short = $200 total notional"
        ),
        
        ParameterSchema(
            key="max_positions",
            prompt="Maximum number of concurrent positions?",
            param_type=ParameterType.INTEGER,
            default=5,
            min_value=1,
            max_value=50,
            required=False,
            help_text="Limit the number of open funding arb positions to manage risk",
            show_default_in_prompt=True
        ),
        
        create_decimal_parameter(
            key="max_total_exposure_usd",
            prompt="Maximum total exposure across all positions (USD)?",
            default=Decimal("1000"),
            min_value=Decimal("1.00"),
            max_value=Decimal("1000000"),
            required=False,
            help_text="Total notional value limit. Example: 5 positions × $200 each = $1000"
        ),
        
        # ====================================================================
        # Profitability Thresholds
        # ====================================================================
        create_decimal_parameter(
            key="min_profit_rate",
            prompt="Minimum profit rate to enter position (e.g., 0.0001 = 0.01%)?",
            default=Decimal("0.0001"),
            min_value=Decimal("0.00001"),
            max_value=Decimal("0.1"),
            required=True,
            help_text="Only enter positions with net profit (after fees) above this threshold. "
                     "Lower = more opportunities but lower profit per trade"
        ),
        
        create_decimal_parameter(
            key="max_oi_usd",
            prompt="Maximum open interest filter (USD)?",
            default=Decimal("10000000"),
            min_value=Decimal("1000"),
            max_value=Decimal("999999999"),
            required=False,
            help_text="For POINT FARMING: use low values (e.g., 50000) to target small markets. "
                     "For PURE PROFIT: use high values (e.g., 10M+) to access all markets"
        ),
        
        # ====================================================================
        # Risk Management
        # ====================================================================
        ParameterSchema(
            key="risk_strategy",
            prompt="Which risk management strategy?",
            param_type=ParameterType.CHOICE,
            choices=["combined", "profit_erosion", "divergence_flip", "time_based"],
            default="combined",
            required=True,
            help_text="COMBINED (recommended): All risk checks. "
                     "PROFIT_EROSION: Close when profit drops. "
                     "DIVERGENCE_FLIP: Close when rates flip. "
                     "TIME_BASED: Close after fixed duration",
            show_default_in_prompt=True
        ),
        
        create_decimal_parameter(
            key="profit_erosion_threshold",
            prompt="Profit erosion threshold (e.g., 0.5 = close when profit drops 50%)?",
            default=Decimal("0.5"),
            min_value=Decimal("0.1"),
            max_value=Decimal("0.9"),
            required=False,
            help_text="Close position when current profit drops to X% of entry profit. "
                     "Lower = exit sooner (more conservative)"
        ),
        
        ParameterSchema(
            key="max_position_age_hours",
            prompt="Maximum position age (hours)?",
            param_type=ParameterType.INTEGER,
            default=168,  # 1 week
            min_value=1,
            max_value=720,  # 30 days
            required=False,
            help_text="Force close positions older than this, regardless of profit",
            show_default_in_prompt=True
        ),
        
        # ====================================================================
        # Execution Settings
        # ====================================================================
        ParameterSchema(
            key="max_new_positions_per_cycle",
            prompt="Maximum new positions to open per cycle?",
            param_type=ParameterType.INTEGER,
            default=2,
            min_value=1,
            max_value=10,
            required=False,
            help_text="Rate limit: open at most N positions per execution cycle (usually 60s). "
                     "Prevents overexposure during volatile markets",
            show_default_in_prompt=True
        ),
        
        ParameterSchema(
            key="check_interval_seconds",
            prompt="How often to check positions and opportunities (seconds)?",
            param_type=ParameterType.INTEGER,
            default=60,
            min_value=10,
            max_value=300,
            required=False,
            help_text="Execution cycle frequency. Lower = more responsive but more API calls",
            show_default_in_prompt=True
        ),
        
        create_boolean_parameter(
            key="dry_run",
            prompt="Run in dry-run mode (no real trades)?",
            default=True,
            required=False,
            help_text="TEST MODE: Strategy will run but won't place real orders. "
                     "Great for testing configuration!"
        ),
    ],
    
    # Category grouping for better UX
    categories={
        "Exchanges": ["primary_exchange", "scan_exchanges"],
        "Position Sizing": ["target_exposure", "max_positions", "max_total_exposure_usd"],
        "Profitability": ["min_profit_rate", "max_oi_usd"],
        "Risk Management": ["risk_strategy", "profit_erosion_threshold", "max_position_age_hours"],
        "Execution": ["max_new_positions_per_cycle", "check_interval_seconds", "dry_run"]
    }
)


# ============================================================================
# Helper Functions
# ============================================================================

def get_funding_arb_schema() -> StrategySchema:
    """Get the funding arbitrage parameter schema."""
    return FUNDING_ARB_SCHEMA


def create_default_funding_config() -> dict:
    """
    Create a default configuration for funding arbitrage.
    
    Useful for quick testing or as a starting point.
    """
    return {
        "primary_exchange": "lighter",
        "scan_exchanges": ["lighter", "grvt", "backpack"],
        "target_exposure": Decimal("100"),
        "max_positions": 5,
        "max_total_exposure_usd": Decimal("1000"),
        "min_profit_rate": Decimal("0.0001"),
        "max_oi_usd": Decimal("10000000"),
        "risk_strategy": "combined",
        "profit_erosion_threshold": Decimal("0.5"),
        "max_position_age_hours": 168,
        "max_new_positions_per_cycle": 2,
        "check_interval_seconds": 60,
        "dry_run": True
    }

