# CCXT Migration Proposal

## Executive Summary

This document outlines a potential future migration from the custom `exchange_clients` package to [CCXT](https://github.com/ccxt/ccxt), a unified cryptocurrency exchange trading library with 100+ exchange connectors. This migration would leverage CCXT's community-maintained exchange implementations, standardized API, and broader exchange coverage while maintaining our existing strategy and execution layers.

**Status**: ⏳ **FUTURE CONSIDERATION** - Not an immediate priority, but documented for future evaluation.

## Current State

### Custom Exchange Clients Architecture

Our current implementation includes:

- **6 Custom Exchange Implementations**:
  - Lighter Network (`exchange_clients/lighter/`)
  - Aster (`exchange_clients/aster/`)
  - Backpack (`exchange_clients/backpack/`)
  - Paradex (`exchange_clients/paradex/`)
  - EdgeX (`exchange_clients/edgex/`)
  - GRVT (`exchange_clients/grvt/`)

- **Base Classes**:
  - `BaseExchangeClient` - Unified trading interface
  - `BaseWebSocketManager` - WebSocket management
  - `BaseFundingAdapter` - Funding rate data collection

- **Key Features**:
  - Perpetual DEX-focused (not CEX)
  - Custom WebSocket implementations for real-time order book
  - Funding rate adapters for each exchange
  - Multi-account credential management
  - Proxy support integration
  - Custom order fill callbacks
  - Liquidation event streams

### Current Exchange Client Structure

```
exchange_clients/
├── base_client.py              # BaseExchangeClient interface
├── base_websocket.py           # BaseWebSocketManager interface
├── base_funding_adapter.py     # BaseFundingAdapter interface
├── base_models.py              # Shared data models
├── factory.py                  # ExchangeFactory for dynamic loading
├── lighter/                    # Full implementation (reference)
│   ├── client/                 # Trading client
│   ├── websocket/              # WebSocket manager
│   └── funding_adapter/        # Funding rate adapter
├── aster/                      # Full implementation
├── backpack/                    # Full implementation
├── paradex/                     # Full implementation
├── edgex/                       # Partial implementation
└── grvt/                        # Partial implementation
```

## CCXT Overview

### What CCXT Provides

- **Unified API**: Standardized methods across all exchanges (`createOrder`, `fetchBalance`, `fetchTicker`, etc.)
- **100+ Exchange Connectors**: Community-maintained implementations
- **Multi-Language Support**: Python, JavaScript/TypeScript, C#, PHP, Go
- **Active Community**: Regular updates, bug fixes, and new exchange additions
- **Well-Documented**: Comprehensive documentation and examples

### CCXT Architecture

```
User Application
    ↓
CCXT Unified API
    ├── Public Methods (loadMarkets, fetchTicker, fetchOrderBook, etc.)
    └── Private Methods (createOrder, fetchBalance, cancelOrder, etc.)
    ↓
Exchange-Specific Implementations
    ├── Binance, Coinbase, Kraken, etc. (CEX)
    └── Custom exchange classes (can be added)
    ↓
Base Exchange Class
```

## Benefits of Migration

### 1. **Reduced Maintenance Burden**
- **Current**: Maintain 6 custom exchange implementations (~15,000+ lines of code)
- **After Migration**: Maintain only custom adapters/wrappers for CCXT
- **Benefit**: Focus development effort on strategies and execution logic, not exchange API changes

### 2. **Broader Exchange Coverage**
- **Current**: 6 exchanges (all DEXs)
- **CCXT**: 100+ exchanges (CEX + DEX)
- **Benefit**: Potential to expand to CEX arbitrage opportunities, more liquidity sources

### 3. **Community Support**
- **Current**: Single developer maintaining all exchange clients
- **CCXT**: Large open-source community with regular updates
- **Benefit**: Bug fixes, API updates, and new features handled by community

### 4. **Standardized API**
- **Current**: Custom base classes with project-specific patterns
- **CCXT**: Industry-standard unified API
- **Benefit**: Easier onboarding, better documentation, more examples available

### 5. **Better Testing**
- **Current**: Custom test infrastructure for each exchange
- **CCXT**: Well-tested library with extensive test coverage
- **Benefit**: Reduced bugs, more reliable exchange interactions

## Challenges & Risks

### 1. **Missing Exchange Support**

**Problem**: CCXT may not support all our current exchanges:
- **Lighter Network**: ❌ Not in CCXT (DEX, Starknet-based)
- **Aster**: ❌ Not in CCXT (DEX, Solana-based)
- **Backpack**: ❓ Unknown (may be supported)
- **Paradex**: ❓ Unknown (Starknet-based DEX)
- **EdgeX**: ❌ Not in CCXT (DEX)
- **GRVT**: ❌ Not in CCXT (DEX)

**Solution**: Implement custom CCXT exchange classes for unsupported exchanges
- CCXT allows custom exchange implementations
- Can extend `ccxt.Exchange` base class
- Still benefits from CCXT's unified API structure

**Effort**: Medium - Need to implement ~6 custom exchange classes, but can reuse existing API knowledge

### 2. **WebSocket Implementation Differences**

**Problem**: Our current implementation has custom WebSocket managers for real-time order book updates:
- `BaseWebSocketManager` with `prepare_market_feed()`, `get_order_book()`
- Exchange-specific WebSocket implementations
- Order fill callbacks and liquidation event streams

**CCXT**: Primarily REST API focused, WebSocket support varies by exchange

**Solution**: 
- Keep custom WebSocket implementations for real-time features
- Use CCXT for REST API calls (order placement, position queries)
- Hybrid approach: CCXT for trading, custom WebSockets for market data

**Effort**: Low - Can maintain existing WebSocket code while migrating REST calls

### 3. **Funding Rate Adapters**

**Problem**: Our `BaseFundingAdapter` pattern collects funding rate data:
- Custom adapters for each exchange
- Integrated with `funding_rate_service/`

**CCXT**: May have `fetchFundingRate()` methods, but implementation varies

**Solution**:
- Use CCXT's funding rate methods where available
- Keep custom adapters for exchanges without CCXT support
- Gradually migrate to CCXT methods as they become available

**Effort**: Low - Can migrate incrementally

### 4. **API Method Differences**

**Problem**: Our `BaseExchangeClient` has custom methods:
- `place_limit_order()`, `place_market_order()`
- `get_position()`, `get_positions()`
- `get_order_book_depth()` with custom BBO logic
- Custom error handling and retry logic

**CCXT**: Uses different method names:
- `create_order()`, `create_limit_order()`, `create_market_order()`
- `fetch_positions()`
- `fetch_order_book()`

**Solution**: Create adapter/wrapper layer:
- Wrap CCXT clients to match our `BaseExchangeClient` interface
- Map method names and parameters
- Preserve existing strategy code compatibility

**Effort**: Medium - Need adapter layer, but strategies remain unchanged

### 5. **Credential Management**

**Problem**: Our system uses:
- Database-stored encrypted credentials
- Multi-account support
- Custom credential loading via `database/credential_loader.py`

**CCXT**: Uses simple config dict with API keys

**Solution**: 
- Keep existing credential management system
- Map credentials to CCXT config format in adapter layer
- No changes needed to credential storage/loading

**Effort**: Low - Simple mapping in adapter

### 6. **Proxy Support**

**Problem**: Our system integrates with:
- `networking/session_proxy.py`
- Proxy rotation for rate limiting
- Per-exchange proxy assignment

**CCXT**: Has proxy support via config, but may need adapter integration

**Solution**: Map proxy configuration to CCXT's proxy settings

**Effort**: Low - Configuration mapping

### 7. **Testing & Migration Risk**

**Problem**: 
- Large codebase with many dependencies
- Production trading system (risk of bugs)
- Need thorough testing before migration

**Solution**: 
- Incremental migration approach
- Migrate one exchange at a time
- Extensive testing in staging environment
- Keep old implementation as fallback

**Effort**: High - Requires careful planning and testing

## Migration Strategy

### Phase 1: Research & Feasibility (1-2 weeks)

**Tasks**:
1. ✅ Audit CCXT exchange support for our exchanges
2. ✅ Evaluate CCXT's WebSocket capabilities
3. ✅ Test CCXT with one exchange (e.g., Backpack if supported)
4. ✅ Create proof-of-concept adapter wrapper
5. ✅ Document API differences and mapping requirements

**Deliverables**:
- Exchange support matrix
- API mapping document
- POC adapter implementation

### Phase 2: Adapter Layer Development (2-4 weeks)

**Tasks**:
1. Create `CCXTAdapter` class implementing `BaseExchangeClient`
2. Implement method mapping (CCXT → our interface)
3. Handle credential mapping
4. Integrate proxy support
5. Add error handling and retry logic
6. Unit tests for adapter layer

**Deliverables**:
- `exchange_clients/ccxt_adapter.py`
- Test suite for adapter
- Documentation

### Phase 3: Custom Exchange Implementations (4-8 weeks)

**Tasks**:
1. Implement custom CCXT exchange classes for unsupported exchanges:
   - Lighter Network
   - Aster
   - Paradex (if not supported)
   - EdgeX
   - GRVT
2. Extend `ccxt.Exchange` base class
3. Implement required CCXT methods
4. Test each custom exchange implementation

**Deliverables**:
- Custom CCXT exchange classes
- Test suite for each exchange

### Phase 4: Incremental Migration (6-12 weeks)

**Tasks**:
1. Migrate one exchange at a time (start with simplest)
2. Update `ExchangeFactory` to support CCXT adapters
3. Update strategies to use CCXT adapters (should be transparent)
4. Extensive testing in staging
5. Monitor production metrics
6. Keep old implementation as fallback

**Migration Order** (suggested):
1. **Backpack** (if CCXT supports) - Simplest, good test case
2. **Paradex** (if CCXT supports) - Well-tested in our system
3. **EdgeX/GRVT** - Partial implementations, lower risk
4. **Aster** - Full implementation, medium complexity
5. **Lighter** - Most complex, reference implementation, migrate last

**Deliverables**:
- Migrated exchange clients
- Updated factory
- Migration test results
- Rollback plan

### Phase 5: WebSocket & Funding Rate Migration (2-4 weeks)

**Tasks**:
1. Evaluate CCXT WebSocket support for each exchange
2. Migrate funding rate adapters to use CCXT methods where available
3. Keep custom WebSocket implementations if needed
4. Update funding rate service to use CCXT adapters

**Deliverables**:
- Updated WebSocket integration
- Migrated funding rate adapters

### Phase 6: Cleanup & Optimization (1-2 weeks)

**Tasks**:
1. Remove old exchange client implementations
2. Update documentation
3. Optimize adapter layer
4. Final testing and validation

**Deliverables**:
- Clean codebase
- Updated documentation
- Performance benchmarks

## Implementation Considerations

### Adapter Pattern

Create a `CCXTAdapter` that wraps CCXT clients and implements our `BaseExchangeClient` interface:

```python
class CCXTAdapter(BaseExchangeClient):
    """
    Adapter that wraps CCXT exchange clients to match our BaseExchangeClient interface.
    """
    
    def __init__(self, config: Dict[str, Any], ccxt_exchange_id: str):
        super().__init__(config)
        # Initialize CCXT exchange
        exchange_class = getattr(ccxt, ccxt_exchange_id)
        self.ccxt_client = exchange_class({
            'apiKey': config.get('api_key'),
            'secret': config.get('secret'),
            # ... map other credentials
        })
        self.ccxt_client.load_markets()
    
    async def place_limit_order(self, symbol: str, side: str, quantity: Decimal, price: Decimal):
        # Map to CCXT API
        order = await self.ccxt_client.create_limit_order(
            symbol=self._normalize_symbol(symbol),
            side=side,
            amount=float(quantity),
            price=float(price)
        )
        # Map back to our OrderResult format
        return self._map_order_result(order)
    
    # ... implement all BaseExchangeClient methods
```

### Custom CCXT Exchange Classes

For unsupported exchanges, create custom CCXT exchange classes:

```python
import ccxt

class lighter(ccxt.Exchange):
    """
    Custom CCXT exchange class for Lighter Network.
    """
    
    def describe(self):
        return {
            'id': 'lighter',
            'name': 'Lighter Network',
            'countries': ['US'],
            'has': {
                'createOrder': True,
                'fetchBalance': True,
                'fetchPositions': True,
                # ... define capabilities
            },
            'urls': {
                'api': {
                    'public': 'https://api.lighter.xyz',
                    'private': 'https://api.lighter.xyz',
                },
            },
        }
    
    async def create_order(self, symbol, type, side, amount, price=None, params={}):
        # Implement using Lighter API
        # Can reuse existing Lighter client code
        pass
    
    # ... implement other required methods
```

### Hybrid Approach

Keep custom implementations where CCXT doesn't provide value:

- **WebSocket Managers**: Keep custom implementations for real-time order book
- **Funding Adapters**: Migrate gradually, keep custom where needed
- **REST API Calls**: Use CCXT (order placement, position queries, balances)

## Success Criteria

### Must Have
- ✅ All 6 exchanges functional via CCXT adapters
- ✅ All existing strategies work without modification
- ✅ Performance equal or better than current implementation
- ✅ No loss of functionality (WebSocket, funding rates, etc.)
- ✅ Comprehensive test coverage

### Nice to Have
- ✅ Reduced codebase size (fewer lines to maintain)
- ✅ Easier to add new exchanges (if CCXT supports)
- ✅ Better error messages and debugging
- ✅ Community support for exchange API changes

## Decision Factors

### When to Proceed

Consider migration when:
1. **Maintenance Burden**: Exchange API changes become too frequent/time-consuming
2. **Exchange Expansion**: Want to add CEX exchanges that CCXT supports
3. **Team Growth**: Have resources for migration project
4. **CCXT Maturity**: CCXT adds support for more of our exchanges
5. **Stability Issues**: Current implementations have recurring bugs

### When to Defer

Defer migration if:
1. **Current System Works**: No major issues with existing implementations
2. **Limited Resources**: Don't have time for 3-6 month migration project
3. **CCXT Gaps**: CCXT doesn't support critical exchanges
4. **Custom Features**: Heavy reliance on custom features CCXT doesn't support
5. **Production Risk**: Can't afford downtime or bugs during migration

## Alternative Approaches

### Option 1: Full Migration (Recommended if proceeding)
- Migrate all exchanges to CCXT
- Remove custom implementations
- **Pros**: Clean codebase, full CCXT benefits
- **Cons**: High effort, migration risk

### Option 2: Hybrid Approach (Lower Risk)
- Use CCXT for supported exchanges (if any)
- Keep custom implementations for unsupported exchanges
- **Pros**: Lower risk, incremental benefits
- **Cons**: Maintain two systems, partial benefits

### Option 3: CCXT-Inspired Refactoring (No Migration)
- Refactor current implementations to match CCXT patterns
- Keep custom code but improve structure
- **Pros**: No migration risk, better code quality
- **Cons**: Still maintain all custom code

### Option 4: Wait and See (Current Approach)
- Monitor CCXT development
- Re-evaluate when CCXT adds more DEX support
- **Pros**: No effort, no risk
- **Cons**: Miss potential benefits

## Recommendations

### Short Term (Next 6 Months)
- **Status**: ⏳ **DEFER** - Focus on strategy improvements and current system stability
- **Reasoning**: 
  - Current system works well
  - CCXT doesn't support most of our exchanges
  - Migration effort is significant
  - Better ROI on strategy/execution improvements

### Medium Term (6-12 Months)
- **Status**: 🔍 **MONITOR** - Watch CCXT development and our maintenance burden
- **Actions**:
  - Check CCXT releases for new DEX support
  - Track time spent on exchange API maintenance
  - Evaluate if adding CEX exchanges becomes desirable
  - Consider POC for one exchange if CCXT adds support

### Long Term (12+ Months)
- **Status**: 📋 **RE-EVALUATE** - Reassess based on:
  - CCXT exchange support growth
  - Maintenance burden of current system
  - Team capacity for migration
  - Business needs (CEX expansion, etc.)

## References

- **CCXT Repository**: https://github.com/ccxt/ccxt
- **CCXT Documentation**: https://docs.ccxt.com
- **CCXT Python Examples**: https://github.com/ccxt/ccxt/tree/master/examples/py
- **Current Exchange Clients**: `exchange_clients/`
- **Base Client Interface**: `exchange_clients/base_client.py`
- **Exchange Factory**: `exchange_clients/factory.py`

## Notes

- This migration is **not urgent** - current system is functional
- Migration would be a **significant undertaking** (3-6 months)
- Benefits are **long-term** (reduced maintenance, broader exchange support)
- Risk is **medium-high** (production trading system)
- Consider **incremental approach** if proceeding
- **Hybrid approach** may be best initial step (CCXT + custom WebSockets)

---

**Created**: 2025-11-20  
**Last Updated**: 2025-11-20  
**Status**: ⏳ Future Consideration  
**Priority**: Low (not blocking current development)

