# Hedge Manager Refactoring Proposal

## Executive Summary

The `HedgeManager` class has grown to **904 lines** with significant complexity and code duplication. This refactoring proposal aims to improve code quality, maintainability, and extensibility while preserving all existing functionality.

## Current Issues

### 1. **Massive Methods**
- `aggressive_limit_hedge()`: **600+ lines** with deeply nested logic
- `hedge()`: **150+ lines** with similar patterns
- Violation of Single Responsibility Principle

### 2. **Code Duplication**
Both methods share similar logic for:
- Calculating remaining quantities (lines 44-94 in `hedge()`, lines 243-272 in `aggressive_limit_hedge()`)
- Validating hedge targets
- Applying results to context
- Tracking maker/taker quantities (lines 159-172 in both)
- Logging hedge attempts

### 3. **Mixed Concerns**
The `HedgeManager` handles:
- Hedge target calculation
- Quantity/USD conversion
- Order execution (direct calls to exchange API)
- Result tracking
- Retry logic with backoffs
- Price fetching and BBO queries
- Break-even price calculations
- Order polling and reconciliation
- Multiplier adjustments

### 4. **Low Testability**
- Hard to unit test individual components
- Tightly coupled to exchange clients
- Difficult to mock specific behaviors

### 5. **Limited Extensibility**
- Adding new hedge strategies requires modifying large methods
- No clear strategy pattern for different approaches
- Hard to customize retry/pricing logic per exchange

## Proposed Refactoring

### Phase 1: Extract Helper Classes (High Priority)

#### 1.1 **HedgeTargetCalculator**
Extract quantity calculation logic into a dedicated class.

**Responsibilities:**
- Calculate remaining quantities
- Handle multiplier adjustments
- Validate hedge targets
- Track accumulated fills

**Benefits:**
- Reusable across hedge strategies
- Easier to test quantity logic in isolation
- Single source of truth for hedge target calculations

```python
class HedgeTargetCalculator:
    """Calculates and tracks hedge targets across exchanges."""
    
    def calculate_hedge_target(
        self,
        trigger_ctx: OrderContext,
        target_ctx: OrderContext,
        logger
    ) -> HedgeTarget:
        """Calculate hedge target with multiplier adjustments."""
        # Lines 657-697 from executor.py
        # Lines 44-94 from hedge_manager.py
        pass
    
    def calculate_remaining_quantity(
        self,
        ctx: OrderContext,
        hedge_target: Decimal,
        accumulated_fills: Decimal = Decimal("0")
    ) -> Decimal:
        """Calculate remaining quantity after fills."""
        # Lines 243-262, 335-342 from aggressive_limit_hedge
        pass
```

**Extraction:**
- Current: Duplicated in `hedge()` (lines 44-94) and `aggressive_limit_hedge()` (lines 243-272)
- New: Single class, ~100 lines
- Reduction: ~170 lines → ~100 lines = **70 lines saved**

---

#### 1.2 **HedgePricer**
Extract price calculation logic.

**Responsibilities:**
- Fetch BBO prices
- Calculate break-even hedge prices
- Determine aggressive limit pricing (inside spread, at touch)
- Handle adaptive pricing strategies

**Benefits:**
- Separate pricing logic from execution
- Easier to test different pricing strategies
- Reusable for future pricing algorithms

```python
class HedgePricer:
    """Calculates hedge prices using various strategies."""
    
    def __init__(self, price_provider):
        self._price_provider = price_provider
    
    async def calculate_aggressive_limit_price(
        self,
        spec: OrderSpec,
        trigger_ctx: OrderContext,
        pricing_strategy: PricingStrategy,
        logger
    ) -> HedgePriceResult:
        """Calculate limit price for aggressive hedge."""
        # Lines 324-333, 643-768 from aggressive_limit_hedge
        pass
```

**Extraction:**
- Current: `_calculate_hedge_price()` method (lines 643-768, 126 lines)
- New: Dedicated class with strategy pattern
- Benefit: Clear separation, easier to add new pricing strategies

---

#### 1.3 **OrderReconciler**
Extract order polling and reconciliation logic.

**Responsibilities:**
- Poll order status with timeout
- Track partial fills
- Reconcile final order state
- Handle order cancellation

**Benefits:**
- Reusable polling logic
- Testable without actual orders
- Centralized reconciliation rules

```python
class OrderReconciler:
    """Handles order polling and reconciliation."""
    
    async def poll_order_until_filled(
        self,
        exchange_client,
        order_id: str,
        timeout_seconds: float,
        logger
    ) -> ReconciliationResult:
        """Poll order status until filled or timeout."""
        # Lines 435-454, 769-904 from aggressive_limit_hedge
        pass
    
    async def reconcile_final_state(
        self,
        exchange_client,
        order_id: str,
        last_known_fills: Decimal,
        logger
    ) -> Decimal:
        """Perform final reconciliation check."""
        # Lines 577-627 from aggressive_limit_hedge
        pass
```

**Extraction:**
- Current: `_poll_order_fill_status()` (lines 769-904, 135 lines)
- Current: Final reconciliation (lines 577-627, 50 lines)
- New: Single class, ~200 lines
- Reduction: ~185 lines embedded → ~200 lines extracted = **Clearer separation**

---

#### 1.4 **HedgeResultTracker**
Extract result tracking and context updates.

**Responsibilities:**
- Apply execution results to context
- Track maker/taker quantities
- Calculate accumulated fills
- Update context state consistently

**Benefits:**
- Consistent result tracking across strategies
- Single source of truth for context updates
- Easier to debug state changes

```python
class HedgeResultTracker:
    """Tracks hedge execution results and updates context."""
    
    def apply_hedge_result(
        self,
        ctx: OrderContext,
        execution_result,
        is_maker: bool,
        accumulated_fills: Decimal,
        initial_fills: Decimal
    ):
        """Apply hedge result to context with maker/taker tracking."""
        # Lines 156-172 (hedge), 484-505 (aggressive_limit_hedge)
        pass
    
    def track_partial_fill(
        self,
        accumulated_fills: Decimal,
        new_fill_qty: Decimal,
        new_fill_price: Decimal,
        accumulated_price: Optional[Decimal]
    ) -> Tuple[Decimal, Decimal]:
        """Track accumulated partial fills across retries."""
        # Lines 299-303, 436-466 from aggressive_limit_hedge
        pass
```

**Extraction:**
- Current: Duplicated tracking logic in both methods
- New: Single class, ~80 lines
- Reduction: Better code reuse, consistent behavior

---

### Phase 2: Refactor HedgeManager (High Priority)

#### 2.1 **Strategy Pattern for Hedge Execution**

Create a `HedgeStrategy` interface with concrete implementations.

```python
from abc import ABC, abstractmethod

class HedgeStrategy(ABC):
    """Base class for hedge execution strategies."""
    
    @abstractmethod
    async def execute_hedge(
        self,
        trigger_ctx: OrderContext,
        target_ctx: OrderContext,
        hedge_target: HedgeTarget,
        logger,
        **kwargs
    ) -> HedgeResult:
        """Execute hedge using this strategy."""
        pass

class MarketHedgeStrategy(HedgeStrategy):
    """Simple market order hedge strategy."""
    
    async def execute_hedge(self, ...) -> HedgeResult:
        # Current hedge() logic
        pass

class AggressiveLimitHedgeStrategy(HedgeStrategy):
    """Aggressive limit order hedge with retries."""
    
    def __init__(
        self,
        pricer: HedgePricer,
        reconciler: OrderReconciler,
        tracker: HedgeResultTracker
    ):
        self._pricer = pricer
        self._reconciler = reconciler
        self._tracker = tracker
    
    async def execute_hedge(self, ...) -> HedgeResult:
        # Current aggressive_limit_hedge() logic
        # But using extracted helpers
        pass

class HybridHedgeStrategy(HedgeStrategy):
    """Try aggressive limit, fallback to market."""
    
    async def execute_hedge(self, ...) -> HedgeResult:
        # Try aggressive limit first
        # Automatic fallback to market on timeout
        pass
```

**Benefits:**
- Easy to add new hedge strategies
- Strategy selection can be configuration-driven
- Each strategy is independently testable
- Clear separation of concerns

---

#### 2.2 **Refactored HedgeManager**

```python
class HedgeManager:
    """Orchestrates hedge execution using pluggable strategies."""
    
    def __init__(
        self,
        price_provider=None,
        default_strategy: Optional[HedgeStrategy] = None
    ):
        self._price_provider = price_provider
        
        # Helper components
        self._target_calculator = HedgeTargetCalculator()
        self._pricer = HedgePricer(price_provider)
        self._reconciler = OrderReconciler()
        self._tracker = HedgeResultTracker()
        
        # Strategies
        self._market_strategy = MarketHedgeStrategy(self._tracker)
        self._aggressive_limit_strategy = AggressiveLimitHedgeStrategy(
            self._pricer,
            self._reconciler,
            self._tracker
        )
        self._default_strategy = default_strategy or self._aggressive_limit_strategy
    
    async def hedge(
        self,
        trigger_ctx: OrderContext,
        contexts: List[OrderContext],
        logger,
        reduce_only: bool = False,
        strategy: Optional[HedgeStrategy] = None
    ) -> Tuple[bool, Optional[str]]:
        """Execute hedge using specified or default strategy."""
        strategy = strategy or self._default_strategy
        
        for ctx in contexts:
            if ctx is trigger_ctx:
                continue
            
            # Calculate hedge target (single source of truth)
            hedge_target = self._target_calculator.calculate_hedge_target(
                trigger_ctx, ctx, logger
            )
            
            if hedge_target.remaining_qty <= Decimal("0"):
                continue
            
            # Execute hedge using strategy
            result = await strategy.execute_hedge(
                trigger_ctx=trigger_ctx,
                target_ctx=ctx,
                hedge_target=hedge_target,
                logger=logger,
                reduce_only=reduce_only
            )
            
            if not result.success:
                return False, result.error_message
        
        return True, None
    
    async def aggressive_limit_hedge(self, ...) -> Tuple[bool, Optional[str]]:
        """Convenience method for aggressive limit hedge."""
        return await self.hedge(
            ...,
            strategy=self._aggressive_limit_strategy
        )
```

**Benefits:**
- Reduced from 904 lines → ~200 lines for manager + ~250 lines per strategy
- Clear orchestration vs execution separation
- Easy to test strategies independently
- Easy to add new strategies without modifying manager

---

### Phase 3: Additional Improvements (Medium Priority)

#### 3.1 **Configuration Objects**

Create typed configuration objects instead of passing many parameters.

```python
@dataclass
class HedgeConfig:
    """Configuration for hedge execution."""
    reduce_only: bool = False
    max_retries: Optional[int] = None
    retry_backoff_ms: Optional[int] = None
    total_timeout_seconds: Optional[float] = None
    inside_tick_retries: Optional[int] = None
    max_deviation_pct: Optional[Decimal] = None
    
    @classmethod
    def for_closing_operation(cls) -> "HedgeConfig":
        """Preset config optimized for closing operations."""
        return cls(
            reduce_only=True,
            max_retries=5,
            retry_backoff_ms=50,
            total_timeout_seconds=3.0,
            inside_tick_retries=2
        )
    
    @classmethod
    def for_opening_operation(cls) -> "HedgeConfig":
        """Preset config optimized for opening operations."""
        return cls(
            reduce_only=False,
            max_retries=8,
            retry_backoff_ms=75,
            total_timeout_seconds=6.0,
            inside_tick_retries=3
        )
```

**Benefits:**
- Type safety
- Clearer method signatures
- Easy to add new configuration options
- Preset configurations for common use cases

---

#### 3.2 **Result Objects**

Replace tuple returns with typed result objects.

```python
@dataclass
class HedgeResult:
    """Result of hedge execution."""
    success: bool
    filled_quantity: Decimal
    fill_price: Optional[Decimal]
    execution_mode: str
    maker_quantity: Decimal = Decimal("0")
    taker_quantity: Decimal = Decimal("0")
    error_message: Optional[str] = None
    retries_used: int = 0
```

**Benefits:**
- Type safety
- Self-documenting
- Easy to extend with additional fields
- IDE autocomplete support

---

### Phase 4: Testing Improvements (Medium Priority)

#### 4.1 **Unit Tests for Helpers**

With extracted helpers, we can write focused unit tests:

```python
class TestHedgeTargetCalculator:
    """Test hedge target calculations in isolation."""
    
    def test_multiplier_adjustment(self):
        """Test quantity conversion across exchanges with different multipliers."""
        # Test lines 657-697 logic independently
        pass
    
    def test_remaining_quantity_after_partial_fills(self):
        """Test remaining quantity calculation with accumulated fills."""
        pass

class TestHedgePricer:
    """Test pricing strategies in isolation."""
    
    async def test_break_even_pricing(self):
        """Test break-even hedge price calculation."""
        pass
    
    async def test_aggressive_limit_pricing_strategies(self):
        """Test inside spread vs at touch pricing."""
        pass
```

#### 4.2 **Integration Tests**

Test strategies end-to-end with mocked exchange clients:

```python
class TestAggressiveLimitHedgeStrategy:
    """Test aggressive limit hedge strategy."""
    
    async def test_successful_hedge_with_retries(self):
        """Test successful hedge after post-only violations."""
        pass
    
    async def test_partial_fill_accumulation(self):
        """Test tracking of partial fills across retries."""
        pass
    
    async def test_fallback_to_market_on_timeout(self):
        """Test market fallback when retries exhausted."""
        pass
```

---

## Implementation Plan

### ⚠️ Stage 0: Remove `remaining_usd` Legacy Code (PREREQUISITE - 1-2 hours)

**Do this FIRST before the larger refactoring!**

The `remaining_usd` field is acknowledged as unreliable (see comment on line 74) yet still used as a fallback. This creates a dangerous edge case that can cause over-hedging.

**Changes:**
1. Remove `elif remaining_usd > 0` fallback in both `hedge()` and `aggressive_limit_hedge()`
2. Simplify skip checks to use only `remaining_qty`
3. Update `reconcile_context_after_cancel()` to check quantity instead of USD
4. Add test case verifying no over-hedging when USD tracking is wrong

**See:** `REMAINING_USD_REMOVAL_ANALYSIS.md` for complete analysis

**Benefits:**
- Removes potential over-hedging bug
- Simplifies code (removes ~15 lines per method)
- Makes quantity the single source of truth
- Easier refactoring with less legacy code

**Risk: Low** - Only removes a buggy edge case, normal flow already uses quantity

---

### Stage 1: Extract Helpers (1-2 days)
1. Create `HedgeTargetCalculator` class
2. Create `HedgePricer` class
3. Create `OrderReconciler` class
4. Create `HedgeResultTracker` class
5. Add unit tests for each helper

**Risk: Low** - These are pure refactorings with no behavior changes

### Stage 2: Create Strategy Pattern (1-2 days)
1. Define `HedgeStrategy` interface
2. Implement `MarketHedgeStrategy`
3. Implement `AggressiveLimitHedgeStrategy`
4. Add tests for each strategy

**Risk: Low** - Existing methods become strategy implementations

### Stage 3: Refactor HedgeManager (1 day)
1. Refactor `HedgeManager` to use strategies
2. Update callers (should be minimal changes)
3. Add integration tests

**Risk: Medium** - Requires updating callers

### Stage 4: Configuration Objects (0.5 days)
1. Create `HedgeConfig` dataclass
2. Update strategy interfaces to accept config
3. Create preset configurations

**Risk: Low** - Backward compatible additions

### Stage 5: Testing & Documentation (1 day)
1. Ensure 100% test coverage for helpers
2. Integration tests for full hedge flows
3. Update documentation

**Total Estimated Time: 4-7 days** (including Stage 0 prerequisite)

---

## Benefits Summary

### Code Quality
- **Reduced complexity**: 904 lines → ~650 lines total (manager + strategies + helpers)
- **Better separation of concerns**: Each class has a single responsibility
- **Eliminated duplication**: Shared logic extracted to helpers
- **Improved testability**: Each component can be tested in isolation

### Maintainability
- **Easier to debug**: Smaller, focused methods
- **Easier to understand**: Clear flow from manager → strategy → helpers
- **Easier to modify**: Changes to pricing/polling/tracking don't affect other parts

### Extensibility
- **Easy to add new strategies**: Implement `HedgeStrategy` interface
- **Easy to customize behavior**: Override specific helpers
- **Configuration-driven**: Select strategies and parameters via config

### Safety
- **Preserves all functionality**: No behavior changes, just reorganization
- **Incremental rollout**: Can implement and test in stages
- **Backward compatible**: Existing method signatures preserved during transition

---

## Alternative: Minimal Refactoring (If Time Constrained)

If full refactoring is not feasible now, consider these minimal improvements:

### Quick Wins (1-2 days)
1. **Extract `_calculate_hedge_target()` method**
   - Pull out lines 44-94 and 243-272 into single method
   - Saves ~80 lines of duplication
   
2. **Extract `_apply_hedge_result()` method**
   - Pull out lines 156-172 and 484-505
   - Consistent result tracking
   
3. **Add `HedgeConfig` dataclass**
   - Replace 6 parameters with single config object
   - Clearer method signatures
   
4. **Split `aggressive_limit_hedge()` into 3 methods**
   - `_execute_limit_retry_loop()` (lines 305-568)
   - `_perform_final_reconciliation()` (lines 577-627)
   - `_fallback_to_market()` (lines 630-742)
   - Still long but more manageable

**Benefits:**
- Reduced duplication
- Improved readability
- Foundation for future full refactoring
- Lower risk, faster implementation

---

## Recommendation

**Proceed with Full Refactoring (Stages 1-5)**

**Rationale:**
- The code is at a critical complexity threshold where future changes will be painful
- The refactoring is low-risk with high reward
- Testing improvements alone justify the effort
- Extensibility will be crucial as new hedge strategies are needed
- The estimated time (4-6 days) is reasonable for the long-term benefits

**If timeline is critical:** Start with Stage 1 (Extract Helpers) which provides immediate benefits and sets foundation for future stages.

---

## Appendix: Current Call Flow

```
position_opener.py
  └─> execution_engine.py
      └─> atomic_executor.execute_atomically()
          └─> executor._handle_full_fill_trigger()
              └─> hedge_manager.aggressive_limit_hedge()  # 600+ lines
                  ├─> _calculate_hedge_price()  # 126 lines
                  ├─> place_limit_order()
                  ├─> _poll_order_fill_status()  # 135 lines
                  ├─> Final reconciliation  # 50 lines
                  └─> Fallback to market  # 112 lines
```

## Appendix: Proposed Call Flow

```
position_opener.py
  └─> execution_engine.py
      └─> atomic_executor.execute_atomically()
          └─> executor._handle_full_fill_trigger()
              └─> hedge_manager.hedge()  # 50 lines (orchestration)
                  ├─> target_calculator.calculate_hedge_target()  # 30 lines
                  └─> strategy.execute_hedge()
                      ├─> pricer.calculate_aggressive_limit_price()  # 40 lines
                      ├─> exchange_client.place_limit_order()
                      ├─> reconciler.poll_order_until_filled()  # 60 lines
                      ├─> reconciler.reconcile_final_state()  # 30 lines
                      └─> tracker.apply_hedge_result()  # 20 lines
```

**Much cleaner, testable, and extensible!**

