# Pricing Mechanisms for Delta-Neutral Execution

This document explains the two key pricing mechanisms used to minimize slippage and ensure break-even entry for delta-neutral positions:

1. **Break-Even Price Alignment** - Ensures `long_entry < short_entry` on initial position opening
2. **Aggressive Limit Order Hedging** - Reduces slippage when hedging after one leg fills

---

## 1. Break-Even Price Alignment

### Objective
Ensure `long_entry < short_entry` to avoid initial unrealized losses when entering delta-neutral positions.

### Mechanism: Min Mid Prices Strategy

**When**: Applied during initial limit order placement (before any fills occur)

**How it works**:

1. **Calculate mid prices** for both exchanges:
   ```
   long_mid = (long_bid + long_ask) / 2
   short_mid = (short_bid + short_ask) / 2
   min_mid = min(long_mid, short_mid)
   ```

2. **Calculate aligned prices**:
   ```
   long_price = min_mid - offset  (ensures long < short)
   short_price = min_mid + offset
   ```
   Where `offset` is typically 25% of the spread or a configurable limit offset.

3. **Feasibility checks**:
   - **Spread threshold**: Only apply if spread between exchanges < `max_spread_threshold_pct` (default: 0.5%)
   - **Post-only validation**: Ensure prices don't violate post-only rules (long_price ≥ long_ask or short_price ≤ short_bid)
   - **Market movement**: If exchanges diverge too much, fallback to BBO-based pricing

4. **Fallback**: If alignment not feasible → use BBO-based pricing:
   ```
   long_price = long_ask
   short_price = short_bid
   ```

**Configuration**:
- `enable_break_even_alignment`: Enable/disable (default: `True`)
- `max_spread_threshold_pct`: Max spread % to use aligned pricing (default: `0.005` = 0.5%)

**Key Principle**: Prioritize break-even when possible, but never sacrifice fill probability. If exchanges diverge or alignment would violate post-only, fallback to BBO-based pricing.

---

## 2. Aggressive Limit Order Hedging

### Objective
Reduce slippage when hedging after one leg fills, while maintaining delta neutrality speed.

### Mechanism: Adaptive Pricing with Retries

**When**: Triggered after one leg fully fills (or partially fills beyond threshold)

**How it works**:

1. **Break-Even Hedge Price** (if feasible):
   - Extract `trigger_fill_price` from the filled leg
   - Calculate break-even target: `hedge_price ≈ trigger_fill_price` (adjusted for side)
   - **Feasibility check**:
     - Must be within current BBO bounds
     - Market movement < `max_deviation_pct` (default: 0.5%)
   - If feasible → use break-even price
   - If not feasible → proceed to adaptive pricing

2. **Adaptive Pricing Strategy**:
   ```
   Retry 1-3 (inside_tick_retries): 1 tick inside spread
     - Buy: best_ask - tick_size
     - Sell: best_bid + tick_size
   
   Retry 4+: Touch (at best bid/ask)
     - Buy: best_ask
     - Sell: best_bid
   ```

3. **Retry Loop**:
   - Place limit order at calculated price
   - Poll for fills (up to `attempt_timeout` per retry, typically 1.5s)
   - **Post-only violation** → retry with fresh BBO
   - **Partial fill** → cancel remaining, place new order for remainder
   - **Full fill** → success
   - **Timeout** → cancel and retry (if retries remaining)

4. **Fill Tracking**:
   - Track `initial_filled_qty`: fills before aggressive hedge starts
   - Track `accumulated_filled_qty`: new fills during aggressive hedge
   - Track `current_order_filled_qty`: fills for current order in retry iteration
   - Final reconciliation check before market fallback

5. **Market Fallback**:
   - If timeout or retries exhausted → place market order for remaining quantity
   - Remaining quantity = `hedge_target - (initial_filled_qty + accumulated_filled_qty)`

**Configuration** (auto-configured based on operation type):

**Opening** (`reduce_only=False`):
- `max_retries`: 8
- `retry_backoff_ms`: 75ms
- `total_timeout_seconds`: 6.0s
- `inside_tick_retries`: 3

**Closing** (`reduce_only=True`):
- `max_retries`: 5
- `retry_backoff_ms`: 50ms
- `total_timeout_seconds`: 3.0s
- `inside_tick_retries`: 2

**Key Principle**: Balance slippage savings with delta neutrality speed. Start conservative (inside spread), become aggressive (touch) if needed, fallback to market if limit orders fail.

---

## Interaction Between Mechanisms

1. **Initial Entry**: Break-even alignment attempts to set `long_entry < short_entry`
2. **One Leg Fills**: Aggressive limit hedge attempts break-even relative to filled price
3. **If Break-Even Not Feasible**: Falls back to adaptive pricing (inside → touch)
4. **If Limit Orders Fail**: Market order fallback ensures delta neutrality

**Priority Order**:
1. Break-even (if feasible)
2. Adaptive limit orders (inside → touch)
3. Market orders (guaranteed fill)

---

## Key Files

- **Break-Even Alignment**: `strategies/execution/core/price_alignment.py`
- **Aggressive Limit Hedge**: `strategies/execution/patterns/atomic_multi_order/components/hedge_manager.py`
- **Configuration**: `strategies/implementations/funding_arbitrage/config.py`
- **Initial Entry**: `strategies/implementations/funding_arbitrage/operations/opening/execution_engine.py`

