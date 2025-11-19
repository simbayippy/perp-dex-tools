# Max Positions Config Exploration

## Description

Currently, the `max_positions` configuration is hardcoded to a maximum of 1 in the config builder, but the underlying config system supports higher values. This limits the ability to run multiple concurrent funding arbitrage positions.

## Current State

### Config Builder (`config_builder/schema.py`)
```python
ParameterSchema(
    key="max_positions",
    prompt="Maximum number of concurrent positions?",
    param_type=ParameterType.INTEGER,
    default=1,
    min_value=1,
    max_value=1,  # Hardcoded to 1
    required=False,
    help_text="Limit the number of open funding arb positions to manage risk. For now only 1 position is allowed",
)
```

### Config Model (`config.py`)
```python
max_positions: int = Field(
    default=10,
    description="Max concurrent positions"
)
```

### Usage (`opportunity_scanner.py`)
```python
if open_count >= strategy.config.max_positions:
    # Skip new opportunities
```

## Impact

- Users cannot configure multiple concurrent positions even if they want to
- Risk management is artificially limited
- Prevents scaling strategies across multiple opportunities

## Questions to Explore - ANSWERED

1. **Why was it limited to 1?** ✅ ANSWERED
   - **Answer**: It was mostly a temporary restriction to keep things simple
   - One thread spun up by Supervisor would just open 1 position
   - This kept `position_monitor.py` simpler compared to managing multiple positions
   - Also had some rate limit considerations
   - **Conclusion**: Not a hard technical limitation, more of a simplification choice

2. **What are the risks of allowing multiple positions?**
   - Capital allocation across positions
   - Exchange exposure limits
   - Margin requirements
   - Position monitoring overhead
   - Rate limiting concerns (as mentioned above)

3. **What safeguards are needed?**
   - Per-exchange exposure limits (already exists: `max_total_exposure_usd`)
   - Position size limits (already exists: `max_position_size_usd`)
   - Risk checks for each new position

## Investigation Tasks

1. **Review codebase for multi-position support**:
   - Check if `max_total_exposure_usd` properly handles multiple positions
   - Verify position monitoring can handle multiple positions
   - Check if exit logic works correctly with multiple positions

2. **Review historical decisions**:
   - Check git history for when this limit was added
   - Look for related issues or PRs
   - Check documentation for reasoning

3. **Test multi-position scenarios**:
   - Verify capacity checks work correctly
   - Test exposure calculations with multiple positions
   - Ensure position tracking doesn't conflict

## Proposed Solution

### Phase 1: Remove Hard Cap
- Change `max_value=1` to `max_value=10` (or higher) in config builder
- Update help text to explain multi-position considerations
- Add validation warnings for high values

### Phase 2: Add Safeguards (if needed)
- Ensure `max_total_exposure_usd` is properly enforced per exchange
- Add position count warnings in logs
- Consider adding a "max_positions_per_symbol" limit

### Phase 3: Testing
- Test with 2-3 concurrent positions
- Verify exposure limits are respected
- Test position closing doesn't interfere with each other

## Affected Files

- `strategies/implementations/funding_arbitrage/config_builder/schema.py` - Config builder limits
- `strategies/implementations/funding_arbitrage/config.py` - Config model
- `strategies/implementations/funding_arbitrage/operations/opportunity_scanner.py` - Capacity checks
- `strategies/implementations/funding_arbitrage/position_manager.py` - Position tracking

## Notes

The infrastructure appears to support multiple positions already:
- `max_total_exposure_usd` is calculated per-exchange
- Position manager tracks multiple positions
- Exit evaluator processes all positions independently

The main blocker is the config builder hard limit.

## Priority

**LOW PRIORITY** - This is a TODO item for future consideration. The current single-position approach works well and keeps the system simple. When implementing:
- Consider rate limiting implications
- Test position monitoring with multiple positions
- Ensure `position_monitor.py` can handle multiple positions efficiently

