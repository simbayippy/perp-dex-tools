"""
Unit tests for RealTimeProfitMonitor.

Tests the WebSocket-driven real-time profit monitoring functionality.
"""

import pytest
import asyncio
import time
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from strategies.implementations.funding_arbitrage.operations.closing.profit_taking.real_time_monitor import RealTimeProfitMonitor
from strategies.implementations.funding_arbitrage.models import FundingArbPosition
from exchange_clients.base_websocket import BBOData
from exchange_clients.base_models import ExchangePositionSnapshot


@pytest.fixture
def mock_strategy():
    """Create a mock strategy for testing."""
    strategy = MagicMock()
    strategy.logger = MagicMock()

    # Mock config
    config = MagicMock()
    config.enable_realtime_profit_taking = True
    config.realtime_profit_check_interval = 1.0
    config.use_cached_snapshots_for_profit_check = True
    strategy.config = config

    # Mock exchange clients with WebSocket managers
    long_client = MagicMock()
    long_ws = MagicMock()
    long_ws.register_bbo_listener = MagicMock()
    long_ws.unregister_bbo_listener = MagicMock()
    long_client.ws_manager = long_ws

    short_client = MagicMock()
    short_ws = MagicMock()
    short_ws.register_bbo_listener = MagicMock()
    short_ws.unregister_bbo_listener = MagicMock()
    short_client.ws_manager = short_ws

    strategy.exchange_clients = {
        "aster": long_client,
        "lighter": short_client,
    }

    # Mock position_closer
    position_closer = MagicMock()
    position_closer._fetch_leg_snapshots = AsyncMock(return_value={})
    strategy.position_closer = position_closer

    # Mock profit_taker
    profit_taker = MagicMock()
    profit_taker.evaluate_and_execute = AsyncMock(return_value=False)
    strategy.profit_taker = profit_taker

    return strategy


@pytest.fixture
def mock_position():
    """Create a mock funding arbitrage position."""
    position = FundingArbPosition(
        id="test-position-123",
        symbol="BTC",
        long_dex="aster",
        short_dex="lighter",
        size_usd=Decimal("1000"),
        entry_long_rate=Decimal("0.0001"),
        entry_short_rate=Decimal("0.0101"),
        entry_divergence=Decimal("0.01"),
        opened_at=datetime.now(timezone.utc),
        status="OPEN",
        metadata={}
    )
    return position


@pytest.mark.asyncio
async def test_register_position(mock_strategy, mock_position):
    """Test registering BBO listeners for a position."""
    checker = RealTimeProfitMonitor(mock_strategy)

    # Register position
    await checker.register_position(mock_position)

    # Verify listeners were registered
    assert mock_position.id in checker._listeners

    # Verify WebSocket managers were called
    long_ws = mock_strategy.exchange_clients["aster"].ws_manager
    short_ws = mock_strategy.exchange_clients["lighter"].ws_manager

    assert long_ws.register_bbo_listener.called
    assert short_ws.register_bbo_listener.called


@pytest.mark.asyncio
async def test_unregister_position(mock_strategy, mock_position):
    """Test unregistering BBO listeners for a position."""
    checker = RealTimeProfitMonitor(mock_strategy)

    # Register then unregister
    await checker.register_position(mock_position)
    await checker.unregister_position(mock_position)

    # Verify listener was removed
    assert mock_position.id not in checker._listeners

    # Verify WebSocket managers were called to unregister
    long_ws = mock_strategy.exchange_clients["aster"].ws_manager
    short_ws = mock_strategy.exchange_clients["lighter"].ws_manager

    assert long_ws.unregister_bbo_listener.called
    assert short_ws.unregister_bbo_listener.called


@pytest.mark.asyncio
async def test_throttling(mock_strategy, mock_position):
    """Test that BBO updates are throttled correctly."""
    checker = RealTimeProfitMonitor(mock_strategy)

    # Register position
    await checker.register_position(mock_position)

    # Get the registered listener
    listener, _ = checker._listeners[mock_position.id]

    # Create a BBO update
    bbo = BBOData(
        symbol="BTC",
        bid=Decimal("50000"),
        ask=Decimal("50001"),
        timestamp=time.time(),
    )

    # Call listener multiple times rapidly
    await listener(bbo)

    # Verify first check happened
    first_check_time = checker._last_check.get(mock_position.id, 0)
    assert first_check_time > 0

    # Call again immediately (should be throttled)
    await listener(bbo)

    # Verify check time didn't update (throttled)
    assert checker._last_check[mock_position.id] == first_check_time


@pytest.mark.asyncio
async def test_symbol_filtering(mock_strategy, mock_position):
    """Test that BBO updates for wrong symbols are filtered."""
    checker = RealTimeProfitMonitor(mock_strategy)

    # Register position for BTC
    await checker.register_position(mock_position)
    listener, _ = checker._listeners[mock_position.id]

    # Create BBO for different symbol
    bbo = BBOData(
        symbol="ETH",
        bid=Decimal("3000"),
        ask=Decimal("3001"),
        timestamp=time.time(),
    )

    # Call listener (should be filtered out)
    await listener(bbo)

    # Verify check didn't happen (no timestamp recorded)
    assert mock_position.id not in checker._last_check


@pytest.mark.asyncio
async def test_profit_opportunity_detection(mock_strategy, mock_position):
    """Test detection and execution of profit opportunities."""
    checker = RealTimeProfitMonitor(mock_strategy)

    # Mock profit_taker to return True (position was closed)
    mock_strategy.profit_taker.evaluate_and_execute = AsyncMock(return_value=True)

    # Mock snapshots
    mock_snapshots = {
        "aster": ExchangePositionSnapshot(
            symbol="BTC",
            quantity=Decimal("0.02"),
            side="long",
            entry_price=Decimal("50000"),
            mark_price=Decimal("50500"),
            exposure_usd=Decimal("1000"),
            unrealized_pnl=Decimal("10"),
            realized_pnl=None,
            funding_accrued=Decimal("2"),
            margin_reserved=Decimal("100"),
            leverage=Decimal("10"),
            liquidation_price=None,
            timestamp=datetime.now(timezone.utc),
            metadata={}
        ),
        "lighter": ExchangePositionSnapshot(
            symbol="BTC",
            quantity=Decimal("-0.02"),
            side="short",
            entry_price=Decimal("50100"),
            mark_price=Decimal("50500"),
            exposure_usd=Decimal("1000"),
            unrealized_pnl=Decimal("-8"),
            realized_pnl=None,
            funding_accrued=Decimal("-2"),
            margin_reserved=Decimal("100"),
            leverage=Decimal("10"),
            liquidation_price=None,
            timestamp=datetime.now(timezone.utc),
            metadata={}
        )
    }
    mock_strategy.position_closer._fetch_leg_snapshots = AsyncMock(return_value=mock_snapshots)

    # Register position
    await checker.register_position(mock_position)
    listener, _ = checker._listeners[mock_position.id]

    # Create BBO update
    bbo = BBOData(
        symbol="BTC",
        bid=Decimal("50500"),
        ask=Decimal("50501"),
        timestamp=time.time(),
    )

    # Call listener
    await listener(bbo)

    # Wait a bit for async execution
    await asyncio.sleep(0.1)

    # Verify profit_taker.evaluate_and_execute was called
    assert mock_strategy.profit_taker.evaluate_and_execute.called
    call = mock_strategy.profit_taker.evaluate_and_execute.call_args
    # Check that position was passed
    assert call.args[0] == mock_position  # Position
    # Check trigger_source in kwargs
    assert call.kwargs.get('trigger_source') == "websocket"


@pytest.mark.asyncio
async def test_cached_snapshots(mock_strategy, mock_position):
    """Test using cached snapshots instead of fresh REST calls."""
    checker = RealTimeProfitMonitor(mock_strategy)

    # Add cached snapshots to position metadata
    cached_time = datetime.now(timezone.utc)
    mock_snapshots = {
        "aster": MagicMock(),
        "lighter": MagicMock(),
    }
    mock_position.metadata["snapshot_cache"] = {
        "timestamp": cached_time.isoformat(),
        "snapshots": mock_snapshots,
    }

    # Get cached snapshots
    result = checker._get_cached_snapshots(mock_position)

    # Verify cached data was returned
    assert result == mock_snapshots


@pytest.mark.asyncio
async def test_stale_cache_rejected(mock_strategy, mock_position):
    """Test that stale cached snapshots are rejected."""
    checker = RealTimeProfitMonitor(mock_strategy)

    # Add old cached snapshots (35 seconds old, threshold is 30s)
    old_time = datetime.now(timezone.utc) - timedelta(seconds=35)
    mock_position.metadata["snapshot_cache"] = {
        "timestamp": old_time.isoformat(),
        "snapshots": {"aster": MagicMock(), "lighter": MagicMock()},
    }

    # Get cached snapshots
    result = checker._get_cached_snapshots(mock_position)

    # Verify stale cache was rejected
    assert result is None


@pytest.mark.asyncio
async def test_cleanup_all(mock_strategy, mock_position):
    """Test cleanup of all registered listeners."""
    checker = RealTimeProfitMonitor(mock_strategy)

    # Register multiple positions
    position2 = FundingArbPosition(
        id="test-position-456",
        symbol="ETH",
        long_dex="aster",
        short_dex="lighter",
        size_usd=Decimal("500"),
        entry_long_rate=Decimal("0.0001"),
        entry_short_rate=Decimal("0.0051"),
        entry_divergence=Decimal("0.005"),
        opened_at=datetime.now(timezone.utc),
        status="OPEN",
        metadata={}
    )

    await checker.register_position(mock_position)
    await checker.register_position(position2)

    # Verify both registered
    assert len(checker._listeners) == 2

    # Cleanup all
    await checker.cleanup_all()

    # Verify all cleaned up
    assert len(checker._listeners) == 0
    assert len(checker._last_check) == 0
    assert len(checker._positions_being_evaluated) == 0


@pytest.mark.asyncio
async def test_symbol_matching():
    """Test symbol matching logic."""
    checker = RealTimeProfitMonitor

    # Test exact match
    assert checker._symbol_matches("BTC", "BTC")
    assert checker._symbol_matches("btc", "BTC")

    # Test with USDT suffix
    assert checker._symbol_matches("BTCUSDT", "BTC")
    assert checker._symbol_matches("BTC", "BTCUSDT")

    # Test partial match
    assert checker._symbol_matches("BTC-PERP", "BTC")

    # Test no match
    assert not checker._symbol_matches("ETH", "BTC")
    assert not checker._symbol_matches(None, "BTC")
    assert not checker._symbol_matches("BTC", None)


@pytest.mark.asyncio
async def test_always_enabled(mock_strategy, mock_position):
    """Test that real-time monitoring is always enabled (no toggle flag)."""
    # Real-time monitoring is now always-on when profit-taking is enabled
    # (No enable_realtime_profit_taking flag - it's always enabled)

    checker = RealTimeProfitMonitor(mock_strategy)

    # Register position - should always work
    await checker.register_position(mock_position)

    # Verify listeners were registered (always-on behavior)
    assert len(checker._listeners) == 1
    assert mock_position.id in checker._listeners

    # Verify both exchanges have listeners registered
    _, exchanges = checker._listeners[mock_position.id]
    assert "aster" in exchanges
    assert "lighter" in exchanges


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
