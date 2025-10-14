## Funding-Arb Liquidation Detection Plan

### Current State
- `PositionMonitor.monitor()` polls exchange leg snapshots on a fixed cadence (`risk_config.check_interval_seconds`, default 60s).
- Liquidation is inferred when `get_position_snapshot()` shows zero quantity for one leg while the opposite leg remains open.
- No real-time stream or asynchronous callbacks feed the strategy yet; exposure can persist between polls.
- Some exchanges drop/lag REST requests; missing data can mask liquidations until a later retry.

### Issues
- **Detection latency**: up to the polling interval (≥60s) before directional exposure is noticed.
- **Single data source**: relies solely on polling; exchange outages or API rate limits delay detection.
- **Reactive close**: mitigation starts only after detection; there’s no immediate hedge.
- **Dirty accounting**: position DB entries remain “open” until a poll sees the liquidation.

### Improvement Tracks
1. **Streaming / Event-Driven Signals**
   - Audit each supported DEX for liquidation feeds. Example: Lighter offers a `notification/{ACCOUNT_ID}` WebSocket channel that pushes `kind: "liquidation"` payloads (size, price, timestamp).
   - Extend `exchange_clients/*/websocket_manager.py` (e.g., Lighter, Aster) to subscribe to notification channels alongside order books.
   - Surface a pluggable `LiquidationListener`/event bus so both the strategy and UI consumers can handle the events concurrently.
   - For on-chain DEXes without native notifications, explore blockchain event subscriptions (perpetual liquidation contracts).

2. **Margin Watchdogs**
   - Query maintenance requirements / liquidation prices (when exposed) via `ExchangePositionSnapshot.liquidation_price`.
   - Monitor underlying prices via the shared `PriceProvider`; trigger emergency close if price approaches liquidation within a configured buffer.
   - Fetch lightweight margin endpoints more frequently than full snapshots to avoid REST saturation.

3. **Adaptive Polling**
   - Shorten monitor intervals dynamically when volatility, divergence, or leverage is high.
   - After any leg experiences errors (partial cancel, timeout), temporarily increase polling frequency (5–10s) until confirmed healthy.

4. **Redundant Data Sources**
   - Cross-check funding-rate service metrics (open interest, funding spikes) to spot stress conditions.
   - Maintain exchange heartbeats; on loss, assume potential exposure and pre-emptively reduce positions.

5. **Local Exposure Sentinel**
   - Track expected net exposure per leg. If an exchange reports a fill/close for one side without matching counter-leg confirmation within N seconds, assume directional exposure and initiate a hedge.
   - Tie into order-executor callbacks and WebSocket order streams to stay ahead of scheduled polls.

6. **Exchange-Adaptive APIs**
   - Catalogue each DEX’s capabilities (`supports_liquidation_stream`, `supports_margin_feed`, etc.) so the strategy can select the best detection path.
   - For custodial APIs without streams, poll dedicated liquidation history endpoints as a secondary signal.

7. **Fail-Safe Hedging**
   - When a liquidation notification arrives, immediately place a compensating hedge on another exchange before full cleanup to reduce directional loss.
   - Requires rapid quantity estimation from surviving leg or most recent snapshot.

### Next Steps
1. Extend Lighter’s WebSocket manager to subscribe to `notification/{ACCOUNT_ID}` and dispatch liquidation events through a shared event bus. ✅ Implemented.
2. Wire Aster’s user data `forceOrder` events into the same event bus to capture exchange-driven liquidations. ✅ Implemented.
3. Design/implement a reusable `ExchangeEventStream` interface in `exchange_clients` that strategies can subscribe to. ✅ Event dispatcher now available.
4. Update `FundingArbitrageStrategy`/`PositionCloser` to consume liquidation events asynchronously, using the monitor loop as fallback. ✅ In place.
5. Introduce adaptive polling as a stopgap and instrument detection latency to quantify improvements.
6. Audit remaining exchanges for liquidation feeds and backfill gaps with alternative monitoring.

---

### Implementation Summary (Current State)

**Event Infrastructure**
- Added `exchange_clients/events.py` with a normalized `LiquidationEvent` dataclass and `LiquidationEventDispatcher` fan‑out queue.
- `BaseExchangeClient` gained lifecycle hooks (`supports_liquidation_stream`, `liquidation_events_queue`, `emit_liquidation_event`) to expose streams to strategies without tight coupling.

**Exchange Integrations**
- **Lighter** now:
  - Subscribes to `notification/{ACCOUNT_ID}` alongside existing order/trade feeds inside `exchange_clients/lighter/websocket_manager.py`.
  - When `kind == "liquidation"` notifications arrive, parses quantity/price/timestamp, and emits a `LiquidationEvent` via the dispatcher (`exchange_clients/lighter/client.py`).
  - Skips close attempts for the already liquidated leg (snapshots return zero quantity).
- **Aster** now:
  - Hooks the user-data WebSocket stream to forward `forceOrder` payloads to the dispatcher (`exchange_clients/aster/websocket_manager.py`, `exchange_clients/aster/client.py`).
  - Normalizes Binance-style symbols (`BTCUSDT` → `BTC`) before emitting events so symbol matching works consistently.

**Strategy Consumption**
- `FundingArbitrageStrategy` starts background consumers for each exchange client that reports `supports_liquidation_stream()`. These tasks listen on the registered queues and forward events to the position closer (`strategy.py`).
- `PositionCloser.handle_liquidation_event()` fetches current exchange snapshots and:
  - Logs the emergency.
  - Market-closes the surviving leg (skips any leg whose snapshot quantity is already zero).
  - Marks the position closed in the DB with reason `LIQUIDATION_<DEX>`, keeping existing metrics intact.
- The legacy polling loop remains active for exchanges without event feeds or as a fallback.

**Testing & Docs**
- Unit tests cover the dispatcher fan-out/unregister behaviour (`tests/exchange_clients/test_events.py`).
- Architecture doc now reflects completed work and outstanding improvements.
