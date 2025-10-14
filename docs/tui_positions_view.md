"""
Draft layout for a positions-centric dashboard menu in the Textual TUI.
"""

## Overview

Goal: Provide a focused, full-screen workflow for browsing active positions. No split panes; each step occupies the entire view. We present a stack of screens:

1. **Main Menu** – choose what to do (for now, “View Positions”, “Exit”).
2. **Strategy Selector** – when multiple strategy sessions exist, pick one.
3. **Positions List** – full-screen table of positions for the chosen strategy.
4. **Position Detail Overlay** – optional zoom-in on a single trade (with long/short legs).

Navigation relies on Textual’s `Screen` API: pressing Enter pushes the next screen; Escape or `q` returns to the previous one.

## Screen Sketches

### 1. Main Menu (Full Screen)

```
┌─────────────────────────────────────────────┐
│ Funding Dashboard                           │
│─────────────────────────────────────────────│
│ › View Positions                            │
│   View Funding Rates       (coming soon)    │
│   Build Config             (coming soon)    │
│   Run Strategy             (coming soon)    │
│   Exit                                       │
└─────────────────────────────────────────────┘
```

### 2. Strategy Selector

First pick the strategy family (funding arbitrage vs grid). Each entry shows aggregate stats across its sessions.

```
┌─────────────────────────────────────────────┐
│ Select Strategy                             │
│─────────────────────────────────────────────│
│ › funding_arbitrage  Sessions: 2  Live: 1    │
│   grid               Sessions: 1  Live: 1    │
│   ‹ Back ↵                                   │
└─────────────────────────────────────────────┘
```

After selecting a strategy we can either show:

- **All sessions in one view** (default): a combined table with a session column and controls (`tab`, `[`/`]`) to cycle the focused session; totals aggregate across all sessions. The session label should use a readable identifier (e.g., config filename, user-defined bot name, account alias) rather than the raw UUID.
- **Optional session picker**: if the operator wants to isolate a single session, provide a secondary list:
  ```
  ┌─────────────────────────────────────────────┐
  │ funding_arbitrage – Choose Session          │
  │─────────────────────────────────────────────│
  │ › Wallet Alpha        LIVE  Notional $42k   │
  │   Config real_test.yml IDLE  Notional $12k   │
  │   All Sessions (combined view)              │
  │   ‹ Back ↵                                  │
  └─────────────────────────────────────────────┘
  ```

The positions screen should accept either a single session ID or the “all sessions” mode and adjust its layout accordingly.

### 3. Positions List (Full Screen Table)

```
┌──────────────────────────────────────────────┐
│ funding_arbitrage – Live Positions           │
│ Session: 93e1…   Last Update: 12:45:32Z      │
│ Total: 3   Notional: $42,000   Net PnL: $120 │
│──────────────────────────────────────────────│
│ Symbol │ Long Dex │ Short Dex │ Notional │ Δ │
│ BTC    │ lighter  │ edgex     │ $10,000  │▲ │
│ SOL    │ lighter  │ grvt      │ $12,000  │▲ │
│ ETH    │ grvt     │ edgex     │ $20,000  │▼ │
│──────────────────────────────────────────────│
│ [,] Prev/Next • Enter View Detail • q Back   │
└──────────────────────────────────────────────┘
```

Columns (initial pass):
- Symbol
- Long venue / short venue
- Notional (per leg)
- Divergence / erosion indicator
- Net PnL

### 4. Position Detail (Overlay or Full Screen)

```
┌ Position – BTC (funding_arbitrage) ───────────────┐
│ Status: open   Age: 6h12m   Divergence: 0.35%     │
│ Fees: $18.22   Net Funding: $63.41                │
│                                                   │
│ Long Leg  (lighter)                               │
│   Side: long    Entry: 64,200    Qty: 0.156       │
│   Mark: 64,120  Funding: $28.12  Fees: $9.11      │
│                                                   │
│ Short Leg (edgex)                                  │
│   Side: short   Entry: 64,195    Qty: 0.156       │
│   Mark: 64,205  Funding: $35.29  Fees: $9.11      │
│                                                   │
│ Timeline: Opened 10:33Z • Last Check 12:45Z       │
│                                                   │
│ [Esc] Back to positions                           │
└───────────────────────────────────────────────────┘
```

If data is missing (no exchange snapshot yet) show “—”.

## Data Flow

- **Live stream**: consume `/stream` from the dashboard control server, just like the current menu. Maintain an in-memory cache of sessions and positions.
- **Strategy grouping**: group positions by `snapshot.session.strategy` and session ID. The selector can list each unique `(strategy, session_id)` pair.
- **Friendly labels**: when rendering sessions, prefer human-readable metadata (bot name, config filename, account alias) sourced from `session.metadata` or config context; fall back to the UUID only if nothing else is available.
- **State updates**: the positions screen listens for snapshot updates and refreshes the table if the active session matches. When inactive, we can pause updates or keep the cache refreshed globally.
- **Fallback**: if `/stream` isn’t reachable, fall back to a one-time `/snapshot` fetch or the original DB load (`load_dashboard_state`).

## Navigation & Controls

- `Enter` on menu items → push next screen.
- `Esc`/`q` on any screen → pop back (or exit from menu).
- Positions screen:
  - Arrow keys or `j/k` to move selection
  - `Enter` to open detail, `Esc` back to list.
- Detail screen: `Esc` returns to the list.

## Implementation Notes

1. **Router/Screens**: introduce a small routing layer (Textual `Screen` objects). Example stack: `MainMenuScreen`, `StrategySelectScreen`, `PositionsScreen`, `PositionDetailScreen`. Manage navigation via `push_screen`/`pop_screen`.
2. **Data cache**: keep a central `DashboardStore` (similar to the old `_current_snapshot`) that screens can query. Use events or reactive variables to notify screens on updates.
3. **First milestone**: implement “View Positions → Strategy Select → Positions List” with live updates. Detail screen can be added once the table is stable.
4. **Extensibility**: future menu items (funding rates, config builder, run bot) can be new screens pushed from the main menu without a layout redesign.
