# Price Entry/Exit Improvements

## Description

Multiple improvements to price validation and exit logic to improve profitability by avoiding bad entries and optimizing exits.

## Issues

### 1. Price Divergence Validation on Entry

**Problem**: Currently, the system uses `max_spread_threshold_pct` (default 0.5%) to decide whether to use break-even price alignment, but it still allows positions to open even if prices diverge significantly between exchanges.

**Current Behavior**:
- If spread > 0.5%, falls back to BBO-based pricing (long_ask, short_bid)
- Still opens position even if prices are drastically different

**Requested**: Don't open positions if prices between exchanges vary drastically (e.g., > 1-2%)

**Impact**: Prevents entering positions where price divergence could lead to immediate unrealized losses

### 2. Wide Spread Cooldown Mechanism

**Problem**: If a coin consistently has wide spreads, the system keeps trying to trade it on every scan, wasting resources.

**Requested**: 
- Skip coins with wide spreads
- Implement a cooldown mechanism (don't skip forever, but temporarily)
- After cooldown expires, try again

**Use Case**: Some coins may have temporary wide spreads due to low liquidity or exchange issues, but should be retried later

### 3. Delayed Exit Until Profitable

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

### 4. Take Profit on Price Divergence Opportunities

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

## Implementation Plan

### Issue 1: Price Divergence Validation

**Location**: `strategies/implementations/funding_arbitrage/operations/opening/execution_engine.py`

**Changes**:
1. Add config parameter: `max_entry_price_divergence_pct` (default: 1.0% or 2.0%)
2. After fetching BBO prices, calculate price divergence:
   ```python
   long_mid = (long_bid + long_ask) / 2
   short_mid = (short_bid + short_ask) / 2
   divergence_pct = abs(long_mid - short_mid) / min(long_mid, short_mid)
   ```
3. If `divergence_pct > max_entry_price_divergence_pct`, skip the opportunity
4. Log reason for skipping

**Files**:
- `execution_engine.py` - Add validation
- `config.py` - Add `max_entry_price_divergence_pct` parameter
- `config_builder/schema.py` - Add config builder prompt

### Issue 2: Wide Spread Cooldown

**Location**: `strategies/implementations/funding_arbitrage/operations/opportunity_scanner.py`

**Changes**:
1. Add in-memory cooldown tracking: `{symbol: last_skip_time}`
2. When spread is too wide, mark symbol with current timestamp
3. Before processing opportunity, check if symbol is in cooldown
4. If in cooldown and cooldown period hasn't expired, skip
5. Add config: `wide_spread_cooldown_minutes` (default: 30-60 minutes)

**Files**:
- `opportunity_scanner.py` - Add cooldown logic
- `config.py` - Add cooldown config
- `strategy.py` - Initialize cooldown tracker

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

Add to `config.py`:
```python
# Entry validation
max_entry_price_divergence_pct: Decimal = Field(
    default=Decimal("0.02"),  # 2%
    description="Maximum price divergence between exchanges to allow entry"
)

# Cooldown
wide_spread_cooldown_minutes: int = Field(
    default=60,
    description="Cooldown period for symbols with wide spreads"
)

# Exit polling
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

# Profit taking
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

## Liquidation Prevention

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

### Affected Files

- `strategies/implementations/funding_arbitrage/operations/closing/exit_evaluator.py` - Add liquidation risk check
- `strategies/implementations/funding_arbitrage/operations/closing/position_closer.py` - Handle liquidation prevention exits
- `strategies/implementations/funding_arbitrage/config.py` - Add liquidation prevention config
- `strategies/implementations/funding_arbitrage/config_builder/schema.py` - Add config builder prompts

### Notes

- Liquidation price is dynamic and changes with margin usage, leverage, and price movements
- Must refresh liquidation price regularly via position monitoring
- Consider margin buffer: even if distance is > threshold, monitor closely if margin is getting tight
- Different exchanges may have different liquidation mechanisms - ensure compatibility

