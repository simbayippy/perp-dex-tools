#!/usr/bin/env python3
"""
Test Aster API endpoints to find open interest data.

This script tests various Aster API endpoints to identify where open interest
data might be available. It inspects the full response structure to help
identify potential fields containing OI data.

Usage:
    python test_aster_open_interest.py [symbol]
    
    If symbol is provided, tests will be run for that specific symbol.
    Otherwise, tests will fetch all symbols.
    
Examples:
    python test_aster_open_interest.py BTCUSDT
    python test_aster_open_interest.py
"""

import sys
import json
from pathlib import Path
from typing import Optional, Dict, Any, List
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
from rich.json import JSON

console = Console()


def format_response(data: Any, max_depth: int = 3, current_depth: int = 0) -> str:
    """Format API response for display, showing structure."""
    if current_depth >= max_depth:
        return "..."
    
    if isinstance(data, dict):
        if current_depth == 0:
            # Top level - show all keys
            return json.dumps(data, indent=2, default=str)
        else:
            # Nested - show keys only
            keys = list(data.keys())[:10]  # Limit to first 10 keys
            if len(data) > 10:
                keys.append(f"... ({len(data) - 10} more)")
            return "{" + ", ".join(keys) + "}"
    elif isinstance(data, list):
        if len(data) == 0:
            return "[]"
        elif len(data) == 1:
            return f"[{format_response(data[0], max_depth, current_depth + 1)}]"
        else:
            first_item = format_response(data[0], max_depth, current_depth + 1)
            return f"[{first_item}, ... ({len(data) - 1} more items)]"
    else:
        return str(data)


def test_endpoint(
    client: AsterClient,
    endpoint_name: str,
    method: callable,
    symbol: Optional[str] = None,
    **kwargs
) -> Dict[str, Any]:
    """
    Test an API endpoint and return results.
    
    Args:
        client: Aster SDK client
        endpoint_name: Human-readable name for the endpoint
        method: Method to call on the client
        symbol: Optional symbol to filter
        **kwargs: Additional arguments to pass to method
        
    Returns:
        Dictionary with test results
    """
    result = {
        'endpoint': endpoint_name,
        'success': False,
        'error': None,
        'response': None,
        'response_type': None,
        'sample_item': None,
        'has_oi_fields': False,
        'oi_fields_found': [],
        'symbol_count': 0
    }
    
    try:
        if symbol:
            if 'symbol' in method.__code__.co_varnames:
                response = method(symbol=symbol, **kwargs)
            else:
                response = method(**kwargs)
        else:
            response = method(**kwargs)
        
        result['success'] = True
        result['response'] = response
        result['response_type'] = type(response).__name__
        
        # Analyze response structure
        if isinstance(response, list):
            result['symbol_count'] = len(response)
            if len(response) > 0:
                result['sample_item'] = response[0]
        elif isinstance(response, dict):
            result['symbol_count'] = 1
            result['sample_item'] = response
        
        # Check for OI-related fields
        oi_keywords = [
            'openInterest', 'open_interest', 'openInterestValue', 
            'openInterestUsd', 'oi', 'OI', 'open_interest_usd',
            'totalOpenInterest', 'total_open_interest'
        ]
        
        def check_dict_for_oi(d: Dict, path: str = "") -> List[str]:
            """Recursively check dictionary for OI fields."""
            found = []
            for key, value in d.items():
                current_path = f"{path}.{key}" if path else key
                
                # Check if key contains OI keywords
                if any(keyword.lower() in key.lower() for keyword in oi_keywords):
                    found.append(current_path)
                    result['has_oi_fields'] = True
                
                # Recursively check nested dicts
                if isinstance(value, dict):
                    found.extend(check_dict_for_oi(value, current_path))
                elif isinstance(value, list) and len(value) > 0 and isinstance(value[0], dict):
                    found.extend(check_dict_for_oi(value[0], f"{current_path}[0]"))
            
            return found
        
        if isinstance(result['sample_item'], dict):
            result['oi_fields_found'] = check_dict_for_oi(result['sample_item'])
        
    except Exception as e:
        result['error'] = str(e)
        result['success'] = False
    
    return result


def test_direct_endpoint(
    client: AsterClient,
    endpoint_path: str,
    symbol: Optional[str] = None
) -> Dict[str, Any]:
    """
    Test an endpoint directly using the query method.
    
    Args:
        client: Aster SDK client
        endpoint_path: API endpoint path (e.g., "/fapi/v1/openInterest")
        symbol: Optional symbol parameter
        
    Returns:
        Dictionary with test results
    """
    result = {
        'endpoint': endpoint_path,
        'success': False,
        'error': None,
        'response': None,
        'response_type': None,
        'sample_item': None,
        'has_oi_fields': False,
        'oi_fields_found': [],
        'symbol_count': 0
    }
    
    try:
        params = {}
        if symbol:
            params['symbol'] = symbol
        
        response = client.query(endpoint_path, params if params else None)
        
        result['success'] = True
        result['response'] = response
        result['response_type'] = type(response).__name__
        
        # Analyze response structure
        if isinstance(response, list):
            result['symbol_count'] = len(response)
            if len(response) > 0:
                result['sample_item'] = response[0]
        elif isinstance(response, dict):
            result['symbol_count'] = 1
            result['sample_item'] = response
        
        # Check for OI-related fields
        oi_keywords = [
            'openInterest', 'open_interest', 'openInterestValue', 
            'openInterestUsd', 'oi', 'OI', 'open_interest_usd',
            'totalOpenInterest', 'total_open_interest'
        ]
        
        def check_dict_for_oi(d: Dict, path: str = "") -> List[str]:
            """Recursively check dictionary for OI fields."""
            found = []
            for key, value in d.items():
                current_path = f"{path}.{key}" if path else key
                
                # Check if key contains OI keywords
                if any(keyword.lower() in key.lower() for keyword in oi_keywords):
                    found.append(current_path)
                    result['has_oi_fields'] = True
                
                # Recursively check nested dicts
                if isinstance(value, dict):
                    found.extend(check_dict_for_oi(value, current_path))
                elif isinstance(value, list) and len(value) > 0 and isinstance(value[0], dict):
                    found.extend(check_dict_for_oi(value[0], f"{current_path}[0]"))
            
            return found
        
        if isinstance(result['sample_item'], dict):
            result['oi_fields_found'] = check_dict_for_oi(result['sample_item'])
        
    except Exception as e:
        result['error'] = str(e)
        result['success'] = False
    
    return result


def create_results_table(results: List[Dict[str, Any]]) -> Table:
    """Create a table showing test results."""
    table = Table(title="🔍 Aster API Endpoint Test Results", box=box.ROUNDED)
    table.add_column("Endpoint", style="cyan", no_wrap=True)
    table.add_column("Status", style="white", justify="center")
    table.add_column("Response Type", style="dim", no_wrap=True)
    table.add_column("Items", style="dim", justify="right")
    table.add_column("OI Fields", style="green" if any(r.get('has_oi_fields') for r in results) else "red", justify="center")
    table.add_column("Error", style="red", no_wrap=False)
    
    for result in results:
        status = "✅" if result['success'] else "❌"
        oi_status = "✅" if result.get('has_oi_fields') else "❌"
        response_type = result.get('response_type', 'N/A')
        item_count = str(result.get('symbol_count', 0))
        error = result.get('error', '')
        
        table.add_row(
            result['endpoint'],
            status,
            response_type,
            item_count,
            oi_status,
            error[:50] + "..." if len(error) > 50 else error
        )
    
    return table


def create_oi_fields_table(results: List[Dict[str, Any]]) -> Table:
    """Create a table showing OI fields found."""
    table = Table(title="📊 Open Interest Fields Found", box=box.ROUNDED)
    table.add_column("Endpoint", style="cyan", no_wrap=True)
    table.add_column("OI Fields", style="green", no_wrap=False)
    
    for result in results:
        if result.get('has_oi_fields') and result.get('oi_fields_found'):
            fields = ", ".join(result['oi_fields_found'])
            table.add_row(result['endpoint'], fields)
    
    if not any(r.get('has_oi_fields') for r in results):
        table.add_row("None", "No OI fields found in any endpoint")
    
    return table


def create_sample_response_panel(result: Dict[str, Any]) -> Panel:
    """Create a panel showing sample response structure."""
    if not result.get('success') or not result.get('sample_item'):
        return Panel("No sample data available", title=f"Sample: {result['endpoint']}")
    
    sample = result['sample_item']
    formatted = format_response(sample, max_depth=2)
    
    return Panel(
        formatted,
        title=f"Sample Response: {result['endpoint']}",
        border_style="blue"
    )


def main():
    """Main test function."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Test Aster API endpoints for open interest data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python test_aster_open_interest.py              # Test all endpoints with all symbols
  python test_aster_open_interest.py BTCUSDT      # Test all endpoints for BTCUSDT
        """
    )
    
    parser.add_argument(
        'symbol',
        nargs='?',
        help='Optional symbol to test (e.g., BTCUSDT). If not provided, fetches all symbols.'
    )
    
    args = parser.parse_args()
    
    console.print("\n[bold cyan]🔍 Testing Aster API Endpoints for Open Interest Data...[/bold cyan]\n")
    
    # Initialize Aster client
    try:
        client = AsterClient(base_url="https://fapi.asterdex.com", timeout=10)
    except Exception as e:
        console.print(f"[red]❌ Failed to initialize Aster client: {e}[/red]")
        sys.exit(1)
    
    results = []
    
    # Test 1: Current endpoints being used
    console.print("[yellow]Testing current endpoints...[/yellow]")
    
    # Test ticker_24hr_price_change (currently used for volume)
    result = test_endpoint(
        client,
        "ticker_24hr_price_change",
        client.ticker_24hr_price_change,
        symbol=args.symbol
    )
    results.append(result)
    
    # Test mark_price (currently used for funding rates)
    result = test_endpoint(
        client,
        "mark_price",
        client.mark_price,
        symbol=args.symbol
    )
    results.append(result)
    
    # Test 2: Potential new endpoints
    console.print("[yellow]Testing potential OI endpoints...[/yellow]")
    
    # Test /fapi/v1/openInterest (user's suggestion)
    result = test_direct_endpoint(
        client,
        "/fapi/v1/openInterest",
        symbol=args.symbol
    )
    results.append(result)
    
    # Test /fapi/v1/openInterestStatistics (alternative)
    result = test_direct_endpoint(
        client,
        "/fapi/v1/openInterestStatistics",
        symbol=args.symbol
    )
    results.append(result)
    
    # Test /fapi/v1/openInterestHist (historical OI)
    result = test_direct_endpoint(
        client,
        "/fapi/v1/openInterestHist",
        symbol=args.symbol
    )
    results.append(result)
    
    # Test /fapi/v1/premiumIndex (mark price - might have OI)
    result = test_direct_endpoint(
        client,
        "/fapi/v1/premiumIndex",
        symbol=args.symbol
    )
    results.append(result)
    
    # Test /fapi/v1/ticker/bookTicker (book ticker - might have OI)
    result = test_endpoint(
        client,
        "book_ticker",
        client.book_ticker,
        symbol=args.symbol
    )
    results.append(result)
    
    # Test /fapi/v1/exchangeInfo (exchange info - might have OI metadata)
    result = test_endpoint(
        client,
        "exchange_info",
        client.exchange_info,
        symbol=args.symbol
    )
    results.append(result)
    
    # Display results
    console.print()
    console.print(create_results_table(results))
    console.print()
    
    # Show OI fields found
    oi_results = [r for r in results if r.get('has_oi_fields')]
    if oi_results:
        console.print(create_oi_fields_table(oi_results))
        console.print()
        
        # Show sample responses for endpoints with OI fields
        console.print("[bold green]✅ Found OI fields! Showing sample responses:[/bold green]\n")
        for result in oi_results:
            console.print(create_sample_response_panel(result))
            console.print()
    else:
        console.print(Panel(
            "[yellow]⚠️  No OI fields found in any endpoint[/yellow]\n\n"
            "This suggests that:\n"
            "1. The endpoint might not exist\n"
            "2. OI data might be in a different format/field name\n"
            "3. OI data might require authentication\n"
            "4. OI data might be available through a different API version",
            title="No OI Fields Found",
            border_style="yellow"
        ))
        console.print()
        
        # Show sample responses for all successful endpoints to help identify structure
        console.print("[bold cyan]Showing sample responses for analysis:[/bold cyan]\n")
        for result in results:
            if result.get('success') and result.get('sample_item'):
                console.print(create_sample_response_panel(result))
                console.print()
    
    # Summary
    successful = sum(1 for r in results if r.get('success'))
    with_oi = sum(1 for r in results if r.get('has_oi_fields'))
    
    console.print(Panel(
        f"[cyan]Total endpoints tested: {len(results)}[/cyan]\n"
        f"[green]Successful: {successful}[/green]\n"
        f"[{'green' if with_oi > 0 else 'red'}]Found OI fields: {with_oi}[/{'green' if with_oi > 0 else 'red'}]",
        title="Summary",
        border_style="cyan"
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

