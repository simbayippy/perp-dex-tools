from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Tuple

import time

from exchange_clients.base_models import ExchangePositionSnapshot

import pytest

from strategies.implementations.grid.config import GridConfig
from strategies.implementations.grid.models import (
    GridCycleState,
    GridOrder,
    GridState,
    TrackedPosition,
)
from strategies.implementations.grid import strategy as grid_strategy_module
from strategies.implementations.grid.strategy import GridStrategy


@pytest.fixture(autouse=True)
def reset_grid_event_notifier(monkeypatch):
    """
    Replace the real notifier with an in-memory stub for tests.

    Avoids file IO and external network calls while allowing assertions on emitted events.
    """
    events: List[Dict[str, Any]] = []

    class StubNotifier:
        def __init__(self, *args, **kwargs):
            self.events = events

        def notify(self, **payload):
            events.append(payload)

    monkeypatch.setattr(grid_strategy_module, "GridEventNotifier", StubNotifier)
    yield events
    events.clear()


def make_config(
    *,
    direction: str = "buy",
    max_margin_usd: Decimal = Decimal("1000"),
    post_only_tick_multiplier: Decimal = Decimal("2"),
    order_notional_usd: Optional[Decimal] = None,
    target_leverage: Optional[Decimal] = None,
) -> GridConfig:
    return GridConfig(
        take_profit=Decimal("0.8"),
        grid_step=Decimal("0.2"),
        direction=direction,
        max_orders=5,
        wait_time=5,
        max_margin_usd=max_margin_usd,
        stop_loss_enabled=True,
        stop_loss_percentage=Decimal("2"),
        position_timeout_minutes=60,
        recovery_mode="ladder",
        stop_price=None,
        pause_price=None,
        boost_mode=False,
        post_only_tick_multiplier=post_only_tick_multiplier,
        order_notional_usd=order_notional_usd,
        target_leverage=target_leverage,
    )


@dataclass
class DummyExchangeConfig:
    ticker: str = "BTC"
    quantity: Decimal = Decimal("1")
    tick_size: Decimal = Decimal("0.1")
    contract_id: str = "BTC-PERP"


class DummyExchange:
    class _OrderResult:
        def __init__(self, success: bool, order_id: str, price: Optional[Decimal], size: Decimal, status: str = "FILLED", error_message: Optional[str] = None):
            self.success = success
            self.order_id = order_id
            self.price = price
            self.size = size
            self.status = status
            self.error_message = error_message

    def __init__(self):
        self.config = DummyExchangeConfig()
        self.market_orders: List[Dict[str, Any]] = []
        self.close_orders: List[Dict[str, Any]] = []
        self.cancelled_orders: List[str] = []
        self.next_market_success = True
        self.best_bid = Decimal("100")
        self.best_ask = Decimal("101")

    def get_exchange_name(self) -> str:
        return "dummy"

    async def fetch_bbo_prices(self, _contract_id: str) -> Tuple[Decimal, Decimal]:
        return self.best_bid, self.best_ask

    async def get_account_positions(self) -> Decimal:
        return Decimal("0")

    async def place_market_order(self, contract_id: str, quantity: Decimal, side: str):
        order_id = f"market-{len(self.market_orders) + 1}"
        self.market_orders.append({
            "contract_id": contract_id,
            "quantity": quantity,
            "side": side,
        })
        if self.next_market_success:
            return DummyExchange._OrderResult(True, order_id, None, quantity, status="FILLED")
        else:
            self.next_market_success = True
            return DummyExchange._OrderResult(False, order_id, None, quantity, status="REJECTED", error_message="simulated failure")

    async def place_close_order(self, contract_id: str, quantity: Decimal, price: Decimal, side: str):
        order_id = f"close-{len(self.close_orders) + 1}"
        self.close_orders.append({
            "contract_id": contract_id,
            "quantity": quantity,
            "price": price,
            "side": side,
        })
        return DummyExchange._OrderResult(True, order_id, price, quantity, status="OPEN")

    async def cancel_order(self, order_id: str):
        self.cancelled_orders.append(order_id)

    async def place_limit_order(self, *args, **kwargs):  # for completeness
        return await self.place_close_order(*args, **kwargs)

    def round_to_tick(self, price: Decimal) -> Decimal:
        tick = self.config.tick_size
        return Decimal(price).quantize(tick, rounding=ROUND_HALF_UP)

    def round_to_step(self, quantity: Decimal) -> Decimal:
        return quantity


def make_snapshot(
    *,
    symbol: str = "BTC",
    quantity: Decimal = Decimal("1"),
    entry_price: Decimal = Decimal("100"),
    mark_price: Optional[Decimal] = None,
    exposure_usd: Optional[Decimal] = None,
    margin_reserved: Optional[Decimal] = None,
) -> ExchangePositionSnapshot:
    return ExchangePositionSnapshot(
        symbol=symbol,
        quantity=quantity,
        side="long" if quantity > 0 else "short",
        entry_price=entry_price,
        mark_price=mark_price,
        exposure_usd=exposure_usd,
        unrealized_pnl=None,
        realized_pnl=None,
        funding_accrued=None,
        margin_reserved=margin_reserved,
        leverage=None,
        liquidation_price=None,
        timestamp=None,
        metadata={},
    )


def test_grid_state_round_trip_serialisation():
    order = GridOrder(order_id="abc123", price=Decimal("100"), size=Decimal("2"), side="sell")
    tracked = TrackedPosition(
        entry_price=Decimal("95"),
        size=Decimal("2"),
        side="long",
        open_time=1234567890.0,
        close_order_ids=["close-1", "close-2"],
        recovery_attempts=1,
        hedged=True,
        last_recovery_time=1234567999.0,
    )
    state = GridState(
        cycle_state=GridCycleState.WAITING_FOR_FILL,
        active_close_orders=[order],
        last_close_orders_count=3,
        last_open_order_time=1234567000.0,
        filled_price=Decimal("90"),
        filled_quantity=Decimal("1.5"),
        pending_open_order_id="open-abc",
        pending_open_quantity=Decimal("1.5"),
        last_known_position=Decimal("5"),
        last_known_margin=Decimal("250"),
        margin_ratio=Decimal("0.1"),
        last_stop_loss_trigger=1234567888.0,
        tracked_positions=[tracked],
    )

    rebuilt = GridState.from_dict(state.to_dict())

    assert rebuilt.cycle_state == GridCycleState.WAITING_FOR_FILL
    assert rebuilt.active_close_orders[0].order_id == "abc123"
    assert rebuilt.filled_price == Decimal("90")
    assert rebuilt.tracked_positions[0].close_order_ids == ["close-1", "close-2"]
    assert rebuilt.tracked_positions[0].hedged is True
    assert rebuilt.pending_open_order_id == "open-abc"
    assert rebuilt.pending_open_quantity == Decimal("1.5")
    assert rebuilt.margin_ratio == Decimal("0.1")


@pytest.mark.asyncio
async def test_check_risk_limits_allows_within_bounds(reset_grid_event_notifier):
    config = make_config()
    exchange = DummyExchange()
    strategy = GridStrategy(config=config, exchange_client=exchange)
    events: List[Dict[str, Any]] = strategy.event_notifier.events  # type: ignore[attr-defined]

    ok, message = await strategy.risk_controller.check_order_limits(
        reference_price=Decimal("100"),
        order_quantity=Decimal("1"),
    )

    assert ok is True
    assert message == "OK"
    assert events == []


@pytest.mark.asyncio
async def test_check_risk_limits_blocks_margin_cap(reset_grid_event_notifier):
    config = make_config(
        max_margin_usd=Decimal("1000"),
    )
    exchange = DummyExchange()
    strategy = GridStrategy(config=config, exchange_client=exchange)
    events: List[Dict[str, Any]] = strategy.event_notifier.events  # type: ignore[attr-defined]

    strategy.grid_state.last_known_position = Decimal("0")
    strategy.grid_state.last_known_margin = Decimal("900")
    strategy.grid_state.margin_ratio = Decimal("1")

    ok, message = await strategy.risk_controller.check_order_limits(
        reference_price=Decimal("100"),
        order_quantity=Decimal("5"),
    )

    assert ok is False
    assert "Margin cap" in message

    assert events, "Expected an event for margin cap breach"
    margin_event = events[-1]
    assert margin_event["event_type"] == "margin_cap_hit"
    assert margin_event["payload"]["margin_limit"] == pytest.approx(1000.0)
    assert margin_event["payload"]["projected_margin"] > margin_event["payload"]["margin_limit"]


@pytest.mark.asyncio
async def test_place_open_order_uses_order_notional(reset_grid_event_notifier):
    config = make_config(order_notional_usd=Decimal("50"))
    exchange = DummyExchange()
    exchange.config.quantity = Decimal("5")  # should be overwritten by notional sizing
    exchange.best_bid = Decimal("100")
    exchange.best_ask = Decimal("100")
    strategy = GridStrategy(config=config, exchange_client=exchange)

    result = await strategy.open_operator.place_open_order()

    assert result["action"] == "order_placed"
    expected_quantity = Decimal("50") / Decimal("100")
    assert exchange.config.quantity == expected_quantity
    assert result["quantity"] == expected_quantity
    assert result["notional_usd"].quantize(Decimal("0.01")) == Decimal("50.00")


@pytest.mark.asyncio
async def test_stop_loss_triggers_for_long(reset_grid_event_notifier):
    config = make_config(direction="buy")
    exchange = DummyExchange()
    strategy = GridStrategy(config=config, exchange_client=exchange)
    events: List[Dict[str, Any]] = strategy.event_notifier.events  # type: ignore[attr-defined]

    snapshot = make_snapshot(quantity=Decimal("1"), entry_price=Decimal("100"))
    strategy.grid_state.last_stop_loss_trigger = 0
    strategy.grid_state.active_close_orders = []

    triggered = await strategy.risk_controller.enforce_stop_loss(
        snapshot=snapshot,
        current_price=Decimal("97"),  # below 2% stop
        current_position=Decimal("1"),
        close_position_fn=strategy.order_closer.market_close,
    )

    assert triggered is True
    assert exchange.market_orders
    assert exchange.market_orders[0]["side"] == "sell"
    event_types = [evt["event_type"] for evt in events]
    assert "stop_loss_initiated" in event_types
    assert "stop_loss_executed" in event_types
    assert strategy.grid_state.last_stop_loss_trigger > 0


@pytest.mark.asyncio
async def test_stop_loss_triggers_for_short(reset_grid_event_notifier):
    config = make_config(direction="sell")
    exchange = DummyExchange()
    strategy = GridStrategy(config=config, exchange_client=exchange)
    events: List[Dict[str, Any]] = strategy.event_notifier.events  # type: ignore[attr-defined]

    snapshot = make_snapshot(quantity=Decimal("-2"), entry_price=Decimal("100"))
    strategy.grid_state.active_close_orders = []

    triggered = await strategy.risk_controller.enforce_stop_loss(
        snapshot=snapshot,
        current_price=Decimal("103"),  # above stop threshold for short
        current_position=Decimal("-2"),
        close_position_fn=strategy.order_closer.market_close,
    )

    assert triggered is True
    assert exchange.market_orders
    assert exchange.market_orders[0]["side"] == "buy"
    event_types = [evt["event_type"] for evt in events]
    assert "stop_loss_initiated" in event_types
    assert "stop_loss_executed" in event_types


@pytest.mark.asyncio
async def test_stop_loss_disabled_no_action(reset_grid_event_notifier):
    config = make_config()
    config.stop_loss_enabled = False
    exchange = DummyExchange()
    strategy = GridStrategy(config=config, exchange_client=exchange)
    events: List[Dict[str, Any]] = strategy.event_notifier.events  # type: ignore[attr-defined]

    async def fake_close(*_args, **_kwargs):
        raise AssertionError("Stop loss should not execute when disabled")

    snapshot = make_snapshot(quantity=Decimal("1"), entry_price=Decimal("100"))

    triggered = await strategy.risk_controller.enforce_stop_loss(
        snapshot=snapshot,
        current_price=Decimal("90"),
        current_position=Decimal("1"),
        close_position_fn=fake_close,
    )

    assert triggered is False
    assert events == []
    assert exchange.market_orders == []


def make_tracked_position(**overrides: Any) -> TrackedPosition:
    defaults = dict(
        entry_price=Decimal("100"),
        size=Decimal("2"),
        side="long",
        open_time=time.time() - 4000,
        close_order_ids=["close-1"],
        recovery_attempts=0,
        hedged=False,
        last_recovery_time=0.0,
    )
    defaults.update(overrides)
    return TrackedPosition(**defaults)


@pytest.mark.asyncio
async def test_recover_position_aggressive(reset_grid_event_notifier):
    config = make_config()
    config.recovery_mode = "aggressive"
    exchange = DummyExchange()
    strategy = GridStrategy(config=config, exchange_client=exchange)
    events: List[Dict[str, Any]] = strategy.event_notifier.events  # type: ignore[attr-defined]

    tracked = make_tracked_position(side="long", size=Decimal("3"))

    result = await strategy.recovery_operator._recover_position(tracked, current_price=Decimal("90"))

    assert result is True
    assert "close-1" in exchange.cancelled_orders
    event_types = [evt["event_type"] for evt in events]
    assert "recovery_aggressive_start" in event_types


@pytest.mark.asyncio
async def test_recover_position_ladder(reset_grid_event_notifier):
    config = make_config()
    config.recovery_mode = "ladder"
    exchange = DummyExchange()
    strategy = GridStrategy(config=config, exchange_client=exchange)
    events: List[Dict[str, Any]] = strategy.event_notifier.events  # type: ignore[attr-defined]

    tracked = make_tracked_position(side="long", size=Decimal("2"))
    result = await strategy.recovery_operator._recover_position(tracked, current_price=Decimal("90"))

    assert result is False
    assert len(exchange.close_orders) == 3
    assert tracked.close_order_ids  # updated ladder order ids
    event_types = [evt["event_type"] for evt in events]
    assert "recovery_ladder_start" in event_types
    assert "recovery_ladder_orders_active" in event_types


@pytest.mark.asyncio
async def test_recover_position_hedge_success(reset_grid_event_notifier):
    config = make_config()
    config.recovery_mode = "hedge"
    exchange = DummyExchange()
    strategy = GridStrategy(config=config, exchange_client=exchange)
    events: List[Dict[str, Any]] = strategy.event_notifier.events  # type: ignore[attr-defined]

    strategy.grid_state.last_known_position = Decimal("5")
    strategy.grid_state.last_known_margin = Decimal("500")
    strategy.grid_state.margin_ratio = Decimal("0.1")

    tracked = make_tracked_position(side="long", size=Decimal("4"))
    result = await strategy.recovery_operator._recover_position(tracked, current_price=Decimal("90"))

    assert result is True
    assert strategy.grid_state.last_known_position == Decimal("0")
    assert strategy.grid_state.last_known_margin == Decimal("0")
    assert strategy.grid_state.margin_ratio is None
    event_types = [evt["event_type"] for evt in events]
    assert "recovery_hedge_start" in event_types
    assert "recovery_hedge_executed" in event_types


@pytest.mark.asyncio
async def test_recover_position_hedge_failure(reset_grid_event_notifier):
    config = make_config()
    config.recovery_mode = "hedge"
    exchange = DummyExchange()
    strategy = GridStrategy(config=config, exchange_client=exchange)
    events: List[Dict[str, Any]] = strategy.event_notifier.events  # type: ignore[attr-defined]
    exchange.next_market_success = False
    tracked = make_tracked_position(side="long", size=Decimal("2"))
    result = await strategy.recovery_operator._recover_position(tracked, current_price=Decimal("90"))

    assert result is False
    event_types = [evt["event_type"] for evt in events]
    assert "recovery_hedge_start" in event_types
    assert "recovery_hedge_rejected" in event_types


@pytest.mark.asyncio
async def test_prepare_risk_snapshot_with_exchange_snapshot(reset_grid_event_notifier, monkeypatch):
    config = make_config()
    exchange = DummyExchange()
    strategy = GridStrategy(config=config, exchange_client=exchange)

    async def fake_positions():
        return Decimal("5")

    async def fake_fetch_snapshot():
        return make_snapshot(
            quantity=Decimal("5"),
            entry_price=Decimal("100"),
            exposure_usd=Decimal("500"),
            margin_reserved=Decimal("75"),
        )

    monkeypatch.setattr(exchange, "get_account_positions", fake_positions)
    monkeypatch.setattr(strategy.risk_controller, "_fetch_position_snapshot", fake_fetch_snapshot)

    position, snapshot = await strategy.risk_controller.refresh_risk_snapshot(reference_price=Decimal("110"))

    assert position == Decimal("5")
    assert snapshot is not None
    assert strategy.grid_state.last_known_margin == Decimal("75")
    assert strategy.grid_state.margin_ratio == Decimal("0.15")


@pytest.mark.asyncio
async def test_prepare_risk_snapshot_handles_exceptions(reset_grid_event_notifier, monkeypatch):
    config = make_config()
    exchange = DummyExchange()
    strategy = GridStrategy(config=config, exchange_client=exchange)

    async def fail_positions():
        raise RuntimeError("boom")

    async def fake_snapshot():
        return None

    monkeypatch.setattr(exchange, "get_account_positions", fail_positions)
    monkeypatch.setattr(strategy.risk_controller, "_fetch_position_snapshot", fake_snapshot)

    position, snapshot = await strategy.risk_controller.refresh_risk_snapshot(reference_price=Decimal("50"))

    assert position == Decimal("0")
    assert snapshot is None
    assert strategy.grid_state.last_known_margin == Decimal("0")
    assert strategy.grid_state.margin_ratio is None


def make_grid_order(order_id: str, price: Decimal, size: Decimal, side: str = "sell") -> GridOrder:
    return GridOrder(order_id=order_id, price=price, size=size, side=side)


def test_calculate_wait_time_handles_order_density(monkeypatch, reset_grid_event_notifier):
    config = make_config()
    exchange = DummyExchange()
    strategy = GridStrategy(config=config, exchange_client=exchange)

    now = time.time()
    strategy.grid_state.active_close_orders = [
        make_grid_order("a", Decimal("110"), Decimal("1")),
        make_grid_order("b", Decimal("111"), Decimal("1")),
        make_grid_order("c", Decimal("112"), Decimal("1")),
    ]
    strategy.grid_state.last_close_orders_count = 3
    strategy.grid_state.last_open_order_time = now - (config.wait_time * 2)

    monkeypatch.setattr(grid_strategy_module.time, "time", lambda: now)

    wait = strategy._calculate_wait_time()
    assert wait == 0  # cooldown elapsed

    # Not enough time elapsed -> should request wait
    strategy.grid_state.last_open_order_time = now
    wait_again = strategy._calculate_wait_time()
    assert wait_again == 1


@pytest.mark.asyncio
async def test_meet_grid_step_condition_buy(reset_grid_event_notifier):
    config = make_config(direction="buy")
    exchange = DummyExchange()
    strategy = GridStrategy(config=config, exchange_client=exchange)

    strategy.grid_state.active_close_orders = [
        make_grid_order("close", Decimal("110"), Decimal("1")),
    ]

    best_bid, best_ask = Decimal("100"), Decimal("101")
    result = await strategy._meet_grid_step_condition(best_bid, best_ask)
    assert result is True

    strategy.grid_state.active_close_orders[0] = make_grid_order("close", Decimal("101.5"), Decimal("1"))
    result_false = await strategy._meet_grid_step_condition(best_bid, best_ask)
    assert result_false is False


@pytest.mark.asyncio
async def test_meet_grid_step_condition_sell(reset_grid_event_notifier):
    config = make_config(direction="sell")
    exchange = DummyExchange()
    strategy = GridStrategy(config=config, exchange_client=exchange)

    strategy.grid_state.active_close_orders = [
        make_grid_order("close", Decimal("90"), Decimal("1"), side="buy"),
    ]

    best_bid, best_ask = Decimal("100"), Decimal("101")
    result = await strategy._meet_grid_step_condition(best_bid, best_ask)
    assert result is True

    strategy.grid_state.active_close_orders[0] = make_grid_order("close", Decimal("99.2"), Decimal("1"), side="buy")
    result_false = await strategy._meet_grid_step_condition(best_bid, best_ask)
    assert result_false is False


@pytest.mark.asyncio
async def test_run_recovery_checks_invokes_recovery(reset_grid_event_notifier, monkeypatch):
    config = make_config()
    exchange = DummyExchange()
    strategy = GridStrategy(config=config, exchange_client=exchange)
    events: List[Dict[str, Any]] = strategy.event_notifier.events  # type: ignore[attr-defined]

    base_time = 1_000_000
    monkeypatch.setattr(grid_strategy_module.time, "time", lambda: base_time)

    tracked = make_tracked_position(open_time=base_time - 4000, last_recovery_time=0.0)
    strategy.grid_state.tracked_positions = [tracked]
    strategy.grid_state.active_close_orders = [
        make_grid_order("close-1", Decimal("110"), Decimal("1")),
    ]

    calls: List[TrackedPosition] = []

    async def fake_recover(tp: TrackedPosition, current_price: Decimal) -> bool:
        calls.append(tp)
        return True

    strategy.recovery_operator._recover_position = fake_recover  # type: ignore[attr-defined]

    await strategy.recovery_operator.run_recovery_checks(current_price=Decimal("90"))

    assert calls == [tracked]
    assert strategy.grid_state.tracked_positions == []
    event_types = [evt["event_type"] for evt in events]
    assert "recovery_detected" in event_types


@pytest.mark.asyncio
async def test_run_recovery_checks_respects_cooldown(reset_grid_event_notifier, monkeypatch):
    config = make_config()
    exchange = DummyExchange()
    strategy = GridStrategy(config=config, exchange_client=exchange)
    events: List[Dict[str, Any]] = strategy.event_notifier.events  # type: ignore[attr-defined]

    base_time = 2_000_000
    monkeypatch.setattr(grid_strategy_module.time, "time", lambda: base_time)

    tracked = make_tracked_position(open_time=base_time - 4000, last_recovery_time=0.0)
    strategy.grid_state.tracked_positions = [tracked]
    strategy.grid_state.active_close_orders = [
        make_grid_order("close-1", Decimal("110"), Decimal("1")),
    ]

    async def fake_recover(tp: TrackedPosition, current_price: Decimal) -> bool:
        tp.last_recovery_time = base_time  # simulate no resolution, set cooldown
        return False

    strategy.recovery_operator._recover_position = fake_recover  # type: ignore[attr-defined]

    await strategy.recovery_operator.run_recovery_checks(current_price=Decimal("80"))

    # still tracked due to failed recovery and active cooldown
    assert strategy.grid_state.tracked_positions
    event_types = [evt["event_type"] for evt in events]
    assert "recovery_detected" in event_types

    # Next call within cooldown window should skip recovery attempt
    monkeypatch.setattr(grid_strategy_module.time, "time", lambda: base_time + 2)
    events.clear()
    await strategy.recovery_operator.run_recovery_checks(current_price=Decimal("80"))
    cooldown_events = [evt["event_type"] for evt in events]
    assert "recovery_cooldown_active" in cooldown_events
