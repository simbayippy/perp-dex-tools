# Shared Exchange Library Architecture

## Design Overview

**Problem:** Code duplication between trading client and funding rate service, with dependency conflicts between exchange SDKs.

**Solution:** Create a shared library (`/exchange_clients/`) that both services import from, with per-exchange dependency isolation.

---

## Project Structure

```
/perp-dex-tools/
├── runbot.py                           # Trading orchestrator
├── trading_bot.py                      # Main trading logic
│
├── /exchange_clients/                  # 🔥 NEW: Shared exchange library
│   ├── __init__.py
│   ├── base.py                         # BaseExchangeClient interface
│   ├── pyproject.toml                  # Dependency management
│   │
│   ├── /lighter/
│   │   ├── __init__.py
│   │   ├── client.py                   # Trading execution (orders, WebSocket)
│   │   ├── funding_adapter.py          # Funding rate collection
│   │   ├── common.py                   # Shared utilities
│   │   └── requirements.txt            # Lighter-specific dependencies
│   │
│   ├── /grvt/
│   │   ├── __init__.py
│   │   ├── client.py
│   │   ├── funding_adapter.py
│   │   ├── common.py
│   │   └── requirements.txt
│   │
│   └── /edgex/
│       ├── __init__.py
│       ├── client.py
│       ├── funding_adapter.py
│       ├── common.py
│       └── requirements.txt
│
├── /strategies/                        # Trading strategies (unchanged)
│   ├── grid_strategy.py
│   └── funding_arbitrage_strategy.py
│
├── /helpers/                           # Shared utilities (unchanged)
│   ├── logger.py
│   └── telegram_bot.py
│
└── /funding_rate_service/              # Funding rate service (minimal changes)
    ├── main.py
    ├── /collection/
    │   ├── orchestrator.py             # Now imports from /exchange_clients
    │   └── base_adapter.py             # Base interface (may move to exchange_clients)
    └── /api/
```

---

## Key Components

### 1. Base Interface (`/exchange_clients/base.py`)

```python
from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any
from decimal import Decimal

class BaseExchangeClient(ABC):
    """Base interface for trading execution"""
    
    @abstractmethod
    async def connect(self) -> None:
        """Initialize connection to exchange"""
        pass
    
    @abstractmethod
    async def place_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        size: Decimal,
        price: Optional[Decimal] = None
    ) -> Dict[str, Any]:
        """Place an order"""
        pass
    
    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an order"""
        pass
    
    @abstractmethod
    async def get_balance(self) -> Dict[str, Decimal]:
        """Get account balance"""
        pass
    
    @abstractmethod
    async def get_positions(self) -> List[Dict[str, Any]]:
        """Get open positions"""
        pass


class BaseFundingAdapter(ABC):
    """Base interface for funding rate collection"""
    
    @abstractmethod
    async def fetch_funding_rates(self) -> List[Dict[str, Any]]:
        """Fetch current funding rates for all symbols"""
        pass
    
    @abstractmethod
    async def fetch_market_data(self, symbol: str) -> Dict[str, Any]:
        """Fetch market data (volume, OI, spreads)"""
        pass
```

---

### 2. Exchange Implementation Example (`/exchange_clients/lighter/`)

#### **client.py** (Trading Execution)
```python
from exchange_clients.base import BaseExchangeClient
from lighter.lighter_client import Client as LighterSDK
from decimal import Decimal

class LighterClient(BaseExchangeClient):
    """
    Lighter trading execution client
    
    Handles:
    - Order placement/cancellation
    - WebSocket subscriptions
    - Position management
    """
    
    def __init__(self, api_key: str, private_key: str, testnet: bool = False):
        self.sdk = LighterSDK(api_key, private_key, testnet)
    
    async def connect(self):
        await self.sdk.connect()
    
    async def place_order(self, symbol, side, order_type, size, price=None):
        # Implementation using Lighter SDK
        return await self.sdk.create_order(...)
    
    async def cancel_order(self, order_id):
        return await self.sdk.cancel_order(order_id)
    
    async def get_balance(self):
        return await self.sdk.get_balances()
    
    async def get_positions(self):
        return await self.sdk.get_positions()
    
    # Lighter-specific methods
    async def subscribe_orderbook(self, symbol, callback):
        """WebSocket orderbook subscription"""
        await self.sdk.subscribe_orderbook(symbol, callback)
```

#### **funding_adapter.py** (Funding Rate Collection)
```python
from exchange_clients.base import BaseFundingAdapter
from lighter.lighter_client import Client as LighterSDK
from typing import List, Dict, Any

class LighterFundingAdapter(BaseFundingAdapter):
    """
    Lighter funding rate collection adapter
    
    Used by funding_rate_service to collect funding data
    """
    
    def __init__(self, api_url: str):
        self.api_url = api_url
        self.sdk = LighterSDK(api_url=api_url)
    
    async def fetch_funding_rates(self) -> List[Dict[str, Any]]:
        """Fetch funding rates for all symbols"""
        # Implementation
        rates = await self.sdk.get_funding_rates()
        return [
            {
                'symbol': rate['symbol'],
                'funding_rate': rate['rate'],
                'next_funding_time': rate['next_time']
            }
            for rate in rates
        ]
    
    async def fetch_market_data(self, symbol: str) -> Dict[str, Any]:
        """Fetch volume, OI, spreads"""
        return await self.sdk.get_market_data(symbol)
```

#### **common.py** (Shared Utilities)
```python
"""Shared utilities for Lighter exchange"""

def normalize_symbol(symbol: str) -> str:
    """Convert symbol to Lighter format"""
    # BTC -> BTC-PERP
    return f"{symbol}-PERP"

def parse_order_response(raw_response: dict) -> dict:
    """Standardize order response format"""
    return {
        'order_id': raw_response['id'],
        'status': raw_response['status'],
        'filled_size': raw_response['filled']
    }
```

#### **__init__.py**
```python
from .client import LighterClient
from .funding_adapter import LighterFundingAdapter

__all__ = ['LighterClient', 'LighterFundingAdapter']
```

---

### 3. Dependency Management (`/exchange_clients/pyproject.toml`)

```toml
[project]
name = "exchange-clients"
version = "1.0.0"
description = "Shared exchange clients for trading and funding rate collection"
requires-python = ">=3.10"

dependencies = [
    "asyncio",
    "aiohttp",
    "websockets",
]

[project.optional-dependencies]
lighter = [
    "lighter-python>=0.5.0",
    "eth-account>=0.8.0",
]
grvt = [
    "grvt-pysdk>=1.2.0",
]
edgex = [
    "edgex-sdk>=0.3.0",
]
all = [
    "lighter-python>=0.5.0",
    "grvt-pysdk>=1.2.0",
    "edgex-sdk>=0.3.0",
]

[build-system]
requires = ["setuptools>=45", "wheel"]
build-backend = "setuptools.build_meta"
```

**Installation:**
```bash
# For trading bot (needs all exchanges)
cd /perp-dex-tools
pip install -e ./exchange_clients[all]

# For funding service (also needs all)
cd /perp-dex-tools/funding_rate_service
pip install -e ../exchange_clients[all]

# If dependency conflicts, install selectively:
pip install -e ./exchange_clients[lighter,grvt]
```

---

## Usage Examples

### Trading Bot
```python
# In runbot.py or trading_bot.py
from exchange_clients.lighter import LighterClient
from exchange_clients.grvt import GRVTClient

# Initialize exchange client
exchange = LighterClient(
    api_key=config.LIGHTER_API_KEY,
    private_key=config.LIGHTER_PRIVATE_KEY
)

await exchange.connect()

# Place order
order = await exchange.place_order(
    symbol="BTC",
    side="BUY",
    order_type="LIMIT",
    size=Decimal("0.1"),
    price=Decimal("45000")
)
```

### Funding Rate Service
```python
# In funding_rate_service/collection/orchestrator.py
from exchange_clients.lighter import LighterFundingAdapter
from exchange_clients.grvt import GRVTFundingAdapter

# Initialize adapters
lighter_adapter = LighterFundingAdapter(api_url=LIGHTER_API_URL)
grvt_adapter = GRVTFundingAdapter(api_url=GRVT_API_URL)

# Collect funding rates
lighter_rates = await lighter_adapter.fetch_funding_rates()
grvt_rates = await grvt_adapter.fetch_funding_rates()
```

---

## Refactoring Process

### Phase 1: Setup (Week 1)

#### Step 1: Create Directory Structure
```bash
cd /perp-dex-tools
mkdir -p exchange_clients/lighter
touch exchange_clients/__init__.py
touch exchange_clients/base.py
touch exchange_clients/lighter/__init__.py
```

#### Step 2: Create Base Interface
Copy content from above into `exchange_clients/base.py`

#### Step 3: Create pyproject.toml
Copy content from above into `exchange_clients/pyproject.toml`

---

### Phase 2: Migrate Lighter (Week 1-2)

#### Step 1: Move Trading Client
```bash
# Copy existing code
cp exchanges/lighter.py exchange_clients/lighter/client.py

# Edit client.py to inherit from BaseExchangeClient
# class LighterClient(BaseExchangeClient):
```

#### Step 2: Move Funding Adapter
```bash
# Copy existing code
cp funding_rate_service/collection/adapters/lighter_adapter.py \
   exchange_clients/lighter/funding_adapter.py

# Edit to inherit from BaseFundingAdapter
```

#### Step 3: Extract Common Code
Create `exchange_clients/lighter/common.py` with shared utilities:
- Symbol normalization
- Response parsers
- Error handlers

#### Step 4: Update Imports

**In trading bot:**
```python
# OLD
from exchanges.lighter import LighterClient

# NEW
from exchange_clients.lighter import LighterClient
```

**In funding service:**
```python
# OLD
from collection.adapters.lighter_adapter import LighterAdapter

# NEW
from exchange_clients.lighter import LighterFundingAdapter
```

#### Step 5: Test Both Services
```bash
# Test trading bot
python runbot.py --exchange lighter --strategy grid

# Test funding service
cd funding_rate_service
python scripts/test_lighter_adapter.py
```

#### Step 6: Delete Old Code
```bash
# Only after tests pass!
rm exchanges/lighter.py
rm funding_rate_service/collection/adapters/lighter_adapter.py
```

---

### Phase 3: Migrate GRVT (Week 3)

Repeat Phase 2 steps for GRVT:
```bash
mkdir -p exchange_clients/grvt
# Copy and refactor grvt.py
# Copy and refactor grvt_adapter.py
# Test both services
# Delete old code
```

---

### Phase 4: Migrate EdgeX (Week 4)

Repeat Phase 2 steps for EdgeX.

---

### Phase 5: Cleanup (Week 5)

#### Delete Old Directories
```bash
rm -rf exchanges/          # Now empty
rm -rf funding_rate_service/collection/adapters/  # Now empty
```

#### Update Documentation
- Update README.md with new import paths
- Update ARCHITECTURE.md with shared library pattern
- Document dependency management approach

---

## Benefits

### Before (Current)
- **2 implementations** per exchange (trading + funding)
- **Code duplication** = 2x maintenance
- **Dependency conflicts** in single requirements.txt
- **Inconsistent behavior** between services

### After (Shared Library)
- **1 implementation** per exchange
- **Single source of truth**
- **Isolated dependencies** per exchange
- **Consistent behavior** (same code)
- **Easy testing** (import and test directly)

---

## Key Design Principles

1. **Separation of Concerns**
   - `client.py` = Trading execution (orders, WebSocket)
   - `funding_adapter.py` = Data collection (funding rates)
   - `common.py` = Shared utilities

2. **Dependency Isolation**
   - Each exchange has own `requirements.txt`
   - Optional dependencies in `pyproject.toml`
   - Install only what you need

3. **Interface-Based Design**
   - All clients inherit from `BaseExchangeClient`
   - All adapters inherit from `BaseFundingAdapter`
   - Enables polymorphism and testing

4. **No Network Calls**
   - Everything stays in-process
   - Zero latency overhead
   - WebSocket streams remain local

---

## When to Move to Microservices

Only consider microservices if:

1. **Unsolvable dependency conflicts** (different Python versions needed)
2. **Team growth** (multiple developers per exchange)
3. **Geographic distribution** (services near exchange APIs)
4. **Extreme scale** (not applicable for solo dev)

For now, shared library is optimal.

---

## Migration Checklist

- [ ] Create `/exchange_clients/` directory structure
- [ ] Write `base.py` with interfaces
- [ ] Create `pyproject.toml` with dependencies
- [ ] Migrate Lighter exchange
  - [ ] Create `client.py`
  - [ ] Create `funding_adapter.py`
  - [ ] Extract `common.py`
  - [ ] Update imports in both services
  - [ ] Test trading bot
  - [ ] Test funding service
  - [ ] Delete old code
- [ ] Migrate GRVT exchange (repeat above)
- [ ] Migrate EdgeX exchange (repeat above)
- [ ] Delete old `/exchanges/` directory
- [ ] Delete old `/collection/adapters/` directory
- [ ] Update all documentation
- [ ] Commit and tag release

---

**Estimated Timeline:** 4-5 weeks  
**Risk Level:** Low (incremental migration, can rollback)  
**Complexity:** Medium (refactoring, not rewriting)

---

END