# 🏗️ Trading Bot Architecture

## Overview

This is a **multi-strategy, multi-exchange trading bot** built with a clean, modular architecture that separates concerns into three distinct layers:

1. **Strategy Layer** (The Manager - Business Logic)
2. **Exchange Layer** (The Implementer - API/SDK Integration)
3. **Orchestration Layer** (The Supervisor - Coordination)

---

## 🎯 The Manager-Implementer Pattern

Think of this like a **portfolio management firm**:

| Layer | Real-World Analogy | Responsibility | Code Location |
|-------|-------------------|----------------|---------------|
| **Strategy** | **Portfolio Manager** | Decides WHAT to trade, WHEN, and WHY | `strategies/grid_strategy.py` |
| **Exchange** | **Stock Broker** | Executes trades via exchange APIs | `exchanges/lighter.py` |
| **Trading Bot** | **Operations Supervisor** | Coordinates everything | `trading_bot.py` |
| **External SDK** | **Stock Exchange** | Actual market infrastructure | Lighter SDK, Paradex SDK |

---

## 📊 Layer 1: Entry Point (`runbot.py`)

**Role**: Command-line interface and configuration builder

### **Responsibilities:**
- ✅ Parse CLI arguments
- ✅ Validate strategy-specific requirements  
- ✅ Build configuration object
- ✅ Initialize and run the trading bot

### **What it does NOT do:**
- ❌ No trading logic
- ❌ No strategy decisions
- ❌ No exchange communication

### **Flow:**
```python
1. Parse CLI: --ticker BTC --quantity 0.00273 --strategy grid --exchange lighter
2. Build strategy_params: {take_profit: 0.008, direction: 'buy', ...}
3. Create TradingConfig(ticker='BTC', exchange='lighter', strategy='grid', ...)
4. Create TradingBot(config)
5. Run: await bot.run()
```

---

## 📊 Layer 2: Orchestration (`trading_bot.py`)

**Role**: Strategy-agnostic coordinator and supervisor

### **The Supervisor's Job:**

```python
class TradingBot:
    def __init__(self, config):
        # 1. Hire a broker (exchange client)
        self.exchange_client = ExchangeFactory.create_exchange(config.exchange, config)
        
        # 2. Hire a portfolio manager (strategy)
        self.strategy = StrategyFactory.create_strategy(config.strategy, config, exchange_client)
        
        # 3. Hire a risk manager (if exchange supports it)
        self.risk_manager = RiskManager(exchange_client, config)
```

### **Main Loop (Universal for All Strategies):**

```python
async def run(self):
    # Connect to exchange
    await self.exchange_client.connect()
    
    # Initialize strategy and risk manager
    await self.strategy.initialize()
    await self.risk_manager.initialize()
    
    # Main trading loop
    while not shutdown:
        # 1. Check risk conditions (account protection)
        risk_action = await self.risk_manager.check_risk_conditions()
        if risk_action != NONE:
            await self._handle_risk_action(risk_action)
        
        # 2. Get market data
        market_data = await self.strategy.get_market_data()
        
        # 3. Ask strategy: "Should we trade?"
        if await self.strategy.should_execute(market_data):
            # 4. Ask strategy: "What should we do?"
            strategy_result = await self.strategy.execute_strategy(market_data)
            
            # 5. Execute the strategy's decision
            await self._handle_strategy_result(strategy_result)
```

### **Key Methods:**

```python
async def _handle_strategy_result(self, strategy_result):
    """Universal handler for all strategies"""
    if strategy_result.action == PLACE_ORDER:
        for order in strategy_result.orders:
            await self._execute_order(order)
    elif strategy_result.action == WAIT:
        await asyncio.sleep(strategy_result.wait_time)
    # ... handles all StrategyAction types

async def _execute_order(self, order_params):
    """Universal order executor"""
    if order_params.order_type == "market":
        result = await self.exchange_client.place_market_order(...)
    else:
        result = await self.exchange_client.place_open_order(...)
```

### **What it does NOT do:**
- ❌ No trading decisions (when/what to trade)
- ❌ No profit calculations
- ❌ No exchange-specific logic
- ❌ **NO strategy-specific special cases!**

---

## 📊 Layer 3: Strategy Layer (`strategies/`)

**Role**: The Portfolio Manager - Makes all trading decisions

### **Interface Contract (BaseStrategy):**

```python
class BaseStrategy(ABC):
    @abstractmethod
    async def should_execute(self, market_data) -> bool:
        """Decide if we should trade right now"""
        pass
    
    @abstractmethod
    async def execute_strategy(self, market_data) -> StrategyResult:
        """Decide what to do and return the plan"""
        pass
    
    @abstractmethod
    def get_strategy_name(self) -> str:
        """Return strategy name"""
        pass
    
    @abstractmethod
    def get_required_parameters(self) -> List[str]:
        """Return required configuration parameters"""
        pass
```

### **Example: Grid Strategy Implementation**

```python
class GridStrategy(BaseStrategy):
    async def should_execute(self, market_data) -> bool:
        """Manager decides: Should we trade now?"""
        # 1. Update list of active close orders
        await self._update_active_orders()
        
        # 2. Calculate wait time based on order density
        wait_time = self._calculate_wait_time()
        if wait_time > 0:
            return False  # "Not yet, too soon"
        
        # 3. Check grid step condition
        if not self._meet_grid_step_condition(market_data):
            return False  # "No, orders are too close together"
        
        return True  # "Yes, conditions are good!"
    
    async def execute_strategy(self, market_data) -> StrategyResult:
        """Manager decides: What should we do?"""
        cycle_state = self.get_strategy_state("cycle_state")
        
        # State 1: Place open order
        if cycle_state == "ready":
            return StrategyResult(
                action=PLACE_ORDER,
                orders=[OrderParams(side='buy', quantity=0.00273, ...)]
            )
            # Tells broker: "Buy 0.00273 BTC"
        
        # State 2: Wait for fill, then place close order
        elif cycle_state == "waiting_for_fill":
            if self.get_strategy_state("filled_price"):
                # Calculate profit target
                close_price = filled_price * (1 + 0.008%)
                
                return StrategyResult(
                    action=PLACE_ORDER,
                    orders=[OrderParams(side='sell', price=close_price, ...)]
                )
                # Tells broker: "Sell at $62,049.60"
```

### **Strategy Decisions (Manager's Brain):**
- ✅ **Timing**: When to place orders (wait time, grid step)
- ✅ **Direction**: Buy or sell
- ✅ **Profit Targets**: Take-profit percentage, dynamic adjustments
- ✅ **Order Sequencing**: Open → Close cycle
- ✅ **State Management**: Track where we are in the cycle

### **What Strategy Does NOT Know:**
- ❌ How to connect to Lighter API
- ❌ How to format Lighter SDK requests
- ❌ How to wait for order fills on Lighter
- ❌ Lighter-specific implementation details

---

## 📊 Layer 4: Exchange Layer (`exchanges/`)

**Role**: The Stock Broker - Handles all exchange communication

### **Interface Contract (BaseExchangeClient):**

```python
class BaseExchangeClient(ABC):
    @abstractmethod
    async def connect(self):
        """Connect to exchange (WebSocket, REST API, etc.)"""
        pass
    
    @abstractmethod
    async def place_open_order(self, contract_id, quantity, direction) -> OrderResult:
        """Place an open order and wait for fill"""
        pass
    
    @abstractmethod
    async def place_close_order(self, contract_id, quantity, price, side) -> OrderResult:
        """Place a close/limit order"""
        pass
    
    @abstractmethod
    async def get_active_orders(self, contract_id) -> List[OrderInfo]:
        """Get list of active orders"""
        pass
    
    @abstractmethod
    async def fetch_bbo_prices(self, contract_id) -> Tuple[Decimal, Decimal]:
        """Get best bid/ask prices"""
        pass
```

### **Example: Lighter Exchange Implementation**

```python
class LighterClient(BaseExchangeClient):
    async def connect(self):
        """Implementer knows: How to connect to Lighter"""
        # 1. Initialize Lighter SDK client
        self.lighter_client = SignerClient(
            url="https://mainnet.zklighter.elliot.ai",
            private_key=self.api_key_private_key,
            ...
        )
        
        # 2. Initialize WebSocket for real-time updates
        self.ws_manager = LighterCustomWebSocketManager(...)
        await self.ws_manager.connect()
    
    async def place_open_order(self, contract_id, quantity, direction):
        """Implementer knows: How to submit orders to Lighter"""
        # 1. Get mid-price from WebSocket
        best_bid, best_ask = await self.fetch_bbo_prices(contract_id)
        order_price = (best_bid + best_ask) / 2
        
        # 2. Format order for Lighter SDK
        order_params = {
            'market_index': contract_id,
            'base_amount': int(quantity * 100_000_000),  # Lighter format
            'price': int(order_price * 100_000),         # Lighter format
            'is_ask': (direction == 'sell'),
            'order_type': self.lighter_client.ORDER_TYPE_LIMIT,
        }
        
        # 3. Submit via Lighter SDK
        create_order, tx_hash, error = await self.lighter_client.create_order(**order_params)
        
        # 4. Wait for fill (poll status via WebSocket)
        start_time = time.time()
        while time.time() - start_time < 10:
            if self.current_order and self.current_order.status == 'FILLED':
                break
            await asyncio.sleep(0.1)
        
        # 5. Return standardized result
        return OrderResult(
            success=True,
            price=order_price,
            status=self.current_order.status
        )
    
    async def fetch_bbo_prices(self, contract_id):
        """Implementer knows: How to get prices from Lighter WebSocket"""
        return (self.ws_manager.best_bid, self.ws_manager.best_ask)
```

### **Exchange Responsibilities (Broker's Job):**
- ✅ **API Communication**: Talking to Lighter/Paradex/etc. APIs
- ✅ **Order Formatting**: Converting Decimals to exchange-specific formats
- ✅ **SDK Integration**: Using exchange SDKs (Lighter SDK, etc.)
- ✅ **Fill Detection**: Waiting for orders to fill via WebSocket/polling
- ✅ **Price Fetching**: Getting real-time market data
- ✅ **WebSocket Management**: Real-time order updates

### **What Exchange Does NOT Know:**
- ❌ Why you're trading (grid? arbitrage? scalping?)
- ❌ When to trade (that's the strategy's decision)
- ❌ Profit targets (strategy calculates that)
- ❌ Risk management logic

---

## 🔄 Complete Trade Flow Example

### **Command:**
```bash
python runbot.py --ticker BTC --quantity 0.00273 --exchange lighter --strategy grid --take-profit 0.008 --direction buy --max-orders 25 --wait-time 35
```

### **Execution Flow:**

```
┌─────────────────────────────────────────────────────────┐
│ 1. runbot.py (Entry Point)                             │
├─────────────────────────────────────────────────────────┤
│ • Parse CLI arguments                                   │
│ • Build strategy_params dict                            │
│ • Create TradingConfig                                  │
│ • Create TradingBot                                     │
│ • Run: await bot.run()                                  │
└─────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────┐
│ 2. trading_bot.py (Orchestrator)                       │
├─────────────────────────────────────────────────────────┤
│ __init__:                                               │
│ • Create LighterClient (via ExchangeFactory)           │
│ • Create GridStrategy (via StrategyFactory)            │
│ • Create RiskManager                                    │
│                                                         │
│ run():                                                  │
│ • await exchange_client.connect()                      │
│ • await strategy.initialize()                          │
│ • Main loop:                                            │
│   ├─ risk_action = await risk_manager.check_risk()    │
│   ├─ market_data = await strategy.get_market_data()   │
│   ├─ should_execute = await strategy.should_execute()  │
│   └─ result = await strategy.execute_strategy()       │
└─────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────┐
│ 3. grid_strategy.py (Manager - Decides)                │
├─────────────────────────────────────────────────────────┤
│ should_execute():                                       │
│ • Check wait time: "Has 35 seconds passed?"            │
│ • Check max orders: "Do we have < 25 orders?"          │
│ • Check grid step: "Is spacing > 0.06%?"               │
│ • Return: True/False                                    │
│                                                         │
│ execute_strategy():                                     │
│ State Machine:                                          │
│   If state == "ready":                                  │
│     • Decision: "Place BUY order for 0.00273 BTC"      │
│     • Return: StrategyResult(                           │
│         action=PLACE_ORDER,                             │
│         orders=[OrderParams(side='buy', qty=0.00273)]  │
│       )                                                 │
│   If state == "waiting_for_fill":                      │
│     • Decision: "Calculate take-profit"                │
│     • Calculation: $62,000 * (1 + 0.008%) = $62,049.60│
│     • Return: StrategyResult(                           │
│         action=PLACE_ORDER,                             │
│         orders=[OrderParams(side='sell', price=62049.60)]│
│       )                                                 │
└─────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────┐
│ 4. trading_bot._execute_order() (Delegates)            │
├─────────────────────────────────────────────────────────┤
│ • Receive: OrderParams(side='buy', qty=0.00273)        │
│ • Call: await exchange_client.place_open_order(...)   │
└─────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────┐
│ 5. lighter.py (Implementer - Executes)                 │
├─────────────────────────────────────────────────────────┤
│ place_open_order():                                     │
│ • Get price: best_bid=$61,995, best_ask=$62,005        │
│ • Calculate: mid_price = $62,000                        │
│ • Format for Lighter:                                   │
│   {                                                     │
│     market_index: 0,                                    │
│     base_amount: 273000,        # 0.00273 * 10^8       │
│     price: 6200000000,          # $62,000 * 10^5       │
│     is_ask: False,              # buying                │
│     order_type: ORDER_TYPE_LIMIT                        │
│   }                                                     │
│ • Submit: await lighter_client.create_order(params)    │
│ • Wait for fill: Poll self.current_order.status        │
│ • Return: OrderResult(success=True, price=62000)       │
└─────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────┐
│ 6. Lighter SDK (External API)                          │
├─────────────────────────────────────────────────────────┤
│ • Format blockchain transaction                         │
│ • Sign with private key                                 │
│ • Submit to Lighter blockchain                          │
│ • Return: (create_order, tx_hash, error)               │
└─────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────┐
│ 7. WebSocket Callback (Real-time Updates)              │
├─────────────────────────────────────────────────────────┤
│ Lighter blockchain → WebSocket → lighter.py             │
│ Message: {order_id: 12345, status: 'FILLED', ...}     │
│                                                         │
│ lighter.py updates:                                     │
│ • self.current_order.status = 'FILLED'                 │
│                                                         │
│ trading_bot.py notifies strategy:                      │
│ • strategy.notify_order_filled(price=62000, qty=0.00273)│
└─────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────┐
│ 8. Next Loop Iteration                                  │
├─────────────────────────────────────────────────────────┤
│ strategy.execute_strategy():                            │
│ • State now: "waiting_for_fill"                        │
│ • Has filled_price: $62,000                             │
│ • Calculate: $62,000 * 1.00008 = $62,049.60           │
│ • Return: StrategyResult(                               │
│     action=PLACE_ORDER,                                 │
│     orders=[OrderParams(side='sell', price=62049.60)]  │
│   )                                                     │
│                                                         │
│ → Executes via lighter.place_close_order()             │
│ → Cycle completes, state resets to "ready"             │
└─────────────────────────────────────────────────────────┘
```

---

## 🎯 Separation of Concerns

### **Strategy Layer (The Brain 🧠)**

**Knows:**
- ✅ Trading logic and rules
- ✅ When to enter/exit positions
- ✅ Profit target calculations
- ✅ Risk parameters (max orders, grid spacing)
- ✅ Order sequencing

**Doesn't Know:**
- ❌ Exchange APIs
- ❌ WebSocket protocols
- ❌ Order formats
- ❌ SDK details

**Example Strategies:**
- `GridStrategy`: Grid trading with take-profit
- `FundingArbitrageStrategy`: Delta-neutral funding rate farming
- `YourCustomStrategy`: Easy to add!

---

### **Exchange Layer (The Hands ✋)**

**Knows:**
- ✅ Exchange-specific APIs
- ✅ SDK integration (Lighter SDK, Paradex SDK)
- ✅ Order submission mechanics
- ✅ WebSocket management
- ✅ Price fetching
- ✅ Fill detection (polling/streaming)

**Doesn't Know:**
- ❌ Trading strategy logic
- ❌ When to trade
- ❌ Profit calculations
- ❌ Risk management rules

**Example Exchanges:**
- `LighterClient`: Lighter DEX via official SDK
- `ParadexClient`: Paradex DEX
- `BackpackClient`: Backpack exchange
- `GRVTClient`, `EdgeXClient`, `AsterClient`

---

### **Trading Bot Layer (The Supervisor 👔)**

**Knows:**
- ✅ How to coordinate strategy + exchange
- ✅ How to handle StrategyResult actions
- ✅ How to manage risk
- ✅ Error handling and shutdown

**Doesn't Know:**
- ❌ Strategy-specific logic (no `if strategy == 'grid'` checks!)
- ❌ Exchange-specific implementation
- ❌ Trading decisions

---

## 🔌 Modularity & Extensibility

### **✅ Adding a New Strategy:**

```python
# 1. Create new strategy class
# strategies/scalping_strategy.py
class ScalpingStrategy(BaseStrategy):
    def get_strategy_name(self) -> str:
        return "scalping"
    
    def get_required_parameters(self) -> List[str]:
        return ["tick_profit", "max_position", "spread_threshold"]
    
    async def should_execute(self, market_data) -> bool:
        # Your scalping logic
        return spread < self.get_parameter('spread_threshold')
    
    async def execute_strategy(self, market_data) -> StrategyResult:
        # Your scalping implementation
        return StrategyResult(action=PLACE_ORDER, orders=[...])

# 2. Register in factory
# strategies/factory.py
_strategies = {
    'grid': GridStrategy,
    'funding_arbitrage': FundingArbitrageStrategy,
    'scalping': ScalpingStrategy,  # ← Add here
}

# 3. Use it!
python runbot.py --strategy scalping --ticker BTC --quantity 0.001 --exchange lighter --tick-profit 0.001
```

**That's it!** No changes to `trading_bot.py` or any exchange clients needed!

---

### **✅ Adding a New Exchange:**

```python
# 1. Create new exchange class
# exchanges/dydx.py
class DydxClient(BaseExchangeClient):
    async def connect(self):
        # Connect to dYdX API
        pass
    
    async def place_open_order(self, contract_id, quantity, direction):
        # Use dYdX SDK
        result = await self.dydx_client.place_order(...)
        return OrderResult(...)
    
    # ... implement all required methods

# 2. Register in factory
# exchanges/factory.py
EXCHANGE_CLASSES = {
    'lighter': 'exchanges.lighter.LighterClient',
    'paradex': 'exchanges.paradex.ParadexClient',
    'dydx': 'exchanges.dydx.DydxClient',  # ← Add here
}

# 3. Use it with ANY strategy!
python runbot.py --strategy grid --exchange dydx --ticker BTC ...
python runbot.py --strategy funding_arbitrage --exchange dydx --ticker HYPE ...
```

**That's it!** No changes to strategies or trading bot needed!

---

## 🛡️ Risk Management Layer

**Role**: Account protection (exchange-specific)

### **Integration:**

```python
# Only enabled for exchanges that support it
if exchange_client.supports_risk_management():
    risk_manager = RiskManager(exchange_client, config)

# Universal monitoring (works for all strategies)
risk_action = await risk_manager.check_risk_conditions()
if risk_action == CLOSE_WORST_POSITIONS:
    await self._close_worst_positions()
```

### **Current Implementation:**
- ✅ **Lighter**: Full SDK integration (account balance, positions, P&L)
- ❌ **Other exchanges**: Not yet implemented (but easy to add!)

### **Risk Thresholds (Lighter):**
- Margin failures: 15 consecutive
- Time stall: 10 minutes
- Account loss: -10%
- Emergency loss: -15%

---

## 🔧 Key Components

### **Factories:**
```python
# strategies/factory.py
StrategyFactory.create_strategy('grid', config, exchange_client)

# exchanges/factory.py
ExchangeFactory.create_exchange('lighter', config)
```

### **Data Classes:**
```python
# Standardized data structures
OrderResult(success, price, status, error_message)
OrderInfo(order_id, side, size, price, status)
OrderParams(side, quantity, price, order_type)
StrategyResult(action, orders, message, wait_time)
MarketData(ticker, best_bid, best_ask, mid_price)
```

### **State Management:**
```python
# Grid strategy manages its own state
strategy_state = {
    "cycle_state": "ready",  # or "waiting_for_fill"
    "filled_price": Decimal('62000'),
    "active_close_orders": [...],
}
```

---

## 📚 Quick Reference

### **File Structure:**
```
strategies/
├── base_strategy.py          # Interface all strategies implement
├── grid_strategy.py          # Grid trading implementation
├── funding_arbitrage_strategy.py  # Funding arb implementation
└── factory.py                # Strategy factory

exchanges/
├── base.py                   # Interface all exchanges implement
├── lighter.py                # Lighter DEX implementation
├── paradex.py, backpack.py, etc.  # Other exchanges
└── factory.py                # Exchange factory

trading_bot.py                # Universal coordinator
runbot.py                     # CLI entry point
helpers/risk_manager.py       # Risk management (optional)
```

### **Key Interfaces:**

```python
# All strategies must implement:
class YourStrategy(BaseStrategy):
    async def should_execute(market_data) -> bool
    async def execute_strategy(market_data) -> StrategyResult

# All exchanges must implement:
class YourExchange(BaseExchangeClient):
    async def place_open_order(...) -> OrderResult
    async def place_close_order(...) -> OrderResult
    async def get_active_orders(...) -> List[OrderInfo]
```
