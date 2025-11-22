# Closing Execution Analysis

## Summary

After analyzing the two closing trades in the logs, I've identified several issues with the current closing execution flow. The system is using `aggressive_limit` execution mode for **all** closing operations, which is not aligned with your original intention of using normal limit orders first and only escalating to aggressive limit when needed.

---

## Current Behavior vs. Expected Behavior

### Your Original Idea
1. **Both sides place normal limit orders** at mid-price (non-aggressive) to try closing positions
2. **Only when one side fully fills OR timeout** → find remaining qty unfilled in the other leg
3. **Then use aggressive limit** with break-even pricing first, followed by adaptive pricing strategy with multiple iterations

### Current Implementation
1. **Both sides immediately use `aggressive_limit` execution mode** from the start
2. This means both legs are using adaptive pricing (inside spread → touch → cross spread) right away
3. No initial attempt with passive mid-price limit orders

---

## Root Cause

The issue is in `/strategies/implementations/funding_arbitrage/operations/closing/order_builder.py`:

```python
# Lines 159-166
if is_critical and not use_market:
    execution_mode = "aggressive_limit"
elif not use_market:
    execution_mode = "aggressive_limit"  # ← ALL non-market closes use aggressive_limit
else:
    execution_mode = "market_only"
```

**Result**: Unless the close reason is in `critical_reasons` AND `order_type="market"` is explicitly set, **all closes use `aggressive_limit`**.

---

## Analysis of Closing Trade 1 (12:00:23 - 12:01:22)

### Position State
- **ASTER**: short 2.3320 @ entry 495.791668
- **LIGHTER**: long 2.3110 @ entry 495.246000
- **Reason**: SEVERE_EROSION (erosion 97.61%, limit 60%)
- **Slight qty imbalance**: 2.3320 vs 2.3110 (0.021 difference)

### Execution Flow

#### 1. Initial Aggressive Limit Execution (Both Sides)
- **LIGHTER**: Sell 2.311 @ 513.710 (attempt 1) → CANCELED (post-only violation)
- **LIGHTER**: Sell 2.311 @ 513.714 (attempt 2) → CANCELED (post-only violation)
- **LIGHTER**: Sell 2.311 @ 513.753 (attempt 3) → **FILLED** ✅
- **ASTER**: Buy 2.332 @ 514.490 (attempt 1) → EXPIRED
- **ASTER**: Buy 2.332 @ 514.440 (attempt 2) → **FILLED** ✅

**Both sides filled successfully** - this is good! The atomic close worked.

#### 2. Residual Qty Hedge (0.021 tokens)
After both main orders filled, the system detected a 0.021 qty imbalance (2.332 - 2.311) and tried to hedge it:

- Calculated hedge qty: **0.021 tokens on LIGHTER** (sell side)
- Break-even price: 514.388556 (not fillable given BBO bid=513.647, ask=514.033)
- Attempted aggressive limit: **amount=21** (0.021 in exchange units)
- **ERROR**: `code=21706 message='invalid order base or quote amount'`

**Issue**: The 0.021 qty is below LIGHTER's minimum order size, causing the order to fail.

#### 3. Emergency Rollback
- System attempted rollback but found **no open positions** (already closed)
- Rollback cost: $0.00
- **Final error**: `Atomic close failed for ZEC: Order statuses: LIGHTER:SELL ZEC PARTIAL (2.311000/2.311000); ASTER:BUY ZEC FILLED | Filled: 2/2 orders`

**Analysis**: The rollback was **unnecessary**. Both main orders filled completely, and the 0.021 residual was due to the original position imbalance (not an execution failure). The system should have recognized this as a successful close.

---

## Analysis of Closing Trade 2 (15:03:02 - 15:06:36)

### Position State
- **ASTER**: long 2.9830 @ entry 503.660000
- **LIGHTER**: short 2.9830 @ entry 502.791000
- **Reason**: DIVERGENCE_FLIPPED (erosion 101.59%, limit 60%)
- **Perfect qty balance**: 2.9830 on both sides

### Execution Flow

#### 1. Initial Aggressive Limit Execution

**ASTER** (sell 2.983):
- Attempt 1: @ 517.650 → **EXPIRED** (Aster's form of post-only violation)
- Attempt 2: @ 517.660 → Opened but not filled within timeout (8.59s)
- **Timeout** → Fallback to market order → **FILLED** @ 516.75 ✅

**LIGHTER** (buy 2.983):
- Attempt 1: @ 517.011 → CANCELED (post-only violation)
- Attempt 2: @ 516.996 → CANCELED (post-only violation)
- Attempt 3: @ 516.806 → **PARTIALLY FILLED** 1.230 tokens
- Continued filling: 0.700 → 0.930 → 1.047 → 1.230 → 1.424 → 2.084 → 2.677 → **FILLED** 2.983 ✅

**Both sides eventually filled**, but with significant delays and complexity.

#### 2. Post-Fill Confusion

After ASTER filled via market order and LIGHTER filled via aggressive limit, the system:

1. Detected LIGHTER had accumulated 1.230 fills (partial tracking issue)
2. Tried to place another order for remaining 1.753
3. **Multiple market order attempts** with 25-second timeouts each
4. Eventually triggered **emergency rollback** (unnecessary - positions already closed)

**Issue**: The reconciliation logic didn't properly track that LIGHTER had **fully filled** (2.983 total), leading to redundant hedge attempts.

---

## Key Problems Identified

### 1. **Wrong Execution Mode from Start**
- Using `aggressive_limit` immediately instead of starting with passive limit orders
- No implementation of mid-price limit orders as initial strategy

### 2. **Residual Qty Handling**
- System tries to hedge tiny imbalances (0.021 tokens) that are below minimum order size
- Should have a threshold to ignore negligible imbalances

### 3. **Reconciliation Issues**
- In Trade 2, the system lost track of LIGHTER's full fill (2.983 total)
- Continued trying to hedge "remaining" qty that didn't exist
- Led to unnecessary rollback attempts

### 4. **Rollback Logic**
- Rollback triggered even when positions are already closed
- Should verify actual position state before attempting rollback
- In both cases, rollback found no positions (correct outcome but wasted time)

### 5. **No Iteration Loop for Limit Orders**
- Your idea was to have a multi-iteration loop for limit orders before escalating
- Current implementation jumps straight to aggressive limit with adaptive pricing

---

## Recommendations

### 1. **Implement Two-Phase Closing Strategy**

```python
# Phase 1: Passive Limit Orders (NEW)
execution_mode = "limit_only"  # Mid-price, non-aggressive
timeout_seconds = 15.0  # Short timeout for first attempt

# Phase 2: Aggressive Limit (if Phase 1 fails or one side fills)
execution_mode = "aggressive_limit"  # Current behavior
```

### 2. **Add Minimum Hedge Threshold**

```python
# In full_fill_handler.py or hedge_manager.py
MIN_HEDGE_THRESHOLD = Decimal("0.05")  # 5% of original qty or absolute minimum

if remaining_qty < MIN_HEDGE_THRESHOLD:
    logger.info(f"Skipping hedge for negligible qty: {remaining_qty}")
    return success
```

### 3. **Fix Reconciliation Logic**

The issue in Trade 2 suggests the `accumulated_filled_qty` tracking got confused. Need to ensure:
- Properly track all partial fills
- Don't double-count fills
- Recognize when order is fully filled (remaining_qty ≈ 0)

### 4. **Improve Rollback Decision**

```python
# Before rollback, check if positions actually exist
if is_close_operation:
    actual_positions = await query_actual_positions()
    if all_positions_closed(actual_positions):
        logger.info("Positions already closed, skipping rollback")
        return success
```

### 5. **Add Execution Mode Config**

Allow configuration of closing execution strategy:

```yaml
closing_execution:
  initial_mode: "limit_only"  # or "aggressive_limit"
  initial_timeout: 15.0
  fallback_mode: "aggressive_limit"
  fallback_timeout: 30.0
```

---

## Specific Code Changes Needed

### 1. **order_builder.py** - Add execution mode logic

```python
def build_order_spec(self, symbol, leg, reason, order_type=None):
    # ... existing code ...
    
    # NEW: Check if this is initial attempt or hedge attempt
    is_hedge = leg.get("is_hedge", False)
    
    if is_hedge:
        # Hedge attempts use aggressive_limit
        execution_mode = "aggressive_limit"
    elif not use_market:
        # Initial close attempts use passive limit
        execution_mode = "limit_only"  # NEW
        timeout_seconds = 15.0  # Shorter timeout
    else:
        execution_mode = "market_only"
```

### 2. **full_fill_handler.py** - Add minimum hedge threshold

```python
async def handle(self, trigger_ctx, other_contexts, ...):
    # ... existing code ...
    
    # Calculate hedge targets
    for ctx in other_contexts:
        remaining_qty = ctx.remaining_quantity
        
        # NEW: Skip negligible hedges
        if remaining_qty < Decimal("0.05"):  # Configurable threshold
            self.logger.info(
                f"Skipping hedge for {ctx.spec.symbol}: "
                f"remaining qty {remaining_qty} below threshold"
            )
            ctx.hedge_target_quantity = Decimal("0")
            continue
```

### 3. **Add Two-Phase Atomic Close**

Create a new method in `CloseExecutor`:

```python
async def _close_legs_with_fallback(self, position, legs, reason):
    """
    Two-phase closing:
    1. Try passive limit orders first
    2. If one side fills or timeout, use aggressive limit for remaining
    """
    # Phase 1: Passive limits
    result1 = await self._try_passive_close(position, legs, reason)
    
    if result1.all_filled:
        return  # Success!
    
    # Phase 2: Aggressive limit for unfilled legs
    unfilled_legs = self._get_unfilled_legs(result1, legs)
    result2 = await self._close_legs_atomically(
        position, unfilled_legs, reason, order_type="aggressive_limit"
    )
```

---

## Testing Plan

1. **Test with balanced positions** (same qty on both sides)
2. **Test with imbalanced positions** (small qty difference like 0.021)
3. **Test with one side filling first** (verify hedge logic)
4. **Test with both sides filling simultaneously** (verify no unnecessary hedges)
5. **Test rollback scenarios** (verify rollback only when actually needed)

---

## Conclusion

The current implementation works (positions do get closed), but it's:
1. **More aggressive than intended** (uses aggressive_limit from start)
2. **Less efficient** (pays more fees, more market impact)
3. **Has edge case bugs** (tiny qty hedges, unnecessary rollbacks)

The recommended changes will:
1. **Align with your original design** (passive first, aggressive as fallback)
2. **Reduce fees** (more maker orders, less taker orders)
3. **Handle edge cases better** (skip tiny hedges, smarter rollback)
4. **Maintain safety** (still has aggressive fallback when needed)

---

## ✅ IMPLEMENTED FIXES

### Fix 1: Centralized Spread Utilities + Protection for Opening AND Closing

**Files Modified**:
1. **NEW**: `strategies/execution/core/spread_utils.py` (centralized utilities)
2. `strategies/execution/core/order_execution/limit_order_executor.py` (uses new utils)
3. `strategies/implementations/funding_arbitrage/operations/core/price_utils.py` (now imports from core)
4. `strategies/execution/core/__init__.py` (exports spread utils)

**What it does**:
- Created centralized `spread_utils.py` at execution core level (single source of truth)
- Checks spread RIGHT before placing limit order (not just in order_builder)
- Applies to **BOTH opening AND closing** operations (not just closing!)
- Uses proper constants: `MAX_ENTRY_SPREAD_PCT` (2%) and `MAX_EXIT_SPREAD_PCT` (2%)
- Protects against spread widening between initial check and order placement

**Architecture improvement**:
```
BEFORE (wrong dependency direction):
  execution/core/limit_order_executor.py
    └─ imports from ─→ implementations/funding_arbitrage/price_utils.py
    (core depends on implementation - BAD!)

AFTER (correct dependency direction):
  execution/core/spread_utils.py (single source of truth)
    ├─ used by ─→ execution/core/limit_order_executor.py
    └─ used by ─→ implementations/funding_arbitrage/price_utils.py
    (both depend on core - GOOD!)
```

**Code changes**:
```python
# In limit_order_executor.py - applies to BOTH opening and closing
is_opening = not reduce_only
acceptable, spread_pct, reason = is_spread_acceptable(
    best_bid, best_ask, is_opening=is_opening, is_critical=False
)

if not acceptable:
    # Reject order - spread too wide (opening or closing)
    return ExecutionResult(
        success=False,
        filled=False,
        error_message=f"Spread too wide: {reason}",
        execution_mode_used="limit_rejected_wide_spread",
        retryable=False,
    )
```

**Benefits**:
- ✅ Protects BOTH opening and closing from wide spreads
- ✅ Single source of truth (no code duplication)
- ✅ Correct dependency direction (core → implementation, not vice versa)
- ✅ Prevents getting "cooked" by spread widening
- ✅ Reusable across all execution strategies

---

### Fix 2: Changed Initial Closes to limit_only

**File**: `strategies/implementations/funding_arbitrage/operations/closing/order_builder.py`

**What it does**:
- Initial atomic close attempts now use `limit_only` instead of `aggressive_limit`
- Places passive maker orders: `ask - 0.01%` (buy) or `bid + 0.01%` (sell)
- Critical exits still use `aggressive_limit` for faster execution
- Hedges automatically use `aggressive_limit` (handled by HedgeManager, not order_builder)

**Code changes**:
```python
# BEFORE:
elif not use_market:
    execution_mode = "aggressive_limit"  # ALL closes used this

# AFTER:
elif is_critical:
    execution_mode = "aggressive_limit"  # Critical exits only
else:
    execution_mode = "limit_only"  # Initial closes use passive limits
```

**Benefits**:
- ✅ More passive initial attempts (better pricing)
- ✅ Lower fees (maker orders)
- ✅ Less market impact
- ✅ Hedge still uses aggressive_limit when one side fills

---

### How It Works Now

**Initial Close Attempt** (both legs simultaneously):
1. `order_builder.py` checks spread (line 108)
2. Creates OrderSpec with `execution_mode="limit_only"`
3. `LimitOrderExecutor` fetches fresh BBO
4. **NEW**: Checks spread again (protects against widening)
5. Places order at `ask - 0.01%` (buy) or `bid + 0.01%` (sell)
6. Waits for fill with 30s timeout

**If One Side Fills First**:
1. `FullFillHandler` detects full fill
2. Cancels other side's limit order
3. Calls `HedgeManager.aggressive_limit_hedge()`
4. **Hedge uses aggressive_limit** (not limit_only)
5. Break-even pricing attempt → adaptive pricing → market fallback

**Protection Against Spread Widening**:
```
Time T0 (order_builder):
  Spread check: 0.5% ✅ OK

Time T1 (500ms later, LimitOrderExecutor):
  Spread check: 2.5% ❌ TOO WIDE
  → Order REJECTED, returns error
  → Atomic executor will handle failure
```

---

## Testing Recommendations

1. **Monitor spread rejections**: Check logs for "Spread too wide" messages
2. **Compare fees**: Should see more maker fees, fewer taker fees
3. **Watch for timeouts**: limit_only may timeout more often (expected)
4. **Verify hedge behavior**: When one side fills, other side should use aggressive_limit

---

## Expected Behavior Changes

**Before** (aggressive_limit from start):
- First attempt: Places at `ask` (buy) or `bid` (sell) - very aggressive
- Retries with adaptive pricing
- Higher fill probability but more taker fees

**After** (limit_only → aggressive_limit):
- First attempt: Places at `ask - 0.01%` (buy) or `bid + 0.01%` (sell) - passive
- If times out or one side fills → escalates to aggressive_limit
- Lower fees, better pricing, still safe via spread checks

