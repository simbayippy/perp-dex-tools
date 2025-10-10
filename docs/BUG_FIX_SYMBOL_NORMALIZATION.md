# Bug Fix: Symbol Normalization in Order Placement

**Date**: 2025-10-10  
**Issue**: Aster orders failing with "not supported symbol" error  
**Root Cause**: Symbol normalization mismatch between initialization and order placement

---

## 🐛 Problem Summary

### Error Messages
```
# Error 1: Symbol normalization in order_executor.py
ERROR: Limit order execution failed: API request failed: {'code': -4095, 'msg': 'not supported symbol'}

# Error 2: Double normalization in aster/client.py
ERROR: [ASTER] Limit order execution failed for MON: API request failed: {'code': -1121, 'msg': 'Invalid symbol.'}
```

### What Happened
1. ✅ **Initialization worked**: Aster client initialized for symbol "MON" → `contract_id=MONUSDT`
2. ✅ **Liquidity check worked**: Used normalized symbol `MONUSDT` 
3. ❌ **Order placement failed (Error 1)**: OrderExecutor passed raw symbol `"MON"` instead of `"MONUSDT"`
4. ❌ **Order placement failed (Error 2)**: Aster client double-normalized `"MONUSDT"` → `"MONUSDTUSDT"`

### Root Cause

**Two separate bugs were discovered:**

#### Bug #1: OrderExecutor not using normalized symbol
`OrderExecutor` was passing the **raw symbol** (`"MON"`) directly to `place_limit_order()` and `place_market_order()`, but these methods expect the **exchange-normalized symbol** (`"MONUSDT"` for Aster).

**Why This Happened**:
- Each exchange has different symbol formats:
  - Lighter: `"MON"` (base asset only)
  - Aster: `"MONUSDT"` (base + quote asset)
  - Other DEXs: Various formats
- During initialization, `get_contract_attributes()` normalizes the symbol and stores it in `config.contract_id`
- But `OrderExecutor` wasn't using this normalized `contract_id` — it was using the raw `symbol` parameter

#### Bug #2: Double normalization in Aster client
After fixing Bug #1, `OrderExecutor` now correctly passes `contract_id="MONUSDT"` to Aster's `place_limit_order()`. However, Aster client was **normalizing again**:
- Received: `contract_id="MONUSDT"` (already normalized)
- Called: `normalize_symbol("MONUSDT")` 
- Result: `"MONUSDTUSDT"` ❌❌❌
- API Error: `-1121 'Invalid symbol.'`

**Why This Happened**:
- `place_limit_order()` and `place_market_order()` were calling `self.normalize_symbol(contract_id)` 
- But `contract_id` is already normalized from `get_contract_attributes()`
- `normalize_symbol()` adds "USDT" suffix, causing double normalization

---

## 🔧 Solution

### Changes Made

#### 1. **`order_executor.py`** - Use normalized `contract_id`

**Before** (❌ Bug):
```python
# Place limit order
order_result = await exchange_client.place_limit_order(
    contract_id=symbol,  # 🔥 Raw symbol "MON"
    quantity=float(quantity),
    price=float(limit_price),
    side=side
)
```

**After** (✅ Fixed):
```python
# Get the exchange-specific contract ID (normalized symbol)
contract_id = getattr(exchange_client.config, 'contract_id', symbol)

self.logger.info(
    f"Placing limit {side} {symbol} (contract_id={contract_id}): "
    f"{quantity} @ ${limit_price}"
)

# Place limit order using the normalized contract_id
order_result = await exchange_client.place_limit_order(
    contract_id=contract_id,  # ✅ Normalized "MONUSDT"
    quantity=float(quantity),
    price=float(limit_price),
    side=side
)
```

Same fix applied to `place_market_order()`.

#### 2. **`aster/client.py`** - Remove double normalization

**Before** (❌ Bug #2):
```python
async def place_limit_order(self, contract_id: str, ...):
    # Normalize symbol to Aster's format
    normalized_symbol = self.normalize_symbol(contract_id)  # 🔥 Double normalization!
    # "MONUSDT" → "MONUSDTUSDT"
    
    order_data = {
        'symbol': normalized_symbol,  # ❌ "MONUSDTUSDT"
        ...
    }
```

**After** (✅ Fixed):
```python
async def place_limit_order(self, contract_id: str, ...):
    # Contract ID should already be in Aster format (e.g., "MONUSDT")
    # from get_contract_attributes(), so use it directly
    # NO need to normalize again (would cause "MONUSDTUSDT")
    
    self.logger.log(
        f"🔍 [ASTER] Using contract_id for order: '{contract_id}'",
        "DEBUG"
    )
    
    order_data = {
        'symbol': contract_id,  # ✅ Already normalized "MONUSDT"
        ...
    }
```

Same fix applied to `place_market_order()`.

#### 3. **`aster/client.py`** - Better error messages

**Added**:
- Clearer distinction between "symbol not found" vs "symbol not tradeable"
- Explicit status checking (only `TRADING` status is allowed)
- Better error logging with symbol name included

```python
# Improved error message
if found_symbol:
    self.logger.log(
        f"Symbol {ticker}USDT exists on Aster but is not tradeable (status: {symbol_status})",
        "ERROR"
    )
    raise ValueError(
        f"Symbol {ticker}USDT is not tradeable on Aster (status: {symbol_status})"
    )
```

#### 3. **`funding_arbitrage/strategy.py`** - Better validation logging

**Added**:
- Clearer pre-trade validation messages
- Exchange name in error messages
- Better indication of which side (long/short) failed

```python
self.logger.log(
    f"📋 [VALIDATION] Checking if {symbol} is tradeable on both {long_dex} and {short_dex}...",
    "INFO"
)

if not long_init_ok:
    self.logger.log(
        f"⛔ [SKIP] Cannot trade {symbol}: Not supported on {long_dex.upper()} (long side)",
        "WARNING"
    )
    return
```

---

## 📊 Impact

### Before Fix
- Orders would fail with cryptic "not supported symbol" error
- Hard to identify which exchange was failing
- No indication that symbol normalization was the issue

### After Fix
- Orders use the correct normalized symbol for each exchange
- Clear logging shows: `Placing limit buy MON (contract_id=MONUSDT)`
- Better error messages identify the failing exchange
- Validation errors caught earlier with clearer messages

---

## 🧪 Testing

### Test Case 1: Aster Order Placement
```bash
python runbot.py --config configs/real_funding_test.yml
```

**Expected Result**:
- ✅ Initialization: `contract_id=MONUSDT` 
- ✅ Order placement: Uses `MONUSDT` (not `MON`)
- ✅ Clear logs: `Placing limit buy MON (contract_id=MONUSDT)`

### Test Case 2: Unsupported Symbol
If a symbol isn't tradeable on an exchange:

**Expected Logs**:
```
⚠️  [ASTER] Symbol MON is NOT TRADEABLE on aster (not listed or not supported)
⛔ [SKIP] Cannot trade MON: Not supported on ASTER (short side)
```

---

## 🎓 Key Learnings

### 1. **Symbol Normalization is Critical**
Every exchange has different symbol formats. Always use the exchange's normalized `contract_id`, not the raw symbol.

### 2. **Separation of Concerns**
- **Strategy layer**: Uses canonical symbols (e.g., "MON")
- **Exchange layer**: Handles normalization to exchange-specific format
- **Execution layer**: Uses `contract_id` from exchange config

### 3. **Error Messages Matter**
Good error messages should:
- Identify which component/exchange is failing
- Show both the input (raw symbol) and normalized symbol
- Distinguish between different failure modes

---

## 🔮 Future Improvements

1. **Symbol Registry**: Create a centralized symbol mapping system
2. **Validation**: Add a `validate_symbol()` method that checks if a symbol is tradeable before attempting initialization
3. **Testing**: Add unit tests for symbol normalization across all exchanges

---

## Related Files

- `strategies/execution/core/order_executor.py` - Order placement logic
- `strategies/implementations/funding_arbitrage/strategy.py` - Strategy validation
- `exchange_clients/aster/client.py` - Aster-specific normalization
- `exchange_clients/lighter/client.py` - Lighter-specific normalization

---

**Status**: ✅ Fixed and Ready for Testing

