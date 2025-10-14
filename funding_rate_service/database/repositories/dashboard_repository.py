"""
Dashboard repository for persisting session snapshots and timeline events.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any, Dict, Optional
from uuid import UUID

from databases import Database


class DashboardRepository:
    """
    High-level data access layer for dashboard persistence.

    This repository keeps trading sessions, aggregated snapshots, and timeline
    events in sync so the dashboard can be replayed or resumed after downtime.
    """

    def __init__(self, db: Database):
        self._db = db

    # --------------------------------------------------------------------- #
    # Session lifecycle                                                     #
    # --------------------------------------------------------------------- #

    async def upsert_session(
        self,
        *,
        session_id: UUID,
        strategy: str,
        config_path: Optional[str],
        started_at: datetime,
        health: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Insert a new session or update metadata if it already exists.
        """
        query = """
            INSERT INTO dashboard_sessions (
                session_id,
                strategy,
                config_path,
                started_at,
                health,
                metadata
            )
            VALUES (:session_id, :strategy, :config_path, :started_at, :health, :metadata)
            ON CONFLICT (session_id) DO UPDATE
            SET
                strategy = EXCLUDED.strategy,
                config_path = EXCLUDED.config_path,
                started_at = EXCLUDED.started_at,
                health = EXCLUDED.health,
                metadata = COALESCE(dashboard_sessions.metadata, '{}'::jsonb) || EXCLUDED.metadata
        """
        await self._db.execute(
            query,
            {
                "session_id": session_id,
                "strategy": strategy,
                "config_path": config_path,
                "started_at": self._to_naive_utc(started_at),
                "health": health,
                "metadata": json.dumps(metadata or {}, default=str),
            },
        )

    async def mark_session_ended(
        self,
        *,
        session_id: UUID,
        ended_at: datetime,
        health: str,
    ) -> None:
        """
        Update the session row when the bot completes or terminates.
        """
        query = """
            UPDATE dashboard_sessions
            SET ended_at = :ended_at,
                health = :health
            WHERE session_id = :session_id
        """
        await self._db.execute(
            query,
            {
                "session_id": session_id,
                "ended_at": self._to_naive_utc(ended_at),
                "health": health,
            },
        )

    # --------------------------------------------------------------------- #
    # Snapshots                                                             #
    # --------------------------------------------------------------------- #

    async def insert_snapshot(
        self,
        *,
        session_id: UUID,
        generated_at: datetime,
        payload: Dict[str, Any],
    ) -> int:
        """
        Persist a dashboard snapshot payload and return the new row id.
        """
        query = """
            INSERT INTO dashboard_snapshots (
                session_id,
                generated_at,
                payload
            )
            VALUES (:session_id, :generated_at, :payload)
            RETURNING id
        """
        return await self._db.fetch_val(
            query,
            {
                "session_id": session_id,
                "generated_at": self._to_naive_utc(generated_at),
                "payload": json.dumps(payload, default=str),
            },
        )

    async def prune_snapshots(
        self,
        *,
        session_id: UUID,
        retain: int,
    ) -> None:
        """
        Keep storage bounded by deleting snapshots beyond the most recent `retain`.
        """
        if retain <= 0:
            return

        query = """
            DELETE FROM dashboard_snapshots
            WHERE id IN (
                SELECT id
                FROM dashboard_snapshots
                WHERE session_id = :session_id
                ORDER BY generated_at DESC
                OFFSET :retain
            )
        """
        await self._db.execute(query, {"session_id": session_id, "retain": retain})

    # --------------------------------------------------------------------- #
    # Events                                                                #
    # --------------------------------------------------------------------- #

    async def insert_event(
        self,
        *,
        session_id: UUID,
        ts: datetime,
        category: str,
        message: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        """
        Persist a timeline event associated with the dashboard session.
        """
        query = """
            INSERT INTO dashboard_events (
                session_id,
                ts,
                category,
                message,
                metadata
            )
            VALUES (:session_id, :ts, :category, :message, :metadata)
            RETURNING id
        """
        return await self._db.fetch_val(
            query,
            {
                "session_id": session_id,
                "ts": self._to_naive_utc(ts),
                "category": category,
                "message": message,
                "metadata": json.dumps(metadata or {}, default=str),
            },
        )

    async def prune_events(
        self,
        *,
        session_id: UUID,
        retain: int,
    ) -> None:
        """
        Keep only the most recent `retain` events.
        """
        if retain <= 0:
            return

        query = """
            DELETE FROM dashboard_events
            WHERE id IN (
                SELECT id
                FROM dashboard_events
                WHERE session_id = :session_id
                ORDER BY ts DESC
                OFFSET :retain
            )
        """
        await self._db.execute(query, {"session_id": session_id, "retain": retain})
    @staticmethod
    def _to_naive_utc(dt: Optional[datetime]) -> Optional[datetime]:
        if dt is None:
            return None
        if dt.tzinfo is None:
            return dt
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
