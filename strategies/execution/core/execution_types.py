"""
Execution types and models for order execution.

Contains shared data structures used across the execution layer to avoid circular imports.
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Optional


class ExecutionMode(Enum):
    """
    Execution modes for order placement.
    
    """
    LIMIT_ONLY = "limit_only"
    LIMIT_WITH_FALLBACK = "limit_with_fallback"
    MARKET_ONLY = "market_only"
    ADAPTIVE = "adaptive"


@dataclass
class ExecutionResult:
    """
    Result of order execution.
    
    Contains all metrics needed for quality analysis.
    """
    success: bool
    filled: bool
    
    # Price & quantity
    fill_price: Optional[Decimal] = None
    filled_quantity: Optional[Decimal] = None
    
    # Quality metrics
    expected_price: Optional[Decimal] = None
    slippage_usd: Decimal = Decimal('0')
    slippage_pct: Decimal = Decimal('0')
    
    # Execution details
    execution_mode_used: str = ""
    execution_time_ms: int = 0
    
    # Error handling
    error_message: Optional[str] = None
    order_id: Optional[str] = None
    
    # Retry handling
    retryable: bool = False  # True if order failure is retryable (e.g., post-only violation)

