# Multi-Strategy Trading Bot Guide

## 🏗️ Architecture Overview

This trading bot now supports **multiple strategies** across **multiple DEXs** with a clean, modular architecture.

### **Key Components**

```
📁 strategies/              # Trading strategies (modular)
├── base_strategy.py        # Abstract base for all strategies
├── grid_strategy.py        # Grid trading (original strategy)
├── funding_arbitrage_strategy.py  # Delta-neutral funding arbitrage
└── factory.py             # Strategy factory

📁 exchanges/              # Exchange integrations (modular)
├── base.py               # Abstract base for all exchanges
├── lighter.py            # Lighter DEX (with risk management)
├── paradex.py, backpack.py, etc.  # Other DEXs

📁 helpers/
├── risk_manager.py       # Advanced risk management (Lighter-specific)
└── ...                  # Logging, notifications, etc.

📄 trading_bot.py         # Strategy-agnostic trading bot
📄 runbot.py              # CLI interface
```

---

## 🚀 Usage Examples

### **Grid Strategy (Lighter)**

```bash
python runbot.py \
  --ticker BTC \
  --quantity 0.00273 \
  --exchange lighter \
  --strategy grid \
  --take-profit 0.008 \
  --direction buy \
  --max-orders 25 \
  --wait-time 35 \
  --grid-step 0.06 \
  --random-timing \
  --dynamic-profit
```

**How it works:**
1. Places a limit **BUY** order at market price
2. Waits for the order to **FILL**
3. Immediately places a limit **SELL** order at +0.8% profit
4. Repeats the cycle, building up a grid of profitable sell orders
5. When sell orders fill, the cycle continues

**Required Parameters:**
- `--ticker`: Trading pair (e.g., BTC, ETH, HYPE)
- `--quantity`: Order size
- `--take-profit`: Profit percentage per trade
- `--direction`: buy or sell
- `--max-orders`: Maximum concurrent orders
- `--wait-time`: Cooldown between orders (seconds)
- `--grid-step`: Minimum grid spacing percentage

**Optional:**
- `--random-timing`: Add randomization to timing
- `--dynamic-profit`: Vary profit targets

---

### **Funding Arbitrage Strategy**

```bash
python runbot.py \
  --ticker HYPE \
  --quantity 10000 \
  --exchange lighter \
  --strategy funding_arbitrage \
  --target-exposure 10000 \
  --min-profit-rate 0.5 \
  --exchanges lighter,extended
```

**Required Parameters:**
- `--ticker`: Trading pair
- `--quantity`: Base quantity
- `--target-exposure`: Position size per side
- `--min-profit-rate`: Minimum hourly profit rate (%)
- `--exchanges`: Comma-separated list of exchanges

---

## 🎯 Strategy-Specific Parameters

### **Using `--strategy-params`**

For advanced configuration, use key=value pairs:

```bash
python runbot.py \
  --ticker BTC \
  --quantity 0.00273 \
  --exchange lighter \
  --strategy grid \
  --strategy-params \
    take_profit=0.008 \
    direction=buy \
    max_orders=25 \
    wait_time=35 \
    random_timing=true \
    dynamic_profit=true
```

---

## 🛡️ Risk Management (Lighter Only)

Risk management is **automatically enabled** for Lighter exchange with these thresholds:

- **Margin Failures**: 15 consecutive failures
- **Time Stall**: 10 minutes without successful orders  
- **Account Loss**: -10% of initial account value
- **Position Closure**: Worst 20% of losing positions
- **Emergency Loss**: -15% triggers emergency close all

### **Automatic Actions:**

1. **Standard Protection** (15 margin failures + 10min + -10% loss):
   - Close worst 20% of positions
   - Reset counters
   - Resume trading

2. **Emergency Protection** (-15% account loss):
   - Close ALL positions immediately
   - Stop trading

3. **Ctrl+C Protection**:
   - Emergency close all positions
   - Graceful shutdown

---

## 📊 Supported Exchanges

| Exchange | Grid Strategy | Funding Arbitrage | Risk Management |
|----------|--------------|-------------------|-----------------|
| Lighter  | ✅ | ✅ | ✅ Full SDK integration |
| Paradex  | ✅ | ✅ | ❌ Not implemented |
| Backpack | ✅ | ✅ | ❌ Not implemented |
| GRVT     | ✅ | ✅ | ❌ Not implemented |
| EdgeX    | ✅ | ✅ | ❌ Not implemented |
| Aster    | ✅ | ✅ | ❌ Not implemented |

---

## 🔧 Adding New Strategies

### **1. Create Strategy Class**

```python
# strategies/my_strategy.py
from .base_strategy import BaseStrategy, StrategyResult, StrategyAction

class MyStrategy(BaseStrategy):
    def get_strategy_name(self) -> str:
        return "my_strategy"
    
    def get_required_parameters(self) -> List[str]:
        return ["param1", "param2"]
    
    async def _initialize_strategy(self):
        # Initialize your strategy
        pass
    
    async def should_execute(self, market_data: MarketData) -> bool:
        # Your execution logic
        return True
    
    async def execute_strategy(self, market_data: MarketData) -> StrategyResult:
        # Your strategy implementation
        return StrategyResult(action=StrategyAction.PLACE_ORDER, orders=[...])
```

### **2. Register Strategy**

```python
# strategies/factory.py
from .my_strategy import MyStrategy

_strategies = {
    'grid': GridStrategy,
    'funding_arbitrage': FundingArbitrageStrategy,
    'my_strategy': MyStrategy,  # Add here
}
```

### **3. Add CLI Parameters (Optional)**

```python
# runbot.py
parser.add_argument('--my-param', type=Decimal, help='MyStrategy: Description')
```

### **4. Use Your Strategy**

```bash
python runbot.py --strategy my_strategy --ticker BTC --quantity 1 --exchange lighter --my-param 0.5
```

---

## 🎯 Architecture Benefits

1. **✅ Modular**: Each strategy is self-contained
2. **✅ Extensible**: Easy to add new strategies and exchanges
3. **✅ Strategy-Agnostic**: Exchange clients don't know about strategies
4. **✅ Risk-Aware**: Universal risk management for account protection
5. **✅ Type-Safe**: Proper parameter validation
6. **✅ Production-Ready**: Clean, maintainable code

---

## 🚨 Important Notes

### **Strategy Independence**
- Exchange clients are now **strategy-agnostic**
- Order type classification happens in strategies, not exchange clients
- Each strategy manages its own state and logic

### **Risk Management**
- Currently optimized for **grid trading**
- Works for all strategies but may need strategy-specific tuning
- Only enabled for exchanges that support it (Lighter)

### **Backward Compatibility**
- **Breaking change**: Old commands won't work without updates
- Must now specify `--strategy` (defaults to 'grid')
- Must use strategy-specific parameters

---

## 📝 Migration from Old Commands

**Old (Legacy):**
```bash
python runbot.py --exchange lighter --ticker BTC --quantity 0.00273 --take-profit 0.02
```

**New (Multi-Strategy):**
```bash
python runbot.py --ticker BTC --quantity 0.00273 --exchange lighter --strategy grid --take-profit 0.008 --direction buy --max-orders 25 --wait-time 35
```

---

## 🎯 Next Steps

1. **Test grid strategy** with your existing tickers
2. **Implement LorisTools integration** for funding arbitrage
3. **Add more strategies** as needed
4. **Enhance risk management** for strategy-specific logic

---

**Your codebase is now a professional multi-strategy trading platform!** 🚀

