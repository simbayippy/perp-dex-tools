"""Helpers for opening funding arbitrage positions."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Optional
from uuid import uuid4

from strategies.execution.patterns.atomic_multi_order import (
    AtomicExecutionResult,
    OrderSpec,
)
from helpers.unified_logger import log_stage

from ..models import FundingArbPosition

if TYPE_CHECKING:
    from ..strategy import FundingArbitrageStrategy


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

        size_usd = strategy.config.default_position_size_usd

        try:
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

            log_stage(strategy.logger, "Leverage Validation & Normalization", icon="🔍", stage_id="2")
            from strategies.execution.core.leverage_validator import LeverageValidator

            leverage_validator = LeverageValidator()

            try:
                leverage_prep = await leverage_validator.prepare_leverage(
                    exchange_clients=[long_client, short_client],
                    symbol=symbol,
                    requested_size_usd=size_usd,
                    min_position_usd=Decimal("5"),
                    check_balance=True,
                    normalize_leverage=True,
                )
            except Exception as exc:  # pragma: no cover - defensive
                strategy.logger.log(
                    f"⛔ [SKIP] {symbol}: Leverage preparation failed - {exc}",
                    "WARNING",
                )
                strategy.failed_symbols.add(symbol)
                return None

            size_usd = leverage_prep.adjusted_size_usd

            if leverage_prep.below_minimum:
                strategy.logger.log(
                    f"⛔ {symbol}: Position size too small after leverage adjustment (${size_usd:.2f})",
                    "WARNING",
                )
                strategy.failed_symbols.add(symbol)
                return None

            strategy.logger.log(
                f"🎯 Execution plan for {symbol}: "
                f"Long {long_dex.upper()} (${size_usd:.2f}) | "
                f"Short {short_dex.upper()} (${size_usd:.2f}) | "
                f"Divergence {opportunity.divergence*100:.3f}%",
                "INFO",
            )

            log_stage(strategy.logger, "Atomic Multi-Order Execution", icon="🧨", stage_id="3")

            result: AtomicExecutionResult = await strategy.atomic_executor.execute_atomically(
                orders=[
                    OrderSpec(
                        exchange_client=long_client,
                        symbol=symbol,
                        side="buy",
                        size_usd=size_usd,
                        execution_mode="limit_with_fallback",
                        timeout_seconds=30.0,
                    ),
                    OrderSpec(
                        exchange_client=short_client,
                        symbol=symbol,
                        side="sell",
                        size_usd=size_usd,
                        execution_mode="limit_with_fallback",
                        timeout_seconds=30.0,
                    ),
                ],
                rollback_on_partial=True,
                pre_flight_check=True,
                skip_preflight_leverage=True,
                stage_prefix="3",
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

            entry_fees = strategy.fee_calculator.calculate_total_cost(
                long_dex, short_dex, size_usd, is_maker=True
            )
            total_cost = entry_fees + result.total_slippage_usd

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

            partial_fee = entry_fees / Decimal("2") if entry_fees else Decimal("0")
            timestamp_iso = datetime.now(timezone.utc).isoformat()
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
                            "exposure_usd": size_usd,
                            "last_updated": timestamp_iso,
                        },
                        short_dex: {
                            "side": "short",
                            "entry_price": short_fill.get("fill_price"),
                            "quantity": short_fill.get("filled_quantity"),
                            "fees_paid": partial_fee,
                            "slippage_usd": short_fill.get("slippage_usd"),
                            "execution_mode": short_fill.get("execution_mode_used"),
                            "exposure_usd": size_usd,
                            "last_updated": timestamp_iso,
                        },
                    },
                    "total_slippage_usd": result.total_slippage_usd,
                }
            )

            position_manager = strategy.position_manager
            strategy.logger.log(f"Persisting position {position.id} to database", "INFO")
            try:
                # Persist to backing store when available; fall back to in-memory cache otherwise.
                if getattr(position_manager, "_check_database_available", lambda: False)():
                    await position_manager.create_position(position)
                else:
                    await position_manager.add_position(position)
                    strategy.logger.log(f"FAILED TO PERSIST POSITION {position.id} TO DATABASE", "INFO")
            except Exception as exc:  # pragma: no cover - defensive fallback
                strategy.logger.log(
                    f"⚠️ Failed to persist position {position.id} ({exc}); keeping in memory only",
                    "WARNING",
                )
                await position_manager.add_position(position)
            strategy.position_opened_this_session = True

            strategy.logger.log(
                f"✅ Position opened {symbol}: "
                f"Long @ ${long_fill['fill_price']}, "
                f"Short @ ${short_fill['fill_price']}, "
                f"Slippage: ${result.total_slippage_usd:.2f}, "
                f"Fees: ${entry_fees:.2f}",
                "INFO",
            )

            if strategy.one_position_per_session:
                strategy.logger.log(
                    "📊 Session limit: Will not open more positions this session (one_position_per_session=True)",
                    "INFO",
                )

            await strategy.dashboard.position_opened(position)

            return position

        except Exception as exc:  # pragma: no cover - defensive logging
            strategy.logger.log(
                f"❌ {opportunity.symbol}: Unexpected error - {exc}",
                "ERROR",
            )
            strategy.failed_symbols.add(opportunity.symbol)
            return None

    async def _ensure_contract_attributes(self, exchange_client: Any, symbol: str) -> bool:
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
