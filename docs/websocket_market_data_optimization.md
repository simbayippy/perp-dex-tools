# WebSocket Market Data Stream Optimization

**Status**: Design Proposal  
**Created**: 2025-10-24  
**Author**: Architecture Review  

## Overview

This document outlines an optimization strategy for managing WebSocket market data subscriptions across exchange clients. The goal is to reduce unnecessary bandwidth and processing overhead by pausing market data streams when they are not actively needed, while maintaining critical user data streams (order updates, liquidations) continuously.

---

## Problem Statement

### Current Behavior

Exchange clients currently maintain continuous WebSocket subscriptions to market data streams (BBO, order book depth) throughout their lifecycle, even when no trading activity is occurring.

**Observed Impact (Aster Exchange)**:
- **Depth stream**: Receives full order book snapshots every 100ms
- **Message volume**: ~10 updates/second = ~36,000 messages/hour
- **Bandwidth**: ~1MB/sec = ~3.6GB/hour = ~2.6TB/month
- **CPU overhead**: JSON parsing + Decimal conversions for each update
- **Memory overhead**: Order book replacement 10 times/second

### When Market Data Is Actually Needed

Market data (BBO, order book depth) is only required during:

1. **Position Opening** (5-30 seconds)
   - Price validation
   - Liquidity analysis
   - Order execution

2. **Position Closing** (5-30 seconds)
   - Exit price calculation
   - Liquidity checks
   - Order execution

3. **Position Monitoring** (optional, for advanced exit strategies)
   - Currently NOT used by the funding arbitrage strategy

**Key Insight**: Market data streams can be idle 95%+ of the time between trading operations.

### What Must Stay Active

**Critical streams that MUST remain continuously subscribed**:
- ✅ Order update notifications (order lifecycle tracking)
- ✅ Liquidation events (risk management)
- ✅ Account balance updates (if supported)

---

## Exchange Architecture Analysis

### Exchange WebSocket Patterns

Different exchanges use different WebSocket architectures, which affects whether selective pausing is possible:

#### **Aster** - Multiple Separate Streams ✅ Can Optimize
```python
self.websocket          # User data (orders, liquidations) - KEEP ON
self._book_ticker_ws    # BBO updates - CAN PAUSE
self._depth_ws          # Order book depth - CAN PAUSE
```

**Characteristics**:
- 3 independent WebSocket connections
- Can pause market data while keeping user data active
- **Perfect candidate for optimization**

**Current overhead when idle**:
- Book ticker: ~5-10 updates/sec
- Depth stream: ~10 updates/sec (100ms interval)

---

#### **Lighter** - Unified Stream ❌ Cannot Optimize
```python
self.ws                 # Everything: orders, book, liquidations - ALL IN ONE
```

**Characteristics**:
- Single WebSocket connection carries all data
- Cannot pause market data without losing order updates
- **Must stay subscribed continuously**

**Verdict**: Keep current behavior (unified architecture prevents selective pausing)

---

#### **Backpack** - Dual Streams ✅ Can Optimize
```python
self._account_ws        # Private: orders, account - KEEP ON
self._depth_ws          # Public: market data - CAN PAUSE
```

**Characteristics**:
- 2 independent WebSocket connections
- Can pause market data while keeping account stream active
- **Good candidate for optimization**

---

## Proposed Solution

### Architecture: Optional Methods Pattern

Add pause/resume capabilities to `BaseWebSocketManager` as **optional methods with no-op defaults**. This allows:
- Exchanges with separate streams to implement optimization
- Exchanges with unified streams to use safe default (no-op)
- Strategy code to call uniformly without conditionals

### Design Principles

1. **Safe Defaults**: Base class provides no-op implementations
2. **Opt-In**: Exchanges implement only if their architecture supports it
3. **Non-Breaking**: Existing exchanges continue working without changes
4. **Uniform Interface**: Strategy code calls methods uniformly

---

## Implementation Plan

### Phase 1: Base Class Interface

**File**: `exchange_clients/base_websocket.py`

Add two optional methods to `BaseWebSocketManager`:

```python
class BaseWebSocketManager(ABC):
    # ... existing methods ...
    
    async def pause_market_data_streams(self) -> None:
        """
        Pause market data streams (BBO, depth) while keeping user data active.
        
        Optional method - only implement if exchange architecture supports it.
        Default implementation is no-op (safe for exchanges with unified streams).
        
        When implemented, should:
        - Cancel background tasks for BBO/book ticker streams
        - Cancel background tasks for order book depth streams
        - Keep user data stream (orders, liquidations) active
        - Log pause action for monitoring
        
        Note: This is a performance optimization to reduce bandwidth and CPU
        overhead when market data is not actively needed.
        """
        pass  # No-op by default
    
    async def resume_market_data_streams(self, symbol: str) -> None:
        """
        Resume market data streams for a specific symbol.
        
        Optional method - only implement if exchange architecture supports it.
        Default implementation is no-op (safe for exchanges with unified streams).
        
        Args:
            symbol: Trading symbol to subscribe to (normalized format)
        
        When implemented, should:
        - Restart BBO/book ticker stream for the symbol
        - Restart order book depth stream for the symbol
        - Wait briefly for initial data to populate
        - Log resume action for monitoring
        
        Note: This is called before order execution to ensure fresh market data.
        """
        pass  # No-op by default
```

**Rationale**: 
- No-op defaults make it safe for all exchanges
- Clear documentation on when/how to implement
- Type hints maintain interface consistency

---

### Phase 2: Exchange-Specific Implementations

#### **Aster** (`exchange_clients/aster/websocket_manager.py`)

```python
async def pause_market_data_streams(self) -> None:
    """Pause BBO and depth streams while keeping user data stream active."""
    # Cancel book ticker task
    if self._book_ticker_task and not self._book_ticker_task.done():
        self._book_ticker_task.cancel()
        try:
            await self._book_ticker_task
        except asyncio.CancelledError:
            pass
        self._book_ticker_ws = None
        self._current_book_ticker_symbol = None
    
    # Cancel depth stream task
    if self._depth_task and not self._depth_task.done():
        self._depth_task.cancel()
        try:
            await self._depth_task
        except asyncio.CancelledError:
            pass
        self._depth_ws = None
        self._current_depth_symbol = None
        self.order_book_ready = False
    
    if self.logger:
        self.logger.debug("[ASTER] 📊 Market data streams paused (user data still active)")

async def resume_market_data_streams(self, symbol: str) -> None:
    """Resume BBO and depth streams for a specific symbol."""
    if not self.running:
        if self.logger:
            self.logger.warning("[ASTER] Cannot resume streams - WebSocket not running")
        return
    
    # Start book ticker stream
    await self.start_book_ticker(symbol)
    
    # Start depth stream
    self._current_depth_symbol = symbol
    self._depth_task = asyncio.create_task(self._connect_depth_stream(symbol))
    
    # Brief wait for initial data
    await asyncio.sleep(0.5)
    
    if self.logger:
        self.logger.debug(f"[ASTER] 📊 Market data streams resumed for {symbol}")
```

**Impact**:
- Saves ~36,000 messages/hour when idle
- Reduces bandwidth by ~3.6GB/hour
- Minimal latency impact (~500ms to resume)

---

#### **Backpack** (`exchange_clients/backpack/websocket_manager.py`)

```python
async def pause_market_data_streams(self) -> None:
    """Pause depth stream while keeping account stream active."""
    if self._depth_task and not self._depth_task.done():
        self._depth_task.cancel()
        try:
            await self._depth_task
        except asyncio.CancelledError:
            pass
        self._depth_task = None
        self._depth_ready_event.clear()
    
    if self.logger:
        self.logger.debug("[BACKPACK] 📊 Market data stream paused (account stream still active)")

async def resume_market_data_streams(self, symbol: str) -> None:
    """Resume depth stream for a specific symbol."""
    if not self.running:
        if self.logger:
            self.logger.warning("[BACKPACK] Cannot resume stream - WebSocket not running")
        return
    
    # Update symbol if needed
    if self.symbol != symbol:
        self.update_symbol(symbol)
    
    # Restart depth stream task
    if self.depth_fetcher:
        self._depth_task = asyncio.create_task(
            self._run_depth_stream(), 
            name="backpack-depth-ws"
        )
        
        # Brief wait for initial data
        await self.wait_for_order_book(timeout=2.0)
    
    if self.logger:
        self.logger.debug(f"[BACKPACK] 📊 Market data stream resumed for {symbol}")
```

---

#### **Lighter** (`exchange_clients/lighter/websocket_manager.py`)

**No implementation needed** - uses base class no-op defaults.

Lighter's unified stream architecture means it must stay subscribed continuously. The no-op default is the correct behavior.

---

### Phase 3: Strategy Integration

Update position opening and closing operations to pause/resume market data.

#### **Position Opener** (`strategies/implementations/funding_arbitrage/operations/position_opener.py`)

```python
async def _prepare_websocket_feeds(self, exchange_client: BaseExchangeClient, symbol: str) -> None:
    """Ensure exchange WebSocket streams are aligned with the symbol we intend to trade."""
    strategy = self._strategy

    try:
        # Resume market data streams (no-op for exchanges that don't support it)
        ws_manager = exchange_client.ws_manager
        if ws_manager:
            await ws_manager.resume_market_data_streams(symbol)
        
        # Ensure feed is aligned with symbol
        await exchange_client.ensure_market_feed(symbol)

        # Wait for data to populate
        if ws_manager:
            await self._await_ws_snapshot(ws_manager)
            
    except Exception as exc:
        strategy.logger.log(
            f"⚠️ [{exchange_client.get_exchange_name().upper()}] WebSocket prep error: {exc}",
            "DEBUG",
        )

async def open(self, opportunity) -> Optional[FundingArbPosition]:
    """Open a new funding arbitrage position."""
    # ... existing code ...
    
    try:
        # Execute trade
        result = await self._execute_trade(opportunity)
        
        # ... existing position creation code ...
        
    finally:
        # Pause market data streams after execution
        # (only affects exchanges that implement it)
        for client in [long_client, short_client]:
            ws_manager = getattr(client, 'ws_manager', None)
            if ws_manager:
                try:
                    await ws_manager.pause_market_data_streams()
                except Exception as exc:
                    strategy.logger.log(
                        f"⚠️ Error pausing market data for {client.get_exchange_name()}: {exc}",
                        "DEBUG"
                    )
```

#### **Position Closer** (`strategies/implementations/funding_arbitrage/operations/position_closer.py`)

```python
async def _ensure_market_feed_once(self, client, symbol: str) -> None:
    """Prepare the client's websocket feed for the target symbol once per session run."""
    exchange_name = client.get_exchange_name().upper()
    symbol_key = symbol.upper()
    previous_symbol = self._ws_prepared.get(exchange_name)
    should_prepare = previous_symbol != symbol_key

    ws_manager = getattr(client, "ws_manager", None)
    if not should_prepare and ws_manager is not None:
        ws_symbol = getattr(ws_manager, "symbol", None)
        if isinstance(ws_symbol, str):
            should_prepare = ws_symbol.upper() != symbol_key

    try:
        if ws_manager:
            # Resume market data streams before preparing feed
            await ws_manager.resume_market_data_streams(symbol)
        
        if should_prepare:
            await client.ensure_market_feed(symbol)

        if ws_manager and getattr(ws_manager, "running", False):
            await self._await_ws_snapshot(ws_manager)
            
    except Exception as exc:
        self._strategy.logger.log(
            f"⚠️ [{exchange_name}] WebSocket prep error during close: {exc}",
            "DEBUG",
        )
    else:
        self._ws_prepared[exchange_name] = symbol_key

async def _close_position_legs(self, position, reason: str) -> bool:
    """Close both legs of a position."""
    # ... existing code ...
    
    try:
        # Execute closes
        results = await self._execute_closes(position, close_specs)
        
        # ... existing result processing ...
        
    finally:
        # Pause market data streams after closing
        for dex in [position.long_dex, position.short_dex]:
            if dex in self._strategy.exchange_clients:
                client = self._strategy.exchange_clients[dex]
                ws_manager = getattr(client, 'ws_manager', None)
                if ws_manager:
                    try:
                        await ws_manager.pause_market_data_streams()
                    except Exception as exc:
                        self._strategy.logger.log(
                            f"⚠️ Error pausing market data for {dex}: {exc}",
                            "DEBUG"
                        )
```

---

## Rollout Strategy

### Stage 1: Foundation (Low Risk)
1. Add optional methods to `BaseWebSocketManager`
2. Add unit tests for no-op behavior
3. Deploy with no exchange implementations (verify no regressions)

### Stage 2: Aster Implementation (Medium Risk)
1. Implement pause/resume in Aster WebSocket manager
2. Test in development environment
3. Monitor bandwidth and performance metrics
4. Deploy to production with monitoring

### Stage 3: Strategy Integration (Medium Risk)
1. Update position opener to resume before execution
2. Update position closer to pause after execution
3. Add metrics to track pause/resume calls
4. Monitor for any timing issues or failures

### Stage 4: Backpack Implementation (Low Risk)
1. Implement pause/resume in Backpack WebSocket manager
2. Test and deploy
3. Monitor metrics

---

## Monitoring & Metrics

### Key Metrics to Track

1. **Bandwidth Savings**
   - Before: ~3.6GB/hour per exchange
   - After: Measure actual reduction
   - Target: >90% reduction when idle

2. **Market Data Availability**
   - Time to resume streams
   - Success rate of resume operations
   - Order execution delays (should be <500ms impact)

3. **Stream Health**
   - Pause/resume failure rate
   - User data stream uptime (should be 100%)
   - WebSocket reconnection events

### Logging

Add structured logs for monitoring:
```python
# On pause
logger.debug("[EXCHANGE] 📊 Market data streams paused", extra={
    "exchange": exchange_name,
    "previous_symbol": symbol,
    "timestamp": datetime.utcnow()
})

# On resume
logger.debug("[EXCHANGE] 📊 Market data streams resumed", extra={
    "exchange": exchange_name,
    "symbol": symbol,
    "resume_duration_ms": duration,
    "timestamp": datetime.utcnow()
})
```

---

## Risks & Mitigations

### Risk 1: Market Data Staleness
**Risk**: Paused streams mean stale data if position opened immediately after another.

**Mitigation**:
- Resume operation includes 500ms wait for data refresh
- Strategy code already validates BBO before execution
- Fallback to REST API if WebSocket data unavailable

### Risk 2: Resume Failures
**Risk**: Stream fails to resume, blocking order execution.

**Mitigation**:
- Timeout on resume operation (5 seconds)
- Fallback to REST API if WebSocket unavailable
- Existing retry logic in order execution layer

### Risk 3: Race Conditions
**Risk**: Multiple concurrent operations try to pause/resume simultaneously.

**Mitigation**:
- Use locks if needed (add `self._stream_control_lock`)
- Idempotent operations (safe to call multiple times)
- State tracking prevents double-pause/resume

### Risk 4: Lighter Architecture Change
**Risk**: If Lighter changes to separate streams, we miss optimization opportunity.

**Mitigation**:
- Documentation clearly states current behavior
- Regular architecture reviews
- Easy to add implementation later (interface already exists)

---

## Alternative Approaches Considered

### Alternative 1: Always-On (Current Behavior)
**Pros**: Simple, no complexity
**Cons**: Wastes bandwidth and CPU

**Verdict**: Rejected - unnecessarily wasteful at scale

### Alternative 2: Subscribe Only On-Demand
**Pros**: Maximum efficiency
**Cons**: Complex state management, potential delays

**Verdict**: Rejected - pause/resume is simpler and sufficient

### Alternative 3: Cooldown Period
Keep streams active for N seconds after execution before pausing.

**Pros**: Handles burst trading better
**Cons**: More complex, harder to tune

**Verdict**: Deferred - can add later if needed

### Alternative 4: Per-Exchange Configuration
Make pause/resume behavior configurable per exchange.

**Pros**: Flexibility
**Cons**: More configuration complexity

**Verdict**: Deferred - start with sensible defaults

---

## Success Criteria

### Performance
- ✅ Reduce idle bandwidth by >90% on supported exchanges
- ✅ No measurable impact on order execution latency (<500ms)
- ✅ No increase in order execution failure rate

### Reliability
- ✅ User data streams maintain 100% uptime
- ✅ Market data resume success rate >99%
- ✅ No new WebSocket connection issues

### Code Quality
- ✅ Clean abstraction that doesn't leak exchange details
- ✅ Non-breaking change for existing exchanges
- ✅ Well-documented and testable

---

## Future Enhancements

### Phase 2 Features (If Needed)

1. **Adaptive Cooldown**
   - Keep streams active during high-frequency trading
   - Pause only after N minutes of inactivity

2. **Partial Depth Subscriptions**
   - Subscribe to top 5 levels instead of 20 when full depth not needed
   - Further reduces bandwidth

3. **Smart Resumption**
   - Pre-warm streams when opportunity scan detects likely trades
   - Reduces latency impact

4. **Metrics Dashboard**
   - Real-time monitoring of stream states
   - Bandwidth usage tracking
   - Cost savings calculations

---

## References

- [Exchange Clients Architecture](./PROJECT_STRUCTURE.md#exchange-clients)
- [Funding Arbitrage Strategy](./ARCHITECTURE.md#funding-arbitrage-strategy)
- [WebSocket Best Practices](./ARCHITECTURE.md#websocket-management)

---

## Appendix: Bandwidth Cost Analysis

### Current Monthly Costs (Per Exchange, Idle)

**Aster**:
- Depth stream: 2.6TB/month
- Book ticker: 0.8TB/month
- **Total**: ~3.4TB/month

**Cloud Provider Costs**:
- AWS: ~$153/month (at $0.09/GB after first 10TB)
- GCP: ~$136/month (at $0.08/GB after first 1TB)
- Digital Ocean: ~$0/month (free bandwidth on most plans)

**After Optimization** (95% idle time):
- Depth stream: 0.13TB/month
- Book ticker: 0.04TB/month
- **Total**: ~0.17TB/month
- **Savings**: ~3.2TB/month = **$140+/month in cloud costs**

### ROI Analysis

**Development Time**: ~8 hours
**Monthly Savings**: $140+ (cloud) or bandwidth quota
**Break-even**: Immediate on metered cloud platforms

---

## Document History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 1.0 | 2025-10-24 | Initial design proposal | Architecture Review |


