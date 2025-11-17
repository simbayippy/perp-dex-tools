#!/usr/bin/env python3
"""
Cleanup script to delete old/inaccurate trade fills and positions from database.

This script allows you to remove trade data before a certain timestamp.
Use with caution - deleted data cannot be recovered!

Usage:
    # Delete trades and positions before 2 hours ago
    python scripts/cleanup_old_trades.py --hours-ago 2 --account acc1
    
    # Delete trades and positions before a specific timestamp
    python scripts/cleanup_old_trades.py --before "2025-11-17T15:00:00Z" --account acc1
    
    # Dry run (show what would be deleted without actually deleting)
    python scripts/cleanup_old_trades.py --hours-ago 2 --account acc1 --dry-run
    
    # Delete for all accounts
    python scripts/cleanup_old_trades.py --hours-ago 2 --all-accounts
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Optional
from datetime import datetime, timedelta, timezone
from uuid import UUID

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box
from rich.prompt import Confirm

console = Console()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Cleanup old/inaccurate trade fills and positions from database"
    )
    parser.add_argument(
        "--hours-ago",
        type=float,
        default=None,
        help="Delete trades/positions older than this many hours (e.g., 2 for 2 hours ago)",
    )
    parser.add_argument(
        "--before",
        type=str,
        default=None,
        help="Delete trades/positions before this timestamp (ISO 8601 format, e.g., '2025-11-17T15:00:00Z')",
    )
    parser.add_argument(
        "--account",
        type=str,
        default=None,
        help="Account name to cleanup (e.g., 'acc1'). Required unless --all-accounts is used.",
    )
    parser.add_argument(
        "--all-accounts",
        action="store_true",
        help="Cleanup for all accounts (use with caution!)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be deleted without actually deleting",
    )
    parser.add_argument(
        "--env-file",
        type=str,
        default=".env",
        help="Path to .env file (default: .env)",
    )
    return parser.parse_args()


async def get_account_id(account_name: str, db) -> Optional[UUID]:
    """Get account ID from account name."""
    row = await db.fetch_one(
        "SELECT id FROM accounts WHERE account_name = :name",
        {"name": account_name}
    )
    return row["id"] if row else None


async def get_cutoff_time(args: argparse.Namespace) -> datetime:
    """Calculate cutoff time from arguments."""
    if args.before:
        # Parse ISO 8601 timestamp
        cutoff_str = args.before.replace("Z", "+00:00")
        return datetime.fromisoformat(cutoff_str)
    elif args.hours_ago:
        return datetime.now(timezone.utc) - timedelta(hours=args.hours_ago)
    else:
        console.print("[bold red]Error:[/bold red] Must specify either --hours-ago or --before")
        sys.exit(1)


async def count_trades_to_delete(db, account_id: Optional[UUID], cutoff_time: datetime) -> int:
    """Count trade fills that would be deleted."""
    query = "SELECT COUNT(*) as count FROM trade_fills WHERE timestamp < :cutoff_time"
    values = {"cutoff_time": cutoff_time.replace(tzinfo=None) if cutoff_time.tzinfo else cutoff_time}
    
    if account_id:
        query += " AND account_id = :account_id"
        values["account_id"] = account_id
    
    row = await db.fetch_one(query, values)
    return row["count"] if row else 0


async def count_positions_to_delete(db, account_id: Optional[UUID], cutoff_time: datetime) -> int:
    """Count positions that would be deleted."""
    query = "SELECT COUNT(*) as count FROM strategy_positions WHERE opened_at < :cutoff_time"
    values = {"cutoff_time": cutoff_time.replace(tzinfo=None) if cutoff_time.tzinfo else cutoff_time}
    
    if account_id:
        query += " AND account_id = :account_id"
        values["account_id"] = account_id
    
    row = await db.fetch_one(query, values)
    return row["count"] if row else 0


async def delete_trades(db, account_id: Optional[UUID], cutoff_time: datetime) -> int:
    """Delete trade fills before cutoff time."""
    query = "DELETE FROM trade_fills WHERE timestamp < :cutoff_time"
    values = {"cutoff_time": cutoff_time.replace(tzinfo=None) if cutoff_time.tzinfo else cutoff_time}
    
    if account_id:
        query += " AND account_id = :account_id"
        values["account_id"] = account_id
    
    result = await db.execute(query, values)
    return result


async def delete_positions(db, account_id: Optional[UUID], cutoff_time: datetime) -> int:
    """Delete positions opened before cutoff time."""
    query = "DELETE FROM strategy_positions WHERE opened_at < :cutoff_time"
    values = {"cutoff_time": cutoff_time.replace(tzinfo=None) if cutoff_time.tzinfo else cutoff_time}
    
    if account_id:
        query += " AND account_id = :account_id"
        values["account_id"] = account_id
    
    result = await db.execute(query, values)
    return result


async def main():
    args = parse_args()
    
    # Load environment variables
    load_dotenv(args.env_file)
    
    # Validate arguments
    if not args.all_accounts and not args.account:
        console.print("[bold red]Error:[/bold red] Must specify either --account or --all-accounts")
        sys.exit(1)
    
    # Get cutoff time
    cutoff_time = await get_cutoff_time(args)
    
    # Connect to database
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
        # Get account ID if specified
        account_id = None
        account_name = None
        if args.account:
            account_id = await get_account_id(args.account, db)
            if not account_id:
                console.print(f"[bold red]Error:[/bold red] Account '{args.account}' not found")
                sys.exit(1)
            account_name = args.account
        
        # Count what would be deleted
        trades_count = await count_trades_to_delete(db, account_id, cutoff_time)
        positions_count = await count_positions_to_delete(db, account_id, cutoff_time)
        
        # Show summary
        cutoff_str = cutoff_time.strftime("%Y-%m-%d %H:%M:%S UTC")
        scope = f"account '{account_name}'" if account_name else "all accounts"
        
        summary_table = Table(title="Cleanup Summary", box=box.ROUNDED, show_header=True, header_style="bold cyan")
        summary_table.add_column("Item", style="cyan")
        summary_table.add_column("Count", style="yellow", justify="right")
        
        summary_table.add_row("Trade Fills", str(trades_count))
        summary_table.add_row("Positions", str(positions_count))
        
        console.print()
        console.print(Panel(
            f"[bold cyan]Scope:[/bold cyan] {scope}\n"
            f"[bold cyan]Cutoff Time:[/bold cyan] {cutoff_str}\n"
            f"[bold yellow]Mode:[/bold yellow] {'DRY RUN (no changes)' if args.dry_run else 'DELETE (permanent)'}",
            title="[bold]Cleanup Preview[/bold]",
            border_style="cyan" if args.dry_run else "red"
        ))
        console.print()
        console.print(summary_table)
        console.print()
        
        if trades_count == 0 and positions_count == 0:
            console.print("[green]✓[/green] No data to delete!")
            return
        
        # Confirm deletion
        if args.dry_run:
            console.print("[yellow]⚠️[/yellow]  [bold]DRY RUN MODE[/bold] - No data will be deleted")
            console.print("Remove --dry-run flag to actually delete the data")
            return
        
        if not Confirm.ask(f"[bold red]⚠️  PERMANENTLY DELETE[/bold red] {trades_count} trade fills and {positions_count} positions?", default=False):
            console.print("[yellow]Cancelled[/yellow]")
            return
        
        # Perform deletion
        console.print("\n[dim]Deleting data...[/dim]")
        
        deleted_trades = await delete_trades(db, account_id, cutoff_time)
        deleted_positions = await delete_positions(db, account_id, cutoff_time)
        
        console.print(f"[green]✓[/green] Deleted {deleted_trades} trade fills")
        console.print(f"[green]✓[/green] Deleted {deleted_positions} positions")
        console.print("\n[bold green]Cleanup complete![/bold green]")
        
    finally:
        await db.disconnect()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        console.print("\n\n[yellow]⚠️[/yellow]  [bold]Cleanup interrupted by user[/bold]")
        sys.exit(1)
    except Exception as e:
        console.print(f"\n\n[bold red]❌ Cleanup failed:[/bold red] {e}")
        import traceback
        console.print(f"[dim]{traceback.format_exc()}[/dim]")
        sys.exit(1)

