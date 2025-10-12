"""
Rich-based terminal renderer for dashboard snapshots.

This renderer keeps a Rich Live layout on screen and updates it whenever the
dashboard service publishes a new snapshot.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

from dashboard.models import DashboardSnapshot, SessionState


class RichDashboardRenderer:
    """Render dashboard snapshots using Rich Live layout."""

    def __init__(
        self,
        *,
        refresh_interval_seconds: float = 1.0,
        max_events: int = 12,
        console: Optional[Console] = None,
    ):
        self._refresh_interval = max(refresh_interval_seconds, 0.1)
        self._refresh_per_second = max(int(1 / self._refresh_interval), 1)
        self._max_events = max_events
        self._console = console or Console()
        self._live: Optional[Live] = None
        self._enabled = self._console.is_terminal
        self._lock = asyncio.Lock()
        self._session_state: Optional[SessionState] = None

    async def start(self, session: SessionState) -> None:
        async with self._lock:
            self._session_state = session
            if not self._enabled:
                self._console.print(
                    "[yellow]Terminal dashboard disabled (stdout is not a TTY).[/]"
                )
                return

            baseline_layout = self._build_layout(session, None)
            self._live = Live(
                baseline_layout,
                console=self._console,
                refresh_per_second=self._refresh_per_second,
                screen=False,
            )
            self._live.start()

    async def render(self, snapshot: DashboardSnapshot) -> None:
        async with self._lock:
            if not self._enabled or not self._live:
                return

            layout = self._build_layout(snapshot.session, snapshot)
            self._live.update(layout, refresh=True)

    async def stop(self) -> None:
        async with self._lock:
            if self._live:
                self._live.stop()
                self._live = None

    # ------------------------------------------------------------------ #
    # Layout helpers                                                     #
    # ------------------------------------------------------------------ #

    def _build_layout(
        self, session: SessionState, snapshot: Optional[DashboardSnapshot]
    ) -> Layout:
        layout = Layout()
        layout.split(
            Layout(name="header", size=5),
            Layout(name="body"),
            Layout(name="footer", size=10),
        )
        layout["body"].split_row(
            Layout(name="positions", ratio=2),
            Layout(name="funding", ratio=2),
        )

        layout["header"].update(self._render_header(session, snapshot))
        layout["positions"].update(self._render_positions(snapshot))
        layout["funding"].update(self._render_funding(snapshot))
        layout["footer"].update(self._render_events(snapshot))
        return layout

    def _render_header(
        self, session: SessionState, snapshot: Optional[DashboardSnapshot]
    ) -> Panel:
        table = Table.grid(expand=True)
        table.add_column(justify="left")
        table.add_column(justify="right")

        config_path = session.config_path or "N/A"
        table.add_row(
            f"[bold cyan]{session.strategy}[/] — {config_path}",
            f"Session: [bold]{session.session_id}[/]",
        )
        table.add_row(
            f"Stage: [bold yellow]{session.lifecycle_stage.value}[/]",
            f"Health: [bold green]{session.health.value}[/]",
        )

        generated_at = snapshot.generated_at if snapshot else datetime.now(timezone.utc)
        uptime = generated_at - session.started_at
        table.add_row(
            f"Started: {session.started_at.astimezone().strftime('%Y-%m-%d %H:%M:%S')}",
            f"Uptime: {self._format_timedelta(uptime)}",
        )

        return Panel(table, title="Session Overview", border_style="cyan")

    def _render_positions(self, snapshot: Optional[DashboardSnapshot]) -> Panel:
        table = Table(expand=True, show_lines=True, title="Active Positions")
        table.add_column("Symbol")
        table.add_column("Lifecycle")
        table.add_column("Notional USD", justify="right")
        table.add_column("Unrealized PnL", justify="right")
        table.add_column("Funding", justify="right")
        table.add_column("Erosion", justify="right")

        if snapshot and snapshot.positions:
            for position in snapshot.positions:
                erosion = (
                    f"{position.profit_erosion_pct:.2f}%"
                    if position.profit_erosion_pct is not None
                    else "—"
                )
                table.add_row(
                    position.symbol,
                    position.lifecycle_stage.value.replace("_", " "),
                    self._format_decimal(position.notional_exposure_usd),
                    self._format_decimal(position.unrealized_pnl),
                    self._format_decimal(position.funding_accrued),
                    erosion,
                )
        else:
            table.add_row("—", "—", "0.00", "0.00", "0.00", "—")

        return Panel(table, border_style="green")

    def _render_funding(self, snapshot: Optional[DashboardSnapshot]) -> Panel:
        table = Table(expand=True, title="Funding Summary")
        table.add_column("Venue")
        table.add_column("Rate (bps)", justify="right")
        table.add_column("Accrued", justify="right")
        table.add_column("Next Event", justify="right")

        total_accrued = Decimal("0")
        weighted_rate = None
        next_event = None

        if snapshot:
            total_accrued = snapshot.funding.total_accrued
            weighted_rate = snapshot.funding.weighted_average_rate
            next_event = snapshot.funding.next_event_countdown_seconds

            for rate in snapshot.funding.rates:
                rate_bps = rate.current_rate * Decimal("10000")
                next_time = (
                    rate.next_funding_time.astimezone().strftime("%H:%M:%S")
                    if rate.next_funding_time
                    else "—"
                )
                table.add_row(
                    rate.venue.upper(),
                    f"{rate_bps:.2f}",
                    self._format_decimal(rate.accrued_since_open),
                    next_time,
                )

        summary = Table.grid(padding=(0, 1))
        summary.add_row(f"Total funding: [bold]{self._format_decimal(total_accrued)}[/]")
        if weighted_rate is not None:
            summary.add_row(
                f"Weighted rate: {weighted_rate * Decimal('100'):.4f}%"
            )
        if next_event is not None:
            summary.add_row(f"Next event in: {int(next_event)}s")

        content = Group(table, summary)
        return Panel(content, border_style="magenta")

    def _render_events(self, snapshot: Optional[DashboardSnapshot]) -> Panel:
        table = Table(expand=True, title="Recent Events")
        table.add_column("Time")
        table.add_column("Category")
        table.add_column("Message")

        events = snapshot.recent_events if snapshot else []
        if events:
            for event in events[: self._max_events]:
                table.add_row(
                    event.ts.astimezone().strftime("%H:%M:%S"),
                    event.category.value,
                    event.message,
                )
        else:
            table.add_row("—", "—", "No recent events")

        return Panel(table, border_style="blue")

    # ------------------------------------------------------------------ #
    # Utility                                                            #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _format_decimal(value: Decimal) -> str:
        return f"{value:.2f}"

    @staticmethod
    def _format_timedelta(delta) -> str:
        total_seconds = int(delta.total_seconds())
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
