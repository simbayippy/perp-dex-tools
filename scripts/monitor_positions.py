#!/usr/bin/env python3
"""Utility script to refresh funding-arb positions without opening new trades.

Usage:
    python scripts/monitor_positions.py --config configs/real_funding_test.yml \
        [--symbol ZORA] [--interval 60]

The script loads the trading configuration, builds the required exchange clients,
and runs the `PositionMonitor` so the latest on-exchange state is merged into the
stored positions. It prints a compact summary after each refresh and logs the
detailed per-leg updates via the unified logger.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from contextlib import suppress
from pathlib import Path
from decimal import Decimal
from typing import Iterable, Optional

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from helpers.unified_logger import get_core_logger

from trading_config.config_yaml import load_config_from_yaml
from runbot import _config_dict_to_trading_config  # type: ignore
from exchange_clients.factory import ExchangeFactory

from strategies.implementations.funding_arbitrage.position_manager import (
    FundingArbPositionManager,
)
from strategies.implementations.funding_arbitrage.position_monitor import (
    PositionMonitor,
)

try:
    from funding_rate_service.database.connection import database
    from funding_rate_service.database.repositories import FundingRateRepository
    FUNDING_DB_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency during tests
    FUNDING_DB_AVAILABLE = False
    database = None  # type: ignore
    FundingRateRepository = None  # type: ignore


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Monitor existing funding-arb positions")
    parser.add_argument("--config", required=True, help="Path to trading config YAML")
    parser.add_argument(
        "--symbol",
        help="Filter to a specific symbol (default: latest position regardless of symbol)",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=0,
        help="Refresh interval in seconds (0 = run once and exit)",
    )
    return parser.parse_args()


async def ensure_database_connected(logger) -> None:
    if not FUNDING_DB_AVAILABLE:
        logger.warning("Funding service database not available; monitor cannot run.")
        raise SystemExit(1)
    if not database.is_connected:
        await database.connect()
        logger.info("📡 Connected to funding_rate_service database")


async def build_exchange_clients(trading_config, exchange_list: Iterable[str]):
    mandatory_exchange = (
        trading_config.strategy_params.get("mandatory_exchange")
        or trading_config.strategy_params.get("primary_exchange")
    )
    if isinstance(mandatory_exchange, str) and mandatory_exchange.strip():
        mandatory_exchange = mandatory_exchange.strip().lower()
    else:
        mandatory_exchange = None

    clients = ExchangeFactory.create_multiple_exchanges(
        exchange_names=[ex.lower() for ex in exchange_list],
        config=trading_config,
        primary_exchange=mandatory_exchange,
    )
    for name, client in clients.items():
        await client.connect()
        print(f"✅ Connected to {name}")
    return clients


async def run_monitor(args: argparse.Namespace) -> None:
    logger = get_core_logger("funding_arb_manual_monitor")

    load_dotenv(PROJECT_ROOT / ".env")

    config_path = Path(args.config).expanduser()
    loaded = load_config_from_yaml(config_path)
    strategy_name = loaded["strategy"].lower()
    if strategy_name != "funding_arbitrage":
        raise SystemExit("This helper only supports funding_arbitrage configs")

    config_payload = loaded["config"]
    if "primary_exchange" in config_payload and "mandatory_exchange" not in config_payload:
        config_payload["mandatory_exchange"] = config_payload.pop("primary_exchange")
    if not config_payload.get("mandatory_exchange"):
        config_payload["mandatory_exchange"] = None
        config_payload["max_oi_usd"] = None
    config_payload.pop("primary_exchange", None)

    trading_config = _config_dict_to_trading_config(loaded["strategy"], config_payload)
    scan_exchanges = config_payload.get("scan_exchanges") or config_payload.get("exchanges")
    if isinstance(scan_exchanges, str):
        exchange_list = [ex.strip() for ex in scan_exchanges.split(",") if ex.strip()]
    elif scan_exchanges:
        exchange_list = [str(ex).strip() for ex in scan_exchanges if str(ex).strip()]
    else:
        fallback_exchange = config_payload.get("mandatory_exchange") or config_payload.get("primary_exchange")
        exchange_list = [fallback_exchange.strip()] if isinstance(fallback_exchange, str) and fallback_exchange.strip() else []

    mandatory_exchange = config_payload.get("mandatory_exchange") or config_payload.get("primary_exchange")
    if isinstance(mandatory_exchange, str) and mandatory_exchange.strip():
        mandatory_exchange = mandatory_exchange.strip().lower()
        if mandatory_exchange not in [ex.lower() for ex in exchange_list]:
            exchange_list.append(mandatory_exchange)

    if not exchange_list:
        raise SystemExit("Monitor requires at least one exchange via 'scan_exchanges'.")

    await ensure_database_connected(logger)
    exchange_clients = await build_exchange_clients(trading_config, exchange_list)

    position_manager = FundingArbPositionManager()
    await position_manager.initialize()

    funding_repo = FundingRateRepository(database)
    monitor = PositionMonitor(
        position_manager=position_manager,
        funding_rate_repo=funding_repo,
        exchange_clients=exchange_clients,
        logger=logger,
    )

    try:
        iteration = 0
        while True:
            iteration += 1
            await monitor.monitor()

            target = await _select_position(position_manager, args.symbol)
            if target:
                _print_position_snapshot(target, iteration)
            else:
                print("ℹ️  No matching open positions found.")

            if args.interval <= 0:
                break
            await asyncio.sleep(args.interval)
    finally:
        for client in exchange_clients.values():
            with suppress(Exception):
                await client.disconnect()
        if database.is_connected:
            with suppress(Exception):
                await database.disconnect()


async def _select_position(manager: FundingArbPositionManager, symbol: Optional[str]):
    positions = await manager.get_open_positions()
    if symbol:
        positions = [p for p in positions if p.symbol.lower() == symbol.lower()]
    positions.sort(key=lambda p: p.opened_at, reverse=True)
    return positions[0] if positions else None


def _print_position_snapshot(position, iteration: int) -> None:
    legs = position.metadata.get("legs", {})
    long_leg = legs.get(position.long_dex, {})
    short_leg = legs.get(position.short_dex, {})
    entry_div = position.entry_divergence or Decimal("0")
    current_div = position.current_divergence or Decimal("0")

    print("\n" + "═" * 70)
    print(f"Refresh #{iteration}: {position.symbol} | opened {position.opened_at:%Y-%m-%d %H:%M:%S}Z")
    print(
        f"Entry divergence={entry_div*Decimal('100'):.3f}%  "
        f"Current={current_div*Decimal('100'):.3f}%  "
        f"PnL=${position.get_net_pnl():.2f}"
    )
    print(
        f"Fees=${position.total_fees_paid:.4f}  Funding=${position.cumulative_funding:.4f}"
    )
    print("-- Long leg:", position.long_dex)
    print(
        f"   qty={long_leg.get('quantity')}  entry={long_leg.get('entry_price')}  "
        f"mark={long_leg.get('mark_price')}  funding={long_leg.get('funding_accrued')}"
    )
    print("-- Short leg:", position.short_dex)
    print(
        f"   qty={short_leg.get('quantity')}  entry={short_leg.get('entry_price')}  "
        f"mark={short_leg.get('mark_price')}  funding={short_leg.get('funding_accrued')}"
    )
    print("═" * 70)


def main() -> None:
    args = parse_args()
    asyncio.run(run_monitor(args))


if __name__ == "__main__":
    main()
