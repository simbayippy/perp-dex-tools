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
class TrackedPosition:
    """Metadata for monitoring active grid positions."""
    entry_price: Decimal
    size: Decimal
    side: str  # 'long' or 'short'
    open_time: float
    close_order_ids: List[str]
    recovery_attempts: int = 0
    hedged: bool = False
    last_recovery_time: float = 0.0
    
    def to_dict(self) -> dict:
        """Serialize tracked position state."""
        return {
            'entry_price': float(self.entry_price),
            'size': float(self.size),
            'side': self.side,
            'open_time': self.open_time,
            'close_order_ids': list(self.close_order_ids),
            'recovery_attempts': self.recovery_attempts,
            'hedged': self.hedged,
            'last_recovery_time': self.last_recovery_time,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'TrackedPosition':
        """Deserialize tracked position state."""
        return cls(
            entry_price=Decimal(str(data['entry_price'])),
            size=Decimal(str(data['size'])),
            side=data['side'],
            open_time=float(data['open_time']),
            close_order_ids=list(data.get('close_order_ids', [])),
            recovery_attempts=int(data.get('recovery_attempts', 0)),
            hedged=bool(data.get('hedged', False)),
            last_recovery_time=float(data.get('last_recovery_time', 0.0)),
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
    pending_open_order_id: Optional[str] = None
    pending_open_quantity: Optional[Decimal] = None
    last_known_position: Decimal = Decimal("0")
    last_known_margin: Decimal = Decimal("0")
    margin_ratio: Optional[Decimal] = None
    last_stop_loss_trigger: float = 0.0
    tracked_positions: List[TrackedPosition] = None
    
    def __post_init__(self):
        """Initialize default values."""
        if self.active_close_orders is None:
            self.active_close_orders = []
        if self.tracked_positions is None:
            self.tracked_positions = []
    
    def to_dict(self) -> dict:
        """Convert to dictionary for persistence."""
        return {
            'cycle_state': self.cycle_state.value,
            'active_close_orders': [order.to_dict() for order in self.active_close_orders],
            'last_close_orders_count': self.last_close_orders_count,
            'last_open_order_time': self.last_open_order_time,
            'filled_price': float(self.filled_price) if self.filled_price else None,
            'filled_quantity': float(self.filled_quantity) if self.filled_quantity else None,
            'pending_open_order_id': self.pending_open_order_id,
            'pending_open_quantity': float(self.pending_open_quantity) if self.pending_open_quantity is not None else None,
            'last_known_position': float(self.last_known_position),
            'last_known_margin': float(self.last_known_margin),
            'margin_ratio': float(self.margin_ratio) if self.margin_ratio is not None else None,
            'last_stop_loss_trigger': self.last_stop_loss_trigger,
            'tracked_positions': [pos.to_dict() for pos in self.tracked_positions],
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
            filled_quantity=Decimal(str(data['filled_quantity'])) if data.get('filled_quantity') else None,
            pending_open_order_id=data.get('pending_open_order_id'),
            pending_open_quantity=Decimal(str(data['pending_open_quantity'])) if data.get('pending_open_quantity') is not None else None,
            last_known_position=Decimal(str(data.get('last_known_position', 0))),
            last_known_margin=Decimal(str(data.get('last_known_margin', 0))),
            margin_ratio=Decimal(str(data['margin_ratio'])) if data.get('margin_ratio') is not None else None,
            last_stop_loss_trigger=data.get('last_stop_loss_trigger', 0.0),
            tracked_positions=[
                TrackedPosition.from_dict(pos)
                for pos in data.get('tracked_positions', [])
            ],
        )
