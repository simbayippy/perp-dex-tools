"""Retry helpers for atomic multi-order execution."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any, Awaitable, Callable, Iterable, List, Optional, Sequence, Tuple

from helpers.unified_logger import log_stage

from .utils import apply_result_to_context


@dataclass
class RetryPolicy:
    """
    Configuration for re-submitting unfilled legs after the primary attempt.

    Attributes:
        max_attempts: Maximum number of retry passes (0 disables retries).
        per_attempt_timeout: Timeout (seconds) for each retry order.
        delay_seconds: Optional delay between retry attempts.
        max_total_duration: Safety cap on total retry time (seconds).
        min_retry_quantity: Ignore deficits smaller than this quantity.
        limit_price_offset_pct_override: Optional override for limit price offset.
    """

    max_attempts: int = 0
    per_attempt_timeout: float = 15.0
    delay_seconds: float = 0.0
    max_total_duration: float = 30.0
    min_retry_quantity: Decimal = Decimal("0")
    limit_price_offset_pct_override: Optional[Decimal] = None


class RetryManager:
    """Plans and executes retry attempts for partially filled atomic orders."""

    def __init__(self, price_provider=None) -> None:
        self._price_provider = price_provider

    async def execute_retries(
        self,
        *,
        contexts: Sequence[Any],
        policy: RetryPolicy,
        place_order: Callable[[Any, asyncio.Event], Awaitable[Any]],
        logger,
        compose_stage: Callable[..., Optional[str]],
    ) -> Tuple[bool, int]:
        """
        Attempt to fill remaining deficits using refreshed limit orders.

        Returns:
            Tuple[success, attempts_used]
        """
        if policy.max_attempts <= 0:
            return False, 0

        attempts_used = 0
        start_ts = time.time()

        for attempt in range(1, policy.max_attempts + 1):
            deficits = self._collect_deficits(contexts, policy)
            if not deficits:
                return True, attempts_used

            if policy.max_total_duration and (time.time() - start_ts) > policy.max_total_duration:
                logger.warning(
                    "Retry max duration reached; aborting additional attempts to limit exposure."
                )
                break

            attempts_used = attempt
            stage_id = compose_stage("2", "retry", str(attempt))
            log_stage(logger, f"Retry Attempt {attempt}", icon="🔁", stage_id=stage_id)

            specs = await self._prepare_specs(deficits, policy, logger)
            if not specs:
                logger.warning("Retry planner produced no actionable orders; stopping retries.")
                break

            order_tasks = []
            for ctx, spec in specs:
                cancel_event = asyncio.Event()
                task = asyncio.create_task(place_order(spec, cancel_event))
                order_tasks.append((ctx, task))

            if order_tasks:
                results = await asyncio.gather(
                    *(t for _, t in order_tasks), return_exceptions=True
                )
                for (ctx, _), result in zip(order_tasks, results):
                    if isinstance(result, Exception):
                        logger.error(f"Retry order task failed for {ctx.spec.symbol}: {result}")
                        continue
                    apply_result_to_context(ctx, result)
                
                # Critical safety check: Detect imbalances after each retry attempt
                total_long_usd = sum(
                    getattr(ctx, "filled_usd", Decimal("0")) 
                    for ctx in contexts 
                    if getattr(ctx.spec, "side", "") == "buy"
                )
                total_short_usd = sum(
                    getattr(ctx, "filled_usd", Decimal("0"))
                    for ctx in contexts
                    if getattr(ctx.spec, "side", "") == "sell"
                )
                imbalance = abs(total_long_usd - total_short_usd)
                imbalance_tolerance = Decimal("10.00")  # Allow some imbalance during retries
                
                if imbalance > imbalance_tolerance:
                    logger.warning(
                        f"⚠️ Critical imbalance detected during retry attempt {attempt}: "
                        f"longs=${total_long_usd:.2f}, shorts=${total_short_usd:.2f}, "
                        f"imbalance=${imbalance:.2f}. Aborting retries for safety."
                    )
                    # Return False to indicate retry failure and let main executor handle rollback
                    return False, attempts_used

            remaining = self._collect_deficits(contexts, policy)
            if not remaining:
                return True, attempts_used

            if policy.delay_seconds and attempt < policy.max_attempts:
                await asyncio.sleep(policy.delay_seconds)

        return False, attempts_used

    @staticmethod
    def _collect_deficits(
        contexts: Sequence[Any],
        policy: RetryPolicy,
    ) -> List[Tuple[Any, Decimal]]:
        deficits: List[Tuple[Any, Decimal]] = []
        for ctx in contexts:
            remaining_qty = getattr(ctx, "remaining_quantity", None)
            if remaining_qty is None:
                remaining_qty = Decimal("0")
            if remaining_qty <= Decimal("0"):
                continue
            if policy.min_retry_quantity and remaining_qty < policy.min_retry_quantity:
                continue
            deficits.append((ctx, remaining_qty))
        return deficits

    async def _prepare_specs(
        self,
        deficits: Iterable[Tuple[Any, Decimal]],
        policy: RetryPolicy,
        logger,
    ) -> List[Tuple[Any, Any]]:
        prepared: List[Tuple[Any, Any]] = []

        for ctx, deficit_qty in deficits:
            base_spec = getattr(ctx, "spec", None)
            if base_spec is None:
                continue

            timeout = policy.per_attempt_timeout or base_spec.timeout_seconds
            limit_offset = (
                policy.limit_price_offset_pct_override
                if policy.limit_price_offset_pct_override is not None
                else base_spec.limit_price_offset_pct
            )

            size_usd = base_spec.size_usd
            quantity = deficit_qty

            if self._price_provider:
                try:
                    bid, ask = await self._price_provider.get_bbo_prices(
                        exchange_client=base_spec.exchange_client,
                        symbol=base_spec.symbol,
                    )
                    price_reference = Decimal(
                        str(ask if base_spec.side == "buy" else bid)
                    )
                    size_usd = quantity * price_reference
                except Exception as exc:
                    logger.warning(
                        f"Failed to refresh BBO for retry on {base_spec.symbol}: {exc}"
                    )
                    # Fall back to proportional scaling using existing spec
                    size_usd = _scale_notional(base_spec.size_usd, base_spec.quantity, quantity)
            else:
                size_usd = _scale_notional(base_spec.size_usd, base_spec.quantity, quantity)

            try:
                new_spec = replace(
                    base_spec,
                    size_usd=size_usd,
                    quantity=quantity,
                    timeout_seconds=timeout,
                    limit_price_offset_pct=limit_offset,
                )
            except TypeError:
                # dataclass replace may fail if spec is not frozen; fall back to manual copy
                new_spec = type(base_spec)(
                    exchange_client=base_spec.exchange_client,
                    symbol=base_spec.symbol,
                    side=base_spec.side,
                    size_usd=size_usd,
                    quantity=quantity,
                    execution_mode=base_spec.execution_mode,
                    timeout_seconds=timeout,
                    limit_price_offset_pct=limit_offset,
                )

            prepared.append((ctx, new_spec))

        return prepared


def _scale_notional(
    original_size_usd: Optional[Decimal],
    original_quantity: Optional[Decimal],
    target_quantity: Decimal,
) -> Optional[Decimal]:
    if original_size_usd is None or original_quantity in (None, Decimal("0")):
        return original_size_usd
    try:
        unit_notional = Decimal(str(original_size_usd)) / Decimal(str(original_quantity))
        return unit_notional * target_quantity
    except Exception:
        return original_size_usd
