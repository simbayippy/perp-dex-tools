# Symbol Normalization Issue: 2Z → Z

## 1. Description of Issue

### Problem
The trading bot is encountering errors when trying to trade opportunities for symbol "Z" on Lighter and Aster exchanges. The errors indicate that the symbol "Z" cannot be found in the exchange markets:

```
❌ [LIGHTER] Symbol 'Z' (looking for 'Z') NOT found in Lighter markets
[ASTER] Could not get account leverage for Z: API request failed: {'code': -1121, 'msg': 'Invalid symbol.'}
```

However, the exchanges actually list the symbol as **"2Z"**, not "Z". This mismatch causes:
- Failed market lookups
- Invalid symbol errors when querying leverage information
- Opportunities being created for non-existent symbols
- Repeated error logs on every scan cycle

### Impact
- Opportunities for "Z" are being created but cannot be executed
- Wasted API calls and processing time
- Error logs cluttering the output
- Potential for similar issues with other symbols that have numeric prefixes

## 2. Root Cause

### Symbol Normalization Logic

The issue originates from the `normalize_symbol()` functions in the exchange client common utilities:

**Files affected:**
- `exchange_clients/lighter/common.py` (lines 52-55)
- `exchange_clients/aster/common.py` (lines 45-48)

### The Problematic Code

```python
# Handle 1000-prefix multipliers (e.g., "1000PEPE" -> "PEPE", "1000TOSHI" -> "TOSHI")
match = re.match(r'^(\d+)([A-Z]+)$', normalized)
if match:
    _, symbol_part = match.groups()
    normalized = symbol_part
```

### Why It Fails

The regex pattern `r'^(\d+)([A-Z]+)$'` matches **any** numeric prefix followed by letters and strips it:

- ✅ **Intended behavior**: `"1000PEPE"` → `"PEPE"` (correct)
- ✅ **Intended behavior**: `"1000TOSHI"` → `"TOSHI"` (correct)
- ❌ **Unintended behavior**: `"2Z"` → `"Z"` (incorrect - strips the "2" prefix)

### Data Flow

1. **Funding Rate Collection**: 
   - Exchange lists symbol as `"2Z"`
   - Adapter calls `normalize_symbol("2Z")` → returns `"Z"`
   - Normalized symbol `"Z"` is stored in database

2. **Opportunity Discovery**:
   - Opportunity finder queries database and finds opportunities for `"Z"`
   - Creates `ArbitrageOpportunity` objects with `symbol="Z"`

3. **Trading Execution**:
   - Strategy tries to trade `"Z"` on exchanges
   - Exchange clients look up `"Z"` in market lists
   - Exchange only has `"2Z"`, not `"Z"` → **LOOKUP FAILS**

### Why This Happens

The normalization logic was designed to handle **1000-prefix multipliers** (like `1000PEPE`, `1000TOSHI`) which are common on exchanges for low-priced tokens. However, the regex pattern is too broad and strips **any** numeric prefix, including legitimate single-digit prefixes like `"2Z"`.

## 3. Solution and Fix

### Temporary Fix (Applied)

Added "Z" to the skip list in `opportunity_scanner.py` to prevent opportunities from being processed:

```python
# Temporary: Skip CC opportunities and Z (which is incorrectly normalized from 2Z)
opportunities = [opp for opp in opportunities if opp.symbol not in ["CC", "Z"]]
```

**Location**: `strategies/implementations/funding_arbitrage/operations/opportunity_scanner.py` (lines 57-68)

**Status**: ✅ Implemented

### Proper Fix (Recommended for Future)

The normalization logic should only strip **specific** numeric prefixes (like "1000"), not all numeric prefixes. Here are two approaches:

#### Option 1: Whitelist-Based Approach (Recommended)

Only strip numeric prefixes for known multiplier symbols:

```python
def normalize_symbol(symbol: str) -> str:
    normalized = symbol.upper()
    
    # Remove perpetual suffixes
    normalized = normalized.replace('-PERP', '')
    normalized = normalized.replace('-USD', '')
    normalized = normalized.replace('-USDC', '')
    normalized = normalized.replace('-USDT', '')
    normalized = normalized.replace('PERP', '')
    
    # Handle 1000-prefix multipliers ONLY for known symbols
    # Check if symbol starts with 1000 and the base symbol is in the whitelist
    match = re.match(r'^1000([A-Z]+)$', normalized)
    if match:
        base_symbol = match.group(1)
        if base_symbol in LIGHTER_1000_PREFIX_SYMBOLS:
            normalized = base_symbol
    
    normalized = normalized.strip('-_/')
    return normalized
```

#### Option 2: Specific Prefix Matching

Only match "1000" prefix specifically:

```python
# Handle ONLY 1000-prefix multipliers
match = re.match(r'^1000([A-Z]+)$', normalized)
if match:
    normalized = match.group(1)
```

### Files to Update

1. `exchange_clients/lighter/common.py` - Update `normalize_symbol()` function
2. `exchange_clients/aster/common.py` - Update `normalize_symbol()` function
3. Consider checking other exchange adapters for similar issues:
   - `exchange_clients/edgex/common.py`
   - `exchange_clients/backpack/common.py`
   - `exchange_clients/paradex/common.py`
   - `exchange_clients/grvt/common.py`

### Additional Considerations

1. **Database Cleanup**: After fixing normalization, may need to:
   - Identify symbols incorrectly normalized (like "Z" that should be "2Z")
   - Update database records or create migration script
   - Re-collect funding rates with correct normalization

2. **Testing**: Add test cases for:
   - `normalize_symbol("2Z")` should return `"2Z"` (not `"Z"`)
   - `normalize_symbol("1000PEPE")` should return `"PEPE"` (correct)
   - Other edge cases with numeric prefixes

3. **Symbol Validation**: Consider adding validation to detect when normalized symbols don't exist on exchanges and log warnings

### Related Issues

This same pattern could affect other symbols:
- Any symbol starting with a digit (e.g., `"3X"`, `"5Y"`, etc.)
- Symbols that legitimately have numeric prefixes that aren't "1000"

### Prevention

To prevent similar issues in the future:
1. Add unit tests for symbol normalization covering edge cases
2. Validate normalized symbols against exchange market lists during collection
3. Log warnings when normalization produces unexpected results
4. Consider maintaining a mapping of exchange-specific symbols to normalized symbols

