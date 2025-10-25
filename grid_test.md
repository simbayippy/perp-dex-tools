• Testing Landscape

  - State Models: GridState, GridOrder, TrackedPosition
  - Strategy Core: GridStrategy
      - Risk hooks: _prepare_risk_snapshot, _enforce_stop_loss, _check_risk_limits
      - Execution: _place_open_order, _handle_filled_order, should_execute, execute_strategy
      - Recovery: _run_recovery_checks, _recover_position, _cancel_orders, _place_ladder_orders, _place_hedge_order
      - Housekeeping: _update_active_orders, _meet_grid_step_condition, _cancel_all_orders, get_status
      - Telemetry: _log_event, integration with GridEventNotifier
  - Notifier: helpers/event_notifier.GridEventNotifier

  Unit Test Plan

  1. State Serialization
      - GridState.to_dict/from_dict
      - TrackedPosition.to_dict/from_dict
      - GridOrder helpers
        (ensures restart persistence works and tracked positions survive round-trips)
  2. Margin / Position Limits
      - _check_risk_limits: blocking when limits exceeded, allowing otherwise; verify position_cap_hit/margin_cap_hit events emitted with correct payloads; both margin ratio and no-ratio paths.
  3. Stop Loss
      - _enforce_stop_loss: triggers when price breaches thresholds for long/short, respects cooldown, no-op when disabled or entry missing; ensures _close_position_market called; event sequence
        (stop_loss_initiated, stop_loss_executed/stop_loss_failed) emitted.
  4. Risk Snapshot
      - _prepare_risk_snapshot: handles exchange snapshot with/without margin/exposure, falls back when API exceptions; ensures state cache updated/reset.
  5. Order Placement
      - _place_open_order: respects quantity validation, honors risk gating, correct price rounding, uses limit order result statuses; ensures tracked state resets.
  6. Close Order Handling
      - _handle_filled_order: correct side/price calc, tracked-position creation when close order not filled, booster path (market order). Emits position_tracked event.
  7. Recovery Engine
      - _run_recovery_checks: detection timing, cooldown, pruning closed positions.
      - _recover_position: each mode (aggressive ladder hedge none) ensures correct downstream calls + events.
      - _place_ladder_orders: tiered prices, success/failure logging, ensures fallback when zero orders placed.
      - _place_hedge_order: success + rejection coverage.
      - _cancel_orders: handles per-order failures gracefully.
  8. Grid Step/Wait Logic
      - _calculate_wait_time and _meet_grid_step_condition: spacing logic, order count transitions.
  9. Telemetry
      - _log_event: serialization, event notifier invocation; ensure no crash with decimals/lists.
      - GridEventNotifier.notify: files appended, telegram gated by env, tolerant to IO/network failures.
  10. Status Reporting
      - get_status: includes new risk metrics, handles exceptions.

  Integration / Async Tests

  1. Happy Path Cycle
      - Mock exchange client (async methods) and feed sequence: allow open order → mark filled → place close order → no recovery trigger; assert state transitions, logs, events, tracked positions
        cleared once close filled.
  2. Margin Exhaustion
      - Preload state to simulate near-cap margin; verify should_execute returns False and event recorded when next placement projected to exceed.
  3. Stop Loss Execution
      - For each mode, craft scenario with stuck position (lingering close order) and confirm appropriate orders issued, events logged, tracked positions updated.
  5. Environment Variations
      - Absence of Telegram credentials: notifier writes JSONL without external calls.
      - Missing balance/snapshot APIs (exchange raises errors): strategy degrades gracefully, events capture errors.
  6. Restart Persistence
      - Create GridState, serialize/deserialize with tracked positions, ensure no data loss.

  Edge Cases

  - Zero quantity / invalid config parameters (caught early).
  - Hedged positions (“hedged” flag), ensure _prune_tracked_positions respects.

  This plan gives broad coverage: deterministic unit tests for each branch and async integrations validating real flow sequences, ensuring confidence before risking capital.