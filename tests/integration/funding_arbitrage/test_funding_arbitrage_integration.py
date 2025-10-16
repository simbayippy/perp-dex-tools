from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from unittest.mock import AsyncMock

from exchange_clients.base import ExchangePositionSnapshot
from exchange_clients.events import LiquidationEvent
from strategies.execution.patterns.atomic_multi_order import AtomicExecutionResult
from strategies.implementations.funding_arbitrage.models import FundingArbPosition
from strategies.implementations.funding_arbitrage.operations.position_closer import PositionCloser
from strategies.implementations.funding_arbitrage.operations.position_opener import PositionOpener
from strategies.implementations.funding_arbitrage.config import RiskManagementConfig


class StubLogger:
    def __init__(self):
        self.messages = []

    def log(self, message: str, level: str = "INFO"):
        self.messages.append((level, message))


class StubPositionManager:
    def __init__(self, open_positions=None, existing=None):
        self._open_positions = open_positions or []
        self._existing = existing
        self.created = []
        self.updated = []
        self.closed = []

    async def find_open_position(self, *args, **kwargs):
        return self._existing

    async def create(self, position):
        self.created.append(position)
        self._open_positions.append(position)

    async def update(self, position):
        self.updated.append(position)

    async def get_open_positions(self):
        return list(self._open_positions)

    async def close(self, position_id, exit_reason, pnl_usd=None):
        self.closed.append((position_id, exit_reason))
        for pos in self._open_positions:
            if pos.id == position_id:
                pos.status = "closed"
                pos.exit_reason = exit_reason

    async def get(self, position_id):
        for pos in self._open_positions:
            if pos.id == position_id:
                return pos
        return None


class StubExchangeClient:
    def __init__(self, name, snapshot=None):
        self.name = name
        self.config = SimpleNamespace(contract_id="CONTRACT")
        self.snapshot = snapshot
        self.closed = []

    def get_exchange_name(self):
        return self.name

    async def get_contract_attributes(self):
        return "CONTRACT", Decimal("0.01")

    async def get_position_snapshot(self, symbol):
        return self.snapshot

    async def close_position(self, symbol):
        self.closed.append(symbol)


def _atomic_success():
    fill = {
        "fill_price": Decimal("25000"),
        "filled_quantity": Decimal("1"),
        "slippage_usd": Decimal("0.5"),
        "execution_mode_used": "limit",
    }
    return AtomicExecutionResult(
        success=True,
        all_filled=True,
        filled_orders=[fill, fill],
        partial_fills=[],
        total_slippage_usd=Decimal("1"),
        execution_time_ms=100,
        error_message=None,
        rollback_performed=False,
        rollback_cost_usd=None,
        residual_imbalance_usd=Decimal("0"),
    )


def _opportunity(symbol="BTC"):
    return SimpleNamespace(
        symbol=symbol,
        long_dex="aster",
        short_dex="lighter",
        divergence=Decimal("0.02"),
        long_rate=Decimal("-0.01"),
        short_rate=Decimal("0.03"),
    )


def _position(symbol="BTC", long_dex="aster", short_dex="lighter"):
    return FundingArbPosition(
        id=uuid4(),
        symbol=symbol,
        long_dex=long_dex,
        short_dex=short_dex,
        size_usd=Decimal("100"),
        entry_long_rate=Decimal("-0.01"),
        entry_short_rate=Decimal("0.04"),
        entry_divergence=Decimal("0.05"),
        opened_at=datetime.now(),
    )


def _strategy(atomic_result=None, position_manager=None, exchange_clients=None, risk_config=None):
    return SimpleNamespace(
        exchange_clients=exchange_clients or {},
        atomic_executor=SimpleNamespace(
            execute_atomically=AsyncMock(return_value=atomic_result or _atomic_success())
        ),
        fee_calculator=SimpleNamespace(calculate_total_cost=lambda *args, **kwargs: Decimal("1.0")),
        position_manager=position_manager or StubPositionManager(),
        config=SimpleNamespace(
            default_position_size_usd=Decimal("50"),
            risk_config=risk_config or RiskManagementConfig(),
        ),
        logger=StubLogger(),
        failed_symbols=set(),
        funding_rate_repo=None,
        price_provider=SimpleNamespace(
            get_bbo_prices=AsyncMock(return_value=(Decimal("100"), Decimal("101")))
        ),
    )


@pytest.mark.asyncio
async def test_open_position_success_integration(monkeypatch):
    position_manager = StubPositionManager()
    exchange_clients = {
        "aster": StubExchangeClient("aster"),
        "lighter": StubExchangeClient("lighter"),
    }
    strategy = _strategy(position_manager=position_manager, exchange_clients=exchange_clients)
    opener = PositionOpener(strategy)

    monkeypatch.setattr(opener, "_ensure_contract_attributes", AsyncMock(return_value=True))
    monkeypatch.setattr(opener, "_validate_leverage", AsyncMock(return_value=Decimal("40")))

    opportunity = _opportunity()
    result = await opener.open(opportunity)

    assert result is not None
    assert len(position_manager.created) == 1
    assert opportunity.symbol not in strategy.failed_symbols
    strategy.atomic_executor.execute_atomically.assert_awaited()


@pytest.mark.asyncio
async def test_open_position_handles_atomic_failure(monkeypatch):
    failure_result = AtomicExecutionResult(
        success=False,
        all_filled=False,
        filled_orders=[],
        partial_fills=[],
        total_slippage_usd=Decimal("0"),
        execution_time_ms=50,
        error_message="Partial fill",
        rollback_performed=True,
        rollback_cost_usd=Decimal("2"),
        residual_imbalance_usd=Decimal("0.4"),
    )

    position_manager = StubPositionManager()
    exchange_clients = {
        "aster": StubExchangeClient("aster"),
        "lighter": StubExchangeClient("lighter"),
    }
    strategy = _strategy(atomic_result=failure_result, position_manager=position_manager, exchange_clients=exchange_clients)
    opener = PositionOpener(strategy)

    monkeypatch.setattr(opener, "_ensure_contract_attributes", AsyncMock(return_value=True))
    monkeypatch.setattr(opener, "_validate_leverage", AsyncMock(return_value=Decimal("40")))

    opportunity = _opportunity()
    result = await opener.open(opportunity)

    assert result is None
    assert opportunity.symbol in strategy.failed_symbols
    assert not position_manager.created


@pytest.mark.asyncio
async def test_open_position_merges_existing(monkeypatch):
    existing = _position()
    position_manager = StubPositionManager(open_positions=[existing], existing=existing)
    exchange_clients = {
        "aster": StubExchangeClient("aster"),
        "lighter": StubExchangeClient("lighter"),
    }
    strategy = _strategy(position_manager=position_manager, exchange_clients=exchange_clients)
    opener = PositionOpener(strategy)

    monkeypatch.setattr(opener, "_ensure_contract_attributes", AsyncMock(return_value=True))
    monkeypatch.setattr(opener, "_validate_leverage", AsyncMock(return_value=Decimal("20")))

    opportunity = _opportunity()
    result = await opener.open(opportunity)

    assert result is not None
    assert not position_manager.created
    assert position_manager.updated


@pytest.mark.asyncio
async def test_liquidation_event_closes_surviving_leg():
    position = _position()
    position_manager = StubPositionManager(open_positions=[position])

    lighter_client = StubExchangeClient("lighter", snapshot=ExchangePositionSnapshot(symbol="BTC", quantity=Decimal("0")))
    aster_client = StubExchangeClient("aster", snapshot=ExchangePositionSnapshot(symbol="BTC", quantity=Decimal("1")))

    strategy = SimpleNamespace(
        position_manager=position_manager,
        exchange_clients={"lighter": lighter_client, "aster": aster_client},
        logger=StubLogger(),
        config=SimpleNamespace(risk_config=RiskManagementConfig()),
        funding_rate_repo=None,
    )

    closer = PositionCloser(strategy)
    closer._risk_manager = None

    event = LiquidationEvent(
        exchange="lighter",
        symbol="BTCUSDT",
        side="sell",
        quantity=Decimal("0.5"),
        price=Decimal("25000"),
    )

    await closer.handle_liquidation_event(event)

    assert lighter_client.closed == []  # already flat
    assert aster_client.closed == ["BTC"]
    assert position_manager.closed
