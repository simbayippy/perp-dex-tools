# API Endpoints

**Base URL:** `http://localhost:8000/api/v1`

## Funding Rates

### `GET /funding-rates`
Get latest funding rates across all DEXs and symbols.
- `?dex=<name>` - Filter by DEX
- `?symbol=<symbol>` - Filter by symbol
- `?include_metadata=true` - Include DEX metadata

**Response:**
```json
{
  "data": {
    "BTC": {
      "hyperliquid": 0.0001,
      "dydx": 0.00015
    },
    "ETH": {
      "hyperliquid": 0.00008,
      "dydx": 0.00012
    }
    ...
  },
  "updated_at": "2024-10-06T12:00:00",
  "count": 4
}
```
### `GET /funding-rates/{dex}`
Get all funding rates for a specific DEX.

**Response:**
```json
{
  "dex_name": "lighter",
  "rates": {
    "BTC": {
      "funding_rate": 0.0001,
      "annualized_rate": 10.95,
      "next_funding_time": "2024-10-06T16:00:00", // nullable
      "timestamp": "2024-10-06T12:00:00"
    },
    "ETH": {
      "funding_rate": 0.00008,
      "annualized_rate": 8.76,
      "next_funding_time": "2024-10-06T16:00:00", // nullable
      "timestamp": "2024-10-06T12:00:00"
    }
  },
  "updated_at": "2024-10-06T12:00:00"
}
```

### `GET /funding-rates/{dex}/{symbol}`
Get funding rate for a specific DEX and symbol pair.

**Response:**
```json
{
  "dex_name": "lighter",
  "symbol": "BTC",
  "funding_rate": 0.0001,
  "annualized_rate": 10.95,
  "next_funding_time": "2024-10-06T16:00:00", // nullable
  "timestamp": "2024-10-06T12:00:00",
  "volume_24h": 1500000.0, // nullable
  "open_interest_usd": 5000000.0 // nullable
}
```

### `GET /funding-rates/compare`
Compare current funding rates between two DEXs for a specific symbol. Perfect for position monitoring - quickly see rate divergence between your positions on different exchanges.

Query Parameters:
- `symbol` (required) - Symbol to compare (e.g., BTC)
- `dex1` (required) - First DEX name
- `dex2` (required) - Second DEX name

**Response:**
```json
{
  "symbol": "BTC",
  "dex1": {
    "name": "lighter",
    "funding_rate": 0.0001,
    "next_funding_time": "2025-10-07T16:00:00Z",
    "timestamp": "2025-10-07T15:55:32Z"
  },
  "dex2": {
    "name": "hyperliquid",
    "funding_rate": 0.0008,
    "next_funding_time": "2025-10-07T16:00:00Z",
    "timestamp": "2025-10-07T15:55:30Z"
  },
  "divergence": 0.0007,
  "divergence_bps": 7.0,
  "long_recommendation": "lighter",
  "short_recommendation": "hyperliquid",
  "estimated_net_profit_8h": 0.0007,
  "timestamp": "2025-10-07T15:55:32Z"
}
```

### `GET /history/funding-rates/{dex}/{symbol}`
Get historical funding rates.
- `?period=7d` - Period: 7d, 30d, 90d (default: 7d)
- `?limit=1000` - Max data points (default: 1000, max: 10000)

**Response:**
```json
{
  "symbol": "BTC",
  "dex_name": "hyperliquid",
  "period_days": 7,
  "data_points": [
    {
      "time": "2024-10-06T08:00:00",
      "funding_rate": 0.0001,
      "annualized_rate": 10.95
    },
    {
      "time": "2024-10-06T00:00:00",
      "funding_rate": 0.00012,
      "annualized_rate": 13.14
    }
  ],
  "count": 2,
  "stats": {
    "average": 0.00011,
    "median": 0.00011,
    "min": 0.0001,
    "max": 0.00012,
    "volatility": 0.00001
  }
}
```

### `GET /stats/funding-rates/{symbol}`
Get statistical analysis of funding rates (average, median, volatility, percentiles, APY).
- `?dex=<name>` - Specific DEX or all DEXs
- `?period=30d` - Analysis period (default: 30d)

**Response:**
```json
{
  "symbol": "BTC",
  "dex_name": "hyperliquid",
  "period_days": 30,
  "statistics": {
    "average": 0.00011,
    "median": 0.0001,
    "min": 0.00005,
    "max": 0.0002,
    "volatility": 0.00003,
    "percentile_25": 0.00008,
    "percentile_75": 0.00014,
    "annualized_apy": 12.045
  },
  "sample_size": 90
}
```

## Opportunities

**⚠️ Important: About Spread Costs**

The opportunities endpoint provides estimated profitability based on:
- ✅ Funding rate divergence
- ✅ Maker/taker fees (from DEX fee structures)
- ❌ **NOT including real-time spread costs**

**Why?** Spreads change every second and would be stale immediately. For accurate execution:
1. Use this API for **opportunity discovery** (filtering by volume/OI/profitability)
2. Fetch **real-time spread** from your trading client before executing
3. Recalculate final profitability: `actual_profit = api_profit - (spread × 4 crossings)`

**Example:** An opportunity showing 0.04% profit with 0.02% spread costs = Actually 0% profit!

---

### `GET /opportunities`
Get arbitrage opportunities with comprehensive filtering.

**Symbol Filter:**
- `?symbol=<symbol>` - Filter by symbol (e.g., BTC)

**DEX Filters (position-agnostic):**
- `?dex=<dex>` - Show opportunities involving this DEX (long or short side)
- `?dex_pair=<dex1,dex2>` - Show only opportunities between these two DEXs (any direction)
- `?dexes=<dex1,dex2,dex3>` - Show opportunities involving ANY of these DEXs
- `?whitelist_dexes=<dex1,dex2>` - Only show opportunities where BOTH sides are from this list
- `?exclude_dexes=<dex1,dex2>` - Exclude opportunities involving any of these DEXs

**Profitability Filters:**
- `?min_divergence=0.0001` - Minimum divergence (default: 0.0001, i.e., 0.01%)
- `?min_profit=0` - Minimum net profit percent (default: 0)

**Volume Filters:**
- `?min_volume=<amount>` - Minimum 24h volume USD (default: none)
- `?max_volume=<amount>` - Maximum 24h volume USD

**Open Interest Filters:**
- `?min_oi=<amount>` - Minimum open interest USD
- `?max_oi=<amount>` - Maximum open interest USD (for low OI farming)
- `?oi_ratio_min=<ratio>` - Minimum OI ratio (long/short)
- `?oi_ratio_max=<ratio>` - Maximum OI ratio (long/short)

**Liquidity Filters:**
- `?max_spread=<bps>` - Maximum spread in basis points

**Sorting & Pagination:**
- `?limit=10` - Number of results (default: 10, max: 100)
- `?sort_by=net_profit_percent` - Sort field (default: net_profit_percent)
- `?sort_desc=true` - Sort descending (default: true)

**Common Use Cases:**
```bash
# All Lighter opportunities
?dex=lighter

# Only Lighter vs GRVT
?dex_pair=lighter,grvt

# Any of these dexes
?dexes=lighter,grvt,edgex

# Only trades between trusted DEXs
?whitelist_dexes=lighter,grvt,hyperliquid

# Anything except EdgeX
?exclude_dexes=edgex

# BTC opportunities on Lighter but not against EdgeX
?symbol=BTC&dex=lighter&exclude_dexes=edgex
```

**Response:**
```json
{
  "opportunities": [
    {
      "symbol": "BTC",
      "long_dex": "hyperliquid",
      "short_dex": "dydx",
      "long_rate": 0.0001,
      "short_rate": 0.00025,
      "divergence": 0.00015,
      "estimated_fees": 0.00006,
      "net_profit_percent": 0.00009,
      "annualized_apy": 9.855,
      "long_dex_volume_24h": 1500000.0, // nullable - volume on the long DEX
      "short_dex_volume_24h": 2000000.0, // nullable - volume on the short DEX
      "min_volume_24h": 1500000.0, // nullable
      "long_dex_oi_usd": 5000000.0, // nullable - OI on the long DEX
      "short_dex_oi_usd": 6000000.0, // nullable - OI on the short DEX
      "min_oi_usd": 5000000.0, // nullable
      "max_oi_usd": 6000000.0, // nullable
      "oi_ratio": 0.833, // nullable - long_dex_oi / short_dex_oi
      "oi_imbalance": "short_heavy", // nullable
      "long_dex_spread_bps": 5, // nullable - spread on the long DEX
      "short_dex_spread_bps": 4, // nullable - spread on the short DEX
      "avg_spread_bps": 4.5, // nullable
      "discovered_at": "2024-10-06T12:00:00"
    }
  ],
  "total_count": 1,
  "filters_applied": {
    "symbol": null,
    "min_divergence": 0.0001,
    "min_profit_percent": 0.0,
    "min_volume_24h": 100000.0,
    "max_oi_usd": null,
    "limit": 10
  },
  "generated_at": "2024-10-06T12:00:00"
}
```

### `GET /opportunities/best`
Get the single best opportunity (highest net profit).
- `?symbol=<symbol>` - Filter by symbol
- `?include_dexes=<dex1,dex2>` - Include only these DEXs
- `?exclude_dexes=<dex1,dex2>` - Exclude these DEXs
- `?min_profit=0` - Minimum net profit percent
- `?max_oi=<amount>` - Maximum open interest (for low OI farming)

**Response:**
```json
{
  "opportunity": {
    "symbol": "BTC",
    "long_dex": "hyperliquid",
    "short_dex": "dydx",
    "long_rate": 0.0001,
    "short_rate": 0.00025,
    "divergence": 0.00015,
    "estimated_fees": 0.00006,
    "net_profit_percent": 0.00009,
    "annualized_apy": 9.855,
    "min_volume_24h": 1500000.0, // nullable
    "min_oi_usd": 5000000.0, // nullable
    "oi_imbalance": "short_heavy", // nullable
    "discovered_at": "2024-10-06T12:00:00"
  },
  "rank": 1,
  "generated_at": "2024-10-06T12:00:00"
}
```

### `GET /opportunities/symbol/{symbol}`
Get opportunities for a specific symbol. **Note:** This endpoint is functional, but consider using the base `/opportunities` endpoint with `?symbol=<symbol>` filter instead for consistency.
- `?min_profit=0` - Minimum net profit percent
- `?limit=10` - Number of results (default: 10, max: 100)

**Response:**
```json
{
  "symbol": "BTC",
  "opportunities": [
    {
      "long_dex": "hyperliquid",
      "short_dex": "dydx",
      "long_rate": 0.0001,
      "short_rate": 0.00025,
      "divergence": 0.00015,
      "net_profit_percent": 0.00009,
      "annualized_apy": 9.855,
      "min_oi_usd": 5000000.0
    }
  ],
  "count": 1,
  "generated_at": "2024-10-06T12:00:00"
}
```


## DEXes

### `GET /dexes`
Get all DEX metadata (fees, health status, supported symbols).

**Response:**
```json
{
  "dexes": [
    {
      "name": "hyperliquid",
      "display_name": "Hyperliquid",
      "is_active": true,
      "fee_structure": {
        "maker_fee_percent": 0.0002,
        "taker_fee_percent": 0.0005,
        "has_fee_tiers": true
      },
      "supported_symbols_count": 25,
      "last_successful_fetch": "2024-10-06T12:00:00",
      "last_error": null,
      "consecutive_errors": 0,
      "is_healthy": true
    }
  ],
  "count": 1
}
```

### `GET /dexes/{dex}`
Get metadata for a specific DEX.

**Response:**
```json
{
  "name": "hyperliquid",
  "display_name": "Hyperliquid",
  "api_base_url": "https://api.hyperliquid.xyz",
  "is_active": true,
  "supports_websocket": true,
  "fee_structure": {
    "maker_fee_percent": 0.0002,
    "taker_fee_percent": 0.0005,
    "has_fee_tiers": true
  },
  "collection_interval_seconds": 30,
  "rate_limit_per_minute": 60,
  "supported_symbols_count": 25,
  "health": {
    "last_successful_fetch": "2024-10-06T12:00:00",
    "last_error": null,
    "consecutive_errors": 0,
    "is_healthy": true
  },
  "created_at": "2024-10-01T00:00:00",
  "updated_at": "2024-10-06T12:00:00"
}
```

### `GET /dexes/{dex}/symbols`
Get all symbols supported by a DEX (includes volume, OI, spreads).

**Response:**
```json
{
  "dex_name": "hyperliquid",
  "symbols": [
    {
      "symbol": "BTC",
      "dex_symbol_format": "BTC-USD",
      "is_active": true,
      "min_order_size": 0.001,
      "volume_24h": 1500000.0,
      "open_interest_usd": 5000000.0,
      "spread_bps": 5,
      "last_updated": "2024-10-06T12:00:00"
    }
  ],
  "count": 1
}
```

## Health

### `GET /health`
Comprehensive service health status (DEX health, data freshness, database).

**Response:**
```json
{
  "status": "healthy",
  "timestamp": "2024-10-06T12:00:00",
  "database": {
    "connected": true
  },
  "dex_health": [
    {
      "dex_name": "hyperliquid",
      "is_healthy": true,
      "is_active": true,
      "last_successful_fetch": "2024-10-06T12:00:00",
      "time_since_fetch_seconds": 30,
      "consecutive_errors": 0,
      "last_error": null,
      "active_symbols": 25
    }
  ],
  "dex_summary": {
    "total": 5,
    "active": 5,
    "healthy": 5
  },
  "data_freshness": {
    "oldest_data_age_seconds": 120,
    "latest_update": "2024-10-06T12:00:00",
    "total_rates": 125
  }
}
```

### `GET /health/simple`
Simple health check (no database queries).

**Response:**
```json
{
  "status": "ok",
  "timestamp": "2024-10-06T12:00:00"
}
```

### `GET /health/database`
Database connectivity and statistics.

**Response:**
```json
{
  "status": "healthy",
  "connected": true,
  "statistics": {
    "symbols": 50,
    "dexes": 5,
    "latest_rates": 125,
    "rates_last_24h": 3600
  },
  "timestamp": "2024-10-06T12:00:00"
}
```

### `GET /health/dex/{dex}`
Health status for a specific DEX.

**Response:**
```json
{
  "dex_name": "hyperliquid",
  "status": "healthy",
  "is_active": true,
  "last_successful_fetch": "2024-10-06T12:00:00",
  "time_since_fetch_seconds": 30,
  "consecutive_errors": 0,
  "last_error": null,
  "active_symbols": 25,
  "recent_updates": 25,
  "timestamp": "2024-10-06T12:00:00"
}
```

## Utility

### `GET /`
Service information and documentation links.

**Response:**
```json
{
  "service": "Funding Rate Service",
  "version": "1.0.0",
  "status": "running",
  "docs": "/api/v1/docs"
}
```

### `GET /ping`
Simple ping endpoint.

**Response:**
```json
{
  "status": "ok"
}
```
