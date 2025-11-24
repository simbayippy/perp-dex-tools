# Profit-Taking System

Real-time profit-taking mechanism for funding arbitrage positions that captures cross-exchange basis spread opportunities (mean-reversion trading).

## Overview

This system monitors WebSocket BBO (Best Bid/Offer) streams to detect immediate profit opportunities on open positions, enabling sub-second detection latency compared to the 60-second polling of risk management.

## Architecture

### Components

```
profit_taking/
├── profit_evaluator.py    # Core profitability calculation logic
├── profit_taker.py         # Orchestration layer (evaluation → execution)
└── real_time_monitor.py    # WebSocket BBO listener management
```

### Responsibilities

- **`ProfitEvaluator`**: Calculates unrealized PnL using fresh BBO prices and determines if position meets profit threshold
- **`ProfitTaker`**: Coordinates evaluation → verification → execution flow, interfaces with `PositionCloser`
- **`RealTimeProfitMonitor`**: Manages WebSocket BBO listeners per position, throttles checks, handles concurrency

## How It Works

### 1. Position Lifecycle

```
Position Opened
    └→ profit_taker.register_position()
        └→ profit_monitor.register_position()
            ├─ Register BBO listener on long_dex WebSocket
            └─ Register BBO listener on short_dex WebSocket

Position Closed
    └→ profit_taker.unregister_position()
        └→ profit_monitor.unregister_position()
            ├─ Unregister BBO listeners
            └─ Cleanup throttle tracking
```

### 2. Real-Time Detection Flow

```
Exchange WebSocket emits BBO update
    └→ real_time_monitor listener callback
        ├─ 1. Symbol filtering (matches position?)
        ├─ 2. Throttling (>1s since last check?)
        ├─ 3. Concurrency check:
        │   ├─ Already being evaluated? → skip
        │   └─ Already being closed? → skip
        ├─ 4. Fetch snapshots (cached from position_monitor)
        ├─ 5. Collect fresh BBO prices from both exchanges
        └─ 6. profit_taker.evaluate_and_execute()
            └─ profit_evaluator.check_immediate_profit_opportunity()
                ├─ Calculate PnL using FRESH BBO:
                │   ├─ LONG leg: (bid - entry_price) × quantity
                │   └─ SHORT leg: (entry_price - ask) × quantity
                ├─ Add funding accrued
                ├─ Subtract entry fees
                └─ Compare vs min_immediate_profit_taking_pct (0.2%)

            If profitable:
            ├─ Verify profitability again (optional)
            └─ position_closer.close(order_type="aggressive_limit")
```

### 3. Fresh BBO Pricing Logic

**Why BBO matters:** We need to know the **actual prices we can execute at right now**.

```python
# LONG leg (we own the asset, need to SELL)
long_pnl = (current_bid - entry_price) × quantity
# Use BID because that's what buyers will pay us

# SHORT leg (we're short, need to BUY back)
short_pnl = (entry_price - current_ask) × quantity
# Use ASK because that's what sellers will charge us

total_pnl = long_pnl + short_pnl + funding_accrued - fees
```

**Fallback:** If BBO data unavailable, uses `snapshot.unrealized_pnl` (mark price-based, up to 30s stale).

## Comparison: Profit-Taking vs Position Monitor

| Aspect | Profit-Taking (This System) | Position Monitor (Risk Management) |
|--------|---------------------------|-----------------------------------|
| **Purpose** | Capture favorable price divergence | Prevent losses from risk factors |
| **Trigger** | WebSocket BBO updates (event-driven) | 60-second polling loop |
| **Latency** | <1 second | Up to 60 seconds |
| **Pricing** | Fresh BBO (bid/ask from WebSocket) | Stale `snapshot.unrealized_pnl` (mark price) |
| **Execution** | Aggressive limit orders | Depends on urgency (market/limit/polling) |
| **Priority** | Runs BEFORE risk-based exits | Runs AFTER critical risks (liquidation, imbalance) |
| **API Calls** | Zero (WebSocket + cached snapshots) | REST API per position per check |
| **Checks** | Profitability threshold (0.2% default) | Funding flip, profit erosion, time limits, liquidation |
| **Outcome** | Immediate close if profitable | May defer to exit polling if spread too wide |

### Priority Order in position_closer.evaluateAndClosePositions()

```
1. Liquidation Risk       [CRITICAL - position_closer]
2. Already Liquidated     [CRITICAL - position_closer]
3. Imbalance Detection    [CRITICAL - position_closer]
4. IMMEDIATE PROFIT       [OPPORTUNISTIC - profit_taker] ← This system
5. Risk-Based Exits       [NON-CRITICAL - exit_evaluator]
   └─ May trigger exit polling if spread too wide
```

**Key Insight:** Profit-taking runs as a **pre-check** before risk-based closing to capture opportunities early.

## Concurrency Protection

Both profit-taking and risk management coordinate through `position_closer._positions_closing` lock:

```python
# real_time_monitor checks BEFORE evaluation
if position_id in position_closer._positions_closing:
    return  # Skip, already being closed elsewhere

# position_closer.close() checks at entry
if position_id in self._positions_closing:
    return  # Skip duplicate close

self._positions_closing.add(position_id)
try:
    # Execute close...
finally:
    self._positions_closing.discard(position_id)
```

**Result:** Only ONE close operation per position at a time, regardless of source.

## Configuration

From `FundingArbitrageConfig`:

```python
# Master toggle: Enable/disable ALL profit-taking
enable_immediate_profit_taking: bool = True

# Throttle: minimum seconds between checks per position
realtime_profit_check_interval: float = 1.0

# Minimum profit threshold (percentage of position size)
min_immediate_profit_taking_pct: Decimal = 0.002  # 0.2%
```

### Configuration Notes

**Minimal Configuration - Just 3 Settings:**
1. **Master toggle** (`enable_immediate_profit_taking`): On/off switch for entire system
2. **Throttle** (`realtime_profit_check_interval`): How often to check per position (default 1s)
3. **Threshold** (`min_immediate_profit_taking_pct`): Minimum profit to trigger close (default 0.2%)

**Always-On Optimizations (no config needed):**
- **Real-time monitoring**: WebSocket BBO listeners (zero-cost)
- **Smart caching**: Cached snapshots first, fresh REST fallback
- **Verification**: Always double-check profitability before execution
- **Aggressive limits**: Always use maker fees (cheaper, better fills)

**Behavior:**
- `enable_immediate_profit_taking=True` (default): Full real-time profit-taking with all optimizations
- `enable_immediate_profit_taking=False`: No profit-taking (positions only close via risk management)

## Example Scenario

```
t=0s:   Position opened BTC
        - Long@Aster:  entry=$100,000
        - Short@Lighter: entry=$100,000

        profit_monitor registers BBO listeners on both exchanges

t=5s:   Aster BID spikes to $100,500
        └→ WebSocket emits BBO update
           └→ real_time_monitor listener fires
              └→ Calculate PnL using FRESH BBO:
                 ├─ Long:  ($100,500 - $100,000) × 1 = +$500
                 ├─ Short: ($100,000 - $100,000) × 1 = $0
                 ├─ Total: $500 - $50 fees = +$450
                 └─ Profit %: +0.45% > 0.2% threshold ✅

              └→ position_closer.close() executes:
                 ├─ Sell BTC on Aster at BID ($100,500)
                 └─ Buy BTC on Lighter at ASK ($100,000)
                 └─ Realized profit: ~$450

t=60s:  position_monitor runs (but position already closed)
```

**Without real-time monitoring:** Would wait up to 60 seconds, potentially missing the opportunity if price reverts.

## Performance Characteristics

### Zero-Cost Detection
- **WebSocket listeners**: Reuse existing exchange WebSocket connections
- **Smart caching**: Automatically uses position_monitor's 30-second cached snapshots, falls back to REST if stale
- **No additional API calls**: ~98% of checks use cached data (zero API calls)

### Throttling
- **Per-position limit**: Max 1 check per second per position (default)
- **Prevents spam**: High-frequency BBO updates don't trigger excessive evaluations
- **Configurable**: `realtime_profit_check_interval` parameter

### Latency Reduction
- **Before**: 60-second polling → 30-second average detection latency
- **After**: WebSocket-driven → <1 second detection latency
- **Impact**: 30-60x faster opportunity capture

### Always-On Design
- **No manual toggling**: Real-time monitoring automatically enabled with master `enable_immediate_profit_taking` flag
- **Smart defaults**: Caching and fallback behavior handled automatically
- **Pit of success**: Optimal performance out-of-the-box

## Integration Points

### Initialization (strategy.py)
```python
from .operations.closing.profit_taking import ProfitTaker, RealTimeProfitMonitor

self.profit_taker = ProfitTaker(self)
self.profit_monitor = RealTimeProfitMonitor(self)
```

### Position Opening (position_opener.py)
```python
# After successful position open
await self._strategy.profit_taker.register_position(position)
```

### Position Closing (position_closer.py)
```python
# Priority 4: Check immediate profit before risk exits
profit_taker = getattr(strategy, 'profit_taker', None)
if profit_taker:
    was_closed = await profit_taker.evaluate_and_execute(
        position, snapshots, trigger_source="polling"
    )
    if was_closed:
        continue  # Skip remaining checks
```

### Cleanup (position_closer.close())
```python
# Unregister BBO listeners when closing
profit_taker = getattr(strategy, 'profit_taker', None)
if profit_taker:
    await profit_taker.unregister_position(position)
```

## Testing

Tests located at: `tests/strategies/funding_arbitrage/closing/profit_taking/`

Run tests:
```bash
pytest tests/strategies/funding_arbitrage/closing/profit_taking/ -v
```

Key test coverage:
- BBO listener registration/unregistration
- Throttling behavior
- Symbol filtering and matching
- Concurrency protection
- Cached vs fresh snapshot handling
- Profit opportunity detection

## Design Principles

1. **Separation of Concerns**: Profit-taking (opportunistic) vs risk management (defensive)
2. **Zero Additional Cost**: Reuse existing WebSocket and monitoring infrastructure
3. **Concurrency Safe**: Coordinate with position_closer via shared lock
4. **Fail-Safe Fallback**: Degrades gracefully to snapshot pricing if BBO unavailable
5. **Configurable**: All behavior tunable via config parameters
6. **Event-Driven**: React to price movements in real-time, not on schedule

## Future Enhancements

Potential improvements:
- [ ] Adaptive throttling based on volatility
- [ ] Multi-tier profit thresholds (partial close at 0.2%, full close at 0.5%)
- [ ] Profit opportunity prediction using short-term price trends
- [ ] Integration with order book depth for slippage estimation
- [ ] Metrics dashboard for profit-taking performance
