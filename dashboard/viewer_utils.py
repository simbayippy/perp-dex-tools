"""
Shared utilities for loading and rendering dashboard snapshots.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Iterable, List, Optional, Tuple
from uuid import UUID

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.table import Table

from dashboard.models import DashboardSnapshot, TimelineEvent
from funding_rate_service.database.connection import database


def _row_to_dict(row) -> Optional[dict]:
    return dict(row) if row is not None else None


async def fetch_latest_session(session_id: Optional[str]) -> Optional[dict]:
    if session_id:
        query = """
            SELECT session_id, strategy, config_path, started_at, health
            FROM dashboard_sessions
            WHERE session_id = :session_id
        """
        row = await database.fetch_one(query, {"session_id": UUID(session_id)})
        return _row_to_dict(row)

    query = """
        SELECT session_id, strategy, config_path, started_at, health
        FROM dashboard_sessions
        ORDER BY started_at DESC
        LIMIT 1
    """
    row = await database.fetch_one(query)
    return _row_to_dict(row)


def parse_snapshot_payload(payload) -> DashboardSnapshot:
    if isinstance(payload, bytes):
        data = json.loads(payload.decode("utf-8"))
    elif isinstance(payload, str):
        data = json.loads(payload)
    elif isinstance(payload, dict):
        data = payload
    else:
        raise ValueError("Unsupported snapshot payload type")
    return DashboardSnapshot.model_validate(data)


async def fetch_latest_snapshot(session_id: UUID) -> Optional[Tuple[DashboardSnapshot, datetime]]:
    query = """
        SELECT payload, generated_at
        FROM dashboard_snapshots
        WHERE session_id = :session_id
        ORDER BY generated_at DESC
        LIMIT 1
    """
    row = await database.fetch_one(query, {"session_id": session_id})
    if not row:
        return None
    snapshot = parse_snapshot_payload(row["payload"])
    return snapshot, row["generated_at"]


async def fetch_recent_events(session_id: UUID, limit: int = 10) -> List[TimelineEvent]:
    query = """
        SELECT ts, category, message, metadata
        FROM dashboard_events
        WHERE session_id = :session_id
        ORDER BY ts DESC
        LIMIT :limit
    """
    rows = await database.fetch_all(query, {"session_id": session_id, "limit": limit})
    events: List[TimelineEvent] = []
    for row in rows:
        metadata = row["metadata"]
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        events.append(
            TimelineEvent.model_validate(
                {
                    "ts": row["ts"],
                    "category": row["category"],
                    "message": row["message"],
                    "metadata": metadata or {},
                }
            )
        )
    return events


async def load_dashboard_state(
    session_id: Optional[str],
    events_limit: int = 10,
) -> Optional[Tuple[dict, Optional[DashboardSnapshot], List[TimelineEvent]]]:

    session_row = await fetch_latest_session(session_id)
    if not session_row:
        return None

    snapshot_result = await fetch_latest_snapshot(session_row["session_id"])
    if not snapshot_result:
        return session_row, None, []

    snapshot, _generated_at = snapshot_result
    events = await fetch_recent_events(session_row["session_id"], events_limit)
    return session_row, snapshot, events


def render_dashboard(
    session_row: dict,
    snapshot: DashboardSnapshot,
    events: Iterable[TimelineEvent],
) -> RenderableType:

    header = _build_header_panel(session_row, snapshot)
    positions = _build_positions_table(snapshot)
    funding = _build_funding_table(snapshot)
    events_table = _build_events_table(events)
    return Group(header, positions, funding, events_table)


def _build_header_panel(session_row: dict, snapshot: DashboardSnapshot) -> Panel:
    table = Table.grid(expand=True)
    table.add_column(justify="left")
    table.add_column(justify="right")
    config_path = session_row.get("config_path") or snapshot.session.config_path or "N/A"
    table.add_row(
        f"[bold cyan]{snapshot.session.strategy}[/] — {config_path}",
        f"Session: [bold]{snapshot.session.session_id}[/]",
    )
    table.add_row(
        f"Stage: [bold yellow]{snapshot.session.lifecycle_stage.value}[/]",
        f"Health: [bold green]{snapshot.session.health.value}[/]",
    )
    uptime = snapshot.generated_at - snapshot.session.started_at
    table.add_row(
        f"Started: {snapshot.session.started_at.astimezone().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Uptime: {_format_timedelta(uptime)}",
    )
    return Panel(table, title="Session Overview", border_style="cyan")


def _build_positions_table(snapshot: DashboardSnapshot) -> Table:
    table = Table(title="Active Positions", show_lines=True, expand=True)
    table.add_column("Symbol")
    table.add_column("Lifecycle")
    table.add_column("Notional USD", justify="right")
    table.add_column("Unrealized PnL", justify="right")
    table.add_column("Funding", justify="right")
    table.add_column("Erosion", justify="right")

    if snapshot.positions:
        for position in snapshot.positions:
            table.add_row(
                position.symbol,
                position.lifecycle_stage.value.replace("_", " "),
                _format_decimal(position.notional_exposure_usd),
                _format_decimal(position.unrealized_pnl),
                _format_decimal(position.funding_accrued),
                _format_percentage(position.profit_erosion_pct),
            )
    else:
        table.add_row("—", "—", "0.00", "0.00", "0.00", "—")
    return table


def _build_funding_table(snapshot: DashboardSnapshot) -> Table:
    table = Table(title="Funding Summary", expand=True)
    table.add_column("Venue")
    table.add_column("Rate (bps)", justify="right")
    table.add_column("Accrued", justify="right")
    table.add_column("Next Event", justify="right")

    for rate in snapshot.funding.rates:
        rate_bps = rate.current_rate * 10000
        next_time = (
            rate.next_funding_time.astimezone().strftime("%H:%M:%S")
            if rate.next_funding_time
            else "—"
        )
        table.add_row(
            rate.venue.upper(),
            f"{rate_bps:.2f}",
            _format_decimal(rate.accrued_since_open),
            next_time,
        )
    table.add_row(
        "[bold]Total[/]",
        "—",
        _format_decimal(snapshot.funding.total_accrued),
        "—",
    )
    return table


def _build_events_table(events: Iterable[TimelineEvent]) -> Table:
    table = Table(title="Recent Events", expand=True)
    table.add_column("Time")
    table.add_column("Category")
    table.add_column("Message")

    has_events = False
    for event in events:
        has_events = True
        table.add_row(
            event.ts.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
            event.category.value,
            event.message,
        )

    if not has_events:
        table.add_row("—", "—", "No events recorded")
    return table


def _format_decimal(value) -> str:
    if value is None:
        return "—"
    return f"{value:.2f}"


def _format_percentage(value) -> str:
    if value is None:
        return "—"
    return f"{value:.2f}%"


def _format_timedelta(delta) -> str:
    total_seconds = int(delta.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
