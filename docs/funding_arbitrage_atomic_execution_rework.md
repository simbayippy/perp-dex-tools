# Funding Arb Atomic Execution Rework

Requirements and design notes for tightening the hedge entry logic used by the funding-arbitrage strategy.

## Goals
- Minimise directional exposure during entry even when only one leg fills.
- Preserve the ability to earn maker rebates when it makes economic sense.
- Keep the implementation extensible so we can later decide which venue should be maker vs taker based on fees/liquidity.

## Current Behaviour (2024-10)
- `PositionOpener` calls `AtomicMultiOrderExecutor` with **two** limit orders (`execution_mode="limit_with_fallback"`).
- Each limit order relies on the executor's default offset. Prior to this change the `order_executor` default was `Decimal("0.01")` (100 bps), which pushed quotes far from the top of book and rarely filled.
- `AtomicMultiOrderExecutor` waits for *both* legs to complete. If any leg times out or fails, it cancels everything and runs `_rollback_filled_orders`, which issues market orders to flatten any partial fills.
- Result: entry can hang for the full timeout, and when only one leg fills we stay exposed until the rollback routine completes.

## Immediate Improvement (Phase 1)
1. **Tighten limit pricing** *(completed)*
   - Allow per-order overrides of `limit_price_offset_pct` and shrink the default (now `0.0001` = 1 bp via `FundingArbConfig.limit_order_offset_pct`).
   - Optionally support negative offsets to intentionally cross the spread when we want an aggressive limit.

2. **Reactive hedging** *(in progress — initial version shipped)*
   - As soon as the first leg reports a confirmed fill, cancel pending sibling orders via cancellation events passed down to the order executors.
   - Immediately submit market orders on the remaining venues to complete the hedge; failures fall back to the rollback flow so exposure is flattened.
   - Execution metadata now tags hedge fills (`hedge=True`) so downstream consumers can distinguish market hedges from maker fills.

   - Negative values place the order at or beyond the touch (e.g., `-0.0002` crosses 2 bps to guarantee a near-instant fill while still being expressed as a limit order).

3. **Robust cancellation**
   - Ensure cancellation requests propagate before hitting the market leg to avoid double fills if the original limit eventually executes.
   - Guard against race conditions (e.g., the second leg fills while we are hedging) by reconciling filled quantities before committing to the market order amount.

## Longer-Term Enhancements (Phase 2+)
- **Maker/Taker Assignment**
  - Evaluate taker vs maker fees per exchange via `strategy.fee_calculator`.
  - Generate an execution plan such as `maker on cheaper venue, taker on other` or `dual taker if volatility high`.
  - Feed that plan into `AtomicMultiOrderExecutor` so each leg knows whether to start as limit or market.

- **Dynamic sizing & slippage guards**
  - Use recent liquidity snapshots to adapt quantity or split orders when order books are thin.
  - Incorporate slippage budgets per leg; abort or downsize when projected impact exceeds tolerance.

- **Telemetry & Circuit Breakers**
  - Track entry latency, hedge completion time, and realized spread to detect degradation.
  - Pause or switch to conservative mode when repeated hedge delays occur.

## Open Questions
- Do we need a configurable threshold before triggering the emergency market hedge (e.g., 30% fill vs any fill)?
- Should the hedge order be adaptive (limit with tight offset) rather than unconditional market to control slippage?
- How should we reconcile fills if both legs partially execute with differing amounts before cancellation completes?

## Next Actions
1. Implement the limit-offset override plumbing and set sane defaults for funding arb.
2. Refactor `AtomicMultiOrderExecutor` to react to per-leg completion and issue immediate hedge orders.
3. Add unit/integration coverage that simulates one leg filling first and verifies the opposite leg is crossed promptly.
