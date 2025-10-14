"""
Configuration models for dashboard behaviour.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, PositiveInt


class DashboardSettings(BaseModel):
    """
    Runtime configuration for the terminal dashboard.
    """

    enabled: bool = Field(default=False, description="Enable terminal dashboard rendering and persistence.")
    renderer: Literal["rich", "plain"] = Field(
        default="rich", description="Renderer to use when dashboard is enabled."
    )
    refresh_interval_seconds: float = Field(
        default=1.0, description="UI refresh cadence when the dashboard is active."
    )
    persist_snapshots: bool = Field(
        default=True, description="Persist snapshots/events to PostgreSQL for later replay."
    )
    snapshot_retention: PositiveInt = Field(
        default=500, description="How many recent snapshots to retain per session."
    )
    event_retention: PositiveInt = Field(
        default=200, description="How many recent events to retain per session."
    )
    write_interval_seconds: float = Field(
        default=5.0,
        description="Minimum interval between persisted snapshots to reduce database load.",
    )
    replay_session_id: Optional[str] = Field(
        default=None,
        description="When set, run in replay mode for the referenced session rather than live updates.",
    )

    class Config:
        validate_assignment = True
