# 🧪 Testing Quick Start

Quick commands to test the security fixes.

## Run All Security Tests

```bash
# From project root
cd /Users/yipsimba/perp-dex-tools

# Run all execution layer tests
pytest tests/execution/ -v

# Expected output:
# tests/execution/test_atomic_multi_order.py ................ 
# tests/execution/test_position_manager.py ..................
# tests/execution/test_race_conditions.py ...................
# ======================== 45 passed in 2.34s =========================
```

## Run Only Critical Fix Tests

```bash
# Test the 4 critical fixes
pytest tests/execution/ -k "rollback_race_condition or balance_validation or duplicate_detection or simultaneous_close" -v
```

## Run Individual Test Files

```bash
# Atomic executor tests (CRITICAL #1 & #2)
pytest tests/execution/test_atomic_multi_order.py -v

# Position manager tests (CRITICAL #3 & #4)
pytest tests/execution/test_position_manager.py -v

# Race condition tests (Integration)
pytest tests/execution/test_race_conditions.py -v
```

## Run Specific Critical Tests

```bash
# Test rollback race condition fix
pytest tests/execution/test_atomic_multi_order.py::test_rollback_race_condition_protection -v

# Test balance validation
pytest tests/execution/test_atomic_multi_order.py::test_preflight_balance_validation_failure -v

# Test duplicate detection
pytest tests/execution/test_position_manager.py::test_create_position_duplicate_detection_memory -v

# Test concurrent close prevention
pytest tests/execution/test_position_manager.py::test_simultaneous_close_prevention -v
```

## Run with Coverage

```bash
# Generate coverage report
pytest tests/execution/ --cov=strategies/execution --cov=strategies/implementations/funding_arbitrage --cov-report=html

# Open coverage report
open htmlcov/index.html
```

## Debugging Failed Tests

```bash
# Show print statements and logs
pytest tests/execution/test_atomic_multi_order.py -v -s

# Full traceback on failure
pytest tests/execution/ -v --tb=long

# Stop at first failure
pytest tests/execution/ -x

# Drop into debugger on failure
pytest tests/execution/test_atomic_multi_order.py --pdb
```

## Expected Results

All tests should PASS:

```
============================= test session starts ==============================
platform darwin -- Python 3.11.x, pytest-7.x.x, pluggy-1.x.x
collected 45 items

tests/execution/test_atomic_multi_order.py::test_atomic_execution_success PASSED
tests/execution/test_atomic_multi_order.py::test_partial_fill_triggers_rollback PASSED
tests/execution/test_atomic_multi_order.py::test_rollback_race_condition_protection PASSED
tests/execution/test_atomic_multi_order.py::test_preflight_balance_validation_success PASSED
tests/execution/test_atomic_multi_order.py::test_preflight_balance_validation_failure PASSED
...

========================= 45 passed in 2.34s ===============================
```

## What Each Test Validates

### Critical Fix #1: Rollback Race Condition
✅ `test_rollback_race_condition_protection` - Orders don't fill more during rollback  
✅ `test_order_fills_during_cancellation` - Handles fill-during-cancel race

### Critical Fix #2: Balance Validation
✅ `test_preflight_balance_validation_success` - Allows trades with sufficient funds  
✅ `test_preflight_balance_validation_failure` - Blocks trades with insufficient funds  
✅ `test_preflight_multiple_exchanges_balance_check` - Multi-exchange validation

### Critical Fix #3: Duplicate Detection
✅ `test_create_position_duplicate_detection_memory` - Prevents memory duplicates  
✅ `test_create_position_duplicate_detection_database` - Prevents DB duplicates  
✅ `test_add_position_duplicate_detection` - Prevents duplicates in add_position()

### Critical Fix #4: Position Locking
✅ `test_simultaneous_close_prevention` - Only one close succeeds  
✅ `test_close_already_closed_position` - Handles already-closed gracefully  
✅ `test_concurrent_close_attempts_race` - Multiple simultaneous closes

## Troubleshooting

### "ModuleNotFoundError: No module named 'strategies'"
```bash
# Make sure you're in project root
cd /Users/yipsimba/perp-dex-tools

# Add to PYTHONPATH
export PYTHONPATH=$PYTHONPATH:/Users/yipsimba/perp-dex-tools
```

### "No tests collected"
```bash
# Make sure pytest can find tests
pytest tests/execution/ --collect-only
```

### "Database not available" warnings
This is expected for unit tests. Tests mock the database.

## Next Steps After Tests Pass

1. ✅ All tests passing → Fixes are working
2. 📊 Review coverage report → Ensure >90% coverage
3. 🔍 Review `docs/SECURITY_AUDIT.md` → Understand vulnerabilities
4. 📖 Review `docs/SECURITY_FIXES_SUMMARY.md` → See what was fixed
5. 🚀 Test on testnet with small amounts → Validate in real environment

## Quick Test During Development

```bash
# Watch mode - rerun on file changes
pytest-watch tests/execution/

# Or use pytest-xdist for parallel execution
pytest tests/execution/ -n auto
```

## CI/CD Integration

Add to your GitHub Actions:

```yaml
- name: Run Security-Critical Tests
  run: pytest tests/execution/ -v --cov --cov-report=xml
  
- name: Upload Coverage
  uses: codecov/codecov-action@v3
  with:
    file: ./coverage.xml
```

---

**Remember:** All 45 tests should PASS before production deployment! 🚨

