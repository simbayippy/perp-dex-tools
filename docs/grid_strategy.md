## Grid Strategy — Overview

The grid bot keeps one entry leg in flight at any given time and pairs every
fill with a deterministic exit. The control loop is a two-state machine backed
by idempotent client order IDs so the strategy can recover cleanly from WebSocket
hiccups or post-only cancels.

### Execution Loop

1. **READY → place entry**  
   `GridOpenPositionOperator` reads the latest BBO, applies the configured
   `post_only_tick_multiplier`, and submits a post-only limit order with a
   deterministic client id (`grid-{n}:entry`). The pending ids land in
   `GridState`.
2. **WAITING_FOR_FILL → watch entry**  
   While the order is live the strategy polls `get_active_orders()` and listens
   for WebSocket updates. If Lighter (the only validated venue so far) rejects the
   order immediately with `CANCELED-POST-ONLY`, `_recover_from_canceled_entry()`
   resets the cycle to `READY`.
3. **Fill callback → post exit**  
   When the entry fills, the exchange client invokes the trading bot’s
   `order_fill_callback`, which routes the event to
   `GridOrderCloser.notify_order_filled`. The closer records the price/size,
   allocates `grid-{n}:close`, and submits the paired take-profit order
   (`reduce_only` limit by default, market if boost mode is enabled).
4. **Exit tracking**  
   Accepted exit orders reset the state machine to `READY` so the next entry can
   launch immediately. Each loop prunes finished positions before calling
   `GridOrderCloser.ensure_close_orders()`, which watches for post-only cancels and
   automatically reposts with a wider tick offset. After three failed attempts it
   executes a targeted market close, leaving other tracked exits untouched.

### Deterministic Order Mapping

- `GridState.order_index_to_position_id` binds every client order index to its
  logical `position_id`, which allows fills to be routed even when multiple
  entries are outstanding and lets the fallback logic clean up a single leg
  without disturbing the rest of the book.
- Lighter’s connector now keeps a `client → server` order-id map sourced from
  websocket updates and REST snapshots. The strategy always talks in client ids,
  while the connector translates them for cancel/query operations.
- `TrackedPosition` persists both entry and exit client indices plus retry
  counters so restart recovery and retry logic reuse the same ids.

### Recovery & Safety Nets

- **Post-only entry retry** – If the pending entry disappears before it fills,
  the loop logs the event and returns to `READY`. When a fill arrives the guard
  in `_recover_from_canceled_entry()` cancels the retry path.
- **Close order retry** – Exit orders cancelled for price-crossing are reposted
  with deterministic `close-retry-{n}` keys and a progressively wider buffer.
  After the fixed retry budget, the closer falls back to a market exit scoped
  to the affected position, keeping other tracked exits alive.
- **Recovery modes** – The existing ladder/hedge/aggressive modes remain in
  place for time-based recovery of stuck exits.

### Exchange Support

- **Implemented**: Lighter (post-only enforcement, websocket fills, id mapping).
- **Pending**: Aster and Backpack adapters still need the deterministic id
  plumbing and close-order retry integration. See `docs/grid_todo.md`.

### Related Components

| Component | Responsibility |
| --- | --- |
| `strategies/implementations/grid/operations/open_position.py` | Entry sizing, post-only placement, state initialisation. |
| `strategies/implementations/grid/operations/close_position.py` | Exit submission, post-only retry engine, tracked position management. |
| `exchange_clients/lighter/client.py` | Client/server id translation, websocket fill dispatch, order polling. |
| `trading_bot.py` | Forwards exchange fill callbacks to `strategy.notify_order_filled`. |

### References

- `strategies/implementations/grid/strategy.py` for the READY ↔ WAITING loop.
- `strategies/implementations/grid/models.py` for persisted state structures.
- `tests/strategies/grid/test_grid_strategy_unit.py` for focused unit coverage
  of fill handling, retries, and recovery guards.
