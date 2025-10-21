# Atomic Multi-Order Executor

This document explains the end-to-end behaviour of the atomic multi-order execution stack:

- What components participate (`OrderExecutor`, `AtomicMultiOrderExecutor`, `RetryManager`, `HedgeManager`)
- How partial fills are handled
- How retries, hedging, and rollback interact
- Example timelines illustrating all major paths

The goal is to keep every trade delta-neutral: either both legs fill to the planned size or all residual exposure is flattened before we return control to the strategy.

---

## High-Level Architecture

```
┌───────────────────────────┐
│ PositionOpener/Closer     │
│  • Builds initial OrderSpec│
│  • Invokes execute_atomically()│
└────────────┬──────────────┘
             │
┌────────────▼──────────────┐
│ AtomicMultiOrderExecutor  │
│ 1. Launch limit legs      │
│ 2. Track partial fills    │
│ 3. Optionally retry       │
│ 4. Hedge residuals        │
│ 5. Rollback if needed     │
└──────┬─────────────┬──────┘
       │             │
   RetryManager   HedgeManager
   (limit re-tries) (market cleanup)
```

Supporting pieces:

- **OrderExecutor** (`strategies/execution/core/order_executor.py`) handles a single order lifecycle (limit/market, timeout management, cancel) and now records partial fills before returning.
- **OrderContext** tracks per-leg state: filled quantity, USD exposure, target quantity, cancellation event, etc.
- **RetryManager** plans additional limit orders for any remaining deficit before we resort to market hedges.
- **HedgeManager** flattens whatever exposure remains (market-only) and hands back success/failure.
- **Rollback logic** fires only if hedge fails (or if we configured `rollback_on_partial=False`, we leave the residual exposure for the caller).

---

## Detailed Flow

### 1. Initial Placement

1. Strategy builds two `OrderSpec` objects (one long, one short) and passes them into `AtomicMultiOrderExecutor.execute_atomically`.
2. The executor spawns an `OrderExecutor` task for each leg. Each task:
   - Places the limit order.
   - Polls status until full fill, cancellation, or timeout.
   - Records any partial fills while waiting (via `get_order_info` and cancel responses).
   - Cancels itself if the executor sets the cancellation event (e.g. because the sibling leg already completed).
3. As soon as one leg completes (either full fill or returning a partial), the executor:
   - Cancels the other in-flight leg(s).
   - Reconciles final fill sizes post-cancel using `get_order_info`.
   - Marks the “trigger context” (the leg that finished first).

At this point we have an accurate view of how much each leg filled. If everything matched, we exit successfully. Otherwise we proceed to retries.

### 2. Retry Cycle (Optional)

Retries are enabled by default (`atomic_retry.enabled = true`) with conservative guard rails:

- `max_attempts`: how many retry passes (default 2)
- `per_attempt_timeout_seconds`: timeout on each retry order (default 15 s)
- `retry_delay_seconds`: pause between attempts (default 1 s)
- `max_retry_duration_seconds`: cap total retry time (default 30 s)
- `min_retry_quantity`: ignore tiny deficits
- `limit_price_offset_pct_override`: optional override on price improvement for retries

Process:

1. `RetryManager` collects each context’s remaining deficit (`ctx.remaining_quantity`).
2. For non-zero deficits above the threshold:
   - Fetch fresh BBOs (if a shared price provider is available) to reprice the new orders.
   - Create new `OrderSpec`s with the deficit quantity, scaled USD notional, shorter timeout, and any configured price offset override.
   - Launch them through `_place_single_order`, reusing the same order-executor pipeline.
3. Merge retry fills back into the contexts (including reconciliation after cancellation).
4. Stop early if all deficits vanish; otherwise delay briefly and iterate until we hit `max_attempts` or the overall duration cap.

Retries ensure we make a second attempt to get maker fills, keeping us at the same venues and price regime before we fall back to taker fills (hedging).

### 3. Hedge Manager

If after retries we still have an imbalance:

1. `HedgeManager` computes `ctx.remaining_quantity` for each leg.
2. For any residual deficit it submits a market order on that exchange to match the trigger leg’s filled quantity.
3. If hedging succeeds, the executor reports success (`all_filled=True`) even though the cleanup used market fills.
4. If hedging fails (order rejected, timeout, etc.), we log the failure and move to rollback (if allowed).

### 4. Rollback

Rollback is the last resort when a hedge fails and `rollback_on_partial=True`:

1. Gather every leg that has any filled quantity.
2. Submit market orders in the opposite direction to close them out (using the exchange-specific contract IDs).
3. Track the incurred cost (`rollback_cost_usd`) so the strategy can log/report it.
4. Return failure to the caller (`success=False`, `all_filled=False`).

If `rollback_on_partial` were false, we would surface the residual imbalance to the caller to handle manually (e.g. by raising an alert or scheduling a later cleanup).

---

## Interaction Points with Strategy Code

- `FundingArbitrageStrategy` hydrates a shared `RetryPolicy` from `FundingArbConfig.atomic_retry` and passes it into both the position opener and closer.
- The atomic result now includes two additional fields:
  - `retry_attempts`: number of retry passes actually executed.
  - `retry_success`: whether retries alone resolved the imbalance (i.e., no hedge required).
- Position operations still inspect the high-level flags (`all_filled`, `rollback_performed`) but can optionally inspect retry metrics for analytics/monitoring.

---

## Example Timelines

### Example A – Smooth Execution

```
T+0s   Submit long & short limits (qty 1.0 each, timeout 60s)
T+4s   Both orders filled completely
T+4s   Executor returns success (no retries, no hedge)
```

Result: `all_filled=True`, `retry_attempts=0`, `retry_success=False` (no retry path taken).

### Example B – Partial + Retry Success

```
T+0s   Submit long & short limits
T+60s  Long reaches timeout with 0.4 filled → executor cancels short leg
T+61s  Retry attempt #1 places both legs with remaining 0.6 (timeout 15s)
T+76s  Short fills (total short 1.0), long fills to 0.7
T+77s  Retry attempt #2 issues remaining 0.3 on long (and ensures short remains balanced)
T+90s  Long fills remaining 0.3 → exposure matched
```

Result: `all_filled=True`, `retry_attempts=2`, `retry_success=True`, no hedge required.

### Example C – Partial + Hedge Cleanup

```
T+0s   Submit legs
T+60s  Long fills 0.3, short 0.0 → executor cancels short
T+61s  Retry #1 fails to fill short
T+77s  Retry #2 also fails (liquidity thin)
T+93s  HedgeManager submits short market order for 0.3 and succeeds
```

Result: `all_filled=True`, `retry_attempts=2`, `retry_success=False`, hedge executed (recorded in logs via HedgeManager). No rollback.

### Example D – Partial + Hedge Failure → Rollback

```
T+0s   Submit legs
T+60s  Long fills 0.3, short 0.0
T+61s  Retry #1, #2 both fail
T+93s  Hedge market short order is rejected (exchange error)
T+94s  Executor logs "Hedge failed — attempting rollback"
T+95s  Rollback market order closes the long 0.3 exposure
```

Result: `all_filled=False`, `rollback_performed=True`, `rollback_cost_usd` populated, strategy receives failure and can react (e.g., mark opportunity as failed).

### Example E – Both Legs Partial but Balanced

```
T+0s   Submit legs
T+60s  Both legs timeout, each filled 0.45
```

Because the exposures match (even though size < plan), there is no deficit:

- No retries triggered (`remaining_quantity=0` on both contexts)
- No hedging required

Result: `all_filled=True`, `retry_attempts=0`.

---

## Tuning Recommendations

- **Retry Duration**: Keep `max_total_duration_seconds` modest. An extra 30–45 s is usually acceptable; beyond that we risk staying directionally exposed too long.
- **Per-Attempt Timeout**: Short timeouts (10–20 s) keep us nimble; if a venue is illiquid, the market hedge will pick up the slack.
- **Min Retry Quantity**: Set to a small positive value (e.g., 0.01 base units) to avoid thrashing on dust-sized deficits.
- **Retry Offset Override**: Consider tightening (smaller offset) or widening (larger offset) specifically for retries if the venue requires more aggressive pricing on follow-up attempts.
- **Monitoring**: Alert on repeated hedge or rollback usage; heavy reliance may mean venue-specific liquidity issues or misconfigured size limits.

---

## Logging & Metrics

- Every retry attempt logs a stage with icon `🔁` and the attempt number.
- On hedge failure, we log a warning before switching to rollback.
- `AtomicExecutionResult` surfaces enough detail (`retry_attempts`, `retry_success`, `residual_imbalance_usd`, `rollback_cost_usd`) for higher-level analytics or dashboards.

---

## Summary

1. Place both legs simultaneously.
2. Track partial fills live; cancel siblings once one leg completes.
3. Retry the exact deficit (limit-only, with guard rails) to stay maker whenever possible.
4. Hedge at market if retries can’t balance exposure.
5. Roll back only when hedging fails, guaranteeing we never leave residual positions unintended.

This layered approach maximizes maker fills, minimizes delta exposure duration, and keeps failure handling deterministic and observable.
