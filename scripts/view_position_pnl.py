#!/usr/bin/env python3
"""
View position-level PnL for funding arbitrage trades.

This script groups trades by position and shows:
- Entry trades (long and short legs)
- Exit trades (long and short legs)
- Overall PnL calculation (price PnL + funding - fees)
- Summary statistics

Usage:
    # View PnL for all positions for account 'acc1'
    python scripts/view_position_pnl.py --account acc1
    
    # View PnL for a specific symbol
    python scripts/view_position_pnl.py --account acc1 --symbol RESOLV
    
    # View only closed positions
    python scripts/view_position_pnl.py --account acc1 --closed-only
    
    # View only open positions
    python scripts/view_position_pnl.py --account acc1 --open-only
    
    # View specific position by ID
    python scripts/view_position_pnl.py --account acc1 --position-id <uuid>
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from decimal import Decimal
from typing import Optional, List, Dict, Any, Tuple
from uuid import UUID
from datetime import datetime

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box
from rich.text import Text

console = Console()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="View position-level PnL for funding arbitrage trades"
    )
    parser.add_argument(
        "--account",
        "-a",
        type=str,
        required=True,
        help="Account name to query positions for (e.g., 'acc1')",
    )
    parser.add_argument(
        "--symbol",
        type=str,
        default=None,
        help="Filter positions by symbol (optional)",
    )
    parser.add_argument(
        "--position-id",
        type=str,
        default=None,
        help="View specific position by UUID (optional)",
    )
    parser.add_argument(
        "--closed-only",
        action="store_true",
        help="Show only closed positions",
    )
    parser.add_argument(
        "--open-only",
        action="store_true",
        help="Show only open positions",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum number of positions to display (default: 20)",
    )
    parser.add_argument(
        "--env-file",
        type=str,
        default=None,
        help="Path to .env file (default: .env in project root)",
    )
    return parser.parse_args()


async def get_account_id(account_name: str) -> Optional[UUID]:
    """Get account ID from account name."""
    try:
        from databases import Database
    except ImportError:
        console.print("[bold red]Error:[/bold red] 'databases' package not installed")
        sys.exit(1)
    
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        console.print("[bold red]Error:[/bold red] DATABASE_URL not set in environment")
        sys.exit(1)
    
    db = Database(database_url)
    await db.connect()
    
    try:
        account = await db.fetch_one(
            "SELECT id FROM accounts WHERE account_name = :name",
            {"name": account_name}
        )
        if account:
            return account["id"]
        return None
    finally:
        await db.disconnect()


async def get_positions(
    account_id: UUID,
    symbol: Optional[str] = None,
    position_id: Optional[UUID] = None,
    closed_only: bool = False,
    open_only: bool = False,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """Get positions for an account."""
    try:
        from databases import Database
    except ImportError:
        console.print("[bold red]Error:[/bold red] 'databases' package not installed")
        sys.exit(1)
    
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        console.print("[bold red]Error:[/bold red] DATABASE_URL not set in environment")
        sys.exit(1)
    
    db = Database(database_url)
    await db.connect()
    
    try:
        query = """
            SELECT 
                sp.id,
                sp.size_usd,
                sp.opened_at,
                sp.closed_at,
                sp.pnl_usd,
                sp.exit_reason,
                sp.entry_long_rate,
                sp.entry_short_rate,
                sp.entry_divergence,
                sp.cumulative_funding_usd,
                s.symbol as symbol_name,
                d1.name as long_dex,
                d2.name as short_dex
            FROM strategy_positions sp
            JOIN symbols s ON sp.symbol_id = s.id
            JOIN dexes d1 ON sp.long_dex_id = d1.id
            JOIN dexes d2 ON sp.short_dex_id = d2.id
            WHERE sp.account_id = :account_id
        """
        values = {"account_id": account_id}
        
        if position_id:
            query += " AND sp.id = :position_id"
            values["position_id"] = position_id
        elif symbol:
            query += " AND s.symbol = :symbol"
            values["symbol"] = symbol
        
        if closed_only:
            query += " AND sp.closed_at IS NOT NULL"
        elif open_only:
            query += " AND sp.closed_at IS NULL"
        
        query += " ORDER BY sp.opened_at DESC LIMIT :limit"
        values["limit"] = limit
        
        positions = await db.fetch_all(query, values)
        return [dict(pos) for pos in positions]
    finally:
        await db.disconnect()


async def get_trades_for_position(
    position_id: UUID,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Get entry and exit trades for a position."""
    try:
        from databases import Database
        from database.repositories.trade_fill_repository import TradeFillRepository
    except ImportError:
        console.print("[bold red]Error:[/bold red] Failed to import database modules")
        sys.exit(1)
    
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        console.print("[bold red]Error:[/bold red] DATABASE_URL not set in environment")
        sys.exit(1)
    
    db = Database(database_url)
    await db.connect()
    
    try:
        repository = TradeFillRepository(db)
        all_trades = await repository.get_trades_by_position(position_id)
        
        entry_trades = [t for t in all_trades if t.get("trade_type") == "entry"]
        exit_trades = [t for t in all_trades if t.get("trade_type") == "exit"]
        
        return entry_trades, exit_trades
    finally:
        await db.disconnect()


def calculate_position_pnl(
    position: Dict[str, Any],
    entry_trades: List[Dict[str, Any]],
    exit_trades: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Calculate PnL for a position from trades."""
    # Calculate entry fees
    entry_fees = Decimal("0")
    for trade in entry_trades:
        fee = trade.get("total_fee") or Decimal("0")
        entry_fees += Decimal(str(fee))
    
    # Calculate exit fees
    exit_fees = Decimal("0")
    for trade in exit_trades:
        fee = trade.get("total_fee") or Decimal("0")
        exit_fees += Decimal(str(fee))
    
    total_fees = entry_fees + exit_fees
    
    # Calculate price PnL from trades (if available)
    price_pnl_from_trades = Decimal("0")
    for trade in exit_trades:
        pnl = trade.get("realized_pnl")
        if pnl:
            price_pnl_from_trades += Decimal(str(pnl))
    
    # Calculate funding from trades (if available)
    funding_from_trades = Decimal("0")
    for trade in entry_trades + exit_trades:
        funding = trade.get("realized_funding")
        if funding:
            funding_from_trades += Decimal(str(funding))
    
    # Use funding from trades if available, otherwise use cumulative_funding from position
    if funding_from_trades != 0:
        total_funding = funding_from_trades
        funding_source = "trade_history"
    else:
        cumulative_funding = position.get("cumulative_funding_usd") or Decimal("0")
        total_funding = Decimal(str(cumulative_funding))
        funding_source = "database"
    
    # If we have price PnL from trades, use it; otherwise calculate from position
    if price_pnl_from_trades != 0:
        price_pnl = price_pnl_from_trades
        pnl_source = "trade_history"
    else:
        # Fallback to position PnL if available (for closed positions)
        pnl_usd = position.get("pnl_usd")
        if pnl_usd:
            price_pnl = Decimal(str(pnl_usd)) - total_funding + total_fees
            pnl_source = "position_record"
        else:
            price_pnl = Decimal("0")
            pnl_source = "unavailable"
    
    # Net PnL = price PnL + funding - total fees
    net_pnl = price_pnl + total_funding - total_fees
    
    return {
        "entry_fees": entry_fees,
        "exit_fees": exit_fees,
        "total_fees": total_fees,
        "price_pnl": price_pnl,
        "total_funding": total_funding,
        "net_pnl": net_pnl,
        "funding_source": funding_source,
        "pnl_source": pnl_source,
        "entry_trade_count": len(entry_trades),
        "exit_trade_count": len(exit_trades),
    }


def format_position_summary(
    position: Dict[str, Any],
    pnl_data: Dict[str, Any],
    entry_trades: List[Dict[str, Any]],
    exit_trades: List[Dict[str, Any]],
) -> Panel:
    """Format position summary as a panel."""
    symbol = position.get("symbol_name") or position.get("symbol", "N/A")
    long_dex = position.get("long_dex", "N/A").upper()
    short_dex = position.get("short_dex", "N/A").upper()
    size_usd = position.get("size_usd") or Decimal("0")
    
    opened_at = position.get("opened_at")
    closed_at = position.get("closed_at")
    is_closed = closed_at is not None
    
    # Calculate position age
    if opened_at:
        if isinstance(opened_at, str):
            opened_dt = datetime.fromisoformat(opened_at.replace("Z", "+00:00"))
        else:
            opened_dt = opened_at
        
        if is_closed and closed_at:
            if isinstance(closed_at, str):
                closed_dt = datetime.fromisoformat(closed_at.replace("Z", "+00:00"))
            else:
                closed_dt = closed_at
            age = closed_dt - opened_dt.replace(tzinfo=None) if opened_dt.tzinfo else closed_dt - opened_dt
            age_str = f"{age.total_seconds() / 3600:.1f}h"
        else:
            age = datetime.now(opened_dt.tzinfo) - opened_dt if opened_dt.tzinfo else datetime.now() - opened_dt
            age_str = f"{age.total_seconds() / 3600:.1f}h (open)"
    else:
        age_str = "N/A"
    
    # Build summary table
    summary_table = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
    summary_table.add_column("Metric", style="cyan", width=25)
    summary_table.add_column("Value", style="bold", width=30)
    
    summary_table.add_row("Symbol", symbol)
    summary_table.add_row("Long DEX", long_dex)
    summary_table.add_row("Short DEX", short_dex)
    summary_table.add_row("Size (USD)", f"${size_usd:.2f}")
    summary_table.add_row("Status", Text("CLOSED" if is_closed else "OPEN", style="bold green" if not is_closed else "bold yellow"))
    summary_table.add_row("Age", age_str)
    
    if opened_at:
        opened_str = opened_at.strftime("%Y-%m-%d %H:%M:%S") if isinstance(opened_at, datetime) else str(opened_at)
        summary_table.add_row("Opened At", opened_str)
    
    if closed_at:
        closed_str = closed_at.strftime("%Y-%m-%d %H:%M:%S") if isinstance(closed_at, datetime) else str(closed_at)
        summary_table.add_row("Closed At", closed_str)
        exit_reason = position.get("exit_reason")
        if exit_reason:
            summary_table.add_row("Exit Reason", exit_reason)
    
    summary_table.add_row("", "")  # Separator
    
    # Entry/Exit trade counts
    summary_table.add_row("Entry Trades", f"{pnl_data['entry_trade_count']} trades")
    summary_table.add_row("Exit Trades", f"{pnl_data['exit_trade_count']} trades")
    
    summary_table.add_row("", "")  # Separator
    
    # Fees
    summary_table.add_row("Entry Fees", f"${pnl_data['entry_fees']:.4f}")
    summary_table.add_row("Exit Fees", f"${pnl_data['exit_fees']:.4f}")
    summary_table.add_row("Total Fees", Text(f"${pnl_data['total_fees']:.4f}", style="bold red"))
    
    summary_table.add_row("", "")  # Separator
    
    # PnL
    summary_table.add_row("Price PnL", f"${pnl_data['price_pnl']:.2f} ({pnl_data['pnl_source']})")
    summary_table.add_row("Funding", f"${pnl_data['total_funding']:.2f} ({pnl_data['funding_source']})")
    
    # Net PnL with color coding
    net_pnl = pnl_data['net_pnl']
    net_pnl_style = "bold green" if net_pnl > 0 else "bold red" if net_pnl < 0 else "bold white"
    net_pnl_pct = (net_pnl / size_usd * 100) if size_usd > 0 else Decimal("0")
    summary_table.add_row(
        "Net PnL",
        Text(f"${net_pnl:.2f} ({net_pnl_pct:.2f}%)", style=net_pnl_style)
    )
    
    # Position metadata
    entry_divergence = position.get("entry_divergence")
    if entry_divergence:
        summary_table.add_row("", "")  # Separator
        summary_table.add_row("Entry Divergence", f"{Decimal(str(entry_divergence)) * 100:.3f}%")
    
    title = f"[bold cyan]Position: {symbol}[/bold cyan]"
    if is_closed:
        title += f" [dim]({position.get('id')})[/dim]"
    
    return Panel(summary_table, title=title, border_style="cyan")


def format_trades_table(trades: List[Dict[str, Any]], title: str) -> Optional[Table]:
    """Format trades as a table."""
    if not trades:
        return None
    
    table = Table(
        title=title,
        box=box.ROUNDED,
        show_header=True,
        header_style="bold cyan",
    )
    
    table.add_column("DEX", style="yellow", width=10)
    table.add_column("Side", style="green", width=6)
    table.add_column("Quantity", justify="right", width=15)
    table.add_column("Price", justify="right", width=15)
    table.add_column("Value (USD)", justify="right", width=15)
    table.add_column("Fee", justify="right", width=12)
    table.add_column("PnL", justify="right", width=12)
    table.add_column("Funding", justify="right", width=12)
    
    for trade in trades:
        dex_name = trade.get("dex_name", "N/A").upper()
        side = trade.get("side", "N/A").upper()
        
        quantity = trade.get("total_quantity")
        quantity_str = f"{quantity:.8f}" if quantity else "N/A"
        
        price = trade.get("weighted_avg_price")
        price_str = f"${price:.6f}" if price else "N/A"
        
        if quantity and price:
            value = float(quantity) * float(price)
            value_str = f"${value:.2f}"
        else:
            value_str = "N/A"
        
        fee = trade.get("total_fee")
        fee_currency = trade.get("fee_currency", "USD")
        fee_str = f"{fee:.4f} {fee_currency}" if fee else "N/A"
        
        realized_pnl = trade.get("realized_pnl")
        pnl_str = f"${realized_pnl:.2f}" if realized_pnl else "-"
        
        realized_funding = trade.get("realized_funding")
        funding_str = f"${realized_funding:.2f}" if realized_funding else "-"
        
        table.add_row(
            dex_name,
            side,
            quantity_str,
            price_str,
            value_str,
            fee_str,
            pnl_str,
            funding_str,
        )
    
    return table


async def main():
    args = parse_args()
    
    # Load environment variables
    env_file = args.env_file or PROJECT_ROOT / ".env"
    if env_file.exists():
        load_dotenv(env_file)
    
    console.print(Panel.fit(
        f"[bold cyan]Position PnL Analysis[/bold cyan] | Account: [bold yellow]{args.account}[/bold yellow]",
        border_style="cyan"
    ))
    console.print()
    
    # Get account ID
    account_id = await get_account_id(args.account)
    if not account_id:
        console.print(f"[bold red]Error:[/bold red] Account '[cyan]{args.account}[/cyan]' not found")
        sys.exit(1)
    
    # Get positions
    position_id = UUID(args.position_id) if args.position_id else None
    positions = await get_positions(
        account_id=account_id,
        symbol=args.symbol,
        position_id=position_id,
        closed_only=args.closed_only,
        open_only=args.open_only,
        limit=args.limit,
    )
    
    if not positions:
        console.print("[yellow]No positions found matching the criteria[/yellow]")
        sys.exit(0)
    
    console.print(f"[green]✓[/green] Found [bold cyan]{len(positions)}[/bold cyan] position(s)\n")
    
    # Process each position
    for idx, position in enumerate(positions, 1):
        if idx > 1:
            console.print()  # Separator between positions
        
        position_id = position["id"]
        
        # Get trades for this position
        entry_trades, exit_trades = await get_trades_for_position(position_id)
        
        # Calculate PnL
        pnl_data = calculate_position_pnl(position, entry_trades, exit_trades)
        
        # Display position summary
        summary_panel = format_position_summary(position, pnl_data, entry_trades, exit_trades)
        console.print(summary_panel)
        
        # Display entry trades
        if entry_trades:
            entry_table = format_trades_table(entry_trades, "Entry Trades")
            if entry_table:
                console.print()
                console.print(entry_table)
        
        # Display exit trades
        if exit_trades:
            exit_table = format_trades_table(exit_trades, "Exit Trades")
            if exit_table:
                console.print()
                console.print(exit_table)
        elif not position.get("closed_at"):
            console.print()
            console.print("[dim]No exit trades yet (position still open)[/dim]")


if __name__ == "__main__":
    asyncio.run(main())

