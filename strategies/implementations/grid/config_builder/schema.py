"""
Grid Strategy - Parameter Schema

Defines all configurable parameters for the grid trading strategy
for use with the interactive configuration builder.
"""

from decimal import Decimal

from strategies.base_schema import (
    StrategySchema,
    ParameterSchema,
    ParameterType,
    create_exchange_choice_parameter,
    create_decimal_parameter,
    create_boolean_parameter,
)


# ============================================================================
# Grid Strategy Schema
# ============================================================================

GRID_STRATEGY_SCHEMA = StrategySchema(
    name="grid",
    display_name="Grid Trading",
    description="Place multiple orders at different price levels to capture profits "
    "from market volatility. Works best in ranging/sideways markets.",
    parameters=[
        # ====================================================================
        # Exchange Configuration
        # ====================================================================
        ParameterSchema(
            key="exchange",
            prompt="Which exchange to trade on?",
            param_type=ParameterType.CHOICE,
            choices=["lighter"],
            default="lighter",
            required=True,
            help_text="Grid currently supports the Lighter connector; other venues are pending.",
            show_default_in_prompt=True,
        ),
        ParameterSchema(
            key="ticker",
            prompt="Which trading pair? (e.g., BTC, ETH, HYPE)",
            param_type=ParameterType.STRING,
            default="BTC",
            required=True,
            min_length=1,
            max_length=20,
            help_text="The cryptocurrency symbol to trade",
        ),
        # ====================================================================
        # Grid Configuration
        # ====================================================================
        ParameterSchema(
            key="direction",
            prompt="Trading direction?",
            param_type=ParameterType.CHOICE,
            choices=["buy", "sell"],
            required=True,
            help_text="BUY: Place buy orders (accumulate). SELL: Place sell orders (distribute)",
        ),
        create_decimal_parameter(
            key="order_notional_usd",
            prompt="Target position notional per order (USD)?",
            min_value=Decimal("1"),
            max_value=Decimal("1000000"),
            required=True,
            help_text=(
                "Total exposure per grid order (USD). Example: 25 = ~$25 position value; "
                "margin required equals notional ÷ applied leverage."
            ),
        ),
        create_decimal_parameter(
            key="take_profit",
            prompt="Take profit percentage (e.g., 0.08 = 0.08%)?",
            default=Decimal("0.08"),
            min_value=Decimal("0.001"),
            max_value=Decimal("1"),
            required=True,
            help_text=(
                "How much profit to take on each trade (0.001–1). Provide values as whole percentages "
                "(0.08 = 0.08%, 0.15 = 0.15%). Higher = more profit but fewer fills."
            ),
        ),
        create_decimal_parameter(
            key="target_leverage",
            prompt="Desired leverage multiple (optional)?",
            default=None,
            min_value=Decimal("1"),
            max_value=Decimal("1000"),
            required=False,
            help_text="Request leverage to use for the grid. We will clamp to the exchange maximum.",
        ),
        # ====================================================================
        # Grid Spacing
        # ====================================================================
        create_decimal_parameter(
            key="grid_step",
            prompt="Grid step (minimum distance between orders, e.g., 0.002 = 0.2%)?",
            default=Decimal("0.002"),
            min_value=Decimal("0.0001"),
            max_value=Decimal("0.1"),
            required=False,
            help_text="Minimum price distance between grid orders. Lower = denser grid",
        ),
        ParameterSchema(
            key="max_orders",
            prompt="Maximum number of active orders?",
            param_type=ParameterType.INTEGER,
            default=25,
            min_value=1,
            max_value=100,
            required=False,
            help_text="Limit the number of open orders to manage risk",
            show_default_in_prompt=True,
        ),
        create_decimal_parameter(
            key="max_margin_usd",
            prompt="Maximum margin to allocate (USD)?",
            min_value=Decimal("10"),
            max_value=Decimal("1000000"),
            required=True,
            help_text="Caps how much account margin the grid strategy can consume",
        ),
        # ====================================================================
        # Timing Configuration
        # ====================================================================
        ParameterSchema(
            key="wait_time",
            prompt="Wait time between orders (seconds)?",
            param_type=ParameterType.INTEGER,
            default=10,
            min_value=1,
            max_value=300,
            required=False,
            help_text="How long to wait between placing new orders",
            show_default_in_prompt=True,
        ),
        # ====================================================================
        # Risk Management
        # ====================================================================
        create_boolean_parameter(
            key="stop_loss_enabled",
            prompt="Enable per-position stop loss?",
            default=True,
            required=False,
            help_text="Automatically place stop loss exits for individual positions",
        ),
        create_decimal_parameter(
            key="stop_loss_percentage",
            prompt="Stop loss percentage (e.g., 2 = 2%)?",
            default=Decimal("2.0"),
            min_value=Decimal("0.5"),
            max_value=Decimal("10"),
            required=False,
            help_text="Closes positions once loss exceeds this percentage threshold",
        ),
        ParameterSchema(
            key="position_timeout_minutes",
            prompt="Minutes before a position is considered stuck?",
            param_type=ParameterType.INTEGER,
            default=60,
            min_value=5,
            max_value=1440,
            required=False,
            help_text="After this time the recovery engine evaluates the position",
            show_default_in_prompt=True,
        ),
        ParameterSchema(
            key="recovery_mode",
            prompt="Recovery mode for stuck positions?",
            param_type=ParameterType.CHOICE,
            choices=["aggressive", "ladder", "hedge", "none"],
            default="ladder",
            required=False,
            help_text=(
                "How to unwind stuck positions: aggressive=market exit,"
                " ladder=staggered limit orders, hedge=offsetting market order,"
                " none=manual management"
            ),
        ),
        create_decimal_parameter(
            key="post_only_tick_multiplier",
            prompt="Tick multiplier for post-only limit placement?",
            default=Decimal("10"),
            min_value=Decimal("1"),
            max_value=Decimal("20"),
            required=False,
            help_text="How many ticks away from the top of book to anchor post-only orders",
        ),
        create_decimal_parameter(
            key="stop_price",
            prompt="Stop price (optional - close all if price crosses)?",
            default=None,
            min_value=Decimal("0"),
            required=False,
            help_text="Emergency exit: close all positions if price goes beyond this level",
        ),
        create_decimal_parameter(
            key="pause_price",
            prompt="Pause price (optional - stop new orders if price crosses)?",
            default=None,
            min_value=Decimal("0"),
            required=False,
            help_text="Pause trading but keep positions open if price reaches this level",
        ),
    ],
    # Category grouping
    categories={
        "Exchange": ["exchange", "ticker"],
        "Grid Setup": ["direction", "order_notional_usd", "take_profit", "target_leverage"],
        "Grid Spacing": ["grid_step", "max_orders"],
        "Capital & Limits": ["max_margin_usd"],
        "Execution": ["wait_time"],
        "Risk Management": [
            "stop_loss_enabled",
            "stop_loss_percentage",
            "position_timeout_minutes",
            "recovery_mode",
            "post_only_tick_multiplier",
            "stop_price",
            "pause_price",
        ],
    },
)


# ============================================================================
# Helper Functions
# ============================================================================


def get_grid_schema() -> StrategySchema:
    """Get the grid strategy parameter schema."""
    return GRID_STRATEGY_SCHEMA


def create_default_grid_config() -> dict:
    """
    Create a default configuration for grid trading.

    Useful for quick testing or as a starting point.
    """
    return {
        "exchange": "lighter",
        "ticker": "BTC",
        "direction": "buy",
        "order_notional_usd": Decimal("100"),
        "target_leverage": Decimal("10"),
        "take_profit": Decimal("0.008"),
        "grid_step": Decimal("0.002"),
        "max_orders": 25,
        "wait_time": 10,
        "max_margin_usd": Decimal("5000"),
        "stop_loss_enabled": True,
        "stop_loss_percentage": Decimal("2.0"),
        "position_timeout_minutes": 60,
        "recovery_mode": "ladder",
        "post_only_tick_multiplier": Decimal("2"),
        "stop_price": None,
        "pause_price": None,
    }
