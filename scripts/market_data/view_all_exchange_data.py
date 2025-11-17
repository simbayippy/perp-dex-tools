#!/usr/bin/env python3
"""
View All Exchange Data

View funding rates, volume, and open interest data across all exchanges.
Helps verify that volume/OI data is being collected correctly and that filters work.

Usage:
    python view_all_exchange_data.py                    # Show all symbols across all exchanges
    python view_all_exchange_data.py BTC                # Show BTC across all exchanges
    python view_all_exchange_data.py --exchange lighter # Show all symbols for Lighter only
    python view_all_exchange_data.py --summary          # Show summary statistics only
"""

import asyncio
import sys
from datetime import datetime, timezone
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
from rich.text import Text
from rich import box

from database.connection import database

console = Console()


async def fetch_exchange_data(
    symbol_filter: Optional[str] = None,
    exchange_filter: Optional[str] = None,
    summary_only: bool = False
) -> Dict:
    """
    Fetch funding rate, volume, and OI data from database
    
    Args:
        symbol_filter: Optional symbol to filter (e.g., "BTC")
        exchange_filter: Optional exchange to filter (e.g., "lighter")
        summary_only: If True, return only summary statistics
        
    Returns:
        Dictionary with data and statistics
    """
    await database.connect()
    
    try:
        # Build query
        query = """
            SELECT 
                d.name as dex_name,
                s.symbol,
                lfr.funding_rate,
                ds.volume_24h,
                ds.open_interest_usd,
                ds.spread_bps,
                lfr.updated_at as funding_updated_at,
                ds.updated_at as market_data_updated_at,
                NOW() - GREATEST(lfr.updated_at, COALESCE(ds.updated_at, lfr.updated_at)) as data_age
            FROM latest_funding_rates lfr
            JOIN dexes d ON lfr.dex_id = d.id
            JOIN symbols s ON lfr.symbol_id = s.id
            LEFT JOIN dex_symbols ds ON ds.dex_id = d.id AND ds.symbol_id = s.id
            WHERE d.is_active = TRUE
        """
        
        params = {}
        
        if symbol_filter:
            query += " AND s.symbol = :symbol"
            params["symbol"] = symbol_filter.upper()
        
        if exchange_filter:
            query += " AND d.name = :dex_name"
            params["dex_name"] = exchange_filter.lower()
        
        query += " ORDER BY d.name, s.symbol"
        
        rows = await database.fetch_all(query, values=params if params else None)
        
        if not rows:
            return {
                'rows': [],
                'statistics': {},
                'exchanges': set(),
                'symbols': set()
            }
        
        # Process rows
        exchanges = set()
        symbols = set()
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        
        processed_rows = []
        for row in rows:
            dex_name = row['dex_name']
            symbol = row['symbol']
            exchanges.add(dex_name)
            symbols.add(symbol)
            
            # Calculate data age
            funding_updated = row['funding_updated_at']
            market_updated = row['market_data_updated_at']
            
            if market_updated:
                updated_at = market_updated
            elif funding_updated:
                updated_at = funding_updated
            else:
                updated_at = None
            
            if updated_at:
                if updated_at.tzinfo is None:
                    updated_at_utc = updated_at
                else:
                    updated_at_utc = updated_at.astimezone(timezone.utc).replace(tzinfo=None)
                age_seconds = (now - updated_at_utc).total_seconds()
            else:
                age_seconds = None
            
            processed_rows.append({
                'dex_name': dex_name,
                'symbol': symbol,
                'funding_rate': row['funding_rate'],
                'volume_24h': row['volume_24h'],
                'open_interest_usd': row['open_interest_usd'],
                'spread_bps': row['spread_bps'],
                'age_seconds': age_seconds,
                'has_market_data': market_updated is not None
            })
        
        # Calculate statistics
        statistics = calculate_statistics(processed_rows, exchanges, symbols)
        
        return {
            'rows': processed_rows,
            'statistics': statistics,
            'exchanges': exchanges,
            'symbols': symbols
        }
        
    finally:
        await database.disconnect()


def calculate_statistics(rows: List[Dict], exchanges: set, symbols: set) -> Dict:
    """Calculate summary statistics"""
    stats = {
        'total_records': len(rows),
        'exchanges': len(exchanges),
        'symbols': len(symbols),
        'with_volume': 0,
        'with_oi': 0,
        'with_market_data': 0,
        'stale_data': 0,
        'exchange_stats': {}
    }
    
    for exchange in exchanges:
        exchange_rows = [r for r in rows if r['dex_name'] == exchange]
        exchange_stats = {
            'total': len(exchange_rows),
            'with_volume': sum(1 for r in exchange_rows if r['volume_24h'] is not None),
            'with_oi': sum(1 for r in exchange_rows if r['open_interest_usd'] is not None),
            'with_market_data': sum(1 for r in exchange_rows if r['has_market_data']),
            'stale': sum(1 for r in exchange_rows if r['age_seconds'] and r['age_seconds'] > 120)
        }
        stats['exchange_stats'][exchange] = exchange_stats
        
        stats['with_volume'] += exchange_stats['with_volume']
        stats['with_oi'] += exchange_stats['with_oi']
        stats['with_market_data'] += exchange_stats['with_market_data']
        stats['stale_data'] += exchange_stats['stale']
    
    return stats


def format_funding_rate(rate: Optional[Decimal]) -> Text:
    """Format funding rate with color coding"""
    if rate is None:
        return Text("N/A", style="dim")
    
    rate_pct = float(rate) * 100
    if rate_pct > 0.01:
        return Text(f"{rate_pct:+.4f}%", style="red")
    elif rate_pct < -0.01:
        return Text(f"{rate_pct:+.4f}%", style="green")
    else:
        return Text(f"{rate_pct:+.4f}%", style="yellow")


def format_currency(value: Optional[Decimal], threshold: Optional[Decimal] = None) -> Text:
    """Format currency value with optional threshold highlighting"""
    if value is None:
        return Text("N/A", style="dim")
    
    value_float = float(value)
    text = f"${value_float:,.0f}"
    
    if threshold:
        if value_float < threshold:
            return Text(text, style="red")
        else:
            return Text(text, style="green")
    
    return Text(text)


def format_age(age_seconds: Optional[float]) -> Text:
    """Format data age"""
    if age_seconds is None:
        return Text("Unknown", style="dim")
    
    if age_seconds < 60:
        return Text(f"{int(age_seconds)}s", style="green")
    elif age_seconds < 120:
        return Text(f"{int(age_seconds)}s", style="yellow")
    else:
        minutes = int(age_seconds / 60)
        return Text(f"{minutes}m", style="red")


def create_summary_table(statistics: Dict) -> Table:
    """Create summary statistics table"""
    table = Table(title="📊 Summary Statistics", box=box.ROUNDED)
    table.add_column("Metric", style="cyan", no_wrap=True)
    table.add_column("Value", style="magenta", justify="right")
    
    table.add_row("Total Records", str(statistics['total_records']))
    table.add_row("Exchanges", str(statistics['exchanges']))
    table.add_row("Symbols", str(statistics['symbols']))
    table.add_row("", "")
    table.add_row("With Volume Data", f"{statistics['with_volume']} ({statistics['with_volume']/max(statistics['total_records'],1)*100:.1f}%)")
    table.add_row("With OI Data", f"{statistics['with_oi']} ({statistics['with_oi']/max(statistics['total_records'],1)*100:.1f}%)")
    table.add_row("With Market Data", f"{statistics['with_market_data']} ({statistics['with_market_data']/max(statistics['total_records'],1)*100:.1f}%)")
    table.add_row("Stale Data (>2min)", f"{statistics['stale_data']} ({statistics['stale_data']/max(statistics['total_records'],1)*100:.1f}%)")
    
    return table


def create_exchange_stats_table(exchange_stats: Dict) -> Table:
    """Create exchange-specific statistics table"""
    table = Table(title="📈 Per-Exchange Statistics", box=box.ROUNDED)
    table.add_column("Exchange", style="cyan", no_wrap=True)
    table.add_column("Total", style="white", justify="right")
    table.add_column("Volume", style="green", justify="right")
    table.add_column("OI", style="blue", justify="right")
    table.add_column("Market Data", style="yellow", justify="right")
    table.add_column("Stale", style="red", justify="right")
    
    for exchange, stats in sorted(exchange_stats.items()):
        table.add_row(
            exchange.upper(),
            str(stats['total']),
            f"{stats['with_volume']} ({stats['with_volume']/max(stats['total'],1)*100:.0f}%)",
            f"{stats['with_oi']} ({stats['with_oi']/max(stats['total'],1)*100:.0f}%)",
            f"{stats['with_market_data']} ({stats['with_market_data']/max(stats['total'],1)*100:.0f}%)",
            f"{stats['stale']} ({stats['stale']/max(stats['total'],1)*100:.0f}%)"
        )
    
    return table


def create_data_table(rows: List[Dict], show_exchange: bool = True) -> Table:
    """Create main data table"""
    if show_exchange:
        table = Table(title="📋 Exchange Data", box=box.ROUNDED, show_header=True, header_style="bold magenta")
        table.add_column("Exchange", style="cyan", no_wrap=True)
        table.add_column("Symbol", style="white", no_wrap=True)
    else:
        table = Table(title="📋 Exchange Data", box=box.ROUNDED, show_header=True, header_style="bold magenta")
        table.add_column("Symbol", style="white", no_wrap=True)
    
    table.add_column("Funding Rate", style="yellow", justify="right")
    table.add_column("Volume 24h", style="green", justify="right")
    table.add_column("OI USD", style="blue", justify="right")
    table.add_column("Spread", style="dim", justify="right")
    table.add_column("Age", style="dim", justify="right")
    
    # Group by exchange if showing exchange column
    if show_exchange:
        current_exchange = None
        for row in rows:
            if row['dex_name'] != current_exchange:
                current_exchange = row['dex_name']
                # Add separator row
                if current_exchange:
                    table.add_section()
            
            table.add_row(
                row['dex_name'].upper(),
                row['symbol'],
                format_funding_rate(row['funding_rate']),
                format_currency(row['volume_24h']),
                format_currency(row['open_interest_usd']),
                Text(f"{row['spread_bps']}bps", style="dim") if row['spread_bps'] else Text("N/A", style="dim"),
                format_age(row['age_seconds'])
            )
    else:
        for row in rows:
            table.add_row(
                row['symbol'],
                format_funding_rate(row['funding_rate']),
                format_currency(row['volume_24h']),
                format_currency(row['open_interest_usd']),
                Text(f"{row['spread_bps']}bps", style="dim") if row['spread_bps'] else Text("N/A", style="dim"),
                format_age(row['age_seconds'])
            )
    
    return table


async def main():
    import argparse
    
    parser = argparse.ArgumentParser(
        description="View funding rates, volume, and OI data across all exchanges",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python view_all_exchange_data.py                    # Show all symbols across all exchanges
  python view_all_exchange_data.py BTC                # Show BTC across all exchanges
  python view_all_exchange_data.py --exchange lighter # Show all symbols for Lighter only
  python view_all_exchange_data.py --summary          # Show summary statistics only
  python view_all_exchange_data.py BTC --exchange paradex  # Show BTC on Paradex only
        """
    )
    
    parser.add_argument(
        'symbol',
        nargs='?',
        help='Filter by specific symbol (e.g., BTC, ETH)'
    )
    
    parser.add_argument(
        '--exchange',
        help='Filter by specific exchange (e.g., lighter, paradex, aster)'
    )
    
    parser.add_argument(
        '--summary',
        action='store_true',
        help='Show only summary statistics'
    )
    
    args = parser.parse_args()
    
    console.print("\n[bold cyan]🔍 Fetching Exchange Data...[/bold cyan]\n")
    
    try:
        data = await fetch_exchange_data(
            symbol_filter=args.symbol,
            exchange_filter=args.exchange,
            summary_only=args.summary
        )
        
        if not data['rows']:
            console.print("[red]❌ No data found[/red]")
            if args.symbol:
                console.print(f"   Symbol filter: {args.symbol}")
            if args.exchange:
                console.print(f"   Exchange filter: {args.exchange}")
            return
        
        # Show summary statistics
        console.print(create_summary_table(data['statistics']))
        console.print()
        
        if not args.summary:
            # Show per-exchange statistics
            if len(data['exchanges']) > 1:
                console.print(create_exchange_stats_table(data['statistics']['exchange_stats']))
                console.print()
            
            # Show main data table
            show_exchange_col = len(data['exchanges']) > 1 or args.exchange is None
            console.print(create_data_table(data['rows'], show_exchange=show_exchange_col))
        
        # Show warnings/recommendations
        console.print()
        stats = data['statistics']
        
        if stats['stale_data'] > 0:
            console.print(Panel(
                f"[yellow]⚠️  Warning: {stats['stale_data']} records have stale data (>2 minutes old)[/yellow]\n"
                "   - Check if funding_rate_service/run_tasks.py is running\n"
                "   - Collection should happen every 60 seconds",
                title="Data Freshness",
                border_style="yellow"
            ))
        
        missing_market_data = stats['total_records'] - stats['with_market_data']
        if missing_market_data > 0:
            console.print(Panel(
                f"[yellow]⚠️  {missing_market_data} records missing market data (volume/OI)[/yellow]\n"
                "   - Some exchanges may not provide volume/OI data\n"
                "   - Check adapter implementations for fetch_market_data()",
                title="Market Data Coverage",
                border_style="yellow"
            ))
        
        if stats['with_market_data'] == stats['total_records']:
            console.print(Panel(
                "[green]✅ All records have market data![/green]\n"
                "   Volume/OI filters will work correctly",
                title="Market Data Status",
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

