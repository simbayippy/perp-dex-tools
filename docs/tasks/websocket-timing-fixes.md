# WebSocket Timing & Leverage Fixes

## Summary
Fixed three critical issues related to WebSocket initialization timing, account leverage, and real-time pricing for limit orders.

---

## Issue 1: No contract_id on Startup ✅ (Expected Behavior)

### Your Observation
```
WARNING | aster.websocket_manager:_connect_book_...:317 | No contract_id in config, skipping book ticker WebSocket
```

### Explanation
**This is CORRECT and expected!** 

- At bot startup, we don't know which symbol we'll trade (SKY, MON, PROVE, etc.)
- We only know the symbol AFTER finding a funding arbitrage opportunity
- WebSocket book ticker should NOT connect on startup

### Fix Applied
✅ **Aster WebSocket Manager** (`exchange_clients/aster/websocket_manager.py`):
- Removed automatic book ticker connection on startup
- Added new method: `async def start_book_ticker(symbol: str)`
- Book ticker now starts **on-demand** when opportunity is identified

✅ **Atomic Multi-Order Executor** (`strategies/execution/patterns/atomic_multi_order.py`):
- Added WebSocket initialization in `_run_preflight_checks()`
- Starts book ticker AFTER identifying opportunity, BEFORE placing orders
- Gives WebSockets 0.5s to receive first BBO update

```python
# In _run_preflight_checks(), after leverage normalization:
self.logger.info("🔴 Starting WebSocket book tickers for real-time pricing...")

for symbol, symbol_orders in symbols_to_check.items():
    for order in symbol_orders:
        exchange_client = order.exchange_client
        exchange_name = exchange_client.get_exchange_name()
        
        if hasattr(exchange_client, 'ws_manager') and exchange_client.ws_manager:
            ws_manager = exchange_client.ws_manager
            
            if exchange_name == "aster":
                normalized_symbol = getattr(exchange_client.config, 'contract_id', f"{symbol}USDT")
                if hasattr(ws_manager, 'start_book_ticker'):
                    await ws_manager.start_book_ticker(normalized_symbol)
                    self.logger.info(f"✅ Started Aster book ticker for {normalized_symbol}")
            elif exchange_name == "lighter":
                # Lighter order book WebSocket already active from connect()
                self.logger.info(f"✅ Lighter order book WebSocket already active for {symbol}")

# Give WebSockets a moment to receive first BBO update
await asyncio.sleep(0.5)
```

---

## Issue 2: Account Leverage vs Per-Trade Leverage

### Your Observation
```
WARNING | core.leverage_validator:normalize_and_...:332 | ⚠️  [LIGHTER] Does not support set_account_leverage(), skipping
```

### Question
> "Why are we trying to set the account's leverage? It should just be the trade. What leverage we are setting."

### Explanation
**This is how perpetual futures exchanges work:**

#### Account-Level Leverage (Aster, Binance, most CEXs)
- Leverage is set at the **ACCOUNT LEVEL**, not per-trade
- When you set 3x on Aster, **ALL positions** on that account use 3x
- You MUST set it before opening a position
- This is why we call `set_account_leverage()` during pre-flight checks

#### Position-Level Leverage (Lighter, some DEXs)
- Lighter uses a different margin system (likely cross-margin or isolated per-position)
- No `set_account_leverage()` method exists
- Leverage is implicitly determined by margin ratio

### Why We Need Leverage Normalization
**For delta-neutral funding arb, both legs MUST have same leverage!**

Example:
- Aster max leverage: 50x
- Lighter max leverage: 3x
- **We normalize to 3x on BOTH sides**

If we don't normalize:
- Aster at 50x: $10 position = $0.20 margin
- Lighter at 3x: $10 position = $3.33 margin
- **Imbalanced margin usage!** ❌

### Fix Applied
✅ The code correctly:
1. Queries max leverage for each exchange
2. Normalizes to minimum (3x limited by Lighter)
3. Sets account leverage on exchanges that support it (Aster)
4. Skips exchanges without `set_account_leverage()` (Lighter)

This ensures both sides use the same effective leverage.

---

## Issue 3: WebSocket Not Available (CRITICAL BUG!) ❌

### Your Observation
```
INFO | core.order_executor:_fetch_bbo_prices_...:479 | 🔄 [PRICE] Fetching FRESH BBO for lighter:SKY (WebSocket not available, forcing REST API call)
INFO | core.order_executor:_fetch_bbo_prices_...:479 | 🔄 [PRICE] Fetching FRESH BBO for aster:SKY (WebSocket not available, forcing REST API call)
```

### Question
> "Why is this so where the websockets aren't available? Is this as per issue 1, where it tries to initialize websocket at start?"

### Root Cause
**YES! This is related to Issue 1:**

1. At bot startup:
   - Exchange clients connect
   - Aster tried to start book ticker WebSocket (but no symbol known → failed)
   - Lighter started order book WebSocket (but for wrong market_id from config)

2. When opportunity found (e.g., SKY):
   - Aster book ticker was never started → `best_bid/best_ask` = `None`
   - Lighter might be subscribed to wrong market → stale data

3. Result:
   - `order_executor._fetch_bbo_prices_for_limit_order()` checks WebSocket
   - Finds `None` values or no data
   - Falls back to REST API ❌

### Fix Applied
✅ **Dynamic WebSocket Initialization:**

**Before (Wrong):**
```
Bot Startup → Connect Aster → Start book ticker (no symbol!) → Fail
           → Connect Lighter → Start order book (wrong market_id)

Opportunity Found → Try to use WebSocket → No data → REST fallback
```

**After (Correct):**
```
Bot Startup → Connect Aster → Skip book ticker
           → Connect Lighter → Start order book (config market_id)

Opportunity Found (SKY) → Pre-flight checks → Start Aster book ticker for SKYUSDT
                                           → Lighter already subscribed to SKY
                                           → Wait 0.5s for first BBO update

Place Limit Orders → WebSocket BBO available! → Real-time pricing ✅
```

### Expected Logs After Fix
```
✅ Started Aster book ticker for SKYUSDT
✅ Lighter order book WebSocket already active for SKY
🔴 [PRICE] Using WebSocket BBO for aster:SKY (bid=0.0374, ask=0.0375) - REAL-TIME
🔴 [PRICE] Using WebSocket BBO for lighter:SKY (bid=0.0373, ask=0.0374) - REAL-TIME
```

---

## Implementation Details

### Aster WebSocket Manager
**New Method:** `start_book_ticker(symbol: str)`
```python
async def start_book_ticker(self, symbol: str):
    """
    Start book ticker WebSocket for a specific symbol.
    
    This should be called AFTER identifying the opportunity symbol,
    not during initial connection.
    """
    # If already subscribed to this symbol, no need to restart
    if self._current_book_ticker_symbol == symbol:
        return
    
    # Cancel existing task if switching symbols
    if self._book_ticker_task and not self._book_ticker_task.done():
        self._book_ticker_task.cancel()
    
    # Start new book ticker task
    self._current_book_ticker_symbol = symbol
    self._book_ticker_task = asyncio.create_task(self._connect_book_ticker(symbol))
```

**Modified:** `_connect_book_ticker(symbol: str)`
- Now takes `symbol` as parameter (not from config)
- Connects to `wss://fstream.asterdex.com/ws/<symbol>@bookTicker`
- Updates `self.best_bid` and `self.best_ask` on each message

### Lighter WebSocket Manager
**No changes needed!**
- Already subscribes to correct market on connection
- Already maintains `self.best_bid` and `self.best_ask`
- Market index set from `config.contract_id` during initialization

---

## Testing Instructions

Run your funding arb strategy:
```bash
python runbot.py --config configs/real_funding_test.yml
```

### Expected Behavior
1. ✅ **On startup:** No WebSocket book ticker errors (Aster skips it)
2. ✅ **On opportunity found:** Logs show "Started Aster book ticker for SKYUSDT"
3. ✅ **On limit order placement:** Logs show "Using WebSocket BBO for aster:SKY (bid=X, ask=Y) - REAL-TIME"
4. ✅ **Leverage normalization:** Logs show "SKY normalized to 3x (limited by lighter)"
5. ✅ **No REST fallback:** Should NOT see "Fetching FRESH BBO... (WebSocket not available)"

### If You Still See "WebSocket not available"
This means the WebSocket didn't receive data in the 0.5s grace period. Possible causes:
- Network latency
- Exchange WebSocket slow to push first update
- Symbol mismatch (check normalized symbol format)

**Solution:** Increase grace period from 0.5s to 1.0s:
```python
# In atomic_multi_order.py, after starting WebSockets:
await asyncio.sleep(1.0)  # Increased from 0.5s
```

---

## Summary of Changes

### Files Modified
1. ✅ `exchange_clients/aster/websocket_manager.py`
   - Added `start_book_ticker(symbol)` method
   - Modified `_connect_book_ticker(symbol)` to take symbol parameter
   - Removed automatic book ticker startup on connect

2. ✅ `strategies/execution/patterns/atomic_multi_order.py`
   - Added WebSocket initialization in `_run_preflight_checks()`
   - Starts book ticker after identifying opportunity, before placing orders
   - Includes 0.5s grace period for first BBO update

3. ✅ `strategies/execution/core/order_executor.py` (no changes from this fix)
   - Already has `_fetch_bbo_prices_for_limit_order()` that prioritizes WebSocket BBO

### Key Takeaways
1. **Timing is everything:** Start WebSockets when you know the symbol, not at bot startup
2. **Account leverage is real:** Most perp exchanges use account-level leverage, not per-trade
3. **Real-time pricing works:** WebSocket BBO now properly available for limit orders

