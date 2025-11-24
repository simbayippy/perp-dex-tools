#!/usr/bin/env python3
"""
Standalone script to view live positions via Control API.

This script fetches position data from the strategy's Control API and displays
it in a real-time Rich table. No need to attach to the running process.

Usage:
    python scripts/strategies/view_live_positions.py --port 8768
    python scripts/strategies/view_live_positions.py --port 8768 --refresh 2
"""

import argparse
import asyncio
import signal
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Optional

import aiohttp
from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.text import Text

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))


class LivePositionViewer:
    """Fetches position data from Control API and displays in Rich table."""

    def __init__(self, api_url: str, refresh_interval: float = 1.0):
        """
        Initialize viewer.

        Args:
            api_url: Base URL for Control API (e.g., http://127.0.0.1:8768)
            refresh_interval: Refresh interval in seconds
        """
        self.api_url = api_url.rstrip("/")
        self.refresh_interval = refresh_interval
        self.console = Console()
        self.session: Optional[aiohttp.ClientSession] = None
        self.running = True

    async def fetch_positions(self) -> Dict[str, Any]:
        """Fetch positions from Control API."""
        if not self.session:
            self.session = aiohttp.ClientSession()

        try:
            async with self.session.get(
                f"{self.api_url}/api/v1/positions",
                timeout=aiohttp.ClientTimeout(total=5)
            ) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    return {"positions": [], "error": f"HTTP {response.status}"}
        except aiohttp.ClientError as e:
            return {"positions": [], "error": f"Connection error: {e}"}
        except asyncio.TimeoutError:
            return {"positions": [], "error": "Request timeout"}

    def generate_table(self, data: Dict[str, Any]) -> Table:
        """Generate Rich table from position data."""
        table = Table(
            title="[bold cyan]Live Positions (via Control API)[/bold cyan]",
            show_header=True,
            header_style="bold magenta"
        )

        # Add columns
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

        # Check for errors
        if "error" in data:
            table.add_row(
                "", "", "", "", "", "",
                Text(f"[red]Error: {data['error']}[/red]"),
                "", "", ""
            )
            return table

        positions = data.get("positions", [])

        if not positions:
            table.add_row(
                "", "", "", "", "", "",
                "[dim]No open positions[/dim]",
                "", "", ""
            )
            return table

        # Add rows for each position
        for position in sorted(positions, key=lambda p: p.get("symbol", "")):
            self._add_position_rows(table, position)

        return table

    def _add_position_rows(self, table: Table, position: Dict[str, Any]) -> None:
        """Add rows for a position's long and short legs."""
        symbol = position.get("symbol", "n/a")
        long_dex = position.get("long_dex")
        short_dex = position.get("short_dex")
        opened_at = position.get("opened_at")

        # Calculate age
        age_str = self._format_age(opened_at)

        # Get metadata
        metadata = position.get("metadata", {})
        legs_metadata = metadata.get("legs", {})
        rate_map = metadata.get("rate_map", {})

        for dex, side in [(long_dex, "long"), (short_dex, "short")]:
            if not dex:
                continue

            leg_meta = legs_metadata.get(dex, {})

            # Extract metrics
            quantity = leg_meta.get("quantity", 0)
            entry_price = leg_meta.get("entry_price")
            mark_price = leg_meta.get("mark_price")
            unrealized_pnl = leg_meta.get("unrealized_pnl")
            funding_accrued = leg_meta.get("funding_accrued")

            # Format fields
            qty_str = f"{float(quantity):.4f}" if quantity else "n/a"
            entry_str = f"{float(entry_price):.6f}" if entry_price else "n/a"
            mark_str = f"{float(mark_price):.6f}" if mark_price else "n/a"

            # Format uPnL with color
            upnl_str = self._format_upnl(unrealized_pnl)

            # Format funding
            funding_str = f"{float(funding_accrued):.2f}" if funding_accrued is not None else "n/a"

            # Get funding APY
            funding_rate = rate_map.get(dex)
            if funding_rate is not None:
                try:
                    rate_decimal = Decimal(str(funding_rate))
                    apy = float(rate_decimal * Decimal("3") * Decimal("365") * Decimal("100"))
                    apy_str = f"{apy:.2f}%"
                except Exception:
                    apy_str = "n/a"
            else:
                apy_str = "n/a"

            # Add row
            table.add_row(
                symbol if dex == long_dex else "",  # Only show symbol on first row
                dex.upper() if dex else "n/a",
                side,
                qty_str,
                entry_str,
                mark_str,
                upnl_str,
                funding_str,
                apy_str,
                age_str if dex == long_dex else "",  # Only show age on first row
            )

    def _format_upnl(self, upnl: Optional[float]) -> Text:
        """Format uPnL with color coding."""
        if upnl is None:
            return Text("n/a", style="dim")

        if upnl > 0:
            return Text(f"+${upnl:.2f}", style="bold green")
        elif upnl < 0:
            return Text(f"-${abs(upnl):.2f}", style="bold red")
        else:
            return Text("$0.00", style="dim")

    def _format_age(self, opened_at: Optional[str]) -> str:
        """Format position age as HH:MM:SS."""
        if not opened_at:
            return "n/a"

        try:
            # Parse ISO format timestamp
            opened_dt = datetime.fromisoformat(opened_at.replace("Z", "+00:00"))

            # Ensure timezone-aware
            if opened_dt.tzinfo is None:
                opened_dt = opened_dt.replace(tzinfo=timezone.utc)

            now = datetime.now(timezone.utc)
            age_seconds = (now - opened_dt).total_seconds()

            hours = int(age_seconds // 3600)
            minutes = int((age_seconds % 3600) // 60)
            seconds = int(age_seconds % 60)

            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        except Exception:
            return "n/a"

    async def run(self) -> None:
        """Run the live viewer."""
        # Setup signal handler
        def signal_handler(sig, frame):
            self.running = False
            self.console.print("\n[yellow]Stopping viewer...[/yellow]")

        signal.signal(signal.SIGINT, signal_handler)

        try:
            self.console.print(f"[green]Connecting to Control API at {self.api_url}[/green]")
            self.console.print(f"[green]Refresh interval: {self.refresh_interval}s[/green]")
            self.console.print("[yellow]Press Ctrl+C to exit[/yellow]\n")

            with Live(
                self.generate_table({"positions": []}),
                console=self.console,
                refresh_per_second=4,
                screen=False,
            ) as live:
                while self.running:
                    # Fetch and update
                    data = await self.fetch_positions()
                    live.update(self.generate_table(data))

                    # Wait for next refresh
                    await asyncio.sleep(self.refresh_interval)

        finally:
            if self.session:
                await self.session.close()
            self.console.print("[green]✅ Viewer stopped[/green]")


async def main():
    parser = argparse.ArgumentParser(
        description="View live positions via Control API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # View positions on default port
  python scripts/strategies/view_live_positions.py --port 8768

  # Custom refresh interval (2 seconds)
  python scripts/strategies/view_live_positions.py --port 8768 --refresh 2

  # Connect to remote server
  python scripts/strategies/view_live_positions.py --host 192.168.1.100 --port 8768
        """
    )

    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Control API host (default: 127.0.0.1)"
    )
    parser.add_argument(
        "--port",
        type=int,
        required=True,
        help="Control API port (e.g., 8768)"
    )
    parser.add_argument(
        "--refresh",
        type=float,
        default=1.0,
        help="Refresh interval in seconds (default: 1.0)"
    )

    args = parser.parse_args()

    api_url = f"http://{args.host}:{args.port}"
    viewer = LivePositionViewer(api_url, args.refresh)

    await viewer.run()


if __name__ == "__main__":
    asyncio.run(main())
