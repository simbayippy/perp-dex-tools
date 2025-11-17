"""Hedging utilities for atomic multi-order execution."""

from __future__ import annotations

from decimal import Decimal
from typing import List, Optional, Tuple

from strategies.execution.core.execution_types import ExecutionMode

from ..contexts import OrderContext
from ..utils import apply_result_to_context, execution_result_to_dict


class HedgeManager:
    """Handles market hedges for partially filled atomic orders."""

    def __init__(self, price_provider=None) -> None:
        self._price_provider = price_provider

    async def hedge(
        self,
        trigger_ctx: OrderContext,
        contexts: List[OrderContext],
        logger,
        reduce_only: bool = False,
    ) -> Tuple[bool, Optional[str]]:
        """
        Attempt to flatten any residual exposure using market orders.

        Returns:
            Tuple of (success, error message).
        """
        # Lazy import to avoid circular dependency
        from strategies.execution.core.order_executor import OrderExecutor
        hedge_executor = OrderExecutor(price_provider=self._price_provider)

        for ctx in contexts:
            if ctx is trigger_ctx:
                continue

            spec = ctx.spec
            exchange_name = spec.exchange_client.get_exchange_name().upper()
            
            # CRITICAL: When hedging after a trigger fill, prioritize hedge_target_quantity
            # This ensures we hedge the correct amount to match the trigger fill, accounting
            # for quantity multipliers across exchanges.
            # Example: Aster fills 233960 TOSHI → Lighter should hedge 233.96 (233960/1000)
            remaining_qty = Decimal("0")
            
            # If hedge_target_quantity is set, use it directly (it's already calculated with multipliers)
            # This is the authoritative target quantity after accounting for cross-exchange multipliers
            if ctx.hedge_target_quantity is not None:
                hedge_target = Decimal(str(ctx.hedge_target_quantity))
                remaining_qty = hedge_target - ctx.filled_quantity
                if remaining_qty < Decimal("0"):
                    remaining_qty = Decimal("0")
                
                logger.debug(
                    f"📊 [HEDGE] {exchange_name} {spec.symbol}: "
                    f"hedge_target={hedge_target}, filled={ctx.filled_quantity}, "
                    f"remaining_qty={remaining_qty}"
                )
            else:
                # Fallback to remaining_quantity property (uses spec.quantity)
                remaining_qty = ctx.remaining_quantity
                logger.debug(
                    f"📊 [HEDGE] {exchange_name} {spec.symbol}: "
                    f"no hedge_target_quantity, using remaining_quantity={remaining_qty}"
                )
            
            # remaining_usd is unreliable after cancellation (may be based on wrong spec.size_usd)
            # Only use it as fallback if remaining_qty is 0
            remaining_usd = ctx.remaining_usd
            
            if remaining_usd <= Decimal("0") and remaining_qty <= Decimal("0"):
                # CRITICAL: Detect suspicious scenario where we're skipping hedge after a trigger fill
                # This can happen if reconciliation incorrectly added a false fill for a canceled order
                # If trigger filled but remaining_qty=0, something is wrong
                trigger_filled = trigger_ctx.filled_quantity > Decimal("0")
                if trigger_filled and ctx.hedge_target_quantity is not None:
                    hedge_target = Decimal(str(ctx.hedge_target_quantity))
                    if hedge_target > Decimal("0"):
                        logger.warning(
                            f"⚠️ [HEDGE] {exchange_name} {spec.symbol}: Skipping hedge (remaining_qty=0, remaining_usd=0) "
                            f"but trigger {trigger_ctx.spec.exchange_client.get_exchange_name().upper()} "
                            f"{trigger_ctx.spec.symbol} filled {trigger_ctx.filled_quantity} and hedge_target={hedge_target}. "
                            f"ctx.filled_quantity={ctx.filled_quantity}. "
                            f"This suggests reconciliation may have incorrectly added fills for a canceled order. "
                            f"Check reconciliation logs for warnings."
                        )
                continue

            log_parts = []
            if remaining_qty > Decimal("0"):
                log_parts.append(f"qty={remaining_qty}")
            if remaining_usd > Decimal("0"):
                log_parts.append(f"${float(remaining_usd):.2f}")
            descriptor = ", ".join(log_parts) if log_parts else "0"
            logger.info(
                f"⚡ Hedging {spec.symbol} on {exchange_name} for remaining {descriptor}"
            )

            size_usd_arg: Optional[Decimal] = None
            quantity_arg: Optional[Decimal] = None
            try:
                # Always prioritize quantity over USD when hedging (more accurate)
                if remaining_qty > Decimal("0"):
                    quantity_arg = remaining_qty
                elif remaining_usd > Decimal("0"):
                    size_usd_arg = remaining_usd
                else:
                    continue
                
                execution = await hedge_executor.execute_order(
                    exchange_client=spec.exchange_client,
                    symbol=spec.symbol,
                    side=spec.side,
                    size_usd=size_usd_arg,
                    quantity=quantity_arg,
                    mode=ExecutionMode.MARKET_ONLY,
                    timeout_seconds=spec.timeout_seconds,
                    reduce_only=reduce_only,  # Use reduce_only when hedging close operations
                )
            except Exception as exc:  # pragma: no cover - defensive
                logger.error(f"Hedge order failed on {exchange_name}: {exc}")
                return False, str(exc)

            if not execution.success or not execution.filled:
                error = execution.error_message or f"Market hedge failed on {exchange_name}"
                logger.error(error)
                return False, error

            hedge_dict = execution_result_to_dict(spec, execution, hedge=True)
            apply_result_to_context(ctx, hedge_dict)

        return True, None
