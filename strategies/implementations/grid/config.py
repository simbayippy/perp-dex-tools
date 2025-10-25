"""
Grid Trading Strategy Configuration

Pydantic models for grid strategy configuration and validation.
"""

from decimal import Decimal
from typing import Optional
from pydantic import BaseModel, Field, validator


class GridConfig(BaseModel):
    """Configuration for grid trading strategy."""
    
    # Required parameters
    take_profit: Decimal = Field(
        ...,
        description="Take profit percentage for each grid level",
        gt=0
    )
    grid_step: Decimal = Field(
        ...,
        description="Distance between grid levels as percentage",
        gt=0
    )
    direction: str = Field(
        ...,
        description="Trading direction: 'buy' (long) or 'sell' (short)"
    )
    max_orders: int = Field(
        ...,
        description="Maximum number of active orders",
        gt=0
    )
    wait_time: float = Field(
        ...,
        description="Base cooldown time between orders in seconds",
        gt=0
    )
    max_margin_usd: Decimal = Field(
        ...,
        description="Maximum margin (USD) allocated to the grid strategy",
        gt=0
    )
    max_position_size: Decimal = Field(
        ...,
        description="Maximum absolute net position size allowed",
        gt=0
    )
    
    # Safety parameters
    stop_price: Optional[Decimal] = Field(
        None,
        description="Stop price - strategy stops if price crosses this level",
        gt=0
    )
    pause_price: Optional[Decimal] = Field(
        None,
        description="Pause price - strategy pauses temporarily if price crosses this level",
        gt=0
    )
    stop_loss_enabled: bool = Field(
        True,
        description="Enable stop loss for individual grid positions"
    )
    stop_loss_percentage: Decimal = Field(
        Decimal('2.0'),
        description="Stop loss percentage to cap losses per position",
        ge=Decimal('0.5'),
        le=Decimal('10')
    )
    position_timeout_minutes: int = Field(
        60,
        description="Minutes before an open position is considered stuck",
        ge=5,
        le=1440
    )
    recovery_mode: str = Field(
        "ladder",
        description="Recovery approach when handling stuck positions"
    )
    
    # Optional enhancement parameters
    boost_mode: bool = Field(
        False,
        description="Use market orders for faster execution (more aggressive)"
    )
    post_only_tick_multiplier: Decimal = Field(
        Decimal('2'),
        description="How many ticks away from top of book to place post-only orders",
        ge=Decimal('1'),
        le=Decimal('10')
    )
    order_margin_usd: Optional[Decimal] = Field(
        None,
        description="Optional: target initial margin (USD) per order",
        ge=Decimal('0')
    )
    
    @validator('direction')
    def validate_direction(cls, v):
        """Validate direction is either 'buy' or 'sell'."""
        if v not in ['buy', 'sell']:
            raise ValueError("Direction must be 'buy' or 'sell'")
        return v
    
    @validator('recovery_mode')
    def validate_recovery_mode(cls, v):
        """Validate recovery mode choice."""
        allowed = {'aggressive', 'ladder', 'hedge', 'none'}
        if v not in allowed:
            raise ValueError(f"Recovery mode must be one of {', '.join(sorted(allowed))}")
        return v
    
    @validator('stop_price', 'pause_price')
    def validate_prices(cls, v, values):
        """Validate stop and pause prices make sense for the direction."""
        if v is not None and v <= 0:
            raise ValueError("Prices must be positive")
        return v
    
    class Config:
        """Pydantic config."""
        validate_assignment = True
        extra = 'forbid'
