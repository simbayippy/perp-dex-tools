# Funding Rate Interval Normalization Fix

**Date:** 2025-10-19
**Status:** ✅ COMPLETE (including symbol-level intervals)
**Severity:** CRITICAL BUG FIX

## Problem Statement

The system was comparing funding rates from different exchanges without accounting for their different payment intervals:

###Exchange-Level Intervals:
- **Lighter:** 1-hour funding intervals
- **All others (GRVT, EdgeX, Backpack, Paradex):** 8-hour funding intervals
- **Aster:** 8-hour default, BUT varies per symbol

### Symbol-Level Intervals (Aster):
- **INJUSDT:** 8 hours
- **ZORAUSDT:** 4 hours
- **Other symbols:** May vary

### Impact

This caused **8x miscalculation** for Lighter opportunities:

- A 0.01% rate on Lighter (per 1h) was compared directly to 0.01% on GRVT (per 8h)
- Lighter's actual 8h-equivalent rate is **0.08%** (8x higher!)
- Result: **Incorrect opportunity rankings, wrong APY calculations, and bad trading decisions**

### Example

```
BEFORE FIX:
Lighter:  0.01% per 1h  → System sees: 0.01%
GRVT:     0.02% per 8h  → System sees: 0.02%
Conclusion: GRVT is higher (WRONG!)

AFTER FIX:
Lighter:  0.01% per 1h  → Normalized: 0.08% per 8h
GRVT:     0.02% per 8h  → Normalized: 0.02% per 8h
Conclusion: Lighter is 4x higher (CORRECT!)
```

## Solution Overview

Implemented **HYBRID standardized 8-hour interval normalization** across the entire system:

### Exchange-Level (Most Exchanges):
1. Database stores default funding interval for each DEX (`dexes.funding_interval_hours`)
2. Base adapter provides normalization method
3. Exchange adapters normalize rates before returning
4. All calculations use normalized (8h) rates

### Symbol-Level (Aster & Others):
1. Database stores symbol-specific intervals (`dex_symbols.funding_interval_hours`)
2. Aster adapter fetches funding configs from `/fapi/v1/fundingInfo` endpoint
3. Per-symbol intervals stored in database during collection
4. Normalization uses symbol-specific interval when available, falls back to exchange default

### Priority: Symbol > Exchange > 8h Default

## Changes Made

### 1. Database Schema ✅

**File:** `funding_rate_service/database/migrations/006_add_funding_interval_hours.sql`

**Part 1: Exchange-Level Intervals**
- Added `funding_interval_hours` column to `dexes` table
- Default: 8 hours
- Lighter set to 1 hour, all others to 8 hours

**Part 2: Symbol-Level Intervals**
- Added `funding_interval_hours` column to `dex_symbols` table (NULL = use exchange default)
- Used by exchanges like Aster where intervals vary per symbol
- Example: Aster's INJUSDT=8h, ZORAUSDT=4h

**Part 3: Convenience View**
- Created `v_symbol_funding_intervals` view
- Provides effective interval per symbol-DEX pair
- COALESCE logic: symbol-specific > exchange default > 8h

### 2. Seed Data ✅

**File:** `funding_rate_service/scripts/seed_dexes.py`

- Added `funding_interval_hours` field to all DEX definitions
- Updated INSERT statement to include interval data

### 3. Base Adapter ✅

**File:** `exchange_clients/base.py`

- Added `funding_interval_hours` parameter to `__init__()`
- Implemented `normalize_funding_rate_to_8h()` method
- Formula: `rate_8h = rate_native * (8 / native_interval_hours)`

### 4. Exchange Adapters ✅

Updated all adapters to specify their funding interval:

- **Lighter** (`exchange_clients/lighter/funding_adapter.py`):
  - Set `funding_interval_hours=1`
  - Calls `normalize_funding_rate_to_8h()` in `fetch_funding_rates()`

- **Aster** (`exchange_clients/aster/funding_adapter.py`) - **SPECIAL HANDLING**:
  - Set `funding_interval_hours=8` (default)
  - Added `fetch_funding_interval_configs()` method
    - Fetches symbol-specific intervals from `/fapi/v1/fundingInfo`
    - Caches intervals for performance
    - Returns Dict[symbol, interval_hours]
  - Updated `fetch_funding_rates()`:
    - Fetches interval configs on first run
    - Uses symbol-specific interval for normalization
    - Falls back to exchange default (8h) if config unavailable
  - Added `get_symbol_intervals()` method for orchestrator access
  - Added `_normalize_rate()` helper for symbol-level normalization

- **GRVT** (`exchange_clients/grvt/funding_adapter.py`):
  - Set `funding_interval_hours=8`

- **EdgeX** (`exchange_clients/edgex/funding_adapter.py`):
  - Set `funding_interval_hours=8`

- **Backpack** (`exchange_clients/backpack/funding_adapter.py`):
  - Set `funding_interval_hours=8`

- **Paradex** (`exchange_clients/paradex/funding_adapter.py`):
  - Set `funding_interval_hours=8`

### 5. Fee Calculator ✅

**File:** `funding_rate_service/core/fee_calculator.py`

- Added documentation clarifying all rates are normalized to 8h
- No code changes needed (already assumes 8h)

### 6. Orchestrator ✅

**File:** `funding_rate_service/collection/orchestrator.py`

- Added `_store_symbol_intervals()` method
  - Stores symbol-specific intervals in `dex_symbols` table
  - Called after funding rate collection
  - Logs non-standard intervals for visibility
- Updated `_collect_from_adapter()`:
  - Calls `adapter.get_symbol_intervals()` after rate collection
  - Stores intervals in database for future reference
  - Non-critical operation (doesn't fail collection if it errors)

### 7. Tests ✅

**File:** `tests/test_funding_rate_normalization.py`

Comprehensive test suite covering:
- 1h rate normalization (multiply by 8)
- 8h rate unchanged (no-op)
- 4h rate normalization (multiply by 2) - **NEW**
- Negative rate handling
- Real-world opportunity comparison scenario
- Aster symbol-level interval handling - **NEW**
- Mixed interval comparison (1h vs 4h vs 8h) - **NEW**
- APY calculation accuracy
- Edge cases (zero, very small rates)

### 7. Documentation ✅

**File:** `CLAUDE.md`

- Added "Funding Rate Intervals" section in Important Conventions
- Added to Common Pitfalls list
- Included implementation examples

## How to Apply

### For Existing Databases

```bash
cd funding_rate_service

# Run the new migration
python scripts/run_migration.py database/migrations/006_add_funding_interval_hours.sql

# OR run all migrations
cd database/migrations
./RUN_ALL_MIGRATIONS.sh
```

### For New Installations

The migration is included in `RUN_ALL_MIGRATIONS.sh`, so it will run automatically.

## Verification

### Run Tests

```bash
# Run normalization tests
pytest tests/test_funding_rate_normalization.py -v

# All tests should pass
```

### Check Database

```sql
-- Verify funding_interval_hours column exists
SELECT name, funding_interval_hours FROM dexes;

-- Expected output:
-- lighter    | 1
-- grvt       | 8
-- edgex      | 8
-- backpack   | 8
-- aster      | 8
-- paradex    | 8
```

### Verify Adapter Behavior

```python
from exchange_clients.lighter.funding_adapter import LighterFundingAdapter
from decimal import Decimal

# Create adapter (1h interval)
adapter = LighterFundingAdapter()
assert adapter.funding_interval_hours == 1

# Test normalization
rate_1h = Decimal("0.0001")  # 0.01% per 1h
rate_8h = adapter.normalize_funding_rate_to_8h(rate_1h)

assert rate_8h == Decimal("0.0008")  # 0.08% per 8h
print("✅ Normalization working correctly!")
```

## Impact Assessment

### Before Fix

- ❌ Lighter opportunities undervalued by 8x
- ❌ APY calculations off by 8x for Lighter
- ❌ Wrong opportunity rankings
- ❌ Potentially missing profitable trades

### After Fix

- ✅ All rates normalized to common 8h standard
- ✅ Accurate opportunity comparison
- ✅ Correct APY calculations
- ✅ Fair profitability analysis

## Future Considerations

### Adding New Exchanges

When adding a new exchange:

1. **Determine native funding interval** (check exchange docs)
2. **Set in adapter `__init__()`:**
   ```python
   super().__init__(
       dex_name="new_exchange",
       funding_interval_hours=X  # 1, 4, 8, etc.
   )
   ```
3. **Normalize in `fetch_funding_rates()`:**
   ```python
   rate_native = Decimal(str(raw_rate))
   rate_8h = self.normalize_funding_rate_to_8h(rate_native)
   return {"BTC": rate_8h, ...}
   ```
4. **Update seed_dexes.py** with correct interval
5. **Add tests** for the new exchange

### Non-8-Hour Standards

If you need to normalize to a different standard (e.g., 1h or 24h):

1. Create a new method in `BaseFundingAdapter`:
   ```python
   def normalize_funding_rate_to_1h(self, rate: Decimal) -> Decimal:
       multiplier = Decimal('1') / Decimal(str(self.funding_interval_hours))
       return rate * multiplier
   ```

2. Update fee calculator if needed

## Testing Checklist

- [x] Migration runs successfully
- [x] Seed script includes intervals
- [x] All adapters specify their interval
- [x] Lighter adapter normalizes 1h → 8h
- [x] Unit tests pass
- [x] Documentation updated
- [x] No breaking changes to existing code

## Questions?

If you have questions about this fix or need to add support for different intervals, refer to:

- `CLAUDE.md` - Implementation guidelines
- `tests/test_funding_rate_normalization.py` - Examples and test cases
- `exchange_clients/base.py` - Normalization implementation

---

**Author:** Claude Code
**Reviewed by:** [Your name]
**Approved:** [Date]
