"""Textual TUI for inspecting live dashboard positions."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, ListItem, ListView, Static

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dashboard.models import (  # noqa: E402
    DashboardSnapshot,
    PositionLegSnapshot,
    PositionSnapshot,
    SessionState,
    TimelineEvent,
)
from dashboard.viewer_utils import load_dashboard_state  # noqa: E402
from database.connection import database  # noqa: E402


CONTROL_SERVER_HTTP = "http://127.0.0.1:8765"
CONTROL_SERVER_WS = "ws://127.0.0.1:8765/stream"


# ---------------------------------------------------------------------------
# Helpers & Store
# ---------------------------------------------------------------------------


def _format_decimal(value: Any, places: int = 2, allow_zero: bool = True) -> str:
    if value is None:
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not allow_zero and number == 0:
        return "—"
    return f"{number:,.{places}f}"


def _format_percent(value: Any, places: int = 2) -> str:
    if value is None:
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if abs(number) <= 1:
        number *= 100
    return f"{number:.{places}f}%"


def _format_datetime(value: Any) -> str:
    if value is None:
        return "—"
    try:
        return value.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except Exception:  # pragma: no cover - defensive
        return str(value)


@dataclass
class SnapshotEntry:
    session: SessionState
    snapshot: DashboardSnapshot


class DashboardStore:
    """In-memory cache of dashboard snapshots grouped by strategy."""

    def __init__(self) -> None:
        self._strategies: Dict[str, Dict[str, SnapshotEntry]] = {}

    def update(self, session: SessionState, snapshot: DashboardSnapshot) -> None:
        strategy_sessions = self._strategies.setdefault(session.strategy, {})
        strategy_sessions[str(session.session_id)] = SnapshotEntry(session=session, snapshot=snapshot)

    # --- Query helpers -------------------------------------------------

    def strategies(self) -> Dict[str, Dict[str, SnapshotEntry]]:
        return self._strategies

    def get_strategy_entries(self, strategy: str) -> List[SnapshotEntry]:
        return list(self._strategies.get(strategy, {}).values())

    def strategy_summary(self, strategy: str) -> Tuple[int, Decimal, Decimal]:
        entries = self.get_strategy_entries(strategy)
        total_positions = sum(len(entry.snapshot.positions) for entry in entries)
        total_notional = sum(
            (pos.notional_exposure_usd or Decimal("0"))
            for entry in entries
            for pos in entry.snapshot.positions
        )
        net_pnl = sum(
            (pos.unrealized_pnl or Decimal("0")) + (pos.realized_pnl or Decimal("0"))
            for entry in entries
            for pos in entry.snapshot.positions
        )
        return total_positions, total_notional, net_pnl

    def collect_positions(
        self, strategy: str, session_filter: Optional[str]
    ) -> List[Tuple[SnapshotEntry, PositionSnapshot]]:
        rows: List[Tuple[SnapshotEntry, PositionSnapshot]] = []
        for entry in self.get_strategy_entries(strategy):
            sid = str(entry.session.session_id)
            if session_filter and sid != session_filter:
                continue
            for position in entry.snapshot.positions:
                rows.append((entry, position))
        return rows

    def session_labels(self, strategy: str) -> List[Tuple[str, str]]:
        labels: List[Tuple[str, str]] = []
        for entry in self.get_strategy_entries(strategy):
            sid = str(entry.session.session_id)
            labels.append((sid, self.session_label(entry.session)))
        return labels

    @staticmethod
    def session_label(session: SessionState) -> str:
        metadata = session.metadata or {}
        for key in ("bot_name", "account", "wallet", "alias"):
            value = metadata.get(key)
            if value:
                return str(value)
        if session.config_path:
            return Path(session.config_path).stem
        return str(session.session_id)[:8]


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


class SnapshotUpdated(Message):
    def __init__(self, strategy: str) -> None:
        self.strategy = strategy
        super().__init__()


class StrategiesChanged(Message):
    pass


# ---------------------------------------------------------------------------
# Screens
# ---------------------------------------------------------------------------


class MainMenuScreen(Screen):
    BINDINGS = [Binding("q", "quit", "Quit"), Binding("escape", "quit", "Quit")]

    def __init__(self, store: DashboardStore) -> None:
        super().__init__()
        self.store = store
        self.menu: ListView | None = None
        self.notice: Static | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="menu-container"):
            yield Static("Funding Dashboard", classes="menu-title")
            self.menu = ListView(
                ListItem(Static("View Positions"), id="view_positions"),
                ListItem(Static("Exit"), id="exit_app"),
            )
            yield self.menu
            self.notice = Static("", classes="menu-notice")
            yield self.notice
        yield Footer()

    def on_mount(self) -> None:
        if self.menu:
            self.menu.focus()
        self._update_notice()

    def on_show(self) -> None:
        self._update_notice()

    def _update_notice(self) -> None:
        if not self.notice:
            return
        if self.store.strategies():
            self.notice.update("[green]Live data available.[/]")
        else:
            self.notice.update("[yellow]Waiting for live snapshots…[/]")

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        option = event.item.id
        if option == "view_positions":
            if not self.store.strategies():
                if self.notice:
                    self.notice.update("[red]No positions yet. Waiting for data…[/]")
                return
            self.app.push_screen(StrategySelectScreen(self.store))
        elif option == "exit_app":
            self.app.exit()

    async def on_strategies_changed(self, _: StrategiesChanged) -> None:
        self._update_notice()


class StrategySelectScreen(Screen):
    BINDINGS = [Binding("escape", "back", "Back"), Binding("q", "back", "Back")]

    def __init__(self, store: DashboardStore) -> None:
        super().__init__()
        self.store = store
        self.list_view: ListView | None = None
        self.status: Static | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical():
            yield Static("Select Strategy", classes="menu-title")
            self.list_view = ListView()
            yield self.list_view
            self.status = Static("", classes="menu-notice")
            yield self.status
        yield Footer()

    def on_mount(self) -> None:
        self._refresh()
        if self.list_view:
            self.list_view.focus()

    def on_show(self) -> None:
        self._refresh()

    def action_back(self) -> None:
        self.app.pop_screen()

    def _refresh(self) -> None:
        if not self.list_view or not self.status:
            return
        self.list_view.clear()
        strategies = self.store.strategies()
        if not strategies:
            self.status.update("[yellow]No strategies available yet.[/]")
            return
        for strategy, entries in strategies.items():
            total_positions, total_notional, net_pnl = self.store.strategy_summary(strategy)
            label = (
                f"{strategy}  |  positions: {total_positions}  "
                f"notional: ${_format_decimal(total_notional)}  "
                f"PnL: ${_format_decimal(net_pnl)}"
            )
            self.list_view.append(ListItem(Static(label), id=strategy))
        self.list_view.append(ListItem(Static("‹ Back"), id="back"))
        self.status.update("[grey]Enter to open, Esc to return.[/]")

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        choice = event.item.id
        if choice == "back":
            self.action_back()
            return
        if choice and choice in self.store.strategies():
            self.app.push_screen(PositionsScreen(self.store, choice))

    async def on_strategies_changed(self, _: StrategiesChanged) -> None:
        self._refresh()

    async def on_snapshot_updated(self, _: SnapshotUpdated) -> None:
        self._refresh()


class PositionsScreen(Screen):
    BINDINGS = [
        Binding("escape", "back", "Back"),
        Binding("q", "back", "Back"),
        Binding("[", "prev_session", "Prev Session"),
        Binding("]", "next_session", "Next Session"),
        Binding("enter", "view_detail", "Details"),
    ]

    def __init__(self, store: DashboardStore, strategy: str) -> None:
        super().__init__()
        self.store = store
        self.strategy = strategy
        self.table: DataTable | None = None
        self.header: Static | None = None
        self.sub_header: Static | None = None
        self.hints: Static | None = None
        self._row_map: Dict[str, Tuple[SnapshotEntry, PositionSnapshot]] = {}
        self._session_cycle: List[str] = []  # includes "__ALL__" as first entry
        self._session_index: int = 0

    @property
    def session_filter(self) -> Optional[str]:
        if not self._session_cycle:
            return None
        choice = self._session_cycle[self._session_index]
        return None if choice == "__ALL__" else choice

    def compose(self) -> ComposeResult:
        yield Header()
        self.header = Static("", classes="positions-header")
        yield self.header
        self.sub_header = Static("", classes="positions-subheader")
        yield self.sub_header
        self.table = DataTable(id="positions-table")
        self.table.add_columns(
            "Session",
            "Symbol",
            "Long",
            "Short",
            "Notional",
            "Divergence",
            "Erosion",
            "PnL",
            "Last Update",
        )
        yield self.table
        self.hints = Static("", classes="positions-hints")
        yield self.hints
        yield Footer()

    def on_mount(self) -> None:
        self._refresh_sessions()
        self._refresh_table()
        if self.table:
            self.table.focus()

    def on_show(self) -> None:
        self._refresh_sessions()
        self._refresh_table()

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_prev_session(self) -> None:
        if not self._session_cycle:
            return
        self._session_index = (self._session_index - 1) % len(self._session_cycle)
        self._refresh_table()

    def action_next_session(self) -> None:
        if not self._session_cycle:
            return
        self._session_index = (self._session_index + 1) % len(self._session_cycle)
        self._refresh_table()

    def action_view_detail(self) -> None:
        if not self.table or not self.table.row_count:
            return
        coord = self.table.cursor_coordinate
        if coord is None:
            return
        row_index = coord.row
        if row_index >= len(self.table.row_keys):
            return
        row_key = self.table.row_keys[row_index]
        entry_pos = self._row_map.get(row_key)
        if not entry_pos:
            return
        entry, position = entry_pos
        label = self.store.session_label(entry.session)
        self.app.push_screen(PositionDetailScreen(entry, position, label))

    def _refresh_sessions(self) -> None:
        labels = self.store.session_labels(self.strategy)
        self._session_cycle = ["__ALL__"] + [sid for sid, _ in labels]
        if self._session_index >= len(self._session_cycle):
            self._session_index = 0

    def _refresh_table(self) -> None:
        if not self.table or not self.header or not self.sub_header or not self.hints:
            return

        entries = self.store.get_strategy_entries(self.strategy)
        total_positions, total_notional, net_pnl = self.store.strategy_summary(self.strategy)
        self.header.update(
            f"[bold]{self.strategy}[/] — positions: {total_positions} | "
            f"notional: ${_format_decimal(total_notional)} | PnL: ${_format_decimal(net_pnl)}"
        )

        labels = dict(self.store.session_labels(self.strategy))
        if self.session_filter:
            label = labels.get(self.session_filter, self.session_filter[:8])
            self.sub_header.update(f"Session: {label}")
        else:
            self.sub_header.update(
                f"All sessions ({len(entries)}) — use [/] to cycle individual sessions"
            )

        rows = self.store.collect_positions(self.strategy, self.session_filter)
        rows.sort(key=lambda item: item[1].last_update or item[1].opened_at, reverse=True)

        self.table.clear()
        self._row_map.clear()

        for entry, position in rows:
            session_id = str(entry.session.session_id)
            row_key = f"{session_id}:{position.position_id}"
            long_leg = _get_leg(position, "long")
            short_leg = _get_leg(position, "short")
            row = [
                self.store.session_label(entry.session),
                position.symbol,
                long_leg.venue if long_leg else "?",
                short_leg.venue if short_leg else "?",
                f"${_format_decimal(position.notional_exposure_usd)}",
                _format_percent(position.current_divergence_pct),
                _format_percent(position.profit_erosion_pct),
                f"${_format_decimal((position.unrealized_pnl or 0) + (position.realized_pnl or 0))}",
                _format_datetime(position.last_update),
            ]
            self.table.add_row(*row, key=row_key)
            self._row_map[row_key] = (entry, position)

        if not rows:
            self.hints.update("[yellow]No open positions for this selection.[/]")
        else:
            self.hints.update("[,]/] cycle sessions • Enter details • q to go back")

    async def on_snapshot_updated(self, message: SnapshotUpdated) -> None:
        if message.strategy != self.strategy:
            return
        self._refresh_sessions()
        self._refresh_table()

    async def on_strategies_changed(self, _: StrategiesChanged) -> None:
        # Strategy removed? Pop back to selector
        if self.strategy not in self.store.strategies():
            self.app.pop_screen()


class PositionDetailScreen(Screen):
    BINDINGS = [Binding("escape", "back", "Back"), Binding("q", "back", "Back")]

    def __init__(self, entry: SnapshotEntry, position: PositionSnapshot, session_label: str) -> None:
        super().__init__()
        self.entry = entry
        self.position = position
        self.session_label = session_label
        self.content: Static | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        self.content = Static("", expand=True, classes="detail-body")
        yield self.content
        yield Footer()

    def on_mount(self) -> None:
        if self.content:
            self.content.update(self._build_body())

    def action_back(self) -> None:
        self.app.pop_screen()

    def _build_body(self) -> str:
        pos = self.position
        unrealized = _format_decimal(pos.unrealized_pnl)
        realized = _format_decimal(pos.realized_pnl, allow_zero=False)
        lines = [
            f"[bold]{pos.symbol}[/] — {self.session_label}",
            f"Status: {pos.lifecycle_stage.value if pos.lifecycle_stage else 'unknown'}",
            f"Notional: ${_format_decimal(pos.notional_exposure_usd)}  Funding: ${_format_decimal(pos.funding_accrued)}",
            f"Unrealized PnL: ${unrealized}  Realized PnL: ${realized}",
            f"Entry divergence: {_format_percent(pos.entry_divergence_pct)}  "
            f"Current: {_format_percent(pos.current_divergence_pct)}  "
            f"Erosion: {_format_percent(pos.profit_erosion_pct)}",
            "",
        ]

        for leg in pos.legs:
            lines.append(f"[bold]{leg.venue.upper()}[/] ({leg.side})")
            lines.append(
                f"  Entry: {_format_decimal(leg.entry_price)}  Qty: {_format_decimal(leg.quantity)}"
            )
            lines.append(
                f"  Mark: {_format_decimal(leg.mark_price)}  Funding: ${_format_decimal(leg.funding_accrued)}  Fees: ${_format_decimal(leg.fees_paid)}"
            )
            lines.append("")

        lines.append(
            f"Opened: {_format_datetime(pos.opened_at)}  •  Last update: {_format_datetime(pos.last_update)}"
        )
        if pos.custom_metadata:
            lines.append(f"Metadata: {pos.custom_metadata}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------


class DashboardApp(App):
    CSS = ""
    BINDINGS = [Binding("ctrl+c", "quit", "Quit")]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.store = DashboardStore()
        self._http_session: Optional[aiohttp.ClientSession] = None
        self._stream_task: Optional[asyncio.Task] = None
        self._known_strategies: set[str] = set()
        self._events: Dict[str, List[TimelineEvent]] = {}

    async def on_mount(self) -> None:
        if not database.is_connected:
            await database.connect()
        await self._ensure_http_session()
        await self._load_initial_state()
        self.push_screen(MainMenuScreen(self.store))
        await self._start_stream()

    async def on_unmount(self) -> None:
        if database.is_connected:
            await database.disconnect()
        if self._stream_task:
            self._stream_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._stream_task
            self._stream_task = None
        if self._http_session:
            await self._http_session.close()
            self._http_session = None

    # --- Networking ----------------------------------------------------

    async def _ensure_http_session(self) -> None:
        if not self._http_session:
            self._http_session = aiohttp.ClientSession()

    async def _load_initial_state(self) -> None:
        loaded = await self._fetch_snapshot_via_api()
        if not loaded:
            await self._load_snapshot_from_db()

    async def _fetch_snapshot_via_api(self) -> bool:
        if not self._http_session:
            return False
        try:
            async with self._http_session.get(f"{CONTROL_SERVER_HTTP}/snapshot") as response:
                if response.status != 200:
                    return False
                payload = await response.json()
        except aiohttp.ClientError:
            return False
        session_payload = payload.get("session")
        snapshot_payload = payload.get("snapshot")
        if not session_payload or not snapshot_payload:
            return False
        session_state = SessionState.model_validate(session_payload)
        snapshot = DashboardSnapshot.model_validate(snapshot_payload)
        self._handle_snapshot(session_state, snapshot)
        return True

    async def _load_snapshot_from_db(self) -> None:
        state = await load_dashboard_state(None, events_limit=0)
        if not state:
            return
        session_row, snapshot, _events = state
        if not snapshot:
            return
        session_state = snapshot.session
        self._handle_snapshot(session_state, snapshot)

    async def _start_stream(self) -> None:
        if self._stream_task and not self._stream_task.done():
            return
        await self._ensure_http_session()
        if not self._http_session:
            return
        self._stream_task = asyncio.create_task(self._listen_stream())

    async def _listen_stream(self) -> None:
        assert self._http_session is not None
        try:
            async with self._http_session.ws_connect(CONTROL_SERVER_WS) as ws:
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        await self._process_stream_message(msg.json())
                    elif msg.type == aiohttp.WSMsgType.ERROR:
                        break
        except aiohttp.ClientError:
            pass
        except asyncio.CancelledError:
            raise

    async def _process_stream_message(self, message: Dict[str, Any]) -> None:
        msg_type = message.get("type")
        payload = message.get("payload")
        if msg_type == "snapshot" and payload:
            session_data = payload.get("session")
            snapshot_data = payload.get("snapshot") or payload
            if session_data and snapshot_data:
                session_state = SessionState.model_validate(session_data)
                snapshot = DashboardSnapshot.model_validate(snapshot_data)
                self._handle_snapshot(session_state, snapshot)
        elif msg_type == "event" and payload:
            event = TimelineEvent.model_validate(payload)
            session_id = str(event.metadata.get("session_id", "global"))
            self._events.setdefault(session_id, []).append(event)

    # --- Snapshot handling ---------------------------------------------

    def _handle_snapshot(self, session: SessionState, snapshot: DashboardSnapshot) -> None:
        before = set(self.store.strategies().keys())
        self.store.update(session, snapshot)
        after = set(self.store.strategies().keys())
        self.post_message(SnapshotUpdated(session.strategy))
        if before != after:
            self.post_message(StrategiesChanged())


def run_dashboard_app() -> None:
    DashboardApp().run()


if __name__ == "__main__":
    run_dashboard_app()
def _get_leg(position: PositionSnapshot, side: str) -> Optional[PositionLegSnapshot]:
    for leg in position.legs:
        if (leg.side or "").lower() == side:
            return leg
    return position.legs[0] if position.legs else None
