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
    
    # Optional safety parameters
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
    
    # Optional enhancement parameters
    boost_mode: bool = Field(
        False,
        description="Use market orders for faster execution (more aggressive)"
    )
    random_timing: bool = Field(
        False,
        description="Add random variation to wait times"
    )
    timing_range: Decimal = Field(
        Decimal('0.5'),
        description="Random timing variation range (e.g., 0.5 = ±50%)",
        ge=0,
        le=1
    )
    dynamic_profit: bool = Field(
        False,
        description="Add random variation to take profit levels"
    )
    profit_range: Decimal = Field(
        Decimal('0.5'),
        description="Dynamic profit variation range (e.g., 0.5 = ±50%)",
        ge=0,
        le=1
    )
    
    @validator('direction')
    def validate_direction(cls, v):
        """Validate direction is either 'buy' or 'sell'."""
        if v not in ['buy', 'sell']:
            raise ValueError("Direction must be 'buy' or 'sell'")
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

