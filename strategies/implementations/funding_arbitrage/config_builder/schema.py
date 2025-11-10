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
    create_decimal_parameter,
    create_boolean_parameter,
)
from exchange_clients.factory import ExchangeFactory


# ============================================================================
# Constants
# ============================================================================

# Funding occurs every 8 hours on most perp venues.
FUNDING_INTERVAL_HOURS = Decimal("8")
HOURS_PER_YEAR = Decimal("8760")
FUNDING_PAYMENTS_PER_YEAR = HOURS_PER_YEAR / FUNDING_INTERVAL_HOURS  # 1095 periods/year

DEFAULT_MIN_PROFIT_APY = Decimal("0.25")
DEFAULT_MIN_PROFIT_RATE_PER_INTERVAL = (DEFAULT_MIN_PROFIT_APY / FUNDING_PAYMENTS_PER_YEAR).quantize(Decimal("0.0000000001"))


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
        ParameterSchema(
            key="scan_exchanges",
            prompt="Which exchanges should we scan for opportunities?",
            param_type=ParameterType.MULTI_CHOICE,
            choices=ExchangeFactory.get_supported_exchanges(),
            default=["lighter", "aster", "paradex"],  # List, not string!
            required=True,
            help_text="We'll look for funding rate divergences across these exchanges",
            show_default_in_prompt=True,
        ),
        ParameterSchema(
            key="mandatory_exchange",
            prompt="Select a MANDATORY exchange (optional, choose 'none' to skip)",
            param_type=ParameterType.CHOICE,
            choices=["none"] + ExchangeFactory.get_supported_exchanges(),
            default="none",
            required=False,
            help_text=(
                "Optional guardrail: pick a DEX that must participate in every trade. "
                "Select 'none' if any combination of the scanned exchanges is acceptable."
            ),
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
            "Example: $100 means $100 long + $100 short = $200 total notional",
        ),
        ParameterSchema(
            key="max_positions",
            prompt="Maximum number of concurrent positions?",
            param_type=ParameterType.INTEGER,
            default=1,
            min_value=1,
            max_value=1,
            required=False,
            help_text="Limit the number of open funding arb positions to manage risk. For now only 1 position is allowed",
            show_default_in_prompt=True,
        ),
        create_decimal_parameter(
            key="max_total_exposure_usd",
            prompt="Maximum total exposure across all positions (USD)?",
            default=Decimal("1000"),
            min_value=Decimal("1.00"),
            max_value=Decimal("1000000"),
            required=False,
            help_text="Total notional value limit. Example: 5 positions × $200 each = $1000",
        ),
        # ====================================================================
        # Profitability Thresholds
        # ====================================================================
        create_decimal_parameter(
            key="min_profit_rate",
            prompt="Minimum annualised net profit (APY) before entering a position? (decimal, 0.50 = 50%)",
            default=DEFAULT_MIN_PROFIT_APY,
            min_value=Decimal("0"),
            max_value=None,
            required=True,
            help_text=(
                "Only take opportunities whose annualised (after-fee) funding yield meets this level. "
                "Enter as a decimal fraction: 0.10 ≈ 10% APY. The builder converts this to the "
                "per-funding-interval rate (8h cadence) required by the strategy."
            ),
        ),
        create_decimal_parameter(
            key="max_oi_usd",
            prompt="Maximum open interest filter (USD)?",
            default=Decimal("10000000"),
            min_value=Decimal("1000"),
            max_value=Decimal("999999999"),
            required=False,
            help_text=(
                "Only used when a mandatory exchange is set: skip trades if that exchange's open interest exceeds this cap. "
                "Use a low cap (e.g., 50000) to focus on point-farming pools; leave blank or set a high number to allow larger markets."
            ),
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
            show_default_in_prompt=True,
        ),
        create_decimal_parameter(
            key="profit_erosion_threshold",
            prompt="Profit erosion threshold (exit when live funding edge drops to X% of entry edge)?",
            default=Decimal("0.5"),
            min_value=Decimal("0.1"),
            max_value=Decimal("0.9"),
            required=False,
            help_text=(
                "Guard the *ongoing* funding edge. Example: 0.5 exits if the current funding "
                "spread is half of the entry spread. Lower values lock in profits sooner but "
                "can cut trades early. Works alongside min_profit_rate (entry guard)."
            ),
        ),
        ParameterSchema(
            key="min_hold_hours",
            prompt="Minimum position hold time (hours) before exits are considered?",
            param_type=ParameterType.DECIMAL,
            default=Decimal("0.0"),
            min_value=Decimal("0"),
            max_value=Decimal("720"),
            required=False,
            help_text="Set to >0 to suppress risk-based exits (funding flip, erosion) until the position has aged at least this many hours. Supports fractional hours (e.g., 1.5 = 1 hour 30 minutes).",
            show_default_in_prompt=True,
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
            show_default_in_prompt=True,
        ),
        # ====================================================================
        # Execution Settings
        # ====================================================================
        ParameterSchema(
            key="max_new_positions_per_cycle",
            prompt="Maximum new positions to open per cycle?",
            param_type=ParameterType.INTEGER,
            default=1,
            min_value=1,
            max_value=1,
            required=False,
            help_text="Rate limit: open at most N positions per execution cycle (usually 60s). For now only 1 position is allowed"
            "Prevents overexposure during volatile markets",
            show_default_in_prompt=True,
        ),
        create_decimal_parameter(
            key="limit_order_offset_pct",
            prompt="Limit order price offset (decimal, negative crosses spread)?",
            default=Decimal("0.0001"),
            min_value=Decimal("-0.01"),
            max_value=Decimal("0.05"),
            required=False,
            help_text="Improves maker pricing. Example: 0.0001 = 1bp inside; 0 = at touch; "
            "-0.0002 crosses by 2bp to fill faster.",
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
            show_default_in_prompt=True,
        ),

    ],
    # Category grouping for better UX
    categories={
        "Exchanges": ["scan_exchanges", "mandatory_exchange"],
        "Position Sizing": ["target_exposure", "max_positions", "max_total_exposure_usd"],
        "Profitability": ["min_profit_rate", "max_oi_usd"],
        "Risk Management": [
            "risk_strategy",
            "profit_erosion_threshold",
            "min_hold_hours",
            "max_position_age_hours",
        ],
        "Execution": [
            "max_new_positions_per_cycle",
            "limit_order_offset_pct",
            "check_interval_seconds",
        ],
    },
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
        "mandatory_exchange": None,
        "scan_exchanges": ["lighter", "aster", "paradex"],
        "target_exposure": Decimal("100"),
        "max_positions": 1,
        "max_total_exposure_usd": Decimal("1000"),
        "min_profit_rate": DEFAULT_MIN_PROFIT_RATE_PER_INTERVAL,
        "max_oi_usd": None,
        "risk_strategy": "combined",
        "profit_erosion_threshold": Decimal("0.5"),
        "min_hold_hours": 0,
        "max_position_age_hours": 168,
        "max_new_positions_per_cycle": 1,
        "check_interval_seconds": 60,
        "dry_run": False,
    }
