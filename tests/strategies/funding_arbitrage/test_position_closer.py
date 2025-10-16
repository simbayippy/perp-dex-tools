import asyncio
import types
from decimal import Decimal
from datetime import datetime
from types import SimpleNamespace
from typing import Dict
from uuid import uuid4

import pytest

from exchange_clients.base import ExchangePositionSnapshot, OrderInfo, OrderResult
from exchange_clients.events import LiquidationEvent
from strategies.implementations.funding_arbitrage.config import RiskManagementConfig
from strategies.implementations.funding_arbitrage.models import FundingArbPosition
from strategies.implementations.funding_arbitrage.operations.position_closer import PositionCloser
from strategies.execution.patterns.atomic_multi_order import AtomicExecutionResult


class StubLogger:
    def __init__(self):
        self.messages = []

    def log(self, message: str, level: str = "INFO"):
        self.messages.append((level, message))


class StubExchangeClient:
    def __init__(self, name: str, snapshots):
        self._name = name
        self.snapshots = snapshots
        self.limit_orders = []
        self.market_orders = []
        self._orders: Dict[str, OrderInfo] = {}
        self._order_counter = 0

    def get_exchange_name(self):
        return self._name

    async def get_position_snapshot(self, symbol: str):
        return self.snapshots.get(symbol)

    async def fetch_bbo_prices(self, symbol: str):
        return Decimal("100"), Decimal("100.5")

    def round_to_step(self, quantity: Decimal) -> Decimal:
        return quantity

    async def place_limit_order(self, contract_id: str, quantity: Decimal, price: Decimal, side: str):
        order_id = f"{self._name}-limit-{self._order_counter}"
        self._order_counter += 1
        info = OrderInfo(
            order_id=order_id,
            side=side,
            size=Decimal(str(quantity)),
            price=Decimal(str(price)),
            status="FILLED",
            filled_size=Decimal(str(quantity)),
        )
        self._orders[order_id] = info
        self.limit_orders.append(
            {
                "contract_id": contract_id,
                "quantity": Decimal(str(quantity)),
                "price": Decimal(str(price)),
                "side": side,
            }
        )
        return OrderResult(success=True, order_id=order_id, side=side, size=Decimal(str(quantity)), price=Decimal(str(price)), status="FILLED")

    async def place_market_order(self, contract_id: str, quantity: Decimal, side: str):
        order_id = f"{self._name}-market-{self._order_counter}"
        self._order_counter += 1
        price = Decimal("100.25")
        self.market_orders.append(
            {
                "contract_id": contract_id,
                "quantity": Decimal(str(quantity)),
                "side": side,
            }
        )
        return OrderResult(success=True, order_id=order_id, side=side, size=Decimal(str(quantity)), price=price, status="FILLED")

    async def cancel_order(self, order_id: str):
        return OrderResult(success=True, order_id=order_id)

    async def get_order_info(self, order_id: str):
        return self._orders.get(order_id)


class StubPositionManager:
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


def _make_position(symbol="BTC", long_dex="aster", short_dex="lighter"):
    return FundingArbPosition(
        id=uuid4(),
        symbol=symbol,
        long_dex=long_dex,
        short_dex=short_dex,
        size_usd=Decimal("1000"),
        entry_long_rate=Decimal("-0.01"),
        entry_short_rate=Decimal("0.03"),
        entry_divergence=Decimal("0.04"),
        opened_at=datetime.now(),
    )


class StubAtomicExecutor:
    def __init__(self):
        self.last_orders = None

    async def execute_atomically(self, orders, **kwargs):
        self.last_orders = orders
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


def _make_strategy(position_manager, exchange_clients, risk_config=None):
    return SimpleNamespace(
        position_manager=position_manager,
        exchange_clients=exchange_clients,
        config=SimpleNamespace(risk_config=risk_config or RiskManagementConfig()),
        logger=StubLogger(),
        funding_rate_repo=None,
        price_provider=None,
        atomic_executor=StubAtomicExecutor(),
    )


def test_handle_liquidation_event_closes_remaining_leg():
    position = _make_position()
    position_manager = StubPositionManager([position])

    lighter_snapshot = ExchangePositionSnapshot(symbol="BTC", quantity=Decimal("0"))
    aster_snapshot = ExchangePositionSnapshot(symbol="BTC", quantity=Decimal("1"))

    exchange_clients = {
        "lighter": StubExchangeClient("lighter", {"BTC": lighter_snapshot}),
        "aster": StubExchangeClient("aster", {"BTC": aster_snapshot}),
    }

    strategy = _make_strategy(position_manager, exchange_clients)
    closer = PositionCloser(strategy)
    # Skip risk manager complexity for this test
    closer._risk_manager = None

    event = LiquidationEvent(
        exchange="lighter",
        symbol="BTC",
        side="sell",
        quantity=Decimal("0.5"),
        price=Decimal("25000"),
    )

    asyncio.run(closer.handle_liquidation_event(event))

    # lighter leg already flat, should not attempt close
    assert exchange_clients["lighter"].market_orders == []
    # surviving leg should be closed via market order
    assert len(exchange_clients["aster"].market_orders) == 1
    assert any(reason.startswith("LIQUIDATION_LIGHTER") for _, reason, _ in position_manager.closed_records)


def test_position_closer_respects_risk_manager_decision():
    position = _make_position()
    position_manager = StubPositionManager([position])

    snapshot = ExchangePositionSnapshot(symbol="BTC", quantity=Decimal("1"))
    exchange_clients = {
        "lighter": StubExchangeClient("lighter", {"BTC": snapshot}),
        "aster": StubExchangeClient("aster", {"BTC": snapshot}),
    }

    strategy = _make_strategy(position_manager, exchange_clients)
    closer = PositionCloser(strategy)

    class StubRiskManager:
        def __init__(self):
            self.calls = 0

        def should_exit(self, position, current_rates):
            self.calls += 1
            return True, "PROFIT_EROSION"

    stub_manager = StubRiskManager()
    closer._risk_manager = stub_manager

    async def fake_rates(self, position):
        return {
            "divergence": Decimal("0.5"),
            "long_rate": Decimal("-0.01"),
            "short_rate": Decimal("0.49"),
            "long_oi_usd": Decimal("100000"),
            "short_oi_usd": Decimal("100000"),
        }

    closer._gather_current_rates = types.MethodType(fake_rates, closer)

    asyncio.run(closer.evaluateAndClosePositions())

    assert stub_manager.calls == 1
    # Both legs should be closed via atomic executor producing two order specs
    assert strategy.atomic_executor.last_orders is not None
    assert len(strategy.atomic_executor.last_orders) == 2
    qtys = [order.quantity for order in strategy.atomic_executor.last_orders]
    assert all(q == Decimal("1") for q in qtys)
    assert any(reason == "PROFIT_EROSION" for _, reason, _ in position_manager.closed_records)


def test_position_closer_fallback_on_divergence_flip():
    position = _make_position()
    position.current_divergence = Decimal("-0.001")
    position_manager = StubPositionManager([position])

    snapshot = ExchangePositionSnapshot(symbol="BTC", quantity=Decimal("1"))
    exchange_clients = {
        "lighter": StubExchangeClient({"BTC": snapshot}),
        "aster": StubExchangeClient({"BTC": snapshot}),
    }

    strategy = _make_strategy(position_manager, exchange_clients)
    closer = PositionCloser(strategy)
    closer._risk_manager = None  # Force fallback heuristics

    async def fake_rates(self, position):
        return None  # skip risk manager evaluation path

    closer._gather_current_rates = types.MethodType(fake_rates, closer)

    asyncio.run(closer.evaluateAndClosePositions())

    assert exchange_clients["lighter"].closed == ["BTC"]
    assert exchange_clients["aster"].closed == ["BTC"]
    assert any(reason == "DIVERGENCE_FLIPPED" for _, reason, _ in position_manager.closed_records)


def test_symbols_match_variants():
    matcher = PositionCloser._symbols_match
    assert matcher("BTC", "BTC")
    assert matcher("BTC", "BTCUSDT")
    assert matcher("ETHUSDT", "ETH")
    assert not matcher("BTC", "ETH")
