"""
Dashboard snapshot data models.

These Pydantic models formalize the structured payload passed between the
strategy layer, dashboard aggregator, and terminal renderers. They intentionally
favour explicit fields over loosely typed dictionaries to improve validation and
keep the interface extensible.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ============================================================================
# Enumerations
# ============================================================================

class SessionHealth(str, Enum):
    """Overall health of the trading session."""

    STARTING = "starting"
    RUNNING = "running"
    IDLE = "idle"
    DEGRADED = "degraded"
    ERROR = "error"
    STOPPING = "stopping"
    STOPPED = "stopped"


class LifecycleStage(str, Enum):
    """High-level lifecycle stage of the active strategy."""

    INITIALIZING = "initializing"
    SCANNING = "scanning"
    OPENING = "opening_position"
    MONITORING = "monitoring_position"
    REBALANCING = "rebalancing"
    CLOSING = "closing_position"
    COMPLETE = "cycle_complete"
    IDLE = "idle"


class TimelineCategory(str, Enum):
    """Event categorisation for the timeline feed."""

    STAGE = "stage"
    EXECUTION = "execution"
    FUNDING = "funding"
    RISK = "risk"
    WARNING = "warning"
    ERROR = "error"
    INFO = "info"


# ============================================================================
# Core Models
# ============================================================================

class SessionState(BaseModel):
    """Metadata describing the current bot session."""

    model_config = ConfigDict(str_strip_whitespace=True, json_encoders={Decimal: str})

    session_id: UUID = Field(..., description="Unique identifier for the running session.")
    strategy: str = Field(..., description="Human-readable strategy identifier.")
    config_path: Optional[str] = Field(
        default=None, description="Path to the configuration file used for this session."
    )
    started_at: datetime = Field(..., description="Timestamp when the session started.")
    last_heartbeat: datetime = Field(..., description="Most recent heartbeat from the session.")
    health: SessionHealth = Field(default=SessionHealth.RUNNING, description="Operational health indicator.")
    lifecycle_stage: LifecycleStage = Field(default=LifecycleStage.INITIALIZING, description="Current lifecycle stage.")
    max_positions: Optional[int] = Field(
        default=None, description="Maximum simultaneous positions allowed for the strategy."
    )
    max_total_exposure_usd: Optional[Decimal] = Field(
        default=None, description="Maximum total USD exposure permitted by the strategy config."
    )
    dry_run: bool = Field(
        default=False,
        description="Indicates whether this session is operating in dry-run / paper trading mode.",
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional strategy-specific metadata (e.g., exchange connectivity).",
    )


class PositionLegSnapshot(BaseModel):
    """Represents one venue leg of a delta-neutral position."""

    model_config = ConfigDict(str_strip_whitespace=True, json_encoders={Decimal: str})

    venue: str = Field(..., description="Exchange or venue identifier (e.g., 'lighter').")
    side: Literal["long", "short"] = Field(..., description="Directional exposure of this leg.")
    quantity: Decimal = Field(..., description="Base asset quantity currently held.")
    exposure_usd: Decimal = Field(..., description="Mark-to-market USD exposure for this leg.")
    entry_price: Decimal = Field(..., description="Average entry price for the filled quantity.")
    mark_price: Optional[Decimal] = Field(default=None, description="Current mark price from the venue.")
    leverage: Optional[Decimal] = Field(default=None, description="Leverage applied to this leg, if any.")
    realized_pnl: Decimal = Field(default=Decimal("0"), description="Realized PnL from partial closes or funding.")
    fees_paid: Decimal = Field(default=Decimal("0"), description="Total trading fees paid for this leg.")
    funding_accrued: Decimal = Field(default=Decimal("0"), description="Net funding payments accrued on this leg.")
    margin_reserved: Optional[Decimal] = Field(
        default=None, description="Margin reserved/collateral requirements for this leg."
    )
    last_updated: datetime = Field(..., description="Timestamp of the most recent update for this leg.")

    @field_validator("venue", mode="before")
    def _lowercase_venue(cls, value: str) -> str:
        return value.lower()


class PositionSnapshot(BaseModel):
    """Aggregated view of a delta-neutral position across venues."""

    model_config = ConfigDict(str_strip_whitespace=True, json_encoders={Decimal: str})

    position_id: UUID = Field(..., description="Unique identifier for the position as stored in the DB.")
    symbol: str = Field(..., description="Canonical market symbol (e.g., 'ZORA').")
    strategy_tag: Optional[str] = Field(
        default=None, description="Optional tag for strategies managing multiple position types."
    )
    opened_at: datetime = Field(..., description="Timestamp when the position became active.")
    last_update: datetime = Field(..., description="Timestamp of the last state change.")
    lifecycle_stage: LifecycleStage = Field(
        default=LifecycleStage.MONITORING, description="Lifecycle stage specific to this position."
    )
    legs: List[PositionLegSnapshot] = Field(..., description="Collection of venue legs composing the position.")
    notional_exposure_usd: Decimal = Field(..., description="Net absolute exposure (sum of |leg.exposure_usd| / 2).")
    entry_divergence_pct: Optional[Decimal] = Field(
        default=None, description="Divergence percentage used when opening the position."
    )
    current_divergence_pct: Optional[Decimal] = Field(
        default=None, description="Current divergence percentage based on mark prices."
    )
    profit_erosion_pct: Optional[Decimal] = Field(
        default=None, description="Percentage erosion vs. entry divergence (positive means erosion)."
    )
    unrealized_pnl: Decimal = Field(default=Decimal("0"), description="Net unrealized PnL across both legs.")
    realized_pnl: Decimal = Field(default=Decimal("0"), description="Net realized PnL across both legs.")
    funding_accrued: Decimal = Field(default=Decimal("0"), description="Net funding collected since opening.")
    rebalance_pending: bool = Field(default=False, description="Flag indicating whether a rebalance is queued.")
    max_position_age_seconds: Optional[int] = Field(
        default=None, description="Maximum permitted lifetime for the position."
    )
    custom_metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Strategy-specific metadata (e.g., stop-loss config, hedging IDs).",
    )

    @field_validator("legs")
    def _validate_legs(cls, legs: List[PositionLegSnapshot]) -> List[PositionLegSnapshot]:
        if not legs:
            raise ValueError("PositionSnapshot requires at least one leg.")
        return legs


class FundingRateSnapshot(BaseModel):
    """Funding rate context for a single venue-symbol pair."""

    model_config = ConfigDict(str_strip_whitespace=True, json_encoders={Decimal: str})

    venue: str = Field(..., description="Venue identifier.")
    symbol: str = Field(..., description="Symbol identifier.")
    current_rate: Decimal = Field(..., description="Current funding rate (decimal form, e.g., 0.0005 = 5 bps).")
    next_rate: Optional[Decimal] = Field(default=None, description="Predicted next funding rate, if available.")
    next_funding_time: Optional[datetime] = Field(
        default=None, description="Timestamp for the next funding interval."
    )
    accrued_since_open: Decimal = Field(
        default=Decimal("0"), description="Funding accrued on this venue since the position opened."
    )
    last_updated: datetime = Field(..., description="Timestamp when this rate was last refreshed.")

    @field_validator("venue", mode="before")
    def _lowercase_venue(cls, value: str) -> str:
        return value.lower()


class FundingSnapshot(BaseModel):
    """Aggregate funding data for active positions."""

    model_config = ConfigDict(json_encoders={Decimal: str})

    total_accrued: Decimal = Field(default=Decimal("0"), description="Total funding accrued across all venues.")
    weighted_average_rate: Optional[Decimal] = Field(
        default=None, description="Exposure-weighted funding rate across positions."
    )
    next_event_countdown_seconds: Optional[int] = Field(
        default=None, description="Seconds until the soonest upcoming funding event."
    )
    rates: List[FundingRateSnapshot] = Field(default_factory=list, description="Per-venue funding rate snapshots.")


class PortfolioSnapshot(BaseModel):
    """Roll-up metrics across all open positions."""

    model_config = ConfigDict(json_encoders={Decimal: str})

    total_positions: int = Field(default=0, description="Number of open positions.")
    total_notional_usd: Decimal = Field(default=Decimal("0"), description="Gross notional exposure across positions.")
    net_unrealized_pnl: Decimal = Field(default=Decimal("0"), description="Aggregate unrealized PnL.")
    net_realized_pnl: Decimal = Field(default=Decimal("0"), description="Aggregate realized PnL.")
    funding_accrued: Decimal = Field(default=Decimal("0"), description="Total funding accrued.")
    free_collateral_usd: Optional[Decimal] = Field(
        default=None, description="Estimated free collateral across venues."
    )
    maintenance_margin_ratio: Optional[Decimal] = Field(
        default=None, description="Composite maintenance margin consumption ratio."
    )
    alerts: List[str] = Field(default_factory=list, description="Human-readable portfolio level alerts.")


class TimelineEvent(BaseModel):
    """Timeline entry representing notable lifecycle events."""

    model_config = ConfigDict(str_strip_whitespace=True)

    ts: datetime = Field(..., description="Event timestamp.")
    category: TimelineCategory = Field(..., description="Event category.")
    message: str = Field(..., description="Concise human-readable message.")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Structured event metadata for renderers.")


class DashboardSnapshot(BaseModel):
    """
    Top-level snapshot consumed by dashboard renderers.

    Combines session, position, funding, and portfolio information with a
    condensed timeline of recent events.
    """

    session: SessionState = Field(..., description="Information about the running trading session.")
    positions: List[PositionSnapshot] = Field(default_factory=list, description="Active positions under management.")
    portfolio: PortfolioSnapshot = Field(default_factory=PortfolioSnapshot, description="Portfolio roll-up metrics.")
    funding: FundingSnapshot = Field(default_factory=FundingSnapshot, description="Funding context for positions.")
    recent_events: List[TimelineEvent] = Field(
        default_factory=list, description="Recent lifecycle events (typically last 10-20)."
    )
    generated_at: datetime = Field(..., description="Timestamp when this snapshot was generated.")

    model_config = ConfigDict(json_encoders={Decimal: str})

    @model_validator(mode="after")
    def _sort_positions(self) -> "DashboardSnapshot":
        self.positions = sorted(self.positions, key=lambda p: p.last_update, reverse=True)
        return self
