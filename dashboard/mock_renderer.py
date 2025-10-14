"""Simple Rich-based renderer prototype for dashboard snapshots."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

from dashboard import (
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


console = Console()


def _build_layout(snapshot: DashboardSnapshot) -> Layout:
    layout = Layout()
    layout.split(
        Layout(name="header", size=5),
        Layout(name="body"),
        Layout(name="footer", size=8),
    )

    layout["body"].split_row(
        Layout(name="positions"),
        Layout(name="funding", ratio=2),
    )

    layout["header"].update(_render_header(snapshot))
    layout["positions"].update(_render_positions(snapshot))
    layout["funding"].update(_render_funding(snapshot))
    layout["footer"].update(_render_events(snapshot))
    return layout


def _render_header(snapshot: DashboardSnapshot) -> Panel:
    session = snapshot.session
    table = Table.grid(expand=True)
    table.add_column(justify="left")
    table.add_column(justify="right")
    table.add_row(
        f"[bold cyan]{session.strategy}[/] — {session.config_path or 'N/A'}",
        f"Session: [bold]{session.session_id}[/]",
    )
    table.add_row(
        f"Stage: [bold yellow]{session.lifecycle_stage.value}[/]",
        f"Health: [bold green]{session.health.value}[/]",
    )
    uptime = snapshot.generated_at - session.started_at
    table.add_row(
        f"Started: {session.started_at.isoformat()}",
        f"Uptime: {uptime}" ,
    )
    return Panel(table, title="Session Overview", border_style="cyan")


def _render_positions(snapshot: DashboardSnapshot) -> Panel:
    table = Table(expand=True, show_lines=True, title="Active Positions")
    table.add_column("Symbol")
    table.add_column("Lifecycle")
    table.add_column("Notional USD", justify="right")
    table.add_column("Unrealized PnL", justify="right")
    table.add_column("Funding Accrued", justify="right")

    for position in snapshot.positions:
        table.add_row(
            position.symbol,
            position.lifecycle_stage.value,
            f"{position.notional_exposure_usd:.2f}",
            f"{position.unrealized_pnl:.2f}",
            f"{position.funding_accrued:.2f}",
        )

    if not snapshot.positions:
        table.add_row("–", "–", "0.00", "0.00", "0.00")

    return Panel(table, border_style="green")


def _render_funding(snapshot: DashboardSnapshot) -> Panel:
    funding = snapshot.funding
    table = Table(expand=True, title="Funding Summary", show_header=True)
    table.add_column("Venue")
    table.add_column("Rate (bps)", justify="right")
    table.add_column("Accrued", justify="right")
    table.add_column("Next", justify="right")

    for rate in funding.rates:
        bps = rate.current_rate * Decimal("10000")
        table.add_row(
            rate.venue.upper(),
            f"{bps:.2f}",
            f"{rate.accrued_since_open:.2f}",
            rate.next_funding_time.isoformat() if rate.next_funding_time else "–",
        )

    summary = Table.grid(padding=(0, 1))
    summary.add_row(f"Total funding: [bold]{funding.total_accrued:.2f}[/]")
    if funding.weighted_average_rate is not None:
        pct = funding.weighted_average_rate * Decimal("100")
        summary.add_row(f"Weighted rate: {pct:.4f}%")
    if funding.next_event_countdown_seconds is not None:
        summary.add_row(f"Next event in: {funding.next_event_countdown_seconds}s")

    content = Group(table, summary)
    return Panel(content, border_style="magenta")


def _render_events(snapshot: DashboardSnapshot) -> Panel:
    table = Table(expand=True, title="Recent Events")
    table.add_column("Time")
    table.add_column("Category")
    table.add_column("Message")
    for event in snapshot.recent_events:
        table.add_row(
            event.ts.strftime("%H:%M:%S"),
            event.category.value,
            event.message,
        )
    return Panel(table, border_style="blue")


async def main() -> None:
    session_id = uuid4()
    snapshot = DashboardSnapshot(
        session=SessionState(
            session_id=session_id,
            strategy="funding_arbitrage",
            config_path="configs/real_funding_test.yml",
            started_at=datetime.now(timezone.utc),
            last_heartbeat=datetime.now(timezone.utc),
            health=SessionHealth.RUNNING,
            lifecycle_stage=LifecycleStage.MONITORING,
            max_positions=1,
            max_total_exposure_usd=Decimal("100.0"),
            dry_run=False,
            metadata={"exchanges": ["lighter", "aster"]},
        ),
        positions=[
            PositionSnapshot(
                position_id=uuid4(),
                symbol="ZORA",
                strategy_tag="funding_arbitrage",
                opened_at=datetime.now(timezone.utc),
                last_update=datetime.now(timezone.utc),
                lifecycle_stage=LifecycleStage.MONITORING,
                legs=[
                    PositionLegSnapshot(
                        venue="lighter",
                        side="long",
                        quantity=Decimal("105.5"),
                        exposure_usd=Decimal("10.0"),
                        entry_price=Decimal("0.094"),
                        mark_price=Decimal("0.095"),
                        leverage=Decimal("3"),
                        realized_pnl=Decimal("0"),
                        fees_paid=Decimal("0.01"),
                        funding_accrued=Decimal("0.02"),
                        margin_reserved=Decimal("3.33"),
                        last_updated=datetime.now(timezone.utc),
                    ),
                    PositionLegSnapshot(
                        venue="aster",
                        side="short",
                        quantity=Decimal("106.1"),
                        exposure_usd=Decimal("10.0"),
                        entry_price=Decimal("0.094"),
                        mark_price=Decimal("0.0937"),
                        leverage=Decimal("3"),
                        realized_pnl=Decimal("0"),
                        fees_paid=Decimal("0.01"),
                        funding_accrued=Decimal("0.03"),
                        margin_reserved=Decimal("3.33"),
                        last_updated=datetime.now(timezone.utc),
                    ),
                ],
                notional_exposure_usd=Decimal("10.0"),
                entry_divergence_pct=Decimal("0.00367"),
                current_divergence_pct=Decimal("0.002"),
                profit_erosion_pct=Decimal("0.45"),
                unrealized_pnl=Decimal("0.12"),
                realized_pnl=Decimal("0.0"),
                funding_accrued=Decimal("0.05"),
                rebalance_pending=False,
                max_position_age_seconds=86400,
                custom_metadata={"notes": "Sample position"},
            )
        ],
        portfolio=PortfolioSnapshot(
            total_positions=1,
            total_notional_usd=Decimal("10.0"),
            net_unrealized_pnl=Decimal("0.12"),
            net_realized_pnl=Decimal("0"),
            funding_accrued=Decimal("0.05"),
            free_collateral_usd=Decimal("20"),
            maintenance_margin_ratio=Decimal("0.15"),
            alerts=["PnL approaching target"],
        ),
        funding=FundingSnapshot(
            total_accrued=Decimal("0.05"),
            weighted_average_rate=Decimal("0.0005"),
            next_event_countdown_seconds=1800,
            rates=[
                FundingRateSnapshot(
                    venue="lighter",
                    symbol="ZORA",
                    current_rate=Decimal("0.0004"),
                    next_rate=Decimal("0.0005"),
                    next_funding_time=datetime.now(timezone.utc),
                    accrued_since_open=Decimal("0.02"),
                    last_updated=datetime.now(timezone.utc),
                ),
                FundingRateSnapshot(
                    venue="aster",
                    symbol="ZORA",
                    current_rate=Decimal("0.0006"),
                    next_rate=Decimal("0.0007"),
                    next_funding_time=datetime.now(timezone.utc),
                    accrued_since_open=Decimal("0.03"),
                    last_updated=datetime.now(timezone.utc),
                ),
            ],
        ),
        recent_events=[
            TimelineEvent(
                ts=datetime.now(timezone.utc),
                category=TimelineCategory.STAGE,
                message="Monitoring active position",
            ),
            TimelineEvent(
                ts=datetime.now(timezone.utc),
                category=TimelineCategory.FUNDING,
                message="Collected funding payment",
            ),
        ],
        generated_at=datetime.now(timezone.utc),
    )

    async def _ticker() -> None:
        for i in range(10):
            await asyncio.sleep(1)
            snapshot.session.last_heartbeat = datetime.now(timezone.utc)
            snapshot.generated_at = datetime.now(timezone.utc)
            snapshot.positions[0].unrealized_pnl += Decimal("0.01")
            snapshot.positions[0].funding_accrued += Decimal("0.005")
            snapshot.funding.total_accrued += Decimal("0.005")
            snapshot.recent_events.insert(0, TimelineEvent(
                ts=datetime.now(timezone.utc),
                category=TimelineCategory.INFO,
                message=f"Heartbeat {i}",
            ))
            snapshot.recent_events = snapshot.recent_events[:5]
            yield snapshot

    async def _render() -> None:
        async for updated in _ticker():
            layout = _build_layout(updated)
            live.update(layout)

    layout = _build_layout(snapshot)
    with Live(layout, console=console, refresh_per_second=4, screen=False) as live:
        await _render()


if __name__ == "__main__":
    asyncio.run(main())
