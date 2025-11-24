#!/usr/bin/env python3
"""
Live position viewer backed by the Control API WebSocket stream.

Key features:
 - Automatically discovers the correct Control API port for a user
 - Fetches stored API keys from the database (via Telegram auth metadata)
 - Streams real-time mark updates via /api/v1/live/bbo WebSocket endpoint
 - Refreshes slow-changing funding/entry/leverage data at a low cadence
"""

import argparse
import asyncio
import json
import os
import signal
import sys
from collections import defaultdict, deque
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
from databases import Database
from dotenv import load_dotenv
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

# Add project root to sys.path
project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project_root))

from database.repositories.user_repository import UserRepository  # noqa: E402
from telegram_bot_service.utils.auth import TelegramAuth  # noqa: E402


@dataclass
class StrategyRunInfo:
    """Minimal info about a running strategy process."""

    run_id: str
    account_name: str
    status: str
    port: int


class ControlAPIDiscovery:
    """Helper that resolves API keys and control ports from the database."""

    def __init__(self, database_url: Optional[str], console: Console):
        self.database_url = database_url
        self.console = console
        self.database: Optional[Database] = None
        self.user_repo: Optional[UserRepository] = None
        self.telegram_auth: Optional[TelegramAuth] = None

    async def connect(self) -> None:
        if not self.database_url:
            raise RuntimeError("DATABASE_URL is required for auto-discovery")
        if self.database:
            return
        self.database = Database(self.database_url)
        await self.database.connect()
        self.user_repo = UserRepository(self.database)
        self.telegram_auth = TelegramAuth(self.database)

    async def close(self) -> None:
        if self.database and self.database.is_connected:
            await self.database.disconnect()
        self.database = None
        self.user_repo = None
        self.telegram_auth = None

    async def get_user(self, username: str) -> Dict[str, Any]:
        if not self.user_repo:
            raise RuntimeError("User repository not initialized")
        user = await self.user_repo.get_by_username(username)
        if not user:
            raise ValueError(f"User '{username}' not found")
        if not user.get("is_active"):
            raise ValueError(f"User '{username}' is inactive")
        return user

    async def resolve_api_key(self, user: Dict[str, Any], override: Optional[str]) -> Optional[str]:
        if override:
            return override
        if not self.telegram_auth:
            return None
        return await self.telegram_auth.get_api_key_for_user(user)

    async def find_strategy_runs(self, user_id: str) -> List[StrategyRunInfo]:
        if not self.database:
            raise RuntimeError("Database connection not initialized")
        rows = await self.database.fetch_all(
            """
            SELECT 
                sr.id::text AS run_id,
                sr.control_api_port,
                sr.status,
                a.account_name
            FROM strategy_runs sr
            JOIN accounts a ON sr.account_id = a.id
            WHERE sr.user_id = :user_id
              AND sr.control_api_port IS NOT NULL
            ORDER BY sr.started_at DESC
            """,
            {"user_id": str(user_id)},
        )
        runs: List[StrategyRunInfo] = []
        for row in rows:
            port = row["control_api_port"]
            if port is None:
                continue
            runs.append(
                StrategyRunInfo(
                    run_id=row["run_id"],
                    account_name=row["account_name"],
                    status=row["status"],
                    port=int(port),
                )
            )
        return runs

    def _prompt_run_choice(self, runs: List[StrategyRunInfo]) -> StrategyRunInfo:
        self.console.print(
            "\n[bold cyan]Multiple strategy runs detected for this user. Please select one:[/bold cyan]"
        )
        for idx, run in enumerate(runs, start=1):
            self.console.print(
                f"  [green]{idx}[/green]. {run.account_name} "
                f"(status: {run.status}, port: {run.port}, run_id: {run.run_id[:8]})"
            )

        while True:
            choice = input("\nEnter selection number: ").strip()
            if not choice:
                continue
            if choice.isdigit():
                index = int(choice) - 1
                if 0 <= index < len(runs):
                    return runs[index]
            self.console.print("[red]Invalid choice. Please enter a valid number.[/red]")

    async def select_strategy_run(
        self,
        user: Dict[str, Any],
        account_name: Optional[str],
    ) -> StrategyRunInfo:
        runs = await self.find_strategy_runs(user["id"])
        if not runs:
            raise ValueError("No strategy runs with control API ports found for this user")

        if account_name:
            filtered = [run for run in runs if run.account_name == account_name]
            if not filtered:
                raise ValueError(
                    f"No runs found for account '{account_name}'. Available: "
                    + ", ".join(sorted({run.account_name for run in runs}))
                )
            if len(filtered) == 1:
                return filtered[0]
            return self._prompt_run_choice(filtered)

        if len(runs) == 1:
            return runs[0]
        return self._prompt_run_choice(runs)


class LivePositionViewer:
    """Fetches static position data via REST and streams BBO updates via websocket."""

    def __init__(
        self,
        api_url: str,
        api_key: str,
        refresh_interval: float = 1.0,
        static_refresh_interval: float = 30.0,
        account_filter: Optional[str] = None,
        log_files: Optional[List[Tuple[str, Path]]] = None,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.refresh_interval = refresh_interval
        self.static_refresh_interval = static_refresh_interval
        self.account_filter = account_filter
        self.log_files = log_files or []

        if self.api_url.startswith("https://"):
            self.ws_url = "wss://" + self.api_url[len("https://") :]
        elif self.api_url.startswith("http://"):
            self.ws_url = "ws://" + self.api_url[len("http://") :]
        else:
            self.ws_url = "ws://" + self.api_url
        self.ws_url = f"{self.ws_url}/api/v1/live/bbo"

        self.console = Console()
        self.session: Optional[aiohttp.ClientSession] = None
        self.running = True

        self._accounts: List[Dict[str, Any]] = []
        self._current_positions: List[Dict[str, Any]] = []
        self._leg_index: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
        self._data_lock = asyncio.Lock()
        self._dirty = asyncio.Event()
        self._last_error: Optional[str] = None
        self._log_lines: List[str] = []
        self._log_lock = asyncio.Lock()
        self._log_tasks: List[asyncio.Task] = []
        self._log_history = 200

    async def fetch_positions(self) -> Dict[str, Any]:
        """Fetch positions from Control API (slow-changing data)."""
        if not self.session:
            self.session = aiohttp.ClientSession()
        params = {}
        if self.account_filter:
            params["account_name"] = self.account_filter
        try:
            headers = {"X-API-Key": self.api_key}
            async with self.session.get(
                f"{self.api_url}/api/v1/positions",
                headers=headers,
                params=params,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                if response.status == 200:
                    return await response.json()
                return {"error": f"HTTP {response.status}"}
        except aiohttp.ClientError as exc:
            return {"error": f"Connection error: {exc}"}
        except asyncio.TimeoutError:
            return {"error": "Request timeout"}

    def generate_table(self) -> Table:
        """Render table using the latest cached data."""
        table = Table(
            title="[bold cyan]Live Positions (via Control API)[/bold cyan]",
            show_header=True,
            header_style="bold magenta",
        )
        table.add_column("Account", style="green", no_wrap=True)
        table.add_column("Symbol", style="cyan", no_wrap=True)
        table.add_column("Exchange", style="yellow")
        table.add_column("Side", style="blue")
        table.add_column("Qty", justify="right")
        table.add_column("Entry", justify="right")
        table.add_column("Mark", justify="right", style="yellow")
        table.add_column("uPnL", justify="right")
        table.add_column("Funding", justify="right")
        table.add_column("APY", justify="right")
        table.add_column("Age", justify="right")

        if self._last_error:
            table.add_row(
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                Text(f"[red]{self._last_error}[/red]"),
                "",
                "",
                "",
            )

        positions = sorted(
            self._current_positions,
            key=lambda p: (p.get("_account_name", ""), p.get("symbol", "")),
        )

        if not positions:
            table.add_row(
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "[dim]No open positions[/dim]",
                "",
                "",
                "",
            )
            return table

        for position in positions:
            self._add_position_rows(table, position)
        return table

    def _render(self):
        if not self.log_files:
            return self.generate_table()

        layout = Layout()
        layout.split(
            Layout(self._render_logs(), size=20),
            Layout(self.generate_table()),
        )
        return layout

    def _render_logs(self) -> Panel:
        if not self.log_files:
            return Panel("[dim]Log streaming disabled[/dim]", title="Strategy Logs", border_style="cyan")

        if not self._log_lines:
            message = "[dim]Waiting for log data...[/dim]"
        else:
            message = "\n".join(self._log_lines[-self._log_history :])
        return Panel(message, title="Strategy Logs", border_style="cyan")

    def _add_position_rows(self, table: Table, position: Dict[str, Any]) -> None:
        symbol = position.get("symbol", "n/a")
        account_name = position.get("_account_name") or position.get("account_name") or "n/a"
        opened_at = position.get("opened_at")
        age_str = self._format_age(opened_at)

        legs = position.get("legs") or []
        if not legs:
            table.add_row(
                account_name,
                symbol,
                "",
                "",
                "",
                "",
                "",
                Text("[red]No leg data[/red]"),
                "",
                "",
                age_str,
            )
            return

        for idx, leg in enumerate(legs):
            dex = leg.get("dex")
            side = leg.get("side")
            qty_str = self._format_number(leg.get("quantity"), 4)
            entry_str = self._format_number(leg.get("entry_price"), 6)
            mark_str = self._format_number(leg.get("mark_price"), 6)
            upnl_text = self._format_upnl(leg.get("unrealized_pnl"))
            funding_str = self._format_number(leg.get("funding_accrued"), 2)
            apy_str = self._format_apy(leg.get("funding_apy"))

            table.add_row(
                account_name if idx == 0 else "",
                symbol if idx == 0 else "",
                dex or "n/a",
                side or "n/a",
                qty_str,
                entry_str,
                mark_str,
                upnl_text,
                funding_str,
                apy_str,
                age_str if idx == 0 else "",
            )

    def _format_number(self, value: Optional[Any], precision: int) -> str:
        if value is None:
            return "n/a"
        try:
            return f"{float(value):.{precision}f}"
        except (TypeError, ValueError):
            return "n/a"

    def _format_upnl(self, upnl: Optional[Any]) -> Text:
        if upnl is None:
            return Text("n/a", style="dim")
        try:
            upnl_val = float(upnl)
        except (TypeError, ValueError):
            return Text("n/a", style="dim")

        if upnl_val > 0:
            return Text(f"+${upnl_val:.2f}", style="bold green")
        if upnl_val < 0:
            return Text(f"-${abs(upnl_val):.2f}", style="bold red")
        return Text("$0.00", style="dim")

    def _format_age(self, opened_at: Optional[str]) -> str:
        if not opened_at:
            return "n/a"
        try:
            opened_dt = datetime.fromisoformat(opened_at.replace("Z", "+00:00"))
            if opened_dt.tzinfo is None:
                opened_dt = opened_dt.replace(tzinfo=timezone.utc)
            delta = datetime.now(timezone.utc) - opened_dt
            total_seconds = int(delta.total_seconds())
            hours, remainder = divmod(total_seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        except Exception:
            return "n/a"

    def _format_apy(self, apy_value: Optional[Any]) -> str:
        if apy_value is None:
            return "n/a"
        try:
            return f"{float(apy_value):.2f}%"
        except (TypeError, ValueError):
            return "n/a"

    async def run(self) -> None:
        """Start HTTP refresh + websocket streaming loops."""
        def signal_handler(sig, frame):
            self.running = False
            self._dirty.set()
            self.console.print("\n[yellow]Stopping viewer...[/yellow]")

        for sig in (signal.SIGINT, signal.SIGTERM):
            with suppress(NotImplementedError):
                signal.signal(sig, signal_handler)

        self.console.print(f"[green]Connecting to Control API at {self.api_url}[/green]")
        if self.account_filter:
            self.console.print(f"[green]Filtering account: {self.account_filter}[/green]")
        self.console.print(f"[green]WebSocket stream: {self.ws_url}[/green]")
        self.console.print(f"[green]Table refresh interval: {self.refresh_interval}s[/green]")
        self.console.print(
            f"[green]Static data refresh interval: {self.static_refresh_interval}s[/green]\n"
        )

        self.session = aiohttp.ClientSession()

        tasks: List[asyncio.Task] = []

        try:
            await self._refresh_positions(initial=True)
            self._dirty.set()

            tasks.append(asyncio.create_task(self._static_refresh_loop()))
            tasks.append(asyncio.create_task(self._websocket_loop()))
            tasks.append(asyncio.create_task(self._log_manager_loop()))

            with Live(
                self._render(),
                console=self.console,
                refresh_per_second=4,
                screen=False,
            ) as live:
                while self.running:
                    try:
                        await asyncio.wait_for(self._dirty.wait(), timeout=self.refresh_interval)
                    except asyncio.TimeoutError:
                        pass
                    self._dirty.clear()
                    live.update(self._render())

        finally:
            self.running = False
            for task in tasks:
                task.cancel()
            for task in tasks:
                with suppress(asyncio.CancelledError):
                    await task
            for log_task in self._log_tasks:
                log_task.cancel()
                with suppress(asyncio.CancelledError):
                    await log_task
            if self.session:
                await self.session.close()
            self.console.print("[green]✅ Viewer stopped[/green]")

    async def _static_refresh_loop(self) -> None:
        """Periodically refresh positions via REST."""
        while self.running:
            await asyncio.sleep(self.static_refresh_interval)
            await self._refresh_positions()

    async def _refresh_positions(self, initial: bool = False) -> None:
        """Fetch static position payload and rebuild local caches."""
        payload = await self.fetch_positions()
        if "error" in payload:
            self._last_error = payload["error"]
            self._dirty.set()
            return

        accounts = payload.get("accounts")
        if accounts is None:
            # Legacy format returned flat positions list
            accounts = [
                {
                    "account_name": payload.get("account_name", "n/a"),
                    "positions": payload.get("positions", []),
                }
            ]

        accounts = self._normalize_accounts(accounts)

        flat_positions: List[Dict[str, Any]] = []
        leg_index: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)

        for account in accounts:
            for position in account.get("positions", []):
                position["_account_name"] = account.get("account_name", "n/a")
                position["_account_id"] = account.get("account_id")
                legs = position.get("legs") or []
                flat_positions.append(position)
                for leg in legs:
                    dex = (leg.get("dex") or "").upper()
                    symbol = (position.get("symbol") or "").upper()
                    if dex and symbol:
                        leg_index[(dex, symbol)].append(leg)

        async with self._data_lock:
            self._accounts = accounts
            self._current_positions = flat_positions
            self._leg_index = leg_index
            self._last_error = None
        self._dirty.set()

    def _normalize_accounts(self, accounts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Ensure each position has a normalized legs list."""
        normalized_accounts: List[Dict[str, Any]] = []
        for account in accounts:
            positions = account.get("positions") or []
            for position in positions:
                metadata = position.get("metadata") or {}
                if isinstance(metadata, str):
                    try:
                        metadata = json.loads(metadata)
                    except Exception:
                        metadata = {}
                legs = position.get("legs")
                if not legs:
                    legs = self._normalize_legs_from_metadata(position, metadata)
                else:
                    legs = [
                        self._normalize_leg_dict(leg, position.get("symbol"), metadata) for leg in legs
                    ]
                position["legs"] = [leg for leg in legs if leg]
            normalized_accounts.append(account)
        return normalized_accounts

    def _normalize_legs_from_metadata(
        self,
        position: Dict[str, Any],
        metadata: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        legs_meta = metadata.get("legs") or {}
        normalized: List[Dict[str, Any]] = []
        for dex, leg_meta in legs_meta.items():
            base = dict(leg_meta or {})
            base["dex"] = dex.upper()
            base["symbol"] = position.get("symbol")
            normalized_leg = self._normalize_leg_dict(base, position.get("symbol"), metadata)
            if normalized_leg:
                normalized.append(normalized_leg)
        return normalized

    def _normalize_leg_dict(
        self,
        leg: Dict[str, Any],
        symbol: Optional[str],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        if not leg:
            return None
        normalized = dict(leg)
        if "dex" in normalized and normalized["dex"]:
            normalized["dex"] = str(normalized["dex"]).upper()
        normalized.setdefault("side", "unknown")
        normalized["symbol"] = symbol

        def to_float(value: Any) -> Optional[float]:
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        for key in ("quantity", "entry_price", "mark_price", "funding_accrued", "funding_apy"):
            if key in normalized and normalized[key] is not None:
                normalized[key] = to_float(normalized[key])

        if normalized.get("funding_apy") is None and metadata:
            rate_map = metadata.get("rate_map") or {}
            dex_name = normalized.get("dex")
            if dex_name:
                rate_value = rate_map.get(dex_name) or rate_map.get(dex_name.lower())
                apy = self._calculate_apy_from_rate(rate_value)
                if apy is not None:
                    normalized["funding_apy"] = apy

        if normalized.get("quantity") is None:
            normalized["quantity"] = 0.0
        return normalized

    def _calculate_apy_from_rate(self, rate: Optional[Any]) -> Optional[float]:
        if rate is None:
            return None
        try:
            return float(rate) * 3 * 365 * 100
        except (TypeError, ValueError):
            return None

    async def _websocket_loop(self) -> None:
        """Consume real-time BBO feed and patch leg marks."""
        if not self.session:
            self.session = aiohttp.ClientSession()

        headers = {"X-API-Key": self.api_key}
        backoff = 1

        while self.running:
            try:
                async with self.session.ws_connect(self.ws_url, headers=headers, heartbeat=30) as ws:
                    backoff = 1
                    async for msg in ws:
                        if not self.running:
                            break
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                data = msg.json()
                            except Exception:
                                continue
                            await self._handle_stream_payload(data)
                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            break
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_error = f"WebSocket error: {exc}"
                self._dirty.set()

            if not self.running:
                break

            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 15)

    async def _handle_stream_payload(self, payload: Dict[str, Any]) -> None:
        if payload.get("type") != "bbo":
            return

        exchange = (payload.get("exchange") or "").upper()
        symbol = (payload.get("symbol") or "").upper()
        if not exchange or not symbol:
            return

        bid = payload.get("bid")
        ask = payload.get("ask")

        async with self._data_lock:
            matching_legs: List[Dict[str, Any]] = []
            for (dex_key, pos_symbol), leg_group in self._leg_index.items():
                if dex_key != exchange:
                    continue
                if self._symbol_matches(symbol, pos_symbol):
                    matching_legs.extend(leg_group)

            if not matching_legs:
                return

            updated = False
            for leg in matching_legs:
                mark = self._select_mark_price(leg.get("side"), bid, ask)
                if mark is None:
                    continue
                entry = leg.get("entry_price")
                quantity = leg.get("quantity")
                if entry is None or quantity is None:
                    continue
                leg["mark_price"] = mark
                leg["unrealized_pnl"] = self._calculate_upnl(leg.get("side"), quantity, entry, mark)
                updated = True
            if updated:
                self._dirty.set()

    def _select_mark_price(
        self,
        side: Optional[str],
        bid: Optional[Any],
        ask: Optional[Any],
    ) -> Optional[float]:
        bid_val = self._safe_float(bid)
        ask_val = self._safe_float(ask)
        side_lower = (side or "").lower()
        if side_lower == "long":
            return bid_val if bid_val is not None else ask_val
        if side_lower == "short":
            return ask_val if ask_val is not None else bid_val
        if bid_val is not None and ask_val is not None:
            return (bid_val + ask_val) / 2
        return bid_val or ask_val

    def _calculate_upnl(
        self,
        side: Optional[str],
        quantity: float,
        entry_price: float,
        mark_price: float,
    ) -> float:
        side_lower = (side or "").lower()
        if side_lower == "short":
            return quantity * (entry_price - mark_price)
        return quantity * (mark_price - entry_price)

    def _safe_float(self, value: Any) -> Optional[float]:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _symbol_matches(self, bbo_symbol: Optional[str], position_symbol: Optional[str]) -> bool:
        """Heuristic symbol matcher to align exchange-specific symbols with normalized ones."""
        if not bbo_symbol or not position_symbol:
            return False

        bbo_upper = str(bbo_symbol).upper()
        pos_upper = str(position_symbol).upper()

        if bbo_upper == pos_upper:
            return True

        # Normalize separators and suffixes (USDT/PERP)
        bbo_compact = bbo_upper.replace("-", "").replace("_", "")
        pos_compact = pos_upper.replace("-", "").replace("_", "")

        if bbo_compact == pos_compact:
            return True

        if bbo_upper == f"{pos_upper}USDT":
            return True
        if bbo_upper.endswith("USDT") and bbo_upper[:-4] == pos_upper:
            return True
        if bbo_upper.endswith("-PERP") and bbo_upper[:-5] == pos_upper:
            return True

        if pos_upper in bbo_upper or bbo_upper in pos_upper:
            return True
        if pos_compact in bbo_compact or bbo_compact in pos_compact:
            return True

        return False

    async def _log_manager_loop(self) -> None:
        if not self.log_files:
            return

        for label, path in self.log_files:
            task = asyncio.create_task(self._tail_log_file(label, path))
            self._log_tasks.append(task)

        while self.running:
            await asyncio.sleep(1)

    async def _tail_log_file(self, label: str, path: Path) -> None:
        path = Path(path)
        while self.running:
            if not path.exists():
                await asyncio.sleep(1)
                continue

            try:
                with path.open("r") as handle:
                    lines = deque(handle, maxlen=self._log_history)
                    if lines:
                        await self._append_log_lines(label, list(lines))
                    position = handle.tell()

                    while self.running:
                        handle.seek(position)
                        chunk = handle.read()
                        if chunk:
                            position = handle.tell()
                            new_lines = chunk.splitlines()
                            if new_lines:
                                await self._append_log_lines(label, new_lines)
                        await asyncio.sleep(0.5)
            except FileNotFoundError:
                await asyncio.sleep(1)
            except Exception as exc:
                await self._append_log_lines(label, [f"[log error] {exc}"])
                await asyncio.sleep(2)

    async def _append_log_lines(self, label: str, lines: List[str]) -> None:
        formatted = []
        for line in lines:
            stripped = line.rstrip()
            if not stripped:
                continue
            tag = label.upper()
            if label.lower() == "stderr":
                formatted.append(f"[red][{tag}][/red] {stripped}")
            else:
                formatted.append(f"[green][{tag}][/green] {stripped}")

        if not formatted:
            return

        async with self._log_lock:
            self._log_lines.extend(formatted)
            if len(self._log_lines) > self._log_history:
                self._log_lines = self._log_lines[-self._log_history :]
        self._dirty.set()


async def auto_configure_viewer(
    args: argparse.Namespace, console: Console
) -> Tuple[str, int, str, Optional[str], Optional[StrategyRunInfo]]:
    """
    Resolve host, port, API key, and account filter using username discovery.

    Returns:
        Tuple (host, port, api_key, account_filter, run_info)
    """
    host = args.host or os.getenv("CONTROL_API_HOST", "127.0.0.1")
    port = args.port or int(os.getenv("CONTROL_API_PORT", "0") or "0")
    api_key = args.api_key or os.getenv("CONTROL_API_KEY")
    account_filter = args.account
    selected_run: Optional[StrategyRunInfo] = None

    if not args.username:
        if not port:
            raise ValueError("Control API port required (use --port or set CONTROL_API_PORT)")
        if not api_key:
            raise ValueError("API key required (use --api-key, CONTROL_API_KEY, or --username)")
        return host, port, api_key, account_filter, None

    database_url = os.getenv("DATABASE_URL")
    discovery = ControlAPIDiscovery(database_url, console)
    await discovery.connect()

    try:
        user = await discovery.get_user(args.username)
        api_key = await discovery.resolve_api_key(user, args.api_key) or api_key
        if not api_key:
            raise ValueError(
                "API key not found. Provide --api-key or ensure Telegram auth stored a key for this user."
            )
        if not port:
            selected_run = await discovery.select_strategy_run(user, args.account)
            port = selected_run.port
            account_filter = selected_run.account_name
    finally:
        await discovery.close()

    return host, port, api_key, account_filter, selected_run


def find_log_files(run: Optional[StrategyRunInfo]) -> List[Tuple[str, Path]]:
    if not run:
        return []
    log_dir = project_root / "logs"
    stdout = log_dir / f"strategy_{run.run_id}.out.log"
    stderr = log_dir / f"strategy_{run.run_id}.err.log"
    return [("stdout", stdout), ("stderr", stderr)]


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Stream live funding-arb positions via Control API WebSocket",
    )
    parser.add_argument("--username", help="Username for auto-detecting API key and control port")
    parser.add_argument("--account", help="Account name to filter (auto-selected when unique)")
    parser.add_argument("--host", help="Control API host (default: env CONTROL_API_HOST or 127.0.0.1)")
    parser.add_argument("--port", type=int, help="Control API port (auto-detected when --username provided)")
    parser.add_argument("--api-key", help="API key override (otherwise auto-detected or CONTROL_API_KEY)")
    parser.add_argument("--refresh", type=float, default=1.0, help="UI refresh cadence (seconds)")
    parser.add_argument(
        "--static-refresh",
        type=float,
        default=300.0,
        help="Static data refresh interval (seconds, default: 300)",
    )

    args = parser.parse_args()

    load_dotenv()
    console = Console()

    try:
        host, port, api_key, account_filter, run_info = await auto_configure_viewer(args, console)
    except Exception as exc:
        console.print(f"[red]❌ {exc}[/red]")
        sys.exit(1)

    log_files = find_log_files(run_info)
    api_url = f"http://{host}:{port}"
    viewer = LivePositionViewer(
        api_url=api_url,
        api_key=api_key,
        refresh_interval=args.refresh,
        static_refresh_interval=args.static_refresh,
        account_filter=account_filter,
        log_files=log_files,
    )
    await viewer.run()


if __name__ == "__main__":
    asyncio.run(main())
