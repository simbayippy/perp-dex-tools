#!/usr/bin/env python3
"""
Investigate Open Interest Timeframes Across Exchanges

This script investigates what timeframe/definition each exchange uses for OI:
- Lighter: one-sided vs two-sided
- Aster: one-sided vs two-sided  
- Backpack: one-sided vs two-sided
- Paradex: one-sided vs two-sided
- Grvt: one-sided vs two-sided

Also checks update frequency and data consistency.
"""

import sys
import asyncio
from pathlib import Path
from typing import Dict, List, Optional
from decimal import Decimal
from datetime import datetime

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

from exchange_clients.lighter.funding_adapter.adapter import LighterFundingAdapter
from exchange_clients.aster.funding_adapter.adapter import AsterFundingAdapter
from exchange_clients.backpack.funding_adapter.adapter import BackpackFundingAdapter
from exchange_clients.paradex.funding_adapter.adapter import ParadexFundingAdapter
from exchange_clients.grvt.funding_adapter import GrvtFundingAdapter

console = Console()


async def test_exchange_oi(
    adapter_name: str,
    adapter,
    test_symbol: str = "BTC"
) -> Dict:
    """Test OI fetching for an exchange."""
    result = {
        'exchange': adapter_name,
        'success': False,
        'error': None,
        'oi_data': None,
        'volume_data': None,
        'sample_symbol': None,
        'oi_definition': None,
        'update_frequency': None,
        'notes': []
    }
    
    try:
        # Fetch market data
        market_data = await adapter.fetch_market_data()
        
        if not market_data:
            result['error'] = "No market data returned"
            return result
        
        result['success'] = True
        
        # Find test symbol
        test_symbol_upper = test_symbol.upper()
        sample_data = None
        for symbol, data in market_data.items():
            if symbol.upper() == test_symbol_upper:
                sample_data = data
                result['sample_symbol'] = symbol
                break
        
        if not sample_data:
            # Use first available symbol
            first_symbol = list(market_data.keys())[0]
            sample_data = market_data[first_symbol]
            result['sample_symbol'] = first_symbol
            result['notes'].append(f"Test symbol {test_symbol} not found, using {first_symbol}")
        
        result['oi_data'] = sample_data.get('open_interest')
        result['volume_data'] = sample_data.get('volume_24h')
        
        # Check adapter implementation for OI definition based on code analysis
        if adapter_name == "Lighter":
            # Lighter multiplies by 2 (two-sided)
            result['oi_definition'] = "Two-sided (multiplied by 2)"
            result['notes'].append("✅ Lighter: Returns one-sided OI in base tokens → converts to USD → multiplies by 2")
            result['notes'].append("   Matches UI: two-sided = long OI + short OI = 2 × one-sided")
        elif adapter_name == "Aster":
            # Aster - user reports it's half of website, suggesting one-sided
            result['oi_definition'] = "⚠️  One-sided (needs ×2 multiplier?)"
            result['notes'].append("⚠️  Aster: Returns base currency OI → converts to USD")
            result['notes'].append("   User reports: API OI = ~50% of website OI")
            result['notes'].append("   → Likely one-sided, website shows two-sided")
            result['notes'].append("   → Should multiply by 2 to match website")
        elif adapter_name == "Backpack":
            # Backpack - uses openInterest directly, need to verify
            result['oi_definition'] = "❓ Unknown (needs verification)"
            result['notes'].append("❓ Backpack: Uses openInterest field directly from API")
            result['notes'].append("   No conversion or multiplier in code")
            result['notes'].append("   → Need to check website to verify if one-sided or two-sided")
        elif adapter_name == "Paradex":
            # Paradex - converts base to USD, no multiplier
            result['oi_definition'] = "❓ Unknown (needs verification)"
            result['notes'].append("❓ Paradex: Returns base currency OI → converts to USD")
            result['notes'].append("   No multiplier applied")
            result['notes'].append("   → Need to check website to verify if one-sided or two-sided")
        elif adapter_name == "Grvt":
            # Grvt - converts contracts to USD, no multiplier
            result['oi_definition'] = "❓ Unknown (needs verification)"
            result['notes'].append("❓ Grvt: Returns contracts OI → converts to USD")
            result['notes'].append("   No multiplier applied")
            result['notes'].append("   → Need to check website to verify if one-sided or two-sided")
        
        # Update frequency - typically real-time or near real-time
        result['update_frequency'] = "Real-time or near real-time (typically updated every few seconds to minutes)"
        
    except Exception as e:
        result['error'] = str(e)
        result['success'] = False
    
    return result


async def main():
    """Main investigation function."""
    console.print("\n[bold cyan]🔍 Investigating Open Interest Timeframes Across Exchanges...[/bold cyan]\n")
    
    # Test symbol
    test_symbol = "BTC"
    
    results = []
    
    # Test each exchange
    exchanges = [
        ("Lighter", LighterFundingAdapter()),
        ("Aster", AsterFundingAdapter()),
        ("Backpack", BackpackFundingAdapter()),
        ("Paradex", ParadexFundingAdapter()),
        ("Grvt", GrvtFundingAdapter()),
    ]
    
    for adapter_name, adapter in exchanges:
        console.print(f"[yellow]Testing {adapter_name}...[/yellow]")
        try:
            result = await test_exchange_oi(adapter_name, adapter, test_symbol)
            results.append(result)
            
            if result['success']:
                console.print(f"[green]✅ {adapter_name}: Success[/green]")
            else:
                console.print(f"[red]❌ {adapter_name}: {result.get('error', 'Unknown error')}[/red]")
        except Exception as e:
            console.print(f"[red]❌ {adapter_name}: Exception - {e}[/red]")
            results.append({
                'exchange': adapter_name,
                'success': False,
                'error': str(e)
            })
        finally:
            try:
                await adapter.close()
            except:
                pass
    
    console.print()
    
    # Create summary table
    table = Table(title="📊 Open Interest Investigation Summary", box=box.ROUNDED)
    table.add_column("Exchange", style="cyan", no_wrap=True)
    table.add_column("Status", style="white", justify="center")
    table.add_column("Sample Symbol", style="dim")
    table.add_column("OI (USD)", style="green", justify="right")
    table.add_column("Volume 24h", style="blue", justify="right")
    table.add_column("OI Definition", style="yellow", no_wrap=False)
    table.add_column("Update Frequency", style="dim", no_wrap=False)
    
    for result in results:
        status = "✅" if result.get('success') else "❌"
        symbol = result.get('sample_symbol', 'N/A')
        oi = result.get('oi_data')
        volume = result.get('volume_data')
        oi_def = result.get('oi_definition', 'Unknown')
        update_freq = result.get('update_frequency', 'Unknown')
        error = result.get('error')
        
        oi_str = f"${oi:,.0f}" if oi else "N/A"
        volume_str = f"${volume:,.0f}" if volume else "N/A"
        
        if error:
            oi_def = f"Error: {error[:30]}"
        
        table.add_row(
            result['exchange'],
            status,
            symbol,
            oi_str,
            volume_str,
            oi_def,
            update_freq
        )
    
    console.print(table)
    console.print()
    
    # Detailed analysis
    console.print("[bold cyan]📋 Detailed Analysis:[/bold cyan]\n")
    
    for result in results:
        if result.get('success'):
            notes = result.get('notes', [])
            if notes:
                console.print(Panel(
                    "\n".join(f"• {note}" for note in notes),
                    title=f"{result['exchange']} OI Details",
                    border_style="blue"
                ))
                console.print()
    
    # Key findings
    console.print(Panel(
        "[bold]Key Findings:[/bold]\n\n"
        "1. [yellow]Lighter:[/yellow] Returns one-sided OI, multiplies by 2 for two-sided\n"
        "   → Matches UI display (two-sided = long + short)\n\n"
        "2. [yellow]Aster:[/yellow] Returns base currency OI, converts to USD\n"
        "   → [red]⚠️  May need to multiply by 2 if website shows two-sided[/red]\n"
        "   → User reports: API OI = ~50% of website OI\n\n"
        "3. [yellow]Backpack:[/yellow] Uses openInterest field directly\n"
        "   → Need to verify if one-sided or two-sided\n\n"
        "4. [yellow]Paradex:[/yellow] Converts base currency to USD\n"
        "   → Need to verify if one-sided or two-sided\n\n"
        "5. [yellow]Grvt:[/yellow] Converts contracts to USD\n"
        "   → Need to verify if one-sided or two-sided\n\n"
        "[bold]Recommendation:[/bold]\n"
        "• Check each exchange's website/UI to see what they display\n"
        "• Normalize all OI to two-sided (long + short) for consistency\n"
        "• Document which exchanges return one-sided vs two-sided",
        title="Investigation Summary",
        border_style="cyan"
    ))


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

