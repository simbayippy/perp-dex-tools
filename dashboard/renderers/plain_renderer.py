"""
Plain-text fallback renderer for dashboard snapshots.
"""

from __future__ import annotations

from typing import Optional

from rich.console import Console

from dashboard.models import DashboardSnapshot, SessionState


class PlainTextDashboardRenderer:
    """Minimal renderer that logs concise snapshot summaries."""

    def __init__(self, *, console: Optional[Console] = None):
        self._console = console or Console()
        self._enabled = self._console.is_terminal

    async def start(self, session: SessionState) -> None:
        if not self._enabled:
            return
        self._console.print(
            f"[bold cyan]Dashboard activated[/] — strategy={session.strategy}, session={session.session_id}"
        )

    async def render(self, snapshot: DashboardSnapshot) -> None:
        if not self._enabled:
            return

        portfolio = snapshot.portfolio
        self._console.print(
            f"[dashboard] {snapshot.generated_at:%H:%M:%S} | stage={snapshot.session.lifecycle_stage.value} | "
            f"positions={portfolio.total_positions} | notional={portfolio.total_notional_usd:.2f} | "
            f"unrealized={portfolio.net_unrealized_pnl:.2f} | funding={portfolio.funding_accrued:.2f}"
        )

    async def stop(self) -> None:
        if not self._enabled:
            return
        self._console.print("[bold cyan]Dashboard stopped[/]")
