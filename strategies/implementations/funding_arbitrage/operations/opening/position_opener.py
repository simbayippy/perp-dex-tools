"""Main orchestrator for opening funding arbitrage positions."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from ..core.contract_preparer import ContractPreparer
from .execution_engine import ExecutionEngine
from .leverage_validator import LeverageValidator
from .position_builder import PositionBuilder
from .persistence_handler import PersistenceHandler

if TYPE_CHECKING:
    from ...strategy import FundingArbitrageStrategy
    from ...models import FundingArbPosition


class PositionOpener:
    """Encapsulates the complex flow required to open a funding arb position."""

    def __init__(self, strategy: "FundingArbitrageStrategy") -> None:
        self._strategy = strategy
        self._contract_preparer = ContractPreparer()
        self._execution_engine = ExecutionEngine(strategy)
        self._leverage_validator = LeverageValidator(strategy)
        self._position_builder = PositionBuilder()
        self._persistence_handler = PersistenceHandler(strategy)

    async def open(self, opportunity) -> Optional["FundingArbPosition"]:
        """
        Attempt to open a position for the given opportunity.

        Returns:
            FundingArbPosition if the execution succeeds, otherwise None.
        """
        try:
            # Validate leverage and get adjusted size
            leverage_result = await self._leverage_validator.validate_leverage(
                symbol=opportunity.symbol,
                long_client=self._strategy.exchange_clients[opportunity.long_dex],
                short_client=self._strategy.exchange_clients[opportunity.short_dex],
            )

            if leverage_result is None:
                self._strategy.failed_symbols.add(opportunity.symbol)
                return None

            # Execute the trade
            execution = await self._execution_engine.execute_trade(
                opportunity=opportunity,
                leverage_result=leverage_result,
                contract_preparer=self._contract_preparer,
                position_builder=self._position_builder,
            )
            
            if execution is None:
                return None

            # Persist the position
            persistence = await self._persistence_handler.persist_position(
                position=execution.position,
                timestamp_iso=execution.timestamp_iso,
                total_cost=execution.total_cost,
                entry_fees=execution.entry_fees,
                total_slippage=execution.result.total_slippage_usd,
                position_builder=self._position_builder,
            )

            if persistence is None:
                return None

            # Store entry trades in database (non-blocking)
            await self._persistence_handler.store_entry_trades(
                position=execution.position,
                long_fill=execution.long_fill,
                short_fill=execution.short_fill,
            )

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
            
            # Send Telegram notification
            try:
                symbol = execution.position.symbol
                long_dex = execution.position.long_dex
                short_dex = execution.position.short_dex
                
                long_client = self._strategy.exchange_clients.get(long_dex)
                short_client = self._strategy.exchange_clients.get(short_dex)
                
                long_snapshot = None
                short_snapshot = None
                
                if long_client:
                    try:
                        long_snapshot = await long_client.get_position_snapshot(symbol)
                    except Exception as exc:
                        self._strategy.logger.debug(f"Could not fetch {long_dex} snapshot for notification: {exc}")
                
                if short_client:
                    try:
                        short_snapshot = await short_client.get_position_snapshot(symbol)
                    except Exception as exc:
                        self._strategy.logger.debug(f"Could not fetch {short_dex} snapshot for notification: {exc}")
                
                position_legs = execution.position.metadata.setdefault("legs", {})
                
                if long_snapshot:
                    long_leg = position_legs.setdefault(long_dex, {})
                    if long_snapshot.entry_price is not None:
                        long_leg["entry_price"] = long_snapshot.entry_price
                    if long_snapshot.quantity is not None:
                        long_leg["quantity"] = long_snapshot.quantity.copy_abs()
                    if long_snapshot.exposure_usd is not None:
                        long_leg["exposure_usd"] = long_snapshot.exposure_usd.copy_abs()
                
                if short_snapshot:
                    short_leg = position_legs.setdefault(short_dex, {})
                    if short_snapshot.entry_price is not None:
                        short_leg["entry_price"] = short_snapshot.entry_price
                    if short_snapshot.quantity is not None:
                        short_leg["quantity"] = short_snapshot.quantity.copy_abs()
                    if short_snapshot.exposure_usd is not None:
                        short_leg["exposure_usd"] = short_snapshot.exposure_usd.copy_abs()
                
                long_leg = position_legs.get(long_dex, {})
                short_leg = position_legs.get(short_dex, {})
                
                long_entry_price = long_leg.get("entry_price")
                short_entry_price = short_leg.get("entry_price")
                long_quantity = long_leg.get("quantity")
                short_quantity = short_leg.get("quantity")
                long_exposure = long_leg.get("exposure_usd")
                short_exposure = short_leg.get("exposure_usd")
                
                normalized_leverage = execution.position.metadata.get("normalized_leverage")
                margin_used = execution.position.metadata.get("margin_used")
                
                await self._strategy.notification_service.notify_position_opened(
                    symbol=symbol,
                    long_dex=long_dex,
                    short_dex=short_dex,
                    size_usd=execution.position.size_usd,
                    entry_divergence=execution.position.entry_divergence,
                    long_price=Decimal(str(long_entry_price)) if long_entry_price else None,
                    short_price=Decimal(str(short_entry_price)) if short_entry_price else None,
                    long_exposure=Decimal(str(long_exposure)) if long_exposure else None,
                    short_exposure=Decimal(str(short_exposure)) if short_exposure else None,
                    long_quantity=Decimal(str(long_quantity)) if long_quantity else None,
                    short_quantity=Decimal(str(short_quantity)) if short_quantity else None,
                    normalized_leverage=normalized_leverage,
                    margin_used=Decimal(str(margin_used)) if margin_used else None,
                )
            except Exception as exc:
                self._strategy.logger.warning(f"Failed to send position opened notification: {exc}")

            return persistence.position

        except Exception as exc:
            strategy = self._strategy
            strategy.logger.error(
                f"❌ {opportunity.symbol}: Unexpected error - {exc}"
            )
            strategy.failed_symbols.add(opportunity.symbol)
            return None

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

        imbalance_tokens = imbalance_usd
        if merged and updated_size is not None and additional_size is not None:
            logger.info(
                f"🔁 Position increased {symbol}: "
                f"New size ${updated_size:.2f} (added ${additional_size:.2f}), "
                f"Long @ ${long_fill['fill_price']}, "
                f"Short @ ${short_fill['fill_price']}, "
                f"Fees Δ ${entry_fees:.2f}, Slippage Δ ${total_slippage:.2f}, "
                f"Qty imbalance {imbalance_tokens:.6f} tokens"
            )
        else:
            logger.info(
                f"✅ Position opened {symbol}: "
                f"Long @ ${long_fill['fill_price']}, "
                f"Short @ ${short_fill['fill_price']}, "
                f"Size ${size_usd:.2f}, "
                f"Slippage: ${total_slippage:.2f}, "
                f"Fees: ${entry_fees:.2f}, "
                f"Qty imbalance {imbalance_tokens:.6f} tokens"
            )

