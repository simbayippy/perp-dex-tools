# DEX Adapters

This directory contains DEX-specific adapters for fetching funding rates.

## Overview

Each adapter extends `BaseDEXAdapter` and implements:
- `fetch_funding_rates()` - Fetch all funding rates for the DEX
- `normalize_symbol()` - Convert DEX-specific symbol format to standard
- `get_dex_symbol_format()` - Convert standard format back to DEX-specific

## Implemented Adapters

### ✅ Lighter (`lighter_adapter.py`)
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

### ✅ GRVT (`grvt_adapter.py`)
- **SDK**: `grvt-pysdk` (CCXT-compatible SDK)
- **API Method**: `GrvtCcxt.fetch_markets()` + `fetch_ticker()`
- **Symbol Format**: `BTC_USDT_Perp`, `ETH_USDT_Perp`
- **Normalized**: `BTC`, `ETH`
- **Notes**: 
  - No authentication required for public data
  - Requires two-step process:
    1. `fetch_markets()` to get all perpetuals
    2. `fetch_ticker(symbol)` for each market to get funding rate
  - Funding rate in `funding_rate_curr` field (scaled by 1,000,000)
  - Only fetches USDT perpetuals

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
from collection.adapters import LighterAdapter, ParadexAdapter, GrvtAdapter

# Initialize adapters
adapters = [
    LighterAdapter(),
    ParadexAdapter(),
    GrvtAdapter()
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

1. **Create new file**: `{dex_name}_adapter.py`

2. **Extend BaseDEXAdapter**:
```python
from collection.base_adapter import BaseDEXAdapter

class NewDEXAdapter(BaseDEXAdapter):
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

3. **Add to `__init__.py`**:
```python
from collection.adapters.newdex_adapter import NewDEXAdapter

__all__ = [..., "NewDEXAdapter"]
```

4. **Update seed data**: Add to `scripts/seed_dexes.py`

5. **Test**: Create standalone test and integration test

See `ADDING_EXCHANGES.md` in the root docs for more details.

## Performance

Typical latencies (production):
- **Lighter**: ~100-300ms (single API call)
- **Paradex**: ~200-400ms (single API call)
- **GRVT**: ~2-5s (N+1 API calls, N=number of markets)

**Note**: GRVT is slower due to requiring individual ticker fetches per market.

## Error Handling

All adapters include:
- ✅ Retry logic (3 attempts)
- ✅ Timeout handling (10s default)
- ✅ Graceful degradation (skip failed symbols)
- ✅ Detailed logging
- ✅ Metrics tracking (success/failure counts)

## Dependencies

```python
# requirements.txt
git+https://github.com/elliottech/lighter-python.git@...
paradex-py>=0.1.0
grvt-pysdk
```

## Future Additions

Planned adapters:
- [ ] EdgeX
- [ ] Hyperliquid
- [ ] Vertex
- [ ] Orderly Network

