#!/usr/bin/env python3
"""
Standalone script to close all open positions on Lighter exchange.

This script is useful for handling database sync issues where positions might
be out of sync between the database and the exchange. It fetches all open
positions directly from Lighter and closes them using market orders.

Usage:
    python scripts/close_lighter_positions.py [--account-index ACCOUNT_INDEX] [--dry-run]

Environment Variables:
    API_KEY_PRIVATE_KEY: Lighter API private key (required)
    LIGHTER_ACCOUNT_INDEX: Account index (default: 0)
    LIGHTER_API_KEY_INDEX: API key index (default: 0)

Options:
    --account-index: Override account index from environment
    --dry-run: Show what would be closed without actually closing positions
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from contextlib import suppress
from decimal import Decimal
from pathlib import Path
from typing import Dict, Any, List

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from exchange_clients.lighter import LighterClient
from helpers.unified_logger import get_core_logger
from trading_bot import TradingConfig


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Close all open positions on Lighter exchange"
    )
    parser.add_argument(
        "--account-index",
        type=int,
        help="Account index (overrides LIGHTER_ACCOUNT_INDEX env var)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show positions that would be closed without actually closing them",
    )
    return parser.parse_args()


async def get_open_positions(client: LighterClient) -> List[Dict[str, Any]]:
    """
    Fetch all open positions from Lighter exchange.
    
    Returns:
        List of position dictionaries with non-zero quantities
    """
    if not client.position_manager:
        raise RuntimeError("Position manager not initialized")
    
    all_positions = await client.position_manager.get_detailed_positions()
    
    # Filter to only positions with non-zero quantity
    open_positions = []
    for pos in all_positions:
        position_qty = pos.get("position", Decimal("0"))
        if abs(position_qty) > Decimal("0.0001"):  # Threshold for "open" position
            open_positions.append(pos)
    
    return open_positions


async def close_position(
    client: LighterClient,
    position: Dict[str, Any],
    logger: Any,
) -> bool:
    """
    Close a single position using a market order.
    
    Args:
        client: Lighter client instance
        position: Position dictionary with market_id, position, symbol
        logger: Logger instance
        
    Returns:
        True if successful, False otherwise
    """
    market_id = position.get("market_id")
    position_qty = position.get("position", Decimal("0"))
    symbol = position.get("symbol", "UNKNOWN")
    
    if market_id is None:
        logger.error(f"Position for {symbol} has no market_id, skipping")
        return False
    
    # Determine side: if position is positive (long), we need to SELL to close
    # If position is negative (short), we need to BUY to close
    if position_qty > 0:
        side = "SELL"
    elif position_qty < 0:
        side = "BUY"
        position_qty = abs(position_qty)  # Make quantity positive for order
    else:
        logger.warning(f"Position for {symbol} has zero quantity, skipping")
        return False
    
    logger.info(
        f"Closing position: {symbol} | "
        f"Market ID: {market_id} | "
        f"Side: {side} | "
        f"Quantity: {position_qty}"
    )
    
    try:
        # Load market configuration to set base_amount_multiplier and price_multiplier
        # This is required before placing orders - the order manager needs these multipliers
        if client.market_data:
            try:
                await client.market_data.get_contract_attributes(symbol)
                logger.debug(f"Loaded market configuration for {symbol}")
            except Exception as config_error:
                logger.warning(
                    f"Failed to load market config for {symbol} via symbol lookup: {config_error}"
                )
                # Fallback: fetch market details directly using market_id to get multipliers
                import lighter
                order_api = lighter.OrderApi(client.api_client)
                market_summary = await order_api.order_book_details(market_id=market_id)
                if market_summary and market_summary.order_book_details:
                    market_detail = market_summary.order_book_details[0]
                    # Set multipliers directly on client (as integers, compatible with Decimal)
                    base_multiplier = pow(10, market_detail.supported_size_decimals)
                    price_multiplier = pow(10, market_detail.supported_price_decimals)
                    client.base_amount_multiplier = base_multiplier
                    client.price_multiplier = price_multiplier
                    logger.debug(
                        f"Loaded multipliers from market_id {market_id}: "
                        f"base={base_multiplier}, price={price_multiplier}"
                    )
                else:
                    raise ValueError(f"Could not fetch market details for market_id {market_id}")
        
        # Verify multipliers are set before placing order
        if client.base_amount_multiplier is None or client.price_multiplier is None:
            raise ValueError(
                f"Market multipliers not loaded for {symbol}. "
                f"base_amount_multiplier={client.base_amount_multiplier}, "
                f"price_multiplier={client.price_multiplier}"
            )
        
        # Place market order with reduce_only=True to close the position
        result = await client.place_market_order(
            contract_id=str(market_id),
            quantity=position_qty,
            side=side,
            reduce_only=True,
        )
        
        if result.success:
            logger.info(f"✅ Successfully closed position: {symbol}")
            return True
        else:
            logger.error(
                f"❌ Failed to close position {symbol}: {result.error_message}"
            )
            return False
            
    except Exception as e:
        logger.error(f"❌ Error closing position {symbol}: {e}")
        return False


async def main() -> None:
    args = parse_args()
    logger = get_core_logger("close_lighter_positions")
    
    # Load environment variables
    load_dotenv(PROJECT_ROOT / ".env")
    
    # Get credentials
    api_key_private_key = os.getenv("API_KEY_PRIVATE_KEY")
    if not api_key_private_key:
        logger.error("API_KEY_PRIVATE_KEY environment variable is required")
        sys.exit(1)
    
    # Get account index
    if args.account_index is not None:
        account_index = args.account_index
    else:
        account_index_str = os.getenv("LIGHTER_ACCOUNT_INDEX", "0")
        account_index = int(account_index_str) if account_index_str else 0
    
    # Create minimal TradingConfig for LighterClient
    # We need at least a ticker field for logging, but it won't be used for position closing
    config = TradingConfig(
        ticker="ALL",  # Placeholder, not used for closing positions
        contract_id="",  # Will be set per-position using market_id
        quantity=Decimal("0"),  # Not used for closing positions
        tick_size=Decimal("0"),  # Not used for closing positions
        exchange="lighter",
        strategy="close_positions",  # Placeholder
        strategy_params={},
    )
    
    # Initialize Lighter client
    client = LighterClient(
        config=config,
        api_key_private_key=api_key_private_key,
        account_index=account_index,
    )
    
    try:
        # Connect to Lighter
        logger.info(f"Connecting to Lighter (account index: {account_index})...")
        await client.connect()
        logger.info("✅ Connected to Lighter")
        
        # Fetch open positions
        logger.info("Fetching open positions...")
        positions = await get_open_positions(client)
        
        if not positions:
            logger.info("ℹ️  No open positions found")
            return
        
        logger.info(f"Found {len(positions)} open position(s)")
        
        # Display positions
        print("\n" + "=" * 80)
        print("Open Positions:")
        print("=" * 80)
        for i, pos in enumerate(positions, 1):
            symbol = pos.get("symbol", "UNKNOWN")
            position_qty = pos.get("position", Decimal("0"))
            market_id = pos.get("market_id", "N/A")
            avg_entry = pos.get("avg_entry_price", Decimal("0"))
            unrealized_pnl = pos.get("unrealized_pnl", Decimal("0"))
            
            print(
                f"{i}. {symbol} | "
                f"Qty: {position_qty} | "
                f"Market ID: {market_id} | "
                f"Entry: ${avg_entry:.4f} | "
                f"Unrealized PnL: ${unrealized_pnl:.2f}"
            )
        print("=" * 80 + "\n")
        
        if args.dry_run:
            logger.info("🔍 DRY RUN: Would close the above positions")
            return
        
        # Close each position
        logger.info(f"Closing {len(positions)} position(s) using market orders...")
        success_count = 0
        fail_count = 0
        
        for pos in positions:
            symbol = pos.get("symbol", "UNKNOWN")
            success = await close_position(client, pos, logger)
            if success:
                success_count += 1
            else:
                fail_count += 1
            
            # Small delay between orders to avoid rate limiting
            if len(positions) > 1:
                await asyncio.sleep(1)
        
        # Summary
        print("\n" + "=" * 80)
        print("Summary:")
        print("=" * 80)
        print(f"✅ Successfully closed: {success_count}")
        if fail_count > 0:
            print(f"❌ Failed to close: {fail_count}")
        print("=" * 80)
        
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        sys.exit(1)
    finally:
        # Disconnect
        with suppress(Exception):
            await client.disconnect()
        logger.info("Disconnected from Lighter")


if __name__ == "__main__":
    asyncio.run(main())

