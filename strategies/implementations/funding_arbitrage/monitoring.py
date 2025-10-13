"""
Position monitoring utilities for the funding arbitrage strategy.

Responsible for:
 - Refreshing funding divergence via the funding rate repository.
 - Polling exchanges for live leg metrics.
 - Updating the position manager with the latest state.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set, Tuple

from exchange_clients.base import BaseExchangeClient, ExchangePositionSnapshot

from .models import FundingArbPosition
from .position_manager import FundingArbPositionManager


class PositionMonitor:
    """Handles live position monitoring and metadata enrichment."""

    def __init__(
        self,
        position_manager: FundingArbPositionManager,
        funding_rate_repo: Any,
        exchange_clients: Dict[str, BaseExchangeClient],
        logger: Any,
    ) -> None:
        self._position_manager = position_manager
        self._funding_rate_repo = funding_rate_repo
        self._exchange_clients = exchange_clients
        self._logger = logger

    async def monitor(self) -> None:
        """Refresh open positions with latest funding rates and exchange data."""
        positions = await self._position_manager.get_open_positions()

        if not positions:
            self._logger.log("No open positions to monitor", "DEBUG")
            return

        exchange_snapshots = await self._fetch_exchange_position_snapshots(positions)

        for position in positions:
            try:
                rate1_data = rate2_data = None
                if self._funding_rate_repo is not None:
                    rate1_data = await self._funding_rate_repo.get_latest_specific(
                        position.long_dex, position.symbol
                    )
                    rate2_data = await self._funding_rate_repo.get_latest_specific(
                        position.short_dex, position.symbol
                    )

                if rate1_data and rate2_data:
                    rate1 = Decimal(str(rate1_data["funding_rate"]))
                    rate2 = Decimal(str(rate2_data["funding_rate"]))
                    position.current_divergence = rate2 - rate1
                    position.last_check = datetime.now()
                else:
                    self._refresh_position_leg_metrics(position, exchange_snapshots)
                    await self._position_manager.update_position(position)
                    self._logger.log(
                        f"Could not fetch rates for {position.symbol}",
                        "WARNING",
                    )
                    continue

                self._refresh_position_leg_metrics(position, exchange_snapshots)
                await self._position_manager.update_position(position)

                erosion = position.get_profit_erosion()
                self._logger.log(
                    (
                        f"Position {position.symbol}: "
                        f"Entry={position.entry_divergence*100:.3f}%, "
                        f"Current={position.current_divergence*100:.3f}%, "
                        f"Erosion={erosion*100:.1f}%, "
                        f"PnL=${position.get_net_pnl():.2f}"
                    ),
                    "INFO",
                )
            except Exception as exc:  # pragma: no cover - defensive logging
                self._logger.log(
                    f"Error monitoring position {position.id}: {exc}",
                    "ERROR",
                )

    async def _fetch_exchange_position_snapshots(
        self, positions: List[FundingArbPosition]
    ) -> Dict[Tuple[str, str], ExchangePositionSnapshot]:
        """
        Collect per-exchange snapshots for the symbols currently held.

        Returns:
            Mapping of (exchange, symbol) -> ExchangePositionSnapshot
        """
        if not positions:
            return {}

        clients_lower = {name.lower(): client for name, client in self._exchange_clients.items()}
        snapshot_lookup: Dict[Tuple[str, str], ExchangePositionSnapshot] = {}
        visited: Set[Tuple[str, str]] = set()

        for pos in positions:
            symbol_key = pos.symbol.upper()
            for dex in (pos.long_dex, pos.short_dex):
                if not dex:
                    continue

                dex_key = dex.lower()
                client = clients_lower.get(dex_key)
                if not client:
                    continue

                key = (dex_key, symbol_key)
                if key in visited:
                    continue
                visited.add(key)

                try:
                    self._logger.log(
                        f"[{dex_key.upper()}] Fetching snapshot for {symbol_key}",
                        "INFO",
                    )
                    snapshot = await client.get_position_snapshot(pos.symbol)
                    if snapshot:
                        self._logger.log(
                            f"[{dex_key.upper()}] Snapshot for {symbol_key}: {snapshot}",
                            "INFO",
                        )
                    else:
                        self._logger.log(
                            f"[{dex_key.upper()}] Snapshot for {symbol_key} returned empty payload",
                            "INFO",
                        )
                except Exception as exc:
                    self._logger.log(
                        f"[{dex_key}] Failed to fetch position snapshot for {symbol_key}: {exc}",
                        "WARNING",
                    )
                    continue

                if snapshot:
                    snapshot_lookup[key] = snapshot

        return snapshot_lookup

    def _refresh_position_leg_metrics(
        self,
        position: FundingArbPosition,
        exchange_snapshots: Dict[Tuple[str, str], ExchangePositionSnapshot],
    ) -> None:
        """Merge exchange snapshots into the position metadata."""
        legs_metadata = position.metadata.setdefault("legs", {})
        symbol_key = position.symbol.upper()
        total_unrealized = Decimal("0")
        has_updates = False
        now_ts = datetime.now(timezone.utc)

        legs = [
            (position.long_dex, "long"),
            (position.short_dex, "short"),
        ]

        for dex, default_side in legs:
            if not dex:
                continue

            dex_key = dex.lower()
            leg_meta = legs_metadata.get(dex, {"side": default_side})
            snapshot = exchange_snapshots.get((dex_key, symbol_key))
            if not snapshot:
                continue

            quantity = snapshot.quantity or Decimal("0")
            quantity_abs = quantity.copy_abs()

            if quantity_abs != 0:
                leg_meta["quantity"] = quantity_abs
                leg_meta["side"] = snapshot.side or ("long" if quantity > 0 else "short")
            else:
                leg_meta.setdefault("quantity", Decimal("0"))
                leg_meta.setdefault("side", snapshot.side or default_side)

            if snapshot.entry_price is not None:
                leg_meta["entry_price"] = snapshot.entry_price

            exposure = snapshot.exposure_usd
            if exposure is None and quantity_abs != 0:
                price = snapshot.mark_price or snapshot.entry_price
                if price is not None:
                    exposure = quantity_abs * price
            if exposure is not None:
                leg_meta["exposure_usd"] = exposure.copy_abs()

            if snapshot.mark_price is not None:
                leg_meta["mark_price"] = snapshot.mark_price

            if snapshot.unrealized_pnl is not None:
                leg_meta["unrealized_pnl"] = snapshot.unrealized_pnl
                total_unrealized += snapshot.unrealized_pnl

            if snapshot.realized_pnl is not None:
                leg_meta["realized_pnl"] = snapshot.realized_pnl

            if snapshot.funding_accrued is not None:
                leg_meta["funding_accrued"] = snapshot.funding_accrued

            if snapshot.margin_reserved is not None:
                leg_meta["margin_reserved"] = snapshot.margin_reserved

            if snapshot.leverage is not None:
                leg_meta["leverage"] = snapshot.leverage

            if snapshot.liquidation_price is not None:
                leg_meta["liquidation_price"] = snapshot.liquidation_price

            if snapshot.metadata:
                extra = leg_meta.setdefault("metadata", {})
                extra.update(snapshot.metadata)

            updated_ts = snapshot.timestamp or now_ts
            leg_meta["last_updated"] = updated_ts.isoformat()

            legs_metadata[dex] = leg_meta
            has_updates = True

        if has_updates:
            position.metadata["legs"] = legs_metadata
            position.metadata["exchange_unrealized_pnl"] = total_unrealized
