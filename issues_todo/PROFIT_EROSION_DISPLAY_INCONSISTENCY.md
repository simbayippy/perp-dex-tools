# Profit Erosion Display Inconsistency

## Description

There is a discrepancy in how "profit erosion" is calculated and displayed between different parts of the system, leading to user confusion.

### Current Behavior

The system calculates erosion as:
```
erosion_ratio = current_divergence / entry_divergence
```

**Example**: If entry APY was 10% and current APY is 4%:
- `erosion_ratio = 4/10 = 0.4` (meaning 40% of original remains, 60% has eroded)

### The Problem

1. **Position Monitor/Closer**: Uses `erosion_ratio` directly (e.g., 0.4) and interprets it as "went from opening TO 40% OF initial opening"
2. **User/Telegram Display**: Users expect to see "40% profit erosion" meaning "40% has been lost" (60% remains)

This creates confusion because:
- When `erosion_ratio = 0.4`, the system shows "40% erosion" but actually means "60% erosion" (60% lost)
- Users expect "40% erosion" to mean "40% lost" (60% remains)

## Impact

- Users misinterpret position health
- Exit decisions based on erosion thresholds are confusing
- Telegram bot displays misleading erosion percentages

## Affected Files

- `strategies/implementations/funding_arbitrage/models.py` - `get_profit_erosion()` method
- `strategies/implementations/funding_arbitrage/position_monitor.py` - Erosion display logic
- `strategies/implementations/funding_arbitrage/risk_management/profit_erosion.py` - Erosion calculation
- `strategies/implementations/funding_arbitrage/risk_management/combined.py` - Erosion checks
- `telegram_bot_service/utils/formatters.py` - Position formatting (lines 89-104)
- `strategies/implementations/funding_arbitrage/operations/closing/exit_evaluator.py` - Exit evaluation

## Solution

### Option 1: Change Display Only (Recommended)
Keep the internal calculation as-is (`erosion_ratio = current/entry`), but change all displays to show:
```
erosion_percentage = (1 - erosion_ratio) * 100
```

So when `erosion_ratio = 0.4`:
- Display: "60% erosion" (60% lost, 40% remains)
- Internal: Still use `0.4` for threshold comparisons

### Option 2: Change Calculation
Change the calculation to:
```
erosion_ratio = 1 - (current_divergence / entry_divergence)
```

This would require updating all threshold comparisons (e.g., `min_erosion_ratio` would need to be inverted).

## Implementation Steps

1. **Audit all erosion displays**:
   - Position monitor output (`position_monitor.py`)
   - Telegram bot position command (`formatters.py`)
   - Log messages
   - Risk manager outputs
   - Config builder prompts (`config_builder/schema.py`)
   - Telegram bot `/create_config` wizard (`telegram_bot_service/handlers/configs.py`)

2. **Update display logic**:
   - Convert `erosion_ratio` to `erosion_percentage = (1 - ratio) * 100` for display
   - Update all formatters to show "X% erosion" correctly
   - Ensure consistency across all display locations

3. **Update config builder schema**:
   - Update `profit_erosion_threshold` help text in `config_builder/schema.py` to accurately describe:
     - Threshold is based on "remaining ratio" (e.g., 0.5 = 50% remains = 50% erosion)
     - Clarify that 0.5 means "exit when profit has eroded to 50% of entry" (i.e., 50% erosion)
   - Update prompt text to match the corrected interpretation

4. **Update Telegram bot config wizard**:
   - Ensure the `/create_config` command displays the same accurate help text
   - The wizard uses `param.help_text` from the schema (see `telegram_bot_service/handlers/configs.py` lines 1779-1780, 2256-2257)
   - Verify the help text is displayed correctly in the wizard prompts

5. **Update documentation**:
   - Clarify that thresholds are still based on "remaining ratio"
   - Add comments explaining the conversion
   - Document that display shows "erosion percentage" while internal logic uses "remaining ratio"

6. **Test**:
   - Verify displays show correct erosion percentages
   - Ensure threshold logic still works correctly
   - Check that exit conditions trigger at expected erosion levels
   - Test config builder prompts show accurate descriptions
   - Test Telegram bot wizard shows accurate help text

## Related Code

```python
# Current calculation (models.py)
def get_profit_erosion(self) -> Decimal:
    if not self.current_divergence or self.entry_divergence == 0:
        return Decimal("1.0")
    return self.current_divergence / self.entry_divergence

# Current display (formatters.py, line 93)
erosion_pct = (1.0 - erosion_ratio) * 100 if erosion_ratio <= 1.0 else 0.0
```

Note: The formatter already does the conversion correctly, but the issue is likely in other display locations or the interpretation of the threshold values.

