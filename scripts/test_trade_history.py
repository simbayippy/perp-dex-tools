#!/usr/bin/env python3
"""
Test script to validate get_user_trade_history() endpoint for all exchanges.

Usage:
    python scripts/test_trade_history.py [--symbol BTC] [--days 7]

This script:
1. Initializes exchange clients for Lighter, Aster, Paradex, and Backpack
2. Connects to each exchange
3. Calls get_user_trade_history() with test parameters
4. Validates the response structure and prints results
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path
from decimal import Decimal
from typing import Dict, Any, List, Optional

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Load environment variables
load_dotenv()

from exchange_clients.factory import ExchangeFactory
from exchange_clients.base_models import TradeData
from helpers.unified_logger import get_core_logger
from trading_bot import TradingConfig


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Test get_user_trade_history() endpoint for all exchanges"
    )
    parser.add_argument(
        "--symbol",
        default="BTC",
        help="Symbol to query trade history for (default: BTC)",
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
        print(f"\n{'='*60}")
        print(f"Testing {exchange_name.upper()}")
        print(f"{'='*60}")
        
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
            print(f"❌ {exchange_name}: Invalid response type - {result['error']}")
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
                print(f"❌ {exchange_name}: {result['error']}")
                return result
        
        # Print results
        print(f"✅ {exchange_name}: Successfully fetched trade history")
        print(f"   Found {len(trades)} trade(s)")
        
        if trades:
            print(f"\n   Sample Trade:")
            sample = trades[0]
            print(f"   - Trade ID: {sample.trade_id}")
            print(f"   - Timestamp: {sample.timestamp} ({time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(sample.timestamp))})")
            print(f"   - Symbol: {sample.symbol}")
            print(f"   - Side: {sample.side}")
            print(f"   - Quantity: {sample.quantity}")
            print(f"   - Price: ${sample.price}")
            print(f"   - Fee: {sample.fee} {sample.fee_currency}")
            if sample.order_id:
                print(f"   - Order ID: {sample.order_id}")
            if sample.realized_pnl is not None:
                print(f"   - Realized PnL: ${sample.realized_pnl}")
            if sample.realized_funding is not None:
                print(f"   - Realized Funding: ${sample.realized_funding}")
        else:
            print(f"   ⚠️  No trades found in the specified time range")
            print(f"   (This is OK if you haven't traded recently)")
        
        return result
        
    except Exception as e:
        result["error"] = str(e)
        result["success"] = False
        print(f"❌ {exchange_name}: Error - {e}")
        import traceback
        print(f"   Traceback: {traceback.format_exc()}")
        return result


async def main():
    args = parse_args()
    
    # Suppress noisy library debug logs
    logging.getLogger('websockets').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('requests').setLevel(logging.WARNING)
    logging.getLogger('aiohttp').setLevel(logging.WARNING)
    logging.getLogger('asyncio').setLevel(logging.WARNING)
    
    logger = get_core_logger("test_trade_history")
    
    # Calculate time range
    end_time = time.time()
    start_time = end_time - (args.days * 24 * 3600)  # days ago
    
    print(f"\n{'='*60}")
    print(f"Trade History Endpoint Test")
    print(f"{'='*60}")
    print(f"Symbol: {args.symbol}")
    print(f"Time Range: {args.days} days")
    print(f"Start Time: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(start_time))}")
    print(f"End Time: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(end_time))}")
    if args.order_id:
        print(f"Order ID Filter: {args.order_id}")
    print(f"{'='*60}\n")
    
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
    exchanges_to_test = ["lighter", "aster", "paradex", "backpack"]
    
    # Create exchange clients
    print("Initializing exchange clients...")
    clients = {}
    for exchange_name in exchanges_to_test:
        try:
            # Create exchange-specific config
            from dataclasses import replace
            exchange_config = replace(test_config, exchange=exchange_name)
            
            client = ExchangeFactory.create_exchange(
                exchange_name=exchange_name,
                config=exchange_config,
            )
            clients[exchange_name] = client
            print(f"✅ Created {exchange_name} client")
        except Exception as e:
            print(f"⚠️  Skipping {exchange_name}: {e}")
            continue
    
    if not clients:
        print("\n❌ No exchange clients could be created. Check your credentials.")
        return
    
    # Connect to all exchanges
    print("\nConnecting to exchanges...")
    connected_clients = {}
    for exchange_name, client in clients.items():
        try:
            await client.connect()
            connected_clients[exchange_name] = client
            print(f"✅ Connected to {exchange_name}")
        except Exception as e:
            print(f"⚠️  Failed to connect to {exchange_name}: {e}")
            continue
    
    if not connected_clients:
        print("\n❌ No exchanges could be connected. Check your credentials and network.")
        return
    
    # Test trade history for each exchange
    print(f"\n{'='*60}")
    print("Testing Trade History Endpoints")
    print(f"{'='*60}\n")
    
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
    print(f"\n{'='*60}")
    print("Test Summary")
    print(f"{'='*60}\n")
    
    successful = sum(1 for r in results.values() if r["success"])
    total = len(results)
    
    print(f"Exchanges Tested: {total}")
    print(f"Successful: {successful}")
    print(f"Failed: {total - successful}\n")
    
    for exchange_name, result in results.items():
        status = "✅" if result["success"] else "❌"
        print(f"{status} {exchange_name.upper()}: ", end="")
        if result["success"]:
            print(f"{result['trade_count']} trade(s) found")
        else:
            print(f"Error - {result['error']}")
    
    # Cleanup
    print("\nCleaning up connections...")
    for client in connected_clients.values():
        try:
            if hasattr(client, "disconnect"):
                await client.disconnect()
        except Exception:
            pass
    
    print("\n✅ Test complete!")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n⚠️  Test interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

