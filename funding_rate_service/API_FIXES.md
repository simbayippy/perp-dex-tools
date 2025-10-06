# API Fixes Applied

## Issues Fixed

### 1. ✅ Missing `/v1/` Prefix (404 Error)
**Problem:** `/api/dexes` returned 404  
**Solution:** Use the correct endpoint: `/api/v1/dexes`

**All endpoints require the `/api/v1/` prefix:**
- ❌ `/api/dexes`
- ✅ `/api/v1/dexes`

### 2. ✅ Global Variable Import Issue (NoneType Error - First Attempt)
**Problem:** `'NoneType' object has no attribute 'find_opportunities'`  
**Root Cause:** Python's `from module import variable` creates a **copy** at import time, not a reference

**First attempt (INCORRECT):**
- Changed: `from core.opportunity_finder import opportunity_finder`
- To: `from core import opportunity_finder as opp_finder_module` ❌
- This still imported the VARIABLE, not the MODULE!

**Second attempt (CORRECT):**
- Changed to: `import core.opportunity_finder as opp_finder_module` ✅
- This imports the MODULE itself, so `opp_finder_module.opportunity_finder` gets the actual global variable

### 3. ✅ SQLAlchemy Parameter Binding Error
**Problem:** `/api/v1/dexes/lighter` failed with: `sqlalchemy.sql.elements.TextClause.bindparams() argument after ** must be a mapping, not int`

**Root Cause:** The `databases` library expects named parameters with `values=` keyword argument

**What was wrong:**
```python
query = "SELECT * FROM dexes WHERE id = $1"
row = await database.fetch_one(query, dex_id)  # ❌ Passing positional arg
```

**Fixed to:**
```python
query = "SELECT * FROM dexes WHERE id = :dex_id"
row = await database.fetch_one(query, values={"dex_id": dex_id})  # ✅ Named params
```

## Files Modified
1. `api/routes/opportunities.py` - Fixed import to use `import` instead of `from`
2. `api/routes/funding_rates.py` - Fixed import + 3 query parameter bindings
3. `api/routes/dexes.py` - Fixed 2 query parameter bindings
4. `api/routes/health.py` - Fixed 1 query parameter binding

## Testing

### Correct API Endpoints (with `/v1/`)

```bash
# Health check
curl http://localhost:8000/api/v1/health

# Get all DEXes
curl http://localhost:8000/api/v1/dexes

# Get all funding rates
curl http://localhost:8000/api/v1/funding-rates

# Get opportunities
curl http://localhost:8000/api/v1/opportunities

# Get best opportunity
curl http://localhost:8000/api/v1/opportunities/best

# Find low OI opportunities (< $2M)
curl "http://localhost:8000/api/v1/opportunities?max_oi=2000000&limit=5"

# Get DEX metadata
curl http://localhost:8000/api/v1/dexes/lighter

# Get DEX symbols
curl http://localhost:8000/api/v1/dexes/lighter/symbols

# Get historical funding rates
curl http://localhost:8000/api/v1/history/funding-rates/lighter/BTC

# Get funding rate statistics
curl http://localhost:8000/api/v1/stats/funding-rates/BTC
```

### Quick Test After Restarting
```bash
# 1. Stop the server (Ctrl+C)

# 2. Restart the server
python main.py

# 3. Test health
curl http://localhost:8000/api/v1/health

# 4. Test opportunities (should work now!)
curl http://localhost:8000/api/v1/opportunities

# 5. Test DEXes
curl http://localhost:8000/api/v1/dexes
```

## What to Expect

**Successful Response Structure:**

```json
// GET /api/v1/opportunities
{
  "opportunities": [
    {
      "symbol": "BTC",
      "long_dex": "lighter",
      "short_dex": "grvt",
      "divergence": 0.0004,
      "net_profit_percent": 0.0001,
      "annualized_apy": 10.95,
      "min_oi_usd": 850000.0,
      ...
    }
  ],
  "total_count": 5,
  "filters_applied": {...},
  "generated_at": "2025-10-06T13:30:00Z"
}

// GET /api/v1/dexes
{
  "dexes": [
    {
      "name": "lighter",
      "display_name": "Lighter Network",
      "is_active": true,
      "fee_structure": {
        "maker_fee_percent": 0.0002,
        "taker_fee_percent": 0.0005
      },
      "is_healthy": true,
      ...
    }
  ],
  "count": 4
}
```

## API Documentation

Once the server is running, access:
- **Swagger UI**: http://localhost:8000/api/v1/docs
- **ReDoc**: http://localhost:8000/api/v1/redoc

These provide interactive API documentation where you can test all endpoints!

