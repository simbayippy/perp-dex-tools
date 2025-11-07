#!/usr/bin/env python3
"""
Debug script to inspect Paradex WebSocket ORDER_BOOK stream messages.

This script connects to Paradex WebSocket and subscribes to the ORDER_BOOK channel
for RESOLV-USD-PERP to see the raw message format and price/size values.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from paradex_py import Paradex
from paradex_py.api.ws_client import ParadexWebsocketChannel


async def order_book_callback(ws_channel, message: dict) -> None:
    """
    Callback for ORDER_BOOK WebSocket messages.
    Logs the raw message structure and parsed values.
    """
    print("\n" + "=" * 80)
    print("📨 ORDER_BOOK WebSocket Message Received")
    print("=" * 80)
    
    # Print full raw message
    print("\n🔍 RAW MESSAGE (JSON):")
    print(json.dumps(message, indent=2))
    
    # Extract and parse data
    params = message.get('params', {})
    data = params.get('data', {})
    
    print("\n📊 PARSED DATA:")
    print(f"  Market: {data.get('market')}")
    print(f"  Update Type: {data.get('update_type')}")
    print(f"  Seq No: {data.get('seq_no')}")
    print(f"  Last Updated At: {data.get('last_updated_at')}")
    
    # Parse inserts
    inserts = data.get('inserts', [])
    print(f"\n📥 INSERTS ({len(inserts)} items):")
    for i, insert in enumerate(inserts[:5]):  # Show first 5
        price_str = insert.get('price', 'N/A')
        size_str = insert.get('size', 'N/A')
        side = insert.get('side', 'N/A')
        
        # Try to parse as decimal
        try:
            price_decimal = float(price_str) if price_str != 'N/A' else None
            size_decimal = float(size_str) if size_str != 'N/A' else None
            
            # Check if it might be quantum (divide by 10^8)
            if price_decimal:
                price_human = price_decimal / (10 ** 8)
                print(f"  [{i}] {side}: price='{price_str}' ({price_decimal}) -> human={price_human}")
            else:
                print(f"  [{i}] {side}: price='{price_str}'")
            
            if size_decimal:
                size_human = size_decimal / (10 ** 8)
                print(f"       size='{size_str}' ({size_decimal}) -> human={size_human}")
            else:
                print(f"       size='{size_str}'")
        except (ValueError, TypeError):
            print(f"  [{i}] {side}: price='{price_str}', size='{size_str}' (could not parse)")
    
    if len(inserts) > 5:
        print(f"  ... and {len(inserts) - 5} more inserts")
    
    # Parse updates
    updates = data.get('updates', [])
    if updates:
        print(f"\n🔄 UPDATES ({len(updates)} items):")
        for i, update in enumerate(updates[:3]):  # Show first 3
            price_str = update.get('price', 'N/A')
            size_str = update.get('size', 'N/A')
            side = update.get('side', 'N/A')
            print(f"  [{i}] {side}: price='{price_str}', size='{size_str}'")
    
    # Parse deletes
    deletes = data.get('deletes', [])
    if deletes:
        print(f"\n🗑️  DELETES ({len(deletes)} items):")
        for i, delete in enumerate(deletes[:3]):  # Show first 3
            price_str = delete.get('price', 'N/A')
            side = delete.get('side', 'N/A')
            print(f"  [{i}] {side}: price='{price_str}'")
    
    print("\n" + "=" * 80 + "\n")


async def bbo_callback(ws_channel, message: dict) -> None:
    """
    Callback for BBO WebSocket messages.
    """
    print("\n" + "=" * 80)
    print("📨 BBO WebSocket Message Received")
    print("=" * 80)
    print("\n🔍 RAW MESSAGE (JSON):")
    print(json.dumps(message, indent=2))
    
    params = message.get('params', {})
    data = params.get('data', {})
    
    bid = data.get('bid') or data.get('best_bid')
    ask = data.get('ask') or data.get('best_ask')
    
    print(f"\n📊 BBO DATA:")
    print(f"  Market: {data.get('market')}")
    print(f"  Bid: {bid} (type: {type(bid).__name__})")
    print(f"  Ask: {ask} (type: {type(ask).__name__})")
    
    # Try quantum conversion
    if bid:
        try:
            bid_float = float(bid)
            bid_human = bid_float / (10 ** 8)
            print(f"  Bid (quantum->human): {bid_float} -> {bid_human}")
        except (ValueError, TypeError):
            pass
    
    if ask:
        try:
            ask_float = float(ask)
            ask_human = ask_float / (10 ** 8)
            print(f"  Ask (quantum->human): {ask_float} -> {ask_human}")
        except (ValueError, TypeError):
            pass
    
    print("=" * 80 + "\n")


async def main():
    """Main function to connect and subscribe to Paradex WebSocket."""
    # Hardcoded to production
    env = "prod"
    
    print("🚀 Starting Paradex WebSocket ORDER_BOOK Debug Script")
    print(f"📡 Environment: {env}")
    print(f"🎯 Market: RESOLV-USD-PERP")
    print("\n" + "=" * 80)
    
    # Initialize Paradex client (no auth needed for public order book)
    paradex = Paradex(env=env, logger=None)
    
    # Connect to WebSocket
    print("\n🔌 Connecting to WebSocket...")
    is_connected = False
    attempts = 0
    while not is_connected and attempts < 5:
        try:
            is_connected = await paradex.ws_client.connect()
            if is_connected:
                print("✅ Connected successfully!")
            else:
                print(f"❌ Connection failed (attempt {attempts + 1}/5)")
                await asyncio.sleep(1)
        except Exception as e:
            print(f"❌ Connection error: {e}")
            await asyncio.sleep(1)
        attempts += 1
    
    if not is_connected:
        print("❌ Failed to connect after 5 attempts. Exiting.")
        return
    
    # Wait a moment for connection to stabilize
    await asyncio.sleep(2)
    
    # Subscribe to ORDER_BOOK channel
    market = "RESOLV-USD-PERP"
    print(f"\n📡 Subscribing to ORDER_BOOK channel for {market}...")
    
    try:
        await paradex.ws_client.subscribe(
            ParadexWebsocketChannel.ORDER_BOOK,
            callback=order_book_callback,
            params={
                "market": market,
                "depth": 15,
                "refresh_rate": "100ms",
                "price_tick": "0_1",
            }
        )
        print("✅ Subscribed to ORDER_BOOK channel")
    except Exception as e:
        print(f"❌ Failed to subscribe to ORDER_BOOK: {e}")
        return
    
    # Also subscribe to BBO channel for comparison
    print(f"\n📡 Subscribing to BBO channel for {market}...")
    try:
        await paradex.ws_client.subscribe(
            ParadexWebsocketChannel.BBO,
            callback=bbo_callback,
            params={"market": market}
        )
        print("✅ Subscribed to BBO channel")
    except Exception as e:
        print(f"⚠️  Failed to subscribe to BBO: {e}")
    
    print("\n" + "=" * 80)
    print("👂 Listening for WebSocket messages...")
    print("   (Press Ctrl+C to stop)")
    print("=" * 80 + "\n")
    
    # Keep running and receiving messages
    try:
        await asyncio.sleep(300)  # Run for 5 minutes, or until interrupted
    except KeyboardInterrupt:
        print("\n\n🛑 Interrupted by user")
    finally:
        print("\n🔌 Closing WebSocket connection...")
        try:
            await paradex.ws_client._close_connection()
            print("✅ Disconnected")
        except Exception as e:
            print(f"⚠️  Error during disconnect: {e}")


if __name__ == "__main__":
    asyncio.run(main())

