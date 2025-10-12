# 🖥️ Terminal Dashboard Initiative

**Status:** Draft Proposal  
**Audience:** Strategy & Execution Engineers  
**Last Updated:** 2025-10-12

---

## 1. Context & Motivation

- Funding arbitrage runs via `python runbot.py --config ...` on Ubuntu VPS instances over SSH.  
- Current output relies on verbose structured logs (stage banners via `helpers.unified_logger`).  
- Once a position is open, continuous log spam obscures high-signal metrics (PnL, funding fees, divergence).  
- We want an **in-place, live dashboard** while keeping rich logs for debugging and future extensibility to other strategies.

---

## 2. Experience Goals

- **Single-process workflow:** Operator starts the bot and immediately sees a live dashboard without launching extra services.  
- **At-a-glance situational awareness:** Snapshot of session status, current position(s), PnL, funding accrual, lifecycle stage.  
- **Low-friction recovery:** Ability to reconnect (e.g., via `tmux`/`screen`) and inspect the latest state after hours/days.  
- **Extensible design:** Dashboard should support multiple positions, different strategies, and alternative render targets later (web/desktop).  
- **Graceful degradation:** Fall back to pure logging when terminal capabilities are limited (non-TTY, redirected stdout, CI).  
- **Persistence:** Retain historical snapshots for post-mortem analysis or delayed review.

---

## 3. Deployment Environment Assumptions

- **Runtime:** Python 3.12, async event loop already driving the trading bot.  
- **Terminal:** SSH sessions on Ubuntu; color-capable terminals (e.g., `xterm`, `tmux`, `wezterm`).  
- **Libraries:** We can add dependencies (e.g., `rich` or `textual`) as long as they are optional and gated behind config.  
- **Database:** PostgreSQL already provisioned via `funding_rate_service/database/__init__.py`.  
- **Process Model:** Bot runs as a foreground process; operators may detach using `tmux` but we shouldn’t require a second process.

---

## 4. Functional Requirements (Initial Scope)

### 4.1 Session Overview
- Strategy name / version, config file, runtime duration.  
- Exchange connectivity status, session limits (max positions, exposure).  
- Lifecycle stage (e.g., “Monitoring Position”, “Scanning”, “Exiting”, “Idle”).

### 4.2 Position Table
- Position ID, symbol, side pairing (e.g., lighter long / aster short).  
- Entry timestamps and prices, target exposure, filled exposure.  
- Live mark prices (per venue), divergence %, profit erosion %.  
- Unrealized PnL (per leg and net), realized PnL, aggregate fees/funding earned.  
- Margin usage / leverage per venue, rebalance flags, aging information.

### 4.3 Funding Stream
- Latest funding rates for involved venues.  
- Accrued funding payments (actual vs expected).  
- Countdown to next funding event.

### 4.4 Event Timeline
- Condensed list of recent actions (opportunity found, orders placed, fills, errors/warnings).  
- Stage transitions with timestamps (mirrors `unified_logger` banners).

### 4.5 Portfolio Summary (Extensibility)
- Total notional, total realized/unrealized PnL across all positions.  
- Risk metrics hook (VAR placeholder, maintenance margin headroom).  
- Roll-up per strategy when multiple strategies share the dashboard.

---

## 5. Non-Functional Requirements

- **Refresh cadence:** ~1s tick when active; slower (5–10s) when idle to avoid flicker.  
- **Resource footprint:** Minimal CPU (render diffing rather than full redraws where possible).  
- **Resilience:** Dashboard must not block or slow strategy execution; failures should degrade to logging.  
- **Testing:** Unit tests for snapshot serialization; integration tests with mocked strategy events; manual verification over SSH.  
- **Configurability:** Toggle via config flag or CLI option (`--dashboard-mode=live|silent`).  
- **Multi-position readiness:** Structured data model that scales beyond “one position per session.”

---

## 6. High-Level Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                   Trading Bot Event Loop                     │
│  (strategies, position manager, execution services)          │
└───────────────┬──────────────────────────────────────────────┘
                │ publishes snapshots/events
                ▼
┌──────────────────────────────────────────────────────────────┐
│        Dashboard Aggregator (new)                            │
│  • Collects structured snapshots & events                    │
│  • Maintains latest session/position state in-memory         │
│  • Persists snapshots for historical replay (PostgreSQL)     │
│  • Exposes async feed to renderer(s)                         │
└───────────────┬─────────────────────────────┬────────────────┘
                │                             │
                │                             │ optional future
                ▼                             ▼
┌────────────────────────────────┐   ┌─────────────────────────┐
│ Terminal Renderer (Rich-based) │   │ External Consumers      │
│ • Runs in same process         │   │ • REST/Web UI (future)  │
│ • Handles live layout refresh  │   │ • CLI replay script     │
└────────────────────────────────┘   └─────────────────────────┘
```

### 6.1 Snapshot Publisher
- New lightweight interface (e.g., `DashboardPublisher.publish(snapshot: DashboardSnapshot)`).
- Initial producers: `FundingArbitrageStrategy`, `PositionManager`, `AtomicMultiOrderExecutor`.
- Emits granular updates (position fills, funding rate refresh) plus periodic heartbeat with current metrics.

### 6.2 Aggregator
- Async task hosted by strategy to collect snapshots via `asyncio.Queue`.  
- Deduplicates noise (e.g., unchanged prices) and computes derivatives (PnL deltas, time since event).  
- Persists snapshots every N seconds or on meaningful change using PostgreSQL (new table group, see §8).  
- Exposes latest state to renderer via async iterator or shared dataclass protected by `asyncio.Lock`.

### 6.3 Renderer
- Prefers `rich`’s `Layout` + `Live` to draw panes (works well in SSH).  
- Supports fallback `PlainTextRenderer` when `rich` is unavailable or output is non-interactive.  
- Handles resize and TTY checks; can be disabled entirely.

### 6.4 Persistence Layer
- Append snapshots/events to PostgreSQL for long-term access (`dashboard_session`, `dashboard_snapshot`, `dashboard_event`).  
- Allow optional JSONL writer for local debugging and easier export.  
- Provide CLI utility `python runbot.py --dashboard-replay session_id` to inspect past sessions.

### 6.5 Terminal Renderer Strategy
- **Primary renderer:** `Rich` Live + Layout for multi-pane output (session header, position table, funding panel, timeline).  
- **Refresh loop:** Dedicated async task consuming snapshot queue; redraw only when snapshot hash changes or heartbeat interval elapses.  
- **TTY detection:** If `sys.stdout.isatty()` is false or Rich import fails, fall back to a minimal plain-text renderer that emits periodic summaries without cursor control.  
- **Graceful exit:** Renderer listens for cancellation signals and restores terminal state (cursor visibility, cleared footers).  
- **Config knobs:** `refresh_interval`, `max_event_rows`, color theme (light/dark), ability to freeze panes for debugging.

---

## 7. Data Model Draft

```python
class DashboardSnapshot(BaseModel):
    session: SessionState
    positions: list[PositionSnapshot]
    funding: FundingSnapshot
    portfolio: PortfolioSnapshot
    recent_events: list[TimelineEvent]  # bounded (e.g., last 20)
    generated_at: datetime

class PositionSnapshot(BaseModel):
    position_id: UUID
    symbol: str
    venues: dict[str, VenueLeg]  # e.g., {"lighter": LegDetails, "aster": ...}
    entry_ts: datetime
    last_update_ts: datetime
    exposure_usd: Decimal
    mark_prices: dict[str, Decimal]
    pnl_unrealized: Decimal
    pnl_realized: Decimal
    funding_accrued: Decimal
    profit_erosion_pct: Decimal
    leverage: dict[str, Decimal]
    rebalance_pending: bool
    lifecycle_stage: str  # e.g., "Open", "Closing"

class TimelineEvent(BaseModel):
    ts: datetime
    category: Literal["stage", "execution", "warning", "info"]
    message: str
    metadata: dict[str, Any] = {}
```

> **Extensibility Note:** Additional fields can be added per strategy by extending the models (Pydantic `Config` with `extra="allow"`) or via strategy-specific payload slice.

---

## 8. Database Integration (PostgreSQL)

- Add new schema objects under `funding_rate_service/database` (see draft DDL below).  
- Reuse existing async engine/session factory for writes via new repositories.  
- Provide retention policy (e.g., keep last N sessions or 30 days).  
- Allow toggling persistence to avoid overhead on resource-constrained runs.

```sql
CREATE TABLE dashboard_sessions (
    session_id UUID PRIMARY KEY,
    strategy VARCHAR(64) NOT NULL,
    config_path TEXT,
    started_at TIMESTAMP NOT NULL,
    ended_at TIMESTAMP,
    health VARCHAR(16) NOT NULL,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE dashboard_snapshots (
    id BIGSERIAL PRIMARY KEY,
    session_id UUID REFERENCES dashboard_sessions(session_id) ON DELETE CASCADE,
    generated_at TIMESTAMP NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_dashboard_snapshots_session_time
    ON dashboard_snapshots (session_id, generated_at DESC);

CREATE TABLE dashboard_events (
    id BIGSERIAL PRIMARY KEY,
    session_id UUID REFERENCES dashboard_sessions(session_id) ON DELETE CASCADE,
    ts TIMESTAMP NOT NULL,
    category VARCHAR(16) NOT NULL,
    message TEXT NOT NULL,
    metadata JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX idx_dashboard_events_session_time
    ON dashboard_events (session_id, ts DESC);
```

**Integration points**

- New repository module (`funding_rate_service/database/repositories/dashboard.py`) encapsulates CRUD with typed helpers.  
- Aggregator opens/updates a `dashboard_sessions` record at start/stop; snapshots/events append during runtime.  
- Replay tooling consumes the same tables for historical inspection.

---

## 9. Integration Points

1. **Configuration:**  
   - Extend bot config schema (`configs/*.yml`) with `dashboard:` block (`enabled`, `renderer`, `refresh_interval_s`, `persistence`).  
   - CLI argument `--dashboard-mode` overrides config.

2. **Strategy Lifecycle:**  
   - Strategy initialization instantiates `DashboardService`.  
   - Hook inside `_open_position`, `_monitor_position`, `_close_position` to publish snapshots/events.  
   - Position manager publishes updates on state changes (rebalance flags, funding payments).

3. **Execution Pipeline:**  
   - Atomic multi-order executor emits concise events for pre-flight success, fills, rollbacks.  
   - Market data/funding fetchers publish rates to the dashboard.

4. **Logging Coexistence:**  
   - Keep `helpers/unified_logger` stage banners (they also feed timeline events).  
   - Dashboard renderer runs on separate async task to avoid blocking log writes.

---

## 10. Implementation Roadmap

### Phase 0 – Research & Spike
- Validate `rich` rendering over SSH (mock snapshots).  
- Draft Pydantic models; ensure they serialize cleanly.

### Phase 1 – Infrastructure
- Build `dashboard` module with publisher, aggregator, renderer abstractions.  
- Wire feature flag/config parsing.  
- Implement minimal terminal renderer (session + single-position table).  
- Add PostgreSQL tables + repository helpers.

### Phase 2 – Funding Arb Integration
- Emit real snapshots from funding arbitrage strategy + position manager.  
- Populate funding stream & event timeline from existing data sources.  
- Persist snapshots/events during live run and confirm replay CLI.

### Phase 3 – UX Refinement
- Add color-coded thresholds, multi-position layout, scrollable event feed.  
- Optimize refresh cadence and diffing to reduce flicker.  
- Introduce `plain` renderer fallback and JSONL export.

### Phase 4 – Extensibility
- Document integration guide for new strategies.  
- Expose read-only API to other processes (future web dashboard).  
- Consider alerting hooks (email/Slack) driven by aggregator state.

---

## 11. Risks & Mitigations

- **Terminal compatibility:** Detect non-interactive sessions and disable renderer automatically.  
- **Performance overhead:** Keep snapshot production lightweight; throttle publishing when updates are unchanged.  
- **Data consistency:** Use versioned snapshot schema and migration plan (e.g., Alembic) for DB tables.  
- **Operator reliance on logs:** Provide config to maintain existing log verbosity alongside dashboard.  
- **Long-term storage growth:** Implement retention policies and optional pruning command.

---

## 12. Open Questions

1. Should we expose a separate CLI command to replay historical sessions without running the bot?  
2. How granular should persistence be (every snapshot vs. significant changes only)?  
3. Do we need multi-user read access (e.g., read-only web view) in the near term?  
4. Should funding fee accrual be reconciled against on-chain settlements in dashboard metrics?  
5. How will we integrate strategies that already stream their own metrics (avoid double counting)?

---

## 13. Next Actions

1. Approve high-level design and decide on baseline renderer dependency (`rich` vs. minimal curses).  
2. Define snapshot schema formally (Pydantic models + validation).  
3. Implement Phase 0 spike to confirm rendering + persistence approach.  
4. Update `docs/ARCHITECTURE.md` once the dashboard module is implemented (add to Layer 3 tooling section).

---

**Appendix:** _Reference Materials_  
- `docs/ARCHITECTURE.md` – overall system layout.  
- `helpers/unified_logger.py` – structured stage logging (feeds timeline).  
- `cli_display_hummingbot_reference/` – inspiration for terminal layouts.  
- `funding_rate_service/database/` – existing PostgreSQL integration utilities.
