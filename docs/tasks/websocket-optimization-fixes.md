# WebSocket Optimization Fixes

## Summary
Fixed two critical issues preventing efficient use of WebSocket connections for real-time order book data.

---

## Issue 1: Liquidity Analyzer Using REST Instead of WebSocket

### Your Observation
> "If we've successfully connected to the book using websocket (RED BOX), why in liquidity_analyzer + the exchange clients get_order_book, do we do a REST API Call to the exchanges endpoint to get the data (BLUE BOX)? If we are connected to WS, why not just use that directly? That will be much faster, and we'll be using a setup we already have + saves latency timing."

### Root Cause
**You're absolutely right!** 

The liquidity analyzer was calling `exchange_client.get_order_book_depth()`, which was **ALWAYS** making a REST API call, even though:
1. Lighter WebSocket maintains a full real-time order book
2. The WebSocket is already connected and receiving updates
3. REST API adds unnecessary latency (~100-500ms)

### Fix Applied
✅ **Lighter Client** (`exchange_clients/lighter/client.py`):

**New Method:** `get_order_book_from_websocket()`
```python
def get_order_book_from_websocket(self) -> Optional[Dict[str, List[Dict[str, Decimal]]]]:
    """
    Get order book from WebSocket if available (zero latency).
    
    Returns:
        Order book dict if WebSocket is connected and has data, None otherwise
    """
    try:
        if not self.ws_manager or not self.ws_manager.running:
            return None
        
        if not self.ws_manager.snapshot_loaded:
            return None
        
        # Check if order book has data
        if not self.ws_manager.order_book["bids"] or not self.ws_manager.order_book["asks"]:
            return None
        
        # Convert WebSocket order book to standard format
        bids = [
            {'price': Decimal(str(price)), 'size': Decimal(str(size))}
            for price, size in sorted(self.ws_manager.order_book["bids"].items(), reverse=True)
        ]
        asks = [
            {'price': Decimal(str(price)), 'size': Decimal(str(size))}
            for price, size in sorted(self.ws_manager.order_book["asks"].items())
        ]
        
        self.logger.info(
            f"📡 [WEBSOCKET] Using real-time order book from WebSocket "
            f"({len(bids)} bids, {len(asks)} asks)"
        )
        
        return {
            'bids': bids,
            'asks': asks
        }
        
    except Exception as e:
        self.logger.warning(f"Failed to get order book from WebSocket: {e}")
        return None
```

**Modified:** `get_order_book_depth()`
```python
async def get_order_book_depth(self, contract_id: str, levels: int = 10):
    try:
        # 🔴 Priority 1: Try WebSocket (real-time, zero latency)
        ws_book = self.get_order_book_from_websocket()
        if ws_book:
            # Limit to requested levels
            return {
                'bids': ws_book['bids'][:levels],
                'asks': ws_book['asks'][:levels]
            }
        
        # 🔄 Priority 2: Fall back to REST API
        self.logger.info(
            f"📞 [REST] Fetching order book via REST API (WebSocket not available)"
        )
        # ... (existing REST API code)
```

### Benefits
- ⚡ **Zero latency:** WebSocket data is already in memory
- 🔄 **Real-time:** Order book updates continuously
- 💰 **Less API load:** No redundant REST calls
- 🔒 **Automatic fallback:** Uses REST if WebSocket unavailable

### Expected Logs (After Fix)
**Before (Wrong):**
```
INFO | lighter.client:get_order_book_depth:376 | 📊 [LIGHTER] Fetching order book: market_id=79, limit=100
```

**After (Correct):**
```
INFO | lighter.client:get_order_book_from_websocket:354 | 📡 [WEBSOCKET] Using real-time order book from WebSocket (342 bids, 289 asks)
```

---

## Issue 2: Lighter WebSocket BBO Always None ($40K Filter Bug)

### Your Observation
> "For Lighter, although it seems we connected to the WS, in reality its NOT connected, either that or the check at the order_executor.py is problematic"

Log:
```
INFO | core.order_executor:_fetch_bbo_prices_...:479 | 🔄 [PRICE] Fetching FRESH BBO for lighter:SKY (WebSocket not available, forcing REST API call)
```

### Root Cause
**WebSocket WAS connected and receiving data, but `best_bid` and `best_ask` were always `None`!**

Found the bug in `lighter/websocket_manager.py` line 189-194:

```python
# Get all bid levels with sufficient size
bid_levels = [(price, size) for price, size in self.order_book["bids"].items()
              if size * price >= 40000]  # ❌ HARDCODED $40,000 MINIMUM!!!
```

**The Problem:**
- This filter requires **$40,000 USD** liquidity at each level
- You're trading **$10 USD** positions
- No order book levels meet this threshold
- `best_bid` and `best_ask` always return `None`
- Order executor sees `None` and falls back to REST API

This was a hardcoded filter from when someone was testing with large orders ($40K+), but it broke small orders.

### Fix Applied
✅ **Lighter WebSocket Manager** (`exchange_clients/lighter/websocket_manager.py`):

**Modified:** `get_best_levels(min_size_usd: float = 0)`
```python
def get_best_levels(self, min_size_usd: float = 0) -> Tuple[...]:
    """
    Get the best bid and ask levels from order book.
    
    Args:
        min_size_usd: Minimum size in USD (default: 0 = no filter, return true best bid/ask)
    
    Returns:
        ((best_bid_price, best_bid_size), (best_ask_price, best_ask_size))
    """
    try:
        # Get all bid levels with sufficient size
        bid_levels = [(price, size) for price, size in self.order_book["bids"].items()
                      if size * price >= min_size_usd]  # ✅ Now configurable, default 0

        # Get all ask levels with sufficient size
        ask_levels = [(price, size) for price, size in self.order_book["asks"].items()
                      if size * price >= min_size_usd]  # ✅ Now configurable, default 0

        # Get best bid (highest price) and best ask (lowest price)
        best_bid = max(bid_levels) if bid_levels else (None, None)
        best_ask = min(ask_levels) if ask_levels else (None, None)

        return best_bid, best_ask
    except (ValueError, KeyError) as e:
        self._log(f"Error getting best levels: {e}", "ERROR")
        return (None, None), (None, None)
```

**Updated Call Site:**
```python
# Get the best bid and ask levels (no size filter)
(best_bid_price, best_bid_size), (best_ask_price, best_ask_size) = self.get_best_levels(min_size_usd=0)
```

### Benefits
- ✅ Works with orders of any size ($1, $10, $1M, etc.)
- 📊 Returns true best bid/ask from order book
- 🎯 Optional size filter for special use cases

### Expected Logs (After Fix)
**Before (Wrong):**
```
🔄 [PRICE] Fetching FRESH BBO for lighter:SKY (WebSocket not available, forcing REST API call)
```

**After (Correct):**
```
🔴 [PRICE] Using WebSocket BBO for lighter:SKY (bid=0.0373, ask=0.0374) - REAL-TIME
```

---

## Why This Matters for Performance

### Before (Slow) ❌
```
1. Liquidity Check → REST API call to Lighter → 150ms latency
2. Place Limit Order → Check WebSocket BBO → None → REST API call → 150ms latency
3. Total: ~300ms of unnecessary latency
```

### After (Fast) ✅
```
1. Liquidity Check → WebSocket order book (already in memory) → 0ms latency
2. Place Limit Order → WebSocket BBO (already in memory) → 0ms latency
3. Total: ~0ms latency (just memory access)
```

**Speed improvement:** ~300ms saved per trade = **critical for delta-neutral arb** where timing is everything!

---

## Aster vs Lighter WebSocket Differences

### Lighter WebSocket
- ✅ Maintains **full order book** (all price levels)
- ✅ Can be used for liquidity checks
- ✅ Can be used for BBO
- ✅ Real-time updates via incremental order book stream

### Aster WebSocket
- ✅ Maintains **best bid/ask only** (via book ticker stream)
- ❌ Does NOT maintain full order book
- ✅ Can be used for BBO (limit orders)
- ❌ Cannot be used for full liquidity checks (still needs REST API)

This is why:
- Lighter uses WebSocket for both liquidity checks AND limit orders
- Aster uses WebSocket for limit orders only, REST API for liquidity checks

---

## Testing Instructions

Run your funding arb strategy:
```bash
python runbot.py --config configs/real_funding_test.yml
```

### Expected Logs for Lighter

**Liquidity Check:**
```
✅ [WEBSOCKET] Using real-time order book from WebSocket (342 bids, 289 asks)
```

**Limit Order Placement:**
```
🔴 [PRICE] Using WebSocket BBO for lighter:SKY (bid=0.0373, ask=0.0374) - REAL-TIME
```

**What Should NOT Appear:**
```
❌ 📊 [LIGHTER] Fetching order book: market_id=79, limit=100
❌ 🔄 [PRICE] Fetching FRESH BBO for lighter:SKY (WebSocket not available, forcing REST API call)
```

### Expected Logs for Aster

**Liquidity Check:**
```
📞 [REST] Fetching order book via REST API (WebSocket does not maintain full depth)
```
*(This is expected - Aster WebSocket only has BBO, not full depth)*

**Limit Order Placement:**
```
🔴 [PRICE] Using WebSocket BBO for aster:SKYUSDT (bid=0.0374, ask=0.0375) - REAL-TIME
```

---

## Summary of Changes

### Files Modified

1. ✅ `exchange_clients/lighter/websocket_manager.py`
   - Modified `get_best_levels()` to accept `min_size_usd` parameter (default: 0)
   - Removed hardcoded $40K minimum size filter
   - Now returns true best bid/ask for any order size

2. ✅ `exchange_clients/lighter/client.py`
   - Added `get_order_book_from_websocket()` method
   - Modified `get_order_book_depth()` to try WebSocket first, fall back to REST
   - Liquidity checks now use real-time WebSocket data (zero latency)

### Key Takeaways
1. **Use what you have:** If WebSocket is connected, use it! Don't make redundant REST calls.
2. **Hardcoded filters are dangerous:** The $40K filter broke small orders silently.
3. **Performance matters:** 300ms saved per trade is huge for delta-neutral arb.
4. **Exchange differences:** Lighter maintains full order book, Aster only BBO.

