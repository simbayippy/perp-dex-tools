# Strategy Refactoring: High-Level Task Overview

## 🎯 Goal

Transform the current flat strategy structure into a **3-Level Hierarchy + Composition Hybrid** architecture that supports both simple strategies (Grid) and complex multi-DEX strategies (Funding Arbitrage).

---

## 📊 Target Structure

```
/strategies/
├── base_strategy.py                  # Level 1: Minimal interface
├── factory.py                        # Strategy factory
│
├── /categories/                      # Level 2: Strategy archetypes
│   ├── stateless_strategy.py        # For simple strategies
│   └── stateful_strategy.py         # For complex strategies
│
├── /components/                      # Shared reusable components
│   ├── position_manager.py          # Position tracking
│   ├── state_manager.py             # State persistence
│   └── base_components.py           # Component interfaces
│
└── /implementations/                 # Level 3: Concrete strategies
    ├── /grid/                        # Simple strategy
    │   ├── strategy.py
    │   ├── config.py
    │   └── models.py
    │
    └── /funding_arbitrage/           # Complex strategy
        ├── strategy.py               # Main orchestrator
        ├── config.py                 # Pydantic configs
        ├── models.py                 # Data models
        ├── position_manager.py       # Position tracking
        ├── rebalancer.py             # Rebalancing logic
        ├── api_client.py             # Funding service client
        │
        ├── /rebalance_strategies/    # Pluggable sub-strategies
        │   ├── __init__.py           # Factory
        │   ├── base.py               # Interface
        │   ├── profit_erosion.py
        │   ├── divergence_flip.py
        │   └── combined.py
        │
        └── /operations/              # Complex operations
            ├── fund_transfer.py      # Cross-DEX transfers
            └── bridge_manager.py     # Cross-chain bridging
```

---

## 📅 Implementation Phases

### **Phase 1: Foundation (Days 1-3)**

**Setup:**
- Create git branch for refactoring
- Create database migration for new tables
- Backup existing code

**Build Base Layer:**
- Update `base_strategy.py` with minimal interface
- Create `components/base_components.py` with interfaces
- Run database migration

**Deliverables:**
- ✅ `base_strategy.py` simplified
- ✅ Component interfaces defined
- ✅ Database tables created
- ✅ No breaking changes yet

---

### **Phase 2: Category Layer (Days 4-5)**

**Create Strategy Categories:**
- Create `categories/stateless_strategy.py`
  - For simple, single-DEX strategies
  - Template method for execution flow
  - Helper methods for market data
  
- Create `categories/stateful_strategy.py`
  - For complex, multi-DEX strategies
  - Factory methods for components
  - No enforced execution flow

**Deliverables:**
- ✅ Two category base classes created
- ✅ Categories tested independently
- ✅ Documentation updated

---

### **Phase 3: Shared Components (Days 6-7)**

**Build Reusable Components:**
- Create `components/position_manager.py`
  - Database-backed position tracking
  - Works for both simple and complex strategies
  
- Create `components/state_manager.py`
  - PostgreSQL implementation
  - In-memory implementation (for testing)

**Deliverables:**
- ✅ Position manager with database sync
- ✅ State manager with multiple backends
- ✅ Component tests written

---

### **(SKIP FOR NOW - NOT SO IMPORANT)Phase 4: Migrate Grid Strategy (Day 8)**

**Validate Architecture:**
- Move Grid strategy to `implementations/grid/`
- Change parent class from `BaseStrategy` to `StatelessStrategy`
- Create Pydantic config models
- Update factory

**Purpose:** Prove the new architecture works without breaking existing functionality

**Deliverables:**
- ✅ Grid strategy migrated
- ✅ All existing tests pass
- ✅ Backward compatibility maintained

---

### **Phase 5: Funding Arbitrage Foundation (Days 9-12)**

**Build Main Strategy:**
- Create `implementations/funding_arbitrage/` package
- Create Pydantic config models (hierarchical)
- Create data models (Position, TransferOperation, etc.)
- Build main strategy orchestrator with 4-phase loop:
  1. Monitor positions
  2. Execute rebalancing
  3. Open new positions
  4. Process fund transfers

**Deliverables:**
- ✅ Funding arb package structure created
- ✅ Config and data models defined
- ✅ Main orchestrator scaffold complete
- ✅ API client for funding service

---

### **Phase 6: Rebalancing System (Days 13-15)**

**Build Pluggable Sub-Strategies:**
- Create rebalance base interface
- Implement sub-strategies:
  - Profit erosion
  - Divergence flip
  - Better opportunity
  - Time-based
  - Combined (composition of multiple)
- Create rebalancer orchestrator
- Create factory for sub-strategies

**Deliverables:**
- ✅ 5+ rebalance strategies implemented
- ✅ Factory pattern for easy swapping
- ✅ Combined strategy with priority rules
- ✅ Unit tests for each strategy

---

### **Phase 7: Fund Transfer Operations (Days 16-18)**

**Build Transfer System:**
- Create fund transfer manager
  - Multi-step state machine
  - Retry logic
  - Error handling
  
- Create bridge manager
  - Support multiple bridge protocols
  - Unified interface

**Deliverables:**
- ✅ Fund transfers working
- ✅ Cross-chain bridging implemented
- ✅ Comprehensive error handling
- ✅ Integration tests

---

### **Phase 8: Testing & Integration (Days 19-20)**

**Complete Testing:**
- Write unit tests for all components
- Write integration tests
- Create mock factories for testing
- Achieve >80% test coverage

**Update Main System:**
- Update `trading_bot.py` to support new architecture
- Handle both stateless and stateful strategies
- Update CLI arguments if needed

**Deliverables:**
- ✅ Comprehensive test suite
- ✅ trading_bot.py updated
- ✅ Both strategies work together

---

### **Phase 9: Documentation (Days 21-22)**

**Create Documentation:**
- Architecture documentation
- Migration guide for strategy developers
- API documentation for components
- Example configs for both strategies
- Deployment guide

**Deliverables:**
- ✅ Complete documentation
- ✅ Migration examples
- ✅ Team training materials

---

### **Phase 10: Deployment (Days 23-25)**

**Deploy to Production:**
- Deploy database migrations
- Deploy new code
- Start with Grid strategy (validate)
- Start funding arbitrage with small positions
- Monitor for 24 hours
- Full production deployment

**Deliverables:**
- ✅ Production deployment complete
- ✅ Monitoring dashboard set up
- ✅ 24-hour validation passed
- ✅ Rollback plan documented

---

## 🗂️ Database Requirements

### New Tables (PostgreSQL)

Add to existing `funding_rate_service` database:

```sql
-- Strategy positions
CREATE TABLE strategy_positions (
    id UUID PRIMARY KEY,
    strategy_name VARCHAR(50),
    symbol VARCHAR(20),
    long_dex VARCHAR(20),
    short_dex VARCHAR(20),
    size_usd DECIMAL(20, 8),
    entry_divergence DECIMAL(20, 8),
    current_divergence DECIMAL(20, 8),
    status VARCHAR(20),
    opened_at TIMESTAMP,
    closed_at TIMESTAMP,
    ...
);

-- Fund transfers
CREATE TABLE fund_transfers (
    id UUID PRIMARY KEY,
    from_dex VARCHAR(20),
    to_dex VARCHAR(20),
    amount_usd DECIMAL(20, 8),
    status VARCHAR(20),
    ...
);

-- Strategy state
CREATE TABLE strategy_state (
    strategy_name VARCHAR(50) PRIMARY KEY,
    state_data JSONB,
    last_updated TIMESTAMP
);
```

**Why PostgreSQL?**
- Reuse existing infrastructure
- Single source of truth
- Easy to query positions + funding rates together
- Better for production deployment

---

## 🏗️ Architecture Principles

### 3-Level Hierarchy

**Level 1: BaseStrategy**
- Minimal interface
- Only essential methods
- No assumptions about execution

**Level 2: Categories**
- Stateless: Simple strategies (Grid, TWAP)
- Stateful: Complex strategies (Funding Arb, Market Making)

**Level 3: Implementations**
- Self-contained packages
- Strategy-specific logic
- Compose components as needed

### Composition Over Inheritance

**Inheritance:** Use for shared contracts (interfaces)

**Composition:** Use for functionality
- Position manager (inject via factory)
- State manager (inject via factory)
- Rebalancer (compose in strategy)
- Fund manager (compose in strategy)

### Benefits

✅ **Flexibility:** Easy to customize components
✅ **Testability:** Easy to inject mocks
✅ **Extensibility:** Easy to add new strategies
✅ **Maintainability:** Clear component boundaries
✅ **Reusability:** Components shared across strategies

---

## 📦 Key Design Decisions

### Monolith vs Microservices

**Decision:** Keep strategies in monolith

**Rationale:**
- Trading requires low latency (milliseconds)
- Strategies need instant access to positions/orders
- No benefit from independent scaling
- Simpler deployment and debugging

### Database Choice

**Decision:** PostgreSQL (extend funding_rate_service DB)

**Rationale:**
- Already have infrastructure
- Single source of truth
- Easy to query positions + opportunities
- Production-ready

### Pluggable Sub-Strategies

**Decision:** Factory pattern for rebalance strategies

**Rationale:**
- Easy to swap strategies at runtime
- Can A/B test different approaches
- Clear separation of concerns
- Easy to add new strategies

---

## ✅ Success Criteria

### Phase 1-4:
- Grid strategy works with zero behavior changes
- All existing tests pass
- New architecture is backward compatible

### Phase 5-8:
- Funding arbitrage can open/close positions
- Rebalancing triggers correctly
- Fund transfers execute successfully
- >80% test coverage

### Phase 9-10:
- Both strategies run simultaneously
- No performance degradation
- Documentation complete
- Team can add new strategies independently

---

## 🚨 Risks & Mitigation

| Risk | Mitigation |
|------|-----------|
| Database migration fails | Test on DB copy first |
| Backward compatibility breaks | Validate Grid in Phase 4 |
| Fund transfers lose funds | Test with small amounts first |
| Performance degradation | Load testing before production |
| System too complex | Comprehensive documentation |

---

## 📊 Estimated Effort

**Total Duration:** 25 days (~5 weeks)

**Breakdown:**
- Foundation & Categories: 5 days
- Components & Grid Migration: 3 days
- Funding Arbitrage Core: 7 days
- Rebalancing & Transfers: 6 days
- Testing & Documentation: 4 days

**Team Size:** 1-2 developers

---

## 🎯 Next Steps

1. **Review this document** with team
2. **Create git branch** for refactoring
3. **Start Phase 1** - Foundation layer
4. **Iterate in phases** - validate each phase before moving forward
5. **Deploy incrementally** - Grid first, then Funding Arb

---

## 📝 Notes

- Each phase builds on previous phases
- Can pause between phases to validate
- Phases 1-4 are safe (no breaking changes)
- Phases 5-8 add new functionality
- Phases 9-10 are deployment
- Rollback possible at any phase

---

**Last Updated:** 2025-10-08
**Status:** Ready to Begin
**Approved By:** [Pending]