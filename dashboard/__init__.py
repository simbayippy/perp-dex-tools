"""
Dashboard package initialization.

Exports high-level schema objects used across the dashboard subsystem.
"""

from .models import (
    DashboardSnapshot,
    FundingRateSnapshot,
    FundingSnapshot,
    LifecycleStage,
    PortfolioSnapshot,
    PositionLegSnapshot,
    PositionSnapshot,
    SessionHealth,
    SessionState,
    TimelineCategory,
    TimelineEvent,
)
from .renderers import PlainTextDashboardRenderer, RichDashboardRenderer
from .event_bus import DashboardEventBus, event_bus
from .state import DashboardState, dashboard_state
from .control_server import DashboardControlServer, control_server

__all__ = [
    "DashboardSnapshot",
    "FundingRateSnapshot",
    "FundingSnapshot",
    "LifecycleStage",
    "PortfolioSnapshot",
    "PositionLegSnapshot",
    "PositionSnapshot",
    "SessionHealth",
    "SessionState",
    "TimelineCategory",
    "TimelineEvent",
    "RichDashboardRenderer",
    "PlainTextDashboardRenderer",
    "DashboardState",
    "dashboard_state",
    "DashboardEventBus",
    "event_bus",
    "DashboardControlServer",
    "control_server",
]
