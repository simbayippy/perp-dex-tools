#!/usr/bin/env python3
"""
Test script to validate get_user_trade_history() endpoint for exchanges.

Usage:
    # Test all exchanges with default symbol (BTC) and 7 days
    python scripts/test_trade_history.py
    
    # Test specific exchange and symbol with account from database
    python scripts/test_trade_history.py --exchange lighter --symbol RESOLV --account acc1
    
    # Test with custom time range and debug logging
    python scripts/test_trade_history.py --exchange lighter --symbol RESOLV --days 1 --debug --account acc1
    
    # Test with order ID filter
    python scripts/test_trade_history.py --exchange lighter --symbol RESOLV --order-id 12345 --account acc1
    
    # Test using environment variables (no --account flag)
    python scripts/test_trade_history.py --exchange lighter --symbol RESOLV --debug

This script:
1. Initializes exchange clients for Lighter, Aster, Paradex, and Backpack (or specific exchange)
2. Connects to the exchange(s)
3. Calls get_user_trade_history() with test parameters
4. Validates the response structure and prints results
5. Shows debug logs when --debug flag is used
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from pathlib import Path
from decimal import Decimal
from typing import Dict, Any, List, Optional

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from exchange_clients.factory import ExchangeFactory
from exchange_clients.base_models import TradeData
from helpers.unified_logger import get_core_logger
from trading_bot import TradingConfig

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box
from rich.text import Text

console = Console()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Test get_user_trade_history() endpoint for exchanges"
    )
    parser.add_argument(
        "--symbol",
        default="BTC",
        help="Symbol to query trade history for (default: BTC)",
    )
    parser.add_argument(
        "--exchange",
        type=str,
        default=None,
        choices=["lighter", "aster", "paradex", "backpack"],
        help="Specific exchange to test (default: test all exchanges)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Number of days to look back for trade history (default: 7)",
    )
    parser.add_argument(
        "--order-id",
        type=str,
        default=None,
        help="Optional order ID to filter trades (for testing order-specific queries)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging to see detailed API calls and responses",
    )
    parser.add_argument(
        "--account",
        "-a",
        type=str,
        default=None,
        help="Account name to load credentials from database (e.g., 'acc1'). "
             "If provided, credentials will be loaded from the database instead of env vars.",
    )
    parser.add_argument(
        "--env-file",
        type=str,
        default=".env",
        help="Path to the environment file (default: .env). Required for DATABASE_URL if using --account",
    )
    return parser.parse_args()


async def test_exchange_trade_history(
    exchange_name: str,
    client: Any,
    symbol: str,
    start_time: float,
    end_time: float,
    order_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Test get_user_trade_history() for a single exchange.
    
    Returns:
        Dictionary with test results including success status, trade count, and sample data
    """
    result = {
        "exchange": exchange_name,
        "success": False,
        "error": None,
        "trade_count": 0,
        "trades": [],
        "sample_trade": None,
    }
    
    try:
        console.print(f"\n[bold cyan]{'='*60}[/bold cyan]")
        console.print(f"[bold]Testing {exchange_name.upper()}[/bold]")
        console.print(f"[bold cyan]{'='*60}[/bold cyan]")
        
        # Call the trade history endpoint
        trades = await client.get_user_trade_history(
            symbol=symbol,
            start_time=start_time,
            end_time=end_time,
            order_id=order_id,
        )
        
        # Validate response
        if not isinstance(trades, list):
            result["error"] = f"Expected list, got {type(trades)}"
            console.print(f"[bold red]❌[/bold red] [bold]{exchange_name}:[/bold] Invalid response type - {result['error']}")
            return result
        
        result["trade_count"] = len(trades)
        result["trades"] = trades
        result["success"] = True
        
        # Validate TradeData structure
        if trades:
            sample = trades[0]
            result["sample_trade"] = {
                "trade_id": sample.trade_id,
                "timestamp": sample.timestamp,
                "symbol": sample.symbol,
                "side": sample.side,
                "quantity": str(sample.quantity),
                "price": str(sample.price),
                "fee": str(sample.fee),
                "fee_currency": sample.fee_currency,
                "order_id": sample.order_id,
                "realized_pnl": str(sample.realized_pnl) if sample.realized_pnl else None,
                "realized_funding": str(sample.realized_funding) if sample.realized_funding else None,
            }
            
            # Validate required fields
            required_fields = ["trade_id", "timestamp", "symbol", "side", "quantity", "price", "fee", "fee_currency"]
            missing_fields = []
            for field in required_fields:
                if not hasattr(sample, field) or getattr(sample, field) is None:
                    missing_fields.append(field)
            
            if missing_fields:
                result["error"] = f"Missing required fields: {missing_fields}"
                result["success"] = False
                console.print(f"[bold red]❌[/bold red] [bold]{exchange_name}:[/bold] {result['error']}")
                return result
        
        # Print results with Rich formatting
        console.print(f"\n[bold green]✅[/bold green] [bold]{exchange_name.upper()}:[/bold] Successfully fetched trade history")
        console.print(f"   Found [cyan]{len(trades)}[/cyan] trade(s)")
        
        if trades:
            # Calculate summary statistics
            total_buy_qty = Decimal("0")
            total_sell_qty = Decimal("0")
            total_buy_value = Decimal("0")
            total_sell_value = Decimal("0")
            total_fees = Decimal("0")
            buy_count = 0
            sell_count = 0
            
            for trade in trades:
                trade_value = trade.quantity * trade.price
                if trade.side.lower() == "buy":
                    total_buy_qty += trade.quantity
                    total_buy_value += trade_value
                    buy_count += 1
                elif trade.side.lower() == "sell":
                    total_sell_qty += trade.quantity
                    total_sell_value += trade_value
                    sell_count += 1
                total_fees += trade.fee
            
            # Calculate net PnL (simplified: sell_value - buy_value - fees)
            # Note: This is a rough estimate, actual PnL depends on entry prices
            net_value = total_sell_value - total_buy_value
            net_pnl = net_value - total_fees
            
            # Create summary table with Rich
            summary_table = Table(title="📊 Trade Summary", box=box.ROUNDED, show_header=True, header_style="bold magenta")
            summary_table.add_column("Metric", style="cyan", no_wrap=True)
            summary_table.add_column("Value", style="white", justify="right")
            
            summary_table.add_row("Total Trades", f"[bold]{len(trades)}[/bold]")
            summary_table.add_row("  ├─ Buy Trades", f"[green]{buy_count}[/green]")
            summary_table.add_row("  └─ Sell Trades", f"[red]{sell_count}[/red]")
            summary_table.add_section()
            summary_table.add_row("Buy Volume", f"{total_buy_qty:,.2f} [dim]{trades[0].symbol}[/dim]")
            summary_table.add_row("Sell Volume", f"{total_sell_qty:,.2f} [dim]{trades[0].symbol}[/dim]")
            summary_table.add_row("Net Volume", f"{total_sell_qty - total_buy_qty:,.2f} [dim]{trades[0].symbol}[/dim]")
            summary_table.add_section()
            summary_table.add_row("Buy Value", f"[green]${total_buy_value:,.2f}[/green]")
            summary_table.add_row("Sell Value", f"[red]${total_sell_value:,.2f}[/red]")
            summary_table.add_row("Net Value", f"${net_value:,.2f}")
            summary_table.add_section()
            summary_table.add_row("Total Fees", f"[yellow]${total_fees:,.4f}[/yellow] [dim]{trades[0].fee_currency}[/dim]")
            
            # Color code PnL
            pnl_style = "green" if net_pnl >= 0 else "red"
            pnl_sign = "+" if net_pnl >= 0 else ""
            summary_table.add_row("Estimated Net PnL", f"[{pnl_style}]{pnl_sign}${net_pnl:,.2f}[/{pnl_style}]")
            
            console.print()
            console.print(summary_table)
            
            # Create sample trade details table
            sample = trades[0]
            sample_table = Table(title="🔍 Sample Trade (First)", box=box.ROUNDED, show_header=False)
            sample_table.add_column("Field", style="cyan", no_wrap=True, width=20)
            sample_table.add_column("Value", style="white")
            
            sample_table.add_row("Trade ID", str(sample.trade_id))
            sample_table.add_row("Timestamp", f"{sample.timestamp:.3f} ({time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(sample.timestamp))})")
            sample_table.add_row("Symbol", f"[bold]{sample.symbol}[/bold]")
            sample_table.add_row("Side", f"[{'green' if sample.side.lower() == 'buy' else 'red'}]{sample.side.upper()}[/{'green' if sample.side.lower() == 'buy' else 'red'}]")
            sample_table.add_row("Quantity", f"{sample.quantity:,.2f}")
            sample_table.add_row("Price", f"${sample.price:,.6f}")
            sample_table.add_row("Trade Value", f"${sample.quantity * sample.price:,.2f}")
            sample_table.add_row("Fee", f"[yellow]${sample.fee:.4f}[/yellow] {sample.fee_currency}")
            if sample.order_id:
                sample_table.add_row("Order ID", str(sample.order_id))
            if sample.realized_pnl is not None:
                pnl_color = "green" if sample.realized_pnl >= 0 else "red"
                sample_table.add_row("Realized PnL", f"[{pnl_color}]${sample.realized_pnl:,.2f}[/{pnl_color}]")
            if sample.realized_funding is not None:
                sample_table.add_row("Realized Funding", f"${sample.realized_funding:,.2f}")
            
            console.print()
            console.print(sample_table)
        else:
            console.print(f"\n[yellow]⚠️[/yellow]  No trades found in the specified time range")
            console.print(f"   [dim](This is OK if you haven't traded recently)[/dim]")
        
        return result
        
    except Exception as e:
        result["error"] = str(e)
        result["success"] = False
        console.print(f"[bold red]❌[/bold red] [bold]{exchange_name}:[/bold] Error - {e}")
        import traceback
        console.print(f"[dim]   Traceback: {traceback.format_exc()}[/dim]")
        return result


async def load_account_credentials(account_name: str) -> dict:
    """Load account credentials from database."""
    try:
        from databases import Database
        from database.credential_loader import DatabaseCredentialLoader
    except ImportError as e:
        console.print(f"[bold red]Error:[/bold red] Failed to import database modules: {e}")
        console.print("[yellow]Ensure 'databases' and 'cryptography' are installed[/yellow]")
        sys.exit(1)
    
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        console.print("[bold red]Error:[/bold red] DATABASE_URL not set in environment")
        console.print("[yellow]Required for loading account credentials from database[/yellow]")
        sys.exit(1)
    
    db = Database(database_url)
    await db.connect()
    
    try:
        loader = DatabaseCredentialLoader(db)
        credentials = await loader.load_account_credentials(account_name)
        
        if not credentials:
            console.print(f"[bold red]Error:[/bold red] No credentials found for account '[cyan]{account_name}[/cyan]'")
            console.print("\n[yellow]Available accounts:[/yellow]")
            console.print("  Run: [dim]python database/scripts/accounts/list_accounts.py[/dim]")
            sys.exit(1)
        
        console.print(f"[green]✓[/green] Loaded credentials for account: [bold cyan]{account_name}[/bold cyan]")
        console.print(f"  Exchanges: [cyan]{', '.join(credentials.keys())}[/cyan]\n")
        return credentials
    except Exception as e:
        console.print(f"[bold red]Error loading account credentials from database:[/bold red] {e}")
        import traceback
        console.print(f"[dim]{traceback.format_exc()}[/dim]")
        sys.exit(1)
    finally:
        await db.disconnect()


async def main():
    args = parse_args()
    
    # Load environment variables (needed for DATABASE_URL if using --account)
    from dotenv import load_dotenv
    load_dotenv(args.env_file)
    
    # Set unified logger level BEFORE creating any clients (so debug logs work)
    if args.debug:
        os.environ['LOG_LEVEL'] = 'DEBUG'
    elif 'LOG_LEVEL' not in os.environ:
        os.environ['LOG_LEVEL'] = 'INFO'
    
    # Suppress noisy library debug logs (unless debug mode)
    log_level = logging.DEBUG if args.debug else logging.WARNING
    logging.getLogger('websockets').setLevel(log_level)
    logging.getLogger('httpcore').setLevel(log_level)
    logging.getLogger('httpx').setLevel(log_level)
    logging.getLogger('urllib3').setLevel(log_level)
    logging.getLogger('requests').setLevel(log_level)
    logging.getLogger('aiohttp').setLevel(log_level)
    logging.getLogger('asyncio').setLevel(log_level)
    
    logger = get_core_logger("test_trade_history")
    
    # Load account credentials if --account provided
    account_credentials = None
    if args.account:
        logger.info(f"Loading credentials for account: {args.account}")
        account_credentials = await load_account_credentials(args.account)
    
    # Calculate time range
    end_time = time.time()
    start_time = end_time - (args.days * 24 * 3600)  # days ago
    
    # Print header with Rich
    header_panel = Panel(
        f"[bold cyan]Symbol:[/bold cyan] {args.symbol}\n"
        f"[bold cyan]Exchange:[/bold cyan] {args.exchange.upper() if args.exchange else 'ALL (testing all exchanges)'}\n"
        f"[bold cyan]Account:[/bold cyan] {args.account + ' (from database)' if args.account else 'Using environment variables'}\n"
        f"[bold cyan]Time Range:[/bold cyan] {args.days} days\n"
        f"[bold cyan]Start Time:[/bold cyan] {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(start_time))}\n"
        f"[bold cyan]End Time:[/bold cyan] {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(end_time))}"
        + (f"\n[bold cyan]Order ID Filter:[/bold cyan] {args.order_id}" if args.order_id else "")
        + (f"\n[bold yellow]Debug Mode:[/bold yellow] ENABLED (detailed logging)" if args.debug else ""),
        title="[bold]Trade History Endpoint Test[/bold]",
        border_style="cyan",
        box=box.ROUNDED
    )
    console.print()
    console.print(header_panel)
    console.print()
    
    # Create a minimal config for testing
    # We just need ticker/symbol for the clients to initialize
    test_config = TradingConfig(
        ticker=args.symbol,
        contract_id=args.symbol,
        quantity=Decimal("0"),  # Not used for trade history testing
        tick_size=Decimal("0.01"),  # Not used for trade history testing
        exchange="lighter",  # Will be overridden per exchange
        strategy="test",
        strategy_params={},
    )
    
    # List of exchanges to test
    all_exchanges = ["lighter", "aster", "paradex", "backpack"]
    if args.exchange:
        exchanges_to_test = [args.exchange.lower()]
        logger.info(f"Testing single exchange: {args.exchange}")
    else:
        exchanges_to_test = all_exchanges
        logger.info(f"Testing all exchanges: {', '.join(exchanges_to_test)}")
    
    # Create exchange clients
    console.print("[bold cyan]Initializing exchange clients...[/bold cyan]")
    clients = {}
    for exchange_name in exchanges_to_test:
        try:
            # Create exchange-specific config
            from dataclasses import replace
            exchange_config = replace(test_config, exchange=exchange_name)
            
            # Get credentials for this exchange if account credentials were loaded
            exchange_creds = None
            if account_credentials and exchange_name in account_credentials:
                exchange_creds = account_credentials[exchange_name]
                logger.debug(f"Using database credentials for {exchange_name}")
            else:
                logger.debug(f"Using environment variables for {exchange_name}")
            
            client = ExchangeFactory.create_exchange(
                exchange_name=exchange_name,
                config=exchange_config,
                credentials=exchange_creds,
            )
            clients[exchange_name] = client
            console.print(f"[green]✅[/green] Created [cyan]{exchange_name}[/cyan] client")
        except Exception as e:
            console.print(f"[yellow]⚠️[/yellow]  Skipping [cyan]{exchange_name}[/cyan]: {e}")
            continue
    
    if not clients:
        console.print("\n[bold red]❌ No exchange clients could be created. Check your credentials.[/bold red]")
        return
    
    # Connect to all exchanges
    console.print("\n[bold cyan]Connecting to exchanges...[/bold cyan]")
    connected_clients = {}
    for exchange_name, client in clients.items():
        try:
            await client.connect()
            connected_clients[exchange_name] = client
            console.print(f"[green]✅[/green] Connected to [cyan]{exchange_name}[/cyan]")
        except Exception as e:
            console.print(f"[yellow]⚠️[/yellow]  Failed to connect to [cyan]{exchange_name}[/cyan]: {e}")
            continue
    
    if not connected_clients:
        console.print("\n[bold red]❌ No exchanges could be connected. Check your credentials and network.[/bold red]")
        return
    
    # Test trade history for each exchange
    console.print(f"\n[bold cyan]{'='*60}[/bold cyan]")
    console.print("[bold]Testing Trade History Endpoints[/bold]")
    console.print(f"[bold cyan]{'='*60}[/bold cyan]\n")
    
    results = {}
    for exchange_name, client in connected_clients.items():
        result = await test_exchange_trade_history(
            exchange_name=exchange_name,
            client=client,
            symbol=args.symbol,
            start_time=start_time,
            end_time=end_time,
            order_id=args.order_id,
        )
        results[exchange_name] = result
    
    # Summary
    successful = sum(1 for r in results.values() if r["success"])
    total = len(results)
    
    summary_table = Table(title="📋 Test Summary", box=box.ROUNDED, show_header=True, header_style="bold magenta")
    summary_table.add_column("Exchange", style="cyan", no_wrap=True)
    summary_table.add_column("Status", style="white", justify="center")
    summary_table.add_column("Result", style="white")
    
    for exchange_name, result in results.items():
        if result["success"]:
            status_icon = "[bold green]✅[/bold green]"
            result_text = f"[green]{result['trade_count']} trade(s) found[/green]"
        else:
            status_icon = "[bold red]❌[/bold red]"
            result_text = f"[red]Error - {result['error']}[/red]"
        summary_table.add_row(exchange_name.upper(), status_icon, result_text)
    
    console.print()
    console.print(summary_table)
    
    # Overall stats
    stats_panel = Panel(
        f"[bold cyan]Exchanges Tested:[/bold cyan] {total}\n"
        f"[bold green]Successful:[/bold green] {successful}\n"
        f"[bold red]Failed:[/bold red] {total - successful}",
        title="[bold]Overall Statistics[/bold]",
        border_style="cyan",
        box=box.ROUNDED
    )
    console.print()
    console.print(stats_panel)
    
    # Cleanup
    console.print("\n[dim]Cleaning up connections...[/dim]")
    for client in connected_clients.values():
        try:
            if hasattr(client, "disconnect"):
                await client.disconnect()
        except Exception:
            pass
    
    console.print("\n[bold green]✅ Test complete![/bold green]")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        console.print("\n\n[yellow]⚠️[/yellow]  [bold]Test interrupted by user[/bold]")
        sys.exit(1)
    except Exception as e:
        console.print(f"\n\n[bold red]❌ Test failed with error:[/bold red] {e}")
        import traceback
        console.print(f"[dim]{traceback.format_exc()}[/dim]")
        sys.exit(1)

