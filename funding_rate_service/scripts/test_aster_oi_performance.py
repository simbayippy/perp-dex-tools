#!/usr/bin/env python3
"""
Test Aster OI Fetching Performance

Fetches OI for all symbols, converts to USD using mark prices, and shows timing.

Usage:
    python test_aster_oi_performance.py [--limit N] [--symbol SYMBOL]
    
    --limit N: Only test first N symbols (default: all)
    --symbol SYMBOL: Test specific symbol only (e.g., BTCUSDT)
"""

import sys
import asyncio
import time
from pathlib import Path
from typing import Dict, List, Optional
from decimal import Decimal

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

try:
    from aster.rest_api import Client as AsterClient
except ImportError:
    print("❌ Aster SDK not found. Install with: pip install aster-connector-python")
    sys.exit(1)

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn

console = Console()


async def fetch_oi_for_symbol(
    client: AsterClient,
    symbol: str,
    semaphore: asyncio.Semaphore
) -> Optional[Dict]:
    """Fetch OI for a single symbol."""
    async with semaphore:
        try:
            # Aster SDK is synchronous, so run in executor
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: client.query("/fapi/v1/openInterest", {"symbol": symbol})
            )
            if isinstance(response, dict) and "openInterest" in response:
                return {
                    "symbol": symbol,
                    "openInterest": Decimal(str(response["openInterest"])),
                    "time": response.get("time")
                }
        except Exception as e:
            return {"symbol": symbol, "error": str(e)}
        return None


async def fetch_all_oi(
    client: AsterClient,
    symbols: List[str],
    max_concurrent: int = 10,
    progress_callback=None
) -> Dict[str, Dict]:
    """
    Fetch OI for all symbols with rate limiting.
    
    Args:
        client: Aster SDK client
        symbols: List of symbols to fetch
        max_concurrent: Maximum concurrent requests
        progress_callback: Optional callback for progress updates
        
    Returns:
        Dictionary mapping symbol to OI data
    """
    semaphore = asyncio.Semaphore(max_concurrent)
    completed = 0
    
    async def fetch_with_progress(symbol):
        nonlocal completed
        result = await fetch_oi_for_symbol(client, symbol, semaphore)
        completed += 1
        if progress_callback:
            progress_callback(completed, len(symbols))
        return result
    
    # Run all fetches concurrently with rate limiting
    tasks = [fetch_with_progress(symbol) for symbol in symbols]
    results = await asyncio.gather(*tasks)
    
    # Build result dict
    oi_data = {}
    for result in results:
        if result and isinstance(result, dict):
            symbol = result.get("symbol")
            if symbol:
                oi_data[symbol] = result
    
    return oi_data


def main():
    """Main test function."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Test Aster OI fetching performance",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        '--limit',
        type=int,
        help='Limit to first N symbols (for testing)'
    )
    parser.add_argument(
        '--symbol',
        type=str,
        help='Test specific symbol only (e.g., BTCUSDT)'
    )
    parser.add_argument(
        '--concurrent',
        type=int,
        default=10,
        help='Max concurrent requests (default: 15)'
    )
    
    args = parser.parse_args()
    
    console.print("\n[bold cyan]🔍 Testing Aster OI Fetching Performance...[/bold cyan]\n")
    
    client = AsterClient(base_url="https://fapi.asterdex.com", timeout=10)
    
    # Step 1: Fetch mark prices (batch) - already have this
    console.print("[yellow]Step 1: Fetching mark prices (batch)...[/yellow]")
    start_time = time.time()
    
    try:
        mark_prices_data = client.mark_price()
        mark_price_time = time.time() - start_time
        
        if not mark_prices_data:
            console.print("[red]❌ Failed to fetch mark prices[/red]")
            return
        
        # Build mark price lookup
        mark_price_lookup = {}
        if isinstance(mark_prices_data, list):
            for item in mark_prices_data:
                symbol = item.get('symbol', '')
                mark_price = item.get('markPrice')
                if symbol and mark_price:
                    mark_price_lookup[symbol] = Decimal(str(mark_price))
        elif isinstance(mark_prices_data, dict):
            symbol = mark_prices_data.get('symbol', '')
            mark_price = mark_prices_data.get('markPrice')
            if symbol and mark_price:
                mark_price_lookup[symbol] = Decimal(str(mark_price))
        
        console.print(f"[green]✅ Fetched {len(mark_price_lookup)} mark prices in {mark_price_time:.2f}s[/green]\n")
        
    except Exception as e:
        console.print(f"[red]❌ Error fetching mark prices: {e}[/red]")
        return
    
    # Step 2: Get list of symbols to fetch OI for
    symbols = list(mark_price_lookup.keys())
    
    # Filter symbols (only USDT perps)
    symbols = [s for s in symbols if s.endswith('USDT')]
    
    # Apply filters
    if args.symbol:
        symbols = [s for s in symbols if s == args.symbol.upper()]
        if not symbols:
            console.print(f"[red]❌ Symbol {args.symbol} not found[/red]")
            return
    elif args.limit:
        symbols = symbols[:args.limit]
    
    console.print(f"[yellow]Step 2: Fetching OI for {len(symbols)} symbols (max {args.concurrent} concurrent requests)...[/yellow]")
    
    # Step 3: Fetch OI for all symbols
    oi_start_time = time.time()
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        console=console
    ) as progress:
        task = progress.add_task(f"Fetching OI...", total=len(symbols))
        
        def update_progress(completed, total):
            progress.update(task, completed=completed)
        
        # Run async fetch
        oi_data = asyncio.run(fetch_all_oi(client, symbols, args.concurrent, update_progress))
    
    oi_fetch_time = time.time() - oi_start_time
    
    # Step 4: Fetch volume data (batch) - for comparison
    console.print(f"\n[yellow]Step 3: Fetching volume data (batch)...[/yellow]")
    volume_start_time = time.time()
    
    try:
        ticker_data = client.ticker_24hr_price_change()
        volume_time = time.time() - volume_start_time
        
        # Build volume lookup
        volume_lookup = {}
        if isinstance(ticker_data, list):
            for item in ticker_data:
                symbol = item.get('symbol', '')
                volume = item.get('quoteVolume') or item.get('volume')
                if symbol and volume:
                    volume_lookup[symbol] = Decimal(str(volume))
        elif isinstance(ticker_data, dict):
            symbol = ticker_data.get('symbol', '')
            volume = ticker_data.get('quoteVolume') or ticker_data.get('volume')
            if symbol and volume:
                volume_lookup[symbol] = Decimal(str(volume))
        
        console.print(f"[green]✅ Fetched {len(volume_lookup)} volumes in {volume_time:.2f}s[/green]\n")
        
    except Exception as e:
        console.print(f"[yellow]⚠️  Error fetching volumes: {e}[/yellow]")
        volume_lookup = {}
    
    # Step 5: Build results table
    console.print("[yellow]Step 4: Building results table...[/yellow]\n")
    
    table = Table(title="📊 Aster Market Data (OI + Volume)", box=box.ROUNDED)
    table.add_column("Symbol", style="cyan", no_wrap=True)
    table.add_column("Volume 24h (USD)", style="green", justify="right")
    table.add_column("OI (Base)", style="yellow", justify="right")
    table.add_column("Mark Price", style="blue", justify="right")
    table.add_column("OI (USD)", style="magenta", justify="right")
    table.add_column("Status", style="white", justify="center")
    
    successful = 0
    failed = 0
    
    for symbol in sorted(symbols):
        oi_info = oi_data.get(symbol, {})
        volume = volume_lookup.get(symbol)
        mark_price = mark_price_lookup.get(symbol)
        
        if "error" in oi_info:
            table.add_row(
                symbol,
                f"${volume:,.0f}" if volume else "N/A",
                "N/A",
                f"${mark_price:,.2f}" if mark_price else "N/A",
                "N/A",
                f"❌ {oi_info['error'][:30]}"
            )
            failed += 1
        elif oi_info.get("openInterest") and mark_price:
            oi_base = oi_info["openInterest"]
            oi_usd = oi_base * mark_price
            
            table.add_row(
                symbol,
                f"${volume:,.0f}" if volume else "N/A",
                f"{oi_base:,.2f}",
                f"${mark_price:,.2f}",
                f"${oi_usd:,.0f}",
                "✅"
            )
            successful += 1
        else:
            table.add_row(
                symbol,
                f"${volume:,.0f}" if volume else "N/A",
                "N/A",
                f"${mark_price:,.2f}" if mark_price else "N/A",
                "N/A",
                "⚠️ Missing data"
            )
            failed += 1
    
    console.print(table)
    console.print()
    
    # Summary
    total_time = time.time() - start_time
    
    summary_table = Table(title="⏱️ Performance Summary", box=box.ROUNDED)
    summary_table.add_column("Metric", style="cyan")
    summary_table.add_column("Value", style="magenta", justify="right")
    
    summary_table.add_row("Total Symbols", str(len(symbols)))
    summary_table.add_row("Successful OI Fetches", f"{successful} ({successful/len(symbols)*100:.1f}%)")
    summary_table.add_row("Failed OI Fetches", f"{failed} ({failed/len(symbols)*100:.1f}%)")
    summary_table.add_row("", "")
    summary_table.add_row("Mark Price Fetch Time", f"{mark_price_time:.2f}s")
    summary_table.add_row("OI Fetch Time", f"{oi_fetch_time:.2f}s ({oi_fetch_time/len(symbols)*1000:.0f}ms per symbol)")
    summary_table.add_row("Volume Fetch Time", f"{volume_time:.2f}s")
    summary_table.add_row("", "")
    summary_table.add_row("Total Time", f"{total_time:.2f}s")
    summary_table.add_row("Avg Time per Symbol", f"{total_time/len(symbols)*1000:.0f}ms")
    
    console.print(summary_table)
    console.print()
    
    # Recommendations
    recommendations = []
    if oi_fetch_time > 30:
        recommendations.append("⚠️  OI fetch took >30s - consider reducing concurrent requests or adding caching")
    if failed > len(symbols) * 0.1:
        recommendations.append(f"⚠️  High failure rate ({failed}/{len(symbols)}) - check rate limits")
    if oi_fetch_time / len(symbols) > 0.5:
        recommendations.append(f"⚠️  Slow per-symbol fetch ({oi_fetch_time/len(symbols)*1000:.0f}ms) - check network/API")
    
    if recommendations:
        console.print(Panel(
            "\n".join(recommendations),
            title="Recommendations",
            border_style="yellow"
        ))
    else:
        console.print(Panel(
            "[green]✅ Performance looks good![/green]",
            title="Status",
            border_style="green"
        ))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n\n[yellow]👋 Cancelled by user[/yellow]")
    except Exception as e:
        console.print(f"\n[red]❌ Error: {e}[/red]")
        import traceback
        traceback.print_exc()
        sys.exit(1)

