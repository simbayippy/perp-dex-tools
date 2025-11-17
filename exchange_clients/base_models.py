"""
Shared data structures, exceptions, and utilities for exchange clients.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple, Type, Union

from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)


class MissingCredentialsError(Exception):
    """Raised when exchange credentials are missing or invalid (placeholders)."""
    pass


def validate_credentials(
    credential_name: str,
    credential_value: Optional[str],
    placeholder_values: Optional[List[str]] = None,
) -> None:
    """
    Validate exchange credentials to ensure they're not missing or placeholders.

    Args:
        credential_name: Name of the credential (e.g., 'API_KEY')
        credential_value: Value of the credential from environment
        placeholder_values: List of placeholder values to reject

    Raises:
        MissingCredentialsError: If credential is missing or is a placeholder
    """
    if placeholder_values is None:
        placeholder_values = [
            "your_account_id_here",
            "your_api_key_here",
            "your_secret_key_here",
            "your_private_key_here",
            "your_public_key_here",
            "your_trading_account_id_here",
            "your_stark_private_key_here",
            "PLACEHOLDER",
            "placeholder",
            "",
        ]

    if not credential_value:
        raise MissingCredentialsError(f"Missing {credential_name} environment variable")

    if credential_value in placeholder_values:
        raise MissingCredentialsError(f"{credential_name} is not configured (placeholder or empty)")


def query_retry(
    default_return: Any = None,
    exception_type: Union[Type[Exception], Tuple[Type[Exception], ...]] = (Exception,),
    max_attempts: int = 5,
    min_wait: float = 1,
    max_wait: float = 10,
    reraise: bool = False,
):
    """
    Retry decorator for query operations with exponential backoff.

    Args:
        default_return: Value to return if all retries fail
        exception_type: Exception types to retry on
        max_attempts: Maximum number of retry attempts
        min_wait: Minimum wait time between retries
        max_wait: Maximum wait time between retries
        reraise: Whether to reraise the exception after retries
    """

    def retry_error_callback(retry_state: RetryCallState):
        print(
            f"Operation: [{retry_state.fn.__name__}] failed after {retry_state.attempt_number} retries, "
            f"exception: {str(retry_state.outcome.exception())}"
        )
        return default_return

    return retry(
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=1, min=min_wait, max=max_wait),
        retry=retry_if_exception_type(exception_type),
        retry_error_callback=retry_error_callback,
        reraise=reraise,
    )


@dataclass
class OrderResult:
    """Standardized order result structure returned by order placement methods."""

    success: bool
    order_id: Optional[str] = None
    side: Optional[str] = None
    size: Optional[Decimal] = None
    price: Optional[Decimal] = None
    status: Optional[str] = None
    error_message: Optional[str] = None
    filled_size: Optional[Decimal] = None


class CancelReason:
    """Standard cancellation reason constants for cross-exchange compatibility."""
    
    # User-initiated or normal cancellations
    USER_CANCELED = "user_canceled"
    TIMEOUT = "timeout"
    EXPIRED = "expired"
    
    # Exchange-initiated cancellations (retryable)
    POST_ONLY_VIOLATION = "post_only_violation"  # Order crossed book, violates post-only
    INSUFFICIENT_BALANCE = "insufficient_balance"
    REJECTED = "rejected"
    
    # Unknown/fallback
    UNKNOWN = "unknown"


def is_retryable_cancellation(cancel_reason: str) -> bool:
    """Check if a cancellation reason indicates we should retry the order.
    
    Args:
        cancel_reason: Cancellation reason string (e.g., CancelReason.POST_ONLY_VIOLATION)
        
    Returns:
        True if the cancellation is retryable (e.g., post-only violation due to price movement)
    """
    retryable_reasons = {
        CancelReason.POST_ONLY_VIOLATION,
        # Could add others like INSUFFICIENT_BALANCE if it's transient
    }
    return cancel_reason in retryable_reasons


@dataclass
class OrderInfo:
    """Standardized order information structure returned by order queries."""

    order_id: str
    side: str
    size: Decimal
    price: Decimal
    status: str
    filled_size: Decimal = 0.0
    remaining_size: Decimal = 0.0
    cancel_reason: str = ""


@dataclass
class ExchangePositionSnapshot:
    """
    Normalized position snapshot for a single trading symbol on an exchange.

    All numeric fields use Decimal for precision and are optional unless noted.
    """

    symbol: str
    quantity: Decimal = Decimal("0")
    side: Optional[str] = None
    entry_price: Optional[Decimal] = None
    mark_price: Optional[Decimal] = None
    exposure_usd: Optional[Decimal] = None
    unrealized_pnl: Optional[Decimal] = None
    realized_pnl: Optional[Decimal] = None
    funding_accrued: Optional[Decimal] = None
    margin_reserved: Optional[Decimal] = None
    leverage: Optional[Decimal] = None
    liquidation_price: Optional[Decimal] = None
    timestamp: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class FundingRateSample:
    """Standardized funding rate payload returned by funding adapters."""

    normalized_rate: Decimal
    raw_rate: Decimal
    interval_hours: Decimal
    next_funding_time: Optional[datetime] = None
    source_timestamp: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TradeData:
    """
    Standardized trade/fill data structure for cross-exchange compatibility.
    
    Represents a single trade/fill execution with all relevant details for PnL calculation.
    """
    trade_id: str  # Exchange-specific trade/fill identifier
    timestamp: float  # Unix timestamp in seconds
    symbol: str
    side: str  # "buy" or "sell"
    quantity: Decimal
    price: Decimal
    fee: Decimal
    fee_currency: str
    order_id: Optional[str] = None  # Order that generated this fill/trade
    realized_pnl: Optional[Decimal] = None  # If exchange provides it (Paradex does)
    realized_funding: Optional[Decimal] = None  # If exchange provides it (Paradex does)


__all__ = [
    "MissingCredentialsError",
    "validate_credentials",
    "query_retry",
    "OrderResult",
    "OrderInfo",
    "CancelReason",
    "is_retryable_cancellation",
    "ExchangePositionSnapshot",
    "FundingRateSample",
    "TradeData",
]
