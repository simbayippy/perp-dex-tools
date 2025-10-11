# Unified Logging Migration Guide

This guide explains how to migrate from the current `TradingLogger` to the new `UnifiedLogger` system for consistent, colored, and informative logging across all components.

## 🎯 Goals

- **Consistent formatting** across all components (exchanges, strategies, services)
- **Colored console output** with source location (file:function:line)
- **Component-specific context** (exchange name, strategy name, ticker, etc.)
- **Backward compatibility** with existing `.log()` method calls
- **Better debugging** with structured logging and source tracking

## 📊 Before vs After

### Before (Current TradingLogger)
```
2025-10-11 16:39:39.677 - INFO - [ASTER_ALL] Connected to Aster WebSocket with listen key
```

### After (UnifiedLogger)
```
2025-10-11 16:39:39 | INFO     | EXCHANGE:ASTER:ticker=BTC | aster.client:connect:192 - Connected to Aster WebSocket with listen key
```

## 🔄 Migration Steps

### 1. Exchange Clients

**Before:**
```python
from helpers.logger import TradingLogger

class AsterClient(BaseExchangeClient):
    def __init__(self, config):
        # ...
        self.logger = TradingLogger(exchange="aster", ticker=self.config.ticker, log_to_console=True)
        
    def some_method(self):
        self.logger.log("Connected to WebSocket", "INFO")
        self.logger.log("Debug message", "DEBUG")
        self.logger.log("Warning message", "WARNING")
        self.logger.log("Error occurred", "ERROR")
```

**After:**
```python
from helpers.unified_logger import get_exchange_logger

class AsterClient(BaseExchangeClient):
    def __init__(self, config):
        # ...
        self.logger = get_exchange_logger("aster", self.config.ticker)
        
    def some_method(self):
        self.logger.info("Connected to WebSocket")
        self.logger.debug("Debug message")
        self.logger.warning("Warning message")
        self.logger.error("Error occurred")
        
        # Backward compatibility - still works!
        self.logger.log("Still works", "INFO")
```

### 2. Trading Strategies

**Before:**
```python
from helpers.logger import TradingLogger

class FundingArbitrageStrategy(StatefulStrategy):
    def __init__(self, config, exchange_client):
        # ...
        self.logger = TradingLogger(exchange="multi", ticker="funding_arb", log_to_console=True)
        
    def execute_cycle(self):
        self.logger.log("🔍 Scanning for opportunities...", "INFO")
        self.logger.log("⚠️ Position needs rebalancing", "WARNING")
```

**After:**
```python
from helpers.unified_logger import get_strategy_logger

class FundingArbitrageStrategy(StatefulStrategy):
    def __init__(self, config, exchange_client):
        # ...
        self.logger = get_strategy_logger("funding_arbitrage")
        
    def execute_cycle(self):
        self.logger.info("🔍 Scanning for opportunities...")
        self.logger.warning("⚠️ Position needs rebalancing")
        
        # Add context for specific operations
        position_logger = self.logger.with_context(position_id="abc123")
        position_logger.info("Closing position due to profit erosion")
```

### 3. Services

**Before:**
```python
import logging
logger = logging.getLogger(__name__)
```

**After:**
```python
from helpers.unified_logger import get_service_logger

logger = get_service_logger("funding_rate_service")
```

## 🎨 New Features

### 1. Component-Specific Context
```python
# Exchange logger with ticker context
logger = get_exchange_logger("aster", "BTC")
# Output: EXCHANGE:ASTER:ticker=BTC

# Strategy logger with multiple context
logger = get_strategy_logger("funding_arbitrage", account="test", mode="paper")
# Output: STRATEGY:FUNDING_ARBITRAGE:account=test:mode=paper
```

### 2. Dynamic Context Addition
```python
base_logger = get_exchange_logger("aster", "BTC")

# Add order-specific context
order_logger = base_logger.with_context(order_id="12345", side="buy")
order_logger.info("Order placed successfully")
# Output includes order_id=12345:side=buy in context
```

### 3. Structured Transaction Logging
```python
# Maintains compatibility with existing transaction logging
logger.log_transaction(
    order_id="12345",
    side="buy", 
    quantity="10.5",
    price="50000.00",
    status="filled"
)
```

### 4. Better Error Tracking
```python
try:
    # Some operation
    pass
except Exception as e:
    logger.error(f"Operation failed: {e}", exc_info=True)
    # Automatically includes stack trace and source location
```

## 📁 File Organization

### Log Files Structure
```
logs/
├── exchange_aster_activity.log      # All Aster exchange logs
├── exchange_aster_errors.log        # Aster error logs only
├── strategy_funding_arbitrage_activity.log
├── strategy_funding_arbitrage_errors.log
├── service_funding_rate_service_activity.log
└── service_funding_rate_service_errors.log
```

### Log Rotation
- **Activity logs**: 100 MB rotation, 7 days retention
- **Error logs**: 10 MB rotation, 30 days retention
- **Compression**: Automatic zip compression of rotated logs

## 🔧 Migration Checklist

### For Each Exchange Client:
- [ ] Replace `from helpers.logger import TradingLogger`
- [ ] Replace `with from helpers.unified_logger import get_exchange_logger`
- [ ] Update logger initialization: `self.logger = get_exchange_logger("exchange_name", ticker)`
- [ ] Replace `.log(message, "LEVEL")` calls with `.level(message)` (optional, backward compatible)
- [ ] Test logging output and verify context appears correctly

### For Each Strategy:
- [ ] Replace logger import
- [ ] Update logger initialization: `self.logger = get_strategy_logger("strategy_name")`
- [ ] Update log calls (optional)
- [ ] Add context where beneficial (`with_context()`)
- [ ] Test logging output

### For Services:
- [ ] Replace logger import  
- [ ] Update logger initialization: `self.logger = get_service_logger("service_name")`
- [ ] Update log calls
- [ ] Test logging output

## 🧪 Testing

### Test Console Output
```python
logger = get_exchange_logger("aster", "BTC")
logger.info("Test message")
# Should see colored output with source location
```

### Test File Output
```python
# Check that log files are created in logs/ directory
# Verify rotation and error separation works
```

### Test Context
```python
logger = get_exchange_logger("aster", "BTC")
order_logger = logger.with_context(order_id="test123")
order_logger.info("Test with context")
# Verify context appears in logs
```

## 🚀 Rollout Plan

1. **Phase 1**: Update core exchange clients (Aster, Lighter, GRVT)
2. **Phase 2**: Update main strategies (Grid, Funding Arbitrage) 
3. **Phase 3**: Update remaining components
4. **Phase 4**: Remove old TradingLogger class

## 🔍 Troubleshooting

### Import Errors
```python
# If you get import errors, make sure the path is correct:
from helpers.unified_logger import get_exchange_logger, get_strategy_logger
```

### Missing Context
```python
# If context is not appearing, check logger initialization:
logger = get_exchange_logger("exchange_name", "ticker_name")  # ✅ Good
logger = get_exchange_logger("exchange_name")  # ⚠️ Missing ticker context
```

### Log Files Not Created
```python
# Check that logs directory exists and is writable
# UnifiedLogger creates it automatically, but permissions matter
```

## 💡 Best Practices

1. **Use specific log levels**: `debug()`, `info()`, `warning()`, `error()`, `critical()`
2. **Add context for important operations**: Use `with_context()` for order tracking, position management, etc.
3. **Include emojis for visual scanning**: `🔍`, `✅`, `⚠️`, `❌`, `📊`, `💰`
4. **Use structured data**: Pass additional data as keyword arguments
5. **Keep messages concise but informative**: Include key identifiers (order_id, symbol, etc.)

## 🔄 Backward Compatibility

The new system is fully backward compatible:
- Existing `.log(message, "LEVEL")` calls continue to work
- Existing `.log_transaction()` calls continue to work  
- File locations remain in `logs/` directory
- Log rotation and retention settings are preserved

You can migrate incrementally without breaking existing functionality!
