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
        create_exchange_choice_parameter(
            key="exchange",
            prompt="Which exchange to trade on?",
            required=True,
            help_text="Grid strategy trades on a single exchange",
        ),
        ParameterSchema(
            key="ticker",
            prompt="Which trading pair? (e.g., BTC, ETH, HYPE)",
            param_type=ParameterType.STRING,
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
            key="quantity",
            prompt="Order quantity (per order)?",
            min_value=Decimal("0.001"),
            max_value=Decimal("1000000"),
            required=True,
            help_text="The size of each grid order",
        ),
        create_decimal_parameter(
            key="take_profit",
            prompt="Take profit percentage (e.g., 0.008 = 0.8%)?",
            min_value=Decimal("0.001"),
            max_value=Decimal("0.1"),
            required=True,
            help_text="How much profit to take on each trade. Higher = more profit but fewer fills",
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
        create_boolean_parameter(
            key="random_timing",
            prompt="Enable random timing variation?",
            default=False,
            required=False,
            help_text="Add randomness to wait time to avoid predictable patterns",
        ),
        # ====================================================================
        # Advanced Features
        # ====================================================================
        create_boolean_parameter(
            key="dynamic_profit",
            prompt="Enable dynamic profit-taking?",
            default=False,
            required=False,
            help_text="Adjust take profit based on market volatility",
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
        "Grid Setup": ["direction", "quantity", "take_profit"],
        "Grid Spacing": ["grid_step", "max_orders"],
        "Timing": ["wait_time", "random_timing"],
        "Advanced": ["dynamic_profit", "stop_price", "pause_price"],
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
        "quantity": Decimal("100"),
        "take_profit": Decimal("0.008"),
        "grid_step": Decimal("0.002"),
        "max_orders": 25,
        "wait_time": 10,
        "random_timing": False,
        "dynamic_profit": False,
        "stop_price": None,
        "pause_price": None,
    }
