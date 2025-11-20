# Price Entry/Exit Improvements

## Description

Multiple improvements to price validation and exit logic to improve profitability by avoiding bad entries and optimizing exits.

## Completion Status

- ✅ **Issue 1**: Price Divergence Validation on Entry - **COMPLETED**
- ✅ **Issue 2**: Wide Spread Cooldown Mechanism - **COMPLETED**
- ⏳ **Issue 3**: Delayed Exit Until Profitable - **TODO**
- ⏳ **Issue 4**: Take Profit on Price Divergence Opportunities - **TODO**
- ✅ **Issue 5**: Liquidation Prevention - **COMPLETED**

## Issues

### 1. Price Divergence Validation on Entry ✅ COMPLETED

**Problem**: Currently, the system uses `max_spread_threshold_pct` (default 0.5%) to decide whether to use break-even price alignment, but it still allows positions to open even if prices diverge significantly between exchanges.

**Current Behavior**:
- If spread > 0.5%, falls back to BBO-based pricing (long_ask, short_bid)
- Still opens position even if prices are drastically different

**Requested**: Don't open positions if prices between exchanges vary drastically (e.g., > 1-2%)

**Impact**: Prevents entering positions where price divergence could lead to immediate unrealized losses

**Implementation Status**: ✅ **COMPLETED**

**Key Implementation Details**:
- Created `EntryValidator` class in `operations/opening/entry_validator.py` with `validate_price_divergence()` method
- Validates BBO prices before entry: calculates mid prices `(bid + ask) / 2` and checks divergence `(max_mid - min_mid) / min_mid`
- Integrated into `execution_engine.py` after fetching BBO prices
- Config: `max_entry_price_divergence_pct` (default: 0.01 = 1%)
- When validation fails, symbol is marked for cooldown and added to `failed_symbols`
- Price divergence is also displayed in position opened notifications

**Files Modified**:
- `strategies/implementations/funding_arbitrage/operations/opening/entry_validator.py` (NEW)
- `strategies/implementations/funding_arbitrage/operations/opening/execution_engine.py`
- `strategies/implementations/funding_arbitrage/config.py`
- `strategies/implementations/funding_arbitrage/config_builder/schema.py`
- `strategies/implementations/funding_arbitrage/utils/notification_service.py`

### 2. Wide Spread Cooldown Mechanism ✅ COMPLETED

**Problem**: If a coin consistently has wide spreads, the system keeps trying to trade it on every scan, wasting resources.

**Requested**: 
- Skip coins with wide spreads
- Implement a cooldown mechanism (don't skip forever, but temporarily)
- After cooldown expires, try again

**Use Case**: Some coins may have temporary wide spreads due to low liquidity or exchange issues, but should be retried later

**Implementation Status**: ✅ **COMPLETED**

**Key Implementation Details**:
- Created `CooldownManager` class in `operations/cooldown_manager.py` to manage symbol cooldowns
- Tracks cooldown state: `{symbol: timestamp}` dictionary
- Cooldown is triggered when:
  - Price divergence validation fails
  - Wide spread detected (`bbo_fallback` strategy used or spread exceeds threshold)
- Cooldown check happens in `opportunity_scanner.py` before processing each opportunity
- Config: `wide_spread_cooldown_minutes` (default: 60 minutes)
- Cooldown manager is initialized in `strategy.py` and accessible via `strategy.cooldown_manager`
- Automatic cleanup of expired cooldowns on each check

**Files Modified**:
- `strategies/implementations/funding_arbitrage/operations/cooldown_manager.py` (NEW)
- `strategies/implementations/funding_arbitrage/operations/opportunity_scanner.py`
- `strategies/implementations/funding_arbitrage/operations/opening/execution_engine.py`
- `strategies/implementations/funding_arbitrage/strategy.py`
- `strategies/implementations/funding_arbitrage/config.py`
- `strategies/implementations/funding_arbitrage/config_builder/schema.py`

### 3. Delayed Exit Until Profitable (TODO)

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

### 4. Take Profit on Price Divergence Opportunities (TODO)

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

## Implementation Plan

### Issue 1: Price Divergence Validation ✅ COMPLETED

**Location**: `strategies/implementations/funding_arbitrage/operations/opening/execution_engine.py`

**Implementation**:
1. ✅ Created `EntryValidator` class with `validate_price_divergence()` static method
2. ✅ Calculates mid prices: `long_mid = (long_bid + long_ask) / 2`, `short_mid = (short_bid + short_ask) / 2`
3. ✅ Calculates divergence: `divergence_pct = (max_mid - min_mid) / min_mid`
4. ✅ Validates before order plan preparation in `execution_engine.py`
5. ✅ If validation fails, marks symbol for cooldown and adds to `failed_symbols`
6. ✅ Config: `max_entry_price_divergence_pct` (default: 0.01 = 1%)
7. ✅ Added to config builder schema with helpful prompts

**Files Modified**:
- ✅ `operations/opening/entry_validator.py` (NEW) - Validation logic
- ✅ `operations/opening/execution_engine.py` - Integration
- ✅ `config.py` - Config parameter
- ✅ `config_builder/schema.py` - Config builder
- ✅ `utils/notification_service.py` - Display price divergence in notifications

### Issue 2: Wide Spread Cooldown ✅ COMPLETED

**Location**: `strategies/implementations/funding_arbitrage/operations/cooldown_manager.py`

**Implementation**:
1. ✅ Created `CooldownManager` class with in-memory tracking: `{symbol: timestamp}`
2. ✅ Cooldown triggered when:
   - Price divergence validation fails
   - Wide spread detected (BBO fallback or spread > threshold)
3. ✅ Cooldown check in `opportunity_scanner.py` before processing opportunities
4. ✅ Automatic cleanup of expired cooldowns
5. ✅ Config: `wide_spread_cooldown_minutes` (default: 60 minutes)

**Files Modified**:
- ✅ `operations/cooldown_manager.py` (NEW) - Cooldown management
- ✅ `operations/opportunity_scanner.py` - Cooldown check integration
- ✅ `operations/opening/execution_engine.py` - Cooldown marking
- ✅ `strategy.py` - Initialize cooldown manager
- ✅ `config.py` - Config parameter
- ✅ `config_builder/schema.py` - Config builder

### Issue 3: Delayed Exit Until Profitable

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

### Issue 4: Take Profit on Divergence

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

## Configuration Changes

### ✅ Implemented Configurations

```python
# Entry validation (Issue 1)
max_entry_price_divergence_pct: Decimal = Field(
    default=Decimal("0.01"),  # 1% (changed from original 2% plan)
    description="Maximum price divergence between exchanges to allow entry"
)

# Cooldown (Issue 2)
wide_spread_cooldown_minutes: int = Field(
    default=60,
    description="Cooldown period for symbols with wide spreads"
)

# Liquidation prevention (Issue 5)
enable_liquidation_prevention: bool = Field(
    default=True,
    description="Enable proactive liquidation prevention"
)
min_liquidation_distance_pct: Decimal = Field(
    default=Decimal("0.10"),  # 10% (changed from original 5% plan)
    description="Minimum distance to liquidation before forced close"
)
```

### ⏳ TODO Configurations (Issues 3 & 4)

```python
# Exit polling (Issue 3)
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

# Profit taking (Issue 4)
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
```

## Testing

1. **Price Divergence Validation**:
   - Test with coins that have >2% price divergence
   - Verify positions are not opened
   - Verify logging is clear

2. **Wide Spread Cooldown**:
   - Test cooldown activation
   - Test cooldown expiration
   - Verify symbol is retried after cooldown

3. **Exit Polling**:
   - Test exit polling activation
   - Test break-even price calculation
   - Test limit order placement
   - Test timeout fallback

4. **Profit Taking**:
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

1. **High**: Issue 1 (Price Divergence Validation) - Prevents bad entries
2. **High**: Issue 3 (Delayed Exit) - Improves exit profitability
3. **Medium**: Issue 4 (Profit Taking) - Captures additional profits
4. **Low**: Issue 2 (Cooldown) - Optimization, less critical

## Notes

- Issue 3 (Delayed Exit) is the most complex and will require careful state management
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

## Liquidation Prevention ✅ COMPLETED

### Problem

Liquidation fees can be significant and should be avoided. If we know and store liquidation prices, we should monitor them and close positions proactively before liquidation occurs.

### Solution

Add liquidation monitoring and prevention logic:

1. **Monitor Liquidation Distance**:
   - Track liquidation price for each leg (already fetched in `position_monitor.py`)
   - Calculate distance to liquidation as percentage
   - Set a safety threshold (e.g., close when within 5-10% of liquidation)

2. **Liquidation Prevention Logic**:
   ```python
   # In exit_evaluator.py or position_closer.py
   def check_liquidation_risk(position, snapshots):
       for dex, snapshot in snapshots.items():
           if not snapshot or not snapshot.liquidation_price:
               continue
           
           mark_price = snapshot.mark_price
           liquidation_price = snapshot.liquidation_price
           side = snapshot.side or ("long" if snapshot.quantity > 0 else "short")
           
           # Calculate distance to liquidation
           if side == "long":
               # Long: liquidation_price < mark_price
               distance_pct = ((mark_price - liquidation_price) / mark_price * 100) if mark_price > 0 else None
           else:  # short
               # Short: liquidation_price > mark_price
               distance_pct = ((liquidation_price - mark_price) / mark_price * 100) if mark_price > 0 else None
           
           # Check if too close to liquidation
           min_liquidation_distance_pct = 5.0  # Configurable threshold
           if distance_pct is not None and distance_pct < min_liquidation_distance_pct:
               return True, f"LIQUIDATION_RISK_{dex.upper()}"
       
       return False, None
   ```

3. **Priority**: Liquidation prevention should be HIGHEST priority exit condition (even before erosion, funding flip, etc.)

4. **Config Parameters**:
   ```python
   min_liquidation_distance_pct: Decimal = Field(
       default=Decimal("0.05"),  # 5%
       description="Minimum distance to liquidation before forced close"
   )
   enable_liquidation_prevention: bool = Field(
       default=True,
       description="Enable proactive liquidation prevention"
   )
   ```

5. **Implementation Location**:
   - Add to `exit_evaluator.py` as highest priority check
   - Check before all other exit conditions
   - Use market orders for liquidation prevention (speed is critical)

**Implementation Status**: ✅ **COMPLETED**

**Key Implementation Details**:
- Implemented `check_liquidation_risk()` method in `exit_evaluator.py`
- Calculates liquidation distance: `(mark_price - liquidation_price) / mark_price` for longs, `(liquidation_price - mark_price) / mark_price` for shorts
- Checked FIRST in `position_closer.evaluateAndClosePositions()` (highest priority)
- Uses LIMIT orders (not market) for liquidation prevention to avoid slippage (per user preference)
- Config: `enable_liquidation_prevention` (default: True), `min_liquidation_distance_pct` (default: 0.10 = 10%)
- Pre-flight check added in `PreFlightChecker` to prevent opening positions that would immediately trigger liquidation risk
- Telegram notifications sent when liquidation risk detected (pre-flight) or when position closed due to liquidation risk
- Notification includes: exchange name, distance percentage, threshold, mark price, liquidation price

**Files Modified**:
- `strategies/implementations/funding_arbitrage/operations/closing/exit_evaluator.py`
- `strategies/implementations/funding_arbitrage/operations/closing/position_closer.py`
- `strategies/implementations/funding_arbitrage/operations/closing/order_builder.py`
- `strategies/implementations/funding_arbitrage/operations/closing/close_executor.py`
- `strategies/implementations/funding_arbitrage/config.py`
- `strategies/implementations/funding_arbitrage/config_builder/schema.py`
- `strategies/implementations/funding_arbitrage/utils/notification_service.py`
- `strategies/execution/patterns/atomic_multi_order/components/preflight_checker.py`
- `strategies/execution/patterns/atomic_multi_order/executor.py`
- `database/migrations/018_add_liquidation_risk_notification_type.sql` (NEW)
- `database/scripts/setup/seed_strategy_configs.py`

### Notes

- Liquidation price is dynamic and changes with margin usage, leverage, and price movements
- Must refresh liquidation price regularly via position monitoring
- Consider margin buffer: even if distance is > threshold, monitor closely if margin is getting tight
- Different exchanges may have different liquidation mechanisms - ensure compatibility
- **Important**: Pre-flight check prevents negative loop where low balance → high liquidation risk → open position → immediate close → lose on fees
- **Notification spam prevention**: Liquidation risk notifications are sent only once per (exchange, symbol) combination until risk is resolved

