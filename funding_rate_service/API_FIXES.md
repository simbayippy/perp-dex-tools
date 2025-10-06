## Endpoint fixes

### FIXED ✅

**Issue:** The `/history`, `/stats`, and `/opportunities` endpoints were returning errors about `'NoneType' object has no attribute 'opportunity_finder'` or `'historical_analyzer'`.

**Root Cause:** The global instances `opportunity_finder` and `historical_analyzer` were not being properly initialized before the route handlers tried to access them.

**Solution Applied:**
1. Added initialization checks in all route handlers that use these instances
2. Added logging to confirm initialization
3. Return HTTP 503 (Service Unavailable) with a clear message if the service is still initializing
4. Improved error handling in routes

**Files Modified:**
- `main.py` - Cleaned up initialization calls
- `core/opportunity_finder.py` - Added initialization logging
- `core/historical_analyzer.py` - Added initialization logging  
- `api/routes/opportunities.py` - Added initialization checks to all endpoints
- `api/routes/funding_rates.py` - Added initialization checks to history and stats endpoints

**Action Required:** Restart the FastAPI service for changes to take effect.

---

### Original Error Reports

## Examples

1. /history/funding-rates/lighter/BTC

client side response:
```
{
  "detail": "'NoneType' object has no attribute 'historical_analyzer'"
}
```

server side logs:
```
2025-10-06 16:49:16 | ERROR    | api.routes.funding_rates:get_historical_funding_rates:283 - Error fetching historical rates: 'NoneType' object has no attribute 'historical_analyzer'
INFO:     127.0.0.1:34822 - "GET /api/v1/history/funding-rates/lighter/BTC HTTP/1.1" 500 Internal Server Error
```

2.  /stats/funding-rates/BTC

client side response:
```
{
  "detail": "'NoneType' object has no attribute 'historical_analyzer'"
}
```

server side logs:
```
2025-10-06 16:49:37 | ERROR    | api.routes.funding_rates:get_funding_rate_stats:314 - Error calculating stats: 'NoneType' object has no attribute 'historical_analyzer'
INFO:     127.0.0.1:43162 - `"GET /api/v1/stats/funding-rates/BTC HTTP/1.1" 500 Internal Server Error
```

3. /opportunities

client side response
```
{
  "detail": "'NoneType' object has no attribute 'opportunity_finder'"
}
```

server side logs
```
2025-10-06 16:50:58 | ERROR    | api.routes.opportunities:get_opportunities:133 - Error finding opportunities: 'NoneType' object has no attribute 'opportunity_finder'
INFO:     127.0.0.1:37520 - "GET /api/v1/opportunities HTTP/1.1" 500 Internal Server Error
```