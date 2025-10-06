# Implementation Roadmap - What to Build Next

Your funding rate service foundation is ready! Here's the implementation order.

---

## 🎯 Current State

### ✅ Completed (Foundation)
- Project structure
- Configuration management (`config.py`)
- Database connection (`database/connection.py`)
- Logging (`utils/logger.py`)
- Main FastAPI app (`main.py`)
- Database schema (`database/schema.sql`)
- Init scripts (`scripts/init_db.py`, `scripts/seed_dexes.py`)

### ⏭️ Ready to Build
Everything else! Let's build it in logical order.

---

## 📋 Phase 1: Data Models & Repositories (Next!)

### 1.1 Create Pydantic Models

**Priority: HIGH** | **Time: 1 hour**

Files to create:
- `models/__init__.py`
- `models/dex.py` - DEX metadata models
- `models/symbol.py` - Symbol models  
- `models/funding_rate.py` - Funding rate models
- `models/opportunity.py` - Opportunity models
- `models/filters.py` - Filter/query models

**Start with**: `models/dex.py`

Example structure (from design doc):
```python
from pydantic import BaseModel, Field
from typing import Optional
from decimal import Decimal
from datetime import datetime

class DEXFeeStructure(BaseModel):
    maker_fee_percent: Decimal
    taker_fee_percent: Decimal
    has_fee_tiers: bool = False
    # ...

class DEXMetadata(BaseModel):
    id: int
    name: str
    display_name: str
    fee_structure: DEXFeeStructure
    # ...
```

### 1.2 Create Mappers (ID ↔ Name)

**Priority: HIGH** | **Time: 30 min**

File: `core/mappers.py`

These provide fast lookup between:
- DEX ID (1, 2, 3) ↔ DEX Name ("lighter", "edgex")
- Symbol ID (1, 2, 3) ↔ Symbol ("BTC", "ETH")

```python
class DEXMapper:
    """Fast bidirectional mapping"""
    def __init__(self):
        self._id_to_name = {}
        self._name_to_id = {}
    
    async def load_from_db(self, db):
        # Load on startup
        pass
    
    def get_id(self, name: str) -> int:
        return self._name_to_id.get(name)
    
    def get_name(self, id: int) -> str:
        return self._id_to_name.get(id)
```

### 1.3 Create Repositories

**Priority: HIGH** | **Time: 2 hours**

Files to create:
- `database/repositories/__init__.py`
- `database/repositories/dex_repository.py`
- `database/repositories/symbol_repository.py`
- `database/repositories/funding_rate_repository.py`
- `database/repositories/opportunity_repository.py`

**Start with**: `dex_repository.py`

Example:
```python
class DEXRepository:
    def __init__(self, db):
        self.db = db
    
    async def get_all(self) -> List[Dict]:
        return await self.db.fetch_all("SELECT * FROM dexes")
    
    async def get_by_id(self, dex_id: int) -> Optional[Dict]:
        return await self.db.fetch_one("SELECT * FROM dexes WHERE id = :id", {"id": dex_id})
    
    async def get_by_name(self, name: str) -> Optional[Dict]:
        return await self.db.fetch_one("SELECT * FROM dexes WHERE name = :name", {"name": name})
```

---

## 📋 Phase 2: DEX Adapters (Data Collection)

### 2.1 Base Adapter Interface

**Priority: HIGH** | **Time: 1 hour**

File: `collection/base_adapter.py`

```python
from abc import ABC, abstractmethod
from typing import Dict
from decimal import Decimal

class BaseDEXAdapter(ABC):
    """Base class for all DEX adapters"""
    
    @abstractmethod
    async def fetch_funding_rates(self) -> Dict[str, Decimal]:
        """Fetch all funding rates from this DEX"""
        pass
    
    @abstractmethod
    def get_dex_name(self) -> str:
        """Return DEX name"""
        pass
```

### 2.2 Implement First Adapter

**Priority: HIGH** | **Time: 2-3 hours**

File: `collection/adapters/lighter.py`

Start with one DEX (e.g., Lighter) to test the pattern:

```python
class LighterAdapter(BaseDEXAdapter):
    def __init__(self, api_url: str):
        self.api_url = api_url
    
    async def fetch_funding_rates(self) -> Dict[str, Decimal]:
        # Call Lighter API
        # Parse response
        # Return {'BTC': Decimal('0.0001'), ...}
        pass
```

**Tip**: Look at `existing-repos/dex-funding-rate-arb/src/` for reference!

### 2.3 Implement Other Adapters

**Priority: MEDIUM** | **Time: 1-2 hours each**

Files:
- `collection/adapters/edgex.py`
- `collection/adapters/paradex.py`
- `collection/adapters/grvt.py`
- `collection/adapters/hyperliquid.py`

Copy the pattern from Lighter adapter.

### 2.4 Collection Orchestrator

**Priority: HIGH** | **Time: 2 hours**

File: `collection/orchestrator.py`

This coordinates all adapters:
```python
class CollectionOrchestrator:
    def __init__(self, adapters: List[BaseDEXAdapter], db, mappers):
        self.adapters = adapters
    
    async def collect_all_rates(self):
        # Run all adapters in parallel
        # Handle failures gracefully
        # Store results in DB
        pass
```

---

## 📋 Phase 3: Business Logic

### 3.1 Fee Calculator

**Priority: MEDIUM** | **Time: 1 hour**

File: `core/fee_calculator.py`

```python
class FeeCalculator:
    def calculate_opportunity_fees(
        self,
        long_dex: str,
        short_dex: str
    ) -> Decimal:
        # Get fees for both DEXs
        # Total = 4 transactions (open + close both sides)
        pass
```

### 3.2 Opportunity Finder

**Priority: HIGH** | **Time: 2-3 hours**

File: `core/opportunity_finder.py`

```python
class OpportunityFinder:
    def find_opportunities(
        self,
        filters: OpportunityFilter
    ) -> List[ArbitrageOpportunity]:
        # Get latest rates
        # Compare across DEXs
        # Calculate profitability
        # Apply filters (OI, volume, etc.)
        # Return sorted opportunities
        pass
```

---

## 📋 Phase 4: API Endpoints

### 4.1 Health & System Endpoints

**Priority: HIGH** | **Time: 30 min**

File: `api/routes/health.py`

Already have basic `/health`, expand it:
```python
@router.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "database": await check_db(),
        "cache": await check_cache(),
        "dexes": await check_dex_health()
    }
```

### 4.2 Funding Rates Endpoints

**Priority: HIGH** | **Time: 2 hours**

File: `api/routes/funding_rates.py`

```python
@router.get("/funding-rates")
async def get_all_rates():
    # Return all latest rates
    pass

@router.get("/funding-rates/{dex}")
async def get_dex_rates(dex: str):
    # Return rates for specific DEX
    pass

@router.get("/funding-rates/{dex}/{symbol}")
async def get_specific_rate(dex: str, symbol: str):
    # Return specific rate
    pass
```

### 4.3 Opportunities Endpoints

**Priority: HIGH** | **Time: 2 hours**

File: `api/routes/opportunities.py`

```python
@router.get("/opportunities")
async def get_opportunities(
    filters: OpportunityFilter = Depends()
):
    # Find and return opportunities
    pass

@router.get("/opportunities/best")
async def get_best_opportunity(
    filters: OpportunityFilter = Depends()
):
    # Return single best opportunity
    pass
```

### 4.4 DEX Metadata Endpoints

**Priority: MEDIUM** | **Time: 1 hour**

File: `api/routes/dexes.py`

```python
@router.get("/dexes")
async def get_all_dexes():
    # Return all DEX metadata
    pass

@router.get("/dexes/{dex}")
async def get_dex_info(dex: str):
    # Return specific DEX info
    pass
```

---

## 📋 Phase 5: Background Tasks

### 5.1 Rate Collection Task

**Priority: HIGH** | **Time: 1-2 hours**

File: `tasks/collection_task.py`

```python
async def rate_collection_task():
    """Background task that runs every 60 seconds"""
    while True:
        try:
            await orchestrator.collect_all_rates()
            await asyncio.sleep(60)
        except Exception as e:
            logger.error(f"Collection failed: {e}")
```

Start this in `main.py` lifespan.

### 5.2 Opportunity Analysis Task

**Priority: MEDIUM** | **Time: 1 hour**

File: `tasks/opportunity_task.py`

Analyzes rates and updates opportunities table.

---

## 📋 Phase 6: Caching (Optional)

### 6.1 Cache Manager

**Priority: LOW** | **Time: 2 hours**

File: `cache/cache_manager.py`

Two-tier caching (memory + Redis).

---

## 🎯 Suggested Implementation Order

### Week 1: Core Data Layer
1. ✅ Foundation (DONE)
2. Create Pydantic models
3. Create mappers
4. Create repositories
5. Test database operations

### Week 2: Data Collection
1. Base adapter interface
2. Implement Lighter adapter
3. Test Lighter adapter manually
4. Implement other adapters
5. Create collection orchestrator

### Week 3: API & Business Logic
1. Fee calculator
2. Opportunity finder
3. Funding rates API endpoints
4. Opportunities API endpoints
5. Test all endpoints

### Week 4: Background Tasks & Polish
1. Background collection task
2. Background opportunity task
3. Add more tests
4. Performance optimization
5. Deploy to VPS

---

## 🔥 Quick Win: Minimal Viable Product (MVP)

Want to see it work ASAP? Build this minimal version first:

**1-Day MVP**:
1. ✅ Models for DEX, Symbol, FundingRate
2. ✅ DEX Repository
3. ✅ One adapter (Lighter)
4. ✅ Simple endpoint: `GET /funding-rates/lighter`
5. ✅ Manual collection script

This proves the concept end-to-end!

---

## 📝 Testing Strategy

For each phase:
1. **Unit tests**: Test individual functions
2. **Integration tests**: Test with real database
3. **Manual tests**: Use curl/Postman
4. **End-to-end**: Full workflow

Example test:
```python
# tests/test_api/test_funding_rates.py
async def test_get_all_rates(client):
    response = await client.get("/api/v1/funding-rates")
    assert response.status_code == 200
    assert "data" in response.json()
```

---

## 🆘 When You Need Help

1. **Design questions**: Check `docs/tasks/funding-rate-service-design.md`
2. **API reference**: Look at existing repos in `existing-repos/`
3. **Database issues**: Check `database/schema.sql`
4. **Implementation examples**: All in the design doc!

---

## ✅ Current Task: Phase 1.1 - Pydantic Models

**Your next step**: Create `models/dex.py`

Would you like me to:
- A) Start implementing Phase 1.1 (Pydantic models)
- B) Show you how to test current setup first
- C) Start with the MVP approach
- D) Something else

Let me know and we'll continue building! 🚀

