"""Data structures for atomic multi-order execution."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from .executor import OrderSpec


@dataclass
class OrderContext:
    """Holds state for a single order during atomic execution."""

    spec: "OrderSpec"
    cancel_event: asyncio.Event
    task: asyncio.Task
    result: Optional[Dict[str, Any]] = None
    completed: bool = False
    filled_quantity: Decimal = Decimal("0")
    filled_usd: Decimal = Decimal("0")
    hedge_target_quantity: Optional[Decimal] = None
    websocket_cancelled: bool = False  # Track if websocket reported cancellation

    @property
    def remaining_usd(self) -> Decimal:
        """How much USD notional still needs to be hedged."""
        remaining = self.spec.size_usd - self.filled_usd
        return remaining if remaining > Decimal("0") else Decimal("0")

    @property
    def remaining_quantity(self) -> Decimal:
        """Remaining base quantity yet to be executed."""
        target_quantity: Optional[Decimal] = None
        if self.hedge_target_quantity is not None:
            target_quantity = Decimal(str(self.hedge_target_quantity))
        else:
            spec_quantity = getattr(self.spec, "quantity", None)
            if spec_quantity is not None:
                target_quantity = Decimal(str(spec_quantity))

        if target_quantity is None:
            return Decimal("0")

        remaining = target_quantity - self.filled_quantity
        return remaining if remaining > Decimal("0") else Decimal("0")

    def record_fill(self, quantity: Optional[Decimal], price: Optional[Decimal]) -> None:
        """Accumulate executed quantity and USD notionals.
        
        This method is idempotent - safe to call multiple times with the same fill.
        """
        if quantity is None or quantity <= Decimal("0"):
            return

        self.filled_quantity += quantity

        if price is not None and price > Decimal("0"):
            self.filled_usd += quantity * price
        elif self.filled_usd == Decimal("0"):
            # Fallback: assume full notional if price is unknown.
            # BUT: Only do this if we actually have a fill (quantity > 0)
            # This prevents setting filled_usd incorrectly when quantity is 0
            if quantity > Decimal("0"):
                self.filled_usd = self.spec.size_usd
            # If quantity is 0 or negative, don't set filled_usd (shouldn't happen due to early return, but defensive)

        if self.filled_usd > self.spec.size_usd:
            self.filled_usd = self.spec.size_usd
    
    def on_websocket_fill(self, quantity: Decimal, price: Decimal) -> None:
        """Record fill from websocket callback.
        
        Called when websocket reports a fill event. This provides real-time
        fill tracking without polling.
        
        Args:
            quantity: Fill quantity (incremental, not cumulative)
            price: Fill price
        """
        if quantity is None or quantity <= Decimal("0"):
            return
        
        # Check if we've already filled more than spec (prevent over-filling)
        spec_quantity = getattr(self.spec, "quantity", None)
        if spec_quantity is not None:
            if self.filled_quantity >= spec_quantity:
                # Already fully filled, ignore additional fills
                return
        
        self.record_fill(quantity, price)
        
        # Update result dict if it exists
        if self.result is not None:
            self.result["filled_quantity"] = self.filled_quantity
            if price is not None and price > Decimal("0"):
                self.result["fill_price"] = price
    
    def on_websocket_cancel(self, filled_size: Decimal) -> None:
        """Mark order as cancelled via websocket callback.
        
        Called when websocket reports order cancellation. This provides real-time
        cancellation tracking and eliminates need for reconciliation in most cases.
        
        Args:
            filled_size: Final filled size reported by websocket (may be 0 if no fills)
        """
        self.websocket_cancelled = True
        
        # If websocket reports filled_size > 0, ensure we've recorded it
        if filled_size is not None and filled_size > Decimal("0"):
            # Check if we need to record additional fills
            if filled_size > self.filled_quantity:
                # Websocket reports more fills than we have - record the difference
                # Note: We don't have price here, so use None (record_fill will handle it)
                additional = filled_size - self.filled_quantity
                self.record_fill(additional, None)
                
                # Update result dict
                if self.result is not None:
                    self.result["filled_quantity"] = self.filled_quantity
                    self.result["filled"] = True
