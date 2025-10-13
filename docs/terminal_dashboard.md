# Terminal Dashboard Guide

**Audience:** Operators, strategy engineers, tooling contributors  
**Applies to:** `funding_arbitrage` strategy (dashboard-enabled builds)  
**Last Updated:** 2025-10-12

---

## 1. Overview

The terminal dashboard offers a live, in-place view of strategy state while `python runbot.py` is running. It supplements the existing log stream with concise tables and status panels, and persists structured snapshots to PostgreSQL so sessions can be inspected after the fact.

- **Live UI:** Renders a Rich layout (or plain-text fallback) in the same SSH session as the bot, refreshing automatically.
- **Structured state:** Publishes session/position/funding metrics as `DashboardSnapshot` models; stores them in the `dashboard_*` tables.
- **Event feed:** Timeline panel surfaces notable lifecycle events (stage changes, fills, warnings) for quick diagnostics.

This guide explains the runtime architecture, the snapshot/event model, configuration controls, and operational validation steps.

---

## 2. Architecture

```
┌────────────────────────────┐
│ Trading Bot (event loop)   │
│  • Strategy execution      │
│  • Position + funding data │
└──────────────┬─────────────┘
               │ publish()
               ▼
┌────────────────────────────┐
│ DashboardService           │
│  • Receives snapshots/events
│  • Updates session heartbeat
│  • Persists to PostgreSQL   │
│  • Dispatches to renderer   │
└──────────────┬─────────────┘
               │ render()
               ▼
┌────────────────────────────┐
│ Terminal Renderer          │
│  • Rich layout (default)   │
│  • Plain-text fallback     │
│  • No-op if non-TTY        │
└────────────────────────────┘
```

### 2.1 Snapshot Producers

The `FundingArbitrageStrategy` gathers live metrics from:
- Position manager (`FundingArbPositionManager`): open positions, funding accrual, rebalance flags.
- Execution layer: entry fills, slippage, divergence.
- Funding data repository: current divergence and funding rates.

It then builds a `DashboardSnapshot` (see §3) and publishes it via `DashboardService.publish_snapshot()`.

### 2.2 DashboardService Responsibilities

`dashboard/service.py` orchestrates three concerns:
1. **Session state:** Maintains a `SessionState` object (stage, heartbeat, metadata). Updates heartbeat on every snapshot.
2. **Persistence:** Writes to PostgreSQL when persistence is enabled:
   - `dashboard_sessions`: one row per bot run (session id, config path, started/ended timestamps).
   - `dashboard_snapshots`: JSON payloads containing the serialized snapshot.
   - `dashboard_events`: timeline entries (stage transitions, warnings, etc.).
3. **Rendering:** Instantiates the configured renderer (Rich or plain-text). Renderer can be omitted—if stdout is non-TTY or import fails, the service logs a warning and continues without UI.

### 2.3 Renderers

Located in `dashboard/renderers/`:

- `RichDashboardRenderer`:
  - Uses `rich.live.Live` with a layout of three panes (session header, body split for positions/funding, event footer).
  - Requires a color-capable TTY (SSH/tmux is fine); automatically disables itself if `Console.is_terminal` is false.
  - Refresh cadence is driven by `dashboard.refresh_interval_seconds`.

- `PlainTextDashboardRenderer`:
  - Logs single-line summaries (timestamp, lifecycle stage, aggregate metrics) for non-TTY environments or minimal setups.

If the renderer fails to import or initialize, the service falls back to log output only, preserving safety.

---

## 3. Data Model

Defined in `dashboard/models.py` using Pydantic v2:

### 3.1 SessionState
- `session_id`: UUID for the bot run.
- `strategy`: Strategy identifier (`funding_arbitrage`).
- `config_path`: Path to the YAML config (if available).
- `started_at`, `last_heartbeat`: Timestamps.
- `health`: `SessionHealth` (`starting`, `running`, `idle`, `degraded`, `error`, `stopping`, `stopped`).
- `lifecycle_stage`: `LifecycleStage` enum (e.g., `opening_position`, `monitoring_position`).
- `metadata`: Arbitrary JSON (e.g., list of trading-capable exchanges).

### 3.2 PositionSnapshot
- `position_id`: ID from `strategy_positions`.
- `symbol`, `strategy_tag`.
- `opened_at`, `last_update`.
- `legs`: list of `PositionLegSnapshot` (per venue), capturing side, quantity, entry/mark prices, leverage, funding accrued, fees.
- Aggregates: `notional_exposure_usd`, `unrealized_pnl`, `funding_accrued`, `profit_erosion_pct`, `rebalance_pending`.

### 3.3 FundingSnapshot
- `total_accrued`: Sum of funding across all positions.
- `weighted_average_rate`: Exposure-weighted funding rate across venues (optional).
- `rates`: List of `FundingRateSnapshot` (per venue: current rate, next rate/time, accrued).
- `next_event_countdown_seconds`: Time until next funding event (optional).

### 3.4 PortfolioSnapshot
- `total_positions`, `total_notional_usd`.
- `net_unrealized_pnl`, `net_realized_pnl`.
- `funding_accrued`, `free_collateral_usd`, `maintenance_margin_ratio`.
- `alerts`: List of portfolio-level warnings (strings).

### 3.5 TimelineEvent
- `ts`: Event timestamp.
- `category`: `TimelineCategory` (`stage`, `execution`, `funding`, `risk`, `warning`, `error`, `info`).
- `message`: Human-readable string.
- `metadata`: Additional structured info (dict).

### 3.6 DashboardSnapshot
- `session`: `SessionState`.
- `positions`: List of `PositionSnapshot` (sorted by `last_update` desc).
- `portfolio`: `PortfolioSnapshot`.
- `funding`: `FundingSnapshot`.
- `recent_events`: Most recent events (typically capped ~12 entries by renderer).
- `generated_at`: Timestamp when the snapshot was assembled.

`DashboardSnapshot.model_dump(mode="json")` is stored in `dashboard_snapshots.payload`.

---

## 4. Configuration

### 4.1 YAML Configuration

Enable the dashboard in the strategy config (e.g., `configs/real_funding_test.yml`):

```yaml
config:
  ...
  dashboard:
    enabled: true
    renderer: rich        # options: 'rich', 'plain'
    refresh_interval_seconds: 1.0
    persist_snapshots: true
    snapshot_retention: 500
    event_retention: 200
```

> **Note:** Keys under `dashboard` mirror `DashboardSettings` in `dashboard/config.py`. `FundingArbConfig` now hydrates this block automatically when loading from YAML.

### 4.2 Runtime Flags

- `enabled`: Master switch; if false, no snapshots/events are published, renderers are never instantiated.
- `renderer`: Choose between the Rich UI or plain log output. Unsupported values fall back to logs with an informational message.
- `refresh_interval_seconds`: Controls UI update cadence; renderer internally bounds this to ≥0.1s and computes `refresh_per_second` accordingly.
- `persist_snapshots`: When false, the dashboard operates in-memory only; no database writes occur.
- `snapshot_retention`, `event_retention`: After each insert, the service prunes older rows beyond these counts (per session).
- `write_interval_seconds`: Minimum seconds between persisted snapshots (throttling DB writes on steady snapshots).

### 4.3 Strategy Hooks

`FundingArbitrageStrategy` now:
- Instantiates `DashboardService` during initialization (after position/state managers are ready).
- Throws a warning if persistence is requested but the funding-rate service DB isn’t importable.
- Publishes snapshots on lifecycle transitions (`_monitor_positions`, `_open_position`, `_close_position`, etc.).
- Emits timeline events for stage changes and notable actions (e.g., “Position opened ZORA”, “Session limit reached”).

---

## 5. Persistence & Database Schema

Migrated via `funding_rate_service/database/migrations/005_create_dashboard_tables.sql`:

- **`dashboard_sessions`**
  - `session_id` (UUID, PK)
  - `strategy`, `config_path`
  - `started_at`, `ended_at`
  - `health`
  - `metadata` (JSONB)

- **`dashboard_snapshots`**
  - `id` (BIGSERIAL)
  - `session_id` (FK → sessions, cascade delete)
  - `generated_at`
  - `payload` (JSONB)
  - Index on `(session_id, generated_at DESC)`

- **`dashboard_events`**
  - `id` (BIGSERIAL)
  - `session_id` (FK → sessions, cascade delete)
  - `ts`
  - `category`
  - `message`
  - `metadata` (JSONB)
  - Index on `(session_id, ts DESC)`

### Running the Migration

```
python funding_rate_service/scripts/run_migration.py funding_rate_service/database/migrations/005_create_dashboard_tables.sql
```

(The script now prepends the project root to `sys.path`, so no extra `PYTHONPATH` is required.)

---

## 6. Dashboard Events

**What counts as a dashboard event?**

Any notable lifecycle update that should surface in the timeline panel:
- Stage transitions (e.g., `monitoring_position → scanning`).
- Execution milestones (position opened/closed, rollback performed).
- Warnings or errors (e.g., rollback cost, funding fetch failure).
- Funding-specific updates (collected payments, erosion thresholds).

Events are created by calling `DashboardService.publish_event()` with a `TimelineEvent`. Currently, the strategy emits:
- Stage-change events (during `execute_strategy` cycle).
- Execution summaries when positions open/close.
- Capacity warnings (once per session).

Future enhancements should expand this to include:
- Funding payment receipts (triggered by position manager).
- Divergence threshold crossings.
- Account-monitor alerts (margin warnings).

---

## 7. Runtime Expectations

### 7.1 Terminal Rendering

- When `renderer: rich`:
  - The first frame prints “Dashboard activated…”.
  - UI updates every `refresh_interval_seconds`.
  - If stdout is not a TTY, a warning prints once and the dashboard silently disables itself (no persistence/renderer interruption).

- When `renderer: plain`:
  - A summary line appears for each snapshot (`[dashboard] HH:MM:SS | stage=… | positions=…`).

- When renderer cannot start:
  - The bot logs a warning and continues with normal logs; snapshots/events still persist if enabled.

### 7.2 Snapshot Cadence

- `publish_snapshot()` is called:
  - After stage transitions within `execute_strategy`.
  - When positions open/close.
  - After `_monitor_positions()` updates (typically once per cycle).
  - Additional hooks can be added as needed (e.g., funding payment receipts).

- Persistence throttle: snapshots are only written to DB if `write_interval_seconds` has elapsed since the last persisted snapshot (default 5s).

### 7.3 Database Verification

After a run (with `dashboard.enabled: true`):

```bash
# Recent sessions
PGPASSWORD='…' psql -h localhost -U funding_user -d funding_rates \
  -c "SELECT session_id, strategy, started_at, health FROM dashboard_sessions ORDER BY started_at DESC LIMIT 5;"

# Latest snapshots
PGPASSWORD='…' psql -h localhost -U funding_user -d funding_rates \
  -c "SELECT id, session_id, generated_at FROM dashboard_snapshots ORDER BY generated_at DESC LIMIT 5;"

# Timeline events
PGPASSWORD='…' psql -h localhost -U funding_user -d funding_rates \
  -c "SELECT ts, category, message FROM dashboard_events ORDER BY ts DESC LIMIT 10;"
```

Use `payload` JSON to inspect raw snapshots for debugging:

```sql
SELECT payload->'positions' AS positions
FROM dashboard_snapshots
WHERE session_id = '<uuid>'
ORDER BY generated_at DESC
LIMIT 1;
```

---

## 8. Operational Checklist

1. **Install dependencies**  
   Ensure `rich` is installed in the virtualenv (`pip install rich`).

2. **Apply database migration**  
   Run migration `005_create_dashboard_tables.sql` on the funding-rate service DB.

3. **Enable dashboard in config**  
   Add the `dashboard:` block under `config:` in your YAML file.

4. **Run the bot**  
   `python runbot.py --config configs/your_config.yml` inside an SSH/Tmux TTY.

5. **Validate UI**  
   - If using Rich renderer, you should see the live layout update.
   - If not visible, check for warnings in logs (non-TTY fallback).

6. **Verify persistence**  
   Query `dashboard_sessions` and related tables to confirm rows were inserted.

7. **Inspect logs**  
   Stage transitions and capacity warnings now appear once per session (events also logged in timeline).

8. **Troubleshoot**  
   - If no snapshots are stored: confirm `dashboard.enabled: true` and migration succeeded.
   - If renderer doesn’t appear: check `console.is_terminal` (run inside a proper terminal) or fallback to `renderer: plain`.
   - If funding service modules missing: the strategy logs a warning and disables persistence; ensure `funding_rate_service` is installed and accessible.

---

## 9. Future Enhancements

- Expand event coverage (funding payments, rebalance triggers, account monitor alerts).
- Add replay tooling (`python runbot.py --dashboard-replay <session_id>`) to inspect past sessions without re-running the bot.
- Provide a REST/graphical viewer that reads `dashboard_sessions` and streams snapshots.
- Integrate multi-strategy support (dashboard aggregating several strategies simultaneously).
- Offer JSON export hooks for alerting systems (e.g., push to Slack when funding erosion passes threshold).

---

## 11. Standalone Viewer (Step 2)

When the in-process dashboard is disabled, you can inspect snapshots using the standalone viewer:

```bash
python scripts/dashboard_viewer.py            # render most recent session
python scripts/dashboard_viewer.py --session-id <uuid>
python scripts/dashboard_viewer.py --events 20
```

- The viewer connects to the same PostgreSQL database (`funding_rate_service` settings).
- By default it selects the latest `dashboard_session` and prints the newest snapshot plus recent events.
- Use `--session-id <uuid>` to target a specific run or `--events N` to change the number of timeline entries displayed.
- Output uses Rich tables when run in a TTY; otherwise it falls back to plain text automatically (Rich handles the detection).

This external viewer keeps the trading terminal clean while still providing an up-to-date summary. It’s the first step toward the menu-driven CLI/TUI described in the roadmap.

---

## 10. FAQ

**Q: What happens if the renderer crashes?**  
A: `DashboardService` wraps renderer calls; any exception disables the renderer with a warning, but snapshot persistence continues.

**Q: Does the dashboard replace existing logs?**  
A: No. The unified logger still prints stage banners; only repetitive capacity logs were throttled to reduce noise.

**Q: Can I run the dashboard headlessly?**  
A: Yes. Set `renderer: plain` to emit textual summaries, or disable the dashboard entirely (`enabled: false`) to run with no overhead.

**Q: How do I clean up old sessions?**  
A: Use SQL `DELETE` statements or add a cron job to remove rows older than N days. The built-in retention keeps only the most recent snapshots/events per session.

---

For deeper architectural context, see `docs/ARCHITECTURE.md` and `docs/terminal_dashboard_plan.md`. Contributions welcome—ensure new features extend the Pydantic models and renderer interfaces in a backwards-compatible manner.
