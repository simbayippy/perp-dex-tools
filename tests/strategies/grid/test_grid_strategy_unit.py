from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Tuple

import time

from exchange_clients.base_models import ExchangePositionSnapshot, OrderInfo

import pytest

from strategies.implementations.grid.config import GridConfig
from strategies.implementations.grid.models import (
    GridCycleState,
    GridOrder,
    GridState,
    TrackedPosition,
)
from strategies.implementations.grid.position_manager import GridPositionManager
from strategies.implementations.grid import strategy as grid_strategy_module
from strategies.implementations.grid.strategy import GridStrategy
from strategies.implementations.grid.operations import close_position as close_position_module


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
        recovery_mode="aggressive",
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
        self.limit_orders: List[Dict[str, Any]] = []
        self.close_orders: List[Dict[str, Any]] = []
        self.cancelled_orders: List[str] = []
        self.next_market_success = True
        self.best_bid = Decimal("100")
        self.best_ask = Decimal("101")
        self.client_to_server_ids: Dict[str, str] = {}

    def get_exchange_name(self) -> str:
        return "dummy"

    async def fetch_bbo_prices(self, _contract_id: str) -> Tuple[Decimal, Decimal]:
        return self.best_bid, self.best_ask

    async def get_account_positions(self) -> Decimal:
        return Decimal("0")

    async def place_market_order(
        self,
        contract_id: str,
        quantity: Decimal,
        side: str,
        client_order_id: Optional[int] = None,
        reduce_only: bool = False,
    ):
        order_id = str(client_order_id) if client_order_id is not None else f"market-{len(self.market_orders) + 1}"
        self.market_orders.append({
            "contract_id": contract_id,
            "quantity": quantity,
            "side": side,
            "client_order_id": client_order_id,
            "reduce_only": reduce_only,
        })
        if self.next_market_success:
            return DummyExchange._OrderResult(True, order_id, None, quantity, status="FILLED")
        else:
            self.next_market_success = True
            return DummyExchange._OrderResult(False, order_id, None, quantity, status="REJECTED", error_message="simulated failure")

    async def cancel_order(self, order_id: str):
        self.cancelled_orders.append(order_id)

    async def place_limit_order(
        self,
        contract_id: str,
        quantity: Decimal,
        price: Decimal,
        side: str,
        reduce_only: bool = False,
        client_order_id: Optional[int] = None,
    ):
        if reduce_only:
            order_id = str(client_order_id) if client_order_id is not None else f"close-{len(self.close_orders) + 1}"
        else:
            order_id = str(client_order_id) if client_order_id is not None else f"open-{len(self.limit_orders) + 1}"
        entry = {
            "contract_id": contract_id,
            "quantity": quantity,
            "price": price,
            "side": side,
            "reduce_only": reduce_only,
            "client_order_id": client_order_id,
            "order_id": order_id,
        }
        if reduce_only:
            self.close_orders.append(entry)
        else:
            self.limit_orders.append(entry)
        return DummyExchange._OrderResult(True, order_id, price, quantity, status="OPEN")

    def round_to_tick(self, price: Decimal) -> Decimal:
        tick = self.config.tick_size
        return Decimal(price).quantize(tick, rounding=ROUND_HALF_UP)

    def round_to_step(self, quantity: Decimal) -> Decimal:
        return quantity

    def resolve_client_order_id(self, client_order_id: str) -> Optional[str]:
        return self.client_to_server_ids.get(str(client_order_id))


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
        position_id="grid-2",
        entry_price=Decimal("95"),
        size=Decimal("2"),
        side="long",
        open_time=1234567890.0,
        close_order_ids=["close-1", "close-2"],
        recovery_attempts=1,
        hedged=True,
        last_recovery_time=1234567999.0,
        entry_client_order_index=111,
        close_client_order_indices=[222, 333],
        post_only_retry_count=2,
        last_post_only_retry=1234568000.0,
    )
    state = GridState(
        cycle_state=GridCycleState.WAITING_FOR_FILL,
        active_close_orders=[order],
        last_close_orders_count=3,
        last_open_order_time=1234567000.0,
        filled_price=Decimal("90"),
        filled_quantity=Decimal("1.5"),
        filled_position_id="grid-1",
        filled_client_order_index=111,
        pending_open_order_id="open-abc",
        pending_open_quantity=Decimal("1.5"),
        pending_open_order_time=1234567990.0,
        pending_position_id="grid-1",
        pending_client_order_index=111,
        last_known_position=Decimal("5"),
        last_known_margin=Decimal("250"),
        margin_ratio=Decimal("0.1"),
        last_stop_loss_trigger=1234567888.0,
        tracked_positions=[tracked],
        position_sequence=7,
        order_index_to_position_id={111: "grid-1"},
    )

    rebuilt = GridState.from_dict(state.to_dict())

    assert rebuilt.cycle_state == GridCycleState.WAITING_FOR_FILL
    assert rebuilt.active_close_orders[0].order_id == "abc123"
    assert rebuilt.filled_price == Decimal("90")
    assert rebuilt.tracked_positions[0].close_order_ids == ["close-1", "close-2"]
    assert rebuilt.tracked_positions[0].hedged is True
    assert rebuilt.tracked_positions[0].entry_client_order_index == 111
    assert rebuilt.tracked_positions[0].close_client_order_indices == [222, 333]
    assert rebuilt.tracked_positions[0].post_only_retry_count == 2
    assert rebuilt.pending_open_order_id == "open-abc"
    assert rebuilt.pending_open_quantity == Decimal("1.5")
    assert rebuilt.pending_open_order_time == 1234567990.0
    assert rebuilt.pending_position_id == "grid-1"
    assert rebuilt.filled_position_id == "grid-1"
    assert rebuilt.filled_client_order_index == 111
    assert rebuilt.pending_client_order_index == 111
    assert rebuilt.position_sequence == 7
    assert rebuilt.margin_ratio == Decimal("0.1")
    assert rebuilt.order_index_to_position_id == {111: "grid-1"}


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
async def test_close_order_price_scales_with_margin_ratio(reset_grid_event_notifier):
    config = make_config(direction="buy")
    exchange = DummyExchange()
    strategy = GridStrategy(config=config, exchange_client=exchange)

    strategy.grid_state.margin_ratio = Decimal("0.05")  # 20x effective leverage
    strategy.grid_state.filled_price = Decimal("100")
    strategy.grid_state.filled_quantity = Decimal("1")

    result = await strategy.order_closer.handle_filled_order()

    assert exchange.close_orders, "Expected a close order to be placed"
    expected_price = Decimal("100") * (
        Decimal("1") + (config.take_profit / Decimal("100")) * Decimal("0.05")
    )
    assert exchange.close_orders[-1]["price"] == expected_price
    assert result["position_id"].startswith("grid-")


@pytest.mark.asyncio
async def test_close_order_price_uses_target_leverage_fallback(reset_grid_event_notifier):
    config = make_config(direction="sell", target_leverage=Decimal("25"))
    exchange = DummyExchange()
    strategy = GridStrategy(config=config, exchange_client=exchange)

    strategy.grid_state.margin_ratio = None
    strategy.grid_state.filled_price = Decimal("200")
    strategy.grid_state.filled_quantity = Decimal("2")

    result = await strategy.order_closer.handle_filled_order()

    assert exchange.close_orders, "Expected a close order to be placed"
    leverage_ratio = Decimal("1") / Decimal("25")
    expected_price = Decimal("200") * (
        Decimal("1") - (config.take_profit / Decimal("100")) * leverage_ratio
    )
    assert exchange.close_orders[-1]["price"] == expected_price
    assert result["position_id"].startswith("grid-")


def test_position_manager_assigns_identifier(reset_grid_event_notifier):
    state = GridState()
    manager = GridPositionManager(state)
    position = TrackedPosition(
        position_id="",
        entry_price=Decimal("100"),
        size=Decimal("1"),
        side="long",
        open_time=time.time(),
        close_order_ids=[],
    )

    manager.track(position)

    assert position.position_id.startswith("grid-")
    assert state.position_sequence == 1


@pytest.mark.asyncio
async def test_stop_loss_triggers_for_long(reset_grid_event_notifier):
    config = make_config(direction="buy")
    exchange = DummyExchange()
    strategy = GridStrategy(config=config, exchange_client=exchange)
    events: List[Dict[str, Any]] = strategy.event_notifier.events  # type: ignore[attr-defined]

    snapshot = make_snapshot(quantity=Decimal("1"), entry_price=Decimal("100"))
    strategy.grid_state.last_stop_loss_trigger = 0
    strategy.grid_state.active_close_orders = []
    strategy.grid_state.margin_ratio = Decimal("0.05")

    triggered = await strategy.risk_controller.enforce_stop_loss(
        snapshot=snapshot,
        current_price=Decimal("99.8"),  # below PnL-derived stop (~0.1%)
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
    strategy.grid_state.margin_ratio = Decimal("0.05")

    triggered = await strategy.risk_controller.enforce_stop_loss(
        snapshot=snapshot,
        current_price=Decimal("100.3"),  # above PnL-derived stop (~0.1%)
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


@pytest.mark.asyncio
async def test_stop_price_forces_exit(reset_grid_event_notifier):
    config = make_config(direction="buy")
    config.stop_price = Decimal("95")
    exchange = DummyExchange()
    strategy = GridStrategy(config=config, exchange_client=exchange)
    events: List[Dict[str, Any]] = strategy.event_notifier.events  # type: ignore[attr-defined]

    strategy.grid_state.last_known_position = Decimal("1")
    strategy.grid_state.active_close_orders = [
        GridOrder(order_id="close-1", price=Decimal("110"), size=Decimal("1"), side="sell")
    ]
    strategy.grid_state.pending_open_order_id = "open-1"
    strategy.grid_state.pending_open_quantity = Decimal("1")

    async def fake_positions():
        return Decimal("1")

    exchange.get_account_positions = fake_positions  # type: ignore[assignment]
    exchange.best_bid = Decimal("94")
    exchange.best_ask = Decimal("94")

    result = await strategy.should_execute()

    assert result is False
    assert exchange.market_orders, "Expected market exit on stop price breach"
    assert exchange.market_orders[0]["side"] == "sell"
    assert "close-1" in exchange.cancelled_orders
    assert strategy.grid_state.pending_open_order_id is None
    assert not strategy.grid_state.active_close_orders
    event_types = [evt["event_type"] for evt in events]
    assert "stop_price_triggered" in event_types
    assert "stop_price_shutdown" in event_types


@pytest.mark.asyncio
async def test_pause_price_pauses_entries_but_runs_maintenance(reset_grid_event_notifier):
    config = make_config(direction="buy")
    config.pause_price = Decimal("105")
    exchange = DummyExchange()
    strategy = GridStrategy(config=config, exchange_client=exchange)

    exchange.best_bid = Decimal("106")
    exchange.best_ask = Decimal("106")

    flags = {"update_called": False, "recovery_called": False}

    async def fake_update_active_orders():
        flags["update_called"] = True

    async def fake_recovery(current_price: Decimal, current_position: Optional[Decimal] = None):
        flags["recovery_called"] = True

    strategy.order_closer.update_active_orders = fake_update_active_orders  # type: ignore[assignment]
    strategy.recovery_operator.run_recovery_checks = fake_recovery  # type: ignore[assignment]

    result = await strategy.should_execute()

    assert result is False
    assert flags["update_called"] is True
    assert flags["recovery_called"] is True
    assert exchange.market_orders == []


def make_tracked_position(**overrides: Any) -> TrackedPosition:
    position_id = overrides.pop("position_id", f"grid-{int(time.time() * 1000)}")
    defaults = dict(
        position_id=position_id,
        entry_price=Decimal("100"),
        size=Decimal("2"),
        side="long",
        open_time=time.time() - 4000,
        close_order_ids=["close-1"],
        recovery_attempts=0,
        hedged=False,
        last_recovery_time=0.0,
        entry_client_order_index=321,
        close_client_order_indices=[654],
        post_only_retry_count=0,
        last_post_only_retry=0.0,
    )
    defaults.update(overrides)
    return TrackedPosition(**defaults)


@pytest.mark.asyncio
async def test_ensure_close_orders_reposts_cancelled_order(reset_grid_event_notifier):
    config = make_config()
    exchange = DummyExchange()
    strategy = GridStrategy(config=config, exchange_client=exchange)

    tracked = make_tracked_position(
        side="long",
        size=Decimal("1"),
        close_order_ids=["old-1"],
        close_client_order_indices=[1111],
        last_post_only_retry=0.0,
    )
    strategy.position_manager.clear()
    strategy.position_manager.track(tracked)
    strategy.grid_state.active_close_orders = []

    await strategy.order_closer.ensure_close_orders(
        current_position=Decimal("1"),
        best_bid=Decimal("100"),
        best_ask=Decimal("101"),
    )

    assert len(exchange.close_orders) == 1
    placed = exchange.close_orders[-1]
    assert tracked.post_only_retry_count == 1
    assert tracked.close_order_ids[0] == str(placed["client_order_id"])
    assert tracked.close_client_order_indices[-1] == placed["client_order_id"]
    assert any(order.order_id == str(placed["client_order_id"]) for order in strategy.grid_state.active_close_orders)




@pytest.mark.asyncio
async def test_ensure_close_orders_runs_when_snapshot_zero(reset_grid_event_notifier):
    config = make_config()
    exchange = DummyExchange()
    strategy = GridStrategy(config=config, exchange_client=exchange)

    tracked = make_tracked_position(
        side="long",
        size=Decimal("1"),
        close_order_ids=["open-close"],
        close_client_order_indices=[999],
        last_post_only_retry=0.0,
    )
    strategy.position_manager.clear()
    strategy.position_manager.track(tracked)
    strategy.grid_state.active_close_orders = []

    await strategy.order_closer.ensure_close_orders(
        current_position=Decimal("0"),
        best_bid=Decimal("100"),
        best_ask=Decimal("101"),
    )

    assert exchange.close_orders, "Expected close order to be re-posted even if snapshot shows zero position"
    assert tracked.close_order_ids

@pytest.mark.asyncio
async def test_ensure_close_orders_market_fallback(reset_grid_event_notifier, monkeypatch):
    config = make_config()
    exchange = DummyExchange()
    strategy = GridStrategy(config=config, exchange_client=exchange)

    tracked = make_tracked_position(
        side="long",
        size=Decimal("1"),
        close_order_ids=["old-1"],
        last_post_only_retry=0.0,
    )
    strategy.position_manager.clear()
    strategy.position_manager.track(tracked)
    strategy.grid_state.active_close_orders = []

    calls: Dict[str, Any] = {}

    async def fake_market_close(position: Decimal, reason: str, tracked_position=None) -> bool:
        calls["args"] = (position, reason, tracked_position.position_id if tracked_position else None)
        return True

    monkeypatch.setattr(strategy.order_closer, "market_close", fake_market_close)
    monkeypatch.setattr(close_position_module, "POST_ONLY_CLOSE_RETRY_LIMIT", 0)

    await strategy.order_closer.ensure_close_orders(
        current_position=Decimal("1"),
        best_bid=Decimal("100"),
        best_ask=Decimal("101"),
    )

    assert "args" in calls
    assert calls["args"][0] == Decimal("1")
    assert calls["args"][2] == tracked.position_id


@pytest.mark.asyncio
async def test_recover_from_canceled_entry_respects_server_id_mapping(reset_grid_event_notifier):
    config = make_config()
    exchange = DummyExchange()
    strategy = GridStrategy(config=config, exchange_client=exchange)

    strategy.grid_state.pending_open_order_id = "111"
    strategy.grid_state.pending_position_id = "grid-1"
    exchange.client_to_server_ids["111"] = "999"

    async def fake_active_orders(_contract_id: str):
        return [
            OrderInfo(
                order_id="999",
                side="buy",
                size=Decimal("1"),
                price=Decimal("100"),
                status="OPEN",
                filled_size=Decimal("0"),
                remaining_size=Decimal("1"),
            )
        ]

    exchange.get_active_orders = fake_active_orders  # type: ignore[assignment]

    result = await strategy._recover_from_canceled_entry()

    assert result is False


@pytest.mark.asyncio
async def test_recover_from_canceled_entry_ignores_when_fill_detected(reset_grid_event_notifier):
    config = make_config()
    exchange = DummyExchange()
    strategy = GridStrategy(config=config, exchange_client=exchange)

    strategy.grid_state.pending_open_order_id = "222"
    strategy.grid_state.pending_position_id = "grid-2"
    strategy.grid_state.filled_client_order_index = 222
    strategy.grid_state.filled_quantity = Decimal("0.5")

    async def fake_active_orders(_contract_id: str):
        return []

    exchange.get_active_orders = fake_active_orders  # type: ignore[assignment]

    result = await strategy._recover_from_canceled_entry()

    assert result is False


@pytest.mark.asyncio
async def test_market_close_targeted_cleanup_preserves_other_positions(reset_grid_event_notifier):
    config = make_config()
    exchange = DummyExchange()
    strategy = GridStrategy(config=config, exchange_client=exchange)

    target = make_tracked_position(
        position_id="grid-1",
        side="long",
        size=Decimal("2"),
        close_order_ids=["close-1"],
        close_client_order_indices=[111],
        entry_client_order_index=55,
    )
    other = make_tracked_position(
        position_id="grid-2",
        side="long",
        size=Decimal("2"),
        close_order_ids=["close-2"],
        close_client_order_indices=[222],
        entry_client_order_index=66,
    )

    strategy.position_manager.clear()
    strategy.position_manager.track(target)
    strategy.position_manager.track(other)

    strategy.grid_state.active_close_orders = [
        GridOrder(order_id="close-1", price=Decimal("100"), size=Decimal("2"), side="sell"),
        GridOrder(order_id="close-2", price=Decimal("100"), size=Decimal("2"), side="sell"),
    ]
    strategy.grid_state.order_index_to_position_id[111] = "grid-1"
    strategy.grid_state.order_index_to_position_id[222] = "grid-2"

    result = await strategy.order_closer.market_close(
        Decimal("2"),
        "test-target",
        tracked_position=target,
    )

    assert result is True
    assert "close-1" in exchange.cancelled_orders
    assert "close-2" not in exchange.cancelled_orders
    assert strategy.position_manager.get("grid-2") is not None
    assert strategy.position_manager.get("grid-1") is None
    assert any(order.order_id == "close-2" for order in strategy.grid_state.active_close_orders)
    assert not any(order.order_id == "close-1" for order in strategy.grid_state.active_close_orders)
    assert 111 not in strategy.grid_state.order_index_to_position_id
    assert 222 in strategy.grid_state.order_index_to_position_id


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
async def test_pending_entry_timeout_cancel(reset_grid_event_notifier, monkeypatch):
    config = make_config()
    config.position_timeout_minutes = 1
    exchange = DummyExchange()
    strategy = GridStrategy(config=config, exchange_client=exchange)
    events: List[Dict[str, Any]] = strategy.event_notifier.events  # type: ignore[attr-defined]

    strategy.grid_state.pending_open_order_id = "open-1"
    strategy.grid_state.pending_open_quantity = Decimal("1")
    strategy.grid_state.pending_open_order_time = 0.0
    strategy.grid_state.pending_position_id = "grid-1"
    strategy.grid_state.pending_client_order_index = 101
    strategy.grid_state.order_index_to_position_id[101] = "grid-1"

    async def fake_active_orders(_contract_id: str):
        return []

    exchange.get_active_orders = fake_active_orders  # type: ignore[assignment]
    monkeypatch.setattr(grid_strategy_module.time, "time", lambda: 120.0)

    result = await strategy._recover_from_canceled_entry()

    assert result is True
    assert "open-1" in exchange.cancelled_orders
    assert strategy.grid_state.pending_open_order_id is None
    event_types = [evt["event_type"] for evt in events]
    assert "entry_order_timeout" in event_types


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
async def test_run_recovery_checks_retracks_missing_close(reset_grid_event_notifier):
    config = make_config()
    exchange = DummyExchange()
    strategy = GridStrategy(config=config, exchange_client=exchange)

    tracked = make_tracked_position(
        side="long",
        size=Decimal("1"),
        close_order_ids=["close-missing"],
        open_time=time.time(),
    )
    strategy.position_manager.clear()
    strategy.position_manager.track(tracked)
    strategy.grid_state.active_close_orders = []
    strategy.grid_state.last_known_position = Decimal("1")

    events: List[Dict[str, Any]] = strategy.event_notifier.events  # type: ignore[attr-defined]

    await strategy.recovery_operator.run_recovery_checks(
        current_price=Decimal("100"),
        current_position=Decimal("1"),
    )

    assert strategy.position_manager.count() == 1, "Tracked position should be restored for close retry"
    assert strategy.position_manager.get(tracked.position_id) is not None
    event_types = [evt["event_type"] for evt in events]
    assert "close_order_missing_retracked" in event_types


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
