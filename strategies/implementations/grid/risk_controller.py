"""
Risk and leverage management helpers for the grid strategy.

Encapsulates leverage preparation, margin usage tracking, stop-loss
enforcement, and order limit validation.
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Callable, Optional, Tuple

from exchange_clients.base_models import ExchangePositionSnapshot

from strategies.execution.core.leverage_validator import LeverageValidator

from .config import GridConfig
from .models import GridState


LogEventFn = Callable[..., None]


class GridRiskController:
    """Collection of risk-related helpers used by ``GridStrategy``."""

    def __init__(
        self,
        config: GridConfig,
        exchange_client,
        grid_state: GridState,
        logger,
        log_event: LogEventFn,
        requested_leverage: Optional[Decimal] = None,
    ) -> None:
        self.config = config
        self.exchange_client = exchange_client
        self.grid_state = grid_state
        self.logger = logger
        self._log_event = log_event

        self._leverage_validator = LeverageValidator()
        self._requested_leverage = requested_leverage
        self._applied_leverage: Optional[Decimal] = None
        self._max_symbol_leverage: Optional[Decimal] = None
        self._fallback_margin_ratio: Optional[Decimal] = None

    # ------------------------------------------------------------------ #
    # Public helpers
    # ------------------------------------------------------------------ #
    async def prepare_leverage_settings(self) -> None:
        """Fetch leverage limits and apply requested leverage if provided."""
        if not self.exchange_client:
            return

        ticker = getattr(self.config, "ticker", None) or getattr(self.exchange_client.config, "ticker", None)
        if not ticker:
            return

        try:
            leverage_info = await self._leverage_validator.get_leverage_info(self.exchange_client, ticker)
            self._max_symbol_leverage = leverage_info.max_leverage

            if leverage_info.max_leverage is not None:
                self.logger.log(
                    f"  - Exchange max leverage for {ticker}: {leverage_info.max_leverage}x",
                    "INFO",
                )

            applied: Optional[Decimal] = None
            if self._requested_leverage is not None:
                applied = self._requested_leverage
                if leverage_info.max_leverage is not None and applied > leverage_info.max_leverage:
                    self.logger.log(
                        f"Requested leverage {applied}x exceeds exchange maximum {leverage_info.max_leverage}x. "
                        "Using the maximum allowed leverage instead.",
                        "WARNING",
                    )
                    applied = leverage_info.max_leverage

                if applied and applied > 0 and hasattr(self.exchange_client, "set_account_leverage"):
                    leverage_to_set = max(int(applied), 1)
                    try:
                        set_success = await self.exchange_client.set_account_leverage(ticker, leverage_to_set)
                        if set_success:
                            self.logger.log(
                                f"Applied leverage {leverage_to_set}x on {self.exchange_client.get_exchange_name().upper()}.",
                                "INFO",
                            )
                            applied = Decimal(leverage_to_set)
                        else:
                            self.logger.log(
                                f"Failed to set leverage to {leverage_to_set}x on "
                                f"{self.exchange_client.get_exchange_name().upper()}.",
                                "WARNING",
                            )
                    except Exception as exc:
                        self.logger.log(
                            f"Error setting leverage on {self.exchange_client.get_exchange_name().upper()}: {exc}",
                            "WARNING",
                        )

            self._applied_leverage = applied
            if self._applied_leverage and self._applied_leverage > 0:
                self._fallback_margin_ratio = Decimal("1") / self._applied_leverage
            else:
                self._fallback_margin_ratio = None

        except Exception as exc:
            self.logger.log(
                f"Grid: Failed to prepare leverage settings for {ticker}: {exc}",
                "WARNING",
            )

    async def refresh_risk_snapshot(
        self,
        reference_price: Decimal,
    ) -> Tuple[Decimal, Optional[ExchangePositionSnapshot]]:
        """
        Refresh cached risk metrics (position, margin usage, margin ratio).
        """
        try:
            current_position = await self.exchange_client.get_account_positions()
        except Exception as exc:
            self.logger.log(f"Grid: Failed to fetch account positions: {exc}", "ERROR")
            current_position = Decimal("0")

        snapshot = await self._fetch_position_snapshot()
        margin_used, margin_ratio = self._calculate_margin_usage(snapshot, current_position, reference_price)

        self.grid_state.last_known_position = current_position
        self.grid_state.last_known_margin = margin_used
        self.grid_state.margin_ratio = margin_ratio

        if current_position == 0:
            self.grid_state.last_stop_loss_trigger = 0.0

        return current_position, snapshot

    async def enforce_stop_loss(
        self,
        snapshot: Optional[ExchangePositionSnapshot],
        current_price: Decimal,
        current_position: Decimal,
        close_position_fn,
    ) -> bool:
        """
        Trigger a stop-loss exit if price has moved against the open position beyond the configured threshold.
        """
        if not self.config.stop_loss_enabled:
            return False

        if current_position == 0:
            # Reset timer when flat
            self.grid_state.last_stop_loss_trigger = 0.0
            return False

        entry_price = getattr(snapshot, "entry_price", None) if snapshot else None
        if entry_price is None or entry_price <= 0:
            return False

        try:
            stop_fraction = Decimal(str(self.config.stop_loss_percentage)) / Decimal("100")
        except Exception:
            stop_fraction = Decimal("0")

        if stop_fraction <= 0:
            return False

        now = time.time()
        if now - self.grid_state.last_stop_loss_trigger < 3:
            # Avoid spamming market orders if the previous stop-loss just fired
            return True

        position_side = "long" if current_position > 0 else "short"
        triggered = False
        if position_side == "long":
            stop_price = entry_price * (Decimal("1") - stop_fraction)
            if current_price <= stop_price:
                message = (
                    f"Price {current_price} breached long stop {stop_price} "
                    f"(entry {entry_price}, threshold {self.config.stop_loss_percentage}%)"
                )
                triggered = await close_position_fn(current_position, message)
        else:
            stop_price = entry_price * (Decimal("1") + stop_fraction)
            if current_price >= stop_price:
                message = (
                    f"Price {current_price} breached short stop {stop_price} "
                    f"(entry {entry_price}, threshold {self.config.stop_loss_percentage}%)"
                )
                triggered = await close_position_fn(current_position, message)

        if triggered:
            self.grid_state.last_stop_loss_trigger = now
            self.grid_state.last_open_order_time = now
            return True

        return False

    async def check_order_limits(
        self,
        reference_price: Decimal,
        order_quantity: Decimal,
    ) -> Tuple[bool, str]:
        """
        Ensure the next order stays within configured margin and position limits.
        """
        current_position = self.grid_state.last_known_position
        if current_position is None:
            try:
                current_position = await self.exchange_client.get_account_positions()
            except Exception:
                current_position = Decimal("0")
            self.grid_state.last_known_position = current_position

        current_margin = self.grid_state.last_known_margin or Decimal("0")
        margin_ratio = self.grid_state.margin_ratio
        notional = order_quantity.copy_abs() * reference_price
        required_margin = notional
        if (margin_ratio is None or margin_ratio <= 0) and self._fallback_margin_ratio:
            margin_ratio = self._fallback_margin_ratio

        if margin_ratio and margin_ratio > 0:
            required_margin *= margin_ratio

        projected_margin = current_margin + required_margin
        if projected_margin > self.config.max_margin_usd:
            projected_val = float(projected_margin)
            limit_val = float(self.config.max_margin_usd)
            message = (
                f"Grid: Margin cap reached (projected ${projected_val:.2f} > "
                f"limit ${limit_val:.2f}). Skipping new order."
            )
            self._log_event(
                "margin_cap_hit",
                message,
                level="WARNING",
                projected_margin=projected_margin,
                margin_limit=self.config.max_margin_usd,
                required_margin=required_margin,
                order_notional_usd=notional,
            )
            return False, message

        return True, "OK"

    # ------------------------------------------------------------------ #
    # Private helpers
    # ------------------------------------------------------------------ #
    async def _fetch_position_snapshot(self) -> Optional[ExchangePositionSnapshot]:
        """Fetch the latest position snapshot from the exchange, trying multiple symbol hints."""
        symbols = [
            getattr(self.config, "ticker", None),
            getattr(self.exchange_client.config, "contract_id", None),
        ]

        for symbol in symbols:
            if not symbol:
                continue
            try:
                snapshot = await self.exchange_client.get_position_snapshot(symbol)
                if snapshot:
                    return snapshot
            except TypeError:
                # Some clients may not support the symbol format (skip to next hint)
                continue
            except Exception as exc:
                self.logger.log(
                    f"Grid: Failed to fetch position snapshot for {symbol}: {exc}",
                    "DEBUG",
                )
        return None

    def _calculate_margin_usage(
        self,
        snapshot: Optional[ExchangePositionSnapshot],
        current_position: Decimal,
        reference_price: Decimal,
    ) -> Tuple[Decimal, Optional[Decimal]]:
        """
        Estimate current margin usage and margin ratio using exchange snapshot data when available.
        """
        abs_position = current_position.copy_abs() if isinstance(current_position, Decimal) else Decimal(
            str(current_position)
        ).copy_abs()
        margin_used = Decimal("0")
        margin_ratio: Optional[Decimal] = None

        if snapshot:
            if getattr(snapshot, "margin_reserved", None) is not None:
                margin_used = snapshot.margin_reserved.copy_abs()
            elif getattr(snapshot, "exposure_usd", None) is not None:
                margin_used = snapshot.exposure_usd.copy_abs()
            else:
                margin_used = abs_position * reference_price

            exposure = getattr(snapshot, "exposure_usd", None)
            reserved = getattr(snapshot, "margin_reserved", None)
            if exposure and reserved:
                exposure_abs = exposure.copy_abs()
                if exposure_abs > 0:
                    margin_ratio = reserved.copy_abs() / exposure_abs
        else:
            margin_used = abs_position * reference_price

        return margin_used, margin_ratio
