"""
Textual-based dashboard CLI.

Provides a menu-driven interface for inspecting dashboard snapshots and serves
as the foundation for a richer operator experience.
"""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Optional

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Footer, Header, Label, ListItem, ListView, Static

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dashboard.viewer_utils import load_dashboard_state, render_dashboard  # noqa: E402
from funding_rate_service.database.connection import database  # noqa: E402


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

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="body"):
            self.menu = ListView(
                ListItem(Label("View Latest Snapshot"), id="view_snapshot"),
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
        self.menu.index = 0
        self.menu.focus()

    async def on_unmount(self) -> None:
        if database.is_connected:
            await database.disconnect()

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        option_id = event.item.id or ""
        await self.handle_menu_option(option_id)

    async def handle_menu_option(self, option_id: str) -> None:
        if option_id == "view_snapshot":
            await self.show_latest_snapshot()
        elif option_id == "start_bot":
            self.content.update("[yellow]Bot start workflow is coming soon.[/]")
        elif option_id == "exit_app":
            await self.action_quit()

    async def show_latest_snapshot(self) -> None:
        self.content.update("[cyan]Loading latest dashboard snapshot...[/]")
        try:
            state = await load_dashboard_state(self.selected_session, events_limit=10)
            if not state:
                self.content.update("[red]No dashboard sessions found.[/]")
                return

            session_row, snapshot, events = state
            if snapshot is None:
                self.content.update("[yellow]No snapshots recorded for this session.[/]")
                return

            renderable = render_dashboard(session_row, snapshot, events)
            self.content.update(renderable)
        except Exception as exc:  # pragma: no cover
            self.content.update(f"[red]Failed to load snapshot: {exc}[/]")


def run_dashboard_app() -> None:
    DashboardApp().run()


if __name__ == "__main__":
    run_dashboard_app()
