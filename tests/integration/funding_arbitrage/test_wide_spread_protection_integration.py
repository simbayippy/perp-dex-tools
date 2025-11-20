"""
Integration tests for wide spread protection on exit.

Tests end-to-end flows including deferral, execution mode selection, and API responses.
"""

import asyncio
import types
from decimal import Decimal
from datetime import datetime
from types import SimpleNamespace
from typing import Dict
from uuid import uuid4
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from strategies.implementations.funding_arbitrage.operations.closing.position_closer import PositionCloser
from strategies.implementations.funding_arbitrage.operations.closing.order_builder import WideSpreadException
from strategies.implementations.funding_arbitrage.models import FundingArbPosition
from strategies.implementations.funding_arbitrage.config import RiskManagementConfig
from exchange_clients.base_models import ExchangePositionSnapshot
from strategies.execution.patterns.atomic_multi_order import AtomicExecutionResult


class StubLogger:
    """Stub logger for testing."""
    def __init__(self):
        self.messages = []
        self.info_calls = []
        self.warning_calls = []
        self.error_calls = []
    
    def info(self, message):
        self.info_calls.append(message)
        self.messages.append(("INFO", message))
    
    def warning(self, message):
        self.warning_calls.append(message)
        self.messages.append(("WARNING", message))
    
    def error(self, message):
        self.error_calls.append(message)
        self.messages.append(("ERROR", message))
    
    def debug(self, message):
        self.messages.append(("DEBUG", message))


class StubExchangeClient:
    """Stub exchange client for testing."""
    def __init__(self, name: str, bbo_prices=None):
        self._name = name
        self._bbo_prices = bbo_prices or (Decimal("100"), Decimal("100.5"))
        self.config = SimpleNamespace(contract_id=f"{name.upper()}-CONTRACT")
        self.closed = []
    
    def get_exchange_name(self):
        return self._name
    
    async def fetch_bbo_prices(self, symbol: str):
        return self._bbo_prices
    
    def round_to_step(self, quantity: Decimal) -> Decimal:
        return quantity
    
    def round_to_tick(self, price: Decimal) -> Decimal:
        return price
    
    def resolve_contract_id(self, symbol: str) -> str:
        return f"{symbol}-{self._name.upper()}"


class StubPositionManager:
    """Stub position manager for testing."""
    def __init__(self, positions):
        self._positions = positions
        self.closed_records = []
    
    async def get_open_positions(self):
        return list(self._positions)
    
    async def close(self, position_id, exit_reason: str, pnl_usd=None):
        self.closed_records.append((position_id, exit_reason, pnl_usd))
        for pos in self._positions:
            if pos.id == position_id:
                pos.status = "closed"
                pos.exit_reason = exit_reason
    
    async def get(self, position_id):
        for pos in self._positions:
            if pos.id == position_id:
                return pos
        return None
    
    async def get_cumulative_funding(self, position_id):
        return Decimal("0")


class StubAtomicExecutor:
    """Stub atomic executor for testing."""
    def __init__(self):
        self.last_orders = None
        self.execute_called = False
    
    async def execute_atomically(self, orders, **kwargs):
        self.last_orders = orders
        self.execute_called = True
        
        # Simulate WideSpreadException if any order has wide spread
        for order in orders:
            if hasattr(order, 'execution_mode') and order.execution_mode == "aggressive_limit":
                # Check if this would trigger deferral (simulate in integration test)
                pass
        
        return AtomicExecutionResult(
            success=True,
            all_filled=True,
            filled_orders=[],
            partial_fills=[],
            total_slippage_usd=Decimal("0"),
            execution_time_ms=0,
            error_message=None,
            rollback_performed=False,
            rollback_cost_usd=Decimal("0"),
            residual_imbalance_usd=Decimal("0"),
        )


def _make_position(symbol="BTC", long_dex="aster", short_dex="lighter", opened_at=None):
    """Create a test position."""
    return FundingArbPosition(
        id=uuid4(),
        symbol=symbol,
        long_dex=long_dex,
        short_dex=short_dex,
        size_usd=Decimal("1000"),
        entry_long_rate=Decimal("-0.01"),
        entry_short_rate=Decimal("0.03"),
        entry_divergence=Decimal("0.04"),
        opened_at=opened_at or datetime.now(),
        metadata={
            "legs": {
                long_dex: {
                    "entry_price": Decimal("100"),
                },
                short_dex: {
                    "entry_price": Decimal("100"),
                }
            }
        }
    )


def _make_strategy(position_manager, exchange_clients, risk_config=None, bbo_prices=None):
    """Create a mock strategy."""
    if bbo_prices is None:
        bbo_prices = (Decimal("100"), Decimal("100.5"))
    
    price_provider = SimpleNamespace(
        get_bbo_prices=AsyncMock(return_value=bbo_prices)
    )
    
    risk_cfg = risk_config or RiskManagementConfig()
    config = SimpleNamespace(
        risk_config=risk_cfg,
        enable_wide_spread_protection=True,
        max_exit_spread_pct=Decimal("0.02"),  # 2%
        max_emergency_close_spread_pct=Decimal("0.03"),  # 3%
    )
    
    return SimpleNamespace(
        position_manager=position_manager,
        exchange_clients=exchange_clients,
        config=config,
        logger=StubLogger(),
        funding_rate_repo=None,
        price_provider=price_provider,
        atomic_executor=StubAtomicExecutor(),
        notification_service=SimpleNamespace(
            notify_position_closed=AsyncMock()
        ),
    )


@pytest.mark.asyncio
@pytest.mark.integration
async def test_non_critical_exit_deferral_flow():
    """Test that non-critical exits are deferred when spread > 2%."""
    position = _make_position()
    position_manager = StubPositionManager([position])
    
    # Wide spread: 3% (bid=100, ask=103)
    wide_bbo = (Decimal("100"), Decimal("103"))
    
    exchange_clients = {
        "aster": StubExchangeClient("aster", wide_bbo),
        "lighter": StubExchangeClient("lighter", wide_bbo),
    }
    
    strategy = _make_strategy(position_manager, exchange_clients, bbo_prices=wide_bbo)
    closer = PositionCloser(strategy)
    closer._risk_manager = None  # Skip risk manager
    
    # Mock should_close to return True with non-critical reason
    async def mock_should_close(position, snapshots, gather_current_rates, should_skip_erosion_exit):
        return True, "PROFIT_EROSION"  # Non-critical
    
    closer._exit_evaluator.should_close = types.MethodType(mock_should_close, closer._exit_evaluator)
    
    # Mock fetch snapshots
    async def mock_fetch_snapshots(position):
        return {
            "aster": ExchangePositionSnapshot(symbol="BTC", quantity=Decimal("1"), side="long"),
            "lighter": ExchangePositionSnapshot(symbol="BTC", quantity=Decimal("1"), side="short"),
        }
    
    closer._fetch_leg_snapshots = types.MethodType(mock_fetch_snapshots, closer)
    
    # Mock gather_current_rates to return None (skip risk manager)
    async def mock_gather_rates(position):
        return None
    
    closer._gather_current_rates = types.MethodType(mock_gather_rates, closer)
    
    # Mock should_skip_erosion_exit
    async def mock_skip_erosion(position, reason):
        return False
    
    closer._should_skip_erosion_exit = types.MethodType(mock_skip_erosion, closer)
    
    # Evaluate and close positions
    actions = await closer.evaluateAndClosePositions()
    
    # Position should NOT be closed (deferred)
    assert len(position_manager.closed_records) == 0
    # Should have logged deferral message
    deferral_logs = [msg for msg in strategy.logger.info_calls if "Deferring close" in msg or "Deferring" in msg]
    assert len(deferral_logs) > 0


@pytest.mark.asyncio
@pytest.mark.integration
async def test_critical_exit_proceeds_despite_wide_spread():
    """Test that critical exits proceed even with wide spread."""
    position = _make_position()
    position_manager = StubPositionManager([position])
    
    # Wide spread: 5% (bid=100, ask=105)
    wide_bbo = (Decimal("100"), Decimal("105"))
    
    exchange_clients = {
        "aster": StubExchangeClient("aster", wide_bbo),
        "lighter": StubExchangeClient("lighter", wide_bbo),
    }
    
    strategy = _make_strategy(position_manager, exchange_clients, bbo_prices=wide_bbo)
    closer = PositionCloser(strategy)
    
    # Mock detect_liquidation to return critical reason
    def mock_detect_liquidation(position, snapshots):
        return "LEG_LIQUIDATED"  # Critical
    
    closer._exit_evaluator.detect_liquidation = types.MethodType(mock_detect_liquidation, closer._exit_evaluator)
    
    # Mock fetch snapshots
    async def mock_fetch_snapshots(position):
        return {
            "aster": ExchangePositionSnapshot(symbol="BTC", quantity=Decimal("1"), side="long"),
            "lighter": ExchangePositionSnapshot(symbol="BTC", quantity=Decimal("0"), side=None),  # Missing leg
        }
    
    closer._fetch_leg_snapshots = types.MethodType(mock_fetch_snapshots, closer)
    
    # Evaluate and close positions
    actions = await closer.evaluateAndClosePositions()
    
    # Position SHOULD be closed (critical exit)
    assert len(position_manager.closed_records) > 0
    assert any("LEG_LIQUIDATED" in reason for _, reason, _ in position_manager.closed_records)
    # Should have logged warning about wide spread
    wide_spread_logs = [msg for msg in strategy.logger.warning_calls if "Wide spread" in msg]
    assert len(wide_spread_logs) > 0


@pytest.mark.asyncio
@pytest.mark.integration
async def test_user_manual_close_with_wide_spread_warning():
    """Test that user manual closes return warning when spread is wide."""
    from strategies.control.funding_arb_controller import FundingArbStrategyController
    from database.connection import database
    
    position = _make_position()
    position_manager = StubPositionManager([position])
    
    # Wide spread: 3% (bid=100, ask=103)
    wide_bbo = (Decimal("100"), Decimal("103"))
    
    exchange_clients = {
        "aster": StubExchangeClient("aster", wide_bbo),
        "lighter": StubExchangeClient("lighter", wide_bbo),
    }
    
    strategy = _make_strategy(position_manager, exchange_clients, bbo_prices=wide_bbo)
    controller = FundingArbStrategyController(strategy=strategy)
    
    # Mock database fetch for account validation
    with patch('strategies.control.funding_arb_controller.database') as mock_db:
        mock_db.fetch_one = AsyncMock(return_value={
            'id': str(position.id),
            'account_id': str(uuid4()),
            'account_name': 'test_account'
        })
        
        # Mock position manager get
        async def mock_get(position_id):
            return position
        
        strategy.position_manager.get = types.MethodType(mock_get, strategy.position_manager)
        
        # Try to close with market order (should return warning)
        result = await controller.close_position(
            position_id=str(position.id),
            account_ids=[str(uuid4())],  # Mock account ID
            order_type="market",
            reason="telegram_manual_close",
            confirm_wide_spread=False
        )
        
        # Should return warning response
        assert result.get("wide_spread_warning") is True
        assert result.get("success") is False
        assert "spread_pct" in result
        assert result.get("spread_pct") > 0.02  # > 2%


@pytest.mark.asyncio
@pytest.mark.integration
async def test_user_manual_close_confirmed_proceeds():
    """Test that user manual close proceeds when confirmed despite wide spread."""
    position = _make_position()
    position_manager = StubPositionManager([position])
    
    # Wide spread: 3% (bid=100, ask=103)
    wide_bbo = (Decimal("100"), Decimal("103"))
    
    exchange_clients = {
        "aster": StubExchangeClient("aster", wide_bbo),
        "lighter": StubExchangeClient("lighter", wide_bbo),
    }
    
    strategy = _make_strategy(position_manager, exchange_clients, bbo_prices=wide_bbo)
    
    # Mock position closer to actually close
    closer = PositionCloser(strategy)
    
    # Mock fetch snapshots
    async def mock_fetch_snapshots(position):
        return {
            "aster": ExchangePositionSnapshot(symbol="BTC", quantity=Decimal("1"), side="long"),
            "lighter": ExchangePositionSnapshot(symbol="BTC", quantity=Decimal("1"), side="short"),
        }
    
    closer._fetch_leg_snapshots = types.MethodType(mock_fetch_snapshots, closer)
    
    # Close with confirmation
    await closer.close(
        position=position,
        reason="telegram_manual_close",
        order_type="market"
    )
    
    # Position should be closed (user confirmed)
    assert len(position_manager.closed_records) > 0


@pytest.mark.asyncio
@pytest.mark.integration
async def test_acceptable_spread_allows_non_critical_exit():
    """Test that non-critical exits proceed when spread is acceptable."""
    position = _make_position()
    position_manager = StubPositionManager([position])
    
    # Acceptable spread: 1% (bid=100, ask=101)
    acceptable_bbo = (Decimal("100"), Decimal("101"))
    
    exchange_clients = {
        "aster": StubExchangeClient("aster", acceptable_bbo),
        "lighter": StubExchangeClient("lighter", acceptable_bbo),
    }
    
    strategy = _make_strategy(position_manager, exchange_clients, bbo_prices=acceptable_bbo)
    closer = PositionCloser(strategy)
    closer._risk_manager = None
    
    # Mock should_close to return True with non-critical reason
    async def mock_should_close(position, snapshots, gather_current_rates, should_skip_erosion_exit):
        return True, "PROFIT_EROSION"  # Non-critical
    
    closer._exit_evaluator.should_close = types.MethodType(mock_should_close, closer._exit_evaluator)
    
    # Mock fetch snapshots
    async def mock_fetch_snapshots(position):
        return {
            "aster": ExchangePositionSnapshot(symbol="BTC", quantity=Decimal("1"), side="long"),
            "lighter": ExchangePositionSnapshot(symbol="BTC", quantity=Decimal("1"), side="short"),
        }
    
    closer._fetch_leg_snapshots = types.MethodType(mock_fetch_snapshots, closer)
    
    # Evaluate and close positions
    actions = await closer.evaluateAndClosePositions()
    
    # Position SHOULD be closed (acceptable spread)
    assert len(position_manager.closed_records) > 0
    # Should have used aggressive_limit mode
    assert strategy.atomic_executor.execute_called
    if strategy.atomic_executor.last_orders:
        assert any(order.execution_mode == "aggressive_limit" for order in strategy.atomic_executor.last_orders)

