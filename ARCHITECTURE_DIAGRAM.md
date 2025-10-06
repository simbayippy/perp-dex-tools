# 🎨 Architecture Visual Diagrams

## 📊 System Architecture Overview

```
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃                      USER COMMAND                          ┃
┃  python runbot.py --ticker BTC --strategy grid             ┃
┃                    --exchange lighter                       ┃
┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛
                            ↓
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃  LAYER 1: runbot.py (Entry Point & Configuration)          ┃
┣━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┫
┃  • Parse CLI arguments                                      ┃
┃  • Build strategy_params dict                               ┃
┃  • Create TradingConfig                                     ┃
┃  • Initialize TradingBot                                    ┃
┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛
                            ↓
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃  LAYER 2: trading_bot.py (Orchestration)                   ┃
┣━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┫
┃  __init__:                                                  ┃
┃    ┌─────────────────┐  ┌──────────────────┐              ┃
┃    │ ExchangeFactory │  │ StrategyFactory  │              ┃
┃    │ .create()       │  │ .create()        │              ┃
┃    └────────┬────────┘  └────────┬─────────┘              ┃
┃             ↓                     ↓                         ┃
┃    exchange_client         strategy                        ┃
┃             ↓                     ↓                         ┃
┃  run():                                                     ┃
┃    while not shutdown:                                      ┃
┃      1. Check risk conditions                               ┃
┃      2. Get market data                                     ┃
┃      3. if strategy.should_execute():                      ┃
┃           result = strategy.execute_strategy()             ┃
┃           handle_strategy_result(result)                   ┃
┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛
              ↓                               ↓
┏━━━━━━━━━━━━━━━━━━━━━━━━┓    ┏━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ LAYER 3A: Strategy      ┃    ┃ LAYER 3B: Exchange       ┃
┃ (The Manager)           ┃    ┃ (The Implementer)        ┃
┣━━━━━━━━━━━━━━━━━━━━━━━━┫    ┣━━━━━━━━━━━━━━━━━━━━━━━━━┫
┃ grid_strategy.py        ┃    ┃ lighter.py               ┃
┃                         ┃    ┃                          ┃
┃ Decides:                ┃    ┃ Implements:              ┃
┃ • WHEN to trade         ┃    ┃ • HOW to connect         ┃
┃ • WHAT to buy/sell      ┃    ┃ • HOW to submit orders   ┃
┃ • HOW MUCH profit       ┃    ┃ • HOW to wait for fills  ┃
┃ • Order sequencing      ┃    ┃ • HOW to get prices      ┃
┃                         ┃    ┃                          ┃
┃ Uses:                   ┃    ┃ Uses:                    ┃
┃ exchange_client.        ┃    ┃ Lighter SDK              ┃
┃   place_open_order() ───┼────┼→ lighter_client.         ┃
┃                         ┃    ┃   create_order()         ┃
┗━━━━━━━━━━━━━━━━━━━━━━━━┛    ┗━━━━━━━━━━━━━━━━━━━━━━━━━┛
                                              ↓
                            ┏━━━━━━━━━━━━━━━━━━━━━━━━━┓
                            ┃ LAYER 4: External API    ┃
                            ┣━━━━━━━━━━━━━━━━━━━━━━━━━┫
                            ┃ Lighter SDK              ┃
                            ┃ • Blockchain tx          ┃
                            ┃ • Signature signing      ┃
                            ┃ • Network communication  ┃
                            ┗━━━━━━━━━━━━━━━━━━━━━━━━━┛
```

---

## 🔄 Grid Strategy State Machine

```
┌──────────────┐
│    START     │
└──────┬───────┘
       │
       ↓
┌─────────────────────────────────────┐
│  STATE: "ready"                     │
│  ────────────────────────────────  │
│  strategy.execute_strategy():       │
│    → Check conditions               │
│    → Return: PLACE_ORDER            │
│       orders=[buy 0.00273 BTC]     │
└──────┬──────────────────────────────┘
       │
       ↓ TradingBot executes order
       │
┌─────────────────────────────────────┐
│  STATE: "waiting_for_fill"          │
│  ────────────────────────────────  │
│  Waiting for:                       │
│    • Order fills on exchange        │
│    • WebSocket notification         │
│    • strategy.notify_order_filled() │
└──────┬──────────────────────────────┘
       │
       ↓ Got fill notification
       │
┌─────────────────────────────────────┐
│  STATE: "waiting_for_fill"          │
│  (with filled_price set)            │
│  ────────────────────────────────  │
│  strategy.execute_strategy():       │
│    → Calculate take-profit price    │
│    → Return: PLACE_ORDER            │
│       orders=[sell @ $62,049.60]   │
└──────┬──────────────────────────────┘
       │
       ↓ TradingBot executes close order
       │
┌─────────────────────────────────────┐
│  STATE: Reset to "ready"            │
│  ────────────────────────────────  │
│  • Cycle complete                   │
│  • Ready for next iteration         │
└──────┬──────────────────────────────┘
       │
       └─────→ Loop back to START
```

---

## 🔌 Modular Design Matrix

### **Strategies × Exchanges = Infinite Combinations**

```
                EXCHANGES →
STRATEGIES ↓   Lighter  Paradex  Backpack  GRVT  EdgeX  Aster
─────────────────────────────────────────────────────────────
Grid            ✅       ✅       ✅       ✅    ✅     ✅
Funding Arb     ✅       ✅       ✅       ✅    ✅     ✅
Momentum        ✅       ✅       ✅       ✅    ✅     ✅
Scalping        ✅       ✅       ✅       ✅    ✅     ✅
Your Strategy   ✅       ✅       ✅       ✅    ✅     ✅

All combinations work because interfaces are properly abstracted!
```

---

## 🎯 Data Flow: Single Grid Trade

```
1. CLI Command
   python runbot.py --ticker BTC --quantity 0.00273 --strategy grid --exchange lighter
   
2. runbot.py
   config = TradingConfig(ticker='BTC', quantity=0.00273, strategy='grid', exchange='lighter')
   bot = TradingBot(config)
   
3. trading_bot.py __init__
   exchange_client = LighterClient(config)
   strategy = GridStrategy(config, exchange_client)
   risk_manager = RiskManager(exchange_client, config)
   
4. trading_bot.py run()
   await exchange_client.connect()        → Connect to Lighter WebSocket
   await strategy.initialize()             → Initialize grid state machine
   await risk_manager.initialize()         → Get baseline account value
   
5. Main Loop Iteration #1
   market_data = await strategy.get_market_data()
   └→ exchange_client.fetch_bbo_prices()  → Get BBO from WebSocket
      Returns: MarketData(best_bid=61995, best_ask=62005)
   
   should_execute = await strategy.should_execute(market_data)
   └→ Check wait time, max orders, grid step
      Returns: True
   
   result = await strategy.execute_strategy(market_data)
   └→ State="ready", create open order
      Returns: StrategyResult(
          action=PLACE_ORDER,
          orders=[OrderParams(side='buy', quantity=0.00273)]
      )
   
   await _handle_strategy_result(result)
   └→ await _execute_order(order_params)
      └→ await exchange_client.place_open_order(contract_id=0, quantity=0.00273, direction='buy')
         └→ Format order for Lighter SDK
         └→ await lighter_client.create_order(market_index=0, base_amount=273000, ...)
         └→ Wait for fill (poll self.current_order.status)
         └→ Returns: OrderResult(success=True, price=62000, status='FILLED')
      └→ strategy.notify_order_filled(price=62000, quantity=0.00273)
         └→ Set filled_price in strategy state

6. Main Loop Iteration #2
   should_execute = await strategy.should_execute(market_data)
   └→ Check conditions again
      Returns: True (immediately - no wait time for close orders)
   
   result = await strategy.execute_strategy(market_data)
   └→ State="waiting_for_fill", has filled_price
   └→ Calculate: close_price = 62000 * (1 + 0.008%) = 62049.60
      Returns: StrategyResult(
          action=PLACE_ORDER,
          orders=[OrderParams(side='sell', price=62049.60)]
      )
   
   await _handle_strategy_result(result)
   └→ await exchange_client.place_close_order(contract_id=0, quantity=0.00273, price=62049.60, side='sell')
      └→ await lighter_client.create_order(...)
      └→ Returns: OrderResult(success=True, status='OPEN')

7. Cycle Complete
   strategy state resets to "ready"
   Wait 35 seconds (grid wait_time)
   Repeat from step 5
```

---

## 🎨 Component Interaction Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                         USER                                     │
│                           │                                      │
│                           ↓                                      │
│  ┌────────────────────────────────────────────────────────┐    │
│  │  runbot.py (CLI)                                        │    │
│  │  • Parse arguments                                      │    │
│  │  • Build config                                         │    │
│  └────────────────────────┬───────────────────────────────┘    │
│                           │                                      │
│                           ↓ creates                             │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  trading_bot.py (Orchestrator)                           │  │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────────┐         │  │
│  │  │ Exchange │  │ Strategy │  │ RiskManager  │         │  │
│  │  │ Factory  │  │ Factory  │  │              │         │  │
│  │  └────┬─────┘  └────┬─────┘  └──────┬───────┘         │  │
│  │       │             │                │                  │  │
│  │       │ creates     │ creates        │ creates          │  │
│  │       ↓             ↓                ↓                  │  │
│  │  ┌─────────┐  ┌──────────┐  ┌──────────────┐         │  │
│  │  │Exchange │  │ Strategy │  │ RiskManager  │         │  │
│  │  │ Client  │←─│ Instance │  │ Instance     │         │  │
│  │  └────┬────┘  └────┬─────┘  └──────┬───────┘         │  │
│  └───────┼────────────┼────────────────┼─────────────────┘  │
│          │            │                │                     │
│          │            │                │                     │
└──────────┼────────────┼────────────────┼─────────────────────┘
           │            │                │
           ↓            ↓                ↓
┌──────────────────────────────────────────────────────────────┐
│  STRATEGY LAYER          │  EXCHANGE LAYER                   │
│  (The Manager)           │  (The Implementer)                │
│  ──────────────────────  │  ────────────────────────────    │
│                          │                                   │
│  BaseStrategy (ABC)      │  BaseExchangeClient (ABC)        │
│       ↑                  │       ↑                           │
│       │ implements       │       │ implements                │
│       │                  │       │                           │
│  GridStrategy            │  LighterClient                    │
│  • should_execute()      │  • connect()                      │
│  • execute_strategy()    │  • place_open_order()            │
│  • calculate wait time   │  • place_close_order()           │
│  • check grid step       │  • fetch_bbo_prices()            │
│  • calc take-profit      │  • WebSocket management          │
│                          │                                   │
│  FundingArbStrategy      │  ParadexClient                    │
│  • check funding rates   │  • Paradex SDK integration        │
│  • execute arbitrage     │                                   │
│                          │  BackpackClient, GRVTClient, ...  │
└──────────────────────────┴───────────────────────────────────┘
                                    ↓
                          ┌──────────────────────┐
                          │  EXTERNAL APIs/SDKs  │
                          │  ───────────────────│
                          │  • Lighter SDK       │
                          │  • Paradex SDK       │
                          │  • Backpack API      │
                          │  • Blockchain comms  │
                          └──────────────────────┘
```

---

## 🔄 Request-Response Flow

### **Strategy Decision Flow:**

```
TradingBot                 GridStrategy              LighterClient
    │                          │                           │
    │ get_market_data() ───────┼──→ fetch_bbo_prices() ───→ WebSocket
    │                          │   ← Returns: (bid, ask)    │
    │ ← Returns: MarketData    │                           │
    │                          │                           │
    │ should_execute() ────────→ Checks:                   │
    │                          │ • Wait time > 35s?        │
    │                          │ • Orders < 25?            │
    │                          │ • Grid step OK?           │
    │ ← Returns: True          │                           │
    │                          │                           │
    │ execute_strategy() ──────→ Decides:                  │
    │                          │ • State = "ready"         │
    │                          │ • Create buy order        │
    │ ← StrategyResult         │                           │
    │   (PLACE_ORDER,          │                           │
    │    orders=[buy BTC])     │                           │
    │                          │                           │
    │ _execute_order() ────────┼──→ place_open_order() ───→ Lighter SDK
    │                          │                           │ • Submit order
    │                          │                           │ • Wait for fill
    │                          │                           │ • Poll status
    │                          │   ← OrderResult(filled)   │
    │                          │ ← notify_order_filled()   │
    │                          │   (sets filled_price)     │
    │                          │                           │
    │ (Next iteration)         │                           │
    │ execute_strategy() ──────→ Decides:                  │
    │                          │ • State = "waiting"       │
    │                          │ • Has filled_price        │
    │                          │ • Calc: 62000 * 1.00008  │
    │                          │ • Create sell order       │
    │ ← StrategyResult         │                           │
    │   (PLACE_ORDER,          │                           │
    │    orders=[sell @ 62049])│                           │
    │                          │                           │
    │ _execute_order() ────────┼──→ place_close_order() ──→ Lighter SDK
    │                          │                           │
    │ ← Success                │                           │
    │                          │                           │
    └──────────────────────────┴───────────────────────────┘
```

---

## 🧩 Modularity Explained

### **Why This Architecture is Powerful:**

#### **1. Strategy Independence**

```
GridStrategy ONLY knows:
  ✅ "I want to buy 0.00273 BTC"
  ✅ "I want to sell at +0.8% profit"
  
GridStrategy DOESN'T know:
  ❌ How to connect to Lighter
  ❌ Lighter API endpoint URLs
  ❌ Lighter SDK method signatures
  ❌ Order format requirements

Result: Same GridStrategy works on Lighter, Paradex, Backpack, etc.
```

#### **2. Exchange Independence**

```
LighterClient ONLY knows:
  ✅ How to call Lighter SDK
  ✅ How to format Lighter orders
  ✅ How to wait for Lighter fills
  
LighterClient DOESN'T know:
  ❌ Why you're trading (grid? arbitrage?)
  ❌ When to trade (strategy decides)
  ❌ Profit targets (strategy calculates)
  ❌ Risk thresholds (risk manager handles)

Result: LighterClient works with Grid, FundingArb, Momentum, etc.
```

#### **3. No Tight Coupling**

```
❌ BAD (Tight Coupling):
   if strategy == 'grid' and exchange == 'lighter':
       do_grid_on_lighter()
   elif strategy == 'grid' and exchange == 'paradex':
       do_grid_on_paradex()
   # ... 18 combinations = 18 special cases!

✅ GOOD (Loose Coupling):
   strategy_result = await strategy.execute_strategy(market_data)
   await self._handle_strategy_result(strategy_result)
   # Works for ANY strategy + ANY exchange
   # 3 strategies × 6 exchanges = 18 combinations, 0 special cases!
```

---

## 🚀 Extensibility Examples

### **Adding a New Strategy: Scalping**

```python
# Step 1: Create strategy file
# strategies/scalping_strategy.py
class ScalpingStrategy(BaseStrategy):
    def get_strategy_name(self) -> str:
        return "scalping"
    
    def get_required_parameters(self) -> List[str]:
        return ["tick_profit", "max_spread"]
    
    async def should_execute(self, market_data) -> bool:
        spread = market_data.best_ask - market_data.best_bid
        return spread < self.get_parameter('max_spread')
    
    async def execute_strategy(self, market_data) -> StrategyResult:
        # Your scalping logic
        return StrategyResult(
            action=StrategyAction.PLACE_ORDER,
            orders=[OrderParams(side='buy', ...)]
        )

# Step 2: Register in factory (ONE LINE)
# strategies/factory.py
_strategies = {
    'grid': GridStrategy,
    'funding_arbitrage': FundingArbitrageStrategy,
    'scalping': ScalpingStrategy,  # ← Add here
}

# Step 3: Use it on ANY exchange
python runbot.py --strategy scalping --exchange lighter --ticker BTC ...
python runbot.py --strategy scalping --exchange paradex --ticker ETH ...
python runbot.py --strategy scalping --exchange backpack --ticker SOL ...
```

**No changes to:**
- ❌ `trading_bot.py`
- ❌ Any exchange clients
- ❌ Risk manager
- ❌ Orchestration logic

---

### **Adding a New Exchange: dYdX**

```python
# Step 1: Create exchange file
# exchanges/dydx.py
class DydxClient(BaseExchangeClient):
    async def connect(self):
        # Connect to dYdX API
        self.dydx_client = DydxClient(api_key=...)
        await self.dydx_client.connect()
    
    async def place_open_order(self, contract_id, quantity, direction):
        # Use dYdX SDK
        order = await self.dydx_client.place_order(
            market=contract_id,
            size=quantity,
            side=direction,
            type='LIMIT'
        )
        # Wait for fill...
        return OrderResult(...)
    
    # ... implement all required methods

# Step 2: Register in factory (ONE LINE)
# exchanges/factory.py
EXCHANGE_CLASSES = {
    'lighter': 'exchanges.lighter.LighterClient',
    'paradex': 'exchanges.paradex.ParadexClient',
    'dydx': 'exchanges.dydx.DydxClient',  # ← Add here
}

# Step 3: Use it with ANY strategy
python runbot.py --strategy grid --exchange dydx --ticker BTC ...
python runbot.py --strategy funding_arbitrage --exchange dydx --ticker HYPE ...
python runbot.py --strategy scalping --exchange dydx --ticker ETH ...
```

**No changes to:**
- ❌ `trading_bot.py`
- ❌ Any strategies
- ❌ Risk manager
- ❌ Orchestration logic

---

## 📐 Design Patterns Used

### **1. Strategy Pattern**
```python
# Different algorithms (strategies) are interchangeable
strategy = StrategyFactory.create_strategy('grid', ...)
strategy = StrategyFactory.create_strategy('funding_arbitrage', ...)
# Both implement same interface, work the same way
```

### **2. Factory Pattern**
```python
# Creation logic is centralized
exchange_client = ExchangeFactory.create_exchange('lighter', config)
strategy = StrategyFactory.create_strategy('grid', config, exchange_client)
```

### **3. Dependency Injection**
```python
# Strategy receives exchange_client as dependency
class GridStrategy(BaseStrategy):
    def __init__(self, config, exchange_client):
        self.exchange_client = exchange_client  # Injected, not created
```

### **4. State Machine Pattern**
```python
# Grid strategy uses state machine for multi-step flow
cycle_state = "ready" → "waiting_for_fill" → "ready"
```

### **5. Template Method Pattern**
```python
# BaseStrategy defines the structure, subclasses fill in details
class BaseStrategy(ABC):
    async def initialize(self):  # Template method
        await self._initialize_strategy()  # Hook for subclass
```


