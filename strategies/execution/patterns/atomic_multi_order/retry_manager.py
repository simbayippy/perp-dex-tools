"""Retry helpers for atomic multi-order execution."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any, Awaitable, Callable, Iterable, List, Optional, Sequence, Tuple

from helpers.unified_logger import log_stage
from strategies.execution.core.order_executor import ExecutionMode

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
        self._logger = logger  # Store logger for _collect_deficits
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
                # Uses same % threshold as position_closer (5%)
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
                imbalance_usd = abs(total_long_usd - total_short_usd)
                
                # Calculate imbalance as percentage: (max - min) / max
                critical_imbalance_pct = Decimal("0.05")  # 5% threshold
                min_usd = min(total_long_usd, total_short_usd)
                max_usd = max(total_long_usd, total_short_usd)
                
                imbalance_pct = Decimal("0")
                if max_usd > Decimal("0"):
                    imbalance_pct = (max_usd - min_usd) / max_usd
                
                if imbalance_pct > critical_imbalance_pct:
                    logger.warning(
                        f"⚠️ Critical imbalance detected during retry attempt {attempt}: "
                        f"longs=${total_long_usd:.2f}, shorts=${total_short_usd:.2f}, "
                        f"imbalance=${imbalance_usd:.2f} ({imbalance_pct*100:.1f}%). Aborting retries for safety."
                    )
                    # Return False to indicate retry failure and let main executor handle rollback
                    return False, attempts_used

            remaining = self._collect_deficits(contexts, policy)
            if not remaining:
                return True, attempts_used

            if policy.delay_seconds and attempt < policy.max_attempts:
                await asyncio.sleep(policy.delay_seconds)

        return False, attempts_used

    def _collect_deficits(
        self,
        contexts: Sequence[Any],
        policy: RetryPolicy,
    ) -> List[Tuple[Any, Decimal]]:
        deficits: List[Tuple[Any, Decimal]] = []
        for ctx in contexts:
            remaining_qty = getattr(ctx, "remaining_quantity", None)
            if remaining_qty is None:
                remaining_qty = Decimal("0")
            
            # Debug logging
            exchange_name = getattr(ctx.spec.exchange_client, "get_exchange_name", lambda: "UNKNOWN")()
            spec_qty = getattr(ctx.spec, "quantity", None)
            filled_qty = getattr(ctx, "filled_quantity", Decimal("0"))
            hedge_target = getattr(ctx, "hedge_target_quantity", None)
            
            if remaining_qty <= Decimal("0"):
                # Log why we're skipping
                if self._logger:
                    self._logger.debug(
                        f"[RETRY] Skipping {exchange_name.upper()} {ctx.spec.symbol}: "
                        f"remaining_qty={remaining_qty} (spec_qty={spec_qty}, filled={filled_qty}, "
                        f"hedge_target={hedge_target})"
                    )
                continue
            if policy.min_retry_quantity and remaining_qty < policy.min_retry_quantity:
                if self._logger:
                    self._logger.debug(
                        f"[RETRY] Skipping {exchange_name.upper()} {ctx.spec.symbol}: "
                        f"remaining_qty={remaining_qty} below min_retry_quantity={policy.min_retry_quantity}"
                    )
                continue
            
            if self._logger:
                self._logger.info(
                    f"[RETRY] Including {exchange_name.upper()} {ctx.spec.symbol}: "
                    f"remaining_qty={remaining_qty} (spec_qty={spec_qty}, filled={filled_qty})"
                )
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
            execution_mode = base_spec.execution_mode  # Default to original mode

            # Fetch current BBO prices for notional calculation and min check
            price_reference = None
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

            # Check minimum notional - if below threshold, use market order fallback
            if price_reference is not None:
                try:
                    exchange_client = base_spec.exchange_client
                    min_notional = exchange_client.get_min_order_notional(base_spec.symbol)
                    
                    if min_notional is not None and min_notional > Decimal("0"):
                        deficit_notional = quantity * price_reference
                        
                        # If below min notional, use market order instead of limit order
                        if deficit_notional < min_notional:
                            exchange_name = exchange_client.get_exchange_name().upper()
                            logger.info(
                                f"⚠️ [{exchange_name}] Retry quantity {quantity} "
                                f"(${deficit_notional:.2f}) below min notional ${min_notional:.2f}. "
                                f"Using market order fallback for faster execution."
                            )
                            execution_mode = "market_only"  # Use string value for OrderSpec
                            # Market orders don't need limit_price_offset_pct
                            limit_offset = None
                except Exception as exc:
                    logger.debug(
                        f"Could not check min notional for {base_spec.symbol}: {exc}. "
                        f"Proceeding with original execution mode."
                    )

            try:
                new_spec = replace(
                    base_spec,
                    size_usd=size_usd,
                    quantity=quantity,
                    timeout_seconds=timeout,
                    limit_price_offset_pct=limit_offset,
                    execution_mode=execution_mode,
                )
            except TypeError:
                # dataclass replace may fail if spec is not frozen; fall back to manual copy
                new_spec = type(base_spec)(
                    exchange_client=base_spec.exchange_client,
                    symbol=base_spec.symbol,
                    side=base_spec.side,
                    size_usd=size_usd,
                    quantity=quantity,
                    execution_mode=execution_mode,
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
