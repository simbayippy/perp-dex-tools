# WebSocket Order Book Architecture

## Overview

This document describes the architecture pattern for using WebSocket connections to fetch real-time order book data across different exchanges. The pattern prioritizes WebSocket data (zero latency) over REST API calls (100-500ms latency) for both liquidity checks and limit order placement.

---

## Table of Contents

1. [Architecture Pattern](#architecture-pattern)
2. [Implementation Checklist](#implementation-checklist)
3. [Exchange-Specific Implementations](#exchange-specific-implementations)
4. [Flow Diagrams](#flow-diagrams)
5. [Adding New Exchanges](#adding-new-exchanges)
6. [Future Improvements](#future-improvements)

---

## Architecture Pattern

### Three-Layer Design

```
┌─────────────────────────────────────────────────────────────┐
│  Layer 1: Base Interface (exchange_clients/base.py)         │
│  - Defines get_order_book_from_websocket() default impl     │
│  - Defines get_order_book_depth() with WS-first pattern     │
└─────────────────────────────────────────────────────────────┘
                            ▼
┌─────────────────────────────────────────────────────────────┐
│  Layer 2: Exchange Client (e.g., aster/client.py)           │
│  - Overrides get_order_book_from_websocket()                │
│  - Implements get_order_book_depth() with WS priority       │
└─────────────────────────────────────────────────────────────┘
                            ▼
┌─────────────────────────────────────────────────────────────┐
│  Layer 3: WebSocket Manager (e.g., aster/websocket_mgr.py)  │
│  - Maintains real-time order book state                     │
│  - Provides start_order_book_stream() or switch_market()    │
└─────────────────────────────────────────────────────────────┘
```

### Key Principles

1. **Interface-First**: All functionality defined in base class
2. **Graceful Degradation**: WebSocket is optimization, REST is fallback
3. **Exchange-Specific**: Each exchange implements based on capabilities
4. **On-Demand**: WebSockets connect when opportunity is identified, not at startup

---

## Implementation Checklist

### For Each Exchange, Implement:

#### 1. Base Interface (`exchange_clients/base.py`)

Already implemented! All exchanges inherit these:

```python
def get_order_book_from_websocket(self) -> Optional[Dict[str, List[Dict[str, Decimal]]]]:
    """
    Get order book from WebSocket if available (zero latency).
    
    Default: Returns None (override in exchange client if supported)
    """
    return None

async def get_order_book_depth(self, contract_id: str, levels: int = 10):
    """
    Get order book depth with WebSocket-first pattern.
    
    Pattern:
    1. Try get_order_book_from_websocket() first
    2. Fall back to REST API if None
    """
```

#### 2. Exchange Client (`exchange_clients/<exchange>/client.py`)

**Required Methods:**

```python
def get_order_book_from_websocket(self) -> Optional[Dict[str, List[Dict[str, Decimal]]]]:
    """
    Override to return order book from WebSocket if available.
    
    Returns:
        {
            'bids': [{'price': Decimal, 'size': Decimal}, ...],
            'asks': [{'price': Decimal, 'size': Decimal}, ...]
        }
        OR None if WebSocket not available/ready
    """
    try:
        if not self.ws_manager or not self.ws_manager.running:
            return None
        
        # Exchange-specific: check if order book is ready
        if not self.ws_manager.order_book_ready:  # or similar flag
            return None
        
        # Return order book in standard format
        return self.ws_manager.order_book
    except Exception as e:
        self.logger.warning(f"Failed to get order book from WebSocket: {e}")
        return None

async def get_order_book_depth(self, contract_id: str, levels: int = 10):
    """
    Implement WebSocket-first pattern.
    """
    # 🔴 Priority 1: Try WebSocket
    ws_book = self.get_order_book_from_websocket()
    if ws_book:
        return {
            'bids': ws_book['bids'][:levels],
            'asks': ws_book['asks'][:levels]
        }
    
    # 🔄 Priority 2: Fall back to REST API
    # ... REST implementation ...
```

#### 3. WebSocket Manager (`exchange_clients/<exchange>/websocket_manager.py`)

**Required Attributes:**

```python
class ExchangeWebSocketManager:
    def __init__(self, ...):
        # Order book state
        self.order_book = {"bids": [], "asks": []}  # Standard format
        self.order_book_ready = False  # Flag to indicate data is available
        
        # BBO state (for limit orders)
        self.best_bid = None
        self.best_ask = None
```

**Required Method (Choose Pattern Based on Exchange):**

**Pattern A: Dynamic Stream Subscription (Aster-style)**
```python
async def start_order_book_stream(self, symbol: str):
    """
    Start order book depth stream for a specific symbol.
    
    Called when opportunity is identified, not at startup.
    
    Args:
        symbol: Symbol to subscribe to (e.g., "SKYUSDT")
    """
    # If already subscribed, skip
    if self._current_symbol == symbol and self._stream_active:
        return
    
    # Cancel existing stream if different symbol
    if self._depth_task and not self._depth_task.done():
        self._depth_task.cancel()
    
    # Start new stream
    self._current_symbol = symbol
    self._depth_task = asyncio.create_task(self._connect_depth_stream(symbol))

async def _connect_depth_stream(self, symbol: str):
    """Connect to order book stream and update self.order_book"""
    # Exchange-specific WebSocket connection
    # Parse incoming messages and update self.order_book
    # Set self.order_book_ready = True when first data received
```

**Pattern B: Market Switching (Lighter-style)**
```python
async def switch_market(self, market_id: int):
    """
    Switch WebSocket subscription to a different market.
    
    Lighter subscribes to one market at startup, but needs to switch
    when a different symbol opportunity is found.
    
    Args:
        market_id: Market ID to switch to
    
    Returns:
        bool: True if switch successful
    """
    if not self.ws or not self.running:
        return False
    
    # If already on this market, skip
    if self.market_index == market_id:
        return True
    
    # Unsubscribe from current market
    await self.ws.send(json.dumps({
        "type": "unsubscribe",
        "channel": f"order_book/{self.market_index}"
    }))
    
    # Update market index
    self.market_index = market_id
    
    # Reset order book
    await self.reset_order_book()
    
    # Subscribe to new market
    await self.ws.send(json.dumps({
        "type": "subscribe",
        "channel": f"order_book/{market_id}"
    }))
    
    # Wait for snapshot
    await asyncio.sleep(0.5)
    
    return True
```

---

## Exchange-Specific Implementations

### Aster (Pattern A: Dynamic Subscription)

**Capabilities:**
- ✅ BBO via `@bookTicker` stream (for limit orders)
- ✅ Full order book via `@depth20@100ms` stream (for liquidity checks)

**WebSocket Manager:**
```python
# Attributes
self.order_book = {"bids": [], "asks": []}
self.order_book_ready = False
self.best_bid = None
self.best_ask = None

# Methods
async def start_book_ticker(symbol: str)  # For BBO
async def start_order_book_stream(symbol: str)  # For full depth
```

**When to Call:**
- `start_book_ticker()` - Called in `atomic_multi_order.py` after opportunity identified
- `start_order_book_stream()` - Called in `atomic_multi_order.py` after opportunity identified

**Data Flow:**
1. Opportunity found for SKYUSDT
2. `start_order_book_stream("SKYUSDT")` called
3. WebSocket connects to `wss://fstream.asterdex.com/ws/skyusdt@depth20@100ms`
4. Receives snapshots every 100ms with top 20 bids/asks
5. Updates `self.order_book` on each message
6. Sets `self.order_book_ready = True`

---

### Lighter (Pattern B: Market Switching)

**Capabilities:**
- ✅ BBO from full order book (for limit orders)
- ✅ Full order book via `order_book/{market_id}` stream (for liquidity checks)

**WebSocket Manager:**
```python
# Attributes
self.order_book = {"bids": {}, "asks": {}}  # Dict format (price -> size)
self.snapshot_loaded = False
self.best_bid = None
self.best_ask = None
self.market_index = None  # Current market_id

# Methods
async def switch_market(market_id: int)  # Switch to different market
```

**When to Call:**
- `switch_market()` - Called in `atomic_multi_order.py` after opportunity identified

**Data Flow:**
1. WebSocket connects at startup to default market (e.g., market_id=0)
2. Opportunity found for SKY (market_id=79)
3. `switch_market(79)` called
4. Unsubscribes from market 0, subscribes to market 79
5. Receives snapshot for market 79
6. Updates `self.order_book` incrementally
7. Sets `self.snapshot_loaded = True`

---

## Flow Diagrams

### Startup Flow

```
Bot Startup
    │
    ├─> Connect to Aster
    │   ├─> Start user data WebSocket (orders)
    │   └─> DON'T start order book streams yet (symbol unknown)
    │
    └─> Connect to Lighter
        ├─> Start user data WebSocket (orders)
        └─> Start order book WebSocket for DEFAULT market (e.g., market_id=0)
```

### Opportunity Execution Flow

```
Opportunity Identified (e.g., SKY funding arb)
    │
    ▼
Pre-Flight Checks Start
    │
    ├─> 1. Leverage Validation
    │   └─> Normalize leverage to min(Aster, Lighter)
    │
    ├─> 2. WebSocket Initialization ◄── THIS IS NEW!
    │   │
    │   ├─> For Aster:
    │   │   ├─> start_book_ticker("SKYUSDT")  ◄── BBO for limit orders
    │   │   └─> start_order_book_stream("SKYUSDT")  ◄── Depth for liquidity
    │   │
    │   └─> For Lighter:
    │       └─> switch_market(79)  ◄── Switch to SKY market
    │
    ├─> Wait 2 seconds for WebSocket data
    │
    ├─> 3. Balance Validation
    │   └─> Check sufficient margin on both exchanges
    │
    └─> 4. Liquidity Check ◄── USES WEBSOCKET!
        │
        ├─> Call exchange_client.get_order_book_depth()
        │   ├─> Tries get_order_book_from_websocket() ◄── 0ms latency!
        │   └─> Falls back to REST API if None
        │
        └─> Analyze liquidity, spread, slippage
    │
    ▼
Pre-Flight Checks Pass
    │
    ▼
Place Limit Orders ◄── USES WEBSOCKET BBO!
    │
    ├─> order_executor._fetch_bbo_prices_for_limit_order()
    │   ├─> Tries ws_manager.best_bid / best_ask ◄── 0ms latency!
    │   └─> Falls back to REST API if None
    │
    └─> Place limit orders at BBO ± offset
```

### Data Priority Hierarchy

```
Order Book Request
    │
    ├─> Priority 1: WebSocket (0ms latency)
    │   ├─> Check ws_manager.order_book_ready
    │   ├─> Return ws_manager.order_book
    │   └─> ✅ ZERO latency, real-time data
    │
    └─> Priority 2: REST API (100-500ms latency)
        ├─> Make HTTP GET request to exchange
        └─> ⚠️ Slower, but reliable fallback
```

---

## Adding New Exchanges

### Step-by-Step Guide

#### Step 1: Assess WebSocket Capabilities

Ask these questions about the new exchange:

1. **Does it have a WebSocket order book stream?**
   - Yes → Implement `get_order_book_from_websocket()`
   - No → Return `None` (use base class default)

2. **What format is the order book?**
   - Snapshot (like Aster `@depth20`) → Use Pattern A
   - Incremental updates (like Lighter) → Use Pattern B
   - Hybrid → Choose based on simplicity

3. **Can you subscribe to multiple markets?**
   - Yes → Use Pattern A (dynamic subscription)
   - No (only one at a time) → Use Pattern B (market switching)

4. **Does it have a BBO stream?**
   - Yes → Also implement book ticker for limit orders
   - No → Use BBO from full order book

#### Step 2: Implement WebSocket Manager

**Option A: Dynamic Subscription (Recommended for most exchanges)**

```python
class NewExchangeWebSocketManager:
    def __init__(self, ...):
        self.order_book = {"bids": [], "asks": []}
        self.order_book_ready = False
        self.best_bid = None
        self.best_ask = None
        self._depth_task = None
        self._current_symbol = None
    
    async def start_order_book_stream(self, symbol: str):
        """Start order book stream for a symbol."""
        # Cancel existing stream if different symbol
        if self._current_symbol != symbol:
            if self._depth_task:
                self._depth_task.cancel()
            
            self._current_symbol = symbol
            self._depth_task = asyncio.create_task(
                self._connect_depth_stream(symbol)
            )
    
    async def _connect_depth_stream(self, symbol: str):
        """Connect to WebSocket and maintain order book."""
        # Exchange-specific WebSocket connection
        async with websockets.connect(ws_url) as ws:
            # Subscribe to order book stream
            await ws.send(json.dumps({
                "subscribe": f"orderbook_{symbol}"
            }))
            
            async for message in ws:
                data = json.loads(message)
                
                # Parse order book data (exchange-specific format)
                bids = [{'price': Decimal(p), 'size': Decimal(s)} 
                        for p, s in data['bids']]
                asks = [{'price': Decimal(p), 'size': Decimal(s)}
                        for p, s in data['asks']]
                
                # Update order book state
                self.order_book = {'bids': bids, 'asks': asks}
                self.order_book_ready = True
                
                # Update BBO
                if bids:
                    self.best_bid = float(bids[0]['price'])
                if asks:
                    self.best_ask = float(asks[0]['price'])
```

**Option B: Market Switching**

```python
class NewExchangeWebSocketManager:
    def __init__(self, ...):
        self.order_book = {}  # Might use dict or list format
        self.snapshot_loaded = False
        self.current_market_id = None
    
    async def switch_market(self, market_id: int):
        """Switch to a different market."""
        if self.current_market_id == market_id:
            return True
        
        # Unsubscribe from current
        await self.ws.send(json.dumps({
            "unsubscribe": self.current_market_id
        }))
        
        # Subscribe to new
        self.current_market_id = market_id
        await self.ws.send(json.dumps({
            "subscribe": market_id
        }))
        
        # Reset state
        self.order_book.clear()
        self.snapshot_loaded = False
        
        # Wait for snapshot
        await asyncio.sleep(0.5)
        
        return True
```

#### Step 3: Implement Exchange Client Methods

```python
class NewExchangeClient(BaseExchangeClient):
    def get_order_book_from_websocket(self):
        """Override to return WebSocket order book."""
        try:
            if not self.ws_manager or not self.ws_manager.running:
                return None
            
            if not self.ws_manager.order_book_ready:
                return None
            
            # Return in standard format
            return self.ws_manager.order_book
        
        except Exception as e:
            self.logger.warning(f"WebSocket order book unavailable: {e}")
            return None
    
    async def get_order_book_depth(self, contract_id: str, levels: int = 10):
        """Implement WebSocket-first pattern."""
        # Try WebSocket first
        ws_book = self.get_order_book_from_websocket()
        if ws_book:
            self.logger.info(
                f"📡 [WEBSOCKET] Using real-time order book "
                f"({len(ws_book['bids'])} bids, {len(ws_book['asks'])} asks)"
            )
            return {
                'bids': ws_book['bids'][:levels],
                'asks': ws_book['asks'][:levels]
            }
        
        # Fall back to REST
        self.logger.info("📞 [REST] Fetching order book via REST API")
        # ... REST implementation ...
```

#### Step 4: Add to atomic_multi_order.py

Add a new branch for your exchange:

```python
# In _run_preflight_checks() method, around line 376
if exchange_name == "aster":
    # Aster implementation
    ...
elif exchange_name == "lighter":
    # Lighter implementation
    ...
elif exchange_name == "newexchange":  # ◄── ADD THIS
    # Get symbol in exchange's format
    normalized_symbol = exchange_client.normalize_symbol(symbol)
    
    # Pattern A: Dynamic subscription
    if hasattr(ws_manager, 'start_order_book_stream'):
        await ws_manager.start_order_book_stream(normalized_symbol)
        self.logger.info(f"✅ Started NewExchange order book stream for {normalized_symbol}")
    
    # Pattern B: Market switching
    elif hasattr(ws_manager, 'switch_market'):
        market_id = await exchange_client._get_market_id(symbol)
        success = await ws_manager.switch_market(market_id)
        if success:
            self.logger.info(f"✅ Switched NewExchange to market {market_id}")
```

---

## Future Improvements

### Problem: Exchange-Specific Code in atomic_multi_order.py

**Current Issue:**
```python
# In atomic_multi_order.py - exchange-specific branches
if exchange_name == "aster":
    await ws_manager.start_order_book_stream(normalized_symbol)
elif exchange_name == "lighter":
    await ws_manager.switch_market(market_id)
elif exchange_name == "newexchange":
    # ... more exchange-specific code
```

**Why This Is Suboptimal:**
- Violates Open/Closed Principle (must modify atomic_multi_order.py for each new exchange)
- Exchange-specific logic leaks into strategy layer
- Harder to test and maintain

### Proposed Improvement: Unified Interface

**Option 1: Add to Base Exchange Client**

```python
# In exchange_clients/base.py
class BaseExchangeClient(ABC):
    async def prepare_order_book_stream(self, symbol: str) -> bool:
        """
        Prepare WebSocket order book stream for a specific symbol.
        
        This method should be called after an opportunity is identified
        and before pre-flight checks. It ensures the exchange's WebSocket
        is subscribed to the correct market/symbol for order book data.
        
        Args:
            symbol: Normalized symbol (e.g., "BTC", "ETH", "SKY")
        
        Returns:
            bool: True if preparation successful, False otherwise
        
        Default Implementation:
            Returns True (no-op). Override if exchange requires setup.
        
        Examples:
            - Aster: Subscribe to depth stream for symbol
            - Lighter: Switch market to symbol's market_id
            - Backpack: No-op (WebSocket maintains all markets)
        """
        # Default: no preparation needed
        return True
```

**Implementation for Each Exchange:**

```python
# aster/client.py
async def prepare_order_book_stream(self, symbol: str) -> bool:
    """Prepare Aster WebSocket for symbol."""
    try:
        if not self.ws_manager:
            return False
        
        normalized_symbol = self.normalize_symbol(symbol)
        
        # Start book ticker for BBO
        if hasattr(self.ws_manager, 'start_book_ticker'):
            await self.ws_manager.start_book_ticker(normalized_symbol)
        
        # Start order book depth stream
        if hasattr(self.ws_manager, 'start_order_book_stream'):
            await self.ws_manager.start_order_book_stream(normalized_symbol)
        
        return True
    except Exception as e:
        self.logger.error(f"Failed to prepare order book stream: {e}")
        return False

# lighter/client.py
async def prepare_order_book_stream(self, symbol: str) -> bool:
    """Prepare Lighter WebSocket for symbol."""
    try:
        if not self.ws_manager:
            return False
        
        # Get market_id for symbol
        market_id = await self._get_market_id_for_symbol(symbol)
        if market_id is None:
            return False
        
        # Switch to market
        if hasattr(self.ws_manager, 'switch_market'):
            return await self.ws_manager.switch_market(market_id)
        
        return True
    except Exception as e:
        self.logger.error(f"Failed to prepare order book stream: {e}")
        return False
```

**Simplified atomic_multi_order.py:**

```python
# In _run_preflight_checks()
self.logger.info("🔴 Preparing WebSocket streams for real-time data...")

for symbol, symbol_orders in symbols_to_check.items():
    for order in symbol_orders:
        exchange_client = order.exchange_client
        
        # ✅ Unified call - no exchange-specific branches!
        success = await exchange_client.prepare_order_book_stream(symbol)
        
        if success:
            exchange_name = exchange_client.get_exchange_name()
            self.logger.info(f"✅ Prepared {exchange_name} WebSocket for {symbol}")
        else:
            self.logger.warning(f"⚠️  Failed to prepare WebSocket for {symbol}")

# Wait for streams to initialize
await asyncio.sleep(2.0)
```

**Benefits:**
- ✅ No exchange-specific code in strategy layer
- ✅ Easy to add new exchanges (just implement one method)
- ✅ Testable in isolation
- ✅ Follows Open/Closed Principle

---

### Option 2: WebSocket Stream Manager Interface

Create a unified interface for WebSocket managers:

```python
# In exchange_clients/websocket_interface.py (new file)
from abc import ABC, abstractmethod
from typing import Optional, Dict, List
from decimal import Decimal

class WebSocketStreamManager(ABC):
    """
    Unified interface for WebSocket order book management.
    
    All exchange WebSocket managers should implement this interface
    to provide consistent order book streaming capabilities.
    """
    
    @abstractmethod
    async def prepare_symbol_stream(self, symbol: str) -> bool:
        """
        Prepare WebSocket to stream order book for a symbol.
        
        Args:
            symbol: Symbol in exchange's format (e.g., "SKYUSDT" for Aster, 79 for Lighter)
        
        Returns:
            bool: True if preparation successful
        """
        pass
    
    @abstractmethod
    def get_order_book(self) -> Optional[Dict[str, List[Dict[str, Decimal]]]]:
        """
        Get current order book from WebSocket.
        
        Returns:
            Order book dict or None if not ready
        """
        pass
    
    @abstractmethod
    def is_ready(self) -> bool:
        """Check if WebSocket has received order book data."""
        pass
```

**Implementation:**

```python
# aster/websocket_manager.py
class AsterWebSocketManager(WebSocketStreamManager):
    async def prepare_symbol_stream(self, symbol: str) -> bool:
        await self.start_order_book_stream(symbol)
        return True
    
    def get_order_book(self):
        if not self.order_book_ready:
            return None
        return self.order_book
    
    def is_ready(self) -> bool:
        return self.order_book_ready

# lighter/websocket_manager.py
class LighterWebSocketManager(WebSocketStreamManager):
    async def prepare_symbol_stream(self, symbol: str) -> bool:
        # symbol here would be market_id (passed by client)
        return await self.switch_market(int(symbol))
    
    def get_order_book(self):
        if not self.snapshot_loaded:
            return None
        # Convert dict format to list format
        bids = [{'price': Decimal(p), 'size': Decimal(s)} 
                for p, s in sorted(self.order_book["bids"].items(), reverse=True)]
        asks = [{'price': Decimal(p), 'size': Decimal(s)}
                for p, s in sorted(self.order_book["asks"].items())]
        return {'bids': bids, 'asks': asks}
    
    def is_ready(self) -> bool:
        return self.snapshot_loaded
```

---

## Summary

### Current Implementation (Working)
✅ WebSocket-first order book fetching  
✅ Separate implementations for Aster and Lighter  
✅ Exchange-specific code in `atomic_multi_order.py`  
⚠️ Requires modification for each new exchange

### Recommended Improvement
✅ Add `prepare_order_book_stream(symbol)` to `BaseExchangeClient`  
✅ Each exchange implements their specific logic  
✅ `atomic_multi_order.py` uses unified interface  
✅ Easy to extend for new exchanges

### Key Takeaways

1. **WebSocket Priority**: Always try WebSocket first, fall back to REST
2. **On-Demand Setup**: Initialize streams when opportunity identified, not at startup
3. **Graceful Degradation**: System works even if WebSocket fails
4. **Exchange Differences**: Different exchanges need different patterns (subscription vs switching)
5. **Future Extensibility**: Abstract exchange-specific code into exchange client methods

