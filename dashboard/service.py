"""
Dashboard service orchestrating snapshot distribution, persistence, and rendering.
"""

from __future__ import annotations

import asyncio
from asyncio import Queue, QueueEmpty
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Protocol, TYPE_CHECKING, Union
from uuid import UUID

from dashboard.config import DashboardSettings
from dashboard.models import (
    DashboardSnapshot,
    LifecycleStage,
    SessionHealth,
    SessionState,
    TimelineEvent,
)
if TYPE_CHECKING:
    from funding_rate_service.database.repositories import DashboardRepository
else:
    DashboardRepository = Any


class DashboardRenderer(Protocol):
    """Minimal protocol for dashboard renderers."""

    async def start(self, session: SessionState) -> None: ...

    async def render(self, snapshot: DashboardSnapshot) -> None: ...

    async def stop(self) -> None: ...


@dataclass
class _SnapshotEnvelope:
    snapshot: DashboardSnapshot


@dataclass
class _EventEnvelope:
    event: TimelineEvent


class DashboardService:
    """
    Coordinates session state, persistence, and optional UI rendering.

    The service receives snapshots/events from strategies and ensures they are
    stored and dispatched without blocking trading logic.
    """

    def __init__(
        self,
        *,
        session_state: SessionState,
        settings: DashboardSettings,
        repository: Optional["DashboardRepository"] = None,
        renderer_factory: Optional[Callable[[], DashboardRenderer]] = None,
        loop: Optional[asyncio.AbstractEventLoop] = None,
    ):
        self._settings = settings
        self._enabled = settings.enabled
        self._repository = repository if settings.persist_snapshots else None
        self._renderer_factory = renderer_factory
        self._renderer: Optional[DashboardRenderer] = None
        self._loop = loop or asyncio.get_event_loop()

        now = datetime.now(timezone.utc)
        self._session_state = session_state.model_copy(
            update={"started_at": now, "last_heartbeat": now}
        )

        self._queue: "Queue[Union[_SnapshotEnvelope, _EventEnvelope]]" = Queue()
        self._worker_task: Optional[asyncio.Task] = None
        self._running = False
        self._latest_snapshot: Optional[DashboardSnapshot] = None
        self._last_persisted_at: Optional[datetime] = None

    # ------------------------------------------------------------------ #
    # Properties                                                         #
    # ------------------------------------------------------------------ #

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def session_id(self) -> UUID:
        return self._session_state.session_id

    @property
    def session_state(self) -> SessionState:
        return self._session_state

    # ------------------------------------------------------------------ #
    # Lifecycle                                                          #
    # ------------------------------------------------------------------ #

    async def start(self) -> None:
        """Initialize persistence and renderer tasks."""
        if not self._enabled or self._running:
            return

        if self._repository:
            await self._repository.upsert_session(
                session_id=self._session_state.session_id,
                strategy=self._session_state.strategy,
                config_path=self._session_state.config_path,
                started_at=self._session_state.started_at,
                health=self._session_state.health.value,
                metadata=self._session_state.metadata,
            )

        if self._renderer_factory and self._settings.renderer != "plain":
            self._renderer = self._renderer_factory()
            await self._renderer.start(self._session_state)

        self._running = True
        self._worker_task = self._loop.create_task(self._run_worker(), name="dashboard-service-worker")

    async def stop(self, *, health: SessionHealth = SessionHealth.STOPPED) -> None:
        """Stop background tasks and finalise persistence."""
        if not self._enabled:
            return

        self._running = False

        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass

        if self._renderer:
            await self._renderer.stop()
            self._renderer = None

        if self._repository:
            await self._repository.mark_session_ended(
                session_id=self._session_state.session_id,
                ended_at=datetime.now(timezone.utc),
                health=health.value,
            )

    # ------------------------------------------------------------------ #
    # Session updates                                                    #
    # ------------------------------------------------------------------ #

    def update_session(
        self,
        *,
        health: Optional[SessionHealth] = None,
        lifecycle_stage: Optional[LifecycleStage] = None,
        metadata: Optional[dict] = None,
    ) -> None:
        """Update cached session metadata."""
        if not self._enabled:
            return

        update_payload = {}
        if health:
            update_payload["health"] = health
        if lifecycle_stage:
            update_payload["lifecycle_stage"] = lifecycle_stage
        if metadata:
            new_metadata = dict(self._session_state.metadata)
            new_metadata.update(metadata)
            update_payload["metadata"] = new_metadata

        if update_payload:
            self._session_state = self._session_state.model_copy(update=update_payload)

    # ------------------------------------------------------------------ #
    # Publishing                                                         #
    # ------------------------------------------------------------------ #

    async def publish_snapshot(self, snapshot: DashboardSnapshot) -> None:
        """Queue a snapshot for persistence and rendering."""
        if not self._enabled:
            return

        heartbeat = snapshot.generated_at
        self._session_state = self._session_state.model_copy(update={"last_heartbeat": heartbeat})

        snapshot = snapshot.model_copy(update={"session": self._session_state})
        await self._queue.put(_SnapshotEnvelope(snapshot=snapshot))

    async def publish_event(self, event: TimelineEvent) -> None:
        """Queue a timeline event for persistence."""
        if not self._enabled:
            return

        await self._queue.put(_EventEnvelope(event=event))

    # ------------------------------------------------------------------ #
    # Internal worker                                                    #
    # ------------------------------------------------------------------ #

    async def _run_worker(self) -> None:
        try:
            while self._running:
                envelope = await self._queue.get()
                if isinstance(envelope, _SnapshotEnvelope):
                    await self._handle_snapshot(envelope.snapshot)
                elif isinstance(envelope, _EventEnvelope):
                    await self._handle_event(envelope.event)
        except asyncio.CancelledError:
            # Flush remaining items on cancellation
            try:
                while True:
                    item = self._queue.get_nowait()
                    if isinstance(item, _SnapshotEnvelope):
                        await self._handle_snapshot(item.snapshot)
                    elif isinstance(item, _EventEnvelope):
                        await self._handle_event(item.event)
            except QueueEmpty:
                pass
            raise
        finally:
            # Ensure renderer is updated one last time with final snapshot
            if self._renderer and self._latest_snapshot:
                await self._renderer.render(self._latest_snapshot)

    async def _handle_snapshot(self, snapshot: DashboardSnapshot) -> None:
        self._latest_snapshot = snapshot

        now = datetime.now(timezone.utc)
        should_persist = False
        if self._repository:
            if self._last_persisted_at is None:
                should_persist = True
            else:
                delta = (now - self._last_persisted_at).total_seconds()
                if delta >= self._settings.write_interval_seconds:
                    should_persist = True

        if should_persist:
            await self._repository.insert_snapshot(
                session_id=self._session_state.session_id,
                generated_at=snapshot.generated_at,
                payload=snapshot.model_dump(mode="json"),
            )
            await self._repository.prune_snapshots(
                session_id=self._session_state.session_id,
                retain=self._settings.snapshot_retention,
            )
            self._last_persisted_at = now

        if self._renderer:
            await self._renderer.render(snapshot)

    async def _handle_event(self, event: TimelineEvent) -> None:
        if self._repository:
            await self._repository.insert_event(
                session_id=self._session_state.session_id,
                ts=event.ts,
                category=event.category.value,
                message=event.message,
                metadata=event.metadata,
            )
            await self._repository.prune_events(
                session_id=self._session_state.session_id,
                retain=self._settings.event_retention,
            )

    # ------------------------------------------------------------------ #
    # Utility                                                            #
    # ------------------------------------------------------------------ #

    def get_latest_snapshot(self) -> Optional[DashboardSnapshot]:
        """Return the most recent snapshot processed by the service."""
        return self._latest_snapshot
