#!/usr/bin/env python3
"""
Cleanup Stale Market Data

Remove market data (volume/OI) records that are older than the specified threshold.
Helps keep the database clean by removing outdated data from exchanges that are
no longer being collected.

Usage:
    python cleanup_stale_market_data.py                    # Show what would be deleted (dry-run)
    python cleanup_stale_market_data.py --execute          # Actually delete stale records
    python cleanup_stale_market_data.py --age-minutes 120  # Use 120 minutes threshold
    python cleanup_stale_market_data.py --exchange lighter # Clean specific exchange only
"""

import asyncio
import sys
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Optional, Dict, List

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich import box
from rich.prompt import Confirm

from database.connection import database

console = Console()

# Exchanges to exclude from cleanup (e.g., EdgeX which is no longer collected)
EXCLUDED_EXCHANGES = {"edgex"}


async def find_stale_records(
    age_minutes: int = 60,
    exchange_filter: Optional[str] = None
) -> Dict:
    """
    Find stale market data records
    
    Args:
        age_minutes: Minimum age in minutes to consider stale (default: 60)
        exchange_filter: Optional exchange to filter (e.g., "lighter")
        
    Returns:
        Dictionary with stale records grouped by exchange
    """
    await database.connect()
    
    try:
        age_threshold = datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
        
        # Build query to find stale records
        query = """
            SELECT 
                d.name as dex_name,
                s.symbol,
                ds.volume_24h,
                ds.open_interest_usd,
                ds.updated_at,
                EXTRACT(EPOCH FROM (NOW() - ds.updated_at)) / 60 as age_minutes
            FROM dex_symbols ds
            JOIN dexes d ON ds.dex_id = d.id
            JOIN symbols s ON ds.symbol_id = s.id
            WHERE ds.updated_at IS NOT NULL
            AND ds.updated_at < :age_threshold
            AND d.is_active = TRUE
        """
        
        params = {"age_threshold": age_threshold}
        
        # Exclude EdgeX and other excluded exchanges
        excluded_list = list(EXCLUDED_EXCHANGES)
        if excluded_list:
            placeholders = ','.join([f":excluded_{i}" for i in range(len(excluded_list))])
            query += f" AND d.name NOT IN ({placeholders})"
            for i, dex in enumerate(excluded_list):
                params[f"excluded_{i}"] = dex.lower()
        
        if exchange_filter:
            query += " AND d.name = :exchange_filter"
            params["exchange_filter"] = exchange_filter.lower()
        
        query += " ORDER BY d.name, ds.updated_at ASC"
        
        rows = await database.fetch_all(query, values=params)
        
        # Group by exchange
        by_exchange: Dict[str, List[Dict]] = {}
        for row in rows:
            dex_name = row['dex_name'].lower()
            if dex_name not in by_exchange:
                by_exchange[dex_name] = []
            
            by_exchange[dex_name].append({
                'symbol': row['symbol'],
                'volume_24h': row['volume_24h'],
                'open_interest_usd': row['open_interest_usd'],
                'updated_at': row['updated_at'],
                'age_minutes': float(row['age_minutes'])
            })
        
        return {
            'records': by_exchange,
            'total_count': len(rows),
            'age_threshold_minutes': age_minutes,
            'age_threshold': age_threshold
        }
        
    finally:
        await database.disconnect()


async def delete_stale_records(
    age_minutes: int = 60,
    exchange_filter: Optional[str] = None
) -> Dict:
    """
    Delete stale market data records
    
    Args:
        age_minutes: Minimum age in minutes to consider stale (default: 60)
        exchange_filter: Optional exchange to filter (e.g., "lighter")
        
    Returns:
        Dictionary with deletion results
    """
    await database.connect()
    
    try:
        age_threshold = datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
        
        # Build delete query
        query = """
            UPDATE dex_symbols ds
            SET 
                volume_24h = NULL,
                open_interest_usd = NULL,
                updated_at = NULL
            FROM dexes d, symbols s
            WHERE ds.dex_id = d.id
            AND ds.symbol_id = s.id
            AND ds.updated_at IS NOT NULL
            AND ds.updated_at < :age_threshold
            AND d.is_active = TRUE
        """
        
        params = {"age_threshold": age_threshold}
        
        # Exclude EdgeX and other excluded exchanges
        excluded_list = list(EXCLUDED_EXCHANGES)
        if excluded_list:
            placeholders = ','.join([f":excluded_{i}" for i in range(len(excluded_list))])
            query += f" AND d.name NOT IN ({placeholders})"
            for i, dex in enumerate(excluded_list):
                params[f"excluded_{i}"] = dex.lower()
        
        if exchange_filter:
            query += " AND d.name = :exchange_filter"
            params["exchange_filter"] = exchange_filter.lower()
        
        # Execute deletion
        result = await database.execute(query, values=params)
        
        return {
            'deleted_count': result,
            'age_threshold_minutes': age_minutes,
            'age_threshold': age_threshold
        }
        
    finally:
        await database.disconnect()


def format_age_minutes(age_minutes: float) -> str:
    """Format age in minutes to human-readable string"""
    if age_minutes < 60:
        return f"{int(age_minutes)}m"
    elif age_minutes < 1440:  # < 24 hours
        hours = int(age_minutes / 60)
        mins = int(age_minutes % 60)
        return f"{hours}h {mins}m"
    else:
        days = int(age_minutes / 1440)
        hours = int((age_minutes % 1440) / 60)
        return f"{days}d {hours}h"


def create_stale_records_table(records_by_exchange: Dict[str, List[Dict]]) -> Table:
    """Create table showing stale records"""
    table = Table(title="🗑️  Stale Records to be Cleaned", box=box.ROUNDED)
    table.add_column("Exchange", style="cyan", no_wrap=True)
    table.add_column("Symbol", style="white", no_wrap=True)
    table.add_column("Volume 24h", style="green", justify="right")
    table.add_column("OI USD", style="blue", justify="right")
    table.add_column("Age", style="yellow", justify="right")
    table.add_column("Last Updated", style="dim", justify="right")
    
    for exchange in sorted(records_by_exchange.keys()):
        records = records_by_exchange[exchange]
        for record in records[:20]:  # Show first 20 per exchange
            volume_str = f"${record['volume_24h']:,.0f}" if record['volume_24h'] else "N/A"
            oi_str = f"${record['open_interest_usd']:,.0f}" if record['open_interest_usd'] else "N/A"
            age_str = format_age_minutes(record['age_minutes'])
            
            updated_at = record['updated_at']
            if updated_at:
                if updated_at.tzinfo is None:
                    updated_at_utc = updated_at
                else:
                    updated_at_utc = updated_at.astimezone(timezone.utc).replace(tzinfo=None)
                updated_str = updated_at_utc.strftime("%Y-%m-%d %H:%M:%S")
            else:
                updated_str = "N/A"
            
            table.add_row(
                exchange.upper(),
                record['symbol'],
                volume_str,
                oi_str,
                age_str,
                updated_str
            )
        
        # Show summary if more than 20 records
        if len(records) > 20:
            table.add_row(
                "",
                f"... and {len(records) - 20} more",
                "",
                "",
                "",
                "",
                style="dim"
            )
    
    return table


def create_summary_table(stale_data: Dict) -> Table:
    """Create summary table"""
    table = Table(title="📊 Cleanup Summary", box=box.ROUNDED)
    table.add_column("Metric", style="cyan", no_wrap=True)
    table.add_column("Value", style="magenta", justify="right")
    
    table.add_row("Age Threshold", f"{stale_data['age_threshold_minutes']} minutes")
    table.add_row("Total Stale Records", str(stale_data['total_count']))
    
    if stale_data['records']:
        table.add_row("", "")
        table.add_row("By Exchange:", "")
        for exchange, records in sorted(stale_data['records'].items()):
            table.add_row(f"  {exchange.upper()}", str(len(records)))
    
    return table


async def main():
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Cleanup stale market data (volume/OI) from database",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python cleanup_stale_market_data.py                    # Dry-run: show what would be deleted
  python cleanup_stale_market_data.py --execute           # Actually delete stale records
  python cleanup_stale_market_data.py --age-minutes 120   # Use 120 minutes threshold
  python cleanup_stale_market_data.py --exchange lighter # Clean specific exchange only
  python cleanup_stale_market_data.py --execute --age-minutes 60  # Delete records >60min old

Note: EdgeX is excluded from cleanup by default (not being collected anymore).
        """
    )
    
    parser.add_argument(
        '--execute',
        action='store_true',
        help='Actually delete stale records (default: dry-run mode)'
    )
    
    parser.add_argument(
        '--age-minutes',
        type=int,
        default=60,
        help='Minimum age in minutes to consider stale (default: 60)'
    )
    
    parser.add_argument(
        '--exchange',
        help='Clean specific exchange only (e.g., lighter, paradex)'
    )
    
    args = parser.parse_args()
    
    console.print("\n[bold cyan]🔍 Finding Stale Market Data...[/bold cyan]\n")
    
    try:
        # Find stale records
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console
        ) as progress:
            task = progress.add_task("Querying database...", total=None)
            stale_data = await find_stale_records(
                age_minutes=args.age_minutes,
                exchange_filter=args.exchange
            )
            progress.update(task, completed=True)
        
        if stale_data['total_count'] == 0:
            console.print(Panel(
                "[green]✅ No stale records found![/green]\n"
                f"   All market data is fresher than {args.age_minutes} minutes",
                title="Cleanup Status",
                border_style="green"
            ))
            return
        
        # Show summary
        console.print(create_summary_table(stale_data))
        console.print()
        
        # Show sample records
        if stale_data['records']:
            console.print(create_stale_records_table(stale_data['records']))
            console.print()
        
        # Show excluded exchanges
        if EXCLUDED_EXCHANGES:
            console.print(Panel(
                f"[yellow]Excluded exchanges: {', '.join([e.upper() for e in EXCLUDED_EXCHANGES])}[/yellow]\n"
                "   These exchanges are not being cleaned up",
                title="Exclusions",
                border_style="yellow"
            ))
            console.print()
        
        if not args.execute:
            console.print(Panel(
                "[yellow]⚠️  DRY-RUN MODE[/yellow]\n"
                f"   Found {stale_data['total_count']} stale records to clean\n"
                "   Run with --execute to actually delete them",
                title="Dry Run",
                border_style="yellow"
            ))
        else:
            # Confirm deletion
            console.print(Panel(
                f"[red]⚠️  WARNING: This will delete {stale_data['total_count']} stale records[/red]\n"
                f"   Records older than {args.age_minutes} minutes will be cleaned\n"
                "   This action cannot be undone!",
                title="Deletion Confirmation",
                border_style="red"
            ))
            
            if not Confirm.ask("\n[bold red]Are you sure you want to proceed?[/bold red]", default=False):
                console.print("[yellow]❌ Cancelled by user[/yellow]")
                return
            
            # Execute deletion
            console.print("\n[bold cyan]🗑️  Deleting stale records...[/bold cyan]\n")
            
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console
            ) as progress:
                task = progress.add_task("Deleting...", total=None)
                result = await delete_stale_records(
                    age_minutes=args.age_minutes,
                    exchange_filter=args.exchange
                )
                progress.update(task, completed=True)
            
            console.print(Panel(
                f"[green]✅ Successfully cleaned {result['deleted_count']} stale records[/green]\n"
                f"   Records older than {result['age_threshold_minutes']} minutes have been removed",
                title="Cleanup Complete",
                border_style="green"
            ))
        
    except Exception as e:
        console.print(f"[red]❌ Error: {e}[/red]")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        console.print("\n\n[yellow]👋 Cancelled by user[/yellow]")
    except Exception as e:
        console.print(f"\n[red]❌ Error: {e}[/red]")
        import traceback
        traceback.print_exc()
        sys.exit(1)

