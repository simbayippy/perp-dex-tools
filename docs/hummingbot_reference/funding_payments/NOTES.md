# Funding Payments & Data Types - Analysis & Notes

## Overview
This package contains **core data types, events, and the actual v2 funding rate arbitrage strategy** from Hummingbot. It's a mix of fundamental trading primitives and the complete funding arb implementation.

---

## 🏗️ Package Structure

### 1. **Data Types** (`data_type/`)
Core trading primitives used across all Hummingbot strategies.

### 2. **Events** (`event/`)
Event system for market/order/funding events.

### 3. **V2 Funding Rate Arb Strategy** (`v2_funding_rate_arb.py`)
⭐ **The actual implementation you want to reference!**

---

## 📦 Core Data Types

### A. **Trading Primitives** (`data_type/common.py`)

```python
class OrderType(Enum):
    MARKET = 1
    LIMIT = 2
    LIMIT_MAKER = 3
    AMM_SWAP = 4

class TradeType(Enum):
    BUY = 1
    SELL = 2
    RANGE = 3

class PositionAction(Enum):
    OPEN = "OPEN"
    CLOSE = "CLOSE"
    NIL = "NIL"

class PositionSide(Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    BOTH = "BOTH"  # For exchanges that support both simultaneously

class PositionMode(Enum):
    HEDGE = "HEDGE"  # Separate long/short positions
    ONEWAY = "ONEWAY"  # Net position only
```

**Applicability to Your System:**
- ✅ Use these enums directly - they're standard trading types
- PositionSide/PositionMode needed for perpetual DEXes

---

### B. **Funding Info** (`data_type/funding_info.py`)

```python
class FundingInfo:
    trading_pair: str
    index_price: Decimal  # Spot index price
    mark_price: Decimal   # Perpetual mark price
    next_funding_utc_timestamp: int
    rate: Decimal  # Funding rate (e.g., 0.0001 = 0.01%)
    
    def update(self, info_update: FundingInfoUpdate)

@dataclass
class FundingInfoUpdate:
    trading_pair: str
    index_price: Optional[Decimal] = None
    mark_price: Optional[Decimal] = None
    next_funding_utc_timestamp: Optional[int] = None
    rate: Optional[Decimal] = None
```

**🎯 Key Insight:**
This is **exactly** what your `funding_rate_service` stores in PostgreSQL! Their in-memory version vs. your persistent version.

**Comparison:**
```python
# Hummingbot (in-memory)
funding_info = FundingInfo(
    trading_pair="BTC-USD",
    rate=Decimal("0.0001"),
    next_funding_utc_timestamp=1234567890
)

# Your System (PostgreSQL)
INSERT INTO funding_rates (
    dex_name, symbol, rate, 
    next_funding_time, created_at
)
```

**Recommendation:**
- Keep your PostgreSQL approach for historical tracking
- Use FundingInfo structure for in-strategy caching
- Fetch from your API → convert to FundingInfo objects

---

### C. **In-Flight Orders** (`data_type/in_flight_order.py`)

```python
class OrderState(Enum):
    PENDING_CREATE = 0
    OPEN = 1
    PENDING_CANCEL = 2
    CANCELED = 3
    PARTIALLY_FILLED = 4
    FILLED = 5
    FAILED = 6

class InFlightOrder:
    client_order_id: str
    trading_pair: str
    order_type: OrderType
    trade_type: TradeType
    amount: Decimal
    price: Optional[Decimal]
    creation_timestamp: float
    current_state: OrderState
    
    # Execution tracking
    executed_amount_base: Decimal
    executed_amount_quote: Decimal
    order_fills: Dict[str, TradeUpdate]
    
    # Events
    exchange_order_id_update_event: asyncio.Event
    completely_filled_event: asyncio.Event
    
    # Methods
    update_with_order_update(order_update: OrderUpdate) -> bool
    update_with_trade_update(trade_update: TradeUpdate) -> bool
    async wait_until_completely_filled()
```

**Key Pattern:**
- Tracks order through entire lifecycle
- Updates from exchange events (OrderUpdate, TradeUpdate)
- Async events for waiting on state changes

**For Your System:**
```python
# When placing order on Lighter
order = InFlightOrder(
    client_order_id="funding_arb_123_long",
    trading_pair="BTC-USD",
    order_type=OrderType.MARKET,
    trade_type=TradeType.BUY,
    amount=Decimal("100")
)

# When exchange confirms
order.update_with_order_update(OrderUpdate(
    new_state=OrderState.OPEN,
    exchange_order_id="0x123abc"
))

# When filled
order.update_with_trade_update(TradeUpdate(
    fill_price=Decimal("50000"),
    fill_base_amount=Decimal("100")
))
```

---

### D. **Trade Fees** (`data_type/trade_fee.py`)

```python
@dataclass
class TradeFeeSchema:
    percent_fee_token: Optional[str] = None  # e.g., "BNB" for Binance
    maker_percent_fee_decimal: Decimal = Decimal("0")
    taker_percent_fee_decimal: Decimal = Decimal("0")
    buy_percent_fee_deducted_from_returns: bool = False
    maker_fixed_fees: List[TokenAmount] = []  # Gas fees
    taker_fixed_fees: List[TokenAmount] = []

@dataclass
class TradeFeeBase(ABC):
    percent: Decimal
    percent_token: Optional[str]
    flat_fees: List[TokenAmount]  # [(token, amount), ...]
    
    def fee_amount_in_token(
        self, trading_pair, price, order_amount, token, exchange
    ) -> Decimal

# Two implementations:
class AddedToCostTradeFee(TradeFeeBase):
    # Fee added to order cost (most common)
    
class DeductedFromReturnsTradeFee(TradeFeeBase):
    # Fee deducted from what you receive
```

**🎯 Critical for Funding Arb:**
Your profitability calculation MUST account for:
1. **Trading fees** on both DEXes (open + close)
2. **Gas fees** for on-chain DEXes (Lighter, etc.)
3. **Fee token** (some DEXes use native token for discounts)

**Example:**
```python
# Opening position: $100 on each side
# Lighter: 0.02% maker fee + $0.05 gas
# Backpack: 0.05% taker fee

total_entry_cost = (
    100 * 0.0002 +  # Lighter fee
    0.05 +           # Gas
    100 * 0.0005     # Backpack fee
) = 0.02 + 0.05 + 0.05 = $0.12

# Funding rate needs to cover $0.12 + exit fees before profitable
```

---

### E. **Order Candidates** (`data_type/order_candidate.py`)

```python
@dataclass
class OrderCandidate:
    """
    Represents a POTENTIAL order before execution.
    Used for budget checking and sizing.
    """
    trading_pair: str
    is_maker: bool
    order_type: OrderType
    order_side: TradeType
    amount: Decimal
    price: Decimal
    
    # Populated by populate_collateral_entries()
    order_collateral: Optional[TokenAmount]
    percent_fee_collateral: Optional[TokenAmount]
    fixed_fee_collaterals: List[TokenAmount]
    potential_returns: Optional[TokenAmount]
    
    def populate_collateral_entries(self, exchange)
    def adjust_from_balances(self, available_balances: Dict[str, Decimal])
    def set_to_zero()  # If insufficient balance

@dataclass  
class PerpetualOrderCandidate(OrderCandidate):
    leverage: Decimal = Decimal("1")
    position_close: bool = False
    
    # Accounts for leverage in collateral calculation
```

**Use Case:**
Before placing orders, check if you have enough balance:

```python
# Want to open $1000 position at 20x leverage
candidate = PerpetualOrderCandidate(
    trading_pair="BTC-USD",
    order_side=TradeType.BUY,
    amount=Decimal("0.02"),  # 0.02 BTC
    price=Decimal("50000"),
    leverage=Decimal("20"),
    is_maker=True
)

candidate.populate_collateral_entries(lighter_connector)
# order_collateral = $1000 / 20 = $50
# percent_fee_collateral = $1000 * 0.0002 * 20 = $4

available = {"USD": Decimal("100")}
candidate.adjust_from_balances(available)
# If insufficient, amount gets scaled down or set to zero
```

---

## 🎪 Event System (`event/`)

### Market Events (`event/events.py`)

```python
class MarketEvent(Enum):
    BuyOrderCreated = 200
    SellOrderCreated = 201
    OrderFilled = 107
    OrderCancelled = 106
    BuyOrderCompleted = 102
    SellOrderCompleted = 103
    OrderFailure = 198
    FundingPaymentCompleted = 202  # ⭐ KEY EVENT
    FundingInfo = 203

@dataclass
class FundingPaymentCompletedEvent:
    timestamp: float
    market: str  # "binance_perpetual"
    trading_pair: str
    amount: Decimal  # Payment amount (+ = received, - = paid)
    funding_rate: Decimal

@dataclass  
class OrderFilledEvent:
    timestamp: float
    order_id: str
    trading_pair: str
    trade_type: TradeType
    price: Decimal
    amount: Decimal
    trade_fee: TradeFeeBase
```

**🎯 For Your Funding Arb:**

```python
def did_complete_funding_payment(
    self, 
    event: FundingPaymentCompletedEvent
):
    # Track cumulative funding
    self.cumulative_funding[event.market] += event.amount
    
    # Check if position still profitable
    total_funding = sum(self.cumulative_funding.values())
    if total_funding + self.unrealized_pnl > self.profit_threshold:
        self.close_position()
```

---

## ⭐ V2 Funding Rate Arbitrage Strategy

**File:** `v2_funding_rate_arb.py` (351 lines)

This is **THE** reference implementation for funding arb in Hummingbot!

### Config Structure

```python
class FundingRateArbitrageConfig(StrategyV2ConfigBase):
    leverage: int = 20
    min_funding_rate_profitability: Decimal = 0.001  # 0.1% minimum spread
    connectors: Set[str] = {"hyperliquid_perpetual", "binance_perpetual"}
    tokens: Set[str] = {"WIF", "FET"}
    position_size_quote: Decimal = 100  # $100 per side
    profitability_to_take_profit: Decimal = 0.01  # 1% total profit to exit
    funding_rate_diff_stop_loss: Decimal = -0.001  # Exit if spread flips
    trade_profitability_condition_to_enter: bool = True
```

### Strategy Class

```python
class FundingRateArbitrage(StrategyV2Base):
    # Exchange-specific mappings
    quote_markets_map = {
        "hyperliquid_perpetual": "USD",
        "binance_perpetual": "USDT"
    }
    
    funding_payment_interval_map = {
        "binance_perpetual": 60 * 60 * 8,  # 8 hours
        "hyperliquid_perpetual": 60 * 60 * 1  # 1 hour
    }
```

### Key Methods Breakdown

#### 1. **Get Funding Info**
```python
def get_funding_info_by_token(self, token):
    """Fetch funding rate from each connector."""
    funding_info_report = {}
    for connector in self.config.connectors:
        trading_pair = self.get_trading_pair_for_connector(token, connector)
        funding_info = self.connectors[connector].get_funding_info(trading_pair)
        funding_info_report[connector] = funding_info
    return funding_info_report
```

**🎯 Your Equivalent:**
```python
async def get_funding_rates(symbol: str):
    response = await http_client.get(
        f"http://your-api/funding-rates/compare",
        params={"dexes": "lighter,backpack,grvt", "symbol": symbol}
    )
    return response.json()
```

#### 2. **Calculate Profitability**
```python
def get_current_profitability_after_fees(
    self, token, connector_1, connector_2, side
):
    """
    Calculate expected profit considering:
    - Funding rate differential
    - Trading fees (entry + exit)
    - Price impact
    """
    funding_info = self.get_funding_info_by_token(token)
    
    # Normalize rates to per-second basis
    rate_1 = self.get_normalized_funding_rate_in_seconds(
        funding_info, connector_1
    )
    rate_2 = self.get_normalized_funding_rate_in_seconds(
        funding_info, connector_2
    )
    
    # Annual rate difference
    funding_diff = abs(rate_1 - rate_2) * 365 * 24 * 60 * 60
    
    # Get trading fees
    fee_1 = self.get_fee_for_connector(connector_1) 
    fee_2 = self.get_fee_for_connector(connector_2)
    total_fees = (fee_1 + fee_2) * 2  # Entry + exit
    
    # Net profitability
    return funding_diff - total_fees
```

**🎯 Key Insight:**
They **annualize** the funding rate for comparison! 
- Binance pays every 8 hours
- Hyperliquid pays every 1 hour
- Need to normalize to same time basis

#### 3. **Find Best Opportunity**
```python
def get_most_profitable_combination(self, funding_info_report):
    """Find best pair of connectors for arbitrage."""
    connectors = list(self.config.connectors)
    max_profitability = Decimal("-Infinity")
    best_combo = None
    
    for i, connector_1 in enumerate(connectors):
        for connector_2 in connectors[i+1:]:
            rate_1 = funding_info_report[connector_1].rate
            rate_2 = funding_info_report[connector_2].rate
            
            # Determine which side to take
            if rate_1 > rate_2:
                # Short connector_1 (receiving funding)
                # Long connector_2 (paying funding)
                side = TradeType.SELL
                profitability = self.get_current_profitability_after_fees(
                    token, connector_1, connector_2, side
                )
            else:
                side = TradeType.BUY
                profitability = self.get_current_profitability_after_fees(
                    token, connector_2, connector_1, side
                )
            
            if profitability > max_profitability:
                max_profitability = profitability
                best_combo = (connector_1, connector_2, side)
    
    return best_combo, max_profitability
```

#### 4. **Entry Logic**
```python
def create_actions_proposal(self) -> List[CreateExecutorAction]:
    """Decide whether to open new positions."""
    actions = []
    
    for token in self.config.tokens:
        # Skip if already have position
        if self.has_position_for_token(token):
            continue
        
        funding_info = self.get_funding_info_by_token(token)
        combo, profitability = self.get_most_profitable_combination(
            funding_info
        )
        
        # Check entry condition
        if profitability >= self.config.min_funding_rate_profitability:
            connector_1, connector_2, side = combo
            
            # Create position executors (one per side)
            executor_configs = self.get_position_executors_config(
                token, connector_1, connector_2, side
            )
            
            for config in executor_configs:
                actions.append(CreateExecutorAction(
                    executor_config=config
                ))
    
    return actions
```

**Pattern:**
1. Loop through each token
2. Get funding rates from all connectors
3. Find most profitable pair
4. If spread > threshold → create executors
5. Each side gets own PositionExecutor

#### 5. **Exit Logic**
```python
def stop_actions_proposal(self) -> List[StopExecutorAction]:
    """Decide whether to close existing positions."""
    actions = []
    
    for executor in self.active_executors:
        # Get current metrics
        pnl = executor.net_pnl_quote
        funding_payments = self.get_cumulative_funding(executor)
        total_profit = pnl + funding_payments
        
        # Take profit condition
        if total_profit >= self.config.profitability_to_take_profit:
            actions.append(StopExecutorAction(
                executor_id=executor.id
            ))
            continue
        
        # Stop loss - funding rate flipped
        current_funding = self.get_funding_info_by_token(executor.token)
        funding_diff = self.calculate_funding_diff(current_funding, executor)
        
        if funding_diff < self.config.funding_rate_diff_stop_loss:
            actions.append(StopExecutorAction(
                executor_id=executor.id
            ))
    
    return actions
```

#### 6. **Funding Payment Tracking**
```python
def did_complete_funding_payment(
    self, 
    event: FundingPaymentCompletedEvent
):
    """
    Called when funding payment received/paid.
    Update cumulative tracking.
    """
    token = event.trading_pair.split("-")[0]
    connector = event.market
    
    self.cumulative_funding_by_token[token][connector] += event.amount
    
    # Log for monitoring
    self.logger().info(
        f"Funding payment: {token} on {connector}: {event.amount} "
        f"(rate: {event.funding_rate})"
    )
```

#### 7. **Position Executor Config**
```python
def get_position_executors_config(
    self, token, connector_1, connector_2, trade_side
):
    """Create configs for both sides of the position."""
    trading_pair_1 = self.get_trading_pair_for_connector(
        token, connector_1
    )
    trading_pair_2 = self.get_trading_pair_for_connector(
        token, connector_2
    )
    
    # Side 1 - e.g., SHORT on Binance
    config_1 = PositionExecutorConfig(
        trading_pair=trading_pair_1,
        connector_name=connector_1,
        side=trade_side,  # SELL
        amount=self.config.position_size_quote,
        leverage=self.config.leverage,
        triple_barrier_config=TripleBarrierConfig(
            # No TP/SL - managed at strategy level
            open_order_type=OrderType.MARKET
        )
    )
    
    # Side 2 - e.g., LONG on Hyperliquid  
    opposite_side = TradeType.BUY if trade_side == TradeType.SELL else TradeType.SELL
    config_2 = PositionExecutorConfig(
        trading_pair=trading_pair_2,
        connector_name=connector_2,
        side=opposite_side,
        amount=self.config.position_size_quote,
        leverage=self.config.leverage,
        triple_barrier_config=TripleBarrierConfig(
            open_order_type=OrderType.MARKET
        )
    )
    
    return [config_1, config_2]
```

---

## 🎯 Key Patterns for Your System

### 1. **Funding Rate Normalization**

```python
def get_normalized_funding_rate_in_seconds(self, funding_info, connector):
    """
    Convert to per-second rate for comparison.
    
    Binance: 0.0001 every 8 hours → 0.0001 / (8*3600) per second
    Hyperliquid: 0.0002 every 1 hour → 0.0002 / 3600 per second
    """
    rate = funding_info[connector].rate
    interval = self.funding_payment_interval_map[connector]
    return rate / interval
```

**Your Implementation:**
```python
FUNDING_INTERVALS = {
    "lighter": 3600,      # 1 hour
    "backpack": 28800,    # 8 hours  
    "grvt": 28800,
    "hyperliquid": 3600,
    "binance": 28800
}

def normalize_rate(dex: str, rate: Decimal) -> Decimal:
    """Return rate per second."""
    return rate / FUNDING_INTERVALS[dex]

def annualize_rate(rate_per_second: Decimal) -> Decimal:
    """Convert to annual percentage."""
    return rate_per_second * 365 * 24 * 3600
```

### 2. **Fee-Adjusted Profitability**

```python
# Entry fees (both sides)
entry_cost = (
    position_size * fee_rate_dex1 +
    position_size * fee_rate_dex2
)

# Exit fees (both sides)
exit_cost = entry_cost  # Assume same

# Total fees
total_fees = entry_cost + exit_cost

# Funding needs to cover fees + profit target
required_funding = total_fees + profit_target

# Annualized funding from spread
annual_funding = (rate_diff * 365 * 24)

# Net profitability
net_profit_pct = annual_funding - (total_fees / position_size)
```

### 3. **Position Pairing Pattern**

```python
class FundingArbPosition:
    token: str
    long_side: PositionSide
    short_side: PositionSide
    entry_timestamp: float
    cumulative_funding: Decimal
    
    def get_net_pnl(self):
        """Combine PnL from both sides."""
        long_pnl = self.long_side.unrealized_pnl
        short_pnl = self.short_side.unrealized_pnl
        return long_pnl + short_pnl + self.cumulative_funding

class PositionSide:
    connector: str
    trading_pair: str
    side: TradeType
    amount: Decimal
    entry_price: Decimal
    unrealized_pnl: Decimal
    fees_paid: Decimal
```

### 4. **Entry Condition Check**

```python
def should_enter_position(
    self,
    symbol: str,
    dex_pair: Tuple[str, str],
    funding_rates: Dict[str, Decimal]
) -> bool:
    """
    Check all conditions for entry.
    """
    dex1, dex2 = dex_pair
    
    # 1. Rate differential
    rate_diff = abs(
        normalize_rate(dex1, funding_rates[dex1]) -
        normalize_rate(dex2, funding_rates[dex2])
    )
    annual_spread = annualize_rate(rate_diff)
    
    # 2. Fee calculation
    total_fees_pct = self.calculate_total_fees(dex1, dex2)
    
    # 3. Net profitability
    net_profit = annual_spread - total_fees_pct
    
    # 4. Threshold check
    if net_profit < self.config.min_profitability:
        return False
    
    # 5. Check we don't already have position
    if self.has_position(symbol, dex1, dex2):
        return False
    
    # 6. Balance check
    if not self.sufficient_balance(dex1, dex2):
        return False
    
    return True
```

---

## 💡 Recommendations for Your System

### ✅ Use Directly From This Package:

1. **FundingInfo** structure for in-memory caching
2. **FundingPaymentCompletedEvent** for tracking payments
3. **TradeType, OrderType, PositionSide** enums
4. **InFlightOrder** for order tracking
5. **TradeFeeBase** for fee calculations

### 🔄 Adapt for Your Context:

1. **Funding rate normalization logic**
   - Extract the per-second conversion
   - Apply to your 5 DEXes with different intervals

2. **Profitability calculation**
   - Use their fee-adjusted approach
   - Add your gas cost estimates

3. **Position pairing pattern**
   - Track long + short as single logical position
   - Aggregate PnL + funding

### ⚠️ Don't Use:

1. **Order book tracking** - You're not doing market making
2. **Remote API data source** - You have your own service
3. **User stream tracker** - Connectors handle this

---

## 🚀 Integration Strategy

### Step 1: Data Layer
```python
# Convert your API response to FundingInfo
async def fetch_funding_info(dex: str, symbol: str) -> FundingInfo:
    data = await funding_rate_api.get(dex, symbol)
    return FundingInfo(
        trading_pair=f"{symbol}-USD",
        rate=Decimal(data["rate"]),
        next_funding_utc_timestamp=data["next_funding_time"],
        mark_price=Decimal(data["mark_price"]),
        index_price=Decimal(data["index_price"])
    )
```

### Step 2: Event Tracking
```python
# Subscribe to funding payment events
def subscribe_to_funding_events(self):
    for dex in self.config.dexes:
        connector = self.connectors[dex]
        connector.add_listener(
            MarketEvent.FundingPaymentCompleted,
            self.did_complete_funding_payment
        )
```

### Step 3: Strategy Logic
```python
# Use v2_funding_rate_arb.py as template
class FundingArbStrategy:
    def get_normalized_rates(self, symbol):
        """Fetch from your API + normalize."""
        ...
    
    def find_best_opportunity(self, rates):
        """Find highest spread pair."""
        ...
    
    def calculate_profitability(self, dex1, dex2, rates):
        """Fees-adjusted spread."""
        ...
    
    def create_position(self, symbol, dex1, dex2, side):
        """Open both sides simultaneously."""
        ...
    
    def should_close_position(self, position):
        """Check TP/SL/flip conditions."""
        ...
```

---

## 📝 Key Takeaways

1. **Hummingbot's approach:**
   - Fetches funding rates on-demand (no persistence)
   - Uses PositionExecutor for each side
   - Tracks cumulative funding in strategy layer
   - Compares profitability including fees

2. **Your approach:**
   - Stores historical rates in PostgreSQL ✅
   - Can query trends and patterns ✅
   - Need to add: Position pairing logic
   - Need to add: Fee-adjusted profitability calc

3. **Best of both:**
   - Keep your persistent storage
   - Add in-memory FundingInfo caching
   - Use their profitability calculation
   - Adopt position pairing pattern

4. **Critical formula:**
   ```
   Net Profitability = 
       (Annual Funding Rate Spread) - 
       (Entry Fees + Exit Fees) - 
       (Gas Costs)
   ```

---

## 🎯 Next Steps

1. **Extract v2_funding_rate_arb.py patterns:**
   - Normalize funding rates to per-second
   - Calculate fee-adjusted profitability
   - Find best DEX pair for each symbol

2. **Implement FundingInfo caching:**
   - Fetch from your API every 60s
   - Convert to FundingInfo objects
   - Use for opportunity detection

3. **Build position pairing:**
   - Track long + short as single unit
   - Aggregate PnL + funding payments
   - Close both sides together

4. **Event-driven funding tracking:**
   - Subscribe to FundingPaymentCompleted
   - Update cumulative totals
   - Check exit conditions on each payment

