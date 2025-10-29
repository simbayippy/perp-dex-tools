#!/usr/bin/env python3
"""
Standalone helper to exercise the Lighter websocket connection.

This script is useful for validating proxy behaviour independently of the
trading loop. It will:
  • Load account credentials (and optional proxy assignments) from the DB.
  • Enable the session proxy unless --disable-proxy is supplied.
  • Instantiate the Lighter exchange client and connect only the websocket.
  • Run until Ctrl+C (or until --duration seconds elapse) while streaming logs.

Example:
    python scripts/test_lighter_websocket.py --account acc1 --duration 300
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
from decimal import Decimal
from pathlib import Path
import sys

import dotenv

# Ensure project root is on sys.path so trading_bot/exchange_clients imports resolve
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from trading_bot import TradingConfig
from exchange_clients.factory import ExchangeFactory
from networking import SessionProxyManager
from runbot import load_account_context


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Test Lighter websocket connectivity")
    parser.add_argument(
        "--account",
        required=True,
        help="Account name to load credentials + proxies from the database",
    )
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Path to .env file (default: .env)",
    )
    parser.add_argument(
        "--ticker",
        default="BTC",
        help="Market symbol to subscribe to (default: BTC)",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=0,
        help="Optional runtime in seconds. 0 = run until Ctrl+C (default).",
    )
    parser.add_argument(
        "--enable-proxy",
        action="store_true",
        help="Enable the account's configured proxy for this test run (disabled by default)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Minimum log level to display (default: INFO)",
    )
    return parser


async def _sleep_until_stop(stop_event: asyncio.Event, duration: int) -> None:
    """Sleep until the stop_event is set, or the optional duration elapses."""
    if duration > 0:
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=duration)
        except asyncio.TimeoutError:
            stop_event.set()
    else:
        await stop_event.wait()


async def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    # Ensure logging configuration matches requested level
    os.environ.setdefault("LOG_LEVEL", args.log_level)

    # Load .env for DATABASE_URL / credentials
    dotenv.load_dotenv(args.env_file)

    # Pull account credentials + proxy assignments from the DB
    credentials, proxy_selector = await load_account_context(args.account)

    # Enable proxy (if available)
    proxy_used = None
    if proxy_selector and args.enable_proxy:
        proxy = proxy_selector.current_proxy()
        if proxy:
            SessionProxyManager.enable(proxy)
            proxy_used = proxy.url_with_auth(mask_password=True)
            print(f"[proxy] Enabled session proxy: {proxy_used}")
        else:
            print("[proxy] No active proxy assignment found; proceeding without proxy")
    elif args.enable_proxy:
        print("[proxy] Proxy usage requested but no proxies found; continuing without proxy")
    else:
        print("[proxy] Proxy usage disabled (use --enable-proxy to test through proxy)")

    # Extract Lighter credentials
    lighter_creds = credentials.get("lighter")
    if not lighter_creds:
        raise SystemExit("Selected account does not have Lighter credentials configured.")

    # Minimal TradingConfig so ExchangeFactory can initialise the client
    trading_config = TradingConfig(
        ticker=args.ticker.upper(),
        contract_id="",
        quantity=Decimal("1"),
        tick_size=Decimal("0"),
        exchange="lighter",
        strategy="ws_monitor",
        strategy_params={"_account_name": args.account},
        order_notional_usd=None,
        target_leverage=None,
    )

    client = ExchangeFactory.create_exchange("lighter", trading_config, lighter_creds)

    # We'll listen for keyboard interrupts to exit cleanly
    stop_event = asyncio.Event()

    def _handle_signal(*_: object) -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGINT, _handle_signal)
    loop.add_signal_handler(signal.SIGTERM, _handle_signal)

    try:
        print("[lighter] Connecting…")
        await client.connect()
        print("[lighter] Websocket connected. Watch the logs above for any reconnects.")
        if proxy_used:
            print(f"[lighter] Proxy in use: {proxy_used}")
        if args.duration > 0:
            print(f"[lighter] Running for {args.duration} seconds… (Ctrl+C to stop early)")
        else:
            print("[lighter] Running until Ctrl+C…")

        await _sleep_until_stop(stop_event, args.duration)
    finally:
        print("[lighter] Disconnecting…")
        try:
            await client.disconnect()
        finally:
            if SessionProxyManager.is_active():
                SessionProxyManager.disable()
        print("[lighter] Shutdown complete.")


if __name__ == "__main__":
    asyncio.run(main())
