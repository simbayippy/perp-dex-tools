# Dashboard Rearchitecture Task List

**Status Tracking for the Dashboard & Control Plane Roadmap**

---

## Phase 1 – Live Snapshot Core
- [x] Design `DashboardState` data structure (fields, thread-safety).
- [x] Implement `dashboard/state.py` (in-process cache + async lock).
- [x] Wire `FundingArbitrageStrategy` to update `DashboardState` whenever:
  - Positions open/close.
  - `_monitor_positions` refreshes divergence/PnL/erosion.
  - Risk manager evaluations trigger alerts.
- [ ] Add optional timer to periodically flush summary snapshots to DB (replace per-cycle writes).
- [ ] Unit tests for state updates & read consistency (mock position manager).
- [ ] Update documentation (strategy internals section) to describe the live state cache.

## Phase 2 – Event Bus & Control API
- [x] Choose transport (local WebSocket via `aiohttp`).
- [x] Implement event publisher that emits snapshot diffs or full payload on state change.
- [x] Expose REST/WebSocket endpoints:
  - `GET /snapshot`
  - `WS /stream` (stream state updates)
- [ ] Define control commands schema (`ClosePosition`, `Ping`). *(Deferred)*
- [ ] Implement command handler inside strategy (invoked via controller). *(Deferred)*
- [ ] Confirm command execution updates `DashboardState` and emits confirmation events. *(Deferred)*
- [x] Integration tests for command flow (mock exchanges & position manager).

- [ ] Update Textual TUI to:
-  - [x] Subscribe to live stream.
-  - [x] Render updates w/o rereading DB.
-  - [ ] Trigger commands via control API. *(Deferred)*
- [ ] Optionally add minimal web dashboard (FastAPI template or Streamlit) using same endpoints.
- [ ] Document operator workflow for new tooling (CLI & web).

## Phase 4 – Persistence Cleanup
- [ ] Reduce snapshot DB writes to significant events/timer.
- [ ] Ensure `strategy_positions`/`funding_payments` remain accurate points of record.
- [ ] Optionally archive/prune historical `dashboard_snapshots`.

## Phase 5 – Extensions (Optional / Future)
- [ ] Multi-strategy support (extend state cache to handle multiple strategies).
- [ ] Slack/Telegram alerts using event bus.
- [ ] Historical replay mode (feed stored snapshots into stream for analysis).

---

**Progress Notes:**  
- Keep this checklist updated as each task completes.  
- Cross-reference with `docs/dashboard_rearchitecture_plan.md` for rationale/details.
