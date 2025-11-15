#!/usr/bin/env python3
"""
Remove Symbol-DEX Combination

Remove a specific symbol-dex combination from the database.
This is useful for removing outdated or problematic symbols that are no longer
traded on a specific exchange but still have stale data in the database.

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


async def find_symbol_dex_records(
    symbol: str,
    dex_name: str
) -> Dict:
    """
    Find all records related to a symbol-dex combination
    
    Args:
        symbol: Symbol to find (e.g., "AI16Z")
        dex_name: DEX name to find (e.g., "paradex")
        
    Returns:
        Dictionary with found records
    """
    # Note: Database connection should be managed by caller
    # Get dex_id and symbol_id
    dex_query = "SELECT id, name FROM dexes WHERE name = :dex_name"
    dex_row = await database.fetch_one(dex_query, values={"dex_name": dex_name.lower()})
    
    if not dex_row:
        return {
            'found': False,
            'error': f"DEX '{dex_name}' not found",
            'records': {}
        }
    
    dex_id = dex_row['id']
    
    symbol_query = "SELECT id, symbol FROM symbols WHERE symbol = :symbol"
    symbol_row = await database.fetch_one(symbol_query, values={"symbol": symbol.upper()})
    
    if not symbol_row:
        return {
            'found': False,
            'error': f"Symbol '{symbol}' not found",
            'records': {}
        }
    
    symbol_id = symbol_row['id']
    
    # Check latest_funding_rates
    lfr_query = """
        SELECT funding_rate, updated_at 
        FROM latest_funding_rates 
        WHERE dex_id = :dex_id AND symbol_id = :symbol_id
    """
    lfr_row = await database.fetch_one(
        lfr_query, 
        values={"dex_id": dex_id, "symbol_id": symbol_id}
    )
    
    # Check dex_symbols
    ds_query = """
        SELECT dex_symbol_format, is_active, volume_24h, open_interest_usd, updated_at
        FROM dex_symbols 
        WHERE dex_id = :dex_id AND symbol_id = :symbol_id
    """
    ds_row = await database.fetch_one(
        ds_query,
        values={"dex_id": dex_id, "symbol_id": symbol_id}
    )
    
    # Count funding_rates historical records
    fr_count_query = """
        SELECT COUNT(*) as count 
        FROM funding_rates 
        WHERE dex_id = :dex_id AND symbol_id = :symbol_id
    """
    fr_count_row = await database.fetch_one(
        fr_count_query,
        values={"dex_id": dex_id, "symbol_id": symbol_id}
    )
    fr_count = fr_count_row['count'] if fr_count_row else 0
    
    return {
        'found': True,
        'dex_id': dex_id,
        'symbol_id': symbol_id,
        'dex_name': dex_row['name'],
        'symbol': symbol_row['symbol'],
        'records': {
            'latest_funding_rate': lfr_row,
            'dex_symbol': ds_row,
            'historical_funding_rates_count': fr_count
        }
    }


async def remove_symbol_dex(
    symbol: str,
    dex_name: str,
    dry_run: bool = False
) -> Dict:
    """
    Remove a symbol-dex combination from the database
    
    Args:
        symbol: Symbol to remove (e.g., "AI16Z")
        dex_name: DEX name (e.g., "paradex")
        dry_run: If True, don't actually delete, just show what would be deleted
        
    Returns:
        Dictionary with removal results
    """
    # Note: Database connection should be managed by caller
    # First find the records
    found = await find_symbol_dex_records(symbol, dex_name)
    
    if not found['found']:
        return {
            'success': False,
            'error': found.get('error', 'Records not found'),
            'removed': {}
        }
    
    dex_id = found['dex_id']
    symbol_id = found['symbol_id']
    
    removed = {
        'latest_funding_rate': False,
        'dex_symbol': False,
        'historical_funding_rates': 0
    }
    
    if dry_run:
        return {
            'success': True,
            'dry_run': True,
            'found': found,
            'would_remove': removed
        }
    
    # Remove from latest_funding_rates
    if found['records']['latest_funding_rate']:
        delete_lfr_query = """
            DELETE FROM latest_funding_rates 
            WHERE dex_id = :dex_id AND symbol_id = :symbol_id
        """
        await database.execute(
            delete_lfr_query,
            values={"dex_id": dex_id, "symbol_id": symbol_id}
        )
        removed['latest_funding_rate'] = True
    
    # Remove from dex_symbols
    if found['records']['dex_symbol']:
        delete_ds_query = """
            DELETE FROM dex_symbols 
            WHERE dex_id = :dex_id AND symbol_id = :symbol_id
        """
        await database.execute(
            delete_ds_query,
            values={"dex_id": dex_id, "symbol_id": symbol_id}
        )
        removed['dex_symbol'] = True
    
    # Note: We don't delete historical funding_rates as they're part of time-series data
    # and might be useful for historical analysis. If needed, this can be added.
    
    return {
        'success': True,
        'dry_run': False,
        'found': found,
        'removed': removed
    }


def create_preview_table(found: Dict) -> Table:
    """Create a table showing what records exist"""
    table = Table(title="📋 Found Records", box=box.ROUNDED)
    table.add_column("Record Type", style="cyan", no_wrap=True)
    table.add_column("Status", style="white")
    table.add_column("Details", style="dim")
    
    # Latest funding rate
    lfr = found['records']['latest_funding_rate']
    if lfr:
        rate_pct = float(lfr['funding_rate']) * 100
        updated = lfr['updated_at'].strftime('%Y-%m-%d %H:%M:%S') if lfr['updated_at'] else 'N/A'
        table.add_row(
            "Latest Funding Rate",
            "✅ Exists",
            f"Rate: {rate_pct:+.4f}%, Updated: {updated}"
        )
    else:
        table.add_row("Latest Funding Rate", "❌ Not found", "")
    
    # DEX Symbol
    ds = found['records']['dex_symbol']
    if ds:
        active_status = "Active" if ds['is_active'] else "Inactive"
        volume = f"${ds['volume_24h']:,.0f}" if ds['volume_24h'] else "N/A"
        oi = f"${ds['open_interest_usd']:,.0f}" if ds['open_interest_usd'] else "N/A"
        updated = ds['updated_at'].strftime('%Y-%m-%d %H:%M:%S') if ds['updated_at'] else 'N/A'
        table.add_row(
            "DEX Symbol",
            f"✅ Exists ({active_status})",
            f"Format: {ds['dex_symbol_format']}, Vol: {volume}, OI: {oi}, Updated: {updated}"
        )
    else:
        table.add_row("DEX Symbol", "❌ Not found", "")
    
    # Historical funding rates
    fr_count = found['records']['historical_funding_rates_count']
    if fr_count > 0:
        table.add_row(
            "Historical Funding Rates",
            f"✅ {fr_count} records",
            "Note: Historical records will NOT be deleted (preserved for analysis)"
        )
    else:
        table.add_row("Historical Funding Rates", "❌ None", "")
    
    return table


async def main():
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Remove symbol-dex combinations from the database",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Single symbol
  python remove_symbol_dex.py --symbol AI16Z --dex paradex
  
  # Multiple symbols (comma-separated)
  python remove_symbol_dex.py --symbols KBONK,KFLOKI,KPEPE,KSHIB --dex paradex
  
  # Multiple symbols (space-separated, use quotes)
  python remove_symbol_dex.py --symbols "KBONK KFLOKI KPEPE KSHIB" --dex paradex
  
  # Dry run to preview
  python remove_symbol_dex.py --symbols KBONK,KFLOKI --dex paradex --dry-run
        """
    )
    
    symbol_group = parser.add_mutually_exclusive_group(required=True)
    symbol_group.add_argument(
        '--symbol',
        help='Single symbol to remove (e.g., AI16Z)'
    )
    symbol_group.add_argument(
        '--symbols',
        help='Multiple symbols to remove, comma or space separated (e.g., KBONK,KFLOKI or "KBONK KFLOKI")'
    )
    
    parser.add_argument(
        '--dex',
        required=True,
        help='DEX name (e.g., paradex)'
    )
    
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Preview what would be removed without actually deleting'
    )
    
    parser.add_argument(
        '--yes',
        action='store_true',
        help='Skip confirmation prompt (use with caution!)'
    )
    
    args = parser.parse_args()
    
    # Parse symbols
    if args.symbol:
        symbols = [args.symbol.upper()]
    else:
        # Handle both comma and space separated
        symbols_str = args.symbols.replace(',', ' ')
        symbols = [s.strip().upper() for s in symbols_str.split() if s.strip()]
    
    if not symbols:
        console.print("[red]❌ No symbols provided[/red]")
        return
    
    # Connect to database once at the start
    await database.connect()
    
    try:
        # Find all records first
        all_found = {}
        for symbol in symbols:
            found = await find_symbol_dex_records(symbol, args.dex)
            all_found[symbol] = found
        
        # Show summary
        found_count = sum(1 for f in all_found.values() if f.get('found'))
        not_found = [s for s, f in all_found.items() if not f.get('found')]
        
        console.print(f"\n[bold cyan]🔍 Found {found_count} of {len(symbols)} symbols on {args.dex.upper()}...[/bold cyan]\n")
        
        if not_found:
            console.print(f"[yellow]⚠️  Symbols not found: {', '.join(not_found)}[/yellow]\n")
        
        # Show details for each found symbol
        for symbol, found in all_found.items():
            if found.get('found'):
                console.print(f"[bold]{symbol}:[/bold]")
                console.print(create_preview_table(found))
                console.print()
        
        if found_count == 0:
            console.print("[red]❌ No records found to remove[/red]")
            return
        
        if args.dry_run:
            console.print(Panel(
                "[yellow]🔍 DRY RUN MODE[/yellow]\n"
                "No records will be deleted. Use without --dry-run to actually remove.",
                title="Dry Run",
                border_style="yellow"
            ))
            return
        
        # Confirm deletion
        symbols_to_remove = [s for s in symbols if all_found[s].get('found')]
        console.print(Panel(
            f"[yellow]⚠️  WARNING[/yellow]\n\n"
            f"This will permanently remove {len(symbols_to_remove)} symbol(s):\n"
            + "\n".join([f"  • {s} on {args.dex.upper()}" for s in symbols_to_remove]) +
            f"\n\nFor each symbol:\n"
            f"  • Latest funding rate\n"
            f"  • DEX symbol mapping\n\n"
            f"Historical funding rate data will be preserved.\n\n"
            f"This action cannot be undone!",
            title="Confirm Deletion",
            border_style="red"
        ))
        
        if not args.yes:
            if not Confirm.ask(f"\n[bold red]Are you sure you want to remove {len(symbols_to_remove)} symbol(s) from {args.dex.upper()}?[/bold red]"):
                console.print("[yellow]👋 Cancelled by user[/yellow]")
                return
        
        # Perform removal for each symbol
        console.print(f"\n[bold cyan]🗑️  Removing {len(symbols_to_remove)} symbol(s) from {args.dex.upper()}...[/bold cyan]\n")
        
        results = []
        for symbol in symbols_to_remove:
            result = await remove_symbol_dex(symbol, args.dex, dry_run=False)
            results.append((symbol, result))
        
        # Show summary
        successful = [s for s, r in results if r.get('success')]
        failed = [(s, r.get('error', 'Unknown error')) for s, r in results if not r.get('success')]
        
        if successful:
            console.print(Panel(
                f"[green]✅ Successfully removed {len(successful)} symbol(s)![/green]\n\n"
                f"Removed: {', '.join(successful)}\n\n"
                f"Details:\n" +
                "\n".join([
                    f"  • {s}: Latest FR {'✅' if r['removed']['latest_funding_rate'] else '❌'}, "
                    f"DEX mapping {'✅' if r['removed']['dex_symbol'] else '❌'}"
                    for s, r in results if r.get('success')
                ]),
                title="Removal Complete",
                border_style="green"
            ))
        
        if failed:
            console.print(Panel(
                f"[red]❌ Failed to remove {len(failed)} symbol(s):[/red]\n\n" +
                "\n".join([f"  • {s}: {err}" for s, err in failed]),
                title="Errors",
                border_style="red"
            ))
        
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

