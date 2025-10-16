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

    @property
    def remaining_usd(self) -> Decimal:
        """How much USD notional still needs to be hedged."""
        remaining = self.spec.size_usd - self.filled_usd
        return remaining if remaining > Decimal("0") else Decimal("0")

    @property
    def remaining_quantity(self) -> Decimal:
        """Remaining base quantity yet to be executed."""
        spec_quantity = getattr(self.spec, "quantity", None)
        if spec_quantity is None:
            return Decimal("0")
        remaining = Decimal(str(spec_quantity)) - self.filled_quantity
        return remaining if remaining > Decimal("0") else Decimal("0")

    def record_fill(self, quantity: Optional[Decimal], price: Optional[Decimal]) -> None:
        """Accumulate executed quantity and USD notionals."""
        if quantity is None or quantity <= Decimal("0"):
            return

        self.filled_quantity += quantity

        if price is not None and price > Decimal("0"):
            self.filled_usd += quantity * price
        elif self.filled_usd == Decimal("0"):
            # Fallback: assume full notional if price is unknown.
            self.filled_usd = self.spec.size_usd

        if self.filled_usd > self.spec.size_usd:
            self.filled_usd = self.spec.size_usd
