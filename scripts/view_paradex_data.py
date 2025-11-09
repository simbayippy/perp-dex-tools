#!/usr/bin/env python3
"""
Quick script to view Paradex funding rate data with volume and OI.

Usage:
    python scripts/view_paradex_data.py              # View all symbols
    python scripts/view_paradex_data.py ZEC          # View specific symbol
"""

import sys
import json
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import httpx

try:
    from rich.console import Console
    from rich.table import Table
    from rich import box
    HAS_RICH = True
    console = Console()
except ImportError:
    HAS_RICH = False
    print("Note: Install 'rich' for better formatting: pip install rich")

API_BASE_URL = "http://localhost:8000/api/v1"


def format_number(value):
    """Format large numbers with K/M/B suffixes"""
    if value is None:
        return "N/A"
    
    value = float(value)
    if value >= 1_000_000_000:
        return f"${value/1_000_000_000:.2f}B"
    elif value >= 1_000_000:
        return f"${value/1_000_000:.2f}M"
    elif value >= 1_000:
        return f"${value/1_000:.2f}K"
    else:
        return f"${value:.2f}"


def format_rate(rate):
    """Format funding rate as percentage"""
    if rate is None:
        return "N/A"
    return f"{float(rate) * 100:.4f}%"


def view_all_symbols():
    """View all Paradex symbols with funding rates, volume, and OI"""
    try:
        response = httpx.get(f"{API_BASE_URL}/dexes/paradex/symbols", timeout=10)
        response.raise_for_status()
        data = response.json()
        
        if not data.get('symbols'):
            print("No symbols found for Paradex")
            return
        
        # Get funding rates for all symbols
        rates_response = httpx.get(f"{API_BASE_URL}/funding-rates/paradex", timeout=10)
        rates_data = rates_response.json() if rates_response.status_code == 200 else {}
        rates_dict = rates_data.get('rates', {})
        
        if HAS_RICH:
            # Create table with rich
            table = Table(title="Paradex Funding Rates & Market Data", box=box.ROUNDED)
            table.add_column("Symbol", style="cyan", no_wrap=True)
            table.add_column("DEX Format", style="dim")
            table.add_column("Funding Rate", justify="right", style="green")
            table.add_column("Volume 24h", justify="right", style="yellow")
            table.add_column("Open Interest", justify="right", style="magenta")
            table.add_column("Updated", style="dim")
            
            for symbol_data in data['symbols']:
                symbol = symbol_data['symbol']
                funding_rate = rates_dict.get(symbol, {}).get('funding_rate')
                
                table.add_row(
                    symbol,
                    symbol_data.get('dex_symbol_format', 'N/A'),
                    format_rate(funding_rate),
                    format_number(symbol_data.get('volume_24h')),
                    format_number(symbol_data.get('open_interest_usd')),
                    symbol_data.get('last_updated', 'N/A')[:19] if symbol_data.get('last_updated') else 'N/A'
                )
            
            console.print(table)
            console.print(f"\n[dim]Total symbols: {data['count']}[/dim]")
        else:
            # Simple text output
            print("\nParadex Funding Rates & Market Data")
            print("=" * 100)
            print(f"{'Symbol':<10} {'DEX Format':<20} {'Funding Rate':<15} {'Volume 24h':<15} {'Open Interest':<15} {'Updated':<20}")
            print("-" * 100)
            
            for symbol_data in data['symbols']:
                symbol = symbol_data['symbol']
                funding_rate = rates_dict.get(symbol, {}).get('funding_rate')
                
                print(f"{symbol:<10} {symbol_data.get('dex_symbol_format', 'N/A'):<20} "
                      f"{format_rate(funding_rate):<15} {format_number(symbol_data.get('volume_24h')):<15} "
                      f"{format_number(symbol_data.get('open_interest_usd')):<15} "
                      f"{(symbol_data.get('last_updated', 'N/A')[:19] if symbol_data.get('last_updated') else 'N/A'):<20}")
            
            print(f"\nTotal symbols: {data['count']}")
        
    except httpx.HTTPStatusError as e:
        error_msg = f"HTTP Error: {e.response.status_code}\nResponse: {e.response.text}"
        if HAS_RICH:
            console.print(f"[red]{error_msg}[/red]")
        else:
            print(f"Error: {error_msg}")
    except Exception as e:
        error_msg = f"Error: {e}"
        if HAS_RICH:
            console.print(f"[red]{error_msg}[/red]")
        else:
            print(error_msg)


def view_symbol(symbol: str):
    """View detailed data for a specific symbol"""
    try:
        symbol_upper = symbol.upper()
        
        # Get funding rate with volume/OI
        response = httpx.get(
            f"{API_BASE_URL}/funding-rates/paradex/{symbol_upper}",
            timeout=10
        )
        response.raise_for_status()
        data = response.json()
        
        if HAS_RICH:
            # Create detailed display with rich
            console.print(f"\n[bold cyan]Paradex: {symbol_upper}[/bold cyan]")
            console.print("=" * 60)
            
            # Funding rate info
            console.print(f"\n[bold]Funding Rate:[/bold]")
            console.print(f"  Rate: {format_rate(data.get('funding_rate'))}")
            console.print(f"  Annualized: {format_rate(data.get('annualized_rate'))}")
            
            if data.get('next_funding_time'):
                console.print(f"  Next Funding: {data['next_funding_time']}")
            
            # Market data
            console.print(f"\n[bold]Market Data:[/bold]")
            console.print(f"  Volume 24h: {format_number(data.get('volume_24h'))}")
            console.print(f"  Open Interest: {format_number(data.get('open_interest_usd'))}")
            
            # Metadata
            console.print(f"\n[bold]Metadata:[/bold]")
            console.print(f"  Last Updated: {data.get('timestamp', 'N/A')}")
            console.print(f"  DEX: {data.get('dex_name', 'paradex')}")
            
            console.print("\n")
        else:
            # Simple text output
            print(f"\nParadex: {symbol_upper}")
            print("=" * 60)
            
            print(f"\nFunding Rate:")
            print(f"  Rate: {format_rate(data.get('funding_rate'))}")
            print(f"  Annualized: {format_rate(data.get('annualized_rate'))}")
            
            if data.get('next_funding_time'):
                print(f"  Next Funding: {data['next_funding_time']}")
            
            print(f"\nMarket Data:")
            print(f"  Volume 24h: {format_number(data.get('volume_24h'))}")
            print(f"  Open Interest: {format_number(data.get('open_interest_usd'))}")
            
            print(f"\nMetadata:")
            print(f"  Last Updated: {data.get('timestamp', 'N/A')}")
            print(f"  DEX: {data.get('dex_name', 'paradex')}")
            print()
        
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            error_msg = f"Symbol '{symbol}' not found for Paradex"
            if HAS_RICH:
                console.print(f"[yellow]{error_msg}[/yellow]")
            else:
                print(error_msg)
        else:
            error_msg = f"HTTP Error: {e.response.status_code}\nResponse: {e.response.text}"
            if HAS_RICH:
                console.print(f"[red]{error_msg}[/red]")
            else:
                print(f"Error: {error_msg}")
    except Exception as e:
        error_msg = f"Error: {e}"
        if HAS_RICH:
            console.print(f"[red]{error_msg}[/red]")
        else:
            print(error_msg)


def main():
    """Main entry point"""
    if len(sys.argv) > 1:
        # View specific symbol
        symbol = sys.argv[1].upper()
        view_symbol(symbol)
    else:
        # View all symbols
        view_all_symbols()


if __name__ == "__main__":
    main()

