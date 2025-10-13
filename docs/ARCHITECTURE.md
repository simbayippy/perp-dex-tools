# 🏗️ System Architecture

**Last Updated:** 2025-10-09  
**Version:** 2.5  
**Status:** Production Ready

---

## 📋 Table of Contents

1. [Overview](#overview)
2. [3-Layer Architecture](#3-layer-architecture)
3. [Data Flow](#data-flow)
4. [Database Architecture](#database-architecture)
5. [Multi-Exchange Support](#multi-exchange-support)
6. [Atomic Execution Pattern](#atomic-execution-pattern)
7. [Funding Rate Service Integration](#funding-rate-service-integration)
8. [Component Interaction Diagram](#component-interaction-diagram)
9. [Execution Flow Example](#execution-flow-example)
10. [Key Design Decisions](#key-design-decisions)
11. [Operator Tooling & Dashboard](#operator-tooling--dashboard)

---

## Overview

This is a **modular trading bot framework** designed for **multi-exchange perpetual futures trading** with a focus on **delta-neutral strategies** (funding rate arbitrage). The architecture emphasizes:

- **Extensibility**: Easy to add new exchanges and strategies
- **Safety**: Atomic execution prevents directional exposure
- **Performance**: Internal service calls (no HTTP overhead)
- **Modularity**: Clear separation between strategy logic, execution, and exchange interfaces
- **Reliability**: Database-backed state persistence

**Primary Use Case:** Funding rate arbitrage across multiple DEXes (Lighter, Backpack, EdgeX, GRVT, etc.)

---

## 3-Layer Architecture

The system follows a **strict 3-layer architecture** with clear separation of concerns:

```
┌─────────────────────────────────────────────────────────────────┐
│                    LAYER 3: Strategy Orchestration               │
│                    "WHAT to trade"                               │
│  Location: /strategies/implementations/{strategy_name}/          │
│                                                                   │
│  Responsibilities:                                                │
│  • Business logic (when to open/close positions)                 │
│  • Risk management decisions                                     │
│  • Position tracking and state management                        │
│  • Opportunity evaluation                                        │
│                                                                   │
│  Example: FundingArbitrageStrategy                               │
│  • 3-phase execution loop (monitor, exit, scan)                  │
│  • Exit conditions (funding flip, profit erosion)                │
│  • Position management via database                              │
└────────────────┬────────────────────────────────────────────────┘
                 │ Uses (Layer 3 → Layer 2)
                 ↓
┌─────────────────────────────────────────────────────────────────┐
│                    LAYER 2: Execution Utilities                  │
│                    "HOW to trade safely"                         │
│  Location: /strategies/execution/                                │
│                                                                   │
│  Core Utilities (/core/):                                        │
│  • OrderExecutor - Smart order placement (limit→market fallback) │
│  • LiquidityAnalyzer - Pre-flight depth checks                   │
│  • PositionSizer - USD ↔ quantity conversion                     │
│  • SlippageCalculator - Slippage estimation                      │
│                                                                   │
│  Execution Patterns (/patterns/):                                │
│  • AtomicMultiOrderExecutor - ⭐ Delta-neutral atomic execution │
│  • PartialFillHandler - Emergency rollback on partial fills      │
│                                                                   │
│  Monitoring (/monitoring/):                                       │
│  • ExecutionTracker - Execution quality metrics                  │
└────────────────┬────────────────────────────────────────────────┘
                 │ Calls (Layer 2 → Layer 1)
                 ↓
┌─────────────────────────────────────────────────────────────────┐
│                    LAYER 1: Exchange Clients                     │
│                    "WHERE to trade"                              │
│  Location: /exchange_clients/                                    │
│                                                                   │
│  BaseExchangeClient (Trading Interface):                         │
│  • fetch_bbo_prices() - Get best bid/ask                        │
│  • place_limit_order() - Place limit order                      │
│  • place_market_order() - Place market order                    │
│  • get_order_book_depth() - Get order book levels               │
│  • cancel_order() - Cancel existing order                       │
│  • get_account_positions() - Get current positions              │
│                                                                   │
│  BaseFundingAdapter (Data Collection Interface):                │
│  • fetch_funding_rates() - Get funding rates                    │
│  • fetch_market_data() - Get volume, open interest              │
│  • normalize_symbol() - Symbol format conversion                │
│                                                                   │
│  Implementations: Lighter, Backpack, EdgeX, GRVT, Aster, Paradex│
└─────────────────────────────────────────────────────────────────┘
```

### Layer Responsibilities

#### **Layer 3: Strategy Orchestration**
- **What it does:** Makes trading decisions (entry/exit/rebalancing)
- **What it knows:** Risk parameters, position limits, profitability thresholds
- **What it doesn't know:** How to execute trades safely, exchange-specific APIs

#### **Layer 2: Execution Utilities**
- **What it does:** Executes trades safely with quality guarantees
- **What it knows:** Order placement tactics, liquidity analysis, slippage management
- **What it doesn't know:** Trading strategy logic, exchange-specific protocols

#### **Layer 1: Exchange Clients**
- **What it does:** Communicates with exchange APIs
- **What it knows:** Exchange-specific protocols, authentication, order formats
- **What it doesn't know:** Strategy logic, execution tactics

---

## Data Flow

### Example: Funding Arbitrage - Opening a Position

```
1. USER STARTS BOT
   python runbot.py --config configs/funding_arb.yml
   
   ↓

2. INITIALIZATION (trading_bot.py)
   
   a. Create exchange clients for all DEXes:
      exchange_clients = {
          "lighter": LighterClient(...),
          "backpack": BackpackClient(...),
          "edgex": EdgeXClient(...)
      }
   
   b. Create strategy with all clients:
      strategy = FundingArbitrageStrategy(
          config=config,
          exchange_clients=exchange_clients  # ← All DEXes
      )
   
   c. Connect to all exchanges
   d. Initialize strategy (connects to database)
   
   ↓

3. MAIN LOOP (trading_bot.py)
   
   while True:
       await strategy.execute_cycle()
       await asyncio.sleep(60)  # Run every minute
   
   ↓

4. STRATEGY EXECUTION CYCLE (Layer 3)
   
   PHASE 1: Monitor Positions
   ────────────────────────────
   For each open position:
   
   a. Fetch current funding rates (DIRECT DB CALL, no HTTP):
      rate1 = await funding_rate_repo.get_latest_specific(
          dex="lighter",
          symbol="BTC"
      )
      rate2 = await funding_rate_repo.get_latest_specific(
          dex="backpack",
          symbol="BTC"
      )
   
   b. Update position state:
      position.current_divergence = rate2 - rate1
      position.last_check = now()
      await position_manager.update_position(position)
   
   c. Calculate current profitability:
      pnl = position.get_net_pnl()  # funding collected - fees paid
   
   ↓
   
   PHASE 2: Check Exit Conditions
   ────────────────────────────────
   For each position:
   
   a. Check exit triggers:
      • Funding rate flipped? (divergence < 0) → CLOSE
      • Profit erosion? (divergence dropped 50%+) → CLOSE
      • Time limit? (position age > 7 days) → CLOSE
   
   b. If closing:
      long_client = exchange_clients["lighter"]
      short_client = exchange_clients["backpack"]
      
      await long_client.close_position("BTC")
      await short_client.close_position("BTC")
      
      position.status = "closed"
      await position_manager.update_position(position)
   
   ↓
   
   PHASE 3: Scan New Opportunities
   ─────────────────────────────────
   a. Query opportunity finder (DIRECT INTERNAL CALL):
      
      opportunities = await opportunity_finder.find_opportunities(
          OpportunityFilter(
              min_profit_percent=Decimal("0.001"),  # 0.1% min
              max_oi_usd=Decimal("10000000"),       # 10M max OI
              whitelist_dexes=["lighter", "backpack", "edgex"],
              limit=10
          )
      )
      
      # OpportunityFinder queries PostgreSQL:
      # 1. Latest funding rates from all DEXes
      # 2. Market data (volume, OI)
      # 3. Calculates divergence, fees, net profit
      # 4. Ranks by profitability
      
      # Returns: List[ArbitrageOpportunity]
      # [
      #   {
      #     symbol: "BTC",
      #     long_dex: "lighter",      # Lower funding rate (pay)
      #     short_dex: "backpack",     # Higher funding rate (receive)
      #     long_rate: 0.0001,
      #     short_rate: 0.0015,
      #     divergence: 0.0014,        # 0.14% profit potential
      #     net_profit_percent: 0.0012 # After fees
      #   },
      #   ...
      # ]
   
   b. For each opportunity (up to max_new_positions_per_cycle):
      
      if should_take_opportunity(opp):
          await open_position(opp)
   
   ↓

5. OPENING POSITION - ATOMIC EXECUTION (Layer 3 → Layer 2)
   
   Strategy calls AtomicMultiOrderExecutor:
   
   result = await atomic_executor.execute_atomically(
       orders=[
           OrderSpec(
               exchange_client=exchange_clients["lighter"],
               symbol="BTC",
               side="buy",              # Long on Lighter
               size_usd=Decimal("1000")
           ),
           OrderSpec(
               exchange_client=exchange_clients["backpack"],
               symbol="BTC",
               side="sell",             # Short on Backpack
               size_usd=Decimal("1000")
           )
       ],
       rollback_on_partial=True,  # 🚨 BOTH fill or NEITHER
       pre_flight_check=True      # Check liquidity first
   )
   
   ↓

6. ATOMIC EXECUTOR FLOW (Layer 2)
   
   Step 1: Pre-flight checks
   ──────────────────────────
   For each order:
   
   liquidity_report = await liquidity_analyzer.check_execution_feasibility(
       exchange_client=lighter_client,
       symbol="BTC",
       side="buy",
       size_usd=Decimal("1000")
   )
   
   if not liquidity_report.depth_sufficient:
       return AtomicExecutionResult(
           success=False,
           error_message="Insufficient liquidity on Lighter"
       )
   
   ↓
   
   Step 2: Place orders SIMULTANEOUSLY
   ─────────────────────────────────────
   long_task = order_executor.execute_order(
       lighter_client, "BTC", "buy", Decimal("1000"),
       mode="limit_with_fallback", timeout=30
   )
   
   short_task = order_executor.execute_order(
       backpack_client, "BTC", "sell", Decimal("1000"),
       mode="limit_with_fallback", timeout=30
   )
   
   results = await asyncio.gather(long_task, short_task)
   
   ↓
   
   Step 3: Check results
   ──────────────────────
   Case A: ✅ BOTH FILLED
       return AtomicExecutionResult(
           success=True,
           all_filled=True,
           filled_orders=[long_result, short_result],
           total_slippage_usd=Decimal("2.50")
       )
   
   Case B: ❌ ONLY ONE FILLED (e.g., only Lighter filled)
       # 🚨 EMERGENCY ROLLBACK
       
       1. Detect partial fill:
          long_filled = True
          short_filled = False
       
       2. Market close the filled order:
          await lighter_client.place_market_order(
              symbol="BTC",
              side="sell",  # Opposite of original
              quantity=filled_quantity
          )
       
       3. Accept slippage loss (better than directional exposure!)
       
       4. Return failure:
          return AtomicExecutionResult(
              success=False,
              all_filled=False,
              rollback_performed=True,
              rollback_cost_usd=Decimal("5.00")  # Slippage cost
          )
   
   ↓

7. ORDER EXECUTION (Layer 2 → Layer 1)
   
   For each order, OrderExecutor tries tiered execution:
   
   Mode: "limit_with_fallback"
   
   Try 1: Limit Order (30 second timeout)
   ───────────────────────────────────────
   a. Get current market prices:
      best_bid, best_ask = await lighter_client.fetch_bbo_prices("BTC")
   
   b. Calculate maker price (favorable):
      For buy: limit_price = best_ask - tick_size  # Maker order
      For sell: limit_price = best_bid + tick_size
   
   c. Place limit order:
      order_result = await lighter_client.place_limit_order(
          contract_id="BTC",
          quantity=0.02,  # Converted from $1000 USD
          price=Decimal("49999.50"),
          side="buy"
      )
   
   d. Wait for fill (polling every 0.5s):
      for i in range(60):  # 30 second timeout
          order_info = await lighter_client.get_order_info(order_id)
          if order_info.status == "FILLED":
              return success
          await asyncio.sleep(0.5)
   
   e. If timeout → cancel and fallback
   
   ↓
   
   Try 2: Market Order (if limit timeout)
   ───────────────────────────────────────
   await lighter_client.place_market_order(
       contract_id="BTC",
       quantity=0.02,
       side="buy"
   )
   # Immediate fill, accepts slippage
   
   ↓

8. EXCHANGE CLIENT EXECUTION (Layer 1)
   
   Lighter Client:
   ───────────────
   async def place_limit_order(contract_id, quantity, price, side):
       # Exchange-specific API call
       
       # Convert to Lighter format
       market_id = self.config.contract_id
       base_amount = int(quantity * self.base_amount_multiplier)
       price_int = int(price * self.price_multiplier)
       
       # Create order using Lighter SDK
       create_order, tx_hash, error = await self.lighter_client.create_order(
           market_index=market_id,
           base_amount=base_amount,
           price=price_int,
           is_ask=False,  # buy order
           order_type=ORDER_TYPE_LIMIT
       )
       
       return OrderResult(success=True, order_id=...)
   
   ↓

9. STRATEGY RECEIVES RESULT (Layer 3)
   
   if result.all_filled:
       # ✅ SUCCESS - Create position record
       
       long_fill = result.filled_orders[0]
       short_fill = result.filled_orders[1]
       
       position = FundingArbPosition(
           id=uuid4(),
           symbol="BTC",
           long_dex="lighter",
           short_dex="backpack",
           size_usd=Decimal("1000"),
           entry_long_rate=Decimal("0.0001"),
           entry_short_rate=Decimal("0.0015"),
           entry_divergence=Decimal("0.0014"),
           opened_at=datetime.now(),
           total_fees_paid=entry_fees + slippage
       )
       
       await position_manager.add_position(position)
       # ↑ Saves to PostgreSQL strategy_positions table
       
       logger.info(f"✅ Position opened: BTC ${1000}")
   
   else:
       # ❌ FAILURE - Log and continue
       
       if result.rollback_performed:
           logger.warning(
               f"Atomic execution failed, rollback cost: "
               f"${result.rollback_cost_usd}"
           )
       
       # No position created, continue scanning
```

---

## Database Architecture

The system uses **PostgreSQL** for both data collection and strategy state persistence.

### Database Tables

```sql
-- ============================================================================
-- FUNDING RATE SERVICE TABLES (Data Collection)
-- ============================================================================

-- DEX metadata
CREATE TABLE dexes (
    id SERIAL PRIMARY KEY,
    name VARCHAR(50) UNIQUE NOT NULL,
    display_name VARCHAR(100),
    is_active BOOLEAN DEFAULT true,
    maker_fee_percent DECIMAL(10, 6),
    taker_fee_percent DECIMAL(10, 6),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Symbol metadata
CREATE TABLE symbols (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(20) UNIQUE NOT NULL,  -- Normalized (e.g., "BTC")
    created_at TIMESTAMP DEFAULT NOW()
);

-- Funding rates (time-series)
CREATE TABLE funding_rates (
    id SERIAL PRIMARY KEY,
    dex_id INTEGER REFERENCES dexes(id),
    symbol_id INTEGER REFERENCES symbols(id),
    funding_rate DECIMAL(18, 10) NOT NULL,
    next_funding_time TIMESTAMP,
    volume_24h DECIMAL(20, 2),
    open_interest_usd DECIMAL(20, 2),
    timestamp TIMESTAMP DEFAULT NOW(),
    UNIQUE(dex_id, symbol_id, timestamp)
);

-- ============================================================================
-- STRATEGY STATE TABLES (Migration 004)
-- ============================================================================

-- Position tracking
CREATE TABLE strategy_positions (
    id UUID PRIMARY KEY,
    strategy_name VARCHAR(50) NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    
    -- Position details
    long_dex VARCHAR(50) NOT NULL,
    short_dex VARCHAR(50) NOT NULL,
    size_usd DECIMAL(20, 2) NOT NULL,
    
    -- Entry data
    entry_long_rate DECIMAL(18, 10) NOT NULL,
    entry_short_rate DECIMAL(18, 10) NOT NULL,
    entry_divergence DECIMAL(18, 10) NOT NULL,
    opened_at TIMESTAMP NOT NULL,
    
    -- Current state
    current_divergence DECIMAL(18, 10),
    last_check TIMESTAMP,
    
    -- Funding tracking
    cumulative_funding DECIMAL(20, 8) DEFAULT 0,
    total_fees_paid DECIMAL(20, 8) DEFAULT 0,
    
    -- Status
    status VARCHAR(20) DEFAULT 'open',  -- open, closed, error
    exit_reason VARCHAR(100),
    closed_at TIMESTAMP,
    
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Funding payments (historical record)
CREATE TABLE funding_payments (
    id UUID PRIMARY KEY,
    position_id UUID REFERENCES strategy_positions(id),
    dex VARCHAR(50) NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    funding_rate DECIMAL(18, 10) NOT NULL,
    payment_amount_usd DECIMAL(20, 8) NOT NULL,
    payment_time TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Cross-DEX fund transfers (future use)
CREATE TABLE fund_transfers (
    id UUID PRIMARY KEY,
    from_dex VARCHAR(50) NOT NULL,
    to_dex VARCHAR(50) NOT NULL,
    amount_usd DECIMAL(20, 2) NOT NULL,
    bridge_used VARCHAR(50),
    status VARCHAR(20) DEFAULT 'pending',
    initiated_at TIMESTAMP NOT NULL,
    completed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Strategy state persistence
CREATE TABLE strategy_state (
    id UUID PRIMARY KEY,
    strategy_name VARCHAR(50) NOT NULL,
    state_key VARCHAR(100) NOT NULL,
    state_value JSONB NOT NULL,
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(strategy_name, state_key)
);
```

### Database Usage

#### **Data Collection (Funding Rate Service)**
```python
# Collection orchestrator fetches funding rates every minute
# Uses BaseFundingAdapter for each DEX

for dex_name in active_dexes:
    adapter = get_adapter(dex_name)  # e.g., LighterFundingAdapter
    
    # Fetch rates
    rates = await adapter.fetch_funding_rates()
    # Returns: {"BTC": Decimal("0.0001"), "ETH": Decimal("0.00008"), ...}
    
    # Fetch market data
    market_data = await adapter.fetch_market_data()
    # Returns: {"BTC": {"volume_24h": 1500000, "open_interest": 5000000}, ...}
    
    # Save to database
    await funding_rate_repo.insert_rates(dex_name, rates, market_data)
```

#### **Strategy State Persistence (Funding Arbitrage)**
```python
# Position Manager - tracks positions
position = FundingArbPosition(
    id=uuid4(),
    symbol="BTC",
    long_dex="lighter",
    short_dex="backpack",
    size_usd=Decimal("1000"),
    entry_divergence=Decimal("0.0014"),
    ...
)

await position_manager.add_position(position)
# ↑ Saves to strategy_positions table

# Later: Update position state
position.current_divergence = Decimal("0.0008")
await position_manager.update_position(position)

# Later: Close position
position.status = "closed"
position.exit_reason = "FUNDING_FLIP"
await position_manager.update_position(position)
```

#### **Funding Payment Tracking**
```python
# Record funding payment when it occurs
payment = FundingPayment(
    id=uuid4(),
    position_id=position.id,
    dex="lighter",
    symbol="BTC",
    funding_rate=Decimal("0.0001"),
    payment_amount_usd=Decimal("0.10"),  # size * rate
    payment_time=datetime.now()
)

await position_manager.record_funding_payment(payment)
# ↑ Saves to funding_payments table

# Query cumulative funding
total_funding = await position_manager.get_cumulative_funding(position_id)
```

---

## Multi-Exchange Support

The system supports **simultaneous connections to multiple exchanges**, essential for funding arbitrage.

### Architecture Overview

```
                          TradingBot
                              │
                              │ Creates
                              ↓
        ┌─────────────────────────────────────────┐
        │      ExchangeFactory                    │
        │                                          │
        │  create_multiple_exchanges([             │
        │      "lighter",                          │
        │      "backpack",                         │
        │      "edgex"                             │
        │  ])                                      │
        └─────────────────────────────────────────┘
                              │
                              │ Returns
                              ↓
        ┌─────────────────────────────────────────┐
        │   exchange_clients = {                   │
        │       "lighter": LighterClient(...),     │
        │       "backpack": BackpackClient(...),   │
        │       "edgex": EdgeXClient(...)          │
        │   }                                      │
        └─────────────────────────────────────────┘
                              │
                              │ Passed to
                              ↓
        ┌─────────────────────────────────────────┐
        │   FundingArbitrageStrategy               │
        │                                          │
        │   self.exchange_clients = {              │
        │       "lighter": <client>,               │
        │       "backpack": <client>,              │
        │       "edgex": <client>                  │
        │   }                                      │
        │                                          │
        │   # Can execute on any exchange:        │
        │   long_client = self.exchange_clients    │
        │                      ["lighter"]         │
        │   short_client = self.exchange_clients   │
        │                      ["backpack"]        │
        └─────────────────────────────────────────┘
```

### Implementation

#### **ExchangeFactory (Multi-Exchange Support)**
```python
# exchange_clients/factory.py

@staticmethod
def create_multiple_exchanges(
    exchange_names: List[str],
    config: TradingConfig,
    primary_exchange: str
) -> Dict[str, BaseExchangeClient]:
    """
    Create multiple exchange clients for multi-DEX strategies.
    
    Args:
        exchange_names: List of exchange names (e.g., ["lighter", "backpack"])
        config: Trading configuration
        primary_exchange: Primary exchange name (for backward compatibility)
    
    Returns:
        Dictionary mapping exchange name to client instance
    """
    clients = {}
    
    for exchange_name in exchange_names:
        try:
            # Create config for this exchange
            exchange_config = copy.copy(config)
            exchange_config.exchange = exchange_name
            
            # Create client
            client = ExchangeFactory.create_exchange(
                exchange_name,
                exchange_config
            )
            
            clients[exchange_name] = client
            
        except Exception as e:
            logger.error(f"Failed to create {exchange_name} client: {e}")
    
    return clients
```

#### **TradingBot (Multi-Exchange Mode)**
```python
# trading_bot.py

def __init__(self, config: TradingConfig):
    # Determine if strategy needs multiple exchanges
    multi_exchange_strategies = ['funding_arbitrage']
    is_multi_exchange = config.strategy in multi_exchange_strategies
    
    if is_multi_exchange:
        # Get list of exchanges from strategy params
        exchange_list = config.strategy_params.get('exchanges', [config.exchange])
        
        # Create multiple exchange clients
        self.exchange_clients = ExchangeFactory.create_multiple_exchanges(
            exchange_names=exchange_list,
            config=config,
            primary_exchange=config.exchange
        )
        
        # Set primary exchange client for backward compatibility
        self.exchange_client = self.exchange_clients[config.exchange]
    else:
        # Single exchange mode (grid strategy)
        self.exchange_client = ExchangeFactory.create_exchange(
            config.exchange,
            config
        )
        self.exchange_clients = None
```

#### **Strategy (Using Multiple Exchanges)**
```python
# strategies/implementations/funding_arbitrage/strategy.py

class FundingArbitrageStrategy(StatefulStrategy):
    def __init__(self, config, exchange_client):
        # Convert single exchange client to dict if needed
        if isinstance(exchange_client, dict):
            exchange_clients = exchange_client
        else:
            # Single exchange - create dict
            primary_exchange = exchange_client.get_exchange_name()
            exchange_clients = {primary_exchange: exchange_client}
        
        super().__init__(config, exchange_clients)
        self.exchange_clients = exchange_clients
    
    async def _open_position(self, opportunity):
        # Get exchange clients for this opportunity
        long_client = self.exchange_clients[opportunity.long_dex]
        short_client = self.exchange_clients[opportunity.short_dex]
        
        # Execute atomically across different exchanges
        result = await atomic_executor.execute_atomically(
            orders=[
                OrderSpec(long_client, symbol, "buy", size_usd),
                OrderSpec(short_client, symbol, "sell", size_usd)
            ]
        )
```

---

## Atomic Execution Pattern

**The most critical safety mechanism** for delta-neutral strategies.

### Problem Statement

**Funding arbitrage requires opening TWO positions simultaneously:**
- Long on DEX A (pay low funding rate)
- Short on DEX B (receive high funding rate)

**What could go wrong:**
- ❌ Only Long fills → You're long BTC (directional exposure!)
- ❌ Only Short fills → You're short BTC (directional exposure!)
- ✅ Both fill → Delta neutral (safe)

**Solution:** Atomic execution guarantees both sides fill or neither fills.

### AtomicMultiOrderExecutor

```python
# strategies/execution/patterns/atomic_multi_order.py

class AtomicMultiOrderExecutor:
    """
    Executes multiple orders atomically - all must succeed or all rollback.
    
    ⭐ Critical for delta-neutral strategies ⭐
    """
    
    async def execute_atomically(
        self,
        orders: List[OrderSpec],
        rollback_on_partial: bool = True,
        pre_flight_check: bool = True
    ) -> AtomicExecutionResult:
        """
        Execute all orders. If any fail:
        1. Market close all successful fills
        2. Accept slippage loss
        3. Return to neutral state
        
        Flow:
        ─────
        1. Pre-flight checks (liquidity on all DEXes)
        2. Place all orders simultaneously (asyncio.gather)
        3. Monitor fills with timeout
        4. If partial fill detected → EMERGENCY ROLLBACK
        5. Return result
        """
```

### Execution Flow

```
┌──────────────────────────────────────────────────────────────┐
│ Step 1: Pre-flight Checks                                    │
│                                                               │
│ For each order:                                              │
│ • Check order book depth                                     │
│ • Estimate slippage                                          │
│ • Verify sufficient liquidity                                │
│                                                               │
│ If any fails → ABORT before placing orders                  │
└───────────────────────────┬──────────────────────────────────┘
                            │
                            │ All checks pass
                            ↓
┌──────────────────────────────────────────────────────────────┐
│ Step 2: Simultaneous Order Placement                         │
│                                                               │
│ long_task = order_executor.execute_order(lighter, ...)      │
│ short_task = order_executor.execute_order(backpack, ...)    │
│                                                               │
│ results = await asyncio.gather(                              │
│     long_task,                                               │
│     short_task,                                              │
│     return_exceptions=True                                   │
│ )                                                            │
│                                                               │
│ ⚡ Both orders placed at nearly the same time                │
└───────────────────────────┬──────────────────────────────────┘
                            │
                            │ Wait for fills (30s timeout)
                            ↓
┌──────────────────────────────────────────────────────────────┐
│ Step 3: Check Results                                        │
│                                                               │
│ Case A: ✅ Both filled                                       │
│     return SUCCESS                                           │
│                                                               │
│ Case B: ❌ Partial fill (only one filled)                   │
│     goto Step 4 (Rollback)                                   │
│                                                               │
│ Case C: ❌ Neither filled                                    │
│     return FAILURE (no rollback needed)                      │
└───────────────────────────┬──────────────────────────────────┘
                            │
                            │ Partial fill detected!
                            ↓
┌──────────────────────────────────────────────────────────────┐
│ Step 4: EMERGENCY ROLLBACK 🚨                                │
│                                                               │
│ Problem: Only Lighter filled (long BTC)                      │
│ Current state: LONG BTC (directional exposure!)              │
│                                                               │
│ Solution:                                                     │
│ 1. Immediately MARKET SELL on Lighter                        │
│    await lighter_client.place_market_order(                  │
│        symbol="BTC",                                         │
│        side="sell",  # Opposite of filled order              │
│        quantity=filled_quantity                              │
│    )                                                         │
│                                                               │
│ 2. Accept slippage cost (~$5-10 typical)                     │
│                                                               │
│ 3. Return to NEUTRAL state (no exposure)                     │
│                                                               │
│ Final state: NO POSITION (safe!)                             │
│ Cost: Slippage loss (acceptable, prevents worse losses)      │
└──────────────────────────────────────────────────────────────┘
```

### Why This Matters

**Without atomic execution:**
```
Scenario: Opening BTC funding arb position

1. Place long on Lighter → FILLED ✅
2. Place short on Backpack → REJECTED ❌ (insufficient margin)

Current state: LONG BTC @ $50,000
If BTC drops to $45,000 → Loss: $5,000 per BTC

This is NOT what we wanted! We wanted delta-neutral.
```

**With atomic execution:**
```
Same scenario:

1. Place long on Lighter → FILLED ✅
2. Place short on Backpack → REJECTED ❌

Atomic executor detects partial fill:
3. IMMEDIATELY market sell on Lighter
   Entry: $50,000
   Exit: $49,995 (slippage)
   Loss: $5 per BTC

Final state: NO POSITION (delta neutral)
Cost: $5 slippage (acceptable!)
```

**Trade-off:** Small slippage loss vs large directional risk
- **Without atomic:** Risk losing $5,000+ on price movement
- **With atomic:** Risk losing $5-10 on slippage
- **Decision:** Pay $5 to avoid $5,000 risk ✅

---

## Funding Rate Service Integration

The funding rate service provides **opportunity discovery** and **rate monitoring**.

### Architecture

```
┌──────────────────────────────────────────────────────────────┐
│              Funding Rate Service                             │
│                                                               │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ Collection Layer (Background Tasks)                   │   │
│  │                                                        │   │
│  │ Every 60 seconds:                                     │   │
│  │   for dex in [lighter, backpack, edgex, ...]:        │   │
│  │       adapter = dex.funding_adapter                  │   │
│  │       rates = await adapter.fetch_funding_rates()    │   │
│  │       market_data = await adapter.fetch_market_data()│   │
│  │       await save_to_database(rates, market_data)     │   │
│  └──────────────────────────────────────────────────────┘   │
│                            │                                  │
│                            ↓                                  │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ PostgreSQL Database                                   │   │
│  │                                                        │   │
│  │ Tables:                                               │   │
│  │ • dexes - DEX metadata                               │   │
│  │ • symbols - Symbol metadata                          │   │
│  │ • funding_rates - Time-series rates                  │   │
│  └──────────────────────────────────────────────────────┘   │
│                            │                                  │
│                            ↓                                  │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ OpportunityFinder                                     │   │
│  │                                                        │   │
│  │ find_opportunities(filters):                          │   │
│  │   1. Query latest rates from DB                      │   │
│  │   2. Calculate all DEX pair divergences              │   │
│  │   3. Calculate fees and net profit                   │   │
│  │   4. Filter by volume, OI, profitability             │   │
│  │   5. Rank and return top opportunities               │   │
│  └──────────────────────────────────────────────────────┘   │
│                            │                                  │
└────────────────────────────┼──────────────────────────────────┘
                             │
                             │ INTERNAL CALL (no HTTP!)
                             │
                             ↓
        ┌────────────────────────────────────────┐
        │   Funding Arbitrage Strategy            │
        │                                         │
        │   opportunities = await                 │
        │     opportunity_finder.find_opportunities│
        │         (filters)                       │
        │                                         │
        │   ⚡ Direct function call               │
        │   ⚡ Shared database connection         │
        │   ⚡ Zero HTTP overhead                 │
        └─────────────────────────────────────────┘
```

### Internal vs External API

The funding rate service has **TWO interfaces**:

#### **1. Internal Interface (Used by Trading Strategies)**
```python
# Direct function calls, no HTTP
# Used when running in same process

from funding_rate_service.core.opportunity_finder import OpportunityFinder
from funding_rate_service.database.connection import database

# Initialize once
opportunity_finder = OpportunityFinder(
    database=database,
    fee_calculator=fee_calculator,
    ...
)

# Query directly
opportunities = await opportunity_finder.find_opportunities(
    OpportunityFilter(min_profit_percent=Decimal("0.001"))
)

# ✅ Advantages:
# • Zero latency (no HTTP)
# • Shared database connection pool
# • Type-safe (Python objects, not JSON)
# • No serialization overhead
```

#### **2. External REST API (Used by External Services)**
```bash
# HTTP API endpoints
# Used when funding service runs separately

# Get opportunities
curl http://localhost:8000/api/v1/opportunities?min_profit=0.001

# Compare rates between two DEXes
curl "http://localhost:8000/api/v1/funding-rates/compare?dex1=lighter&dex2=backpack&symbol=BTC"

# Get funding rate history
curl http://localhost:8000/api/v1/history/funding-rates/lighter/BTC?period=7d

# ✅ Advantages:
# • Service isolation
# • Language agnostic
# • Can scale independently
# • External monitoring tools can access
```

**Current setup:** Trading bot uses **internal interface** for zero latency.

---

## Component Interaction Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                                                                      │
│                         USER                                         │
│                                                                      │
│  Runs: python runbot.py --config configs/funding_arb.yml           │
│                                                                      │
└──────────────────────────────┬───────────────────────────────────────┘
                               │
                               ↓
┌──────────────────────────────────────────────────────────────────────┐
│                         runbot.py                                     │
│                                                                       │
│  • Parses config file                                                │
│  • Loads environment variables                                       │
│  • Creates TradingBot instance                                       │
└──────────────────────────────┬────────────────────────────────────────┘
                               │
                               ↓
┌──────────────────────────────────────────────────────────────────────┐
│                        TradingBot                                     │
│                                                                       │
│  Initialization:                                                      │
│  1. Create exchange clients (ExchangeFactory)                        │
│     ├─ LighterClient                                                 │
│     ├─ BackpackClient                                                │
│     └─ EdgeXClient                                                   │
│                                                                       │
│  2. Create strategy (StrategyFactory)                                │
│     └─ FundingArbitrageStrategy(exchange_clients)                   │
│                                                                       │
│  Main Loop:                                                           │
│  while True:                                                          │
│      await strategy.execute_cycle()                                  │
│      await asyncio.sleep(60)                                         │
└──────────────────────────────┬────────────────────────────────────────┘
                               │
                               ↓
┌──────────────────────────────────────────────────────────────────────┐
│               FundingArbitrageStrategy (Layer 3)                      │
│                                                                       │
│  Components:                                                          │
│  ├─ FundingRateAnalyzer - Rate normalization                        │
│  ├─ FundingArbFeeCalculator - Fee & profitability analysis          │
│  ├─ PositionManager - Position tracking (DB)                        │
│  ├─ StateManager - State persistence (DB)                           │
│  ├─ AtomicMultiOrderExecutor - Safe execution                       │
│  └─ OpportunityFinder - Opportunity discovery                       │
│                                                                       │
│  execute_cycle():                                                     │
│  ├─ Phase 1: Monitor positions                                      │
│  ├─ Phase 2: Check exits                                            │
│  └─ Phase 3: Scan opportunities                                     │
└─────────┬──────────┬──────────┬────────────────────────────────────┘
          │          │          │
          │          │          └─────────────────┐
          │          │                            │
          ↓          ↓                            ↓
┌──────────────┐ ┌──────────────┐ ┌──────────────────────────────────┐
│ PostgreSQL   │ │ Execution    │ │ OpportunityFinder                │
│ Database     │ │ Layer (L2)   │ │                                  │
│              │ │              │ │ • Query funding rates from DB    │
│ Tables:      │ │ Components:  │ │ • Calculate divergences          │
│ • positions  │ │ • Atomic     │ │ • Calculate fees                 │
│ • payments   │ │   Executor   │ │ • Filter opportunities           │
│ • state      │ │ • Order      │ │ • Rank by profitability          │
│              │ │   Executor   │ │                                  │
│              │ │ • Liquidity  │ │                                  │
│              │ │   Analyzer   │ │                                  │
└──────────────┘ └─────┬────────┘ └──────────────────────────────────┘
                       │
                       │ Uses
                       ↓
        ┌──────────────────────────────────────┐
        │   Exchange Clients (Layer 1)         │
        │                                      │
        │   LighterClient:                     │
        │   • fetch_bbo_prices()              │
        │   • place_limit_order()             │
        │   • place_market_order()            │
        │                                      │
        │   BackpackClient:                    │
        │   • fetch_bbo_prices()              │
        │   • place_limit_order()             │
        │   • place_market_order()            │
        │                                      │
        │   EdgeXClient:                       │
        │   • fetch_bbo_prices()              │
        │   • place_limit_order()             │
        │   • place_market_order()            │
        └────────────┬─────────────────────────┘
                     │
                     │ Executes on
                     ↓
        ┌──────────────────────────────────────┐
        │         DEX APIs                      │
        │                                       │
        │   • Lighter DEX API                  │
        │   • Backpack DEX API                 │
        │   • EdgeX DEX API                    │
        └───────────────────────────────────────┘
```

---

## Execution Flow Example

**Complete trace of opening a funding arbitrage position:**

```
TIME: 12:00:00 - Strategy wakes up
════════════════════════════════════════════════════════════════════

[12:00:00.100] Strategy: execute_cycle() called
[12:00:00.105] Strategy: Phase 1 - Monitor positions (0 open positions)
[12:00:00.110] Strategy: Phase 2 - Check exits (nothing to exit)
[12:00:00.115] Strategy: Phase 3 - Scan opportunities

[12:00:00.120] OpportunityFinder: Query PostgreSQL for latest rates
                                   
                SQL: SELECT * FROM funding_rates
                     WHERE timestamp > NOW() - INTERVAL '5 minutes'
                     
                Results:
                ┌──────────┬──────────────┬──────────────┐
                │ DEX      │ Symbol       │ Rate         │
                ├──────────┼──────────────┼──────────────┤
                │ lighter  │ BTC          │ 0.0001       │
                │ backpack │ BTC          │ 0.0015       │
                │ edgex    │ BTC          │ 0.0012       │
                │ lighter  │ ETH          │ 0.0002       │
                │ backpack │ ETH          │ 0.0008       │
                └──────────┴──────────────┴──────────────┘

[12:00:00.250] OpportunityFinder: Calculate opportunities
                
                BTC opportunities:
                ┌──────────┬──────────┬────────────┬─────────────┐
                │ Long DEX │ Short DEX│ Divergence │ Net Profit  │
                ├──────────┼──────────┼────────────┼─────────────┤
                │ lighter  │ backpack │ 0.0014     │ 0.0012      │
                │ lighter  │ edgex    │ 0.0011     │ 0.0009      │
                │ edgex    │ backpack │ 0.0003     │ 0.0001      │
                └──────────┴──────────┴────────────┴─────────────┘
                
                Best: lighter (long) / backpack (short)
                Net profit: 0.12% after fees

[12:00:00.300] Strategy: Found 3 opportunities, taking top 1

[12:00:00.305] Strategy: Opening position
                Symbol: BTC
                Long: lighter @ 0.0001 (pay funding)
                Short: backpack @ 0.0015 (receive funding)
                Size: $1,000 USD

[12:00:00.310] AtomicExecutor: Starting atomic execution
                Orders:
                  1. Buy BTC on lighter ($1,000)
                  2. Sell BTC on backpack ($1,000)

[12:00:00.315] AtomicExecutor: Pre-flight checks

[12:00:00.320] LiquidityAnalyzer: Check lighter
                Symbol: BTC, Side: buy, Size: $1,000
                
                Order book (lighter):
                ┌────────────┬──────────┐
                │ Price      │ Size     │
                ├────────────┼──────────┤
                │ 50,001 (A) │ 2.5 BTC  │
                │ 50,000 (B) │ 5.0 BTC  │
                └────────────┴──────────┘
                
                Analysis:
                • Depth sufficient: YES
                • Expected slippage: 0.01%
                • Liquidity score: 0.95
                • Recommendation: use_limit

[12:00:00.350] LiquidityAnalyzer: Check backpack
                Symbol: BTC, Side: sell, Size: $1,000
                
                Order book (backpack):
                ┌────────────┬──────────┐
                │ Price      │ Size     │
                ├────────────┼──────────┤
                │ 50,005 (A) │ 3.0 BTC  │
                │ 50,004 (B) │ 4.0 BTC  │
                └────────────┴──────────┴
                
                Analysis:
                • Depth sufficient: YES
                • Expected slippage: 0.01%
                • Liquidity score: 0.93
                • Recommendation: use_limit

[12:00:00.380] AtomicExecutor: ✅ Pre-flight checks passed

[12:00:00.385] AtomicExecutor: Placing orders simultaneously

[12:00:00.390] OrderExecutor (lighter): Execute buy order
                Mode: limit_with_fallback
                Size: $1,000 USD = 0.02 BTC @ $50,000
                
[12:00:00.395] OrderExecutor (backpack): Execute sell order
                Mode: limit_with_fallback
                Size: $1,000 USD = 0.02 BTC @ $50,004

[12:00:00.400] OrderExecutor (lighter): Try limit order
                Price: $49,999 (maker order, one tick below ask)
                
[12:00:00.405] LighterClient: place_limit_order()
                Contract: BTC-PERP
                Quantity: 0.02 BTC
                Price: $49,999
                Side: buy
                
[12:00:00.450] LighterClient: Order placed, ID: 12345

[12:00:00.405] OrderExecutor (backpack): Try limit order
                Price: $50,005 (maker order, one tick above bid)

[12:00:00.410] BackpackClient: place_limit_order()
                Contract: BTC-PERP
                Quantity: 0.02 BTC
                Price: $50,005
                Side: sell

[12:00:00.480] BackpackClient: Order placed, ID: 67890

[12:00:00.500] OrderExecutor (lighter): Waiting for fill (30s timeout)
[12:00:00.500] OrderExecutor (backpack): Waiting for fill (30s timeout)

[12:00:01.000] OrderExecutor (lighter): Check order status
[12:00:01.050] LighterClient: Order 12345 status: OPEN (0% filled)

[12:00:01.500] OrderExecutor (lighter): Check order status
[12:00:01.550] LighterClient: Order 12345 status: FILLED ✅
                Filled: 0.02 BTC @ $49,999
                
[12:00:01.000] OrderExecutor (backpack): Check order status
[12:00:01.080] BackpackClient: Order 67890 status: OPEN (0% filled)

[12:00:01.500] OrderExecutor (backpack): Check order status
[12:00:01.580] BackpackClient: Order 67890 status: OPEN (0% filled)

[12:00:02.000] OrderExecutor (backpack): Check order status
[12:00:02.080] BackpackClient: Order 67890 status: FILLED ✅
                Filled: 0.02 BTC @ $50,005

[12:00:02.100] OrderExecutor (lighter): ✅ FILLED
                Result: {
                    "success": true,
                    "filled": true,
                    "fill_price": 49999,
                    "slippage_usd": 0.20,
                    "execution_mode_used": "limit"
                }

[12:00:02.100] OrderExecutor (backpack): ✅ FILLED
                Result: {
                    "success": true,
                    "filled": true,
                    "fill_price": 50005,
                    "slippage_usd": 0.20,
                    "execution_mode_used": "limit"
                }

[12:00:02.150] AtomicExecutor: Check results
                Order 1 (lighter buy): FILLED ✅
                Order 2 (backpack sell): FILLED ✅
                
                ✅ ALL FILLED - Success!

[12:00:02.160] AtomicExecutor: Calculate metrics
                Total slippage: $0.40
                Execution time: 1,760 ms
                
[12:00:02.170] AtomicExecutor: Return result
                AtomicExecutionResult(
                    success=True,
                    all_filled=True,
                    filled_orders=[lighter_result, backpack_result],
                    total_slippage_usd=0.40,
                    execution_time_ms=1760
                )

[12:00:02.180] Strategy: ✅ Atomic execution successful!

[12:00:02.185] FundingArbFeeCalculator: Calculate entry fees
                lighter: maker fee = 0.02% × $1,000 = $0.20
                backpack: maker fee = 0.02% × $1,000 = $0.20
                Total entry fees: $0.40

[12:00:02.190] Strategy: Create position record
                
                position = FundingArbPosition(
                    id=550e8400-e29b-41d4-a716-446655440000,
                    symbol="BTC",
                    long_dex="lighter",
                    short_dex="backpack",
                    size_usd=1000,
                    entry_long_rate=0.0001,
                    entry_short_rate=0.0015,
                    entry_divergence=0.0014,
                    total_fees_paid=0.80,  # $0.40 fees + $0.40 slippage
                    opened_at=2025-10-09 12:00:02
                )

[12:00:02.200] PositionManager: Save to database
                
                INSERT INTO strategy_positions (
                    id, strategy_name, symbol,
                    long_dex, short_dex, size_usd,
                    entry_long_rate, entry_short_rate,
                    entry_divergence, total_fees_paid,
                    opened_at, status
                ) VALUES (
                    '550e8400-e29b-41d4-a716-446655440000',
                    'funding_arbitrage', 'BTC',
                    'lighter', 'backpack', 1000.00,
                    0.0001, 0.0015,
                    0.0014, 0.80,
                    '2025-10-09 12:00:02', 'open'
                )

[12:00:02.250] PositionManager: ✅ Position saved

[12:00:02.255] Strategy: ✅ Position opened successfully
                BTC: Long lighter @ $49,999, Short backpack @ $50,005
                Entry cost: $0.80
                Expected profit: 0.14% per 8 hours = $1.40
                Breakeven: ~4.5 hours

[12:00:02.260] Strategy: execute_cycle() complete
                Actions taken: 1 (opened BTC position)
                Next check: 12:01:02

TIME: 12:00:02 - Strategy sleeps for 60 seconds
════════════════════════════════════════════════════════════════════
```

---

## Key Design Decisions

### 1. **Why 3-layer Architecture?**

**Decision:** Strict separation between strategy, execution, and exchange layers.

**Rationale:**
- **Strategy (Layer 3)** focuses on WHAT to trade (business logic)
- **Execution (Layer 2)** focuses on HOW to trade safely (tactics)
- **Exchanges (Layer 1)** focuses on WHERE to trade (exchange APIs)

**Benefits:**
- Strategies are exchange-agnostic (can switch DEXes easily)
- Execution utilities are reusable across all strategies
- Exchange clients are isolated (changes don't affect strategies)

**Alternative Rejected:** Monolithic strategies with embedded execution logic
- Would duplicate execution code across strategies
- Would make exchange changes require strategy rewrites

---

### 2. **Why Internal Service Calls vs HTTP?**

**Decision:** Funding rate service uses direct function calls, not HTTP API.

**Rationale:**
```python
# HTTP API approach (rejected):
response = await http_client.get("http://localhost:8000/api/v1/opportunities")
opportunities = response.json()
# Latency: ~5-20ms, serialization overhead

# Internal call approach (chosen):
opportunities = await opportunity_finder.find_opportunities(filters)
# Latency: <1ms, no serialization
```

**Benefits:**
- Zero HTTP overhead (~20ms saved per call)
- Type-safe (Python objects, not JSON)
- Shared database connection pool (efficient)
- No serialization/deserialization

**Trade-off:** Services must run in same process
- **Acceptable for now:** Trading bot and funding service are tightly coupled
- **Future:** Can add HTTP API for external monitoring tools

---

### 3. **Why Atomic Execution Pattern?**

**Decision:** Use AtomicMultiOrderExecutor for delta-neutral strategies.

**Rationale:**
```
Without atomic execution:
• Place long on DEX A → Filled
• Place short on DEX B → Rejected
• Current state: LONG exposure (BAD!)
• Risk: Large losses from price movement

With atomic execution:
• Place long on DEX A → Filled
• Place short on DEX B → Rejected
• Atomic executor detects partial fill
• Immediately market close on DEX A
• Current state: No exposure (SAFE!)
• Cost: Small slippage loss (acceptable)
```

**Benefits:**
- Prevents directional exposure (critical for delta-neutral)
- Automatic rollback on failures
- Predictable worst-case cost (slippage vs large losses)

**Alternative Rejected:** Manual position management
- Too error-prone (easy to forget to close)
- Race conditions (price can move before manual close)

---

### 4. **Why PostgreSQL for State Persistence?**

**Decision:** Store positions, funding payments, and state in PostgreSQL.

**Rationale:**
- **Durability:** Strategy survives restarts
- **Auditability:** Complete history of positions and payments
- **Analytics:** Can query historical performance
- **Shared access:** Multiple instances can coordinate (future)

**Benefits:**
```python
# Without database (in-memory only):
# Bot restarts → Lost all position tracking!

# With database:
# Bot restarts → Loads positions from DB, continues tracking
positions = await position_manager.load_all_positions()
for position in positions:
    # Continue monitoring from where we left off
    ...
```

**Alternative Rejected:** In-memory only (like vanilla Hummingbot)
- Loses state on restart
- No historical analysis
- Can't scale to multiple instances

---

### 5. **Why Multi-Exchange Architecture?**

**Decision:** Support simultaneous connections to multiple DEXes.

**Rationale:**
- **Funding arb requires it:** Must trade on 2+ DEXes simultaneously
- **Grid strategy doesn't need it:** Single DEX is fine
- **Solution:** Conditional multi-exchange mode

**Implementation:**
```python
# Single-exchange mode (grid strategy):
exchange_client = LighterClient(...)
strategy = GridStrategy(config, exchange_client)

# Multi-exchange mode (funding arbitrage):
exchange_clients = {
    "lighter": LighterClient(...),
    "backpack": BackpackClient(...),
    "edgex": EdgeXClient(...)
}
strategy = FundingArbitrageStrategy(config, exchange_clients)
```

**Benefits:**
- Flexible (each strategy gets what it needs)
- Backward compatible (single-exchange strategies still work)
- Efficient (don't create unnecessary connections)

---

### 6. **Why Hummingbot-Inspired Patterns?**

**Decision:** Extract execution patterns from Hummingbot (battle-tested).

**Rationale:**
- **Proven:** Hummingbot has handled billions in volume
- **Correct:** Funding rate normalization formulas are accurate
- **Safe:** Position tracking patterns prevent common bugs

**What we extracted:**
- Funding rate normalization (different DEXes have different intervals)
- Fee calculation patterns (accurate entry/exit cost estimation)
- Position aggregation (track long + short as single logical position)
- Atomic execution concept (ArbitrageExecutor pattern)

**What we didn't take:**
- UI/CLI system (too complex, not needed)
- Full orchestrator (overkill for single strategy)
- In-memory state (we use PostgreSQL instead)

---

## Operator Tooling & Dashboard

- **Dashboard snapshots/events (DB-backed)**  
  - `dashboard/models.py` defines the Pydantic schema (`DashboardSnapshot`, `TimelineEvent`, etc.) shared across the strategy and tooling.
  - `dashboard/service.py` runs inside the strategy but stays dormant unless `dashboard.enabled` is true. It mirrors session heartbeat, optionally persists to PostgreSQL (`dashboard_sessions`, `dashboard_snapshots`, `dashboard_events`), and can drive renderers.
  - Persistence uses lightweight repo wrappers (`funding_rate_service/database/repositories/dashboard_repository.py`) with JSON/UTC-safe encoding.

- **Headless scripts**  
  - `scripts/dashboard_viewer.py` renders the latest snapshot as a static Rich summary, decoupled from the bot terminal. This leverages `dashboard/viewer_utils.py` for loading and formatting.
  - The script is safe to run while the bot is active; it opens a DB connection, prints, and exits.

- **Textual TUI (Step 3 foundation)**  
  - `tui/dashboard_app.py` defines a Textual application with a simple menu (`View Latest Snapshot`, `Start Bot` placeholder, `Exit`).  
  - Entry point: `python scripts/dashboard_tui.py`. Requires `textual>=0.44.0` plus existing dependencies (`rich`, `databases`).
  - The TUI currently fetches on demand (no live polling). Future enhancements: periodic refresh, bot lifecycle controls, funding monitors.
  - The control server (`dashboard/control_server.py`) exposes `/snapshot`, `/stream`, and `/commands` (WebSocket + REST) so future UIs can subscribe to live updates and issue actions (e.g., manual close). `FundingArbitrageStrategy` registers a handler when dashboard mode is enabled.

- **Configuration defaults**  
  - `FundingArbConfig.dashboard` defaults to disabled, so the trading loop runs without rendering or persistence unless explicitly enabled in YAML.
  - Offline tools rely on the stored snapshots; they are unaffected when in-process rendering is turned off.

These additions let operators monitor state after the fact or in a dedicated terminal, while paving the way for a richer CLI/TUI experience without disturbing the core trading architecture.

---

## Summary

This architecture provides:
- ✅ **Safety** - Atomic execution prevents directional exposure
- ✅ **Performance** - Internal service calls, zero HTTP overhead
- ✅ **Extensibility** - Easy to add new exchanges and strategies
- ✅ **Reliability** - Database-backed state persistence
- ✅ **Modularity** - Clear separation of concerns (3 layers)
- ✅ **Multi-DEX** - Simultaneous trading across multiple exchanges

**Primary use case:** Delta-neutral funding rate arbitrage across perpetual DEX markets.

---

**Version History:**
- v1.0 (2025-01-15): Initial single-exchange architecture
- v2.0 (2025-08-20): Shared exchange library refactor
- v2.1 (2025-09-10): Modular strategy architecture + Hummingbot patterns
- v2.5 (2025-10-09): Multi-exchange support + Interactive config + This document

**Next Steps:**
- See `docs/QUICK_START.md` for getting started
- See `docs/CLI_COMMANDS.md` for command reference
- See `funding_rate_service/docs/API_ENDPOINTS.md` for API documentation
- See `strategies/implementations/funding_arbitrage/` for strategy implementation
### Live Snapshot Cache
- `dashboard/state.py` maintains an in-memory `DashboardState` that stores the most recent `DashboardSnapshot`, session metadata, and a rolling buffer of `TimelineEvent`s.
- `dashboard_service` updates this cache on every snapshot/event publication, even when persistence/rendering are disabled. It acts as the authoritative live view for downstream consumers (UI, control plane) while PostgreSQL retains historical snapshots.
- Future iterations will expose this state via a control API/event stream so new UIs don’t have to poll the database for up-to-date metrics.
