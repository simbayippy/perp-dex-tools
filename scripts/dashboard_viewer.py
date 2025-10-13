#!/usr/bin/env python3
"""
Standalone dashboard viewer.

Fetches the most recent dashboard snapshot from PostgreSQL and renders a static
summary in the terminal. Useful when the trading bot is running with the in-process
dashboard disabled.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional
from uuid import UUID

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dashboard.models import DashboardSnapshot, TimelineCategory, TimelineEvent  # noqa: E402
from funding_rate_service.database.connection import database  # noqa: E402


console = Console()


async def fetch_latest_session(session_id: Optional[str]) -> Optional[dict]:
    row = None
    if session_id:
        query = """
            SELECT session_id, strategy, config_path, started_at, health
            FROM dashboard_sessions
            WHERE session_id = :session_id
        """
        row = await database.fetch_one(query, {"session_id": UUID(session_id)})
    else:
        query = """
            SELECT session_id, strategy, config_path, started_at, health
            FROM dashboard_sessions
            ORDER BY started_at DESC
            LIMIT 1
        """
        row = await database.fetch_one(query)
    return dict(row) if row else None


async def fetch_latest_snapshot(session_id: UUID) -> Optional[dict]:
    query = """
        SELECT payload, generated_at
        FROM dashboard_snapshots
        WHERE session_id = :session_id
        ORDER BY generated_at DESC
        LIMIT 1
    """
    row = await database.fetch_one(query, {"session_id": session_id})
    return dict(row) if row else None


async def fetch_recent_events(session_id: UUID, limit: int = 10) -> list[TimelineEvent]:
    query = """
        SELECT ts, category, message, metadata
        FROM dashboard_events
        WHERE session_id = :session_id
        ORDER BY ts DESC
        LIMIT :limit
    """
    rows = await database.fetch_all(query, {"session_id": session_id, "limit": limit})
    events: list[TimelineEvent] = []
    for row in rows:
        row = dict(row)
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


def parse_snapshot(payload) -> DashboardSnapshot:
    if isinstance(payload, bytes):
        data = json.loads(payload.decode("utf-8"))
    elif isinstance(payload, str):
        data = json.loads(payload)
    elif isinstance(payload, dict):
        data = payload
    else:
        raise ValueError("Unsupported payload type for snapshot")
    return DashboardSnapshot.model_validate(data)


def format_decimal(value) -> str:
    if value is None:
        return "—"
    return f"{value:.2f}"


def format_percentage(value) -> str:
    if value is None:
        return "—"
    return f"{value:.2f}%"


def render_snapshot(session_row: dict, snapshot: DashboardSnapshot, events: list[TimelineEvent]) -> None:
    header_table = Table.grid(expand=True)
    header_table.add_column(justify="left")
    header_table.add_column(justify="right")
    config_path = session_row.get("config_path") or snapshot.session.config_path or "N/A"
    header_table.add_row(
        f"[bold cyan]{snapshot.session.strategy}[/] — {config_path}",
        f"Session: [bold]{snapshot.session.session_id}[/]",
    )
    header_table.add_row(
        f"Stage: [bold yellow]{snapshot.session.lifecycle_stage.value}[/]",
        f"Health: [bold green]{snapshot.session.health.value}[/]",
    )
    uptime = snapshot.generated_at - snapshot.session.started_at
    header_table.add_row(
        f"Started: {snapshot.session.started_at.astimezone().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Uptime: {format_timedelta(uptime)}",
    )
    console.print(Panel(header_table, title="Session Overview", border_style="cyan"))

    positions_table = Table(title="Active Positions", show_lines=True, expand=True)
    positions_table.add_column("Symbol")
    positions_table.add_column("Lifecycle")
    positions_table.add_column("Notional USD", justify="right")
    positions_table.add_column("Unrealized PnL", justify="right")
    positions_table.add_column("Funding", justify="right")
    positions_table.add_column("Erosion", justify="right")

    if snapshot.positions:
        for position in snapshot.positions:
            erosion = format_percentage(position.profit_erosion_pct)
            positions_table.add_row(
                position.symbol,
                position.lifecycle_stage.value.replace("_", " "),
                format_decimal(position.notional_exposure_usd),
                format_decimal(position.unrealized_pnl),
                format_decimal(position.funding_accrued),
                erosion,
            )
    else:
        positions_table.add_row("—", "—", "0.00", "0.00", "0.00", "—")

    funding_table = Table(title="Funding Summary", expand=True)
    funding_table.add_column("Venue")
    funding_table.add_column("Rate (bps)", justify="right")
    funding_table.add_column("Accrued", justify="right")
    funding_table.add_column("Next Event", justify="right")

    for rate in snapshot.funding.rates:
        rate_bps = rate.current_rate * 10000
        next_time = (
            rate.next_funding_time.astimezone().strftime("%H:%M:%S")
            if rate.next_funding_time
            else "—"
        )
        funding_table.add_row(
            rate.venue.upper(),
            f"{rate_bps:.2f}",
            format_decimal(rate.accrued_since_open),
            next_time,
        )
    funding_table.add_row(
        "[bold]Total[/]",
        "—",
        format_decimal(snapshot.funding.total_accrued),
        "—",
    )

    console.print(positions_table)
    console.print(funding_table)

    events_table = Table(title="Recent Events", expand=True)
    events_table.add_column("Time")
    events_table.add_column("Category")
    events_table.add_column("Message")
    if events:
        for event in events:
            events_table.add_row(
                event.ts.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
                event.category.value,
                event.message,
            )
    else:
        events_table.add_row("—", "—", "No events recorded")
    console.print(events_table)


def format_timedelta(delta) -> str:
    total_seconds = int(delta.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


async def main(args: argparse.Namespace) -> None:
    await database.connect()
    try:
        session_row = await fetch_latest_session(args.session_id)
        if not session_row:
            console.print("[red]No dashboard sessions found.[/]")
            return

        session_id = session_row["session_id"]
        snapshot_row = await fetch_latest_snapshot(session_id)
        if not snapshot_row:
            console.print("[yellow]No snapshots captured yet for this session.[/]")
            return

        snapshot = parse_snapshot(snapshot_row["payload"])
        events = await fetch_recent_events(session_id, args.events)
        render_snapshot(session_row, snapshot, events)
    finally:
        await database.disconnect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="View the latest dashboard snapshot.")
    parser.add_argument(
        "--session-id",
        type=str,
        help="Optional session UUID to inspect (defaults to the most recent session).",
    )
    parser.add_argument(
        "--events",
        type=int,
        default=10,
        help="Number of recent events to display (default: 10).",
    )
    asyncio.run(main(parser.parse_args()))
