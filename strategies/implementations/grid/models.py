"""
Grid Trading Strategy Data Models

Data structures specific to the grid trading strategy.
"""

from decimal import Decimal
from typing import Optional, List
from dataclasses import dataclass
from enum import Enum


class GridCycleState(Enum):
    """Grid trading cycle states."""
    READY = "ready"                      # Ready to place open order
    WAITING_FOR_FILL = "waiting_for_fill"  # Waiting for open order to fill
    COMPLETE = "complete"                # Cycle complete


@dataclass
class GridOrder:
    """Represents an active grid order."""
    order_id: str
    price: Decimal
    size: Decimal
    side: str  # 'buy' or 'sell'
    
    def to_dict(self) -> dict:
        """Convert to dictionary for state storage."""
        return {
            'id': self.order_id,
            'price': float(self.price),
            'size': float(self.size),
            'side': self.side
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'GridOrder':
        """Create from dictionary."""
        return cls(
            order_id=data['id'],
            price=Decimal(str(data['price'])),
            size=Decimal(str(data['size'])),
            side=data['side']
        )


@dataclass
class GridState:
    """Internal state for grid strategy."""
    cycle_state: GridCycleState = GridCycleState.READY
    active_close_orders: List[GridOrder] = None
    last_close_orders_count: int = 0
    last_open_order_time: float = 0
    filled_price: Optional[Decimal] = None
    filled_quantity: Optional[Decimal] = None
    
    def __post_init__(self):
        """Initialize default values."""
        if self.active_close_orders is None:
            self.active_close_orders = []
    
    def to_dict(self) -> dict:
        """Convert to dictionary for persistence."""
        return {
            'cycle_state': self.cycle_state.value,
            'active_close_orders': [order.to_dict() for order in self.active_close_orders],
            'last_close_orders_count': self.last_close_orders_count,
            'last_open_order_time': self.last_open_order_time,
            'filled_price': float(self.filled_price) if self.filled_price else None,
            'filled_quantity': float(self.filled_quantity) if self.filled_quantity else None
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'GridState':
        """Create from dictionary."""
        return cls(
            cycle_state=GridCycleState(data.get('cycle_state', 'ready')),
            active_close_orders=[
                GridOrder.from_dict(order) 
                for order in data.get('active_close_orders', [])
            ],
            last_close_orders_count=data.get('last_close_orders_count', 0),
            last_open_order_time=data.get('last_open_order_time', 0),
            filled_price=Decimal(str(data['filled_price'])) if data.get('filled_price') else None,
            filled_quantity=Decimal(str(data['filled_quantity'])) if data.get('filled_quantity') else None
        )

