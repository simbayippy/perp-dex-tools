from datetime import datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from unittest.mock import AsyncMock, MagicMock

from strategies.implementations.funding_arbitrage.config import FundingArbConfig
from strategies.implementations.funding_arbitrage.models import FundingArbPosition
from strategies.implementations.funding_arbitrage.strategy import FundingArbitrageStrategy


@pytest.fixture
def strategy(monkeypatch):
    monkeypatch.setattr(
        "strategies.implementations.funding_arbitrage.strategy.OpportunityFinder",
        lambda **kwargs: MagicMock(),
    )
    monkeypatch.setattr(
        "strategies.implementations.funding_arbitrage.strategy.FundingRateRepository",
        lambda _db: MagicMock(),
    )
    mock_db = MagicMock()
    mock_db.connect = AsyncMock()
    mock_db.disconnect = AsyncMock()
    monkeypatch.setattr(
        "strategies.implementations.funding_arbitrage.strategy.database", mock_db
    )

    config = FundingArbConfig(
        exchanges=["lighter", "aster"],
        database_url="sqlite://",
    )
    exchange_clients = {"lighter": MagicMock(), "aster": MagicMock()}
    strat = FundingArbitrageStrategy(config, exchange_clients)
    strat.dashboard_service.enabled = False
    strat.control_server = None
    return strat


@pytest.mark.asyncio
async def test_pause_resume_commands(strategy):
    result = await strategy._handle_dashboard_command({"type": "pause_strategy"})
    assert result["ok"]
    assert strategy._manual_pause is True
    assert not await strategy.should_execute(None)

    result = await strategy._handle_dashboard_command({"type": "resume_strategy"})
    assert result["ok"]
    assert strategy._manual_pause is False
    assert await strategy.should_execute(None)


@pytest.mark.asyncio
async def test_close_position_command(strategy):
    position_id = uuid4()
    position = FundingArbPosition(
        id=position_id,
        symbol="BTC",
        long_dex="lighter",
        short_dex="aster",
        size_usd=Decimal("1000"),
        entry_long_rate=Decimal("0.0001"),
        entry_short_rate=Decimal("0.0002"),
        entry_divergence=Decimal("0.0003"),
        opened_at=datetime.utcnow(),
    )
    strategy.position_manager._positions[position_id] = position

    close_mock = AsyncMock()
    strategy._close_position = close_mock  # type: ignore

    result = await strategy._handle_dashboard_command(
        {"type": "close_position", "position_id": str(position_id)}
    )

    assert result["ok"] is True
    close_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_close_position_invalid(strategy):
    result = await strategy._handle_dashboard_command(
        {"type": "close_position", "position_id": str(uuid4())}
    )
    assert result["ok"] is False
