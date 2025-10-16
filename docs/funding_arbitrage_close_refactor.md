# Funding Arb Close Flow Refactor Notes

Date: 2025-10-15  
Author: Codex (analysis partner)

## Context & Current Behaviour
- `PositionMonitor.monitor()` gathers live `ExchangePositionSnapshot`s for each leg and updates DB metadata, but it never touches order flow.
- `PositionCloser._close_exchange_positions()` expects every exchange client to expose `close_position(symbol)`. Test stubs implement it; real clients (e.g., `exchange_clients/aster/client.py`) do not, so the call fails before any order is submitted.
- Opening legs already uses `AtomicMultiOrderExecutor` + `OrderSpec`, which provides limit offsets, fallback handling, and rollback logic. Closing legs bypass that machinery, so we lose parity in controls (execution mode, timeouts, coordinated two-leg unwind, etc.).
- Exchange clients expose primitives (`place_limit_order`, `place_market_order`, `place_close_order`) but no high-level “flatten” orchestration.

## Goals
1. Mirror the opener architecture for unwinds so closing is deterministic, observable, and configurable.
2. Remove the implicit `close_position` expectation from the strategy layer.
3. Support multiple close scenarios:
   - **Normal exit** – both legs present; close delta-neutral with maker-first bias.
   - **Emergency exit** – one leg missing (e.g., sim/liquidation); remaining leg should be flattened ASAP (likely market-only or limit-with-short-timeout).
4. Preserve or improve existing features: leverage/risk checks, logging, and auditability.

## Architectural Direction

### Option A – Extend `OrderExecutor`
- Add quantity-driven execution helpers (current methods require USD size).  
- Surface reduce-only flags, preferred execution mode, and timeout controls for closing flows.  
- Enable callers to supply a pre-fetched snapshot (quantity + side) to avoid redundant REST hits.
- Pros: Reuses a mature executor that already handles price discovery, retries, and fallback paths.  
- Cons: Requires API expansion (quantity input, reduce-only, returning final position delta).

### Option B – Introduce `CloseOrderExecutor`
- Light wrapper that accepts per-leg instructions (quantity, side, execution mode) and internally delegates to `OrderExecutor`.  
- Keeps opening/closing code paths conceptually separate while sharing core logic under the hood.
- Pros: Minimal change to existing `OrderExecutor`; can tailor defaults for closing (e.g., reduce-only).  
- Cons: Another abstraction layer to maintain.

**Recommendation:** Start with Option B. Implement a `CloseLegSpec` + `CloseExecutor` that internally composes `OrderExecutor` so we avoid breaking existing entry code. If we later add quantity support to `OrderExecutor`, the close executor can simply pass through.

## Proposed Changes

### 1. Strategy Layer (`PositionCloser`)
- Replace `_close_exchange_positions()` with orchestration that builds per-leg specs from the live snapshots collected earlier.
- Normal exit path:
  - Construct two `CloseLegSpec`s (long + short) containing quantity, side, execution mode (default `limit_only`), limit offset, timeout, and reduce-only flag.
  - Dispatch both via an atomic executor (reuse `AtomicMultiOrderExecutor` or a slimmer two-leg coordinator). Roll back or escalate if one leg fails.
- Emergency exit path:
  - Detect the missing leg using current logic.
  - Build a single `CloseLegSpec` for the surviving leg with `ExecutionMode.MARKET_ONLY` (or `LIMIT_WITH_FALLBACK` + short timeout).
  - Skip atomic coordination; execute immediately and log aggressively.
- Continue to resolve PnL and update DB once execution feedback is known.

### 2. Atomic Execution Support
- Add helper(s) in `strategies/execution/patterns/atomic_multi_order`:
  - either extend `OrderSpec` to allow “exact quantity” orders with `reduce_only=True`, or
  - add a new `CloseOrderSpec` + `execute_closing_atomically()` that translates into existing order specs using quantity -> USD conversions with proper rounding safeguards.
- Ensure rollback logic understands “closing” semantics (i.e., if one leg closes and the second fails, we may need to reopen or alert). For emergency closes we likely skip rollback entirely.

### 3. Exchange Client Interface (`exchange_clients/base.py`)
- Remove the expectation that subclasses implement `close_position`.
- Optionally provide a default `close_position(symbol, snapshot=None, prefer_market=False, **kwargs)` convenience if we want to keep it around, but update strategy code to stop calling it directly.
- Verify primitives needed by the close executor are present on all venues:
  - `fetch_bbo_prices`
  - `place_limit_order` / `place_market_order`
  - `place_close_order` (maker-focused helper; ensure it supports reduce-only or equivalent flags)
  - `cancel_order`
  - `round_to_step` / `normalize_symbol`
  If any venue is missing reduce-only support, document a workaround (e.g., explicit quantity).

### 4. Exchange Client Implementations
- Add any missing reduce-only or quantity normalization helpers required by the close executor.
- Confirm `place_close_order` implementations accept `reduceOnly` (or emulate it via order params on venues that support it).
- Ensure `get_position_snapshot` returns signed quantity, entry, mark, exposure—already true for Aster and Lighter.

### 5. Testing
- Extend existing unit tests to cover the new executor path (mock executor to verify spec construction).
- Add integration-style test that simulates:
  - Normal close with both legs filled.
  - Emergency close where one leg snapshot is zero.
- Regression test to ensure we no longer call non-existent `close_position` on real clients.

## Step-by-Step Plan Summary
1. **Design executor API** (decide Option A vs B, document spec fields, default behaviours).
2. **Implement close executor** (new module in `strategies/execution/core` or `patterns/atomic_multi_order`).
3. **Refactor `PositionCloser`** to build close specs:
   - Introduce helper for translating snapshot → spec.
   - Wire normal and emergency flows to the executor.
4. **Update exchange client interface/usage**:
   - Remove direct `close_position` calls.
   - Ensure reduce-only/price rounding helpers are available.
5. **Adjust tests** (unit + integration).
6. **Document config knobs** (e.g., default close execution mode, optional overrides in strategy config).

## Open Questions / Follow-Ups
- Should closing use the same limit offset as opening by default, or a dedicated config value?
- How aggressive should emergency close defaults be? Market-only may be safest but carries taker fees.
- For partial fills during close, do we attempt to re-hedge or is alerting sufficient?
- Do we need telemetry metrics (latency, slippage) for close operations similar to open?

These should be resolved during design of the close executor to avoid mismatched expectations between strategy and exchange layers.
