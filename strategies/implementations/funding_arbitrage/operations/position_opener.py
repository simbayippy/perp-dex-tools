"""Helpers for opening funding arbitrage positions."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from typing import TYPE_CHECKING, Any, Dict, List, Optional
from uuid import uuid4

from exchange_clients import BaseExchangeClient
from strategies.execution.patterns.atomic_multi_order import (
    AtomicExecutionResult,
    OrderSpec,
)
from helpers.unified_logger import log_stage

from ..models import FundingArbPosition

if TYPE_CHECKING:
    from ..strategy import FundingArbitrageStrategy

@dataclass
class TradeExecutionResult:
    """Container for the result of executing the opening hedge."""

    position: FundingArbPosition
    timestamp_iso: str
    result: AtomicExecutionResult
    long_fill: Dict[str, Any]
    short_fill: Dict[str, Any]
    entry_fees: Decimal
    total_cost: Decimal

@dataclass
class PersistenceOutcome:
    """Describes how the position was persisted (merged or created)."""

    type: str  # "merged" | "created"
    position: FundingArbPosition
    updated_size: Optional[Decimal] = None
    additional_size: Optional[Decimal] = None

@dataclass
class OrderPlan:
    """Pre-computed execution plan for the atomic opener."""

    orders: List[OrderSpec]
    quantity: Decimal
    long_notional: Decimal
    short_notional: Decimal
    long_price: Decimal
    short_price: Decimal

class PositionOpener:
    """Encapsulates the complex flow required to open a funding arb position."""

    def __init__(self, strategy: "FundingArbitrageStrategy") -> None:
        self._strategy = strategy

    async def open(self, opportunity) -> Optional[FundingArbPosition]:
        """
        Attempt to open a position for the given opportunity.

        Returns:
            FundingArbPosition if the execution succeeds, otherwise None.
        """
        try:
            execution = await self._execute_trade(opportunity)
            if execution is None:
                return None

            persistence = await self._persist_position(
                position=execution.position,
                timestamp_iso=execution.timestamp_iso,
                total_cost=execution.total_cost,
                entry_fees=execution.entry_fees,
                total_slippage=execution.result.total_slippage_usd,
            )

            if persistence is None:
                return None

            self._log_open_success(
                symbol=execution.position.symbol,
                long_fill=execution.long_fill,
                short_fill=execution.short_fill,
                entry_fees=execution.entry_fees,
                total_slippage=execution.result.total_slippage_usd,
                size_usd=execution.position.size_usd,
                merged=persistence.type == "merged",
                updated_size=getattr(persistence, "updated_size", None),
                additional_size=getattr(persistence, "additional_size", None),
                imbalance_usd=execution.result.residual_imbalance_usd,
            )

            return persistence.position

        except Exception as exc:  # pragma: no cover - defensive logging
            strategy = self._strategy
            strategy.logger.log(
                f"❌ {opportunity.symbol}: Unexpected error - {exc}",
                "ERROR",
            )
            strategy.failed_symbols.add(opportunity.symbol)
            return None

    async def _execute_trade(self, opportunity) -> Optional["TradeExecutionResult"]:
        """Run validation, leverage normalization, and atomic execution."""
        strategy = self._strategy
        symbol = opportunity.symbol
        long_dex = opportunity.long_dex
        short_dex = opportunity.short_dex

        if long_dex not in strategy.exchange_clients or short_dex not in strategy.exchange_clients:
            strategy.logger.log(
                f"⛔ [SKIP] {symbol}: Missing exchange clients for {long_dex}/{short_dex}",
                "WARNING",
            )
            strategy.failed_symbols.add(symbol)
            return None

        long_client = strategy.exchange_clients[long_dex]
        short_client = strategy.exchange_clients[short_dex]

        log_stage(strategy.logger, f"{symbol} • Opportunity Validation", icon="📋", stage_id="1")
        strategy.logger.log(
            f"Ensuring {symbol} is tradeable on both {long_dex} and {short_dex}",
            "INFO",
        )

        long_init_ok = await self._ensure_contract_attributes(long_client, symbol)
        short_init_ok = await self._ensure_contract_attributes(short_client, symbol)

        if not long_init_ok or not short_init_ok:
            if not long_init_ok:
                strategy.logger.log(
                    f"⛔ [SKIP] Cannot trade {symbol}: Not supported on {long_dex.upper()} (long side)",
                    "WARNING",
                )
            if not short_init_ok:
                strategy.logger.log(
                    f"⛔ [SKIP] Cannot trade {symbol}: Not supported on {short_dex.upper()} (short side)",
                    "WARNING",
                )
            strategy.failed_symbols.add(symbol)
            return None

        strategy.logger.log(
            f"✅ {symbol} available on both {long_dex.upper()} and {short_dex.upper()}",
            "INFO",
        )

        adjusted_size = await self._validate_leverage(
            symbol=symbol,
            long_client=long_client,
            short_client=short_client,
            requested_size=strategy.config.default_position_size_usd,
        )

        if adjusted_size is None:
            strategy.failed_symbols.add(symbol)
            return None

        strategy.logger.log(
            f"🎯 Execution plan for {symbol}: "
            f"Long {long_dex.upper()} (${adjusted_size:.2f}) | "
            f"Short {short_dex.upper()} (${adjusted_size:.2f}) | "
            f"Divergence {opportunity.divergence*100:.3f}%",
            "INFO",
        )

        log_stage(strategy.logger, "Atomic Multi-Order Execution", icon="🧨", stage_id="3")

        limit_offset_pct = getattr(strategy.config, "limit_order_offset_pct", None)
        if limit_offset_pct is not None and not isinstance(limit_offset_pct, Decimal):
            limit_offset_pct = Decimal(str(limit_offset_pct))

        plan = await self._prepare_order_plan(
            symbol=symbol,
            adjusted_size=adjusted_size,
            long_client=long_client,
            short_client=short_client,
            limit_offset_pct=limit_offset_pct,
        )

        if plan is None:
            strategy.failed_symbols.add(symbol)
            return None

        strategy.logger.log(
            f"📏 Planned execution for {symbol}: qty={plan.quantity} "
            f"(long≈${plan.long_notional:.2f}, short≈${plan.short_notional:.2f})",
            "DEBUG",
        )

        result: AtomicExecutionResult = await strategy.atomic_executor.execute_atomically(
            orders=plan.orders,
            rollback_on_partial=True,
            pre_flight_check=True,
            skip_preflight_leverage=True,
            stage_prefix="3",
            retry_policy=strategy.atomic_retry_policy,
        )

        if not result.all_filled:
            strategy.logger.log(
                f"❌ {symbol}: Atomic execution failed - {result.error_message}",
                "ERROR",
            )

            if result.rollback_performed:
                strategy.logger.log(
                    f"🔄 Emergency rollback performed, cost: ${result.rollback_cost_usd:.2f}",
                    "WARNING",
                )

            strategy.failed_symbols.add(symbol)
            return None

        long_fill = result.filled_orders[0]
        short_fill = result.filled_orders[1]

        long_exposure = self._compute_leg_exposure(long_fill)
        short_exposure = self._compute_leg_exposure(short_fill)
        exposures = [exposure for exposure in (long_exposure, short_exposure) if exposure > Decimal("0")]
        effective_size = min(exposures) if exposures else adjusted_size

        imbalance_usd = result.residual_imbalance_usd or abs(long_exposure - short_exposure)
        if imbalance_usd > Decimal("0.01"):
            strategy.logger.log(
                f"⚠️ {symbol}: residual imbalance ${imbalance_usd:.5f} after execution",
                "WARNING",
            )

        entry_fees = strategy.fee_calculator.calculate_total_cost(
            long_dex,
            short_dex,
            effective_size,
            is_maker=True,
        )
        total_cost = entry_fees + result.total_slippage_usd

        position, timestamp_iso = self._build_new_position(
            symbol=symbol,
            long_dex=long_dex,
            short_dex=short_dex,
            size_usd=effective_size,
            opportunity=opportunity,
            entry_fees=entry_fees,
            total_cost=total_cost,
            long_fill=long_fill,
            short_fill=short_fill,
            total_slippage=result.total_slippage_usd,
            long_exposure=long_exposure,
            short_exposure=short_exposure,
            imbalance_usd=imbalance_usd,
            planned_quantity=plan.quantity,
        )

        return TradeExecutionResult(
            position=position,
            timestamp_iso=timestamp_iso,
            result=result,
            long_fill=long_fill,
            short_fill=short_fill,
            entry_fees=entry_fees,
            total_cost=total_cost,
        )

    async def _persist_position(
        self,
        *,
        position: FundingArbPosition,
        timestamp_iso: str,
        total_cost: Decimal,
        entry_fees: Decimal,
        total_slippage: Decimal,
    ) -> Optional["PersistenceOutcome"]:
        """
        Persist the opened position. Returns outcome describing whether
        we merged or created the record.
        """
        strategy = self._strategy
        position_manager = strategy.position_manager
        existing_position = await position_manager.find_open_position(
            position.symbol,
            position.long_dex,
            position.short_dex,
        )

        if existing_position:
            merge_result = self._merge_existing_position(
                existing_position=existing_position,
                new_position=position,
                total_cost=total_cost,
                entry_fees=entry_fees,
                total_slippage=total_slippage,
                timestamp_iso=timestamp_iso,
            )

            if merge_result is None:
                strategy.logger.log(
                    f"⚠️ Skipping position update for {position.symbol}: resulting size would be non-positive",
                    "WARNING",
                )
                return None

            merged_position, updated_size, additional_size = merge_result
            await position_manager.update(merged_position)
            strategy.position_opened_this_session = True

            return PersistenceOutcome(
                type="merged",
                position=merged_position,
                updated_size=updated_size,
                additional_size=additional_size,
            )

        await position_manager.create(position)
        strategy.position_opened_this_session = True

        return PersistenceOutcome(type="created", position=position)

    async def _validate_leverage(
        self,
        *,
        symbol: str,
        long_client: Any,
        short_client: Any,
        requested_size: Decimal,
    ) -> Optional[Decimal]:
        """Normalize leverage and confirm balances."""
        strategy = self._strategy
        log_stage(strategy.logger, "Leverage Validation & Normalization", icon="🔍", stage_id="2")

        from strategies.execution.core.leverage_validator import LeverageValidator

        leverage_validator = LeverageValidator()

        try:
            leverage_prep = await leverage_validator.prepare_leverage(
                exchange_clients=[long_client, short_client],
                symbol=symbol,
                requested_size_usd=requested_size,
                min_position_usd=Decimal("5"),
                check_balance=True,
                normalize_leverage=True,
            )
        except Exception as exc:  # pragma: no cover - defensive
            strategy.logger.log(
                f"⛔ [SKIP] {symbol}: Leverage preparation failed - {exc}",
                "WARNING",
            )
            return None

        adjusted_size = leverage_prep.adjusted_size_usd

        if leverage_prep.below_minimum:
            strategy.logger.log(
                f"⛔ {symbol}: Position size too small after leverage adjustment (${adjusted_size:.2f})",
                "WARNING",
            )
            return None

        return adjusted_size

    async def _prepare_order_plan(
        self,
        *,
        symbol: str,
        adjusted_size: Decimal,
        long_client: BaseExchangeClient,
        short_client: BaseExchangeClient,
        limit_offset_pct: Optional[Decimal],
    ) -> Optional[OrderPlan]:
        """Derive an execution plan that respects venue step sizes."""
        strategy = self._strategy
        price_provider = getattr(strategy, "price_provider", None)
        if price_provider is None:
            strategy.logger.log(
                "❌ Price provider not available; cannot prepare execution plan",
                "ERROR",
            )
            return None

        await self._prepare_websocket_feeds(long_client, symbol)
        await self._prepare_websocket_feeds(short_client, symbol)

        try:
            long_bid, long_ask = await price_provider.get_bbo_prices(long_client, symbol)
            short_bid, short_ask = await price_provider.get_bbo_prices(short_client, symbol)
        except Exception as exc:
            strategy.logger.log(
                f"❌ Failed to fetch BBO for {symbol}: {exc}",
                "ERROR",
            )
            return None

        long_price = Decimal(str(long_ask))
        short_price = Decimal(str(short_bid))

        if long_price <= 0 or short_price <= 0:
            strategy.logger.log(
                f"❌ Invalid BBO for {symbol}: long_price={long_price}, short_price={short_price}",
                "ERROR",
            )
            return None

        raw_long_qty = adjusted_size / long_price
        raw_short_qty = adjusted_size / short_price

        rounded_long_qty = self._round_quantity(long_client, raw_long_qty)
        rounded_short_qty = self._round_quantity(short_client, raw_short_qty)

        common_qty = min(rounded_long_qty, rounded_short_qty)
        if common_qty <= Decimal("0"):
            strategy.logger.log(
                f"⛔ [SKIP] {symbol}: Unable to derive non-zero quantity after rounding "
                f"(long={rounded_long_qty}, short={rounded_short_qty})",
                "WARNING",
            )
            return None

        long_notional = common_qty * long_price
        short_notional = common_qty * short_price

        orders = [
            OrderSpec(
                exchange_client=long_client,
                symbol=symbol,
                side="buy",
                size_usd=long_notional,
                quantity=common_qty,
                execution_mode="limit_only",
                timeout_seconds=30.0,
                limit_price_offset_pct=limit_offset_pct,
            ),
            OrderSpec(
                exchange_client=short_client,
                symbol=symbol,
                side="sell",
                size_usd=short_notional,
                quantity=common_qty,
                execution_mode="limit_only",
                timeout_seconds=30.0,
                limit_price_offset_pct=limit_offset_pct,
            ),
        ]

        return OrderPlan(
            orders=orders,
            quantity=common_qty,
            long_notional=long_notional,
            short_notional=short_notional,
            long_price=long_price,
            short_price=short_price,
        )

    def _round_quantity(self, client: BaseExchangeClient, quantity: Decimal) -> Decimal:
        """Round quantity to the exchange's supported precision."""
        if quantity <= Decimal("0"):
            return Decimal("0")

        rounded = client.round_to_step(quantity)

        if rounded <= Decimal("0"):
            return Decimal("0")

        # Fallback to config-driven rounding when the client leaves quantity unchanged.
        if rounded == quantity:
            step_size = getattr(getattr(client, "config", None), "step_size", None)
            if step_size:
                try:
                    step = Decimal(str(step_size))
                    if step > 0:
                        rounded = (quantity / step).to_integral_value(rounding=ROUND_DOWN) * step
                except (InvalidOperation, TypeError):
                    rounded = quantity
            if rounded == quantity:
                multiplier = getattr(client, "base_amount_multiplier", None)
                if multiplier:
                    try:
                        step = Decimal("1") / Decimal(multiplier)
                        rounded = quantity.quantize(step, rounding=ROUND_DOWN)
                    except (InvalidOperation, TypeError):
                        pass

        min_qty = getattr(getattr(client, "config", None), "min_quantity", None)
        if min_qty is not None:
            try:
                min_qty_dec = Decimal(str(min_qty))
                if rounded < min_qty_dec:
                    return Decimal("0")
            except (InvalidOperation, TypeError):
                pass

        return rounded if rounded > Decimal("0") else Decimal("0")

    @staticmethod
    def _compute_leg_exposure(fill: Dict[str, Any]) -> Decimal:
        """Compute USD exposure for a filled leg."""
        quantity = fill.get("filled_quantity")
        price = fill.get("fill_price")

        if quantity is None or price is None:
            return Decimal("0")

        try:
            qty_dec = Decimal(str(quantity))
            price_dec = Decimal(str(price))
            exposure = qty_dec * price_dec
            return exposure.copy_abs()
        except (InvalidOperation, TypeError):
            return Decimal("0")

    async def _prepare_websocket_feeds(self, exchange_client: BaseExchangeClient, symbol: str) -> None:
        """Ensure exchange WebSocket streams are aligned with the symbol we intend to trade."""
        strategy = self._strategy

        try:
            await exchange_client.ensure_market_feed(symbol)

            ws_manager = exchange_client.ws_manager
            if ws_manager:
                await self._await_ws_snapshot(ws_manager)
        except Exception as exc:  # pragma: no cover - defensive logging
            strategy.logger.log(
                f"⚠️ [{exchange_client.get_exchange_name().upper()}] WebSocket prep error: {exc}",
                "DEBUG",
            )

    async def _await_ws_snapshot(self, ws_manager: Any, timeout: float = 1.0) -> None:
        """Wait briefly for websocket feeds to populate best bid/ask data."""
        if not getattr(ws_manager, "running", False):
            return

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout

        while loop.time() < deadline:
            snapshot_ready = False

            if hasattr(ws_manager, "snapshot_loaded"):
                snapshot_ready = bool(ws_manager.snapshot_loaded)

            if hasattr(ws_manager, "best_bid"):
                snapshot_ready = snapshot_ready or ws_manager.best_bid is not None

            if snapshot_ready:
                return

            await asyncio.sleep(0.05)

    def _build_new_position(
        self,
        *,
        symbol: str,
        long_dex: str,
        short_dex: str,
        size_usd: Decimal,
        opportunity,
        entry_fees: Decimal,
        total_cost: Decimal,
        long_fill: dict,
        short_fill: dict,
        total_slippage: Decimal,
        long_exposure: Decimal,
        short_exposure: Decimal,
        imbalance_usd: Decimal,
        planned_quantity: Decimal,
    ) -> tuple[FundingArbPosition, str]:
        """Instantiate a FundingArbPosition populated with initial metadata."""
        partial_fee = entry_fees / Decimal("2") if entry_fees else Decimal("0")
        timestamp_iso = datetime.now(timezone.utc).isoformat()

        position = FundingArbPosition(
            id=uuid4(),
            symbol=symbol,
            long_dex=long_dex,
            short_dex=short_dex,
            size_usd=size_usd,
            entry_long_rate=opportunity.long_rate,
            entry_short_rate=opportunity.short_rate,
            entry_divergence=opportunity.divergence,
            opened_at=datetime.now(),
            total_fees_paid=total_cost,
        )

        position.metadata.update(
            {
                "legs": {
                    long_dex: {
                        "side": "long",
                        "entry_price": long_fill.get("fill_price"),
                        "quantity": long_fill.get("filled_quantity"),
                        "fees_paid": partial_fee,
                        "slippage_usd": long_fill.get("slippage_usd"),
                        "execution_mode": long_fill.get("execution_mode_used"),
                        "exposure_usd": long_exposure,
                        "last_updated": timestamp_iso,
                    },
                    short_dex: {
                        "side": "short",
                        "entry_price": short_fill.get("fill_price"),
                        "quantity": short_fill.get("filled_quantity"),
                        "fees_paid": partial_fee,
                        "slippage_usd": short_fill.get("slippage_usd"),
                        "execution_mode": short_fill.get("execution_mode_used"),
                        "exposure_usd": short_exposure,
                        "last_updated": timestamp_iso,
                    },
                },
                "total_slippage_usd": total_slippage,
                "planned_quantity": planned_quantity,
                "residual_imbalance_usd": imbalance_usd,
            }
        )

        return position, timestamp_iso

    def _log_open_success(
        self,
        *,
        symbol: str,
        long_fill: dict,
        short_fill: dict,
        entry_fees: Decimal,
        total_slippage: Decimal,
        size_usd: Decimal,
        merged: bool,
        updated_size: Optional[Decimal],
        additional_size: Optional[Decimal],
        imbalance_usd: Decimal,
    ) -> None:
        """Emit final log entry summarizing the persistence outcome."""
        logger = self._strategy.logger

        if merged and updated_size is not None and additional_size is not None:
            logger.log(
                f"🔁 Position increased {symbol}: "
                f"New size ${updated_size:.2f} (added ${additional_size:.2f}), "
                f"Long @ ${long_fill['fill_price']}, "
                f"Short @ ${short_fill['fill_price']}, "
                f"Fees Δ ${entry_fees:.2f}, Slippage Δ ${total_slippage:.2f}, "
                f"Imbalance ${imbalance_usd:.5f}",
                "INFO",
            )
        else:
            logger.log(
                f"✅ Position opened {symbol}: "
                f"Long @ ${long_fill['fill_price']}, "
                f"Short @ ${short_fill['fill_price']}, "
                f"Size ${size_usd:.2f}, "
                f"Slippage: ${total_slippage:.2f}, "
                f"Fees: ${entry_fees:.2f}, "
                f"Imbalance ${imbalance_usd:.5f}",
                "INFO",
            )

    def _merge_existing_position(
        self,
        *,
        existing_position: FundingArbPosition,
        new_position: FundingArbPosition,
        total_cost: Decimal,
        entry_fees: Decimal,
        total_slippage: Decimal,
        timestamp_iso: str,
    ) -> Optional[tuple[FundingArbPosition, Decimal, Decimal]]:
        """
        Merge a new fill into an existing logical position.

        Returns:
            Tuple of (updated_position, updated_size, additional_size) or None if merge skipped.
        """
        existing_size = existing_position.size_usd or Decimal("0")
        additional_size = new_position.size_usd or Decimal("0")
        updated_size = existing_size + additional_size

        if updated_size <= 0:
            return None

        existing_long_rate = existing_position.entry_long_rate or Decimal("0")
        existing_short_rate = existing_position.entry_short_rate or Decimal("0")

        weighted_long = (existing_long_rate * existing_size) + (
            new_position.entry_long_rate * additional_size
        )
        weighted_short = (existing_short_rate * existing_size) + (
            new_position.entry_short_rate * additional_size
        )

        existing_position.size_usd = updated_size
        existing_position.entry_long_rate = weighted_long / updated_size
        existing_position.entry_short_rate = weighted_short / updated_size
        existing_position.entry_divergence = (
            existing_position.entry_short_rate - existing_position.entry_long_rate
        )
        existing_position.total_fees_paid = self._add_decimal(
            existing_position.total_fees_paid,
            total_cost,
        ) or Decimal("0")

        existing_metadata = existing_position.metadata or {}
        new_metadata = new_position.metadata or {}

        existing_legs = existing_metadata.setdefault("legs", {})
        for dex, leg_meta in new_metadata.get("legs", {}).items():
            current_leg = existing_legs.get(dex, {}).copy()
            for key, value in leg_meta.items():
                if key in {"quantity", "fees_paid", "slippage_usd", "exposure_usd"}:
                    current_leg[key] = self._add_decimal(current_leg.get(key), value)
                else:
                    current_leg[key] = value
            existing_legs[dex] = current_leg
        existing_metadata["legs"] = existing_legs
        existing_metadata["total_slippage_usd"] = self._add_decimal(
            existing_metadata.get("total_slippage_usd"),
            new_metadata.get("total_slippage_usd"),
        )

        new_legs = new_metadata.get("legs", {})
        long_leg_meta = new_legs.get(existing_position.long_dex, {})
        short_leg_meta = new_legs.get(existing_position.short_dex, {})

        fills = existing_metadata.setdefault("fills", [])
        fills.append(
            {
                "id": str(uuid4()),
                "timestamp": timestamp_iso,
                "size_usd": additional_size,
                "long_fill_price": long_leg_meta.get("entry_price"),
                "short_fill_price": short_leg_meta.get("entry_price"),
                "slippage_usd": total_slippage,
                "fees_usd": entry_fees,
            }
        )
        existing_metadata["last_update"] = timestamp_iso
        existing_position.metadata = existing_metadata

        return existing_position, updated_size, additional_size

    @staticmethod
    def _to_decimal(value: Any) -> Optional[Decimal]:
        """Best-effort conversion to Decimal."""
        if isinstance(value, Decimal):
            return value
        if value is None:
            return None
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            return None

    @classmethod
    def _add_decimal(cls, base: Any, increment: Any) -> Optional[Decimal]:
        """Add two numeric values after Decimal coercion."""
        base_dec = cls._to_decimal(base)
        inc_dec = cls._to_decimal(increment)
        if base_dec is None and inc_dec is None:
            return None
        if base_dec is None:
            return inc_dec
        if inc_dec is None:
            return base_dec
        return base_dec + inc_dec

    async def _ensure_contract_attributes(self, exchange_client: BaseExchangeClient, symbol: str) -> bool:
        """Ensure the given exchange client is prepared to trade the symbol."""
        strategy = self._strategy
        try:
            exchange_name = exchange_client.get_exchange_name()

            if not hasattr(exchange_client.config, "contract_id") or exchange_client.config.ticker == "ALL":
                strategy.logger.log(
                    f"🔧 [{exchange_name.upper()}] Initializing contract attributes for {symbol}",
                    "INFO",
                )

                original_ticker = exchange_client.config.ticker
                exchange_client.config.ticker = symbol

                try:
                    contract_id, tick_size = await exchange_client.get_contract_attributes()
                    if not contract_id:
                        strategy.logger.log(
                            f"❌ [{exchange_name.upper()}] Symbol {symbol} initialization returned empty contract_id",
                            "WARNING",
                        )
                        return False

                    strategy.logger.log(
                        f"✅ [{exchange_name.upper()}] {symbol} initialized → contract_id={contract_id}, tick_size={tick_size}",
                        "INFO",
                    )

                except ValueError as exc:
                    error_msg = str(exc).lower()
                    if "not found" in error_msg or "not supported" in error_msg:
                        strategy.logger.log(
                            f"⚠️  [{exchange_name.upper()}] Symbol {symbol} is NOT TRADEABLE on {exchange_name}",
                            "WARNING",
                        )
                        return False
                    raise
                finally:
                    exchange_client.config.ticker = original_ticker

            return True

        except Exception as exc:
            strategy.logger.log(
                f"❌ [{exchange_client.get_exchange_name().upper()}] Failed to ensure contract attributes for {symbol}: {exc}",
                "ERROR",
            )
            return False
