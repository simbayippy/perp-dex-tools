#!/usr/bin/env python3
"""
Remove Exchange Funding Rate Data

Remove funding rate data for a specific exchange from the database.
This removes the data that appears in view_all_exchange_data.py:
- Latest funding rates (latest_funding_rates table)
- DEX symbol mappings and market data (dex_symbols table)

This does NOT remove:
- Opportunities (preserved)
- Historical funding rates (optional, can remove with --include-historical)
- Collection logs (preserved)

This is useful for removing outdated exchanges that are no longer being collected.

Usage:
    python remove_funding_exchange.py --exchange edgex                    # Remove EdgeX funding data
    python remove_funding_exchange.py --exchange edgex --dry-run          # Preview what would be removed
    python remove_funding_exchange.py --exchange edgex --include-historical  # Also remove historical funding rates
    python remove_funding_exchange.py --exchange edgex --inactive-only    # Just mark exchange as inactive
"""

import asyncio
import sys
from pathlib import Path
from typing import Optional, Dict

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from dotenv import load_dotenv
load_dotenv()

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Confirm
from rich import box

from database.connection import database

console = Console()


async def get_exchange_stats(exchange_name: str) -> Dict:
    """
    Get statistics about exchange data in the database
    
    Args:
        exchange_name: Exchange name (e.g., "edgex")
        
    Returns:
        Dictionary with statistics
    """
    # Get dex_id
    dex_query = "SELECT id, name, display_name, is_active FROM dexes WHERE name = :dex_name"
    dex_row = await database.fetch_one(dex_query, values={"dex_name": exchange_name.lower()})
    
    if not dex_row:
        return {
            'found': False,
            'error': f"Exchange '{exchange_name}' not found",
            'stats': {}
        }
    
    dex_id = dex_row['id']
    
    # Count records in each table (only what shows in view_all_exchange_data.py)
    stats = {
        'dex_id': dex_id,
        'dex_name': dex_row['name'],
        'dex_display_name': dex_row['display_name'],
        'is_active': dex_row['is_active'],
        'latest_funding_rates': 0,
        'dex_symbols': 0,
        'historical_funding_rates': 0
    }
    
    # Count latest_funding_rates (shown in view_all_exchange_data.py)
    lfr_count_query = "SELECT COUNT(*) as count FROM latest_funding_rates WHERE dex_id = :dex_id"
    lfr_count_row = await database.fetch_one(lfr_count_query, values={"dex_id": dex_id})
    stats['latest_funding_rates'] = lfr_count_row['count'] if lfr_count_row else 0
    
    # Count dex_symbols (shown in view_all_exchange_data.py)
    ds_count_query = "SELECT COUNT(*) as count FROM dex_symbols WHERE dex_id = :dex_id"
    ds_count_row = await database.fetch_one(ds_count_query, values={"dex_id": dex_id})
    stats['dex_symbols'] = ds_count_row['count'] if ds_count_row else 0
    
    # Count historical funding_rates (optional - not shown in view_all_exchange_data.py)
    fr_count_query = "SELECT COUNT(*) as count FROM funding_rates WHERE dex_id = :dex_id"
    fr_count_row = await database.fetch_one(fr_count_query, values={"dex_id": dex_id})
    stats['historical_funding_rates'] = fr_count_row['count'] if fr_count_row else 0
    
    return {
        'found': True,
        'stats': stats
    }


async def remove_exchange_data(
    exchange_name: str,
    include_historical: bool = False,
    inactive_only: bool = False
) -> Dict:
    """
    Remove funding rate data for an exchange (what shows in view_all_exchange_data.py)
    
    Args:
        exchange_name: Exchange name (e.g., "edgex")
        include_historical: If True, also remove historical funding_rates data
        inactive_only: If True, only mark exchange as inactive, don't delete data
        
    Returns:
        Dictionary with removal results
    """
    # Get dex_id
    dex_query = "SELECT id FROM dexes WHERE name = :dex_name"
    dex_row = await database.fetch_one(dex_query, values={"dex_name": exchange_name.lower()})
    
    if not dex_row:
        return {
            'success': False,
            'error': f"Exchange '{exchange_name}' not found"
        }
    
    dex_id = dex_row['id']
    
    removed = {
        'latest_funding_rates': 0,
        'dex_symbols': 0,
        'historical_funding_rates': 0,
        'dex_marked_inactive': False,
        'dex_deleted': False
    }
    
    if inactive_only:
        # Just mark as inactive
        update_query = "UPDATE dexes SET is_active = FALSE WHERE id = :dex_id"
        await database.execute(update_query, values={"dex_id": dex_id})
        removed['dex_marked_inactive'] = True
        return {
            'success': True,
            'removed': removed
        }
    
    # Delete latest_funding_rates (what shows in view_all_exchange_data.py)
    delete_lfr_query = "DELETE FROM latest_funding_rates WHERE dex_id = :dex_id"
    result = await database.execute(delete_lfr_query, values={"dex_id": dex_id})
    removed['latest_funding_rates'] = result
    
    # Delete dex_symbols (market data: volume, OI, spread - what shows in view_all_exchange_data.py)
    delete_ds_query = "DELETE FROM dex_symbols WHERE dex_id = :dex_id"
    result = await database.execute(delete_ds_query, values={"dex_id": dex_id})
    removed['dex_symbols'] = result
    
    # Delete historical funding_rates (optional - not shown in view_all_exchange_data.py)
    if include_historical:
        delete_fr_query = "DELETE FROM funding_rates WHERE dex_id = :dex_id"
        result = await database.execute(delete_fr_query, values={"dex_id": dex_id})
        removed['historical_funding_rates'] = result
    
    # Note: We do NOT delete:
    # - Opportunities (preserved - they may still be useful)
    # - Collection logs (preserved - historical record)
    # - Exchange entry (preserved - can mark inactive instead)
    
    return {
        'success': True,
        'removed': removed
    }


def create_stats_table(stats: Dict) -> Table:
    """Create a table showing exchange statistics"""
    table = Table(title="📊 Exchange Data Statistics", box=box.ROUNDED)
    table.add_column("Data Type", style="cyan", no_wrap=True)
    table.add_column("Count", style="white", justify="right")
    table.add_column("Description", style="dim")
    
    table.add_row("Latest Funding Rates", str(stats['latest_funding_rates']), "Current funding rate entries (shown in view_all_exchange_data.py)")
    table.add_row("DEX Symbols", str(stats['dex_symbols']), "Symbol mappings and market data (shown in view_all_exchange_data.py)")
    table.add_row("Historical Funding Rates", str(stats['historical_funding_rates']), "Time-series funding rate history (optional removal)")
    table.add_row("", "", "")
    table.add_row("Exchange Status", "Active" if stats['is_active'] else "Inactive", f"Current status in database")
    table.add_row("", "", "")
    table.add_row("Note", "", "Opportunities and collection logs are preserved")
    
    return table


async def main():
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Remove funding rate data for a specific exchange from the database",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Preview what will be removed
  python remove_funding_exchange.py --exchange edgex --dry-run
  
  # Remove EdgeX funding data (latest rates + symbol mappings)
  python remove_funding_exchange.py --exchange edgex
  
  # Remove EdgeX funding data and also remove historical funding rates
  python remove_funding_exchange.py --exchange edgex --include-historical
  
  # Just mark exchange as inactive (don't delete data)
  python remove_funding_exchange.py --exchange edgex --inactive-only
        """
    )
    
    parser.add_argument(
        '--exchange',
        required=True,
        help='Exchange name to remove (e.g., edgex)'
    )
    
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Preview what would be removed without actually deleting'
    )
    
    parser.add_argument(
        '--include-historical',
        action='store_true',
        help='Also remove historical funding_rates data (by default, only removes current data shown in view_all_exchange_data.py)'
    )
    
    parser.add_argument(
        '--inactive-only',
        action='store_true',
        help='Only mark exchange as inactive, do not delete any data'
    )
    
    parser.add_argument(
        '--yes',
        action='store_true',
        help='Skip confirmation prompt (use with caution!)'
    )
    
    args = parser.parse_args()
    
    exchange_name = args.exchange.lower()
    
    console.print(f"\n[bold cyan]🔍 Analyzing {exchange_name.upper()} exchange data...[/bold cyan]\n")
    
    # Connect to database
    await database.connect()
    
    try:
        # Get statistics
        stats_result = await get_exchange_stats(exchange_name)
        
        if not stats_result['found']:
            console.print(f"[red]❌ {stats_result.get('error', 'Exchange not found')}[/red]")
            return
        
        stats = stats_result['stats']
        
        # Show statistics
        console.print(create_stats_table(stats))
        console.print()
        
        total_records = (
            stats['latest_funding_rates'] +
            stats['dex_symbols'] +
            (stats['historical_funding_rates'] if args.include_historical else 0)
        )
        
        if total_records == 0:
            console.print(Panel(
                f"[yellow]⚠️  No data found for {exchange_name.upper()}[/yellow]\n"
                f"The exchange exists but has no associated data.",
                title="No Data",
                border_style="yellow"
            ))
            
            # No data to remove, just show status
            return
        
        if args.dry_run:
            console.print(Panel(
                "[yellow]🔍 DRY RUN MODE[/yellow]\n"
                "No data will be deleted. Use without --dry-run to actually remove.",
                title="Dry Run",
                border_style="yellow"
            ))
            return
        
        if args.inactive_only:
            console.print(Panel(
                f"[yellow]⚠️  INACTIVE MODE[/yellow]\n\n"
                f"This will mark {exchange_name.upper()} as inactive but preserve all data.\n"
                f"Total records preserved: {total_records:,}\n\n"
                f"The exchange will no longer appear in active queries.",
                title="Mark Inactive",
                border_style="yellow"
            ))
        else:
            historical_note = f" (will also remove {stats['historical_funding_rates']:,} historical records)" if args.include_historical else " (preserved)"
            console.print(Panel(
                f"[yellow]⚠️  WARNING[/yellow]\n\n"
                f"This will remove funding rate data for {exchange_name.upper()}:\n\n"
                f"  • {stats['latest_funding_rates']:,} latest funding rate entries\n"
                f"  • {stats['dex_symbols']:,} DEX symbol mappings (volume, OI, spread)\n"
                f"  • Historical funding rates{historical_note}\n\n"
                f"Total records to delete: {total_records:,}\n\n"
                f"[dim]Note: Opportunities and collection logs are preserved[/dim]\n\n"
                f"[bold red]This action cannot be undone![/bold red]",
                title="Confirm Deletion",
                border_style="yellow"
            ))
        
        if not args.yes:
            action = "mark as inactive" if args.inactive_only else "remove funding rate data"
            if not Confirm.ask(f"\n[bold red]Are you sure you want to {action} for {exchange_name.upper()}?[/bold red]"):
                console.print("[yellow]👋 Cancelled by user[/yellow]")
                return
        
        # Perform removal
        action_desc = "Marking as inactive" if args.inactive_only else "Removing data"
        console.print(f"\n[bold cyan]🗑️  {action_desc} for {exchange_name.upper()}...[/bold cyan]\n")
        
        result = await remove_exchange_data(
            exchange_name,
            include_historical=args.include_historical,
            inactive_only=args.inactive_only
        )
        
        if result['success']:
            removed = result['removed']
            
            if args.inactive_only:
                console.print(Panel(
                    f"[green]✅ Successfully marked {exchange_name.upper()} as inactive![/green]\n\n"
                    f"All {total_records:,} records have been preserved.",
                    title="Complete",
                    border_style="green"
                ))
            else:
                deleted_count = (
                    removed['latest_funding_rates'] +
                    removed['dex_symbols'] +
                    removed['historical_funding_rates']
                )
                
                console.print(Panel(
                    f"[green]✅ Successfully removed {exchange_name.upper()} funding rate data![/green]\n\n"
                    f"Deleted:\n"
                    f"  • Latest funding rates: {removed['latest_funding_rates']:,}\n"
                    f"  • DEX symbols: {removed['dex_symbols']:,}\n"
                    f"  • Historical funding rates: {removed['historical_funding_rates']:,}\n\n"
                    f"Total records deleted: {deleted_count:,}\n\n"
                    f"[dim]Note: Opportunities and collection logs were preserved[/dim]",
                    title="Removal Complete",
                    border_style="green"
                ))
        else:
            console.print(f"[red]❌ Error: {result.get('error', 'Unknown error')}[/red]")
        
    except Exception as e:
        console.print(f"[red]❌ Error: {e}[/red]")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        await database.disconnect()


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

