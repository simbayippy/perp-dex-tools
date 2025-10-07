# DEX Adapters

This directory contains DEX-specific adapters for fetching funding rates.

## Overview

**Note:** All adapters have been migrated to the shared `exchange_clients` library.

Each adapter extends `BaseFundingAdapter` (from `exchange_clients.base`) and implements:
- `fetch_funding_rates()` - Fetch all funding rates for the DEX
- `fetch_market_data()` - Fetch volume and open interest data
- `normalize_symbol()` - Convert DEX-specific symbol format to standard
- `get_dex_symbol_format()` - Convert standard format back to DEX-specific

## Implemented Adapters

All adapters are now in `exchange_clients/` (shared library):

### ✅ Lighter (`exchange_clients/lighter/funding_adapter.py`)
- **SDK**: `lighter-python` (official Python SDK)
- **API Method**: `FundingApi.funding_rates()`
- **Symbol Format**: `BTC-PERP`, `ETH-PERP`, `1000PEPE-PERP`
- **Normalized**: `BTC`, `ETH`, `PEPE`
- **Notes**: 
  - Handles multipliers (1000PEPE → PEPE)
  - Returns funding rates directly

### ✅ Paradex (`paradex_adapter.py`)
- **SDK**: `paradex-py` (official Python SDK)
- **API Method**: `ParadexApiClient.fetch_markets_summary()`
- **Symbol Format**: `BTC-USD-PERP`, `ETH-USD-PERP`
- **Normalized**: `BTC`, `ETH`
- **Notes**: 
  - No authentication required for public data
  - Returns market summaries with `funding_rate` field
  - Filters perpetual markets by `-USD-PERP` suffix

### ✅ GRVT (`exchange_clients/grvt/funding_adapter.py`)
- **SDK**: `grvt-pysdk` (CCXT-compatible SDK)
- **API Method**: `GrvtCcxt.fetch_markets()` + `fetch_ticker()` (parallel)
- **Symbol Format**: `BTC_USDT_Perp`, `ETH_USDT_Perp`
- **Normalized**: `BTC`, `ETH`
- **Notes**: 
  - No authentication required for public data
  - Requires two-step process:
    1. `fetch_markets()` to get all perpetuals
    2. `fetch_ticker(symbol)` for each market to get funding rate
  - **Uses parallel fetching** with configurable concurrency limit (default: 10)
  - Funding rate in `funding_rate_8h_curr` field (percentage format)
  - Only fetches USDT perpetuals
  - Configurable: `GrvtAdapter(max_concurrent_requests=20)` for faster fetching

## SDK Verification

All adapters have been verified against their official SDKs:
- ✅ Paradex: 100% match with `paradex-py` SDK
- ✅ GRVT: Verified with `grvt-pysdk` (fixed to use `fetch_ticker`)
- ✅ Lighter: Verified with `lighter-python` SDK

See `SDK_VERIFICATION.md` for detailed analysis.

## Usage

### Standalone Testing

Each adapter can be tested independently:

```bash
# Test specific adapter
python -m funding_rate_service.collection.adapters.lighter_adapter
python -m funding_rate_service.collection.adapters.paradex_adapter
python -m funding_rate_service.collection.adapters.grvt_adapter

# Test all adapters
python scripts/test_all_adapters.py

# Test specific adapter
python scripts/test_all_adapters.py --adapter lighter
```

### Integration with Orchestrator

```python
from collection.orchestrator import CollectionOrchestrator
from exchange_clients.lighter import LighterFundingAdapter
from exchange_clients.grvt import GrvtFundingAdapter
from exchange_clients.edgex import EdgeXFundingAdapter

# Initialize adapters
adapters = [
    LighterFundingAdapter(),
    GrvtFundingAdapter(),
    EdgeXFundingAdapter()
]

# Create orchestrator
orchestrator = CollectionOrchestrator(
    dex_repository=dex_repo,
    symbol_repository=symbol_repo,
    funding_rate_repository=fr_repo,
    collection_log_repository=cl_repo,
    dex_mapper=dex_mapper,
    symbol_mapper=symbol_mapper,
    adapters=adapters,
    logger=logger
)

# Collect from all DEXs in parallel
rates = await orchestrator.collect_all_rates()
```

## Return Format

All adapters return:
```python
Dict[str, Decimal]
{
    "BTC": Decimal("0.0001"),
    "ETH": Decimal("0.00008"),
    "SOL": Decimal("0.00015"),
    ...
}
```

Where:
- **Key**: Normalized symbol (e.g., "BTC")
- **Value**: Funding rate as Decimal (8-hour rate, not annualized)

## Symbol Normalization

All adapters normalize symbols to a common format:

| DEX | Format | Example | Normalized |
|-----|--------|---------|------------|
| **Lighter** | `{SYMBOL}-PERP` | `BTC-PERP` | `BTC` |
| **Paradex** | `{SYMBOL}-USD-PERP` | `BTC-USD-PERP` | `BTC` |
| **GRVT** | `{BASE}_USDT_Perp` | `BTC_USDT_Perp` | `BTC` |

**Special cases**:
- Multipliers: `1000PEPE-PERP` → `PEPE`
- Underscores/dashes removed: `BTC-PERP`, `BTC_PERP` → `BTC`

## Adding New Adapters

To add a new DEX adapter:

1. **Create new directory**: `exchange_clients/{dex_name}/`

2. **Create funding adapter**: `exchange_clients/{dex_name}/funding_adapter.py`

3. **Extend BaseFundingAdapter**:
```python
from exchange_clients.base import BaseFundingAdapter

class NewDEXFundingAdapter(BaseFundingAdapter):
    def __init__(self, ...):
        super().__init__(dex_name="newdex", api_base_url="...", ...)
    
    async def fetch_funding_rates(self) -> Dict[str, Decimal]:
        # Implement fetching logic
        pass
    
    def normalize_symbol(self, dex_symbol: str) -> str:
        # Convert DEX format to standard
        pass
    
    def get_dex_symbol_format(self, normalized_symbol: str) -> str:
        # Convert standard back to DEX format
        pass
```

4. **Create `__init__.py`**:
```python
from .funding_adapter import NewDEXFundingAdapter

__all__ = ['NewDEXFundingAdapter']
```

5. **Update seed data**: Add to `scripts/seed_dexes.py`

6. **Test**: Create standalone test and integration test

7. **Optional**: Create trading client at `exchange_clients/{dex_name}/client.py`

See `ADDING_EXCHANGES.md` in the root docs for more details.

## Performance

Typical latencies (production):
- **Lighter**: ~100-300ms (single API call)
- **Paradex**: ~200-400ms (single API call)
- **GRVT**: ~1-3s (parallel fetching with 10 concurrent requests, ~60 markets)

### GRVT Performance Tuning

The GRVT adapter uses parallel fetching with a configurable concurrency limit:

```python
# Default (balanced - 10 concurrent requests)
adapter = GrvtAdapter()

# Faster (20 concurrent requests - more aggressive)
adapter = GrvtAdapter(max_concurrent_requests=20)

# Gentler (5 concurrent requests - less load on API/system)
adapter = GrvtAdapter(max_concurrent_requests=5)
```

**Performance comparison** (60 markets):
- Sequential: ~16-20s
- 5 concurrent: ~4-6s
- 10 concurrent: ~1-3s ⭐ (recommended)
- 20 concurrent: ~0.8-2s (may hit rate limits)

## Error Handling

All adapters include:
- ✅ Retry logic (3 attempts)
- ✅ Timeout handling (10s default)
- ✅ Graceful degradation (skip failed symbols)
- ✅ Detailed logging
- ✅ Metrics tracking (success/failure counts)

## Dependencies

All exchange SDKs are now managed in `exchange_clients/pyproject.toml`:

```toml
[project.optional-dependencies]
lighter = ["lighter-sdk @ git+...", "eth-account>=0.8.0"]
grvt = ["grvt-pysdk"]
edgex = ["edgex-python-sdk @ git+...", "httpx>=0.24.0"]
all = [...]  # All exchange deps
```

Install with:
```bash
pip install -e './exchange_clients[all]'
```

### ✅ EdgeX (`exchange_clients/edgex/funding_adapter.py`)
- **SDK**: Forked `edgex-python-sdk` (for trading client only)
- **API Method**: Public HTTP endpoints (funding adapter uses direct HTTP)
- **Symbol Format**: `BTCUSDT`, `ETHUSDT`, `1000PEPEUSDT`
- **Normalized**: `BTC`, `ETH`, `PEPE`
- **Notes**:
  - Funding adapter uses HTTP-only (no SDK required)
  - Trading client uses forked SDK with post_only support
  - Handles multiplier prefixes (1000PEPE → PEPE)

## Future Additions

Planned adapters:
- [ ] Hyperliquid
- [ ] Vertex
- [ ] Orderly Network
- [ ] Paradex (dependency issues resolved)

