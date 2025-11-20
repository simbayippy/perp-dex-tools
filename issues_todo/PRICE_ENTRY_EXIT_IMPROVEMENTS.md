# Price Entry/Exit Improvements

## Description

Multiple improvements to price validation and exit logic to improve profitability by avoiding bad entries and optimizing exits.

## Completion Status

- ⏳ **Issue 1**: Delayed Exit Until Profitable - **TODO**
- ⏳ **Issue 2**: Take Profit on Price Divergence Opportunities - **TODO**
- ⏳ **Issue 3**: Wide Spread Protection on Exit - **TODO**

**Note**: Issues 1, 2, and 5 from the original document have been completed and removed. This document now focuses only on remaining TODO items.

## Issues

### 1. Delayed Exit Until Profitable (TODO)

**Problem**: When exit conditions are met (e.g., min_hold satisfied, erosion threshold), positions are immediately closed with market orders, which can lead to slippage and losses.

**Requested**:
- Don't immediately exit when exit conditions are satisfied
- Start "polling" prices to find a good exit point
- Exit when at least break-even (or profitable)
- Use limit/aggressive limit orders instead of market orders
- Assume prices will converge after entry (if entries were similar)

**Rationale**: 
- Price fluctuations are temporary
- Similar entry prices suggest convergence is likely
- Limit orders reduce slippage
- Waiting for profitability improves overall PnL

**Implementation Notes & Considerations**:

1. **State Management**:
   - Need to track exit polling state in position metadata: `exit_polling_state: Optional[Dict]`
   - State should include: `started_at`, `last_check_at`, `exit_reason`, `target_prices`
   - Consider using position status enum: `"exit_polling"` vs `"open"` vs `"closing"`

2. **Break-Even Calculation**:
   - Entry prices are stored in `position.metadata["legs"][dex]["entry_price"]`
   - For long leg: break-even = entry_price (need to sell at >= entry)
   - For short leg: break-even = entry_price (need to buy at <= entry)
   - Must account for fees: `break_even_with_fees = entry_price ± (fees / quantity)`
   - Consider using `position.total_fees_paid` to estimate per-leg fees

3. **Price Polling**:
   - Use existing `position_monitor.py` infrastructure for fetching snapshots
   - Poll interval should be configurable (15-30 seconds recommended)
   - Check current mark prices vs break-even prices
   - Use `ExchangePositionSnapshot.mark_price` for current prices

4. **Order Execution**:
   - Use `ExecutionMode.LIMIT_ONLY` or `ExecutionMode.AGGRESSIVE_LIMIT_INSIDE_SPREAD` (see `order_builder.py`)
   - Leverage existing `close_executor.py` infrastructure
   - Can reuse `order_builder.py.build_order_spec()` with `order_type="limit"`
   - Consider using `limit_price_offset_pct` for aggressive limit orders

5. **Timeout & Fallback**:
   - If polling timeout expires, fall back to current exit logic (market orders)
   - Should respect existing risk management (liquidation risk still triggers immediate close)
   - Consider max duration: 30-60 minutes before forcing exit

6. **Integration Points**:
   - Modify `exit_evaluator.py` to return exit reason but not trigger immediate close
   - Modify `position_closer.py.evaluateAndClosePositions()` to check for polling state
   - Add new method: `position_closer.start_exit_polling(position, reason)`
   - Add polling check in main strategy loop (similar to how positions are monitored)

7. **Edge Cases**:
   - What if position becomes profitable during polling? → Exit immediately
   - What if liquidation risk appears during polling? → Exit immediately (highest priority)
   - What if funding divergence flips during polling? → May want to exit faster
   - What if one leg fills but other doesn't? → Need partial fill handling

8. **Existing Code References**:
   - `position_closer.py` already supports `order_type="limit"` parameter
   - `close_executor.py._force_close_leg()` respects `order_type` parameter
   - `order_builder.py` has logic for limit vs market orders
   - `position.metadata["legs"]` contains entry prices and fill data

### 2. Take Profit on Price Divergence Opportunities (TODO)

**Problem**: When price movements create profitable opportunities (one leg profitable, other leg less losing), the system doesn't take advantage.

**Requested**:
- Monitor for situations where:
  - Same entry prices
  - One leg becomes more profitable due to price divergence
  - Other leg is less losing
  - Net PnL is positive
- Take profit in these scenarios
- On the flip side: if price deviates too much unfavorably, don't close even if funding flips (hold and wait for convergence)

**Use Case**: 
- Entry: Long at $100, Short at $100
- Price moves: Long leg now at $105 (profitable), Short leg at $103 (less losing)
- Net: +$2 profit → Take it!

**Implementation Notes & Considerations**:

1. **PnL Calculation**:
   - Entry prices: `position.metadata["legs"][dex]["entry_price"]`
   - Current prices: `ExchangePositionSnapshot.mark_price` (from `position_monitor.py`)
   - Quantities: `snapshot.quantity` or `position.metadata["legs"][dex]["quantity"]`
   - Long leg PnL: `(current_price - entry_price) * quantity`
   - Short leg PnL: `(entry_price - current_price) * quantity` (inverse)
   - Net PnL: `long_pnl + short_pnl - fees`
   - Consider using `position.get_net_pnl()` method if available

2. **Profit-Taking Conditions**:
   - Check if net PnL > `min_profit_taking_pct` threshold (e.g., 0.5-1%)
   - One leg profitable AND other leg not too losing (e.g., within 2-3% of entry)
   - Consider: `long_pnl > 0 AND short_pnl > -threshold` OR `short_pnl > 0 AND long_pnl > -threshold`
   - Must account for fees: `net_pnl > fees + buffer` (use `position.total_fees_paid` as reference)

3. **Hold Logic (Unfavorable Deviation)**:
   - If price deviates unfavorably beyond `max_price_deviation_for_hold_pct` (e.g., 5%)
   - Override normal exit conditions (erosion, funding flip) and hold position
   - Wait for price convergence before allowing normal exits
   - This prevents closing at a loss when price will likely converge back

4. **Integration Points**:
   - Add to `exit_evaluator.py` as a new check method: `check_profit_taking_opportunity()`
   - Should run AFTER liquidation risk check but BEFORE erosion/flip checks
   - Modify `exit_evaluator.should_close()` to include profit-taking check
   - Add hold override logic to prevent exits when unfavorable deviation detected

5. **Price Deviation Calculation**:
   - Calculate deviation for each leg: `abs(current_price - entry_price) / entry_price`
   - Unfavorable deviation: long leg below entry OR short leg above entry
   - If max deviation > threshold, set hold flag in position metadata
   - Clear hold flag when deviation returns to acceptable range

6. **Order Execution**:
   - Use limit orders for profit-taking (similar to Issue 3)
   - Can leverage same infrastructure as delayed exit
   - Consider using `order_type="limit"` with `limit_price_offset_pct` for better fills

7. **Edge Cases**:
   - What if one leg is very profitable but other is very losing? → Net PnL check prevents this
   - What if entry prices were different? → May need to normalize or skip profit-taking
   - What if position size changed (merged positions)? → Use weighted average entry price
   - What if funding divergence flips during hold? → Still hold if price deviation is unfavorable

8. **Existing Code References**:
   - `exit_evaluator.py` has `check_liquidation_risk()` - similar pattern to follow
   - `position_closer.py` evaluates exit conditions in `evaluateAndClosePositions()`
   - `position.get_net_pnl()` and `position.get_net_pnl_pct()` methods exist
   - `position.metadata["legs"]` contains all entry data needed
   - `position_monitor.py` fetches current snapshots with mark prices

9. **Configuration Considerations**:
   - `min_profit_taking_pct`: Minimum net profit to trigger (0.5-1% recommended)
   - `max_price_deviation_for_hold_pct`: Max unfavorable deviation before holding (5% recommended)
   - `profit_taking_loss_tolerance_pct`: Max loss on one leg while other is profitable (2-3% recommended)
   - Consider making these configurable per-symbol or based on volatility

### 3. Wide Spread Protection on Exit (TODO)

**Problem**: When closing positions, the system places limit orders without checking if the spread is too wide, leading to terrible execution and significant slippage losses.

**Example from Logs**:
```
✅ [PARADEX] BBO: bid=369.64, ask=378.2
[PARADEX] Placing limit buy XMR (contract_id=XMR-USD-PERP): 4.301 @ $378.12
```

**Issue Analysis**:
- Paradex has an **insane spread**: bid=369.64, ask=378.2 (~2.3% spread)
- System places limit buy order at $378.12 (near the ask price)
- When closing a short position, buying at ask = terrible execution
- This results in massive slippage losses ("getting cooked")
- The system doesn't validate spread width before placing close orders
- No mechanism to defer closing when liquidity is poor

**Impact**: 
- Significant slippage losses on exit
- Especially problematic for exchanges with poor liquidity (like Paradex)
- Can turn profitable positions into losses due to exit slippage

**Requested Solution**:

1. **Spread Validation Before Exit**:
   - Check BBO spread before placing close orders
   - Calculate spread percentage: `(ask - bid) / mid_price`
   - If spread exceeds threshold (e.g., >1-2%), defer closing or use alternative strategy

2. **Wide Spread Handling Options**:
   - **Option A**: Defer closing - Skip closing this iteration, retry later when spread narrows
   - **Option B**: Use market orders only if spread is acceptable (but still risky)
   - **Option C**: Place limit order on favorable side (bid for sells, ask for buys) and wait
   - **Option D**: Split order - place partial limit orders over time to reduce impact

3. **Implementation Approach**:
   - Add spread check in `order_builder.py` or `close_executor.py` before building order specs
   - Fetch BBO prices and calculate spread
   - If spread > threshold:
     - Log warning with spread details
     - Either skip closing (defer) or use market orders (if critical)
   - Add config: `max_exit_spread_pct` (default: 0.01 = 1%)
   - Add config: `wide_spread_exit_strategy` ("defer", "market", "limit_favorable_side")

4. **Edge Cases**:
   - **Critical exits (liquidation risk)**: Should still proceed even with wide spread, but use smarter order placement
   - **Emergency closes (LEG_LIQUIDATED)**: Currently uses market orders which fail on wide spread - needs special handling
   - **Post-only violations**: When spread is wide, limit orders at ask/bid may violate post-only - need to place inside spread
   - **Market order slippage failures**: When market order fails due to exceeds_max_slippage, limit fallback also fails - need better strategy
   - Consider exchange-specific thresholds (Paradex might need higher tolerance)
   - Track which exchanges consistently have wide spreads
   - May want to mark exchange for cooldown if spread consistently wide

5. **Emergency Close Specific Issues** (from logs):
   - **Problem**: When one leg is liquidated/missing, emergency close uses market orders
   - **Failure Chain**: Market order → exceeds_max_slippage → Limit fallback → post_only_violation → Complete failure
   - **Example**: Paradex bid=366.87, ask=378.2 (~3% spread), market order at ask fails, limit at ask violates post-only
   - **Solution**: **Leverage Hedge Manager's Aggressive Limit Strategy** (see `hedge_manager.py` and `strategies.py`)
     - The hedge manager already solves this exact problem with `AggressiveLimitHedgeStrategy`:
       - Places limit orders **inside spread** (1 tick inside) to avoid post-only violations
       - Retries with fresh BBO on post-only violations
       - Handles partial fills and continues filling remainder
       - Falls back to market orders if timeout/retries exhausted
       - Has adaptive pricing: starts inside spread, moves to touch after retries
     - **Reuse this infrastructure** for emergency closes:
       - Create a similar strategy for emergency closes (or reuse hedge strategy)
       - Check spread before attempting close
       - Use aggressive limit strategy instead of simple market → limit fallback
       - This ensures order fills as much as possible, even with wide spreads
     - **Benefits**:
       - Avoids post-only violations by placing inside spread
       - Handles partial fills gracefully
       - Retries intelligently with fresh BBO
       - Falls back to market only if truly necessary
       - Already battle-tested in hedge manager

**Files to Modify**:

**Phase 1: Refactoring (Recommended First Step)**:
- `strategies/execution/core/aggressive_limit_executor.py` (NEW) - Extract general-purpose aggressive limit execution
  - Move core logic from `AggressiveLimitHedgeStrategy`
  - Make it generic (not hedge-specific)
  - Support all execution scenarios (opening, closing, hedging)
- `strategies/execution/core/aggressive_limit_pricer.py` (NEW) - Extract price calculation logic
  - Move from `hedge_pricer.py`
  - General-purpose price calculation inside spread
- `strategies/execution/patterns/atomic_multi_order/components/hedge/strategies.py` - Update to use refactored version
  - Hedge manager becomes a thin wrapper around the general executor

**Phase 2: Integration**:
- `operations/closing/order_builder.py` - Add spread validation before building order spec
- `operations/closing/close_executor.py` - Check spread before executing close, especially in `_force_close_leg()`
  - **KEY**: Replace simple market → limit fallback with `AggressiveLimitExecutor`
  - Use the refactored general-purpose executor
- `operations/opening/execution_engine.py` - Consider using `AggressiveLimitExecutor` for position opening
  - Could improve entry execution quality (similar to how atomic_multi_order uses it)
- `operations/closing/exit_evaluator.py` - Consider spread when detecting liquidation (may want to defer if spread too wide)
- `config.py` - Add spread threshold config
- `config_builder/schema.py` - Add config builder prompts

**Reusable Components** (Current Location - Needs Refactoring):
- `strategies/execution/patterns/atomic_multi_order/components/hedge/strategies.py` - `AggressiveLimitHedgeStrategy`
  - Already handles: inside spread pricing, post-only retries, partial fills, market fallback
  - Currently hedge-specific but logic is general-purpose
- `strategies/execution/patterns/atomic_multi_order/components/hedge/hedge_pricer.py` - Price calculation logic
  - Calculates prices inside spread to avoid post-only violations
  - Currently hedge-specific but logic is general-purpose

**Architectural Improvement Opportunity**:
The `AggressiveLimitHedgeStrategy` is actually a **general-purpose execution strategy** that should be reusable across many scenarios, not just hedging. Similar to how `OrderExecutor` is in `strategies/execution/core/`, this should be refactored to a more general location.

**Proposed Refactoring**:
1. **Move to general execution layer**: `strategies/execution/core/aggressive_limit_executor.py` or `strategies/execution/strategies/aggressive_limit.py`
2. **Rename to be more general**: `AggressiveLimitExecutionStrategy` or `SmartLimitExecutor`
3. **Make it reusable for**:
   - **Position Opening**: Funding arb position opener could use this instead of simple limit orders
   - **Position Closing**: Emergency closes, wide spread exits
   - **Hedging**: Current use case (would use the refactored version)
   - **Any execution**: Any scenario needing smart limit order placement with retries

**Benefits of Refactoring**:
- **Reusability**: One implementation used across all execution scenarios
- **Consistency**: Same smart execution logic everywhere
- **Maintainability**: Fix bugs/improvements in one place
- **Testability**: Test once, use everywhere
- **Better Architecture**: Follows separation of concerns (execution logic separate from hedge-specific logic)

**Implementation Approach**:
- Create `strategies/execution/core/aggressive_limit_executor.py` (or similar)
- Extract core logic from `AggressiveLimitHedgeStrategy`
- Make it accept generic parameters (not hedge-specific)
- Update hedge manager to use the refactored version
- Use it in position opener, close executor, etc.

**Code References**:
- `order_builder.py.build_order_spec()` - Currently uses `extract_snapshot_price()` or `fetch_mid_price()`
- `close_executor.py._close_legs_atomically()` - Calls order_builder
- `close_executor.py._force_close_leg()` - **CRITICAL**: Emergency close path that needs spread protection
- `exit_evaluator.py.detect_liquidation()` - Triggers emergency close when leg missing
- BBO fetching: `price_provider.get_bbo_prices()` (used in execution_engine.py)
- Spread calculation similar to `entry_validator.py.validate_price_divergence()`
- Market order executor fallback logic (when market fails, limit fallback also fails on wide spread)

**Priority**: **HIGH** - This is causing immediate financial losses on every wide-spread exit

## Implementation Plan

### Issue 1: Delayed Exit Until Profitable

**Location**: `strategies/implementations/funding_arbitrage/operations/closing/`

**Changes**:
1. Add exit polling mechanism:
   - When exit conditions met, don't close immediately
   - Enter "exit_polling" state
   - Poll prices periodically (e.g., every 10-30 seconds)
   - Calculate break-even price based on entry prices
   - When current prices allow break-even exit, place limit orders
   
2. Exit polling logic:
   ```python
   # Calculate break-even prices
   long_break_even = long_entry_price  # For long: need to sell at >= entry
   short_break_even = short_entry_price  # For short: need to buy at <= entry
   
   # Check if current prices allow break-even
   if long_mark >= long_break_even and short_mark <= short_break_even:
       # Place limit orders at break-even or better
       place_limit_close_orders()
   ```

3. Add config:
   - `enable_exit_polling`: bool (default: True)
   - `exit_polling_interval_seconds`: int (default: 15)
   - `exit_polling_max_duration_minutes`: int (default: 30)
   - `exit_order_type`: str ("limit" or "aggressive_limit", default: "limit")

4. Fallback: If polling timeout expires, use current exit logic

**Files**:
- `exit_evaluator.py` - Add exit polling state
- `position_closer.py` - Implement polling logic
- `close_executor.py` - Use limit orders for exit
- `config.py` - Add polling config
- `models.py` - Add `exit_polling_state` to position metadata

### Issue 2: Take Profit on Divergence

**Location**: `strategies/implementations/funding_arbitrage/operations/closing/`

**Changes**:
1. Add profit-taking check in exit evaluator:
   - Calculate per-leg PnL
   - Check if one leg is profitable and other is less losing
   - If net PnL > threshold (e.g., > fees + small buffer), trigger profit-taking exit
   
2. Add config:
   - `enable_profit_taking`: bool (default: True)
   - `min_profit_taking_pct`: Decimal (default: 0.5% or 1.0%)
   - `max_price_deviation_for_hold_pct`: Decimal (default: 5.0%)
   
3. Profit-taking logic:
   ```python
   long_pnl = (long_mark - long_entry) * long_qty
   short_pnl = (short_entry - short_mark) * short_qty
   net_pnl = long_pnl + short_pnl
   
   if net_pnl > min_profit_taking_threshold:
       if (long_pnl > 0 and short_pnl > -some_threshold) or 
          (short_pnl > 0 and long_pnl > -some_threshold):
           return True, "PROFIT_TAKING"
   ```

4. Hold logic (opposite):
   - If price deviates unfavorably beyond threshold, don't close even if funding flips
   - Wait for price convergence

**Files**:
- `exit_evaluator.py` - Add profit-taking checks
- `position_closer.py` - Handle profit-taking exits
- `config.py` - Add profit-taking config

### Issue 3: Wide Spread Protection on Exit

**Location**: `strategies/implementations/funding_arbitrage/operations/closing/` + `strategies/execution/core/` (refactoring)

**Recommended Approach**: **Refactor First, Then Integrate**

**Phase 1: Refactoring (Recommended First)**:
1. Extract `AggressiveLimitHedgeStrategy` to general execution layer:
   - Create `strategies/execution/core/aggressive_limit_executor.py` (or `strategies/execution/strategies/aggressive_limit.py`)
   - Move core logic from `AggressiveLimitHedgeStrategy` (retries, partial fills, market fallback)
   - Make it generic (not hedge-specific) - accept `exchange_client`, `symbol`, `side`, `quantity` instead of `OrderContext`
   - Rename to `AggressiveLimitExecutor` or `SmartLimitExecutor`
   - This becomes a reusable execution component, similar to `OrderExecutor`
2. Extract price calculation logic:
   - Create `strategies/execution/core/aggressive_limit_pricer.py`
   - Move from `hedge_pricer.py`
   - General-purpose price calculation inside spread
3. Update hedge manager to use refactored version:
   - Hedge manager becomes thin wrapper around general executor
   - Maintains backward compatibility
   - Uses `AggressiveLimitExecutor` internally

**Benefits of Refactoring**:
- **Reusability**: Same executor for position opening, closing, hedging, any execution
- **Consistency**: All execution uses same smart limit order logic
- **Maintainability**: Fix bugs/improvements once, benefits all scenarios
- **Testability**: Test once, use everywhere
- **Better Architecture**: Follows separation of concerns (execution logic separate from strategy-specific logic)

**Phase 2: Integration**:
1. Add spread validation before closing:
   - Fetch BBO prices for each exchange leg
   - Calculate spread: `spread_pct = (ask - bid) / mid_price`
   - If spread > `max_exit_spread_pct`, use `AggressiveLimitExecutor` instead of simple market/limit

2. Wide spread handling logic (using refactored executor):
   ```python
   # In order_builder.py or close_executor.py
   bid, ask = await price_provider.get_bbo_prices(client, symbol)
   mid_price = (bid + ask) / 2
   spread_pct = (ask - bid) / mid_price
   
   if spread_pct > max_exit_spread_pct:
       if wide_spread_exit_strategy == "defer":
           # Skip closing this iteration, log warning
           logger.warning(f"Wide spread on {exchange}: {spread_pct*100:.2f}%, deferring close")
           return None  # Skip this close
       elif wide_spread_exit_strategy == "market":
           # Use market orders (only if critical)
           execution_mode = "market_only"
       elif wide_spread_exit_strategy == "limit_favorable_side":
           # Place limit on favorable side (bid for sells, ask for buys)
           # This won't fill immediately but avoids terrible execution
       elif wide_spread_exit_strategy == "limit_inside_spread":
           # Use AggressiveLimitExecutor (refactored from hedge manager)
           from strategies.execution.core.aggressive_limit_executor import AggressiveLimitExecutor
           
           executor = AggressiveLimitExecutor(price_provider=price_provider)
           result = await executor.execute(
               exchange_client=client,
               symbol=symbol,
               side=side,
               quantity=quantity,
               reduce_only=True,
               max_retries=5,
               total_timeout_seconds=3.0,
               inside_tick_retries=2,
               logger=logger,
           )
           # Executor handles: inside spread pricing, retries, partial fills, market fallback
   ```

3. **Emergency Close Specific Handling** (Using Refactored Aggressive Limit Executor):
   ```python
   # In close_executor.py._force_close_leg()
   # After refactoring, use the general-purpose executor:
   from strategies.execution.core.aggressive_limit_executor import AggressiveLimitExecutor
   
   # Check spread first
   bid, ask = await price_provider.get_bbo_prices(client, symbol)
   spread_pct = (ask - bid) / ((bid + ask) / 2)
   
   if spread_pct > max_exit_spread_pct:
       # Wide spread detected - use aggressive limit executor
       executor = AggressiveLimitExecutor(price_provider=price_provider)
       
       result = await executor.execute(
           exchange_client=client,
           symbol=symbol,
           side=leg["side"],
           quantity=leg["quantity"],
           reduce_only=True,  # Closing operation
           max_retries=5,  # Fewer retries for emergency
           total_timeout_seconds=3.0,  # Shorter timeout
           inside_tick_retries=2,  # Start inside spread
           logger=logger,
       )
       
       if result.success:
           # Successfully closed using aggressive limit
           return
       else:
           # Executor already tried market fallback, but we can handle error
           raise RuntimeError(f"Emergency close failed: {result.error_message}")
   ```

   **Key Insight**: The hedge manager's `AggressiveLimitHedgeStrategy` already solves the exact problem, but it should be refactored to be general-purpose:
   - Places orders **inside spread** (1 tick inside) to avoid post-only violations
   - Retries with fresh BBO on violations
   - Handles partial fills gracefully
   - Falls back to market if needed
   - Has adaptive pricing (inside → touch → market)
   
   **Refactoring Benefits**:
   - **Reusable**: Same executor for opening, closing, hedging
   - **Consistent**: All execution uses same smart logic
   - **Maintainable**: Fix bugs once, benefits all scenarios
   - **Testable**: Test once, use everywhere
   
   **Implementation Approach**:
   1. **First**: Refactor `AggressiveLimitHedgeStrategy` → `AggressiveLimitExecutor` in `strategies/execution/core/`
   2. **Then**: Use it in emergency closes, position opening, and anywhere else needing smart limit orders
   3. Check spread before attempting close
   4. If spread wide, use aggressive limit executor instead of simple market → limit fallback
   5. This ensures maximum fill probability even with wide spreads

3. Add config:
   - `max_exit_spread_pct`: Decimal (default: 0.01 = 1%)
   - `wide_spread_exit_strategy`: str ("defer", "market", "limit_favorable_side")
   - `enable_wide_spread_protection`: bool (default: True)

4. Critical exit override:
   - Liquidation risk exits should still proceed even with wide spread
   - May use market orders for critical exits if spread is too wide

**Files**:

**Phase 1 (Refactoring - Recommended First)**:
- `strategies/execution/core/aggressive_limit_executor.py` (NEW) - General-purpose aggressive limit executor
  - Extract core logic from `AggressiveLimitHedgeStrategy`
  - Make it generic (accept `exchange_client`, `symbol`, `side`, `quantity` instead of `OrderContext`)
  - Reusable for opening, closing, hedging, any execution scenario
- `strategies/execution/core/aggressive_limit_pricer.py` (NEW) - Price calculation inside spread
  - Extract from `hedge_pricer.py`
  - General-purpose price calculation logic
- `strategies/execution/patterns/atomic_multi_order/components/hedge/strategies.py` - Update to use refactored executor
  - Hedge manager becomes thin wrapper around general executor
- `strategies/execution/__init__.py` - Export new `AggressiveLimitExecutor`

**Phase 2 (Integration)**:
- `operations/closing/order_builder.py` - Add spread check before building order spec
- `operations/closing/close_executor.py` - Validate spread before executing close, use `AggressiveLimitExecutor`
- `operations/opening/execution_engine.py` - Consider using `AggressiveLimitExecutor` for position opening (optional improvement)
  - Currently uses `atomic_multi_order` executor which has its own logic
  - Could enhance with `AggressiveLimitExecutor` for better fill rates on wide spreads
  - Similar to how hedge manager uses it - ensures orders fill even with poor liquidity
- `config.py` - Add spread protection config
- `config_builder/schema.py` - Add config builder prompts

**Usage Examples After Refactoring**:
```python
# Position Opening (funding arb)
executor = AggressiveLimitExecutor(price_provider=price_provider)
result = await executor.execute(
    exchange_client=long_client,
    symbol=symbol,
    side="buy",
    quantity=long_quantity,
    reduce_only=False,  # Opening position
    max_retries=8,  # More retries for opening
    total_timeout_seconds=6.0,
    logger=logger,
)

# Position Closing (emergency close)
executor = AggressiveLimitExecutor(price_provider=price_provider)
result = await executor.execute(
    exchange_client=client,
    symbol=symbol,
    side="buy",
    quantity=quantity,
    reduce_only=True,  # Closing position
    max_retries=5,  # Fewer retries for closing
    total_timeout_seconds=3.0,
    logger=logger,
)

# Hedging (existing use case)
# Hedge manager would use the same executor internally
```

## Configuration Changes

### TODO Configurations

```python
# Exit polling (Issue 1)
enable_exit_polling: bool = Field(
    default=True,
    description="Enable exit polling to wait for profitable exit"
)
exit_polling_interval_seconds: int = Field(
    default=15,
    description="Interval between price checks during exit polling"
)
exit_polling_max_duration_minutes: int = Field(
    default=30,
    description="Maximum time to wait for profitable exit"
)
exit_order_type: str = Field(
    default="limit",
    description="Order type for exit (limit or aggressive_limit)"
)

# Profit taking (Issue 2)
enable_profit_taking: bool = Field(
    default=True,
    description="Enable profit-taking on favorable price divergence"
)
min_profit_taking_pct: Decimal = Field(
    default=Decimal("0.01"),  # 1%
    description="Minimum profit percentage to trigger profit-taking"
)
max_price_deviation_for_hold_pct: Decimal = Field(
    default=Decimal("0.05"),  # 5%
    description="Maximum unfavorable price deviation to hold position"
)

# Wide spread protection (Issue 3)
max_exit_spread_pct: Decimal = Field(
    default=Decimal("0.01"),  # 1%
    description="Maximum spread percentage allowed before deferring exit"
)
wide_spread_exit_strategy: str = Field(
    default="limit_inside_spread",
    description="Strategy when spread is too wide: 'defer', 'market', 'limit_favorable_side', 'limit_inside_spread'"
)
enable_wide_spread_protection: bool = Field(
    default=True,
    description="Enable spread validation before closing positions"
)
emergency_close_wide_spread_strategy: str = Field(
    default="limit_inside_spread",
    description="Strategy for emergency closes when spread is wide: 'limit_inside_spread', 'retry_with_backoff', 'split_order'"
)
max_emergency_close_spread_pct: Decimal = Field(
    default=Decimal("0.03"),  # 3% - higher tolerance for emergency closes
    description="Maximum spread percentage for emergency closes before using alternative strategy"
)
```

## Testing

1. **Wide Spread Protection**:
   - Test with exchanges that have wide spreads (e.g., Paradex with >2% spread)
   - Verify closing is deferred when spread exceeds threshold
   - Test that critical exits (liquidation) still proceed despite wide spread
   - Verify logging shows spread details when deferring
   - Test different exit strategies (defer vs market vs limit_favorable_side)

2. **Exit Polling**:
   - Test exit polling activation
   - Test break-even price calculation
   - Test limit order placement
   - Test timeout fallback

3. **Profit Taking**:
   - Test profit-taking trigger conditions
   - Test hold logic for unfavorable deviations
   - Verify PnL calculations are correct

## Related Files

- `strategies/implementations/funding_arbitrage/operations/opening/execution_engine.py`
- `strategies/implementations/funding_arbitrage/operations/opening/position_opener.py`
- `strategies/implementations/funding_arbitrage/operations/opportunity_scanner.py`
- `strategies/implementations/funding_arbitrage/operations/closing/exit_evaluator.py`
- `strategies/implementations/funding_arbitrage/operations/closing/position_closer.py`
- `strategies/implementations/funding_arbitrage/operations/closing/close_executor.py`
- `strategies/implementations/funding_arbitrage/config.py`
- `strategies/execution/core/price_alignment.py`

## Priority

1. **High**: Issue 3 (Wide Spread Protection) - Prevents immediate slippage losses
2. **High**: Issue 1 (Delayed Exit) - Improves exit profitability
3. **Medium**: Issue 2 (Profit Taking) - Captures additional profits

## Notes

- Issue 1 (Delayed Exit) is the most complex and will require careful state management
- Consider adding position metadata fields to track exit polling state
- Exit polling should respect existing risk management (e.g., liquidation risk)
- Profit-taking should not interfere with normal exit conditions (erosion, funding flip, etc.)

## Expansion and Future Improvements

These improvements can be expanded upon, and new improvements should be added wherever it makes sense. Consider:

- **Price convergence analysis**: Track historical price convergence patterns to improve exit timing
- **Dynamic spread thresholds**: Adjust spread thresholds based on market volatility
- **Multi-leg profit optimization**: Optimize exit timing across multiple positions simultaneously
- **Slippage prediction**: Use historical data to predict slippage and adjust order types accordingly
- **Market condition awareness**: Adapt exit strategies based on overall market conditions (volatile vs stable)


