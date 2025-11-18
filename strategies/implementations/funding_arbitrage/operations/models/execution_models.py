"""Execution-related data models."""

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from strategies.execution.patterns.atomic_multi_order import AtomicExecutionResult, OrderSpec
    from ...models import FundingArbPosition


@dataclass
class TradeExecutionResult:
    """Container for the result of executing the opening hedge."""

    position: "FundingArbPosition"
    timestamp_iso: str
    result: "AtomicExecutionResult"
    long_fill: Dict[str, Any]
    short_fill: Dict[str, Any]
    entry_fees: Decimal
    total_cost: Decimal


@dataclass
class PersistenceOutcome:
    """Describes how the position was persisted (merged or created)."""

    type: str  # "merged" | "created"
    position: "FundingArbPosition"
    updated_size: Optional[Decimal] = None
    additional_size: Optional[Decimal] = None


@dataclass
class OrderPlan:
    """Pre-computed execution plan for the atomic opener."""

    orders: List["OrderSpec"]
    quantity: Decimal
    long_notional: Decimal
    short_notional: Decimal
    long_price: Decimal
    short_price: Decimal

