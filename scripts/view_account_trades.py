#!/usr/bin/env python3
"""
View latest trades for a specific account from the trade_fills table.

Usage:
    # View latest 50 trades for account 'acc1'
    python scripts/view_account_trades.py --account acc1
    
    # View latest 100 trades for account 'acc1'
    python scripts/view_account_trades.py --account acc1 --limit 100
    
    # View trades for a specific symbol
    python scripts/view_account_trades.py --account acc1 --symbol RESOLV
    
    # View only entry trades
    python scripts/view_account_trades.py --account acc1 --trade-type entry
    
    # View only exit trades
    python scripts/view_account_trades.py --account acc1 --trade-type exit
    
    # View trades for a specific DEX
    python scripts/view_account_trades.py --account acc1 --dex lighter
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from decimal import Decimal
from typing import Optional, List, Dict, Any
from uuid import UUID

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
        description="View latest trades for a specific account from trade_fills table"
    )
    parser.add_argument(
        "--account",
        "-a",
        type=str,
        required=True,
        help="Account name to query trades for (e.g., 'acc1')",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum number of trades to display (default: 50)",
    )
    parser.add_argument(
        "--symbol",
        type=str,
        default=None,
        help="Filter trades by symbol (optional)",
    )
    parser.add_argument(
        "--dex",
        type=str,
        default=None,
        choices=["lighter", "aster", "paradex", "backpack"],
        help="Filter trades by DEX (optional)",
    )
    parser.add_argument(
        "--trade-type",
        type=str,
        default=None,
        choices=["entry", "exit"],
        help="Filter by trade type: 'entry' or 'exit' (optional)",
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


async def get_trades_for_account(
    account_id: UUID,
    limit: int = 50,
    symbol: Optional[str] = None,
    dex_name: Optional[str] = None,
    trade_type: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Get trades for an account from trade_fills table."""
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
        
        # Use the repository method that supports filtering
        trades = await repository.get_trades_by_account(
            account_id=account_id,
            symbol=symbol,
            trade_type=trade_type,
            limit=limit * 2 if not symbol and not trade_type else limit,  # Get more if we need to filter by DEX
        )
        
        # Apply DEX filter if specified (not supported by repository method)
        if dex_name:
            filtered_trades = [
                t for t in trades
                if t.get("dex_name", "").lower() == dex_name.lower()
            ]
            return filtered_trades[:limit]
        
        return trades[:limit]
    finally:
        await db.disconnect()


def format_trade_table(trades: List[Dict[str, Any]]) -> Table:
    """Format trades as a rich table."""
    table = Table(
        title="Account Trades",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold cyan",
    )
    
    table.add_column("Timestamp", style="dim", width=20)
    table.add_column("Symbol", style="cyan", width=12)
    table.add_column("DEX", style="yellow", width=10)
    table.add_column("Type", style="magenta", width=8)
    table.add_column("Side", style="green", width=6)
    table.add_column("Quantity", justify="right", width=15)
    table.add_column("Price", justify="right", width=15)
    table.add_column("Value (USD)", justify="right", width=15)
    table.add_column("Fee", justify="right", width=12)
    table.add_column("PnL", justify="right", width=12)
    table.add_column("Funding", justify="right", width=12)
    
    for trade in trades:
        timestamp = trade.get("timestamp")
        if timestamp:
            timestamp_str = timestamp.strftime("%Y-%m-%d %H:%M:%S")
        else:
            timestamp_str = "N/A"
        
        symbol = trade.get("symbol", "N/A")
        dex_name = trade.get("dex_name", "N/A")
        trade_type = trade.get("trade_type", "N/A")
        side = trade.get("side", "N/A")
        
        quantity = trade.get("total_quantity")
        quantity_str = f"{quantity:.8f}" if quantity else "N/A"
        
        price = trade.get("weighted_avg_price")
        price_str = f"${price:.6f}" if price else "N/A"
        
        # Calculate value
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
        
        # Color code trade type
        type_style = "bold green" if trade_type == "entry" else "bold red"
        
        table.add_row(
            timestamp_str,
            symbol,
            dex_name.upper(),
            Text(trade_type.upper(), style=type_style),
            side.upper(),
            quantity_str,
            price_str,
            value_str,
            fee_str,
            pnl_str,
            funding_str,
        )
    
    return table


def format_summary(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Calculate summary statistics."""
    total_trades = len(trades)
    entry_trades = [t for t in trades if t.get("trade_type") == "entry"]
    exit_trades = [t for t in trades if t.get("trade_type") == "exit"]
    
    total_entry_value = Decimal("0")
    total_exit_value = Decimal("0")
    total_entry_fees = Decimal("0")
    total_exit_fees = Decimal("0")
    total_pnl = Decimal("0")
    total_funding = Decimal("0")
    
    for trade in trades:
        quantity = trade.get("total_quantity") or Decimal("0")
        price = trade.get("weighted_avg_price") or Decimal("0")
        fee = trade.get("total_fee") or Decimal("0")
        pnl = trade.get("realized_pnl") or Decimal("0")
        funding = trade.get("realized_funding") or Decimal("0")
        
        value = quantity * price
        
        if trade.get("trade_type") == "entry":
            total_entry_value += value
            total_entry_fees += fee
        else:
            total_exit_value += value
            total_exit_fees += fee
        
        total_pnl += pnl
        total_funding += funding
    
    return {
        "total_trades": total_trades,
        "entry_trades": len(entry_trades),
        "exit_trades": len(exit_trades),
        "total_entry_value": total_entry_value,
        "total_exit_value": total_exit_value,
        "total_entry_fees": total_entry_fees,
        "total_exit_fees": total_exit_fees,
        "total_fees": total_entry_fees + total_exit_fees,
        "total_pnl": total_pnl,
        "total_funding": total_funding,
        "net_pnl": total_pnl + total_funding - (total_entry_fees + total_exit_fees),
    }


async def main():
    args = parse_args()
    
    # Load environment variables
    env_file = args.env_file or PROJECT_ROOT / ".env"
    if env_file.exists():
        load_dotenv(env_file)
    
    console.print(Panel.fit(
        f"[bold cyan]Viewing Trades for Account:[/bold cyan] [bold yellow]{args.account}[/bold yellow]",
        border_style="cyan"
    ))
    console.print()
    
    # Get account ID
    console.print(f"[dim]Looking up account ID for '{args.account}'...[/dim]")
    account_id = await get_account_id(args.account)
    
    if not account_id:
        console.print(f"[bold red]Error:[/bold red] Account '[cyan]{args.account}[/cyan]' not found in database")
        console.print("\n[yellow]Available accounts:[/yellow]")
        console.print("  Run: [dim]python database/scripts/accounts/list_accounts.py[/dim]")
        sys.exit(1)
    
    console.print(f"[green]✓[/green] Found account ID: [cyan]{account_id}[/cyan]\n")
    
    # Build filter description
    filters = []
    if args.symbol:
        filters.append(f"Symbol: {args.symbol}")
    if args.dex:
        filters.append(f"DEX: {args.dex}")
    if args.trade_type:
        filters.append(f"Type: {args.trade_type}")
    
    if filters:
        console.print(f"[dim]Filters: {', '.join(filters)}[/dim]\n")
    
    # Get trades
    console.print(f"[dim]Fetching latest {args.limit} trades...[/dim]")
    trades = await get_trades_for_account(
        account_id=account_id,
        limit=args.limit,
        symbol=args.symbol,
        dex_name=args.dex,
        trade_type=args.trade_type,
    )
    
    if not trades:
        console.print("[yellow]No trades found matching the criteria[/yellow]")
        sys.exit(0)
    
    console.print(f"[green]✓[/green] Found [bold cyan]{len(trades)}[/bold cyan] trades\n")
    
    # Display summary
    summary = format_summary(trades)
    summary_table = Table(box=box.SIMPLE, show_header=False)
    summary_table.add_column("Metric", style="cyan", width=25)
    summary_table.add_column("Value", style="bold", width=20)
    
    summary_table.add_row("Total Trades", str(summary["total_trades"]))
    summary_table.add_row("Entry Trades", str(summary["entry_trades"]))
    summary_table.add_row("Exit Trades", str(summary["exit_trades"]))
    summary_table.add_row("Total Entry Value", f"${summary['total_entry_value']:.2f}")
    summary_table.add_row("Total Exit Value", f"${summary['total_exit_value']:.2f}")
    summary_table.add_row("Total Entry Fees", f"${summary['total_entry_fees']:.4f}")
    summary_table.add_row("Total Exit Fees", f"${summary['total_exit_fees']:.4f}")
    summary_table.add_row("Total Fees", f"${summary['total_fees']:.4f}")
    summary_table.add_row("Total PnL", f"${summary['total_pnl']:.2f}")
    summary_table.add_row("Total Funding", f"${summary['total_funding']:.2f}")
    summary_table.add_row("Net PnL", f"${summary['net_pnl']:.2f}")
    
    console.print(Panel(summary_table, title="[bold cyan]Summary[/bold cyan]", border_style="cyan"))
    console.print()
    
    # Display trades table
    trade_table = format_trade_table(trades)
    console.print(trade_table)
    
    console.print()
    console.print(f"[dim]Showing {len(trades)} of {summary['total_trades']} total trades[/dim]")


if __name__ == "__main__":
    asyncio.run(main())

