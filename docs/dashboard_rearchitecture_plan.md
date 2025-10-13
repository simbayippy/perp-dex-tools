# Dashboard & Control Plane Roadmap

**Status:** Draft  
**Audience:** Strategy/Core/Tooling engineers  
**Last Updated:** 2025-10-12

---

## 1. Motivation

The current dashboard tooling relies on the PostgreSQL snapshot tables introduced during the initial UI work. The flow looks like:

1. Funding arbitrage strategy publishes snapshots/events via `DashboardService`.
2. Snapshots are persisted to `dashboard_snapshots` (with per-snapshot metadata in JSONB).
3. CLI/TUI tools read the latest row per session and render it.

This works for offline inspection and ensures historical data is saved, but it has drawbacks for a live control plane:

- Every render hits the DB; UIs are only as up-to-date as the last write.
- Transient metrics (mark prices, PnL, funding erosion) are duplicated in the database even though they change rapidly.
- Position management actions (e.g. “close position now”) would have to mutate state directly or rely on polling, which is brittle.
- There’s no streaming channel for event-driven updates or confirmations.

Goal: introduce a real-time control plane that separates *durable history* from *live state*, supports responsive UIs, and provides hooks for operator commands (close/rebalance, adjust risk, etc.).

---

## 2. Current Components

### Strategy-side
- `FundingArbitrageStrategy` (Layer 3) drives execution and updates the position manager (`FundingArbPositionManager`).
- `FundingArbPositionManager` persists positions and funding payments to `strategy_positions` / `funding_payments`.
- `risk_management/` contains pluggable exit logic (profit erosion, divergence flip, combined rules).
- `BaseExchangeClient` exposes order and position primitives; subclasses (e.g., `AsterClient`, `LighterClient`) implement WebSocket and REST integration.

### Dashboard tooling
- `dashboard/service.py`: optional in-process publisher (currently writing snapshots/events to DB).
- `scripts/dashboard_viewer.py`: CLI that prints the latest snapshot table (reads DB).
- `tui/dashboard_app.py`: Textual prototype menu, also reading DB snapshots.
- DB schema: `dashboard_sessions`, `dashboard_snapshots`, `dashboard_events`.

---

## 3. Design Principles for the Refactor

1. **Separate live cache vs. persistence**
   - Keep writing important lifecycle events to PostgreSQL (position opened/closed, funding payments, periodic summary).
   - Maintain an in-memory snapshot of live metrics (PnL, mark prices, divergence, risk flags) that UIs read directly.
   - The live cache is authoritative for current state; DB is authoritative for history.

2. **Publish/subscribe for UI updates**
   - When the strategy updates its live snapshot, broadcast an event (e.g., via local WebSocket, ZeroMQ, or Redis pub/sub).
   - UIs subscribe to that channel for real-time updates, avoiding repeated DB reads.

3. **Explicit control channel**
   - Provide a small API/control service (e.g., FastAPI or Textual RPC) that accepts operator commands: close position, rebalance, adjust risk parameters, pause/resume strategy.
   - The service relays commands to the strategy (or a new orchestrator) and returns synchronous/async responses.

4. **Extensible information model**
   - Extend the live snapshot to combine:
     - Position manager data (size, cumulative funding, state).
     - Risk manager outputs (should_exit, reasons).
     - Exchange-level info (current margin/leverage, mark prices) gathered via WebSockets or cached order book feeds.
   - Cache should be updated whenever the strategy polls new funding rates or receives WebSocket events.

5. **Operator UX**
   - Provide a TUI or web UI that can:
     - Display real-time metrics (with streaming updates).
     - Surface risk alerts (e.g., erosion threshold reached).
     - Offer actionable controls (buttons or menu options) that call the control API.
   - UIs fall back to DB snapshots only when the live cache/control channel is unavailable.

---

## 4. Proposed Architecture

```
┌─────────────────────┐       ┌──────────────────────────┐
│ Funding Arb Strategy│       │ Live Snapshot Cache      │
│ • Position manager  │──────▶│ (shared state object)    │
│ • Risk managers     │       │  stores structure from   │
│ • Atomic executor   │       │  dashboard models        │
└────────┬────────────┘       └──────────┬───────────────┘
         │                                 │ publish events
         │ updates                         ▼
         │                         ┌────────────────────┐
         ▼                         │ Event Bus / RPC    │
┌─────────────────────┐            │ (e.g., WebSocket)  │
│ Dashboard Service   │◀───────────│  + Control API     │
│ (in-process)        │ commands   └────────┬───────────┘
│ • Publishes to bus  │                     │ subscribe
│ • Writes periodic   │                     ▼
│   snapshots to DB   │            ┌────────────────────┐
└────────┬────────────┘            │ TUI / Web Clients  │
         │                         │ • Real-time view   │
         ▼                         │ • Position actions │
┌─────────────────────┐            └────────────────────┘
│ PostgreSQL          │
│ • Positions         │
│ • Funding history   │
│ • Periodic snapshots│
└─────────────────────┘
```

### Components
**Live Snapshot Cache**  
Central class (e.g., `DashboardState`) that the strategy updates. It should:
- Track session info, positions, PnL, funding metrics.
- Provide thread-safe reads/writes (async locks or queue).
- Expose a `.model_dump()` returning the same schema used by downstream tools.

**Event Bus / Control Plane**  
Options:
- Simple: `asyncio.Queue` with a local WebSocket server (`websockets` or `FastAPI`/`starlette` WebSocket endpoint).
- Persistent: Redis pub/sub or NATS if multi-process/multi-host is required.
Responsibilities:
- Broadcast snapshot diffs or full snapshots to subscribers.
- Expose RPC endpoints (REST or websocket-based) for commands.
- Authenticate/authorize (if running in production).

**Implementing Controls**  
Define command schema (`ClosePositionCommand`, `AdjustRiskCommand`, etc.). Control handler routes to strategy methods. Strategy acknowledges via event stream (e.g., `PositionClosedEvent`).

**Persistence**  
Continue using PostgreSQL:
- `strategy_positions` / `funding_payments`: unchanged (position manager already writes).
- `dashboard_*`: shift to periodic (e.g., every 30s) or event-driven snapshots (open/close, risk trigger) instead of every tick.
- Possibly add `controller_events` table for command logs if needed.

---

## 5. Implementation Plan

### Phase 1 – Live Snapshot Core
- Create `dashboard/state.py` housing `DashboardState` (mutable object storing latest snapshot, events, session metadata).
- Modify `FundingArbitrageStrategy` to update `DashboardState` whenever positions/rates refresh (instead of directly dumping to DB).  
  - Leave current DB persistence as-is temporarily (for reliability) but gate writes behind a less frequent timer.
- Add unit tests around `DashboardState` update and read semantics.

### Phase 2 – Event Bus & Control API
- Introduce a lightweight async server (FastAPI or bare `asyncio` websockets) that:
  - Serves `GET /snapshot` (return current state).
  - Streams events over WebSocket (`/stream`).
  - Accepts commands (`POST /actions/close_position`, etc.).
- Strategy registers callbacks with this server (e.g., a `Controller` object injected at startup).
- Implement command routing for at least:
  - `ClosePosition(position_id)`
  - `PauseStrategy` / `ResumeStrategy`
  - Possibly `AdjustRiskConfig`.

**Status:** Implemented via `dashboard/control_server.py` (aiohttp) with `/snapshot`, `/stream`, `/commands` and manual close handling in `FundingArbitrageStrategy`.

### Phase 3 – TUI/Web UI Integration
- Update Textual app to connect to the control API:
  - Menu item “Live Monitor” opens a live-updating view (using WebSocket subscription).
  - Commands (buttons/menu) call the control API endpoints and display confirmations.
- Optionally build a basic web interface (FastAPI + `jinja2` or Streamlit) using the same API for operators preferring browser access.

### Phase 4 – Persistence Downshift
- Reduce snapshot DB writes to:
  - On session start/stop.
  - Every N minutes or when significant events occur (position open/close, risk alerts).
- Ensure funding payments and critical metrics still flow to the existing tables (`strategy_positions`, `funding_payments`) for analytics/audit.
- Provide tooling to prune old `dashboard_snapshots` as needed.

### Phase 5 – Extensions
- Multi-strategy support: extend `DashboardState` to track multiple strategies or strategy instances.
- Alerting: integrate with Telegram/Slack when certain risk thresholds are crossed, using the same event bus.
- Historical replay: feed stored snapshots into the event bus for post-mortem analysis.

---

## 6. Open Questions / Considerations

1. **Concurrency & Fault Tolerance**
   - Ensure the control API can’t contradict the strategy’s state (e.g., attempt to close an already pending-close position).
   - Decide whether the event bus/control server runs inside the trading process or as a sidecar service.

2. **Security**
   - For remote operators, secure the control API (authentication, TLS).
   - Provide audit logs for commands.

3. **Data Freshness**
   - Determine acceptable update frequency for live metrics (e.g., every strategy cycle vs. streaming WebSocket updates).
   - When to reconcile snapshot cache with DB (e.g., after restart, reload from DB state first).

4. **Risk Manager Integration**
   - Surface risk manager decisions/events explicitly in the live snapshot (e.g., `last_exit_reason`, `should_exit` flag).
   - Allow operators to override risk manager decisions via control commands.

5. **Testing**
   - Mock event bus/service in integration tests to ensure commands propagate correctly.
   - Provide simulation harness to test close/rebalance flows without hitting real exchanges.

---

## 7. Summary

By introducing a dedicated live state cache, an event-driven control plane, and separating real-time updates from historical persistence, we can build a responsive operator experience without overloading the database. The phased plan lets us evolve the current system incrementally:

1. `DashboardState` for live metrics.
2. Control/event API.
3. TUI/Web UI powered by the API.
4. Reduced DB snapshot churn.
5. Advanced features (multi-strategy, alerts).

This approach aligns with the long-term goal of a first-class CLI/TUI (and eventually web UI) that can *both* monitor and manage positions in real time.
