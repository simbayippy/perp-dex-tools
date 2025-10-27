## Grid Strategy – Deterministic Client IDs

The grid strategy now keeps a hard link between every entry order, its fills, and
the paired exit order by assigning deterministic client order IDs. This removes
the earlier dependency on a single “waiting for fill” state and survives
post-only leaks or replays.

### Summary

- When the strategy allocates `position_id` (e.g. `grid-7`) it derives a client
  order index via `client_order_index_from_position(position_id, "entry")`. The
  index is short (default `mod 1_000_000`) to satisfy exchanges such as Lighter.
- `GridState.order_index_to_position_id` records the mapping so callbacks can
  jump directly to the correct position regardless of how many entries are
  outstanding.
- Fill callbacks now carry the exchange order ID all the way through
  (`trading_bot._handle_order_fill` → `GridStrategy.notify_order_filled` →
  `GridOrderCloser.notify_order_filled`). The closer verifies that the ID matches
  the pending position, gracefully ignoring unrelated fills, and realigns if the
  strategy is out of sync.
- The paired exit limit (or market) order receives its own deterministic client
  ID (`client_order_index_from_position(position_id, "close")`); tracked positions
  store both entry and exit IDs for recovery/resubmission bookkeeping. When
  post-only cancellations occur the strategy derives retry IDs
  (`close-retry-1`, `close-retry-2`, …) so every repost stays idempotent; after
  a small fixed retry budget it optionally falls back to a market exit to avoid
  dangling exposure.
- Stop-loss and recovery flows clear the mapping so stale IDs never accumulate.

### Exchange Support

- **Lighter** – `place_limit_order` / `place_market_order` accept the optional
  `client_order_id`. Websocket updates echo the same ID in `client_order_index`,
  so fills are resolved without searching.
- **Aster** – the REST API takes `newClientOrderId`; the client now passes it
  whenever we supply an idempotent key.
- **Backpack** – accepts `client_order_id` via the SDK helper. Our client passes
  the ID when provided by the strategy.

### State & Persistence

- `GridState` persists the pending/filled client IDs plus the `order_index_to_position_id`
  map so restarts keep the pairing intact.
- `TrackedPosition` records `entry_client_order_index` and
  `close_client_order_indices`, which helps recovery operations reuse the same IDs
  during ladder or hedge attempts.

### Call Flow Sketch

1. `GridOpenPositionOperator.place_open_order`
   - allocate `position_id`
   - derive entry client ID, store in state and mapping
   - send limit order with `client_order_id`
2. `GridStrategy.notify_order_filled(price, qty, order_id)`
   - closer validates the order ID, resolves the position, and places the paired
     exit order with a deterministic close ID
3. `GridOrderCloser.handle_filled_order`
   - creates/updates `TrackedPosition` with both IDs
4. Exit fills follow the same path, using the close ID to find the tracked
   position. Recovery or stop-loss clears the mapping when the position ends.

### Tests

- Unit and integration tests now hydrate the new fields (e.g. supplying the
  order ID to `strategy.notify_order_filled`) so the deterministic keys are
  exercised in CI.
