# Outdated Tests Requiring Architecture Updates

This document tracks tests that are failing due to major architectural refactoring and require significant updates to match the current codebase.

## Execution Layer Tests (23 tests - HIGH PRIORITY)

These tests were written for an older execution architecture and need complete rewrites:

### Atomic Multi-Order Tests (`test_atomic_multi_order.py`) - 11 tests
**Status**: Outdated - requires complete rewrite
**Reason**: Tests reference removed components:
- `hedge_manager` module (now uses `HedgeManager` class in `components/`)
- `_run_preflight_checks()` method (now `_preflight_checker`)
- Direct rollback methods (now handled by `RollbackManager`)

**Affected Tests**:
1. `test_atomic_execution_success`
2. `test_partial_fill_triggers_market_hedge`
3. `test_market_hedge_failure_triggers_rollback`
4. `test_execute_market_hedge_places_market_orders`
5. `test_execute_market_hedge_skips_already_filled_contexts`
6. `test_rollback_race_condition_protection`
7. `test_preflight_balance_validation_success`
8. `test_preflight_multiple_exchanges_balance_check`
9. `test_rollback_cost_calculation`
10. `test_rollback_on_partial_false`
11. `test_rollback_handles_missing_order_id`

**New Architecture**:
- Preflight checks: `patterns/atomic_multi_order/components/preflight_checker.py`
- Hedge management: `patterns/atomic_multi_order/components/hedge_manager.py`
- Rollback: `patterns/atomic_multi_order/components/rollback_manager.py`
- State: `patterns/atomic_multi_order/components/execution_state.py`

### Edge Case Verification Tests (`test_edge_case_verification.py`) - 3 tests
**Status**: Outdated - depends on atomic multi-order
**Reason**: Same architecture changes as atomic multi-order tests

**Affected Tests**:
1. `test_one_side_fill_hedge_failure_rollback`
2. `test_partial_fill_hedge_partial_fill_rollback`
3. `test_rollback_on_partial_false_no_rollback`

### Order Executor Tests (`test_order_executor.py`) - 5 tests
**Status**: Outdated - refactored architecture
**Reason**: Tests reference `price_fetcher` attribute which no longer exists

**Affected Tests**:
1. `test_execute_limit_respects_configured_offset[offset0]`
2. `test_execute_limit_respects_configured_offset[offset1]`
3. `test_execute_limit_respects_configured_offset[offset2]`
4. `test_execute_limit_uses_executor_default_offset`
5. `test_execute_limit_cancellation_event_triggers_cancel_order`

**New Architecture**: Price fetching now handled by `PriceProvider` class

### Race Conditions Tests (`test_race_conditions.py`) - 3 tests
**Status**: Outdated - refactored rollback
**Reason**: Tests call `_rollback_filled_orders()` which doesn't exist

**Affected Tests**:
1. `test_order_fills_during_cancellation`
2. `test_multiple_simultaneous_rollbacks`
3. `test_rollback_with_slow_exchange_response`

### Position Manager Test (`test_position_manager.py`) - 1 test
**Status**: Unknown - needs investigation

## Strategy Tests (12 tests - MEDIUM PRIORITY)

### Position Opener Tests (`test_position_opener.py`) - 4 tests
**Status**: Needs stub updates
**Reason**: `StubExchangeClient` missing methods, execution flow changed

**Affected Tests**:
1. `test_position_opener_success` - Returns None instead of position
2. `test_position_opener_passes_limit_offset[offset0]` - NoneType error
3. `test_position_opener_passes_limit_offset[offset1]` - NoneType error
4. `test_position_opener_missing_exchange_clients` - Logger signature issue (FIXED)

### Position Closer Tests (`test_position_closer.py`) - 4 tests
**Status**: Needs stub updates
**Reason**: `StubExchangeClient` missing:
- `resolve_contract_id()` method
- `get_quantity_multiplier()` method

**Affected Tests**:
1. `test_handle_liquidation_event_closes_remaining_leg`
2. `test_position_closer_respects_risk_manager_decision`
3. `test_position_closer_fallback_on_divergence_flip`
4. `test_position_closer_respects_max_position_age`

### Integration Tests (`test_funding_arbitrage_integration.py`) - 3 tests
**Status**: Needs investigation
**Reason**: Position creation returning None

**Affected Tests**:
1. `test_open_position_success_integration` - Returns None
2. `test_open_position_merges_existing` - Returns None
3. `test_liquidation_event_closes_surviving_leg` - Missing `resolve_contract_id`

### Other Strategy Tests - 1 test
**Status**: Needs investigation

**Affected Tests**:
1. `test_acceptable_spread_allows_non_critical_exit` - No positions closed
2. `test_non_critical_exit_proceed_on_acceptable_spread` - Wrong execution mode

## Other Tests (0 tests currently)

### Networking Tests (`test_session_proxy.py`) - 1 test
**Status**: Implementation issue
**Reason**: Proxy rotation not creating SOCKS socket

**Affected Tests**:
1. `test_rotate_switches_to_new_proxy`

## Summary

- **Total Outdated**: 35 tests
- **Execution Layer**: 23 tests (66% of failures)
- **Strategy Tests**: 11 tests (31% of failures)
- **Other**: 1 test (3% of failures)

## Recommendations

### Immediate Actions:
1. **Skip outdated execution tests** with clear documentation
2. **Update strategy test stubs** (add missing methods to StubExchangeClient)
3. **Investigate position opener** returning None (likely missing mock setup)

### Long-term Actions:
1. **Rewrite execution layer tests** based on new architecture:
   - Study new component-based design
   - Create test fixtures for new components
   - Write new tests matching current architecture

2. **Create test documentation** for new execution architecture:
   - Document component responsibilities
   - Provide test examples
   - Create reusable test fixtures

3. **Consider test coverage tool** to identify untested code paths

## Estimated Effort

- **Fix strategy stubs**: 2-3 hours
- **Investigate position opener issues**: 2-3 hours
- **Rewrite execution tests**: 20-30 hours
- **Document new architecture**: 8-10 hours

**Total for full fix**: 32-46 hours
