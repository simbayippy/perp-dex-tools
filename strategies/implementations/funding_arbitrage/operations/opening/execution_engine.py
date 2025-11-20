"""Execution engine for opening positions."""

from decimal import Decimal, InvalidOperation, ROUND_DOWN
from typing import TYPE_CHECKING, Any, Dict, Optional

from exchange_clients import BaseExchangeClient
from strategies.execution.patterns.atomic_multi_order import AtomicExecutionResult, OrderSpec
from strategies.execution.core.price_alignment import BreakEvenPriceAligner
from helpers.unified_logger import log_stage

from ..core.websocket_manager import WebSocketManager
from ..core.decimal_utils import to_decimal
from ..models.execution_models import TradeExecutionResult, OrderPlan
from .entry_validator import EntryValidator

if TYPE_CHECKING:
    from ...strategy import FundingArbitrageStrategy
    from ...models import FundingArbPosition


class ExecutionEngine:
    """Handles trade execution for position opening."""
    
    def __init__(self, strategy: "FundingArbitrageStrategy"):
        self._strategy = strategy
        self._ws_manager = WebSocketManager()
    
    async def execute_trade(
        self,
        opportunity: Any,
        leverage_result: Dict[str, Any],
        contract_preparer: Any,
        position_builder: Any,
    ) -> Optional[TradeExecutionResult]:
        """
        Run validation, leverage normalization, and atomic execution.
        
        Args:
            opportunity: Trading opportunity
            leverage_result: Result from leverage validation
            contract_preparer: Contract preparer instance
            position_builder: Position builder instance
            
        Returns:
            TradeExecutionResult if successful, None otherwise
        """
        strategy = self._strategy
        symbol = opportunity.symbol
        long_dex = opportunity.long_dex
        short_dex = opportunity.short_dex

        if long_dex not in strategy.exchange_clients or short_dex not in strategy.exchange_clients:
            strategy.logger.warning(
                f"⛔ [SKIP] {symbol}: Missing exchange clients for {long_dex}/{short_dex}"
            )
            strategy.failed_symbols.add(symbol)
            return None

        long_client = strategy.exchange_clients[long_dex]
        short_client = strategy.exchange_clients[short_dex]

        log_stage(strategy.logger, f"{symbol} • Opportunity Validation", icon="📋", stage_id="1")
        strategy.logger.info(
            f"Ensuring {symbol} is tradeable on both {long_dex} and {short_dex}"
        )

        long_init_ok = await contract_preparer.ensure_contract_attributes(
            long_client, symbol, strategy.logger
        )
        short_init_ok = await contract_preparer.ensure_contract_attributes(
            short_client, symbol, strategy.logger
        )

        if not long_init_ok or not short_init_ok:
            if not long_init_ok:
                strategy.logger.warning(
                    f"⛔ [SKIP] Cannot trade {symbol}: Not supported on {long_dex.upper()} (long side)"
                )
            if not short_init_ok:
                strategy.logger.warning(
                    f"⛔ [SKIP] Cannot trade {symbol}: Not supported on {short_dex.upper()} (short side)"
                )
            strategy.failed_symbols.add(symbol)
            return None

        strategy.logger.info(
            f"✅ {symbol} available on both {long_dex.upper()} and {short_dex.upper()}"
        )

        adjusted_size = leverage_result["adjusted_size"]
        normalized_leverage = leverage_result.get("normalized_leverage")

        strategy.logger.info(
            f"🎯 Execution plan for {symbol}: "
            f"Long {long_dex.upper()} (${adjusted_size:.2f}) | "
            f"Short {short_dex.upper()} (${adjusted_size:.2f}) | "
            f"Divergence {opportunity.divergence*100:.3f}%"
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

        strategy.logger.debug(
            f"📏 Planned execution for {symbol}: qty={plan.quantity} "
            f"(long≈${plan.long_notional:.2f}, short≈${plan.short_notional:.2f})"
        )

        # Store normalized leverage in executor for margin calculations
        if normalized_leverage is not None:
            executor = strategy.atomic_executor
            long_exchange_name = long_client.get_exchange_name()
            short_exchange_name = short_client.get_exchange_name()
            executor._normalized_leverage[(long_exchange_name, symbol)] = normalized_leverage
            executor._normalized_leverage[(short_exchange_name, symbol)] = normalized_leverage

        # Get liquidation prevention config
        risk_config = strategy.config.risk_config
        enable_liquidation_prevention = getattr(risk_config, "enable_liquidation_prevention", True)
        min_liquidation_distance_pct = getattr(risk_config, "min_liquidation_distance_pct", None)
        
        result: AtomicExecutionResult = await strategy.atomic_executor.execute_atomically(
            orders=plan.orders,
            rollback_on_partial=True,
            pre_flight_check=True,
            skip_preflight_leverage=True,
            stage_prefix="3",
            enable_liquidation_prevention=enable_liquidation_prevention,
            min_liquidation_distance_pct=min_liquidation_distance_pct,
        )

        if not result.all_filled:
            strategy.logger.error(
                f"❌ {symbol}: Atomic execution failed - {result.error_message}"
            )

            if result.rollback_performed:
                strategy.logger.warning(
                    f"🔄 Emergency rollback performed, cost: ${result.rollback_cost_usd:.2f}"
                )

            strategy.failed_symbols.add(symbol)
            return None

        long_fill = result.filled_orders[0]
        short_fill = result.filled_orders[1]

        long_exposure = self._compute_leg_exposure(long_fill)
        short_exposure = self._compute_leg_exposure(short_fill)
        exposures = [exposure for exposure in (long_exposure, short_exposure) if exposure > Decimal("0")]
        effective_size = min(exposures) if exposures else adjusted_size

        imbalance_tokens = result.residual_imbalance_usd or Decimal("0")
        exposure_diff_usd = abs(long_exposure - short_exposure)
        
        if imbalance_tokens > Decimal("0.0001"):
            strategy.logger.warning(
                f"⚠️ {symbol}: residual quantity imbalance {imbalance_tokens:.6f} tokens after execution "
                f"(USD exposure diff: ${exposure_diff_usd:.2f} for reference)"
            )
        elif exposure_diff_usd > Decimal("0.01"):
            strategy.logger.debug(
                f"ℹ️ {symbol}: USD exposure difference ${exposure_diff_usd:.2f} (quantity balanced, price differences only)"
            )

        entry_fees = strategy.fee_calculator.calculate_total_cost(
            long_dex,
            short_dex,
            effective_size,
            is_maker=True,
        )
        total_cost = entry_fees + result.total_slippage_usd

        position, timestamp_iso = position_builder.build_new_position(
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
            imbalance_usd=imbalance_tokens,
            planned_quantity=plan.quantity,
            normalized_leverage=normalized_leverage,
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
            strategy.logger.error(
                "❌ Price provider not available; cannot prepare execution plan"
            )
            return None

        await self._ws_manager.prepare_websocket_feeds(long_client, symbol, strategy.logger)
        await self._ws_manager.prepare_websocket_feeds(short_client, symbol, strategy.logger)

        try:
            long_bid, long_ask = await price_provider.get_bbo_prices(long_client, symbol)
            short_bid, short_ask = await price_provider.get_bbo_prices(short_client, symbol)
        except Exception as exc:
            strategy.logger.error(
                f"❌ Failed to fetch BBO for {symbol}: {exc}"
            )
            return None

        if long_bid <= 0 or long_ask <= 0 or short_bid <= 0 or short_ask <= 0:
            strategy.logger.error(
                f"❌ Invalid BBO for {symbol}: long_bid={long_bid}, long_ask={long_ask}, "
                f"short_bid={short_bid}, short_ask={short_ask}"
            )
            return None

        # Validate price divergence before proceeding
        max_divergence_pct = getattr(strategy.config, "max_entry_price_divergence_pct", None)
        if max_divergence_pct is not None:
            is_valid, divergence_pct, reason = EntryValidator.validate_price_divergence(
                long_bid=Decimal(str(long_bid)),
                long_ask=Decimal(str(long_ask)),
                short_bid=Decimal(str(short_bid)),
                short_ask=Decimal(str(short_ask)),
                max_divergence_pct=Decimal(str(max_divergence_pct)),
            )
            
            if not is_valid:
                strategy.logger.warning(
                    f"⛔ [SKIP] {symbol}: Entry validation failed - {reason}"
                )
                # Mark cooldown if cooldown manager exists (for Issue 2)
                if hasattr(strategy, "cooldown_manager"):
                    strategy.cooldown_manager.mark_cooldown(symbol)
                strategy.failed_symbols.add(symbol)
                return None
            
            strategy.logger.debug(
                f"✅ [{symbol}] Price divergence validation passed: {divergence_pct*100:.2f}% "
                f"(threshold: {max_divergence_pct*100:.2f}%)"
            )

        # Use break-even price alignment if enabled
        enable_alignment = getattr(strategy.config, "enable_break_even_alignment", True)
        max_spread_threshold = getattr(strategy.config, "max_spread_threshold_pct", None)
        
        if enable_alignment:
            aligned_prices = BreakEvenPriceAligner.calculate_aligned_prices(
                long_bid=Decimal(str(long_bid)),
                long_ask=Decimal(str(long_ask)),
                short_bid=Decimal(str(short_bid)),
                short_ask=Decimal(str(short_ask)),
                limit_offset_pct=limit_offset_pct,
                max_spread_threshold_pct=max_spread_threshold,
            )
            long_price = aligned_prices.long_price
            short_price = aligned_prices.short_price
            
            strategy.logger.info(
                f"📊 [{symbol}] Price alignment: {aligned_prices.strategy_used} "
                f"(spread: {aligned_prices.spread_pct*100:.2f}%) - "
                f"long={long_price:.6f} < short={short_price:.6f}"
            )
            
            # Check for wide spread (BBO fallback or spread exceeds threshold)
            if aligned_prices.strategy_used == "bbo_fallback" or (
                max_spread_threshold is not None and 
                aligned_prices.spread_pct > max_spread_threshold
            ):
                # Mark cooldown for wide spread
                if hasattr(strategy, "cooldown_manager"):
                    strategy.cooldown_manager.mark_cooldown(symbol)
                    strategy.logger.debug(
                        f"⏸️  [{symbol}] Marked for cooldown due to wide spread "
                        f"({aligned_prices.spread_pct*100:.2f}%)"
                    )
        else:
            # Fallback to BBO-based pricing
            long_price = Decimal(str(long_ask))
            short_price = Decimal(str(short_bid))
            strategy.logger.debug(
                f"📊 [{symbol}] Using BBO-based pricing (alignment disabled): "
                f"long={long_price:.6f}, short={short_price:.6f}"
            )

        if long_price <= 0 or short_price <= 0:
            strategy.logger.error(
                f"❌ Invalid prices after alignment for {symbol}: long_price={long_price}, short_price={short_price}"
            )
            return None

        long_multiplier = Decimal(str(long_client.get_quantity_multiplier(symbol)))
        short_multiplier = Decimal(str(short_client.get_quantity_multiplier(symbol)))

        raw_long_qty = adjusted_size / long_price
        raw_short_qty = adjusted_size / short_price

        rounded_long_qty = self._round_quantity(long_client, raw_long_qty)
        rounded_short_qty = self._round_quantity(short_client, raw_short_qty)

        if long_multiplier != short_multiplier:
            long_actual_tokens = rounded_long_qty * long_multiplier
            short_actual_tokens = rounded_short_qty * short_multiplier
            
            common_actual_tokens = min(long_actual_tokens, short_actual_tokens)
            
            final_long_qty = common_actual_tokens / long_multiplier
            final_short_qty = common_actual_tokens / short_multiplier
            
            final_long_qty = self._round_quantity(long_client, final_long_qty)
            final_short_qty = self._round_quantity(short_client, final_short_qty)
            
            strategy.logger.debug(
                f"📊 [MULTIPLIER] {symbol}: long={final_long_qty} (×{long_multiplier}), "
                f"short={final_short_qty} (×{short_multiplier}) = {common_actual_tokens} tokens"
            )
        else:
            common_qty = min(rounded_long_qty, rounded_short_qty)
            final_long_qty = common_qty
            final_short_qty = common_qty

        if final_long_qty <= Decimal("0") or final_short_qty <= Decimal("0"):
            strategy.logger.warning(
                f"⛔ [SKIP] {symbol}: Unable to derive non-zero quantity after rounding "
                f"(long={final_long_qty}, short={final_short_qty})"
            )
            return None

        long_notional = final_long_qty * long_price
        short_notional = final_short_qty * short_price

        orders = [
            OrderSpec(
                exchange_client=long_client,
                symbol=symbol,
                side="buy",
                size_usd=long_notional,
                quantity=final_long_qty,
                execution_mode="limit_only",
                timeout_seconds=30.0,
                limit_price_offset_pct=limit_offset_pct,
            ),
            OrderSpec(
                exchange_client=short_client,
                symbol=symbol,
                side="sell",
                size_usd=short_notional,
                quantity=final_short_qty,
                execution_mode="limit_only",
                timeout_seconds=30.0,
                limit_price_offset_pct=limit_offset_pct,
            ),
        ]

        return OrderPlan(
            orders=orders,
            quantity=final_long_qty,
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

