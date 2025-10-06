# Funding Rate Service - System Design Document

## 📋 Table of Contents
1. [Overview](#overview)
2. [Core Requirements](#core-requirements)
3. [System Architecture](#system-architecture)
4. [Database Design](#database-design)
5. [Data Models](#data-models)
6. [API Design](#api-design)
7. [Fee Management](#fee-management)
8. [Caching Strategy](#caching-strategy)
9. [Error Handling & Reliability](#error-handling--reliability)
10. [Performance Considerations](#performance-considerations)
11. [Future Extensibility](#future-extensibility)
12. [Implementation Checklist](#implementation-checklist)

---

## Overview

The Funding Rate Service is a **standalone microservice** designed to collect, aggregate, and analyze funding rate data across multiple decentralized exchanges (DEXs). It provides a comprehensive API for discovering funding rate arbitrage opportunities.

### Design Philosophy
- **Generic & Extensible**: Not tied to specific trading strategies
- **DEX-Agnostic**: Easily add new DEXs without service rewrite
- **Data-Rich**: Provide comprehensive data for various use cases
- **Reliable**: Fault-tolerant with retry mechanisms
- **Performant**: Caching and efficient data retrieval

---

## Core Requirements

### Functional Requirements

#### FR1: Data Collection
- **FR1.1**: Fetch funding rates from multiple DEXs (Lighter, EdgeX, Paradex, GRVT, Hyperliquid, and future DEXs)
- **FR1.2**: Collect funding rates for all available perpetual contracts
- **FR1.3**: Update rates at configurable intervals (default: 60 seconds)
- **FR1.4**: Handle DEX-specific API formats and convert to standard format
- **FR1.5**: Filter by minimum 24h volume to ensure liquidity

#### FR2: Fee Management
- **FR2.1**: Store and manage fee structures for each DEX
- **FR2.2**: Support both maker and taker fees
- **FR2.3**: Handle DEXs with zero fees
- **FR2.4**: Support fee tiers based on volume/VIP levels (future)
- **FR2.5**: Allow fee structure updates without service restart

#### FR3: Opportunity Analysis
- **FR3.1**: Calculate funding rate divergences across all DEX pairs
- **FR3.2**: Compute net profitability after fees
- **FR3.3**: Filter opportunities by minimum divergence threshold
- **FR3.4**: Support filtering by specific DEX(es)
- **FR3.5**: Rank opportunities by multiple criteria (profit, volume, etc.)

#### FR4: Historical Data
- **FR4.1**: Store historical funding rates
- **FR4.2**: Store historical opportunities
- **FR4.3**: Provide historical analysis endpoints
- **FR4.4**: Calculate funding rate volatility
- **FR4.5**: Track average funding rates over time

#### FR5: API Endpoints
- **FR5.1**: Get current funding rates (all DEXs, specific DEX, specific symbol)
- **FR5.2**: Get arbitrage opportunities (all, filtered by DEX, filtered by symbol)
- **FR5.3**: Get historical data (rates, opportunities)
- **FR5.4**: Get DEX metadata (fees, supported symbols, status)
- **FR5.5**: WebSocket streaming for real-time updates
- **FR5.6**: Health check and metrics endpoints

### Non-Functional Requirements

#### NFR1: Performance
- **NFR1.1**: API response time < 100ms (cached data)
- **NFR1.2**: API response time < 500ms (fresh data from DB)
- **NFR1.3**: Support 100+ requests per second
- **NFR1.4**: Parallel DEX data collection (< 5 seconds total)

#### NFR2: Reliability
- **NFR2.1**: 99.9% uptime
- **NFR2.2**: Graceful handling of DEX API failures
- **NFR2.3**: Automatic retry with exponential backoff
- **NFR2.4**: Continue operation even if some DEXs are down

#### NFR3: Scalability
- **NFR3.1**: Support 10+ DEXs
- **NFR3.2**: Support 100+ symbols per DEX
- **NFR3.3**: Horizontal scaling capability
- **NFR3.4**: Database can handle 1M+ historical records

#### NFR4: Maintainability
- **NFR4.1**: Clear separation of concerns
- **NFR4.2**: Easy to add new DEXs (< 100 lines of code)
- **NFR4.3**: Comprehensive logging
- **NFR4.4**: Monitoring and alerting integration

---

## System Architecture

### High-Level Components

```
┌────────────────────────────────────────────────────────────────┐
│                    Funding Rate Service                        │
├────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌───────────────────────────────────────────────────────┐    │
│  │  API Layer (FastAPI)                                  │    │
│  │  • REST Endpoints                                     │    │
│  │  • WebSocket Endpoints                                │    │
│  │  • Request Validation                                 │    │
│  │  • Response Formatting                                │    │
│  └───────────────────────────────────────────────────────┘    │
│                           ↓                                      │
│  ┌───────────────────────────────────────────────────────┐    │
│  │  Business Logic Layer                                 │    │
│  │  • OpportunityFinder                                  │    │
│  │  • FeeCalculator                                      │    │
│  │  • DataAggregator                                     │    │
│  │  • HistoricalAnalyzer                                 │    │
│  └───────────────────────────────────────────────────────┘    │
│                           ↓                                      │
│  ┌───────────────────────────────────────────────────────┐    │
│  │  Data Access Layer                                    │    │
│  │  • RateRepository                                     │    │
│  │  • OpportunityRepository                              │    │
│  │  • FeeRepository                                      │    │
│  │  • CacheManager (Redis/In-Memory)                     │    │
│  └───────────────────────────────────────────────────────┘    │
│                           ↓                                      │
│  ┌───────────────────────────────────────────────────────┐    │
│  │  Data Collection Layer                                │    │
│  │  • CollectionOrchestrator                             │    │
│  │  • DEX Adapters (Lighter, EdgeX, Paradex, ...)       │    │
│  │  • RateLimiter                                        │    │
│  │  • RetryHandler                                       │    │
│  └───────────────────────────────────────────────────────┘    │
│                           ↓                                      │
│  ┌───────────────────────────────────────────────────────┐    │
│  │  Background Tasks                                     │    │
│  │  • Rate Collection Task (every 60s)                   │    │
│  │  • Opportunity Analysis Task (every 60s)              │    │
│  │  • Historical Data Cleanup (daily)                    │    │
│  │  • DEX Health Check (every 5 min)                     │    │
│  └───────────────────────────────────────────────────────┘    │
│                                                                  │
└────────────────────────────────────────────────────────────────┘
              ↓                                    ↓
    ┌──────────────────┐                ┌──────────────────┐
    │   PostgreSQL     │                │   Redis Cache    │
    │   (Historical    │                │   (Real-time     │
    │    Data)         │                │    Data)         │
    └──────────────────┘                └──────────────────┘
              ↓                                    
    ┌──────────────────────────────────────────────────────┐
    │          External DEX APIs                           │
    │  Lighter • EdgeX • Paradex • GRVT • Hyperliquid     │
    └──────────────────────────────────────────────────────┘
```

### Component Descriptions

#### 1. API Layer
- **Technology**: FastAPI (async Python framework)
- **Responsibilities**:
  - Expose REST and WebSocket endpoints
  - Request validation using Pydantic models
  - Authentication/rate limiting (future)
  - Error response formatting
  - OpenAPI documentation generation

#### 2. Business Logic Layer
- **OpportunityFinder**: Analyzes rates and finds arbitrage opportunities
- **FeeCalculator**: Computes trading fees and net profits
- **DataAggregator**: Combines data from multiple sources
- **HistoricalAnalyzer**: Performs time-series analysis

#### 3. Data Access Layer
- **Repositories**: Abstract database operations
- **CacheManager**: Handles Redis/in-memory caching
- **Query Optimization**: Efficient data retrieval

#### 4. Data Collection Layer
- **CollectionOrchestrator**: Coordinates parallel DEX data fetching
- **DEX Adapters**: DEX-specific implementations
- **Error Handling**: Retry logic, fallbacks

#### 5. Background Tasks
- Scheduled jobs for data collection and maintenance
- Run independently of API requests

---

## Key Design Questions & Answers

### Q1: Can I run PostgreSQL directly on my VPS?

**Answer: Yes, absolutely!** This is actually the recommended approach for initial deployment.

**VPS All-in-One Setup:**
- Run PostgreSQL directly on VPS (localhost:5432)
- Run Redis on VPS (optional - can use memory-only cache)
- Run FastAPI service on VPS
- Use Nginx as reverse proxy for SSL/domain

**Benefits:**
- ✅ **Simple**: Everything on one server
- ✅ **Cost-effective**: Single VPS ($10-40/month)
- ✅ **Easy management**: No container orchestration needed
- ✅ **Good performance**: No network latency between components
- ✅ **Perfect for starting**: Easy to upgrade to distributed setup later

**Installation on VPS:**
```bash
# Install PostgreSQL + TimescaleDB
sudo apt update
sudo apt install postgresql-15 postgresql-contrib
sudo sh -c "echo 'deb https://packagecloud.io/timescale/timescaledb/ubuntu/ $(lsb_release -c -s) main' > /etc/apt/sources.list.d/timescaledb.list"
wget --quiet -O - https://packagecloud.io/timescale/timescaledb/gpgkey | sudo apt-key add -
sudo apt update
sudo apt install timescaledb-2-postgresql-15

# Install Redis (optional)
sudo apt install redis-server

# Configure PostgreSQL
sudo -u postgres psql -c "CREATE DATABASE funding_rates;"
sudo -u postgres psql -c "CREATE USER funding_user WITH PASSWORD 'your_password';"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE funding_rates TO funding_user;"

# Enable TimescaleDB extension
sudo -u postgres psql -d funding_rates -c "CREATE EXTENSION IF NOT EXISTS timescaledb;"
```

### Q2: Should DEXs be stored as numbers (enums) or strings?

**Answer: Use BOTH - integer IDs internally, string names in API** (this is already in the design!)

**Database (Internal):**
```sql
-- DEXs have integer IDs for performance
CREATE TABLE dexes (
    id SERIAL PRIMARY KEY,      -- Integer: 1, 2, 3, etc.
    name VARCHAR(50) UNIQUE,    -- String: 'lighter', 'edgex', etc.
    ...
);

-- All foreign keys use integer IDs
CREATE TABLE opportunities (
    long_dex_id INTEGER REFERENCES dexes(id),  -- Integer FK
    short_dex_id INTEGER REFERENCES dexes(id), -- Integer FK
    ...
);
```

**API (External):**
```json
// API uses string names for clarity
{
  "symbol": "BTC",
  "long_dex": "lighter",   // String, not integer
  "short_dex": "edgex",    // String, not integer
  "divergence": 0.0004
}
```

**Why this approach?**
- ✅ **DB Performance**: Integer joins are faster than string joins
- ✅ **API Usability**: Strings are human-readable ("lighter" vs "1")
- ✅ **Easy mapping**: Simple lookup table in code
- ✅ **Flexibility**: Can change display names without changing IDs

**Implementation:**
```python
class DEXMapper:
    """Fast bidirectional mapping between IDs and names"""
    
    def __init__(self):
        self._id_to_name = {}  # {1: 'lighter', 2: 'edgex', ...}
        self._name_to_id = {}  # {'lighter': 1, 'edgex': 2, ...}
    
    async def load_from_db(self, db):
        """Load mapping from database on startup"""
        dexes = await db.fetch_all("SELECT id, name FROM dexes")
        self._id_to_name = {row['id']: row['name'] for row in dexes}
        self._name_to_id = {row['name']: row['id'] for row in dexes}
    
    def get_id(self, name: str) -> int:
        """'lighter' -> 1"""
        return self._name_to_id.get(name)
    
    def get_name(self, id: int) -> str:
        """1 -> 'lighter'"""
        return self._id_to_name.get(id)

# Global mapper instance
dex_mapper = DEXMapper()

# Usage in API
@app.get("/api/v1/opportunities")
async def get_opportunities():
    # Query DB using integer IDs (fast)
    rows = await db.fetch_all("""
        SELECT symbol_id, long_dex_id, short_dex_id, ...
        FROM opportunities
    """)
    
    # Convert to API response with string names
    opportunities = []
    for row in rows:
        opportunities.append({
            'symbol': symbol_mapper.get_name(row['symbol_id']),
            'long_dex': dex_mapper.get_name(row['long_dex_id']),  # ID -> name
            'short_dex': dex_mapper.get_name(row['short_dex_id']),
            ...
        })
    
    return opportunities
```

### Q3: How do we handle new crypto pairs dynamically?

**Answer: Automatic discovery - no pre-mapping needed!**

**How it works:**
1. **Data Collection**: When fetching from DEX APIs, discover new symbols automatically
2. **Auto-Insert**: If symbol doesn't exist in DB, create it
3. **String-based**: Symbols are stored as strings (no enum), so infinitely extensible

**Implementation:**
```python
class CollectionOrchestrator:
    """Handles data collection with automatic symbol discovery"""
    
    async def process_funding_rates(
        self,
        dex_name: str,
        raw_rates: Dict[str, Decimal]  # {'BTC-PERP': 0.0001, 'PEPE-PERP': 0.0005, ...}
    ):
        """Process rates and auto-create symbols if needed"""
        
        dex_id = dex_mapper.get_id(dex_name)
        
        for dex_symbol_format, rate in raw_rates.items():
            # Normalize symbol (DEX format -> standard format)
            normalized_symbol = self._normalize_symbol(dex_symbol_format)
            # e.g., 'BTC-PERP' -> 'BTC', 'PERP_PEPE_USDC' -> 'PEPE'
            
            # Get or create symbol ID
            symbol_id = await self._get_or_create_symbol(normalized_symbol)
            
            # Get or create dex_symbol mapping
            await self._ensure_dex_symbol(dex_id, symbol_id, dex_symbol_format)
            
            # Insert funding rate
            await db.execute("""
                INSERT INTO funding_rates (time, dex_id, symbol_id, funding_rate, ...)
                VALUES (NOW(), $1, $2, $3, ...)
            """, dex_id, symbol_id, rate)
    
    async def _get_or_create_symbol(self, symbol: str) -> int:
        """Get symbol ID, creating if doesn't exist"""
        
        # Try to get existing
        result = await db.fetch_one(
            "SELECT id FROM symbols WHERE symbol = $1",
            symbol
        )
        
        if result:
            return result['id']
        
        # Create new symbol
        new_id = await db.fetch_val("""
            INSERT INTO symbols (symbol, first_seen)
            VALUES ($1, NOW())
            ON CONFLICT (symbol) DO UPDATE SET symbol = EXCLUDED.symbol
            RETURNING id
        """, symbol)
        
        logger.info(f"New symbol discovered: {symbol} (ID: {new_id})")
        
        # Update in-memory mapper
        symbol_mapper.add(new_id, symbol)
        
        return new_id
    
    def _normalize_symbol(self, dex_format: str) -> str:
        """Convert DEX-specific format to standard symbol"""
        # Remove common suffixes
        normalized = dex_format.upper()
        normalized = normalized.replace('-PERP', '')
        normalized = normalized.replace('_USDC', '')
        normalized = normalized.replace('_USDT', '')
        normalized = normalized.replace('PERP_', '')
        return normalized
```

**Example flow:**
```
1. Lighter API returns: {'BTC-PERP': 0.0001, 'PEPE-PERP': 0.0005}
2. Normalize: 'BTC-PERP' -> 'BTC', 'PEPE-PERP' -> 'PEPE'
3. Check DB:
   - 'BTC' exists (id=1) ✅
   - 'PEPE' doesn't exist ❌
4. Create 'PEPE' (id=47) ✅
5. Update symbol_mapper: {47: 'PEPE'}
6. Insert funding rates for both
```

### Q4: How is Open Interest (OI) integrated?

**Answer: OI is a first-class citizen in the design!**

**Database Storage:**
```sql
-- OI tracked in multiple places:

-- 1. Current OI in dex_symbols (real-time)
CREATE TABLE dex_symbols (
    ...
    open_interest_usd NUMERIC(20, 2),   -- Current OI in USD
    open_interest_base NUMERIC(20, 8),  -- Current OI in base asset
    ...
);

-- 2. Historical OI snapshots in funding_rates
CREATE TABLE funding_rates (
    ...
    open_interest_usd NUMERIC(20, 2),  -- OI at this time
    volume_24h NUMERIC(20, 2),         -- Volume at this time
    ...
);

-- 3. OI included in opportunities for strategy filtering
CREATE TABLE opportunities (
    ...
    long_oi_usd NUMERIC(20, 2),      -- OI on long DEX
    short_oi_usd NUMERIC(20, 2),     -- OI on short DEX
    min_oi_usd NUMERIC(20, 2),       -- Min of the two (for low OI filtering!)
    max_oi_usd NUMERIC(20, 2),       -- Max of the two
    oi_ratio NUMERIC(10, 4),         -- long_oi / short_oi
    ...
);
```

**API Filtering:**
```python
# GET /api/v1/opportunities with OI filters
@app.get("/api/v1/opportunities")
async def get_opportunities(
    # Standard filters
    symbol: Optional[str] = None,
    min_profit: Decimal = Decimal('0'),
    
    # OI FILTERS for strategy selection!
    max_oi_usd: Optional[Decimal] = None,  # For LOW OI farming
    min_oi_usd: Optional[Decimal] = None,  # For HIGH OI (liquidity)
    oi_imbalance: Optional[str] = None,    # 'long_heavy', 'short_heavy'
):
    """
    Find opportunities with OI filtering
    
    Example use cases:
    1. Low OI farming: ?max_oi_usd=1000000 (< $1M OI)
    2. High liquidity: ?min_oi_usd=10000000 (> $10M OI)
    3. OI imbalance: ?oi_imbalance=long_heavy (more longs than shorts)
    """
    
    query = """
        SELECT 
            s.symbol,
            d1.name as long_dex,
            d2.name as short_dex,
            o.long_rate,
            o.short_rate,
            o.divergence,
            o.net_profit_percent,
            o.long_oi_usd,
            o.short_oi_usd,
            o.min_oi_usd,
            o.oi_ratio
        FROM opportunities o
        JOIN symbols s ON o.symbol_id = s.id
        JOIN dexes d1 ON o.long_dex_id = d1.id
        JOIN dexes d2 ON o.short_dex_id = d2.id
        WHERE o.net_profit_percent >= $1
    """
    
    params = [min_profit]
    
    # Add OI filters
    if max_oi_usd:
        query += " AND o.min_oi_usd <= $2"
        params.append(max_oi_usd)
    
    if min_oi_usd:
        query += f" AND o.min_oi_usd >= ${len(params) + 1}"
        params.append(min_oi_usd)
    
    if oi_imbalance == 'long_heavy':
        query += f" AND o.oi_ratio > 1.2"  # 20% more long OI
    elif oi_imbalance == 'short_heavy':
        query += f" AND o.oi_ratio < 0.8"  # 20% more short OI
    
    query += " ORDER BY o.net_profit_percent DESC LIMIT 10"
    
    results = await db.fetch_all(query, *params)
    return results
```

**Example API Calls:**

```bash
# 1. Find low OI opportunities (for farming points)
GET /api/v1/opportunities?max_oi_usd=2000000&min_profit=0.0001
# Returns opportunities where both DEXs have < $2M OI

# 2. Find opportunities with OI imbalance
GET /api/v1/opportunities?oi_imbalance=long_heavy
# Returns opportunities where long side has more OI (potential squeeze)

# 3. Find specific DEX with low OI
GET /api/v1/opportunities?include_dexes=lighter,edgex&max_oi_usd=1000000
# Returns Lighter/EdgeX opportunities with low OI
```

**Response includes OI data:**
```json
{
  "opportunities": [
    {
      "symbol": "PEPE",
      "long_dex": "lighter",
      "short_dex": "edgex",
      "divergence": 0.0005,
      "net_profit_percent": 0.0003,
      "annualized_apy": 32.85,
      
      "long_oi_usd": 850000,      // $850k OI on Lighter
      "short_oi_usd": 1200000,    // $1.2M OI on EdgeX
      "min_oi_usd": 850000,       // Min is $850k (good for low OI farming!)
      "max_oi_usd": 1200000,
      "oi_ratio": 0.708,          // 70% ratio (slightly short-heavy)
      "oi_imbalance": "short_heavy",
      
      "long_volume_24h": 5000000,
      "short_volume_24h": 8000000
    }
  ]
}
```

**Strategy Use Case:**
Your funding arb strategy can now:
1. **Find low OI pairs** for points farming (less competition)
2. **Avoid high OI pairs** if you want to avoid crowded trades
3. **Detect OI imbalances** for potential liquidation cascades
4. **Track OI changes** over time to see market trends

---

## Database Design

### Why PostgreSQL?

**Chosen Database**: PostgreSQL

**Rationale**:
1. **Relational Data**: Clear relationships between DEXs, symbols, rates
2. **Time-Series Support**: Excellent for historical data with TimescaleDB extension
3. **JSON Support**: Can store metadata flexibly
4. **ACID Compliance**: Data integrity for financial data
5. **Performance**: Handles complex queries efficiently
6. **Mature Ecosystem**: Well-supported, reliable

**Alternative Considered**: InfluxDB (pure time-series DB)
- **Pros**: Optimized for time-series
- **Cons**: Less flexible for relational queries, smaller ecosystem

**Decision**: Start with PostgreSQL + TimescaleDB extension for time-series optimization

### Database Schema

```sql
-- ============================================================================
-- CORE TABLES
-- ============================================================================

-- Table: dexes
-- Stores DEX metadata
-- Note: Uses integer ID internally for performance, but API uses string names for usability
CREATE TABLE dexes (
    id SERIAL PRIMARY KEY,  -- Integer ID for DB performance
    name VARCHAR(50) UNIQUE NOT NULL,  -- String name for API (e.g., 'lighter', 'edgex')
    display_name VARCHAR(100) NOT NULL,  -- Human-readable (e.g., 'Lighter Network')
    api_base_url VARCHAR(255),
    websocket_url VARCHAR(255),
    is_active BOOLEAN DEFAULT TRUE,
    supports_websocket BOOLEAN DEFAULT FALSE,
    
    -- Fee structure
    maker_fee_percent NUMERIC(10, 6) NOT NULL,  -- e.g., 0.0002 for 0.02%
    taker_fee_percent NUMERIC(10, 6) NOT NULL,
    has_fee_tiers BOOLEAN DEFAULT FALSE,
    fee_metadata JSONB,  -- For complex fee structures
    
    -- Operational metadata
    collection_interval_seconds INTEGER DEFAULT 60,
    rate_limit_per_minute INTEGER DEFAULT 60,
    last_successful_fetch TIMESTAMP,
    last_error TIMESTAMP,
    consecutive_errors INTEGER DEFAULT 0,
    
    -- Audit fields
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    -- Indexes
    INDEX idx_dexes_name (name),
    INDEX idx_dexes_is_active (is_active)
);

-- Create enum-like mapping for fast lookups (optional optimization)
-- This gives you the best of both worlds: integer IDs in DB, string names in API
CREATE INDEX idx_dexes_id_name ON dexes(id, name);  -- Composite index for quick mapping

-- Table: symbols
-- Stores trading symbols across all DEXs
-- Dynamically grows as new pairs are discovered on DEXs
-- No need for pre-mapping - symbols are added automatically during collection
CREATE TABLE symbols (
    id SERIAL PRIMARY KEY,  -- Integer ID for DB performance
    symbol VARCHAR(20) UNIQUE NOT NULL,  -- Normalized string: e.g., 'BTC', 'ETH', 'PEPE'
    display_name VARCHAR(50),  -- e.g., 'Bitcoin', 'Ethereum'
    category VARCHAR(20),  -- e.g., 'crypto', 'forex'
    
    -- Metadata
    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_active BOOLEAN DEFAULT TRUE,  -- Can be set to false if symbol becomes obsolete
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    INDEX idx_symbols_symbol (symbol),
    INDEX idx_symbols_active (is_active)
);

-- Create enum-like mapping for fast lookups
CREATE INDEX idx_symbols_id_symbol ON symbols(id, symbol);

-- Table: dex_symbols
-- Maps which symbols are available on which DEXs
-- This is the bridge table that enables dynamic symbol addition
CREATE TABLE dex_symbols (
    id SERIAL PRIMARY KEY,
    dex_id INTEGER NOT NULL REFERENCES dexes(id) ON DELETE CASCADE,
    symbol_id INTEGER NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
    
    -- DEX-specific symbol format
    dex_symbol_format VARCHAR(50) NOT NULL,  -- e.g., 'BTC-PERP', 'PERP_BTC_USDC'
    
    -- Market metadata
    is_active BOOLEAN DEFAULT TRUE,
    min_order_size NUMERIC(20, 8),
    max_order_size NUMERIC(20, 8),
    tick_size NUMERIC(20, 8),
    
    -- Volume and liquidity metrics
    volume_24h NUMERIC(20, 2),  -- USD value of 24h volume
    volume_24h_base NUMERIC(20, 8),  -- Volume in base asset (e.g., BTC)
    
    -- OPEN INTEREST TRACKING (Key for strategy!)
    open_interest_usd NUMERIC(20, 2),  -- Open interest in USD
    open_interest_base NUMERIC(20, 8),  -- Open interest in base asset
    
    -- Additional liquidity metrics
    best_bid NUMERIC(20, 8),
    best_ask NUMERIC(20, 8),
    spread_bps INTEGER,  -- Spread in basis points (0.01% = 1 bps)
    
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE(dex_id, symbol_id),
    INDEX idx_dex_symbols_dex (dex_id),
    INDEX idx_dex_symbols_symbol (symbol_id),
    INDEX idx_dex_symbols_active (is_active),
    INDEX idx_dex_symbols_volume (volume_24h DESC),
    INDEX idx_dex_symbols_oi (open_interest_usd DESC)  -- For low OI filtering
);

-- ============================================================================
-- TIME-SERIES TABLES (Use TimescaleDB hypertables)
-- ============================================================================

-- Table: funding_rates
-- Stores historical funding rates with market context
CREATE TABLE funding_rates (
    time TIMESTAMP NOT NULL,
    dex_id INTEGER NOT NULL REFERENCES dexes(id),
    symbol_id INTEGER NOT NULL REFERENCES symbols(id),
    
    funding_rate NUMERIC(15, 10) NOT NULL,  -- Can be negative
    
    -- Additional context
    next_funding_time TIMESTAMP,
    predicted_rate NUMERIC(15, 10),
    index_price NUMERIC(20, 8),
    mark_price NUMERIC(20, 8),
    
    -- Snapshot of market conditions at this time
    open_interest_usd NUMERIC(20, 2),  -- OI snapshot
    volume_24h NUMERIC(20, 2),  -- Volume snapshot
    
    -- Metadata
    collection_latency_ms INTEGER,  -- How long it took to fetch
    
    PRIMARY KEY (time, dex_id, symbol_id)
);

-- Convert to TimescaleDB hypertable for time-series optimization
SELECT create_hypertable('funding_rates', 'time');

-- Indexes for common queries
CREATE INDEX idx_funding_rates_dex_symbol_time 
    ON funding_rates (dex_id, symbol_id, time DESC);
CREATE INDEX idx_funding_rates_symbol_time 
    ON funding_rates (symbol_id, time DESC);

-- Retention policy: Keep detailed data for 30 days, then aggregate
SELECT add_retention_policy('funding_rates', INTERVAL '30 days');

-- ============================================================================
-- OPPORTUNITY TABLES
-- ============================================================================

-- Table: opportunities
-- Stores calculated arbitrage opportunities with market context
CREATE TABLE opportunities (
    id SERIAL PRIMARY KEY,
    
    -- Opportunity details
    symbol_id INTEGER NOT NULL REFERENCES symbols(id),
    long_dex_id INTEGER NOT NULL REFERENCES dexes(id),
    short_dex_id INTEGER NOT NULL REFERENCES dexes(id),
    
    -- Rates at time of opportunity
    long_rate NUMERIC(15, 10) NOT NULL,
    short_rate NUMERIC(15, 10) NOT NULL,
    divergence NUMERIC(15, 10) NOT NULL,  -- short_rate - long_rate
    
    -- Profitability
    estimated_fees NUMERIC(15, 10) NOT NULL,
    net_profit_percent NUMERIC(15, 10) NOT NULL,  -- After fees
    annualized_apy NUMERIC(10, 4),  -- If held for full funding period
    
    -- Market conditions (volume)
    long_volume_24h NUMERIC(20, 2),
    short_volume_24h NUMERIC(20, 2),
    min_volume_24h NUMERIC(20, 2),  -- Min of the two
    
    -- Market conditions (OPEN INTEREST - key for low OI strategies!)
    long_oi_usd NUMERIC(20, 2),
    short_oi_usd NUMERIC(20, 2),
    min_oi_usd NUMERIC(20, 2),  -- Min of the two
    max_oi_usd NUMERIC(20, 2),  -- Max of the two
    oi_ratio NUMERIC(10, 4),  -- long_oi / short_oi (for imbalance detection)
    
    -- Liquidity metrics
    long_spread_bps INTEGER,
    short_spread_bps INTEGER,
    
    -- Timestamps
    discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    valid_until TIMESTAMP,  -- When to recheck
    
    -- Metadata
    metadata JSONB,  -- For extensibility
    
    INDEX idx_opportunities_symbol (symbol_id),
    INDEX idx_opportunities_profit (net_profit_percent DESC),
    INDEX idx_opportunities_discovered (discovered_at DESC),
    INDEX idx_opportunities_long_dex (long_dex_id),
    INDEX idx_opportunities_short_dex (short_dex_id),
    INDEX idx_opportunities_min_oi (min_oi_usd ASC),  -- For low OI filtering
    INDEX idx_opportunities_max_oi (max_oi_usd DESC),  -- For high OI filtering
    INDEX idx_opportunities_composite (net_profit_percent DESC, min_oi_usd ASC)  -- Composite for profit + low OI
);

-- ============================================================================
-- AGGREGATED TABLES (For Performance)
-- ============================================================================

-- Table: latest_funding_rates
-- Materialized view of most recent funding rates (for fast API responses)
CREATE TABLE latest_funding_rates (
    dex_id INTEGER NOT NULL REFERENCES dexes(id),
    symbol_id INTEGER NOT NULL REFERENCES symbols(id),
    
    funding_rate NUMERIC(15, 10) NOT NULL,
    next_funding_time TIMESTAMP,
    
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    PRIMARY KEY (dex_id, symbol_id),
    INDEX idx_latest_rates_dex (dex_id),
    INDEX idx_latest_rates_symbol (symbol_id)
);

-- ============================================================================
-- AUDIT & MONITORING TABLES
-- ============================================================================

-- Table: collection_logs
-- Tracks data collection runs
CREATE TABLE collection_logs (
    id SERIAL PRIMARY KEY,
    dex_id INTEGER REFERENCES dexes(id),
    
    started_at TIMESTAMP NOT NULL,
    completed_at TIMESTAMP,
    status VARCHAR(20) NOT NULL,  -- 'success', 'partial', 'failed'
    
    symbols_fetched INTEGER DEFAULT 0,
    symbols_failed INTEGER DEFAULT 0,
    
    error_message TEXT,
    
    INDEX idx_collection_logs_dex_time (dex_id, started_at DESC)
);

-- Table: api_metrics
-- Tracks API usage (optional, for monitoring)
CREATE TABLE api_metrics (
    time TIMESTAMP NOT NULL,
    endpoint VARCHAR(100) NOT NULL,
    method VARCHAR(10) NOT NULL,
    status_code INTEGER NOT NULL,
    response_time_ms INTEGER,
    
    PRIMARY KEY (time, endpoint, method)
);

SELECT create_hypertable('api_metrics', 'time');
SELECT add_retention_policy('api_metrics', INTERVAL '7 days');
```

### Database Initialization Seed Data

```sql
-- Seed DEX data
INSERT INTO dexes (name, display_name, api_base_url, maker_fee_percent, taker_fee_percent) VALUES
    ('lighter', 'Lighter Network', 'https://api.lighter.xyz', 0.0002, 0.0005),
    ('edgex', 'EdgeX', 'https://api.edgex.exchange', 0.0000, 0.0000),  -- Zero fees
    ('paradex', 'Paradex', 'https://api.paradex.trade', 0.0002, 0.0005),
    ('grvt', 'GRVT', 'https://api.grvt.io', 0.0001, 0.0003),
    ('hyperliquid', 'Hyperliquid', 'https://api.hyperliquid.xyz', 0.00025, 0.00075);

-- Seed common symbols
INSERT INTO symbols (symbol, display_name, category) VALUES
    ('BTC', 'Bitcoin', 'crypto'),
    ('ETH', 'Ethereum', 'crypto'),
    ('SOL', 'Solana', 'crypto'),
    ('ARB', 'Arbitrum', 'crypto'),
    ('AVAX', 'Avalanche', 'crypto');
```

---

## Data Models

### Python Data Models (Pydantic)

```python
from pydantic import BaseModel, Field, validator
from typing import Optional, Dict, Any, List
from decimal import Decimal
from datetime import datetime
from enum import Enum

# ============================================================================
# ENUMS
# ============================================================================

class CollectionStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"

class FeeType(str, Enum):
    MAKER = "maker"
    TAKER = "taker"

# ============================================================================
# DEX MODELS
# ============================================================================

class DEXFeeStructure(BaseModel):
    """Fee structure for a DEX"""
    maker_fee_percent: Decimal = Field(..., description="Maker fee as decimal (0.0002 = 0.02%)")
    taker_fee_percent: Decimal = Field(..., description="Taker fee as decimal")
    has_fee_tiers: bool = Field(default=False)
    fee_tiers: Optional[List[Dict[str, Any]]] = None
    
    def get_fee(self, fee_type: FeeType, volume_30d: Decimal = Decimal('0')) -> Decimal:
        """Get fee based on type and volume (for tiered fees)"""
        if not self.has_fee_tiers:
            return self.maker_fee_percent if fee_type == FeeType.MAKER else self.taker_fee_percent
        
        # TODO: Implement tiered fee logic
        return self.maker_fee_percent if fee_type == FeeType.MAKER else self.taker_fee_percent

class DEXMetadata(BaseModel):
    """Metadata about a DEX"""
    id: int
    name: str = Field(..., description="Internal name (lowercase, no spaces)")
    display_name: str = Field(..., description="Display name")
    api_base_url: Optional[str] = None
    websocket_url: Optional[str] = None
    is_active: bool = True
    supports_websocket: bool = False
    
    fee_structure: DEXFeeStructure
    
    collection_interval_seconds: int = 60
    rate_limit_per_minute: int = 60
    
    last_successful_fetch: Optional[datetime] = None
    last_error: Optional[datetime] = None
    consecutive_errors: int = 0
    
    created_at: datetime
    updated_at: datetime
    
    class Config:
        orm_mode = True

class DEXHealth(BaseModel):
    """Health status of a DEX"""
    dex_name: str
    is_healthy: bool
    last_successful_fetch: Optional[datetime]
    consecutive_errors: int
    error_rate_percent: float
    avg_collection_latency_ms: float

# ============================================================================
# SYMBOL MODELS
# ============================================================================

class Symbol(BaseModel):
    """Trading symbol"""
    id: int
    symbol: str = Field(..., description="Normalized symbol (e.g., BTC, ETH)")
    display_name: Optional[str] = None
    category: Optional[str] = None
    
    class Config:
        orm_mode = True

class DEXSymbol(BaseModel):
    """Symbol availability on a specific DEX"""
    id: int
    dex_id: int
    symbol_id: int
    dex_symbol_format: str = Field(..., description="DEX-specific format")
    
    is_active: bool = True
    min_order_size: Optional[Decimal] = None
    max_order_size: Optional[Decimal] = None
    tick_size: Optional[Decimal] = None
    
    volume_24h: Optional[Decimal] = None
    open_interest: Optional[Decimal] = None
    
    last_updated: datetime
    
    class Config:
        orm_mode = True

# ============================================================================
# FUNDING RATE MODELS
# ============================================================================

class FundingRate(BaseModel):
    """Funding rate at a specific time"""
    time: datetime
    dex_id: int
    symbol_id: int
    funding_rate: Decimal
    
    next_funding_time: Optional[datetime] = None
    predicted_rate: Optional[Decimal] = None
    index_price: Optional[Decimal] = None
    mark_price: Optional[Decimal] = None
    
    collection_latency_ms: Optional[int] = None
    
    class Config:
        orm_mode = True

class FundingRateResponse(BaseModel):
    """API response for funding rate"""
    dex_name: str
    symbol: str
    funding_rate: Decimal
    next_funding_time: Optional[datetime] = None
    timestamp: datetime
    
    # Additional context
    annualized_rate: Optional[Decimal] = None  # funding_rate * 365 * 3 (assuming 8h periods)
    
    @validator('annualized_rate', always=True)
    def calculate_annualized(cls, v, values):
        if v is None and 'funding_rate' in values:
            # Assuming 8-hour funding periods (3 per day)
            return values['funding_rate'] * Decimal('365') * Decimal('3')
        return v

class LatestFundingRates(BaseModel):
    """Latest funding rates across all DEXs for a symbol"""
    symbol: str
    rates: Dict[str, FundingRateResponse]  # dex_name -> rate
    updated_at: datetime
    
class AllLatestFundingRates(BaseModel):
    """Latest funding rates for all symbols across all DEXs"""
    symbols: Dict[str, Dict[str, Decimal]]  # symbol -> dex_name -> rate
    dex_metadata: Dict[str, DEXMetadata]
    updated_at: datetime

# ============================================================================
# OPPORTUNITY MODELS
# ============================================================================

class ArbitrageOpportunity(BaseModel):
    """Funding rate arbitrage opportunity with comprehensive market data"""
    id: Optional[int] = None
    
    # Core opportunity data
    symbol: str
    long_dex: str = Field(..., description="DEX to go long on (lower funding rate)")
    short_dex: str = Field(..., description="DEX to go short on (higher funding rate)")
    
    # Funding rates
    long_rate: Decimal
    short_rate: Decimal
    divergence: Decimal = Field(..., description="short_rate - long_rate")
    
    # Profitability
    estimated_fees: Decimal
    net_profit_percent: Decimal = Field(..., description="Profit after fees")
    annualized_apy: Optional[Decimal] = None
    
    # Volume metrics
    long_volume_24h: Optional[Decimal] = None
    short_volume_24h: Optional[Decimal] = None
    min_volume_24h: Optional[Decimal] = None
    
    # OPEN INTEREST METRICS (key for strategy selection!)
    long_oi_usd: Optional[Decimal] = Field(None, description="Long DEX open interest in USD")
    short_oi_usd: Optional[Decimal] = Field(None, description="Short DEX open interest in USD")
    min_oi_usd: Optional[Decimal] = Field(None, description="Minimum OI (for low OI detection)")
    max_oi_usd: Optional[Decimal] = Field(None, description="Maximum OI")
    oi_ratio: Optional[Decimal] = Field(None, description="OI ratio (long/short)")
    oi_imbalance: Optional[str] = Field(None, description="'long_heavy', 'short_heavy', or 'balanced'")
    
    # Liquidity metrics
    long_spread_bps: Optional[int] = Field(None, description="Long DEX spread in basis points")
    short_spread_bps: Optional[int] = Field(None, description="Short DEX spread in basis points")
    avg_spread_bps: Optional[int] = Field(None, description="Average spread")
    
    # Timestamps
    discovered_at: datetime
    valid_until: Optional[datetime] = None
    
    # Additional metadata
    metadata: Optional[Dict[str, Any]] = None
    
    @validator('divergence', always=True)
    def calculate_divergence(cls, v, values):
        if v is None and 'short_rate' in values and 'long_rate' in values:
            return values['short_rate'] - values['long_rate']
        return v
    
    @validator('annualized_apy', always=True)
    def calculate_apy(cls, v, values):
        if v is None and 'net_profit_percent' in values:
            # Assuming 8-hour funding periods (3 per day)
            return values['net_profit_percent'] * Decimal('365') * Decimal('3') * Decimal('100')
        return v
    
    class Config:
        orm_mode = True

class OpportunityFilter(BaseModel):
    """Filter parameters for opportunity queries"""
    symbol: Optional[str] = None
    long_dex: Optional[str] = None
    short_dex: Optional[str] = None
    include_dexes: Optional[List[str]] = Field(default=None, description="Only include these DEXs")
    exclude_dexes: Optional[List[str]] = Field(default=None, description="Exclude these DEXs")
    
    # Profitability filters
    min_divergence: Optional[Decimal] = Field(default=Decimal('0.0001'), description="Minimum divergence")
    min_profit_percent: Optional[Decimal] = Field(default=Decimal('0'), description="Minimum net profit")
    
    # Volume filters
    min_volume_24h: Optional[Decimal] = Field(default=Decimal('100000'), description="Minimum 24h volume")
    max_volume_24h: Optional[Decimal] = Field(default=None, description="Maximum 24h volume (for niche pairs)")
    
    # OPEN INTEREST FILTERS (for low OI farming strategies!)
    min_oi_usd: Optional[Decimal] = Field(default=None, description="Minimum open interest")
    max_oi_usd: Optional[Decimal] = Field(default=None, description="Maximum open interest (for low OI farming)")
    oi_ratio_min: Optional[Decimal] = Field(default=None, description="Min OI ratio (long/short)")
    oi_ratio_max: Optional[Decimal] = Field(default=None, description="Max OI ratio")
    
    # Liquidity filters
    max_spread_bps: Optional[int] = Field(default=None, description="Maximum spread in basis points")
    
    limit: int = Field(default=10, ge=1, le=100)
    sort_by: str = Field(default="net_profit_percent", description="Sort field")
    sort_desc: bool = Field(default=True, description="Sort descending")

class OpportunityResponse(BaseModel):
    """API response for opportunities"""
    opportunities: List[ArbitrageOpportunity]
    total_count: int
    filters_applied: OpportunityFilter
    generated_at: datetime

# ============================================================================
# HISTORICAL ANALYSIS MODELS
# ============================================================================

class FundingRateHistory(BaseModel):
    """Historical funding rates for a symbol on a DEX"""
    dex_name: str
    symbol: str
    data_points: List[Dict[str, Any]]  # [{time, rate}, ...]
    
    # Statistics
    avg_rate: Decimal
    median_rate: Decimal
    std_dev: Decimal
    min_rate: Decimal
    max_rate: Decimal
    
    period_start: datetime
    period_end: datetime

class FundingRateStats(BaseModel):
    """Statistical analysis of funding rates"""
    symbol: str
    dex_name: Optional[str] = None  # None for all DEXs
    
    # Time period
    period_days: int
    period_start: datetime
    period_end: datetime
    
    # Basic stats
    avg_funding_rate: Decimal
    median_funding_rate: Decimal
    std_dev: Decimal
    volatility: Decimal  # Standard deviation / mean
    
    # Distribution
    min_rate: Decimal
    max_rate: Decimal
    percentile_25: Decimal
    percentile_75: Decimal
    
    # Profitability metrics
    avg_annualized_apy: Decimal
    positive_rate_frequency: float  # % of time rate was positive

# ============================================================================
# SYSTEM MODELS
# ============================================================================

class ServiceHealth(BaseModel):
    """Overall service health"""
    status: str  # "healthy", "degraded", "unhealthy"
    timestamp: datetime
    
    dex_health: List[DEXHealth]
    
    # System metrics
    uptime_seconds: int
    total_requests: int
    cache_hit_rate: float
    
    # Data freshness
    oldest_data_age_seconds: int
    last_collection_time: datetime

class CollectionLog(BaseModel):
    """Log of a data collection run"""
    id: int
    dex_id: Optional[int]
    dex_name: Optional[str]
    
    started_at: datetime
    completed_at: Optional[datetime]
    status: CollectionStatus
    
    symbols_fetched: int
    symbols_failed: int
    
    error_message: Optional[str] = None
    
    class Config:
        orm_mode = True
```

---

## API Design

### Base URL
```
Production: https://funding-rate-api.yourdomain.com
Development: http://localhost:8000
```

### API Versioning
```
/api/v1/...
```

### Authentication (Future)
```
Header: Authorization: Bearer <token>
```

### Endpoints

#### 1. Funding Rates

##### `GET /api/v1/funding-rates`
Get latest funding rates across all DEXs and symbols.

**Query Parameters:**
- `dex` (optional): Filter by specific DEX (e.g., `?dex=lighter`)
- `symbol` (optional): Filter by specific symbol (e.g., `?symbol=BTC`)
- `include_metadata` (optional): Include DEX metadata (default: false)

**Response:**
```json
{
  "data": {
    "BTC": {
      "lighter": 0.0001,
      "edgex": 0.0003,
      "paradex": -0.0001,
      "grvt": 0.0002,
      "hyperliquid": 0.00015
    },
    "ETH": {
      "lighter": 0.00008,
      "edgex": 0.00012,
      ...
    }
  },
  "updated_at": "2025-10-06T12:34:56Z",
  "dex_metadata": {
    "lighter": {
      "name": "lighter",
      "display_name": "Lighter Network",
      "maker_fee_percent": 0.0002,
      "taker_fee_percent": 0.0005
    },
    ...
  }
}
```

##### `GET /api/v1/funding-rates/{dex}`
Get funding rates for a specific DEX.

**Response:**
```json
{
  "dex_name": "lighter",
  "rates": {
    "BTC": {
      "funding_rate": 0.0001,
      "annualized_rate": 10.95,
      "next_funding_time": "2025-10-06T16:00:00Z",
      "timestamp": "2025-10-06T12:34:56Z"
    },
    "ETH": { ... }
  },
  "updated_at": "2025-10-06T12:34:56Z"
}
```

##### `GET /api/v1/funding-rates/{dex}/{symbol}`
Get funding rate for specific DEX and symbol.

**Response:**
```json
{
  "dex_name": "lighter",
  "symbol": "BTC",
  "funding_rate": 0.0001,
  "annualized_rate": 10.95,
  "next_funding_time": "2025-10-06T16:00:00Z",
  "timestamp": "2025-10-06T12:34:56Z",
  "mark_price": 62000.50,
  "index_price": 62005.25
}
```

#### 2. Opportunities

##### `GET /api/v1/opportunities`
Get all arbitrage opportunities.

**Query Parameters:**
- `symbol` (optional): Filter by symbol
- `long_dex` (optional): Filter by long DEX
- `short_dex` (optional): Filter by short DEX
- `include_dexes` (optional): Comma-separated list of DEXs to include (e.g., `lighter,edgex`)
- `exclude_dexes` (optional): Comma-separated list of DEXs to exclude
- `min_divergence` (optional): Minimum divergence (default: 0.0001)
- `min_profit` (optional): Minimum net profit percent (default: 0)
- `min_volume` (optional): Minimum 24h volume in USD (default: 100000)
- `limit` (optional): Number of results (default: 10, max: 100)
- `sort_by` (optional): Sort field (default: net_profit_percent)

**Response:**
```json
{
  "opportunities": [
    {
      "id": 12345,
      "symbol": "BTC",
      "long_dex": "paradex",
      "short_dex": "lighter",
      "long_rate": -0.0001,
      "short_rate": 0.0003,
      "divergence": 0.0004,
      "estimated_fees": 0.0007,
      "net_profit_percent": -0.0003,
      "annualized_apy": -3.29,
      "long_volume_24h": 5000000,
      "short_volume_24h": 8000000,
      "min_volume_24h": 5000000,
      "discovered_at": "2025-10-06T12:34:56Z",
      "valid_until": "2025-10-06T12:35:56Z"
    },
    ...
  ],
  "total_count": 25,
  "filters_applied": {
    "min_divergence": 0.0001,
    "min_profit_percent": 0,
    "min_volume_24h": 100000,
    "limit": 10
  },
  "generated_at": "2025-10-06T12:34:56Z"
}
```

##### `GET /api/v1/opportunities/best`
Get the single best opportunity (highest net profit).

**Query Parameters:** Same as `/opportunities`

**Response:**
```json
{
  "opportunity": {
    "symbol": "ETH",
    "long_dex": "grvt",
    "short_dex": "edgex",
    ...
  },
  "rank": 1,
  "total_opportunities": 25,
  "generated_at": "2025-10-06T12:34:56Z"
}
```

##### `GET /api/v1/opportunities/symbol/{symbol}`
Get opportunities for a specific symbol.

**Response:** Same as `/opportunities` but filtered by symbol

##### `GET /api/v1/opportunities/compare`
Compare opportunities between specific DEXs.

**Query Parameters:**
- `dex1` (required): First DEX
- `dex2` (required): Second DEX
- `symbol` (optional): Filter by symbol

**Response:**
```json
{
  "dex1": "lighter",
  "dex2": "edgex",
  "opportunities": [
    {
      "symbol": "BTC",
      "rate_diff": 0.0002,
      "better_on": "edgex",
      "recommendation": "short_on_edgex_long_on_lighter",
      "net_profit_percent": 0.00013,
      "annualized_apy": 14.23
    },
    ...
  ]
}
```

#### 3. Historical Data

##### `GET /api/v1/history/funding-rates/{dex}/{symbol}`
Get historical funding rates.

**Query Parameters:**
- `start_time` (optional): Start timestamp (ISO 8601)
- `end_time` (optional): End timestamp (ISO 8601)
- `period` (optional): Period shorthand (e.g., "7d", "30d", "90d")
- `interval` (optional): Data point interval (e.g., "1h", "4h", "1d")

**Response:**
```json
{
  "dex_name": "lighter",
  "symbol": "BTC",
  "data_points": [
    {"time": "2025-10-01T00:00:00Z", "rate": 0.0001},
    {"time": "2025-10-01T08:00:00Z", "rate": 0.00012},
    ...
  ],
  "statistics": {
    "avg_rate": 0.00011,
    "median_rate": 0.0001,
    "std_dev": 0.00003,
    "min_rate": -0.00005,
    "max_rate": 0.0003
  },
  "period_start": "2025-10-01T00:00:00Z",
  "period_end": "2025-10-06T12:34:56Z"
}
```

##### `GET /api/v1/history/opportunities`
Get historical opportunities.

**Query Parameters:**
- `symbol` (optional)
- `start_time` (optional)
- `end_time` (optional)
- `min_profit` (optional)
- `limit` (optional)

**Response:**
```json
{
  "opportunities": [
    {
      "id": 12345,
      "symbol": "BTC",
      "long_dex": "paradex",
      "short_dex": "lighter",
      "discovered_at": "2025-10-05T10:00:00Z",
      "net_profit_percent": 0.0005,
      ...
    },
    ...
  ],
  "total_count": 150,
  "period_start": "2025-10-01T00:00:00Z",
  "period_end": "2025-10-06T12:34:56Z"
}
```

##### `GET /api/v1/stats/funding-rates/{symbol}`
Get statistical analysis of funding rates.

**Query Parameters:**
- `dex` (optional): Specific DEX or all DEXs
- `period` (optional): Analysis period (default: "30d")

**Response:**
```json
{
  "symbol": "BTC",
  "dex_name": "lighter",
  "period_days": 30,
  "period_start": "2025-09-06T00:00:00Z",
  "period_end": "2025-10-06T00:00:00Z",
  "avg_funding_rate": 0.00011,
  "median_funding_rate": 0.0001,
  "std_dev": 0.00003,
  "volatility": 0.27,
  "min_rate": -0.00005,
  "max_rate": 0.0003,
  "percentile_25": 0.00008,
  "percentile_75": 0.00015,
  "avg_annualized_apy": 12.05,
  "positive_rate_frequency": 0.82
}
```

#### 4. DEX Metadata

##### `GET /api/v1/dexes`
Get all DEX metadata.

**Response:**
```json
{
  "dexes": [
    {
      "name": "lighter",
      "display_name": "Lighter Network",
      "is_active": true,
      "fee_structure": {
        "maker_fee_percent": 0.0002,
        "taker_fee_percent": 0.0005,
        "has_fee_tiers": false
      },
      "supported_symbols": ["BTC", "ETH", "SOL", ...],
      "last_successful_fetch": "2025-10-06T12:34:00Z",
      "consecutive_errors": 0
    },
    ...
  ]
}
```

##### `GET /api/v1/dexes/{dex}`
Get metadata for specific DEX.

##### `GET /api/v1/dexes/{dex}/symbols`
Get all symbols supported by a DEX.

**Response:**
```json
{
  "dex_name": "lighter",
  "symbols": [
    {
      "symbol": "BTC",
      "dex_symbol_format": "BTC-PERP",
      "is_active": true,
      "min_order_size": 0.001,
      "volume_24h": 8500000,
      "open_interest": 25000000
    },
    ...
  ]
}
```

#### 5. System Health

##### `GET /api/v1/health`
Get service health status.

**Response:**
```json
{
  "status": "healthy",
  "timestamp": "2025-10-06T12:34:56Z",
  "dex_health": [
    {
      "dex_name": "lighter",
      "is_healthy": true,
      "last_successful_fetch": "2025-10-06T12:34:00Z",
      "consecutive_errors": 0,
      "error_rate_percent": 0.5,
      "avg_collection_latency_ms": 250
    },
    ...
  ],
  "uptime_seconds": 86400,
  "total_requests": 15000,
  "cache_hit_rate": 0.85,
  "oldest_data_age_seconds": 45,
  "last_collection_time": "2025-10-06T12:34:00Z"
}
```

##### `GET /api/v1/metrics`
Get system metrics (for monitoring).

**Response:**
```json
{
  "requests_per_minute": 50,
  "avg_response_time_ms": 45,
  "cache_size_mb": 12.5,
  "database_connections": 5,
  "active_websockets": 3
}
```

#### 6. WebSocket

##### `WS /api/v1/ws/funding-rates`
Subscribe to real-time funding rate updates.

**Subscribe Message:**
```json
{
  "action": "subscribe",
  "channels": ["funding_rates"],
  "filters": {
    "symbols": ["BTC", "ETH"],
    "dexes": ["lighter", "edgex"]
  }
}
```

**Update Message (from server):**
```json
{
  "type": "funding_rate_update",
  "timestamp": "2025-10-06T12:34:56Z",
  "data": {
    "dex_name": "lighter",
    "symbol": "BTC",
    "funding_rate": 0.0001,
    "next_funding_time": "2025-10-06T16:00:00Z"
  }
}
```

##### `WS /api/v1/ws/opportunities`
Subscribe to real-time opportunity updates.

---

## Fee Management

### Fee Structure Handling

#### Basic Fee Structure
```python
class FeeCalculator:
    """Calculate trading fees and net profitability"""
    
    def __init__(self, dex_repository):
        self.dex_repo = dex_repository
        self._fee_cache = {}  # Cache fee structures
    
    def calculate_opportunity_fees(
        self,
        long_dex: str,
        short_dex: str,
        use_maker: bool = True
    ) -> Decimal:
        """
        Calculate total fees for an arbitrage opportunity.
        
        Fees include:
        - Opening long position
        - Opening short position
        - Closing long position
        - Closing short position
        
        Total = 4 transactions worth of fees
        """
        long_dex_meta = self._get_dex_metadata(long_dex)
        short_dex_meta = self._get_dex_metadata(short_dex)
        
        fee_type = FeeType.MAKER if use_maker else FeeType.TAKER
        
        # Get fees for each DEX
        long_fee = long_dex_meta.fee_structure.get_fee(fee_type)
        short_fee = short_dex_meta.fee_structure.get_fee(fee_type)
        
        # Total fees = 2 transactions per DEX (open + close)
        total_fees = (long_fee * 2) + (short_fee * 2)
        
        return total_fees
    
    def calculate_net_profit(
        self,
        divergence: Decimal,
        long_dex: str,
        short_dex: str,
        use_maker: bool = True
    ) -> Decimal:
        """Calculate net profit after fees"""
        fees = self.calculate_opportunity_fees(long_dex, short_dex, use_maker)
        return divergence - fees
    
    def _get_dex_metadata(self, dex_name: str) -> DEXMetadata:
        """Get DEX metadata with caching"""
        if dex_name not in self._fee_cache:
            self._fee_cache[dex_name] = self.dex_repo.get_by_name(dex_name)
        return self._fee_cache[dex_name]
```

#### Complex Fee Structures (Volume-Based Tiers)

Some DEXs may have volume-based fee tiers:

```python
# Example fee tier structure in database
fee_metadata_example = {
    "tiers": [
        {
            "min_volume_30d": 0,
            "max_volume_30d": 1000000,
            "maker_fee": 0.0002,
            "taker_fee": 0.0005
        },
        {
            "min_volume_30d": 1000000,
            "max_volume_30d": 10000000,
            "maker_fee": 0.00015,
            "taker_fee": 0.0004
        },
        {
            "min_volume_30d": 10000000,
            "max_volume_30d": None,  # Unlimited
            "maker_fee": 0.0001,
            "taker_fee": 0.0003
        }
    ]
}

# Update DEX model to support this:
def get_fee_for_volume(self, fee_type: FeeType, volume_30d: Decimal) -> Decimal:
    """Get fee based on 30-day volume"""
    if not self.has_fee_tiers or not self.fee_metadata:
        return self.maker_fee_percent if fee_type == FeeType.MAKER else self.taker_fee_percent
    
    tiers = self.fee_metadata.get('tiers', [])
    
    for tier in tiers:
        min_vol = Decimal(str(tier['min_volume_30d']))
        max_vol = Decimal(str(tier['max_volume_30d'])) if tier['max_volume_30d'] else Decimal('inf')
        
        if min_vol <= volume_30d < max_vol:
            return Decimal(str(tier['maker_fee'])) if fee_type == FeeType.MAKER else Decimal(str(tier['taker_fee']))
    
    # Default to base fees
    return self.maker_fee_percent if fee_type == FeeType.MAKER else self.taker_fee_percent
```

### Fee Update Mechanism

```python
# API endpoint to update fees (admin only in production)
@app.put("/api/v1/admin/dexes/{dex}/fees")
async def update_dex_fees(
    dex: str,
    fees: DEXFeeStructure,
    db: Database = Depends(get_db)
):
    """Update fee structure for a DEX"""
    await db.execute(
        """
        UPDATE dexes 
        SET maker_fee_percent = $1,
            taker_fee_percent = $2,
            has_fee_tiers = $3,
            fee_metadata = $4,
            updated_at = NOW()
        WHERE name = $5
        """,
        fees.maker_fee_percent,
        fees.taker_fee_percent,
        fees.has_fee_tiers,
        json.dumps(fees.fee_tiers) if fees.fee_tiers else None,
        dex
    )
    
    # Clear cache
    fee_calculator.clear_cache()
    
    return {"status": "success", "message": f"Fees updated for {dex}"}
```

---

## Caching Strategy

### Two-Tier Caching

#### Tier 1: In-Memory Cache (Python dict)
- **Use Case**: Ultra-fast access for frequently accessed data
- **TTL**: 60 seconds
- **Size**: Limited to ~100MB
- **Data**: Latest funding rates, DEX metadata

#### Tier 2: Redis Cache
- **Use Case**: Shared cache across multiple service instances
- **TTL**: Configurable per key (60s - 3600s)
- **Size**: Larger capacity
- **Data**: Historical data, computed opportunities

### Cache Implementation

```python
from typing import Optional, Any
import json
from datetime import datetime, timedelta

class CacheManager:
    """Two-tier cache manager"""
    
    def __init__(self, redis_client=None):
        self.redis = redis_client
        self.memory_cache = {}  # {key: (value, expiry_time)}
        self.memory_cache_ttl = 60  # seconds
    
    async def get(self, key: str, use_memory: bool = True) -> Optional[Any]:
        """Get value from cache"""
        # Try memory cache first
        if use_memory and key in self.memory_cache:
            value, expiry = self.memory_cache[key]
            if datetime.now() < expiry:
                return value
            else:
                del self.memory_cache[key]
        
        # Try Redis cache
        if self.redis:
            value = await self.redis.get(key)
            if value:
                deserialized = json.loads(value)
                # Populate memory cache
                if use_memory:
                    self.memory_cache[key] = (
                        deserialized,
                        datetime.now() + timedelta(seconds=self.memory_cache_ttl)
                    )
                return deserialized
        
        return None
    
    async def set(
        self,
        key: str,
        value: Any,
        ttl: int = 60,
        use_memory: bool = True
    ):
        """Set value in cache"""
        # Set in memory cache
        if use_memory:
            self.memory_cache[key] = (
                value,
                datetime.now() + timedelta(seconds=self.memory_cache_ttl)
            )
        
        # Set in Redis cache
        if self.redis:
            serialized = json.dumps(value, default=str)
            await self.redis.setex(key, ttl, serialized)
    
    async def invalidate(self, key: str):
        """Invalidate cache entry"""
        if key in self.memory_cache:
            del self.memory_cache[key]
        
        if self.redis:
            await self.redis.delete(key)
    
    async def invalidate_pattern(self, pattern: str):
        """Invalidate all keys matching pattern"""
        # Clear memory cache (pattern match)
        keys_to_delete = [k for k in self.memory_cache.keys() if pattern in k]
        for key in keys_to_delete:
            del self.memory_cache[key]
        
        # Clear Redis cache
        if self.redis:
            keys = await self.redis.keys(pattern)
            if keys:
                await self.redis.delete(*keys)

# Usage in service
cache_manager = CacheManager(redis_client)

async def get_latest_funding_rates():
    """Get latest rates with caching"""
    cache_key = "latest_funding_rates"
    
    # Try cache first
    cached = await cache_manager.get(cache_key)
    if cached:
        return cached
    
    # Fetch from database
    rates = await db.fetch_latest_rates()
    
    # Cache for 60 seconds
    await cache_manager.set(cache_key, rates, ttl=60)
    
    return rates
```

### Cache Invalidation Strategy

**Invalidate When:**
1. New data collected → Invalidate `latest_funding_rates*`
2. DEX fees updated → Invalidate `dex_metadata:{dex_name}`
3. New opportunities calculated → Invalidate `opportunities*`

**Cache Keys:**
```
latest_funding_rates              # All rates
latest_funding_rates:{dex}        # Rates for specific DEX
latest_funding_rates:{dex}:{symbol}  # Specific rate
opportunities:all                 # All opportunities
opportunities:symbol:{symbol}     # Opportunities for symbol
dex_metadata:{dex_name}           # DEX metadata
```

---

## Error Handling & Reliability

### Error Handling Strategy

#### 1. DEX API Failures

```python
class DEXAdapter:
    """Base adapter with error handling"""
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((TimeoutError, ConnectionError))
    )
    async def fetch_rates(self) -> Dict[str, Decimal]:
        """Fetch rates with retry logic"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    self.api_url,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status != 200:
                        raise APIError(f"API returned {response.status}")
                    
                    data = await response.json()
                    return self._parse_response(data)
        
        except asyncio.TimeoutError:
            logger.error(f"{self.dex_name}: Request timeout")
            await self._record_error("timeout")
            raise
        
        except aiohttp.ClientError as e:
            logger.error(f"{self.dex_name}: Connection error: {e}")
            await self._record_error("connection_error")
            raise
        
        except Exception as e:
            logger.error(f"{self.dex_name}: Unexpected error: {e}")
            await self._record_error("unknown_error")
            raise
    
    async def _record_error(self, error_type: str):
        """Record error in database for monitoring"""
        await db.execute(
            """
            UPDATE dexes 
            SET last_error = NOW(),
                consecutive_errors = consecutive_errors + 1
            WHERE name = $1
            """,
            self.dex_name
        )
```

#### 2. Partial Failures

```python
class CollectionOrchestrator:
    """Orchestrate data collection across DEXs"""
    
    async def collect_all_rates(self) -> Dict[str, Dict[str, Decimal]]:
        """Collect rates from all DEXs, handling partial failures"""
        tasks = {
            adapter.dex_name: adapter.fetch_rates()
            for adapter in self.adapters
        }
        
        # Gather results, don't raise on exceptions
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        
        successful_results = {}
        failed_dexes = []
        
        for dex_name, result in zip(tasks.keys(), results):
            if isinstance(result, Exception):
                logger.error(f"Failed to collect from {dex_name}: {result}")
                failed_dexes.append(dex_name)
                # Log failure but continue
                await self._log_collection_failure(dex_name, str(result))
            else:
                successful_results[dex_name] = result
                await self._log_collection_success(dex_name, len(result))
        
        if not successful_results:
            raise AllDEXsFailedError("All DEXs failed to respond")
        
        if failed_dexes:
            logger.warning(f"Partial failure: {len(failed_dexes)} DEXs failed")
        
        return successful_results
```

#### 3. Database Failures

```python
# Use database connection pooling with retry
from databases import Database

database = Database(
    DATABASE_URL,
    min_size=5,
    max_size=20,
    max_queries=50000,
    max_inactive_connection_lifetime=300
)

async def execute_with_retry(query, *args, max_retries=3):
    """Execute database query with retry"""
    for attempt in range(max_retries):
        try:
            return await database.execute(query, *args)
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            logger.warning(f"DB query failed (attempt {attempt + 1}): {e}")
            await asyncio.sleep(2 ** attempt)  # Exponential backoff
```

### Circuit Breaker Pattern

```python
class CircuitBreaker:
    """Circuit breaker for DEX adapters"""
    
    def __init__(self, failure_threshold=5, timeout=60):
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.failures = {}
        self.last_failure_time = {}
        self.state = {}  # 'closed', 'open', 'half_open'
    
    def is_open(self, dex_name: str) -> bool:
        """Check if circuit is open (failing)"""
        if dex_name not in self.state:
            self.state[dex_name] = 'closed'
            return False
        
        if self.state[dex_name] == 'open':
            # Check if timeout has passed
            if time.time() - self.last_failure_time[dex_name] > self.timeout:
                self.state[dex_name] = 'half_open'
                return False
            return True
        
        return False
    
    async def call(self, dex_name: str, func):
        """Execute function with circuit breaker"""
        if self.is_open(dex_name):
            raise CircuitBreakerOpenError(f"Circuit open for {dex_name}")
        
        try:
            result = await func()
            self._on_success(dex_name)
            return result
        except Exception as e:
            self._on_failure(dex_name)
            raise
    
    def _on_success(self, dex_name: str):
        """Handle successful call"""
        self.failures[dex_name] = 0
        self.state[dex_name] = 'closed'
    
    def _on_failure(self, dex_name: str):
        """Handle failed call"""
        self.failures[dex_name] = self.failures.get(dex_name, 0) + 1
        self.last_failure_time[dex_name] = time.time()
        
        if self.failures[dex_name] >= self.failure_threshold:
            self.state[dex_name] = 'open'
            logger.error(f"Circuit breaker opened for {dex_name}")
```

---

## Performance Considerations

### 1. Database Optimization

#### Indexes
- Already defined in schema
- Monitor slow queries and add indexes as needed

#### Query Optimization
```python
# BAD: N+1 query problem
for symbol in symbols:
    rate = await db.fetch_one(
        "SELECT * FROM funding_rates WHERE symbol_id = $1",
        symbol.id
    )

# GOOD: Single query with JOIN
rates = await db.fetch_all("""
    SELECT s.symbol, d.name as dex_name, fr.funding_rate
    FROM funding_rates fr
    JOIN symbols s ON fr.symbol_id = s.id
    JOIN dexes d ON fr.dex_id = d.id
    WHERE fr.time = (
        SELECT MAX(time) FROM funding_rates WHERE symbol_id = s.id AND dex_id = d.id
    )
""")
```

#### Connection Pooling
```python
# Configure in database setup
database = Database(
    DATABASE_URL,
    min_size=5,  # Minimum connections
    max_size=20,  # Maximum connections
    max_inactive_connection_lifetime=300
)
```

### 2. API Performance

#### Async Everywhere
- Use `async/await` for all I/O operations
- Leverage FastAPI's async capabilities

#### Response Streaming
```python
from fastapi.responses import StreamingResponse

@app.get("/api/v1/opportunities/stream")
async def stream_opportunities():
    """Stream opportunities as they're found"""
    async def generate():
        async for opp in opportunity_finder.find_continuously():
            yield json.dumps(opp.dict()) + "\n"
    
    return StreamingResponse(generate(), media_type="application/x-ndjson")
```

#### Pagination
```python
@app.get("/api/v1/history/funding-rates")
async def get_history(
    symbol: str,
    dex: str,
    page: int = 1,
    page_size: int = 100
):
    """Paginated historical data"""
    offset = (page - 1) * page_size
    
    rates = await db.fetch_all(
        """
        SELECT * FROM funding_rates
        WHERE dex_id = (SELECT id FROM dexes WHERE name = $1)
          AND symbol_id = (SELECT id FROM symbols WHERE symbol = $2)
        ORDER BY time DESC
        LIMIT $3 OFFSET $4
        """,
        dex, symbol, page_size, offset
    )
    
    total = await db.fetch_val(
        """
        SELECT COUNT(*) FROM funding_rates
        WHERE dex_id = (SELECT id FROM dexes WHERE name = $1)
          AND symbol_id = (SELECT id FROM symbols WHERE symbol = $2)
        """,
        dex, symbol
    )
    
    return {
        "data": rates,
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": (total + page_size - 1) // page_size
    }
```

### 3. Parallel Processing

```python
# Collect rates from all DEXs in parallel
results = await asyncio.gather(
    lighter_adapter.fetch_rates(),
    edgex_adapter.fetch_rates(),
    paradex_adapter.fetch_rates(),
    grvt_adapter.fetch_rates(),
    hyperliquid_adapter.fetch_rates(),
    return_exceptions=True
)
```

### 4. Monitoring & Profiling

```python
from prometheus_client import Counter, Histogram, Gauge
import time

# Metrics
request_count = Counter('api_requests_total', 'Total API requests', ['endpoint', 'method'])
request_duration = Histogram('api_request_duration_seconds', 'Request duration', ['endpoint'])
active_connections = Gauge('active_connections', 'Active database connections')

@app.middleware("http")
async def add_metrics(request: Request, call_next):
    """Middleware to track metrics"""
    start_time = time.time()
    
    response = await call_next(request)
    
    duration = time.time() - start_time
    request_count.labels(endpoint=request.url.path, method=request.method).inc()
    request_duration.labels(endpoint=request.url.path).observe(duration)
    
    return response
```

---

## Future Extensibility

### 1. Plugin Architecture for New DEXs

```python
# dex_adapter_plugin.py
class DEXAdapterPlugin(Protocol):
    """Protocol for DEX adapter plugins"""
    
    @staticmethod
    def get_adapter_name() -> str:
        """Return unique adapter name"""
        ...
    
    @staticmethod
    def create_adapter(config: Dict[str, Any]) -> BaseDEXAdapter:
        """Factory method to create adapter instance"""
        ...
    
    @staticmethod
    def get_required_config() -> List[str]:
        """Return required configuration keys"""
        ...

# Plugin loader
class PluginLoader:
    def __init__(self, plugin_dir: str = "plugins"):
        self.plugin_dir = plugin_dir
        self.adapters = {}
    
    def load_plugins(self):
        """Dynamically load DEX adapter plugins"""
        for file in os.listdir(self.plugin_dir):
            if file.endswith("_adapter.py"):
                module = importlib.import_module(f"{self.plugin_dir}.{file[:-3]}")
                if hasattr(module, "Plugin"):
                    plugin = module.Plugin()
                    self.adapters[plugin.get_adapter_name()] = plugin
```

### 2. Webhook Support

```python
# Allow users to register webhooks for opportunities
@app.post("/api/v1/webhooks")
async def register_webhook(webhook: WebhookConfig):
    """Register a webhook for notifications"""
    await db.execute(
        """
        INSERT INTO webhooks (url, event_type, filters, user_id)
        VALUES ($1, $2, $3, $4)
        """,
        webhook.url,
        webhook.event_type,  # 'opportunity', 'funding_rate_change'
        json.dumps(webhook.filters),
        webhook.user_id
    )
    
    return {"status": "success", "webhook_id": webhook_id}

# Trigger webhooks
async def trigger_webhooks(event_type: str, data: Dict[str, Any]):
    """Trigger registered webhooks"""
    webhooks = await db.fetch_all(
        "SELECT * FROM webhooks WHERE event_type = $1 AND is_active = TRUE",
        event_type
    )
    
    for webhook in webhooks:
        # Filter data based on webhook filters
        if _matches_filters(data, webhook['filters']):
            asyncio.create_task(
                send_webhook(webhook['url'], data)
            )
```

### 3. Machine Learning Integration

```python
# Future: Add ML-based predictions
@app.get("/api/v1/predictions/funding-rate/{dex}/{symbol}")
async def predict_funding_rate(dex: str, symbol: str):
    """Predict future funding rate using ML model"""
    # Load historical data
    history = await get_funding_rate_history(dex, symbol, days=30)
    
    # Run through ML model
    prediction = ml_model.predict(history)
    
    return {
        "dex": dex,
        "symbol": symbol,
        "current_rate": history[-1]['rate'],
        "predicted_rate": prediction,
        "confidence": confidence_score,
        "prediction_horizon": "8h"
    }
```

### 4. Multi-Asset Arbitrage

```python
# Future: Support triangular or multi-leg arbitrage
@app.get("/api/v1/opportunities/multi-asset")
async def find_multi_asset_opportunities():
    """Find opportunities involving multiple assets"""
    # Example: BTC funding arb + ETH/BTC pair trade
    pass
```

---

## Implementation Checklist

### Phase 1: Core Infrastructure (Week 1)
- [ ] Set up project structure
- [ ] Configure PostgreSQL + TimescaleDB
- [ ] Create database schema
- [ ] Set up FastAPI application
- [ ] Implement data models (Pydantic)
- [ ] Set up logging
- [ ] Configure environment variables

### Phase 2: Data Collection Layer (Week 1-2)
- [ ] Implement `BaseDEXAdapter`
- [ ] Implement `LighterAdapter`
- [ ] Implement `EdgeXAdapter`
- [ ] Implement `ParadexAdapter`
- [ ] Implement `GRVTAdapter`
- [ ] Implement `HyperliquidAdapter`
- [ ] Implement `CollectionOrchestrator`
- [ ] Add retry logic & error handling
- [ ] Add circuit breaker pattern
- [ ] Test each adapter individually

### Phase 3: Data Access Layer (Week 2)
- [ ] Implement repositories (DEX, Symbol, FundingRate, Opportunity)
- [ ] Implement cache manager (memory + Redis)
- [ ] Add database connection pooling
- [ ] Implement cache invalidation logic
- [ ] Write database migration scripts

### Phase 4: Business Logic Layer (Week 2-3)
- [ ] Implement `FeeCalculator`
- [ ] Implement `OpportunityFinder`
- [ ] Implement `DataAggregator`
- [ ] Add opportunity ranking logic
- [ ] Implement historical analyzer
- [ ] Write unit tests

### Phase 5: API Layer (Week 3)
- [ ] Implement funding rate endpoints
- [ ] Implement opportunity endpoints
- [ ] Implement historical data endpoints
- [ ] Implement DEX metadata endpoints
- [ ] Implement health check endpoints
- [ ] Implement WebSocket endpoints
- [ ] Add request validation
- [ ] Add error handling middleware
- [ ] Generate OpenAPI documentation

### Phase 6: Background Tasks (Week 3-4)
- [ ] Implement rate collection task
- [ ] Implement opportunity analysis task
- [ ] Implement data cleanup task
- [ ] Implement DEX health check task
- [ ] Add task scheduling
- [ ] Add task monitoring

### Phase 7: Testing (Week 4)
- [ ] Write unit tests (80%+ coverage)
- [ ] Write integration tests
- [ ] Write end-to-end tests
- [ ] Performance testing
- [ ] Load testing (100+ RPS)
- [ ] Test error scenarios
- [ ] Test cache behavior

### Phase 8: Monitoring & Observability (Week 4)
- [ ] Add Prometheus metrics
- [ ] Set up Grafana dashboards
- [ ] Add structured logging
- [ ] Set up error alerting
- [ ] Add health checks
- [ ] Add request tracing

### Phase 9: Documentation (Week 5)
- [ ] API documentation (auto-generated + manual)
- [ ] Architecture documentation
- [ ] Deployment guide
- [ ] Developer guide (how to add new DEX)
- [ ] Configuration guide
- [ ] Troubleshooting guide

### Phase 10: Deployment (Week 5)
- [ ] Create Docker container
- [ ] Create docker-compose for local development
- [ ] Set up CI/CD pipeline
- [ ] Deploy to staging
- [ ] Run integration tests in staging
- [ ] Deploy to production
- [ ] Monitor production metrics

---

## Technology Stack

### Backend
- **Language**: Python 3.11+
- **Framework**: FastAPI
- **Database**: PostgreSQL 15+ with TimescaleDB extension
- **Cache**: Redis 7+ (optional - can use in-memory only for single VPS)
- **ORM/Query Builder**: asyncpg (raw SQL for performance) + databases library

### Infrastructure
- **Deployment**: Single VPS (all-in-one) or containerized
- **Containerization**: Docker (optional for VPS deployment)
- **Orchestration**: Docker Compose (dev) / Kubernetes (prod) / Systemd (VPS)
- **Monitoring**: Prometheus + Grafana
- **Logging**: Structured logging with loguru
- **Reverse Proxy**: Nginx (for SSL/domain)

### Development Tools
- **Testing**: pytest, pytest-asyncio
- **Linting**: ruff, black
- **Type Checking**: mypy
- **API Testing**: httpx (async client)

---

## Deployment Options

### Option 1: VPS All-in-One (Recommended for Starting)

**Architecture:**
```
┌─────────────────────────────────────────┐
│           Your VPS                      │
├─────────────────────────────────────────┤
│  • FastAPI Service (port 8000)          │
│  • PostgreSQL (localhost:5432)          │
│  • Redis (optional, localhost:6379)     │
│  • Nginx (port 80/443)                  │
└─────────────────────────────────────────┘
```

**Setup Steps:**
1. Install PostgreSQL + TimescaleDB
2. Install Redis (optional - can use memory-only cache)
3. Install Python 3.11+
4. Deploy FastAPI service
5. Configure Nginx as reverse proxy

**Pros:**
- Simple setup
- Low cost (single VPS)
- Easy to manage
- Good for initial deployment

**Cons:**
- Limited horizontal scaling
- Single point of failure

**VPS Requirements:**
- **CPU**: 2+ cores
- **RAM**: 4GB+ (8GB recommended)
- **Storage**: 50GB+ SSD
- **Network**: Good connectivity to DEX APIs

### Option 2: Containerized (Docker)

If you want easier deployment management on VPS:

```yaml
# docker-compose.yml
version: '3.8'

services:
  postgres:
    image: timescale/timescaledb:latest-pg15
    volumes:
      - postgres_data:/var/lib/postgresql/data
    environment:
      POSTGRES_DB: funding_rates
      POSTGRES_USER: funding_user
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    ports:
      - "5432:5432"
  
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
  
  api:
    build: .
    ports:
      - "8000:8000"
    environment:
      DATABASE_URL: postgresql://funding_user:${DB_PASSWORD}@postgres:5432/funding_rates
      REDIS_URL: redis://redis:6379/0
    depends_on:
      - postgres
      - redis

volumes:
  postgres_data:
```

## Configuration

### Environment Variables

```bash
# Database
DATABASE_URL=postgresql://user:password@localhost:5432/funding_rates
DATABASE_POOL_MIN_SIZE=5
DATABASE_POOL_MAX_SIZE=20

# Redis (optional - set to empty to use memory-only cache)
REDIS_URL=redis://localhost:6379/0
REDIS_PASSWORD=
USE_REDIS=true  # Set to false for memory-only cache on VPS

# Service
SERVICE_PORT=8000
SERVICE_HOST=0.0.0.0
LOG_LEVEL=INFO
ENVIRONMENT=development  # development, staging, production

# DEX APIs
LIGHTER_API_URL=https://api.lighter.xyz
EDGEX_API_URL=https://api.edgex.exchange
PARADEX_API_URL=https://api.paradex.trade
GRVT_API_URL=https://api.grvt.io
HYPERLIQUID_API_URL=https://api.hyperliquid.xyz

# Collection settings
COLLECTION_INTERVAL_SECONDS=60
MAX_CONCURRENT_COLLECTIONS=10
COLLECTION_TIMEOUT_SECONDS=30

# Cache settings
CACHE_TTL_SECONDS=60
CACHE_MAX_SIZE_MB=100

# Monitoring
PROMETHEUS_PORT=9090
```

---

## Conclusion

This system design provides a comprehensive, extensible, and production-ready funding rate service. Key highlights:

1. **Modular Architecture**: Clean separation of concerns
2. **Robust Database Design**: Optimized for time-series data
3. **Comprehensive API**: Covers all use cases with flexibility
4. **Fee Management**: Handles various fee structures
5. **Error Handling**: Graceful degradation and retries
6. **Performance**: Caching, async operations, query optimization
7. **Extensibility**: Easy to add new DEXs, features, and integrations
8. **Production-Ready**: Monitoring, logging, health checks

This design should serve not only your current funding arbitrage needs but also future use cases like:
- Historical analysis and backtesting
- ML-based predictions
- Multi-asset strategies
- Third-party integrations
- Analytics dashboards

