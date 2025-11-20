# Hedge Manager Current Flow (After Refactoring)

## Overview

The hedge manager now uses a clean strategy pattern with helper classes. This document shows the complete flow from entry point to execution.

---

## Main Entry Point: Aggressive Limit Hedge Flow

```
position_opener.py / execution_engine.py
  └─> AtomicMultiOrderExecutor.execute_atomically()
      └─> executor._handle_full_fill_trigger() 
          │
          ├─> [1] Cancel remaining limit orders
          │   └─> ctx.cancel_event.set() for each other_context
          │
          ├─> [2] Wait for pending completions
          │   └─> asyncio.gather(*pending_completion)
          │   └─> reconcile_context_after_cancel()  # Final fill reconciliation
          │
          ├─> [3] Calculate hedge targets with multiplier adjustments
          │   └─> For each ctx in other_contexts:
          │       ├─> Get trigger_qty from trigger_ctx.filled_quantity
          │       ├─> Get multipliers: trigger_multiplier, ctx_multiplier
          │       ├─> Convert: actual_tokens = trigger_qty × trigger_multiplier
          │       ├─> Convert: target_qty = actual_tokens ÷ ctx_multiplier
          │       └─> Set: ctx.hedge_target_quantity = target_qty
          │
          └─> [4] Execute aggressive limit hedge
              └─> hedge_manager.aggressive_limit_hedge() 
                  │
                  ├─> [4.1] For each context (skip trigger_ctx):
                  │   │
                  │   ├─> [4.1.1] Get hedge_target from ctx.hedge_target_quantity
                  │   │   └─> Fallback to spec.quantity if not set    # not ideal
                  │   │
                  │   ├─> [4.1.2] Calculate remaining quantity
                  │   │   └─> target_calculator.calculate_remaining_quantity(ctx, hedge_target)
                  │   │       └─> Returns: hedge_target - ctx.filled_quantity
                  │   │
                  │   └─> [4.1.3] Execute strategy
                  │       └─> aggressive_limit_strategy.execute_hedge()  
                  │           │
                  │           ├─> [4.1.3.1] Auto-configure retry parameters
                  │           │   ├─> If reduce_only: max_retries=5, timeout=3s, backoff=50ms
                  │           │   └─> Else: max_retries=8, timeout=6s, backoff=75ms
                  │           │
                  │           ├─> [4.1.3.2] Calculate remaining quantity
                  │           │   └─> target_calculator.calculate_remaining_quantity()
                  │           │
                  │           ├─> [4.1.3.3] Retry loop (for retry_count in range(max_retries)):
                  │           │   │
                  │           │   ├─> [4.1.3.3.1] Check timeout
                  │           │   │   └─> If elapsed >= total_timeout_seconds:
                  │           │   │       └─> Break → Fallback to market
                  │           │   │
                  │           │   ├─> [4.1.3.3.2] Calculate hedge price
                  │           │   │   └─> pricer.calculate_aggressive_limit_price()  # Line 354
                  │           │   │       │
                  │           │   │       ├─> Fetch BBO prices
                  │           │   │       │   └─> price_provider.get_bbo_prices()
                  │           │   │       │
                  │           │   │       ├─> Try break-even pricing (if trigger fill price available)
                  │           │   │       │   └─> BreakEvenPriceAligner.calculate_break_even_hedge_price()
                  │           │   │       │       └─> Returns: (break_even_price, strategy)
                  │           │   │       │
                  │           │   │       └─> If break-even not feasible:
                  │           │   │           ├─> If retry_count < inside_tick_retries:
                  │           │   │           │   └─> "inside_spread": best_ask - tick_size (buy) or best_bid + tick_size (sell)
                  │           │   │           └─> Else:
                  │           │   │               └─> "touch": best_ask (buy) or best_bid (sell)
                  │           │   │
                  │           │   ├─> [4.1.3.3.3] Calculate remaining quantity after partial fills
                  │           │   │   └─> remaining_qty = hedge_target - (initial_filled_qty + accumulated_filled_qty)
                  │           │   │
                  │           │   ├─> [4.1.3.3.4] Place limit order
                  │           │   │   └─> exchange_client.place_limit_order(
                  │           │   │       contract_id, quantity, price, side, reduce_only
                  │           │   │   )
                  │           │   │
                  │           │   ├─> [4.1.3.3.5] Poll for fill status
                  │           │   │   └─> reconciler.poll_order_until_filled()  # Line 380
                  │           │   │       │
                  │           │   │       ├─> Poll loop (while time < attempt_timeout):
                  │           │   │       │   ├─> exchange_client.get_order_info(order_id)
                  │           │   │       │   │
                  │           │   │       │   ├─> If status == "FILLED":
                  │           │   │       │   │   └─> Track fills → Return filled=True
                  │           │   │       │   │
                  │           │   │       │   ├─> If status == "PARTIALLY_FILLED":
                  │           │   │       │   │   ├─> Track partial fills
                  │           │   │       │   │   ├─> Cancel order
                  │           │   │       │   │   └─> Break → Retry with remaining quantity
                  │           │   │       │   │
                  │           │   │       │   └─> If status == "CANCELED":
                  │           │   │       │       ├─> Check for partial fills before cancellation
                  │           │   │       │       ├─> If post-only violation → Retry
                  │           │   │       │       └─> Else → Check if enough fills (99% threshold)
                  │           │   │       │
                  │           │   │       └─> Returns: ReconciliationResult(
                  │           │   │           filled, filled_qty, fill_price,
                  │           │   │           accumulated_filled_qty, current_order_filled_qty,
                  │           │   │           partial_fill_detected, error
                  │           │   │       )
                  │           │   │
                  │           │   ├─> [4.1.3.3.6] Check if filled (fully or via accumulated partial fills)
                  │           │   │   ├─> If filled and total_filled >= hedge_target × 0.99:
                  │           │   │   │   └─> tracker.apply_aggressive_limit_hedge_result()
                  │           │   │   │       ├─> execution_result_to_dict()
                  │           │   │   │       ├─> apply_result_to_context()
                  │           │   │   │       └─> Track maker_qty, taker_qty
                  │           │   │   │   └─> Return HedgeResult(success=True)
                  │           │   │   │
                  │           │   │   ├─> If partial fill but not enough:
                  │           │   │   │   └─> Continue retry loop with remaining quantity
                  │           │   │   │
                  │           │   │   └─> If not filled:
                  │           │   │       ├─> Cancel order
                  │           │   │       └─> Continue retry loop
                  │           │   │
                  │           │   └─> [4.1.3.3.7] Handle exceptions
                  │           │       └─> Log error → Continue retry or break
                  │           │
                  │           ├─> [4.1.3.4] Final reconciliation check
                  │           │   └─> If not hedge_success and last_order_id:
                  │           │       └─> reconciler.reconcile_final_state()
                  │           │           └─> exchange_client.get_order_info(last_order_id)
                  │           │           └─> Check for missed fills → Add to accumulated_filled_qty
                  │           │
                  │           └─> [4.1.3.5] Fallback to market if limit hedge failed
                  │               └─> If not hedge_success:
                  │                   ├─> Track partial fills before fallback
                  │                   │   └─> tracker.track_partial_fills_before_market_fallback()
                  │                   │       └─> Update ctx.filled_quantity, ctx.result
                  │                   │
                  │                   └─> Execute market hedge fallback
                  │                       └─> market_fallback.execute_hedge()  # MarketHedgeStrategy
                  │                           │
                  │                           ├─> Calculate remaining quantity
                  │                           │   └─> target_calculator.calculate_remaining_quantity()
                  │                           │
                  │                           ├─> Execute market order
                  │                           │   └─> OrderExecutor.execute_order(
                  │                           │       mode=MARKET_ONLY
                  │                           │   )
                  │                           │
                  │                           ├─> Check for partial fills
                  │                           │   └─> If has_partial_fill and not success:
                  │                           │       └─> tracker.apply_market_hedge_result()
                  │                           │           └─> Record partial fill for rollback
                  │                           │
                  │                           └─> Return HedgeResult
                  │
                  └─> [4.1.4] Return HedgeResult
                      ├─> If result.success == False:
                      │   └─> Return result immediately
                      └─> If all contexts hedged successfully:
                          └─> Return HedgeResult(success=True)
```

---

## Simple Market Hedge Flow

```
executor._handle_partial_fill() 
  └─> hedge_manager.hedge()  
      │
      ├─> [1] For each context (skip trigger_ctx):
      │   │
      │   ├─> [1.1] Calculate remaining quantity
      │   │   └─> target_calculator.get_remaining_quantity_for_hedge(ctx, logger)
      │   │       └─> Returns: hedge_target_quantity - ctx.filled_quantity
      │   │
      │   ├─> [1.2] Skip if remaining_qty <= 0
      │   │   └─> (with suspicious scenario warning if trigger filled)
      │   │
      │   ├─> [1.3] Determine hedge_target
      │   │   └─> From ctx.hedge_target_quantity or spec.quantity
      │   │
      │   └─> [1.4] Execute market strategy
      │       └─> market_strategy.execute_hedge()  
      │           │
      │           ├─> [1.4.1] Calculate remaining quantity
      │           │   └─> target_calculator.calculate_remaining_quantity()
      │           │
      │           ├─> [1.4.2] Skip if remaining_qty <= 0
      │           │   └─> Return HedgeResult(success=True, execution_mode="market_skip")
      │           │
      │           ├─> [1.4.3] Calculate USD estimate (for logging)
      │           │   └─> price_provider.get_bbo_prices() → estimated_usd = remaining_qty × price
      │           │
      │           ├─> [1.4.4] Execute market order
      │           │   └─> OrderExecutor.execute_order(
      │           │       mode=MARKET_ONLY
      │           │   )
      │           │
      │           ├─> [1.4.5] Check for partial fills
      │           │   └─> If has_partial_fill and not success:
      │           │       └─> tracker.apply_market_hedge_result()
      │           │           └─> Record partial fill for rollback
      │           │           └─> Return HedgeResult(success=False, error_message=...)
      │           │
      │           ├─> [1.4.6] Apply result if successful
      │           │   └─> tracker.apply_market_hedge_result()
      │           │       ├─> execution_result_to_dict()
      │           │       ├─> apply_result_to_context()
      │           │       └─> Track taker_qty (market orders are taker)
      │           │
      │           └─> [1.4.7] Return HedgeResult
      │
      └─> [2] Return HedgeResult
          └─> If all contexts hedged successfully:
              └─> Return HedgeResult(success=True)
```

---

## Component Responsibilities

### HedgeManager (Orchestrator)
- **Location**: `components/hedge_manager.py` (~185 lines)
- **Role**: Thin orchestrator that delegates to strategies
- **Responsibilities**:
  - Iterate over contexts
  - Calculate hedge targets
  - Delegate to appropriate strategy
  - Return typed HedgeResult

### MarketHedgeStrategy
- **Location**: `components/hedge/strategies.py` (~130 lines)
- **Role**: Simple, fast market order execution
- **Use Cases**: Fallback from limit hedge, partial fill hedging
- **No retries**: Execute once, done

### AggressiveLimitHedgeStrategy
- **Location**: `components/hedge/strategies.py` (~400 lines)
- **Role**: Limit order hedge with retries and adaptive pricing
- **Features**:
  - Retry loop with backoff
  - Adaptive pricing (break-even → inside_spread → touch)
  - Partial fill tracking
  - Market fallback on timeout/failure

### HedgeTargetCalculator
- **Location**: `components/hedge/hedge_target_calculator.py` (~195 lines)
- **Role**: Calculate hedge targets and remaining quantities
- **Methods**:
  - `calculate_hedge_target()`: With multiplier adjustments
  - `calculate_remaining_quantity()`: After fills
  - `get_remaining_quantity_for_hedge()`: Main entry point

### HedgePricer
- **Location**: `components/hedge/hedge_pricer.py` (~170 lines)
- **Role**: Calculate hedge prices using various strategies
- **Methods**:
  - `calculate_aggressive_limit_price()`: Break-even or adaptive pricing
- **Strategies**:
  - Break-even (if trigger fill price available)
  - Inside spread (1 tick away from touch)
  - Touch (at best bid/ask)

### OrderReconciler
- **Location**: `components/hedge/order_reconciler.py` (~261 lines)
- **Role**: Poll orders and reconcile fill status
- **Methods**:
  - `poll_order_until_filled()`: Poll loop with timeout
  - `reconcile_final_state()`: Final check after polling

### HedgeResultTracker
- **Location**: `components/hedge/hedge_result_tracker.py` (~151 lines)
- **Role**: Apply execution results to contexts consistently
- **Methods**:
  - `apply_market_hedge_result()`: Market order results
  - `apply_aggressive_limit_hedge_result()`: Limit order results
  - `track_partial_fills_before_market_fallback()`: Before fallback

---

## Key Design Patterns

### 1. Strategy Pattern
- `HedgeStrategy` ABC defines interface
- `MarketHedgeStrategy` and `AggressiveLimitHedgeStrategy` implement it
- `HedgeManager` orchestrates using strategies

### 2. Helper Classes
- Single Responsibility Principle
- Each helper handles one concern
- Easy to test in isolation

### 3. Type Safety
- `HedgeResult` replaces tuples
- Clear, self-documenting return types
- IDE autocomplete support

### 4. Fallback Mechanism
- Aggressive limit hedge → Market hedge
- Graceful degradation
- Partial fill tracking throughout

---

## Data Flow

```
OrderContext (input)
  ├─> hedge_target_quantity (set by executor)
  ├─> filled_quantity (updated during execution)
  └─> result (dict with execution details)

HedgeResult (output)
  ├─> success: bool
  ├─> filled_quantity: Decimal
  ├─> fill_price: Optional[Decimal]
  ├─> execution_mode: str
  ├─> maker_quantity: Decimal
  ├─> taker_quantity: Decimal
  ├─> error_message: Optional[str]
  └─> retries_used: int
```

---

## Error Handling Flow

```
1. Strategy execution fails
   └─> Returns HedgeResult(success=False, error_message=...)

2. HedgeManager receives failure
   └─> Returns HedgeResult immediately (stops processing other contexts)

3. Executor receives failure
   ├─> If rollback_on_partial:
   │   └─> RollbackManager.rollback() → Close partial fills
   └─> Else:
       └─> Return error to caller
```

---



