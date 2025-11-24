"""
Position monitoring utilities for the funding arbitrage strategy.

Responsible for:
 - Refreshing funding divergence via the funding rate repository.
 - Polling exchanges for live leg metrics.
 - Updating the position manager with the latest state.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set, Tuple

from exchange_clients.base_client import BaseExchangeClient
from exchange_clients.base_models import ExchangePositionSnapshot

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
        strategy_config: Any = None,
    ) -> None:
        self._position_manager = position_manager
        self._funding_rate_repo = funding_rate_repo
        self._exchange_clients = exchange_clients
        self._logger = logger
        self._strategy_config = strategy_config
        # Store account name for logging
        self._account_name = getattr(position_manager, 'account_name', None)

    async def monitor(self) -> None:
        """Refresh open positions with latest funding rates and exchange data."""
        # this is getting from a cache, not DB
        positions = await self._position_manager.get_open_positions()

        if not positions:
            account_info = f" for account {self._account_name}" if self._account_name else ""
            self._logger.debug(f"No open positions to monitor{account_info}")
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
                    rate1 = rate2 = None
                    if rate1_data or rate2_data:
                        self._logger.warning(
                            f"Partial rate data for {position.symbol}: long={bool(rate1_data)} short={bool(rate2_data)}"
                        )

                self._refresh_position_leg_metrics(position, exchange_snapshots, rate1, rate2)
                await self._position_manager.update(position)

                self._log_exchange_metrics(position)
            except Exception as exc:  # pragma: no cover - defensive logging
                account_info = f" [Account: {self._account_name}]" if self._account_name else ""
                self._logger.error(
                    f"Error monitoring position {position.id}{account_info}: {exc}"
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
                    # Convert position.opened_at (datetime) to Unix timestamp (float) for exchange APIs
                    # This avoids expensive trades API calls (300 weight) by using our database timestamp
                    position_opened_at_ts = None
                    if pos.opened_at:
                        # Convert datetime to Unix timestamp (seconds)
                        position_opened_at_ts = pos.opened_at.timestamp()
                        # Both Lighter and Aster can use this to optimize funding fee fetching
                    
                    snapshot = await client.get_position_snapshot(
                        pos.symbol,
                        position_opened_at=position_opened_at_ts,
                    )
                except Exception as exc:
                    self._logger.warning(
                        f"[{dex_key}] Failed to fetch position snapshot for {symbol_key}: {exc}"
                    )
                    continue

                if snapshot:
                    snapshot_lookup[key] = snapshot

        return snapshot_lookup

    def _refresh_position_leg_metrics(
        self,
        position: FundingArbPosition,
        exchange_snapshots: Dict[Tuple[str, str], ExchangePositionSnapshot],
        long_rate: Optional[Decimal] = None,
        short_rate: Optional[Decimal] = None,
    ) -> None:
        """Merge exchange snapshots into the position metadata."""
        legs_metadata = position.metadata.setdefault("legs", {})
        symbol_key = position.symbol.upper()
        total_unrealized = Decimal("0")
        has_updates = False
        now_ts = datetime.now(timezone.utc)
        total_funding = Decimal("0")

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
                total_funding += snapshot.funding_accrued

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
            position.metadata["exchange_funding"] = total_funding

            # Track cross-exchange spread for profit-taking opportunities
            long_snapshot = exchange_snapshots.get((position.long_dex.lower(), position.symbol.upper()))
            short_snapshot = exchange_snapshots.get((position.short_dex.lower(), position.symbol.upper()))

            if long_snapshot and short_snapshot:
                long_price = long_snapshot.mark_price
                short_price = short_snapshot.mark_price

                if long_price and short_price and long_price > 0 and short_price > 0:
                    avg_price = (long_price + short_price) / Decimal("2")
                    spread_pct = abs(long_price - short_price) / avg_price

                    position.metadata["cross_exchange_spread_pct"] = float(spread_pct)
                    position.metadata["cross_exchange_prices"] = {
                        "long_mark": float(long_price),
                        "short_mark": float(short_price),
                        "spread_bps": float(spread_pct * Decimal("10000")),  # Basis points
                    }

                    # Log if spread is unusually wide (potential profit opportunity)
                    if spread_pct > Decimal("0.002"):  # > 0.2%
                        logger = getattr(self, "logger", None)
                        if logger:
                            logger.info(
                                f"📊 Wide cross-exchange spread detected for {position.symbol}: {spread_pct*100:.3f}%",
                                extra={
                                    "long_dex": position.long_dex,
                                    "short_dex": position.short_dex,
                                    "long_price": float(long_price),
                                    "short_price": float(short_price),
                                    "spread_pct": float(spread_pct * 100),
                                }
                            )

        rate_map = position.metadata.setdefault("rate_map", {})
        if long_rate is not None:
            rate_map[position.long_dex] = long_rate
        if short_rate is not None:
            rate_map[position.short_dex] = short_rate
        
        # Calculate and store funding APY for each leg
        # This should happen whenever rates are available, even if no snapshot updates occurred
        apy_updated = False
        for dex, leg_meta in legs_metadata.items():
            # Get the funding rate for this DEX from rate_map
            # Try exact match first, then case-insensitive match
            funding_rate = rate_map.get(dex)
            if funding_rate is None:
                # Try case-insensitive lookup
                dex_lower = dex.lower()
                for rate_dex, rate in rate_map.items():
                    if rate_dex.lower() == dex_lower:
                        funding_rate = rate
                        break
            
            if funding_rate is not None:
                # Calculate APY: rate * 3 (fundings per day) * 365 (days) * 100 (percentage)
                funding_apy = float(funding_rate * Decimal("3") * Decimal("365") * Decimal("100"))
                leg_meta["funding_apy"] = funding_apy
                apy_updated = True
            # If rate not available, ensure funding_apy is None (don't overwrite existing value)
            elif "funding_apy" not in leg_meta:
                leg_meta["funding_apy"] = None
        
        # Update legs metadata with APY values if we calculated APY or had other updates
        if has_updates or apy_updated:
            position.metadata["legs"] = legs_metadata

        # Cache snapshots for real-time profit checking (zero-API-call profit checks)
        # Convert exchange_snapshots from {(dex, symbol): snapshot} to {dex: snapshot} format
        if exchange_snapshots:
            snapshot_cache = {}
            for (dex_key, symbol_key), snapshot in exchange_snapshots.items():
                # Match the dex from position (case-insensitive)
                if dex_key.lower() == position.long_dex.lower():
                    snapshot_cache[position.long_dex] = snapshot
                elif dex_key.lower() == position.short_dex.lower():
                    snapshot_cache[position.short_dex] = snapshot

            if snapshot_cache:
                position.metadata["snapshot_cache"] = {
                    "timestamp": now_ts.isoformat(),
                    "snapshots": snapshot_cache,
                }
                self._logger.debug(
                    f"[SNAPSHOT_CACHE] Cached snapshots for {position.symbol} "
                    f"({len(snapshot_cache)} legs, timestamp={now_ts.isoformat()})"
                )

    def _log_exchange_metrics(self, position: FundingArbPosition) -> None:
        """
        Emit an INFO log with the latest per-leg metrics and aggregate exchange figures.
        Useful for verifying delta-neutral hedges after position openings.
        """
        legs_metadata = position.metadata.get("legs")
        if not legs_metadata:
            return

        headers = [
            ("Exchange", 12),
            ("Side", 6),
            ("Qty", 11),
            ("Entry", 12),
            ("Mark", 12),
            ("uPnL", 12),
            ("Funding", 12),
            ("Funding APY", 12),
        ]
        header_line = " ".join(f"{title:<{width}}" for title, width in headers)
        separator = "-" * len(header_line)

        rate_lookup = position.metadata.get("rate_map", {})

        rows = []
        for dex, meta in legs_metadata.items():
            side = meta.get("side", "n/a")
            quantity = self._format_decimal(meta.get("quantity"), precision=4)
            entry_price = self._format_decimal(meta.get("entry_price"), precision=6)
            mark_price = self._format_decimal(meta.get("mark_price"), precision=6)
            unrealized = self._format_decimal(meta.get("unrealized_pnl"), precision=2)
            funding = self._format_decimal(meta.get("funding_accrued"), precision=2)
            rate_display = self._format_rate(rate_lookup.get(dex))

            rows.append(
                f"{dex.upper():<{headers[0][1]}}"
                f"{side:<{headers[1][1]}}"
                f"{quantity:>{headers[2][1]}}"
                f"{entry_price:>{headers[3][1]}}"
                f"{mark_price:>{headers[4][1]}}"
                f"{unrealized:>{headers[5][1]}}"
                f"{funding:>{headers[6][1]}}"
                f"{rate_display:>{headers[7][1]}}"
            )

        # Add account info if available
        account_info = f" [Account: {self._account_name}]" if self._account_name else ""
        
        message_lines = [
            f"Position {position.symbol} snapshot{account_info}",
            self._compose_yield_summary(position),
            self._compose_hold_summary(position),
            self._compose_max_hold_summary(position),
            separator,
            header_line,
            separator,
            *rows,
        ]
        self._logger.info("\n".join(message_lines))

    @staticmethod
    def _format_decimal(value: Optional[Decimal], precision: int = 2) -> str:
        """Format Decimal values consistently for logging."""
        if value is None:
            return "n/a"

        try:
            dec_value = Decimal(str(value)) if not isinstance(value, Decimal) else value
            quant = Decimal("1." + "0" * precision)
            return f"{dec_value.quantize(quant):.{precision}f}"
        except Exception:  # pragma: no cover - fallback for non-decimal types
            return str(value)

    @staticmethod
    def _format_rate(rate: Optional[Decimal], precision: int = 4) -> str:
        if rate is None:
            return "n/a"
        try:
            dec_rate = Decimal(str(rate)) if not isinstance(rate, Decimal) else rate
            annualized = dec_rate * Decimal("3") * Decimal("365") * Decimal("100")
            quant = Decimal("1." + "0" * precision)
            return f"{annualized.quantize(quant):.{precision}f}%"
        except Exception:
            return str(rate)

    @staticmethod
    def _format_percent(value: Optional[Decimal], precision: int = 2) -> str:
        if value is None:
            return "n/a"
        try:
            dec = Decimal(str(value)) if not isinstance(value, Decimal) else value
            quant = Decimal("1." + "0" * precision)
            return f"{(dec * Decimal('100')).quantize(quant):.{precision}f}%"
        except Exception:
            return str(value)

    def _compose_yield_summary(self, position: FundingArbPosition) -> str:
        entry_rate_display = self._format_rate(position.entry_divergence)
        current_rate_display = self._format_rate(position.current_divergence)

        erosion_ratio = position.get_profit_erosion()
        # Convert remaining ratio to erosion percentage for display
        # erosion_ratio = 0.4 means 40% remains, so 60% erosion
        erosion_percentage = (Decimal("1") - erosion_ratio) * Decimal("100") if erosion_ratio <= Decimal("1") else Decimal("0")
        erosion_display = self._format_percent(erosion_percentage / Decimal("100"))

        threshold = None
        min_profit = None
        if getattr(self, "_strategy_config", None):
            risk_cfg = getattr(self._strategy_config, "risk_config", None)
            if risk_cfg:
                threshold = getattr(risk_cfg, "min_erosion_threshold", None)

        # Convert threshold (remaining ratio) to erosion percentage for display
        # threshold = 0.4 means exit when 40% remains, so 60% erosion
        if threshold is not None:
            threshold_erosion_pct = (Decimal("1") - Decimal(str(threshold))) * Decimal("100")
            threshold_display = self._format_percent(threshold_erosion_pct / Decimal("100"))
        else:
            threshold_display = "n/a"

        return (
            "Yield (annualised) | "
            f"entry {entry_rate_display} | "
            f"current {current_rate_display} | "
            f"erosion {erosion_display} (limit {threshold_display})"
        )

    def _compose_hold_summary(self, position: FundingArbPosition) -> str:
        risk_cfg = getattr(self._strategy_config, "risk_config", None) if getattr(self, "_strategy_config", None) else None
        if not risk_cfg:
            return "Min hold: n/a"

        min_hold_hours = getattr(risk_cfg, "min_hold_hours", 0) or 0
        if min_hold_hours <= 0:
            return "Min hold: disabled"

        age_hours = position.get_age_hours()
        remaining = max(0.0, float(min_hold_hours) - age_hours)
        ready_at = position.opened_at + timedelta(hours=min_hold_hours)
        ready_display = ready_at.strftime("%Y-%m-%d %H:%M:%S")

        if remaining <= 0:
            return f"Min hold: satisfied (risk checks active since {ready_display})"

        remaining_minutes = max(0, int(round(remaining * 60)))
        hours_left, minutes_left = divmod(remaining_minutes, 60)
        parts = []
        if hours_left:
            parts.append(f"{hours_left}h")
        if minutes_left or not parts:
            parts.append(f"{minutes_left}m")
        remaining_fmt = " ".join(parts)

        return f"Min hold: ACTIVE ({remaining_fmt} remaining, risk checks resume {ready_display})"

    def _compose_max_hold_summary(self, position: FundingArbPosition) -> str:
        risk_cfg = getattr(self._strategy_config, "risk_config", None) if getattr(self, "_strategy_config", None) else None
        if not risk_cfg:
            return "Max hold: n/a"

        max_age_hours = getattr(risk_cfg, "max_position_age_hours", None)
        if max_age_hours is None or max_age_hours <= 0:
            return "Max hold: disabled"

        age_hours = position.get_age_hours()
        remaining = max(0.0, float(max_age_hours) - age_hours)
        force_close_at = position.opened_at + timedelta(hours=max_age_hours)
        force_close_display = force_close_at.strftime("%Y-%m-%d %H:%M:%S")

        if remaining <= 0:
            return f"Max hold: EXCEEDED (force close was due at {force_close_display})"

        remaining_minutes = max(0, int(round(remaining * 60)))
        hours_left, minutes_left = divmod(remaining_minutes, 60)
        parts = []
        if hours_left:
            parts.append(f"{hours_left}h")
        if minutes_left or not parts:
            parts.append(f"{minutes_left}m")
        remaining_fmt = " ".join(parts)

        return f"Max hold: {remaining_fmt} remaining (force close at {force_close_display}, configured: {max_age_hours}h)"
