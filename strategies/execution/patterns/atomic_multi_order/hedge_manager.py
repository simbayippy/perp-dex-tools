"""Hedging utilities for atomic multi-order execution."""

from __future__ import annotations

from decimal import Decimal
from typing import List, Optional, Tuple

from strategies.execution.core.order_executor import ExecutionMode, OrderExecutor

from .contexts import OrderContext
from .utils import apply_result_to_context, execution_result_to_dict


class HedgeManager:
    """Handles market hedges for partially filled atomic orders."""

    def __init__(self, price_provider=None) -> None:
        self._price_provider = price_provider

    async def hedge(
        self,
        trigger_ctx: OrderContext,
        contexts: List[OrderContext],
        logger,
    ) -> Tuple[bool, Optional[str]]:
        """
        Attempt to flatten any residual exposure using market orders.

        Returns:
            Tuple of (success, error message).
        """
        hedge_executor = OrderExecutor(price_provider=self._price_provider)

        for ctx in contexts:
            if ctx is trigger_ctx:
                continue

            remaining_usd = ctx.remaining_usd
            remaining_qty = ctx.remaining_quantity
            if remaining_usd <= Decimal("0") and remaining_qty <= Decimal("0"):
                continue

            spec = ctx.spec
            exchange_name = spec.exchange_client.get_exchange_name().upper()
            logger.info(
                f"⚡ Hedging {spec.symbol} on {exchange_name} for remaining "
                f"${float(remaining_usd):.2f} (qty={remaining_qty})"
            )

            try:
                execution = await hedge_executor.execute_order(
                    exchange_client=spec.exchange_client,
                    symbol=spec.symbol,
                    side=spec.side,
                    size_usd=remaining_usd if remaining_usd > Decimal("0") else None,
                    quantity=remaining_qty if remaining_qty > Decimal("0") else None,
                    mode=ExecutionMode.MARKET_ONLY,
                    timeout_seconds=spec.timeout_seconds,
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
