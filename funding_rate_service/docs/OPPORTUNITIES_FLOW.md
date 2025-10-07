# Opportunities Flow

## Overview

The opportunities system has **two modes** for finding arbitrage opportunities:

### 1. **Real-Time Mode** (Default - Currently Used)
- ✅ **Used when:** User queries `/opportunities` endpoint
- ✅ **How it works:** Calculates opportunities ON-THE-FLY from latest funding rates
- ✅ **Storage:** Opportunities are **NOT stored** in the database
- ✅ **Freshness:** Always real-time, never stale
- ✅ **Performance:** Fast (queries latest_funding_rates table + dex_symbols for market data)

### 2. **Batch Storage Mode** (Not Currently Implemented)
- ❌ **Used when:** Background job runs periodically
- ❌ **How it works:** Pre-calculates and stores opportunities in `opportunities` table
- ❌ **Storage:** Opportunities **ARE stored** in database
- ❌ **Freshness:** Depends on batch job frequency
- ❌ **Performance:** Faster API responses (just query stored data)

---

## Current Flow (Real-Time Mode)

```
User Request: GET /opportunities?symbol=BTC&dex=lighter
         ↓
api/routes/opportunities.py
         ↓
core/opportunity_finder.py::find_opportunities()
         ↓
1. Fetch latest funding rates from latest_funding_rates table
2. Fetch market data (volume, OI) from dex_symbols table
3. Calculate opportunities on-the-fly:
   - Find rate divergences
   - Calculate fees (from dexes table)
   - Calculate net profit
   - Apply filters
         ↓
Return ArbitrageOpportunity objects (NOT stored in DB)
         ↓
API serializes to JSON and returns to user
```

### Key Components:

**1. OpportunityFinder (`core/opportunity_finder.py`)**
- `find_opportunities()` - Main entry point
- `_fetch_latest_rates_with_market_data()` - Gets funding rates + volume/OI
- `_create_opportunity()` - Calculates profitability for each pair
- `_apply_filters()` - Applies user filters

**2. Data Sources:**
- `latest_funding_rates` table - Latest funding rate for each DEX+symbol
- `dex_symbols` table - Market data (volume_24h, open_interest_usd)
- `dexes` table - Fee structures (maker_fee, taker_fee)

**3. API Route (`api/routes/opportunities.py`)**
- `get_opportunities()` - Main endpoint handler
- `get_best_opportunity()` - Find single best opportunity

---

## Why NOT Store Opportunities Currently?

### Advantages of Real-Time Mode:
1. ✅ **Always Fresh** - No stale data
2. ✅ **Dynamic Filtering** - Users can apply any filter combination
3. ✅ **No Background Jobs** - Simpler architecture
4. ✅ **Market Data Integration** - Always uses latest volume/OI

### When to Use Batch Storage Mode:
1. High traffic API (thousands of requests/sec)
2. Historical opportunity tracking
3. ML/Analytics on past opportunities
4. Compliance/audit trail requirements

---

## Opportunities Table (Currently Unused)

The `opportunities` table exists for **future use** if you want to:
- Store historical opportunities
- Track which opportunities were actually traded
- Analyze opportunity patterns over time
- Pre-calculate opportunities for faster API responses

### To Enable Batch Storage:

Add a background job that runs periodically:
```python
# Example background job (not implemented yet)
async def store_opportunities_job():
    finder = OpportunityFinder(db)
    opportunities = await finder.find_opportunities(
        filters=OpportunityFilter()  # No filters = all opportunities
    )
    
    for opp in opportunities:
        await opportunity_repo.insert(
            symbol_id=symbol_mapper.get_id(opp.symbol),
            long_dex_id=dex_mapper.get_id(opp.long_dex),
            short_dex_id=dex_mapper.get_id(opp.short_dex),
            # ... all other fields
        )
```

---

## Summary

**Current Behavior:**
- `/opportunities` endpoint → Real-time calculation → Return JSON
- Opportunities **NOT** stored in database
- Always fresh, never stale
- Perfect for low-to-medium traffic

**Future Option:**
- Add background job to pre-calculate and store opportunities
- Query from `opportunities` table for faster responses
- Good for high traffic or historical analysis

