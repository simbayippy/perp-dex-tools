# Quick Start - Developer Guide

This guide helps you get started developing with the Funding Rate Service.

## 🎯 What's Been Built (40% Complete)

### ✅ Phase 1: Data Foundation (100%)
- **8 Pydantic models** - Complete data structure
- **2 Mappers** - Fast ID↔Name lookups  
- **4 Repositories** - Database operations
- **Key feature**: Open Interest (OI) tracking for low OI farming

### ✅ Phase 2: Data Collection (50%)
- **Base adapter** - Abstract interface for all DEXs
- **Lighter adapter** - Fully functional, tested
- **Still needed**: EdgeX, Paradex, GRVT adapters + orchestrator

## 🚀 Quick Test

```bash
# 1. Install Lighter SDK (one-time)
cd ../lighter-python
pip install -e .

# 2. Install service dependencies
cd ../funding_rate_service
pip install -r requirements.txt

# 3. Test Lighter adapter
python scripts/test_lighter_adapter.py
```

**Expected output:**
```
============================================================
Testing Lighter Adapter
============================================================

📡 Fetching funding rates from Lighter...

✅ Success!
   Latency: 250ms
   Fetched: 20 funding rates

------------------------------------------------------------
Symbol     Funding Rate     Annualized APY 
------------------------------------------------------------
BTC        0.00010000             10.95%
ETH        0.00008000              8.76%
SOL        0.00012000             13.14%
...
```

## 📁 Project Structure

```
funding_rate_service/
├── models/                    # ✅ Pydantic data models
│   ├── dex.py                # DEX metadata, fees
│   ├── symbol.py             # Symbols
│   ├── funding_rate.py       # Funding rates
│   ├── opportunity.py        # Arbitrage opportunities (with OI!)
│   └── filters.py            # Query filters
│
├── core/                      # ✅ Core components
│   └── mappers.py            # ID↔Name fast lookups
│
├── database/
│   ├── connection.py         # ✅ Database connection
│   ├── schema.sql            # ✅ Database schema
│   └── repositories/         # ✅ Data access layer
│       ├── dex_repository.py
│       ├── symbol_repository.py
│       ├── funding_rate_repository.py
│       └── opportunity_repository.py
│
├── collection/               # ✅ Data collection (50% done)
│   ├── base_adapter.py       # ✅ Base adapter interface
│   └── adapters/
│       └── lighter_adapter.py  # ✅ Lighter implementation
│
├── scripts/
│   ├── init_db.py            # ✅ Database initialization
│   ├── seed_dexes.py         # ✅ Seed DEX data
│   └── test_lighter_adapter.py # ✅ Test Lighter
│
└── main.py                   # ✅ FastAPI app skeleton
```

## 🔧 How It All Fits Together

### 1. Data Models (models/)
Define the structure of everything:
```python
from models import ArbitrageOpportunity, OpportunityFilter

# Filter opportunities by OI
filter = OpportunityFilter(
    max_oi_usd=2000000,  # Low OI farming!
    min_profit_percent=0.0001
)
```

### 2. Mappers (core/mappers.py)
Fast lookups between IDs and names:
```python
from core.mappers import dex_mapper, symbol_mapper

# Load at startup
await dex_mapper.load_from_db(db)

# Use throughout codebase
dex_id = dex_mapper.get_id("lighter")  # Returns 1
dex_name = dex_mapper.get_name(1)      # Returns "lighter"
```

### 3. Repositories (database/repositories/)
All database operations:
```python
from database.repositories import FundingRateRepository

repo = FundingRateRepository(db)

# Insert funding rate
await repo.insert(
    dex_id=1,
    symbol_id=1,
    funding_rate=Decimal('0.0001')
)

# Get latest rates
rates = await repo.get_latest_all()
```

### 4. Adapters (collection/adapters/)
Fetch data from DEXs:
```python
from collection.adapters import LighterAdapter

adapter = LighterAdapter()
rates, latency = await adapter.fetch_with_metrics()
# Returns: {"BTC": Decimal("0.0001"), "ETH": ...}
```

## 🎯 Next Steps: Two Options

### Option A: Add More DEX Adapters (Phase 2.3)
Follow the Lighter adapter pattern:

```python
# collection/adapters/edgex_adapter.py
class EdgeXAdapter(BaseDEXAdapter):
    async def fetch_funding_rates(self) -> Dict[str, Decimal]:
        # Implement EdgeX-specific logic
        pass
    
    def normalize_symbol(self, dex_symbol: str) -> str:
        # Convert EdgeX format to standard
        pass
```

### Option B: Build Collection Orchestrator (Phase 2.4) 🌟 **Recommended**

This will tie everything together:

```python
# collection/orchestrator.py
class CollectionOrchestrator:
    async def collect_all_rates(self):
        # 1. Fetch rates from all adapters in parallel
        # 2. Store in database via repositories
        # 3. Update mappers with new symbols
        # 4. Handle partial failures gracefully
```

**Why Option B first?**
- See the system work end-to-end with real data
- Test database integration
- Verify mapper functionality
- Can add more DEX adapters later

## 💡 Key Design Features

### 1. Dynamic Symbol Discovery
No pre-configuration needed! When a new symbol appears:
```python
# Automatically creates symbol if not exists
symbol_id = await symbol_repo.get_or_create("NEWCOIN")
# Updates mapper
symbol_mapper.add(symbol_id, "NEWCOIN")
```

### 2. Open Interest First-Class Support
Perfect for your low OI farming strategy:
```python
# Filter opportunities by OI
opportunities = await opp_repo.find_opportunities(
    OpportunityFilter(
        max_oi_usd=2000000,      # Only show < $2M OI
        min_profit_percent=0.0001
    )
)
```

### 3. Integer IDs + String Names
- **Database**: Uses integer IDs for fast joins
- **API**: Uses string names for readability
- **Mappers**: O(1) translation between them

## 🐛 Common Issues

### "No module named 'lighter'"
```bash
cd ../lighter-python
pip install -e .
```

### Database connection error
```bash
# Make sure PostgreSQL is running
pg_isready

# Initialize database
python scripts/init_db.py
python scripts/seed_dexes.py
```

### Import errors
```bash
# Make sure you're in the right directory
cd funding_rate_service
python scripts/test_lighter_adapter.py
```

## 📚 Documentation

- **ARCHITECTURE.md** - High-level system design
- **funding-rate-service-design.md** - Detailed design doc (2600+ lines!)
- **NEXT_STEPS.md** - Implementation roadmap
- **PROGRESS.md** - Current status and completed work
- **INSTALL.md** - Installation guide

## 🤝 Need Help?

Check these files for implementation details:
- `models/` - See how data is structured
- `collection/adapters/lighter_adapter.py` - Reference implementation
- `database/repositories/` - See how to query database
- `tests/` - Example tests

---

**Current Status**: 40% complete, fully functional Lighter adapter, ready for orchestrator or more adapters!

