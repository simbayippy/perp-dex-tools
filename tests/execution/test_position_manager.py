"""Tests for FundingArbPositionManager (database-backed)."""

from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from unittest.mock import AsyncMock

import strategies.implementations.funding_arbitrage.position_manager as pm
from strategies.implementations.funding_arbitrage.models import FundingArbPosition


class StubMapper:
    def __init__(self, mapping):
        self.mapping = mapping.copy()
        self.rev = {v: k for k, v in self.mapping.items()}

    def get_id(self, key):
        return self.mapping.get(key)

    def add(self, id_, name):
        self.mapping[name] = id_
        self.rev[id_] = name

    def get_name(self, id_):
        return self.rev.get(id_)

    def is_loaded(self):
        return True


@pytest.fixture
def db_env(monkeypatch):
    database = SimpleNamespace(
        execute=AsyncMock(),
        fetch_one=AsyncMock(),
        fetch_all=AsyncMock(),
        is_connected=True,
        connect=AsyncMock(),
    )
    symbol_mapper = StubMapper({'BTC': 1})
    dex_mapper = StubMapper({'aster': 10, 'lighter': 11})

    monkeypatch.setattr(pm, "DATABASE_AVAILABLE", True)
    monkeypatch.setattr(pm, "database", database)
    monkeypatch.setattr(pm, "symbol_mapper", symbol_mapper)
    monkeypatch.setattr(pm, "dex_mapper", dex_mapper)
    monkeypatch.setattr(pm, "SymbolRepository", None)
    monkeypatch.setattr(pm, "DEXRepository", None)
    return database, symbol_mapper, dex_mapper


@pytest.fixture
def manager(db_env, monkeypatch):
    manager = pm.FundingArbPositionManager()
    manager._initialized = True
    monkeypatch.setattr(manager, "_ensure_mappers_loaded", AsyncMock())
    return manager


@pytest.fixture
def sample_position():
    return FundingArbPosition(
        id=uuid4(),
        symbol='BTC',
        long_dex='aster',
        short_dex='lighter',
        size_usd=Decimal('100'),
        entry_long_rate=Decimal('-0.01'),
        entry_short_rate=Decimal('0.03'),
        entry_divergence=Decimal('0.04'),
        opened_at=datetime.now(),
        status='open',
    )


@pytest.mark.asyncio
async def test_create_inserts_position(manager, db_env, sample_position):
    database, symbol_mapper, dex_mapper = db_env

    await manager.create(sample_position)

    database.execute.assert_awaited_once()
    args = database.execute.await_args.kwargs
    values = args["values"]
    assert values["symbol_id"] == symbol_mapper.get_id(sample_position.symbol)
    assert values["long_dex_id"] == dex_mapper.get_id(sample_position.long_dex)
    assert values["short_dex_id"] == dex_mapper.get_id(sample_position.short_dex)


@pytest.mark.asyncio
async def test_create_missing_mapping_raises(manager, db_env, sample_position, monkeypatch):
    database, symbol_mapper, dex_mapper = db_env
    symbol_mapper.mapping.pop(sample_position.symbol, None)
    dex_mapper.mapping.pop(sample_position.long_dex, None)

    with pytest.raises(ValueError):
        await manager.create(sample_position)

    assert database.execute.await_count == 0


@pytest.mark.asyncio
async def test_get_returns_position(manager, db_env, sample_position)
*** End Patch
@pytest.mark.asyncio
def test_get_returns_position(manager, db_env, sample_position):
    database, symbol_mapper, dex_mapper = db_env
    row = {
        'id': sample_position.id,
        'symbol': sample_position.symbol,
        'long_dex_id': dex_mapper.get_id(sample_position.long_dex),
        'short_dex_id': dex_mapper.get_id(sample_position.short_dex),
        'size_usd': sample_position.size_usd,
        'entry_long_rate': sample_position.entry_long_rate,
        'entry_short_rate': sample_position.entry_short_rate,
        'entry_divergence': sample_position.entry_divergence,
        'opened_at': sample_position.opened_at,
        'current_divergence': None,
        'last_check': None,
        'status': sample_position.status,
        'rebalance_pending': False,
        'rebalance_reason': None,
        'exit_reason': None,
        'closed_at': None,
        'pnl_usd': None,
        'cumulative_funding_usd': Decimal('0'),
        'metadata': None,
    }
    database.fetch_one.return_value = row

    result = await manager.get(sample_position.id)

    assert result is not None
    assert result.symbol == sample_position.symbol
    assert result.long_dex == sample_position.long_dex


@pytest.mark.asyncio
async def test_get_returns_none_when_missing(manager, db_env, sample_position):
    database, *_ = db_env
    database.fetch_one.return_value = None

    result = await manager.get(sample_position.id)
    assert result is None


@pytest.mark.asyncio
async def test_get_open_positions(manager, db_env, sample_position):
    database, symbol_mapper, dex_mapper = db_env
    row = {
        'id': sample_position.id,
        'symbol': sample_position.symbol,
        'long_dex_id': dex_mapper.get_id(sample_position.long_dex),
        'short_dex_id': dex_mapper.get_id(sample_position.short_dex),
        'size_usd': sample_position.size_usd,
        'entry_long_rate': sample_position.entry_long_rate,
        'entry_short_rate': sample_position.entry_short_rate,
        'entry_divergence': sample_position.entry_divergence,
        'opened_at': sample_position.opened_at,
        'current_divergence': None,
        'last_check': None,
        'status': 'open',
        'rebalance_pending': False,
        'rebalance_reason': None,
        'exit_reason': None,
        'closed_at': None,
        'pnl_usd': None,
        'cumulative_funding_usd': Decimal('0'),
        'metadata': None,
    }
    database.fetch_all.return_value = [row]

    positions = await manager.get_open_positions()
    assert len(positions) == 1
    assert positions[0].symbol == sample_position.symbol


@pytest.mark.asyncio
async def test_update_persists_changes(manager, db_env, sample_position):
    database, *_ = db_env
    sample_position.metadata = {'legs': {'aster': {'qty': Decimal('1')}}}
    await manager.update(sample_position)
    database.execute.assert_awaited()


@pytest.mark.asyncio
async def test_close_updates_status(manager, db_env, sample_position, monkeypatch):
    database, *_ = db_env

    closing_position = sample_position
    closing_position.status = 'open'

    manager.get = AsyncMock(return_value=closing_position)
    manager.get_cumulative_funding = AsyncMock(return_value=Decimal('5'))

    await manager.close(sample_position.id, exit_reason='TEST', pnl_usd=None)

    database.execute.assert_awaited()
    values = database.execute.await_args.kwargs['values']
    assert values['exit_reason'] == 'TEST'
    assert values['pnl_usd'] == Decimal('5')


@pytest.mark.asyncio
async def test_record_funding_payment_updates_tables(manager, db_env, sample_position):
    database, *_ = db_env
    manager.get_cumulative_funding = AsyncMock(return_value=Decimal('25'))

    await manager.record_funding_payment(
        position_id=sample_position.id,
        long_payment=Decimal('-2.5'),
        short_payment=Decimal('5.0'),
        timestamp=datetime.now(),
    )

    assert database.execute.await_count == 2


@pytest.mark.asyncio
async def test_update_position_state(manager, db_env, sample_position):
    database, *_ = db_env
    await manager.update_position_state(sample_position.id, Decimal('0.01'))
    database.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_flag_for_rebalance(manager, db_env, sample_position):
    database, *_ = db_env
    await manager.flag_for_rebalance(sample_position.id, 'PROFIT_EROSION')
    database.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_pending_rebalance_positions(manager, db_env, sample_position):
    database, symbol_mapper, dex_mapper = db_env
    row = {
        'id': sample_position.id,
        'symbol': sample_position.symbol,
        'long_dex_id': dex_mapper.get_id(sample_position.long_dex),
        'short_dex_id': dex_mapper.get_id(sample_position.short_dex),
        'size_usd': sample_position.size_usd,
        'entry_long_rate': sample_position.entry_long_rate,
        'entry_short_rate': sample_position.entry_short_rate,
        'entry_divergence': sample_position.entry_divergence,
        'opened_at': sample_position.opened_at,
        'current_divergence': Decimal('0.01'),
        'last_check': datetime.now(),
        'status': 'open',
        'rebalance_pending': True,
        'rebalance_reason': 'PROFIT_EROSION',
        'exit_reason': None,
        'closed_at': None,
        'pnl_usd': None,
    }
    database.fetch_all.return_value = [row]

    positions = await manager.get_pending_rebalance_positions()
    assert len(positions) == 1
    assert positions[0].rebalance_reason == 'PROFIT_EROSION'


@pytest.mark.asyncio
async def test_initialize_without_database(monkeypatch):
    monkeypatch.setattr(pm, "DATABASE_AVAILABLE", False)
    manager = pm.FundingArbPositionManager()
    await manager.initialize()
    assert manager._initialized is True

