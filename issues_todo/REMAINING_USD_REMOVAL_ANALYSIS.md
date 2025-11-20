# Analysis: Remove `remaining_usd` Legacy Code

## Executive Summary

**Recommendation: REMOVE `remaining_usd` and `filled_usd` from hedge logic**

The code itself acknowledges this field is unreliable (hedge_manager.py line 74), yet it's still used as a fallback. This creates a dangerous edge case that could lead to over-hedging.

---

## Current Usage Analysis

### 1. Where `remaining_usd` is Defined

**File:** `contexts.py` lines 27-31

```python
@property
def remaining_usd(self) -> Decimal:
    """How much USD notional still needs to be hedged."""
    remaining = self.spec.size_usd - self.filled_usd
    return remaining if remaining > Decimal("0") else Decimal("0")
```

**Depends on:**
- `spec.size_usd` - initial target USD size (static, doesn't update)
- `filled_usd` - accumulated USD from fills (calculated, error-prone)

---

### 2. Where `filled_usd` is Tracked

**File:** `contexts.py` lines 50-68 (record_fill method)

```python
def record_fill(self, quantity: Optional[Decimal], price: Optional[Decimal]) -> None:
    """Accumulate executed quantity and USD notionals."""
    if quantity is None or quantity <= Decimal("0"):
        return

    self.filled_quantity += quantity  # ✅ Reliable - from exchange

    if price is not None and price > Decimal("0"):
        self.filled_usd += quantity * price  # ⚠️ Calculated - can be wrong
    elif self.filled_usd == Decimal("0"):
        # Fallback: assume full notional if price is unknown
        if quantity > Decimal("0"):
            self.filled_usd = self.spec.size_usd  # ⚠️ ASSUMPTION
```

**Problems:**
1. **Price dependency**: `filled_usd` requires accurate price for every fill
2. **Fallback logic**: If price is None, assumes `spec.size_usd` (wrong for partial fills)
3. **After cancellations**: Price info may be missing or stale
4. **Slippage**: Actual fill price != expected price, USD tracking drifts

---

### 3. Current Usage in HedgeManager

#### Usage Pattern (hedge_manager.py lines 110-115):

```python
size_usd_arg: Optional[Decimal] = None
quantity_arg: Optional[Decimal] = None
try:
    # Always prioritize quantity over USD when hedging (more accurate)
    if remaining_qty > Decimal("0"):
        quantity_arg = remaining_qty  # ✅ PRIMARY PATH
    elif remaining_usd > Decimal("0"):
        size_usd_arg = remaining_usd  # ⚠️ FALLBACK PATH (dangerous!)
    else:
        continue
```

**Same pattern repeated** in aggressive_limit_hedge (lines 288-293)

#### When is the fallback used?

**Scenario: `remaining_qty <= 0` but `remaining_usd > 0`**

This means:
- We've filled the required **quantity** (`filled_quantity >= hedge_target`)
- But USD tracking thinks we're under-filled (`filled_usd < spec.size_usd`)

**Why does this happen?**
1. Price tracking is incomplete (missing prices for some fills)
2. Price used for `filled_usd` differs from actual execution price
3. `spec.size_usd` doesn't account for slippage or price changes

**What happens if we hedge?**
- We've already filled the required quantity
- Hedging more would **OVER-HEDGE** and create directional exposure
- This is **EXACTLY** the bug we're trying to prevent!

---

### 4. The Code's Own Warning

**File:** hedge_manager.py line 74-75

```python
# remaining_usd is unreliable after cancellation (may be based on wrong spec.size_usd)
# Only use it as fallback if remaining_qty is 0
```

**Translation:** "This is unreliable, but we use it anyway as a last resort"

This is a **code smell** - if it's unreliable, we shouldn't use it at all, especially for critical hedge decisions.

---

### 5. Other Usage Locations

#### Logging Only (Safe):
- hedge_manager.py lines 99-100, 276-277: Display in log messages
  ```python
  if remaining_usd > Decimal("0"):
      log_parts.append(f"${float(remaining_usd):.2f}")
  ```

#### Skip Check (Redundant):
- hedge_manager.py lines 78, 261:
  ```python
  if remaining_usd <= Decimal("0") and remaining_qty <= Decimal("0"):
      continue
  ```
  - If `remaining_qty <= 0`, we should skip regardless of `remaining_usd`
  - The `and remaining_usd` part adds no value, only confusion

#### Reconciliation Check (Dubious):
- utils.py line 58 in `reconcile_context_after_cancel()`:
  ```python
  if ctx.remaining_usd <= Decimal("0"):
      return
  ```
  - Should check `remaining_quantity` instead
  - If quantity is 0, reconciliation is unnecessary regardless of USD

---

## Why Quantity is Superior

### 1. **Direct from Exchange**
- Exchanges report filled quantity directly in order status
- No calculation, no assumptions, no drift
- Example: `filled_size: 1.187` (exact)

### 2. **No Price Dependency**
- Quantity doesn't change with price movements
- Works correctly even if price data is missing
- Not affected by slippage

### 3. **Used by OrderExecutor**
- Both parameters are optional: `size_usd` or `quantity`
- When both provided, quantity takes precedence
- OrderExecutor converts `size_usd` → `quantity` anyway using current BBO
- So passing `size_usd` just adds an extra conversion step

**From order_executor.py lines 124-125:**
```python
if size_usd is None and quantity is None:
    raise ValueError("OrderExecutor.execute_order requires size_usd or quantity")
```

**Conversion in limit_order_executor.py lines 92-97:**
```python
if quantity is not None:
    order_quantity = Decimal(str(quantity)).copy_abs()
else:
    if size_usd is None:
        raise ValueError("Limit execution requires size_usd or quantity")
    order_quantity = (Decimal(str(size_usd)) / limit_price).copy_abs()
```

---

## The Dangerous Edge Case

### Scenario: Over-Hedging Bug

**Setup:**
1. Initial spec: Buy 4.393 XMR @ $365 = $1,603.45
2. Fill 4.393 XMR @ $370 (slippage) = $1,625.41 actual USD
3. `filled_quantity = 4.393` ✅
4. `filled_usd = 1,625.41` (more than `spec.size_usd = 1,603.45`)
5. But due to missing price for one partial fill, `filled_usd = 1,200` (wrong!)

**State:**
- `remaining_qty = 4.393 - 4.393 = 0` ✅ Correct!
- `remaining_usd = 1,603.45 - 1,200 = 403.45` ⚠️ Wrong!

**Current code path:**
```python
if remaining_qty > Decimal("0"):
    # Skipped (remaining_qty = 0)
elif remaining_usd > Decimal("0"):
    size_usd_arg = 403.45  # ❌ HEDGES EXTRA $403.45!
```

**Result:**
- We already filled the required 4.393 XMR
- But we hedge an extra $403.45 worth (≈1.09 XMR)
- **Total exposure: 5.483 XMR instead of 4.393 XMR**
- **Delta-neutrality broken!**

This is the **EXACT TYPE OF BUG** we just fixed with partial fill tracking!

---

## Proposed Changes

### Phase 1: Remove Dangerous Fallback (High Priority)

#### 1.1 HedgeManager.hedge() - Remove USD Fallback

**Current (hedge_manager.py lines 106-115):**
```python
size_usd_arg: Optional[Decimal] = None
quantity_arg: Optional[Decimal] = None
try:
    # Always prioritize quantity over USD when hedging (more accurate)
    if remaining_qty > Decimal("0"):
        quantity_arg = remaining_qty
    elif remaining_usd > Decimal("0"):
        size_usd_arg = remaining_usd  # ❌ REMOVE THIS
    else:
        continue
```

**Proposed:**
```python
# Always use quantity for hedging - USD tracking is unreliable after cancellations
if remaining_qty <= Decimal("0"):
    continue

try:
    execution = await hedge_executor.execute_order(
        exchange_client=spec.exchange_client,
        symbol=spec.symbol,
        side=spec.side,
        quantity=remaining_qty,  # Always use quantity
        mode=ExecutionMode.MARKET_ONLY,
        timeout_seconds=spec.timeout_seconds,
        reduce_only=reduce_only,
    )
```

**Benefits:**
- Removes dangerous fallback
- Clearer logic (single decision path)
- No over-hedging risk
- 10 lines → 5 lines

---

#### 1.2 HedgeManager.aggressive_limit_hedge() - Same Fix

**Current (hedge_manager.py lines 286-293):**
```python
size_usd_arg: Optional[Decimal] = None
quantity_arg: Optional[Decimal] = None
if remaining_qty > Decimal("0"):
    quantity_arg = remaining_qty
elif remaining_usd > Decimal("0"):
    size_usd_arg = remaining_usd  # ❌ REMOVE THIS
else:
    continue
```

**Proposed:**
```python
if remaining_qty <= Decimal("0"):
    continue
```

Then use `remaining_qty` directly in order placement (line 347).

---

#### 1.3 Fix Skip Check Logic

**Current (hedge_manager.py line 78):**
```python
if remaining_usd <= Decimal("0") and remaining_qty <= Decimal("0"):
    # ... suspicious scenario check ...
    continue
```

**Proposed:**
```python
if remaining_qty <= Decimal("0"):
    # ... suspicious scenario check (simplified) ...
    continue
```

**Rationale:** If quantity is filled, we're done. USD doesn't matter.

---

#### 1.4 Fix Reconciliation Check

**Current (utils.py line 58):**
```python
if ctx.remaining_usd <= Decimal("0"):
    return
```

**Proposed:**
```python
if ctx.remaining_quantity <= Decimal("0"):
    return
```

**Rationale:** Reconciliation is about finding missing quantity fills, not USD.

---

### Phase 2: Update Logging (Medium Priority)

Keep USD in logs for human readability, but calculate it on-the-fly from quantity:

**Current (hedge_manager.py lines 96-104):**
```python
log_parts = []
if remaining_qty > Decimal("0"):
    log_parts.append(f"qty={remaining_qty}")
if remaining_usd > Decimal("0"):
    log_parts.append(f"${float(remaining_usd):.2f}")  # ❌ Unreliable
descriptor = ", ".join(log_parts) if log_parts else "0"
logger.info(
    f"⚡ Hedging {spec.symbol} on {exchange_name} for remaining {descriptor}"
)
```

**Proposed:**
```python
# Calculate USD estimate from quantity using latest BBO (for logging only)
estimated_usd = Decimal("0")
if remaining_qty > Decimal("0"):
    try:
        best_bid, best_ask = await self._price_provider.get_bbo_prices(
            spec.exchange_client, spec.symbol
        )
        price = best_ask if spec.side == "buy" else best_bid
        estimated_usd = remaining_qty * price
    except Exception:
        pass  # Skip USD estimate if BBO unavailable

logger.info(
    f"⚡ Hedging {spec.symbol} on {exchange_name}: "
    f"{remaining_qty} qty (≈${float(estimated_usd):.2f})"
)
```

**Benefits:**
- USD shown in logs for human readability
- But calculated from reliable quantity, not from error-prone tracking
- Clear it's an estimate (≈ symbol)

---

### Phase 3: Remove filled_usd Tracking (Low Priority, Optional)

If we're not using `remaining_usd` for decisions, we can simplify the context:

**Option A: Remove entirely**
- Remove `filled_usd` field from OrderContext
- Remove `remaining_usd` property
- Simplify `record_fill()` to only track quantity

**Option B: Keep for reporting only**
- Keep tracking for post-execution reporting
- But never use for hedge decisions
- Add comment: "For reporting only, not reliable for decisions"

**Recommendation: Option A (Clean removal)**
- Simpler is better
- If we need USD for reporting, calculate it from `filled_quantity * actual_price`
- Use `result["slippage_usd"]` for accurate USD tracking

---

## Impact Analysis

### Files to Modify:

1. **hedge_manager.py** (2 methods)
   - `hedge()` lines 106-115: Remove USD fallback
   - `aggressive_limit_hedge()` lines 286-293: Remove USD fallback
   - Skip checks lines 78, 261: Simplify to quantity-only
   - Logging lines 96-104, 273-283: Update to calculated USD

2. **utils.py**
   - `reconcile_context_after_cancel()` line 58: Use quantity check

3. **contexts.py** (Optional Phase 3)
   - Remove `filled_usd` field
   - Remove `remaining_usd` property
   - Simplify `record_fill()`

### Lines Changed:
- **Phase 1 (Critical)**: ~30 lines across 2 files
- **Phase 2 (Logging)**: ~20 lines
- **Phase 3 (Optional)**: ~20 lines in contexts.py

### Risk Level:
- **Phase 1**: Low risk, HIGH benefit (removes dangerous code path)
- **Phase 2**: Very low risk (logging only)
- **Phase 3**: Low risk (cleanup, no behavior change)

---

## Testing Strategy

### 1. Verify Existing Tests Still Pass

Run existing test suite, especially:
- `test_edge_case_verification.py::test_market_hedge_partial_fill_before_cancel_tracked`
- All hedge manager tests

### 2. Add Specific Test for Removed Fallback

**Test:** Verify hedge does NOT execute when quantity filled but USD tracking is wrong

```python
@pytest.mark.asyncio
async def test_hedge_skips_when_quantity_filled_even_if_usd_tracking_wrong():
    """
    Verify that hedge is skipped when quantity is fully filled,
    even if filled_usd tracking thinks there's remaining USD.
    
    This prevents over-hedging due to unreliable USD tracking.
    """
    hedge_manager = HedgeManager()
    
    # Simulate context that is fully filled by quantity
    ctx = OrderContext(
        spec=OrderSpec(
            exchange_client=mock_client,
            symbol="XMR",
            side="buy",
            size_usd=Decimal("1600"),  # Original target
            quantity=Decimal("4.393")   # Original target
        ),
        cancel_event=asyncio.Event(),
        task=asyncio.create_task(asyncio.sleep(0)),
        completed=True,
        filled_quantity=Decimal("4.393"),  # ✅ Fully filled by quantity
    )
    
    # Simulate filled_usd being WRONG (due to missing price)
    # This would make remaining_usd > 0, triggering old fallback bug
    ctx.filled_usd = Decimal("1200")  # Wrong! Should be 1600
    ctx.hedge_target_quantity = Decimal("4.393")
    
    # Mock trigger context (doesn't matter for this test)
    trigger_ctx = OrderContext(...)
    
    # Execute hedge - should skip since quantity is filled
    with patch('strategies.execution.core.order_executor.OrderExecutor') as mock_exec_cls:
        mock_executor = AsyncMock()
        mock_exec_cls.return_value = mock_executor
        
        success, error = await hedge_manager.hedge(
            trigger_ctx=trigger_ctx,
            contexts=[trigger_ctx, ctx],
            logger=executor.logger,
            reduce_only=False
        )
        
        # CRITICAL: Verify NO hedge order was placed
        mock_executor.execute_order.assert_not_called()
        
        # Hedge should succeed (nothing to do)
        assert success is True
        assert error is None
```

---

## Implementation Plan

### Stage 1: Remove USD Fallback (1-2 hours)
1. Update hedge_manager.py: Remove `elif remaining_usd > 0` branches
2. Update hedge_manager.py: Simplify skip checks to quantity-only  
3. Update utils.py: Use quantity check in reconciliation
4. Add test case for removed fallback
5. Run full test suite

**Risk: Low** - Removes dangerous code path, makes logic simpler

---

### Stage 2: Update Logging (30 mins)
1. Update logging to calculate USD from quantity on-the-fly
2. Update tests if they check log messages

**Risk: Very Low** - Logging only, no behavior change

---

### Stage 3: Remove filled_usd (1 hour, Optional)
1. Remove `filled_usd` from OrderContext
2. Remove `remaining_usd` property
3. Simplify `record_fill()` method
4. Update any tests that reference these fields

**Risk: Low** - Cleanup, makes code simpler

---

## Recommendation

**Proceed with Stage 1 immediately** (High Priority)

**Rationale:**
- The USD fallback is **actively dangerous** - it can cause over-hedging
- The code itself admits it's unreliable
- Quantity is always more reliable (direct from exchange)
- Removal makes code simpler and safer
- Low implementation time (1-2 hours)
- No behavior change for normal cases (they already use quantity)
- Only removes a buggy edge case

**Include in hedge_manager refactoring plan as Pre-Requisite** (Stage 0)
- Do this BEFORE the larger refactoring
- Makes the refactoring cleaner (less legacy code to deal with)
- Fixes a potential bug in production

---

## Additional Notes

### Why was USD tracking added in the first place?

Likely reasons:
1. **Historical**: Early code might have used USD-only tracking
2. **Fallback safety net**: Seemed safer to have two checks than one
3. **Display**: Wanted to show USD in logs

But in practice:
- USD tracking proved unreliable (as the comment admits)
- The "safety net" is actually a bug (over-hedging)
- We can still display USD by calculating it from quantity

### Why didn't this cause issues before?

1. **Rare edge case**: Only triggers when quantity tracking works but USD tracking fails
2. **Masked by other logic**: Other parts of the code might prevent execution
3. **Small discrepancies**: Might have caused small over-hedges that went unnoticed
4. **Recent fix**: The partial fill tracking fix makes quantity tracking more reliable,
   exposing the USD tracking as the weak link

---

## Appendix: Complete Diff

### hedge_manager.py

```diff
     # Calculate remaining quantity
     remaining_qty = Decimal("0")
     
     if ctx.hedge_target_quantity is not None:
         hedge_target = Decimal(str(ctx.hedge_target_quantity))
         remaining_qty = hedge_target - ctx.filled_quantity
         if remaining_qty < Decimal("0"):
             remaining_qty = Decimal("0")
     else:
         remaining_qty = ctx.remaining_quantity
     
-    remaining_usd = ctx.remaining_usd
-    
-    if remaining_usd <= Decimal("0") and remaining_qty <= Decimal("0"):
+    if remaining_qty <= Decimal("0"):
         # Check for suspicious scenario...
         continue

-    log_parts = []
-    if remaining_qty > Decimal("0"):
-        log_parts.append(f"qty={remaining_qty}")
-    if remaining_usd > Decimal("0"):
-        log_parts.append(f"${float(remaining_usd):.2f}")
-    descriptor = ", ".join(log_parts) if log_parts else "0"
     logger.info(
-        f"⚡ Hedging {spec.symbol} on {exchange_name} for remaining {descriptor}"
+        f"⚡ Hedging {spec.symbol} on {exchange_name}: {remaining_qty} qty"
     )

-    size_usd_arg: Optional[Decimal] = None
-    quantity_arg: Optional[Decimal] = None
     try:
-        # Always prioritize quantity over USD when hedging (more accurate)
-        if remaining_qty > Decimal("0"):
-            quantity_arg = remaining_qty
-        elif remaining_usd > Decimal("0"):
-            size_usd_arg = remaining_usd
-        else:
-            continue
-        
         execution = await hedge_executor.execute_order(
             exchange_client=spec.exchange_client,
             symbol=spec.symbol,
             side=spec.side,
-            size_usd=size_usd_arg,
-            quantity=quantity_arg,
+            quantity=remaining_qty,
             mode=ExecutionMode.MARKET_ONLY,
             timeout_seconds=spec.timeout_seconds,
             reduce_only=reduce_only,
         )
```

**Total:** ~15 lines removed, clearer logic

---

**End of Analysis**

