# Complete Migration Commands for Funding Interval Fix

**Date:** 2025-10-19
**Migration:** 006_add_funding_interval_hours.sql

## Prerequisites

1. **PostgreSQL running:**
   ```bash
   # Check if PostgreSQL is running
   pg_isready

   # If using Docker:
   cd funding_rate_service
   docker-compose up -d
   ```

2. **Python environment activated:**
   ```bash
   cd /Users/yipsimba/perp-dex-tools
   source venv/bin/activate  # or your virtualenv path
   ```

3. **Dependencies installed:**
   ```bash
   pip install -r funding_rate_service/requirements.txt
   ```

## Option 1: Run All Migrations (RECOMMENDED)

This runs all migrations including the new funding interval migration:

```bash
cd /Users/yipsimba/perp-dex-tools/funding_rate_service

# Run all migrations
cd database/migrations
./RUN_ALL_MIGRATIONS.sh

# Expected output:
# Running all database migrations...
# ==================================
# ...
# Migration 006 completed successfully!
# ========================================
# Added funding interval support:
#   1. Exchange-level: dexes.funding_interval_hours
#      - Lighter: 1h
#      - Others: 8h (default)
#
#   2. Symbol-level: dex_symbols.funding_interval_hours
#      - NULL = use exchange default
#      - Non-NULL = override for specific symbols
#
#   3. View: v_symbol_funding_intervals
#      - Easy lookup of effective interval per symbol
# ========================================
```

## Option 2: Run Single Migration

Run only the funding interval migration:

```bash
cd /Users/yipsimba/perp-dex-tools/funding_rate_service

# Run migration 006
python scripts/run_migration.py database/migrations/006_add_funding_interval_hours.sql
```

## Verification Steps

### 1. Check Database Schema

```sql
-- Connect to your database
psql -U postgres -d your_database_name

-- Verify dexes table has funding_interval_hours
\d dexes
-- Should show: funding_interval_hours | integer | not null | default 8

-- Verify dex_symbols table has funding_interval_hours
\d dex_symbols
-- Should show: funding_interval_hours | integer |  |

-- Check the view exists
\dv v_symbol_funding_intervals
-- Should show: v_symbol_funding_intervals | view | ...
```

### 2. Check Funding Intervals

```sql
-- Check exchange-level intervals
SELECT name, funding_interval_hours FROM dexes ORDER BY name;

-- Expected output:
--  name        | funding_interval_hours
-- -------------+------------------------
--  aster       | 8
--  backpack    | 8
--  edgex       | 8
--  grvt        | 8
--  hyperliquid | 8
--  lighter     | 1
--  paradex     | 8
```

### 3. Test the View

```sql
-- Query the convenience view
SELECT * FROM v_symbol_funding_intervals
WHERE dex_name = 'lighter'
LIMIT 5;

-- Should show effective_interval_hours = 1 for Lighter symbols
```

### 4. Run Unit Tests

```bash
cd /Users/yipsimba/perp-dex-tools

# Run funding rate normalization tests
pytest tests/test_funding_rate_normalization.py -v

# Expected: All tests pass, including:
# ✓ test_1h_rate_normalization_to_8h
# ✓ test_8h_rate_normalization_unchanged
# ✓ test_4h_rate_normalization (new)
# ✓ test_aster_symbol_level_intervals (new)
# ✓ test_mixed_interval_comparison (new)
# ✓ test_negative_rate_normalization
# ✓ test_opportunity_comparison_scenario
# ✓ test_apy_calculation_with_normalization
# ✓ test_zero_rate_normalization
# ✓ test_very_small_rate_precision
```

### 5. Test Funding Rate Collection

```bash
cd /Users/yipsimba/perp-dex-tools/funding_rate_service

# Start the service
uvicorn main:app --reload

# In another terminal, trigger a collection
# (or wait for the scheduled collection to run)

# Check logs for:
# - "Lighter: Collected X rates in Yms"
# - "aster: Fetched funding intervals for X symbols (Y non-standard)"
# - No errors about missing columns
```

### 6. Verify Symbol-Level Intervals (Aster)

```sql
-- Check if Aster symbols have specific intervals
SELECT
    ds.symbol,
    ds.funding_interval_hours as symbol_specific,
    d.funding_interval_hours as exchange_default,
    COALESCE(ds.funding_interval_hours, d.funding_interval_hours) as effective
FROM dex_symbols ds
JOIN dexes d ON ds.dex_id = d.id
JOIN symbols s ON ds.symbol_id = s.id
WHERE d.name = 'aster'
  AND ds.funding_interval_hours IS NOT NULL
ORDER BY ds.funding_interval_hours, s.symbol;

-- Expected: Rows showing symbols with non-8h intervals
-- Example:
--  symbol | symbol_specific | exchange_default | effective
-- --------+-----------------+------------------+-----------
--  ZORA   | 4               | 8                | 4
--  INJ    | 8               | 8                | 8
```

## Rollback (If Needed)

If you need to rollback the migration:

```sql
-- Connect to database
psql -U postgres -d your_database_name

-- Drop the view
DROP VIEW IF EXISTS v_symbol_funding_intervals;

-- Remove columns
ALTER TABLE dex_symbols DROP COLUMN IF EXISTS funding_interval_hours;
ALTER TABLE dexes DROP COLUMN IF EXISTS funding_interval_hours;

-- Drop indexes
DROP INDEX IF EXISTS idx_dexes_funding_interval;
DROP INDEX IF EXISTS idx_dex_symbols_funding_interval;
```

## Troubleshooting

### Error: "column 'funding_interval_hours' does not exist"

**Cause:** Migration hasn't been run yet.

**Solution:** Run the migration using Option 1 or 2 above.

### Error: "relation 'v_symbol_funding_intervals' does not exist"

**Cause:** View wasn't created (migration partially failed).

**Solution:**
```bash
cd /Users/yipsimba/perp-dex-tools/funding_rate_service
python scripts/run_migration.py database/migrations/006_add_funding_interval_hours.sql
```

### Lighter rates look wrong (too low)

**Cause:** Rates aren't being normalized from 1h to 8h.

**Solution:**
1. Check that Lighter adapter is calling `normalize_funding_rate_to_8h()`
2. Restart the funding rate service
3. Force a new collection

### Aster symbol intervals not being stored

**Cause:** Aster SDK might not have `funding_info()` method.

**Solution:**
1. Check logs for: "funding_info() method not available in SDK"
2. Update aster-connector-python: `pip install --upgrade aster-connector-python`
3. If method doesn't exist, Aster will use default 8h (acceptable fallback)

## Post-Migration Actions

1. **Restart Services:**
   ```bash
   # Restart funding rate service
   cd funding_rate_service
   uvicorn main:app --reload

   # If running trading bot, restart it too
   cd ..
   # (restart your trading bot)
   ```

2. **Monitor Logs:**
   - Check for successful funding rate collections
   - Look for "non-standard" interval messages for Aster
   - Verify no errors about missing columns

3. **Update Documentation:**
   - Review `CLAUDE.md` for funding interval info
   - Share `FUNDING_INTERVAL_FIX.md` with team

## Success Criteria

✅ All migrations run without errors
✅ Both `dexes` and `dex_symbols` have `funding_interval_hours` columns
✅ Lighter set to 1h, others to 8h in dexes table
✅ View `v_symbol_funding_intervals` exists and works
✅ All unit tests pass
✅ Funding rate collection succeeds
✅ Aster symbols with non-standard intervals are stored correctly
✅ No errors in logs about missing columns

## Questions?

If you encounter issues:
1. Check PostgreSQL logs: `docker-compose logs postgres` (if using Docker)
2. Check application logs for detailed error messages
3. Verify database connection settings in `.env`
4. Ensure all dependencies are installed

---

**Migration created by:** Claude Code
**Date:** 2025-10-19
**Version:** 1.0 (including symbol-level intervals)
