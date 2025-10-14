"""
In-process dashboard state cache.

Maintains the latest snapshot, session metadata, and a rolling window of
timeline events. This serves as the authoritative live view while the database
remains the source of historical truth.
"""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime
from typing import Deque, Dict, List, Optional

from dashboard.models import DashboardSnapshot, SessionState, TimelineEvent


class DashboardState:
    """Mutable cache for live dashboard data."""

    def __init__(self, *, max_events: int = 100) -> None:
        self._lock = asyncio.Lock()
        self._latest_snapshot: Optional[DashboardSnapshot] = None
        self._session_state: Optional[SessionState] = None
        self._events: Deque[TimelineEvent] = deque(maxlen=max_events)
        self._last_updated: Optional[datetime] = None

    async def update_snapshot(self, snapshot: DashboardSnapshot) -> None:
        """Store the latest snapshot (deep copy handled by caller)."""
        async with self._lock:
            self._session_state = snapshot.session
            self._latest_snapshot = snapshot
            self._last_updated = snapshot.generated_at

    async def add_event(self, event: TimelineEvent) -> None:
        """Append an event to the rolling buffer."""
        async with self._lock:
            self._events.append(event)

    async def set_session_state(self, session: SessionState) -> None:
        """Persist session metadata even before the first snapshot arrives."""
        async with self._lock:
            self._session_state = session

    async def clear(self) -> None:
        """Reset cached data (used on shutdown or test setup)."""
        async with self._lock:
            self._latest_snapshot = None
            self._session_state = None
            self._events.clear()
            self._last_updated = None

    async def get_state(self) -> Dict[str, Optional[object]]:
        """Return copies of the current state for external consumers."""
        async with self._lock:
            snapshot = (
                self._latest_snapshot.model_copy(deep=True)
                if self._latest_snapshot
                else None
            )
            session = (
                self._session_state.model_copy(deep=True)
                if self._session_state
                else None
            )
            events = [event.model_copy(deep=True) for event in self._events]
            return {
                "session": session,
                "snapshot": snapshot,
                "events": events,
                "last_updated": self._last_updated,
            }

    async def get_snapshot(self) -> Optional[DashboardSnapshot]:
        async with self._lock:
            if self._latest_snapshot is None:
                return None
            return self._latest_snapshot.model_copy(deep=True)

    async def get_events(self) -> List[TimelineEvent]:
        async with self._lock:
            return [event.model_copy(deep=True) for event in self._events]


dashboard_state = DashboardState()
