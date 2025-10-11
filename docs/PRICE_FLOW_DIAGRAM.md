# Price Data Flow - Visual Diagrams

**Complete visualization of the new cache-first architecture**

---

## 📊 **Complete System Flow**

```
┌─────────────────────────────────────────────────────────────────┐
│                   Funding Arbitrage Strategy                     │
│                                                                   │
│  __init__():                                                     │
│    price_provider = PriceProvider(ttl=5.0) ←─┐                 │
│    atomic_executor = AtomicMultiOrderExecutor(│                 │
│        price_provider ─────────────────────────┤                │
│    )                                           │                 │
│    liquidity_analyzer = LiquidityAnalyzer(     │                │
│        price_provider ─────────────────────────┘                │
│    )                                                              │
└──────────────────────────────┬────────────────────────────────────┘
                               │
                               │ execute_cycle()
                               ↓
┌─────────────────────────────────────────────────────────────────┐
│               Phase 1: Pre-flight Checks                         │
│                                                                   │
│  atomic_executor._run_preflight_checks()                        │
│    └─→ liquidity_analyzer.check_execution_feasibility()        │
│                                                                   │
│         ┌────────────────────────────────────┐                  │
│         │ Exchange Client (Lighter)          │                  │
│         │                                     │                  │
│         │ get_order_book_depth("BTC", 20)   │                  │
│         │   ↓ HTTP GET                       │                  │
│         │   ↓ /api/v1/orderBookOrders        │                  │
│         │   ↓ market_id=1, limit=100         │                  │
│         │   ↓ [50ms latency]                 │                  │
│         │   ↓                                 │                  │
│         │ Returns: {                          │                  │
│         │   bids: [{price: 50000, size: 10}] │                  │
│         │   asks: [{price: 50001, size: 8}]  │                  │
│         │ }                                   │                  │
│         └────────────────────────────────────┘                  │
│                        │                                          │
│                        ↓                                          │
│         ┌────────────────────────────────────┐                  │
│         │ Liquidity Analyzer                 │                  │
│         │                                     │                  │
│         │ ✅ depth_sufficient = True         │                  │
│         │ ✅ slippage_pct = 0.002            │                  │
│         │ ✅ liquidity_score = 0.95          │                  │
│         │                                     │                  │
│         │ 💾 CACHE ORDER BOOK:               │                  │
│         │ price_provider.cache_order_book(   │                  │
│         │   exchange="lighter",              │                  │
│         │   symbol="BTC",                    │                  │
│         │   order_book=...                   │                  │
│         │ )                                   │                  │
│         └────────────────────────────────────┘                  │
│                        │                                          │
│                        ↓                                          │
│         ┌────────────────────────────────────┐                  │
│         │ PriceProvider Cache                │                  │
│         │                                     │                  │
│         │ cache["lighter:BTC"] = PriceData(  │                  │
│         │   best_bid=50000,                  │                  │
│         │   best_ask=50001,                  │                  │
│         │   timestamp=2025-10-10 16:48:45,   │                  │
│         │   source="liquidity_check"         │                  │
│         │ )                                   │                  │
│         └────────────────────────────────────┘                  │
└─────────────────────────────────────────────────────────────────┘

                               │
                               │ [~10ms later]
                               ↓

┌─────────────────────────────────────────────────────────────────┐
│               Phase 2: Order Execution                           │
│                                                                   │
│  atomic_executor._place_single_order()                          │
│    └─→ order_executor.execute_order()                           │
│         └─→ order_executor._execute_limit()                     │
│              └─→ order_executor._fetch_bbo_prices()             │
│                                                                   │
│                   ┌────────────────────────────────────┐        │
│                   │ Order Executor                     │        │
│                   │                                     │        │
│                   │ _fetch_bbo_prices("BTC"):          │        │
│                   │   if price_provider:               │        │
│                   │     return price_provider          │        │
│                   │       .get_bbo_prices(...)  ←─┐   │        │
│                   └─────────────────────────────┼─┘   │        │
│                                                  │             │
│                                                  ↓             │
│                   ┌────────────────────────────────────┐      │
│                   │ PriceProvider                      │      │
│                   │                                     │      │
│                   │ get_bbo_prices():                  │      │
│                   │   cache_key = "lighter:BTC"        │      │
│                   │   cached = cache.get(cache_key)    │      │
│                   │                                     │      │
│                   │   if cached and age < 5.0s:        │      │
│                   │     ✅ CACHE HIT!                  │      │
│                   │     age = 0.05s (50ms ago)         │      │
│                   │     return (50000, 50001)          │      │
│                   │     [0.001ms latency] ⚡           │      │
│                   └────────────────────────────────────┘      │
│                                │                                │
│                                ↓                                │
│                   ┌────────────────────────────────────┐       │
│                   │ Order Executor                     │       │
│                   │                                     │       │
│                   │ Calculate limit price:             │       │
│                   │   best_ask = 50001                 │       │
│                   │   offset = 0.01% = 5               │       │
│                   │   limit_price = 50001 - 5 = 49996  │       │
│                   │                                     │       │
│                   │ Place order:                        │       │
│                   │   exchange_client.place_limit_order(│      │
│                   │     "BTC", qty, 49996, "buy"       │       │
│                   │   )                                  │       │
│                   └────────────────────────────────────┘       │
└─────────────────────────────────────────────────────────────────┘
```

---

## 🔄 **Cache State Timeline**

```
t=0ms: [START] Strategy begins opening position
   │
   ├─→ Cache state: {}  (empty)
   │
   ↓

t=50ms: [LIQUIDITY CHECK] Fetching order book
   │
   ├─→ HTTP GET /api/v1/orderBookOrders
   │   └─→ Returns: {bids: [...], asks: [...]}
   │
   ├─→ Liquidity analyzer: ✅ PASS
   │
   ├─→ 💾 Cache order book:
   │   cache["lighter:BTC"] = {
   │     bid: 50000,
   │     ask: 50001,
   │     time: t=50ms,
   │     source: "liquidity_check"
   │   }
   │
   └─→ Cache state: {"lighter:BTC": ...}  (cached!)
   │
   ↓

t=60ms: [ORDER EXECUTION] Getting current prices
   │
   ├─→ _fetch_bbo_prices("BTC")
   │   └─→ price_provider.get_bbo_prices(...)
   │       └─→ Check cache: "lighter:BTC"
   │           └─→ ✅ HIT! Age = 10ms < 5000ms
   │               └─→ Return (50000, 50001)
   │                   [NO API CALL!] ⚡
   │
   └─→ Calculate limit price: 49996
   │
   ↓

t=80ms: [PLACE ORDER] Sending to exchange
   │
   └─→ exchange_client.place_limit_order(...)
   │
   ↓

t=100ms: [DONE] Order placed successfully
   │
   └─→ Total time: 100ms (vs 150ms with duplicate API call)
   └─→ API calls: 1 (vs 2 with duplicate)
   └─→ Cache hits: 1
   └─→ Cache hit rate: 100% ✅
```

---

## 🆚 **OLD vs NEW Architecture**

### **OLD: WebSocket-First with REST Fallback**

```
┌─────────────────────────────────────────┐
│ Order Executor                           │
│                                          │
│ _fetch_bbo_prices():                    │
│   1. Try WebSocket                      │
│      ├─→ if ws_manager.best_bid:        │
│      │     return (bid, ask)            │
│      │     [1-5ms] ⚡                    │
│      └─→ else:                          │
│            goto step 2 ❌               │
│                                          │
│   2. Fallback to REST API               │
│      ├─→ get_order_book_depth(levels=1) │
│      │     [50ms] 🐌                     │
│      └─→ return (bid, ask)              │
└─────────────────────────────────────────┘

Problems:
❌ WebSocket not ready during initialization
❌ WebSocket can have stale/invalid data
❌ Duplicate REST API calls (liquidity check + fallback)
❌ Complex error handling (2 failure modes)
```

### **NEW: Cache-First with REST Fallback**

```
┌─────────────────────────────────────────┐
│ PriceProvider                            │
│                                          │
│ get_bbo_prices():                       │
│   1. Try Cache (5s TTL)                 │
│      ├─→ if cached and valid:           │
│      │     return (bid, ask)            │
│      │     [0.001ms] ⚡⚡⚡              │
│      └─→ else:                          │
│            goto step 2                  │
│                                          │
│   2. Fetch via REST API                 │
│      ├─→ get_order_book_depth(levels=1) │
│      │     [50ms]                        │
│      ├─→ cache result                    │
│      └─→ return (bid, ask)              │
└─────────────────────────────────────────┘

Benefits:
✅ Cache always available (filled during liquidity check)
✅ Zero duplicate API calls
✅ Simple error handling (1 failure mode)
✅ Predictable performance
```

---

## 🎯 **Data Freshness Analysis**

```
Scenario: Opening BTC funding arb position

┌─────────────────────────────────────────────────────────────┐
│ Time Point      │ Action              │ Price Age          │
├─────────────────┼─────────────────────┼───────────────────┤
│ t=0ms           │ Strategy starts     │ N/A                │
│                 │                     │                    │
│ t=50ms          │ Liquidity check     │ Fresh (0ms)        │
│                 │ • Fetch order book  │                    │
│                 │ • Cache result      │                    │
│                 │                     │                    │
│ t=60ms          │ Order execution     │ Cached (10ms old)  │
│                 │ • Use cached prices │                    │
│                 │ • Calculate limit   │                    │
│                 │                     │                    │
│ t=80ms          │ Place order         │ Cached (30ms old)  │
│                 │ • Submit to exchange│                    │
└─────────────────────────────────────────────────────────────┘

Question: Is 10-30ms price age acceptable?

Answer: YES! ✅

Reasoning:
1. Funding rates change HOURLY (not per-second)
2. Our orders are LIMIT orders (not market)
3. Limit order pricing uses bid/ask with buffer
4. 10-30ms is negligible compared to:
   - Order routing: 50-100ms
   - Order fill time: 1-30 seconds
   - Funding period: 8 hours = 28,800,000ms

Conclusion: Cache freshness is MORE than adequate for funding arbitrage.
```

---

## 📈 **Cache Hit Rate Projections**

```
Scenario: Opening 10 funding arb positions per hour

┌──────────────────────────────────────────────────────────────┐
│ Position │ Liquidity Check │ Order Execution │ Cache Result │
├──────────┼─────────────────┼─────────────────┼──────────────┤
│ 1        │ REST API (50ms) │ Cache hit (0ms) │ HIT ✅       │
│ 2        │ REST API (50ms) │ Cache hit (0ms) │ HIT ✅       │
│ 3        │ REST API (50ms) │ Cache hit (0ms) │ HIT ✅       │
│ ...      │ ...             │ ...             │ ...          │
│ 10       │ REST API (50ms) │ Cache hit (0ms) │ HIT ✅       │
└──────────────────────────────────────────────────────────────┘

Cache Hit Rate: 10/10 = 100% ✅

Why so high?
• Each position requires 2 price fetches (liq check + execution)
• Liq check → execution gap is < 100ms (well within 5s TTL)
• No cache misses expected in normal operation

API Call Reduction:
• OLD: 20 API calls (10 positions × 2 calls each)
• NEW: 10 API calls (10 positions × 1 call each)
• Savings: 50% ✅
```

---

## 🔮 **Future: Multi-Tier Caching**

```
┌─────────────────────────────────────────────────────────────┐
│                    Future Architecture                       │
│                                                               │
│  get_bbo_prices():                                           │
│    1. L1 Cache (In-memory, 5s TTL)                          │
│       ├─→ Hit rate: 80%                                      │
│       └─→ Latency: 0.001ms ⚡⚡⚡                            │
│                                                               │
│    2. L2 Cache (Redis, 60s TTL, shared)                     │
│       ├─→ Hit rate: 15%                                      │
│       └─→ Latency: 5ms ⚡                                    │
│                                                               │
│    3. L3 REST API (Fresh data)                              │
│       ├─→ Hit rate: 5%                                       │
│       └─→ Latency: 50ms                                      │
│                                                               │
│    4. L4 Historical (Degraded mode)                         │
│       ├─→ Hit rate: 0.01%                                    │
│       └─→ Latency: 100ms                                     │
└─────────────────────────────────────────────────────────────┘

Benefits:
✅ 95% hit rate on L1+L2 (< 5ms latency)
✅ Shared cache across bot instances
✅ Graceful degradation on API failures
```

---

## ✨ **Visual Summary**

```
┌────────────────────────────────────────────────────────────┐
│                    Cache-First Flow                         │
│                                                              │
│  Liquidity Check            Order Execution                 │
│  ─────────────────          ───────────────                 │
│                                                              │
│  ┌──────────┐              ┌──────────┐                    │
│  │ REST API │              │  Cache   │                    │
│  │  [50ms]  │              │ [0.001ms]│                    │
│  └────┬─────┘              └────┬─────┘                    │
│       │                         │                           │
│       │ Fetch order book        │ Reuse cached data        │
│       │                         │                           │
│       ↓                         ↓                           │
│  ┌─────────────┐          ┌─────────────┐                 │
│  │ 💾 Cache it │          │ ✅ Use it   │                 │
│  └─────────────┘          └─────────────┘                 │
│                                                              │
│  Result: 1 API call, 50ms total                            │
│  Cache hit rate: 100% ✅                                   │
└────────────────────────────────────────────────────────────┘
```

---

**The key insight:** By treating the liquidity check as a **cache warming step**, we eliminate the need for WebSocket during order execution entirely.

**Simple. Fast. Reliable.** 🚀

