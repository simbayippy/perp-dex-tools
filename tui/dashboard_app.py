"""
Textual-based dashboard CLI.

Provides a menu-driven interface for inspecting dashboard snapshots and serves
as the foundation for a richer operator experience.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from pathlib import Path
import sys
from typing import Optional

import aiohttp

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Footer, Header, Label, ListItem, ListView, Static

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dashboard.viewer_utils import load_dashboard_state, render_dashboard  # noqa: E402
from dashboard.models import DashboardSnapshot, SessionState, TimelineEvent  # noqa: E402
from funding_rate_service.database.connection import database  # noqa: E402


CONTROL_SERVER_HTTP = "http://127.0.0.1:8765"
CONTROL_SERVER_WS = "ws://127.0.0.1:8765/stream"


class MenuOptionSelected(Message):
    """Custom message emitted when a menu option is activated."""

    def __init__(self, option_id: str) -> None:
        self.option_id = option_id
        super().__init__()


class DashboardApp(App):
    """Main Textual application for dashboard interaction."""

    CSS = """
    Screen {
        layout: vertical;
    }
    #body {
        layout: horizontal;
        height: 100%;
    }
    #menu {
        width: 30%;
        border: solid rgb(60,60,60);
    }
    #content {
        padding: 1;
        border: solid rgb(60,60,60);
        overflow: auto;
    }
    ListView > ListItem.-selected {
        background: $accent;
        color: black;
    }
    """

    selected_session: reactive[Optional[str]] = reactive(None)

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._http_session: Optional[aiohttp.ClientSession] = None
        self._stream_task: Optional[asyncio.Task] = None
        self._current_snapshot: Optional[DashboardSnapshot] = None
        self._current_events: list[TimelineEvent] = []
        self._current_session_row: dict = {}

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="body"):
            self.menu = ListView(
                ListItem(Label("View Latest Snapshot"), id="view_snapshot"),
                ListItem(Label("Close Latest Position"), id="close_latest"),
                ListItem(Label("Pause Strategy"), id="pause_strategy"),
                ListItem(Label("Resume Strategy"), id="resume_strategy"),
                ListItem(Label("Start Bot (coming soon)"), id="start_bot"),
                ListItem(Label("Exit"), id="exit_app"),
                id="menu",
            )
            yield self.menu
            self.content = Static("Select an option from the menu to begin.", id="content")
            yield self.content
        yield Footer()

    async def on_mount(self) -> None:
        if not database.is_connected:
            await database.connect()
        if not self._http_session:
            self._http_session = aiohttp.ClientSession()
        self.menu.index = 0
        self.menu.focus()

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

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        option_id = event.item.id or ""
        await self.handle_menu_option(option_id)

    async def handle_menu_option(self, option_id: str) -> None:
        if option_id == "view_snapshot":
            await self.show_latest_snapshot()
            await self._start_stream()
        elif option_id == "close_latest":
            await self._command_close_latest()
        elif option_id == "start_bot":
            self.content.update("[yellow]Bot start workflow is coming soon.[/]")
        elif option_id == "pause_strategy":
            await self._command_pause()
        elif option_id == "resume_strategy":
            await self._command_resume()
        elif option_id == "exit_app":
            await self.action_quit()

    async def show_latest_snapshot(self) -> None:
        self.content.update("[cyan]Loading latest dashboard snapshot...[/]")
        try:
            api_state = await self._fetch_snapshot_via_api()
            if api_state:
                session_row, snapshot, events = api_state
            else:
                state = await load_dashboard_state(self.selected_session, events_limit=10)
                if not state:
                    self.content.update("[red]No dashboard sessions found.[/]")
                    return
                session_row, snapshot, events = state
                if snapshot is None:
                    self.content.update("[yellow]No snapshots recorded for this session.[/]")
                    return

            self._current_session_row = session_row
            self._current_snapshot = snapshot
            self._current_events = list(events)
            renderable = render_dashboard(session_row, snapshot, events)
            self.content.update(renderable)
        except Exception as exc:  # pragma: no cover
            self.content.update(f"[red]Failed to load snapshot: {exc}[/]")

    async def _fetch_snapshot_via_api(self):
        url = f"{CONTROL_SERVER_HTTP}/snapshot"
        if not self._http_session:
            return None
        try:
            async with self._http_session.get(url) as response:
                    if response.status != 200:
                        return None
                    payload = await response.json()
        except aiohttp.ClientError:
            return None
        except asyncio.CancelledError:
            raise
        except Exception:
            return None

        session_data = payload.get("session")
        snapshot_data = payload.get("snapshot")
        events_data = payload.get("events", [])

        if not snapshot_data:
            return None

        session_state = SessionState.model_validate(session_data) if session_data else None
        snapshot = DashboardSnapshot.model_validate(snapshot_data)
        events = [TimelineEvent.model_validate(item) for item in events_data]

        session_row = {
            "session_id": session_state.session_id if session_state else None,
            "strategy": session_state.strategy if session_state else "funding_arbitrage",
            "config_path": session_state.config_path if session_state else None,
            "started_at": session_state.started_at if session_state else None,
            "health": session_state.health.value if session_state else "unknown",
        }
        return session_row, snapshot, events

    async def _start_stream(self) -> None:
        if self._stream_task and not self._stream_task.done():
            return
        if not self._http_session:
            self._http_session = aiohttp.ClientSession()
        self._stream_task = asyncio.create_task(self._listen_stream())

    async def _listen_stream(self) -> None:
        if not self._http_session:
            return
        try:
            async with self._http_session.ws_connect(CONTROL_SERVER_WS) as ws:
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        data = msg.json()
                        await self._process_stream_message(data)
                    elif msg.type == aiohttp.WSMsgType.ERROR:
                        break
        except aiohttp.ClientError:
            self.call_from_thread(
                self.content.update,
                "[red]Lost connection to control server stream.[/]",
            )
        except asyncio.CancelledError:
            raise

    async def _process_stream_message(self, message: dict) -> None:
        msg_type = message.get("type")
        payload = message.get("payload")
        if not payload:
            return

        if msg_type == "snapshot":
            if isinstance(payload, dict) and "snapshot" in payload:
                session_data = payload.get("session")
                events_data = payload.get("events", [])
                snapshot_data = payload.get("snapshot")
                if session_data:
                    session_state = SessionState.model_validate(session_data)
                    self._current_session_row = {
                        "session_id": session_state.session_id,
                        "strategy": session_state.strategy,
                        "config_path": session_state.config_path,
                        "started_at": session_state.started_at,
                        "health": session_state.health.value,
                    }
                if events_data:
                    self._current_events = [
                        TimelineEvent.model_validate(event) for event in events_data
                    ]
                if snapshot_data:
                    self._current_snapshot = DashboardSnapshot.model_validate(snapshot_data)
            else:
                self._current_snapshot = DashboardSnapshot.model_validate(payload)
            await self._refresh_view()
        elif msg_type == "event":
            event = TimelineEvent.model_validate(payload)
            self._current_events.append(event)
            await self._refresh_view()

    async def _refresh_view(self) -> None:
        if not self._current_snapshot:
            return
        if not self._current_session_row:
            self._current_session_row = {
                "session_id": None,
                "strategy": "funding_arbitrage",
                "config_path": None,
                "started_at": None,
                "health": "unknown",
            }
        renderable = render_dashboard(
            self._current_session_row,
            self._current_snapshot,
            self._current_events,
        )
        self.call_from_thread(self.content.update, renderable)

    async def _send_command(self, payload: dict) -> tuple[bool, str]:
        if not self._http_session:
            return False, "Control server unavailable"
        try:
            async with self._http_session.post(
                f"{CONTROL_SERVER_HTTP}/commands", json=payload
            ) as response:
                data = await response.json()
                ok = data.get("ok", response.status == 200)
                message = data.get("message") or data.get("error") or response.reason
                return ok and response.status == 200, message
        except aiohttp.ClientError as exc:
            return False, str(exc)

    async def _command_close_latest(self) -> None:
        if not self._current_snapshot:
            api_state = await self._fetch_snapshot_via_api()
            if api_state:
                self._current_session_row, self._current_snapshot, self._current_events = api_state
        if not self._current_snapshot or not self._current_snapshot.positions:
            self.content.update("[yellow]No open positions to close.[/]")
            return
        position = self._current_snapshot.positions[0]
        ok, message = await self._send_command(
            {"type": "close_position", "position_id": str(position.position_id)}
        )
        color = "green" if ok else "red"
        self.content.update(f"[{color}]{message}[/]")

    async def _command_pause(self) -> None:
        ok, message = await self._send_command({"type": "pause_strategy"})
        color = "green" if ok else "red"
        self.content.update(f"[{color}]{message}[/]")

    async def _command_resume(self) -> None:
        ok, message = await self._send_command({"type": "resume_strategy"})
        color = "green" if ok else "red"
        self.content.update(f"[{color}]{message}[/]")


def run_dashboard_app() -> None:
    DashboardApp().run()


if __name__ == "__main__":
    run_dashboard_app()
