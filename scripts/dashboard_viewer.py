#!/usr/bin/env python3
"""
Standalone dashboard viewer.

Fetches the most recent dashboard snapshot from PostgreSQL and renders a static
summary in the terminal. Useful when the trading bot is running with the
in-process dashboard disabled.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from rich.console import Console

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dashboard.viewer_utils import (  # noqa: E402
    load_dashboard_state,
    render_dashboard,
)
from funding_rate_service.database.connection import database  # noqa: E402


console = Console()


async def main(args: argparse.Namespace) -> None:
    await database.connect()
    try:
        state = await load_dashboard_state(args.session_id, args.events)
        if not state:
            console.print("[red]No dashboard sessions found.[/]")
            return

        session_row, snapshot, events = state
        if snapshot is None:
            console.print("[yellow]No snapshots captured yet for this session.[/]")
            return

        console.print(render_dashboard(session_row, snapshot, events))
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
