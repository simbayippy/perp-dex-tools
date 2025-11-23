from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import AsyncMock

import pytest

from strategies.execution.patterns.atomic_multi_order import AtomicExecutionResult
from strategies.implementations.funding_arbitrage.operations.opening.position_opener import PositionOpener
from strategies.implementations.funding_arbitrage.config import RiskManagementConfig


class StubLogger:
    def __init__(self):
        self.entries = []

    def log(self, message: str, level: str = "INFO", **kwargs):
        self.entries.append((level, message))

    def info(self, message: str, **kwargs):
        self.entries.append(("INFO", message))

    def debug(self, message: str, **kwargs):
        self.entries.append(("DEBUG", message))

    def error(self, message: str, **kwargs):
        self.entries.append(("ERROR", message))

    def warning(self, message: str, **kwargs):
        self.entries.append(("WARNING", message))


class StubFeeCalculator:
    def calculate_total_cost(self, *args, **kwargs) -> Decimal:
        return Decimal("1.5")


class StubPositionManager:
    def __init__(self, existing=None):
        self._existing = existing
        self.created = []
        self.updated = []

    async def find_open_position(self, *args, **kwargs):
        return self._existing

    async def create(self, position):
        self.created.append(position)

    async def update(self, position):
        self.updated.append(position)


class StubAtomicExecutor:
    def __init__(self, result: AtomicExecutionResult):
        self._result = result
        self.calls = 0
        self.last_args = None
        self.last_kwargs = None
        self._normalized_leverage = {}  # Required by execution engine

    async def execute_atomically(self, *args, **kwargs):
        self.calls += 1
        self.last_args = args
        self.last_kwargs = kwargs
        return self._result


class StubPriceProvider:
    async def get_bbo_prices(self, exchange_client, symbol):
        return Decimal("100"), Decimal("101")


def _strategy(
    exchange_clients,
    atomic_result: AtomicExecutionResult,
    position_manager=None,
    config_overrides=None,
):
    config_kwargs = {
        "default_position_size_usd": Decimal("100"),
        "risk_config": RiskManagementConfig(),
    }
    if config_overrides:
        config_kwargs.update(config_overrides)

    return SimpleNamespace(
        exchange_clients=exchange_clients,
        atomic_executor=StubAtomicExecutor(atomic_result),
        fee_calculator=StubFeeCalculator(),
        position_manager=position_manager or StubPositionManager(),
        config=SimpleNamespace(**config_kwargs),
        logger=StubLogger(),
        failed_symbols=set(),
        price_provider=StubPriceProvider(),
        atomic_retry_policy=None,
        position_opened_this_session=False,  # Required by persistence handler
        notification_service=None,  # Optional - position opener catches exceptions
    )


def _filled_order(fill_price="100", qty="0.9"):
    return {
        "fill_price": Decimal(fill_price),
        "filled_quantity": Decimal(qty),
        "slippage_usd": Decimal("0.5"),
        "execution_mode_used": "limit",
    }


def _opportunity(symbol="BTC"):
    return SimpleNamespace(
        symbol=symbol,
        long_dex="aster",
        short_dex="lighter",
        divergence=Decimal("0.02"),
        long_rate=Decimal("-0.01"),
        short_rate=Decimal("0.03"),
    )


def _exchange_client():
    async def get_contract_attributes():
        return "BTCUSDT", Decimal("0.01")

    async def ensure_market_feed(symbol):
        pass  # No-op for testing

    return SimpleNamespace(
        config=SimpleNamespace(contract_id="BTCUSDT", ticker="BTC"),
        get_exchange_name=lambda: "stubdex",
        round_to_step=lambda qty: qty,
        get_contract_attributes=get_contract_attributes,
        ensure_market_feed=ensure_market_feed,
        get_quantity_multiplier=lambda symbol=None: Decimal("1.0"),
    )


def _atomic_success():
    return AtomicExecutionResult(
        success=True,
        all_filled=True,
        filled_orders=[_filled_order(), _filled_order()],
        partial_fills=[],
        total_slippage_usd=Decimal("1"),
        execution_time_ms=100,
        error_message=None,
        rollback_performed=False,
        rollback_cost_usd=None,
        residual_imbalance_usd=Decimal("0"),
    )


def _atomic_failure():
    return AtomicExecutionResult(
        success=False,
        all_filled=False,
        filled_orders=[],
        partial_fills=[],
        total_slippage_usd=Decimal("0"),
        execution_time_ms=100,
        error_message="Partial fill",
        rollback_performed=True,
        rollback_cost_usd=Decimal("2"),
        residual_imbalance_usd=Decimal("0.5"),
    )


@pytest.mark.asyncio
async def test_position_opener_success(monkeypatch):
    atomic_result = _atomic_success()
    strategy = _strategy(
        exchange_clients={"aster": _exchange_client(), "lighter": _exchange_client()},
        atomic_result=atomic_result,
    )
    opener = PositionOpener(strategy)

    # Mock the leverage validator to return a successful result
    monkeypatch.setattr(
        opener._leverage_validator,
        "validate_leverage",
        AsyncMock(return_value={"adjusted_size": Decimal("90"), "normalized_leverage": Decimal("10")}),
    )

    opportunity = _opportunity()
    position = await opener.open(opportunity)

    assert position is not None
    assert strategy.atomic_executor.calls == 1
    assert strategy.position_manager.created, "Position should be persisted"
    assert opportunity.symbol not in strategy.failed_symbols


@pytest.mark.asyncio
async def test_position_opener_handles_atomic_failure(monkeypatch):
    atomic_result = _atomic_failure()
    strategy = _strategy(
        exchange_clients={"aster": _exchange_client(), "lighter": _exchange_client()},
        atomic_result=atomic_result,
    )
    opener = PositionOpener(strategy)

    monkeypatch.setattr(opener._leverage_validator, "validate_leverage", AsyncMock(return_value={"adjusted_size": Decimal("50"), "normalized_leverage": Decimal("10")}))

    opportunity = _opportunity()
    position = await opener.open(opportunity)

    assert position is None
    assert opportunity.symbol in strategy.failed_symbols
    assert not strategy.position_manager.created


@pytest.mark.asyncio
async def test_position_opener_leverage_validation_failure(monkeypatch):
    atomic_result = _atomic_success()
    strategy = _strategy(
        exchange_clients={"aster": _exchange_client(), "lighter": _exchange_client()},
        atomic_result=atomic_result,
    )
    opener = PositionOpener(strategy)

    monkeypatch.setattr(opener._leverage_validator, "validate_leverage", AsyncMock(return_value=None))

    opportunity = _opportunity()
    position = await opener.open(opportunity)

    assert position is None
    assert opportunity.symbol in strategy.failed_symbols
    assert not strategy.atomic_executor.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("offset", [Decimal("0"), Decimal("-0.0005")])
async def test_position_opener_passes_limit_offset(monkeypatch, offset):
    atomic_result = _atomic_success()
    strategy = _strategy(
        exchange_clients={"aster": _exchange_client(), "lighter": _exchange_client()},
        atomic_result=atomic_result,
        config_overrides={"limit_order_offset_pct": offset},
    )
    opener = PositionOpener(strategy)

    monkeypatch.setattr(opener._leverage_validator, "validate_leverage", AsyncMock(return_value={"adjusted_size": Decimal("75"), "normalized_leverage": Decimal("10")}))

    opportunity = _opportunity()
    await opener.open(opportunity)

    orders = strategy.atomic_executor.last_kwargs["orders"]
    assert all(order.limit_price_offset_pct == offset for order in orders)
    assert all(order.quantity is not None for order in orders)


@pytest.mark.asyncio
async def test_position_opener_missing_exchange_clients():
    atomic_result = _atomic_success()
    strategy = _strategy(
        exchange_clients={"aster": _exchange_client()},
        atomic_result=atomic_result,
    )
    opener = PositionOpener(strategy)

    opportunity = _opportunity()
    position = await opener.open(opportunity)

    assert position is None
    assert opportunity.symbol in strategy.failed_symbols
