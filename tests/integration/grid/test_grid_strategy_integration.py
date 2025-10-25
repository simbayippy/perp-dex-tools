from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional

import time

import pytest

from exchange_clients.base_models import ExchangePositionSnapshot, OrderInfo, OrderResult

from strategies.implementations.grid.models import GridOrder, TrackedPosition

from strategies.implementations.grid import strategy as grid_strategy_module
from strategies.implementations.grid.config import GridConfig
from strategies.implementations.grid.strategy import GridStrategy


@pytest.fixture(autouse=True)
def patch_event_notifier(monkeypatch):
    events: List[Dict[str, Any]] = []

    class StubNotifier:
        def __init__(self, *args, **kwargs):
            self.events = events

        def notify(self, **payload: Any) -> None:
            events.append(payload)

    monkeypatch.setattr(grid_strategy_module, "GridEventNotifier", StubNotifier)
    yield events
    events.clear()


def make_config(
    direction: str = "buy",
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
        max_margin_usd=Decimal("2000"),
        max_position_size=Decimal("50"),
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
class ExchangeConfig:
    quantity: Decimal = Decimal("1")
    tick_size: Decimal = Decimal("0.1")
    contract_id: str = "BTC-PERP"
    ticker: str = "BTC"


class IntegrationExchange:
    def __init__(self):
        self.config = ExchangeConfig()
        self.best_bid = Decimal("100")
        self.best_ask = Decimal("101")
        self.limit_orders: List[Dict[str, Any]] = []
        self.close_orders: List[Dict[str, Any]] = []
        self.market_orders: List[Dict[str, Any]] = []
        self.cancelled_orders: List[str] = []
        self.active_close_order_infos: List[OrderInfo] = []
        self.next_market_success = True
        self.position_quantity = Decimal("0")
        self.position_entry_price = Decimal("0")
        self.position_margin = Decimal("0")

    def get_exchange_name(self) -> str:
        return "dummy"

    async def fetch_bbo_prices(self, _contract_id: str):
        return self.best_bid, self.best_ask

    async def get_active_orders(self, _contract_id: str) -> List[OrderInfo]:
        return list(self.active_close_order_infos)

    async def get_account_positions(self) -> Decimal:
        return self.position_quantity

    def round_to_tick(self, price: Decimal) -> Decimal:
        tick = self.config.tick_size
        return Decimal(price).quantize(tick, rounding=ROUND_HALF_UP)

    def round_to_step(self, quantity: Decimal) -> Decimal:
        return quantity

    async def get_position_snapshot(self, _symbol: str) -> Optional[ExchangePositionSnapshot]:
        if self.position_quantity == 0:
            return None
        exposure = abs(self.position_quantity) * (self.position_entry_price or self.best_bid)
        return ExchangePositionSnapshot(
            symbol=self.config.ticker,
            quantity=self.position_quantity,
            side="long" if self.position_quantity > 0 else "short",
            entry_price=self.position_entry_price,
            mark_price=self.best_bid if self.position_quantity > 0 else self.best_ask,
            exposure_usd=Decimal(exposure),
            unrealized_pnl=None,
            realized_pnl=None,
            funding_accrued=None,
            margin_reserved=self.position_margin,
            leverage=None,
            liquidation_price=None,
            timestamp=None,
            metadata={},
        )

    async def place_limit_order(self, *, contract_id: str, quantity: Decimal, price: Decimal, side: str) -> OrderResult:
        order_id = f"open-{len(self.limit_orders) + 1}"
        entry = {
            "order_id": order_id,
            "contract_id": contract_id,
            "quantity": Decimal(str(quantity)),
            "price": Decimal(str(price)),
            "side": side,
        }
        self.limit_orders.append(entry)
        return OrderResult(
            success=True,
            order_id=order_id,
            side=side,
            size=Decimal(str(quantity)),
            price=Decimal(str(price)),
            status="OPEN",
            filled_size=Decimal("0"),
        )

    async def place_close_order(self, *, contract_id: str, quantity: Decimal, price: Decimal, side: str) -> OrderResult:
        order_id = f"close-{len(self.close_orders) + 1}"
        entry = {
            "order_id": order_id,
            "contract_id": contract_id,
            "quantity": Decimal(str(quantity)),
            "price": Decimal(str(price)),
            "side": side,
        }
        self.close_orders.append(entry)
        self.active_close_order_infos = [
            OrderInfo(
                order_id=order_id,
                side=side,
                size=Decimal(str(quantity)),
                price=Decimal(str(price)),
                status="OPEN",
                filled_size=Decimal("0"),
                remaining_size=Decimal(str(quantity)),
                cancel_reason="",
            )
        ]
        return OrderResult(
            success=True,
            order_id=order_id,
            side=side,
            size=Decimal(str(quantity)),
            price=Decimal(str(price)),
            status="OPEN",
            filled_size=Decimal("0"),
        )

    async def place_market_order(self, *, contract_id: str, quantity: Decimal, side: str) -> OrderResult:
        order_id = f"market-{len(self.market_orders) + 1}"
        self.market_orders.append(
            {
                "order_id": order_id,
                "contract_id": contract_id,
                "quantity": Decimal(str(quantity)),
                "side": side,
            }
        )
        if self.next_market_success:
            return OrderResult(
                success=True,
                order_id=order_id,
                side=side,
                size=Decimal(str(quantity)),
                price=self.best_ask if side == "buy" else self.best_bid,
                status="FILLED",
                filled_size=Decimal(str(quantity)),
            )
        return OrderResult(
            success=False,
            order_id=order_id,
            side=side,
            size=Decimal(str(quantity)),
            price=None,
            status="REJECTED",
            filled_size=Decimal("0"),
            error_message="simulated failure",
        )

    async def cancel_order(self, order_id: str):
        self.cancelled_orders.append(order_id)
        self.active_close_order_infos = [
            info for info in self.active_close_order_infos if info.order_id != order_id
        ]


@pytest.mark.asyncio
async def test_grid_cycle_places_open_and_close_order(patch_event_notifier):
    config = make_config(direction="buy")
    exchange = IntegrationExchange()
    strategy = GridStrategy(config=config, exchange_client=exchange)

    should_trade = await strategy.should_execute()
    assert should_trade is True

    open_result = await strategy.execute_strategy()
    assert open_result["action"] == "order_placed"
    assert exchange.limit_orders
    open_price = exchange.limit_orders[0]["price"]
    open_size = exchange.limit_orders[0]["quantity"]

    # Simulate fill notification from execution engine
    strategy.notify_order_filled(open_price, open_size)

    close_result = await strategy.execute_strategy()
    assert close_result["action"] == "order_placed"
    assert exchange.close_orders

    events = patch_event_notifier
    event_types = [evt["event_type"] for evt in events]
    assert "position_tracked" in event_types
    assert strategy.grid_state.tracked_positions


@pytest.mark.asyncio
async def test_execute_strategy_respects_margin_cap(monkeypatch, patch_event_notifier):
    config = make_config(direction="buy")
    config.max_margin_usd = Decimal("1000")
    exchange = IntegrationExchange()
    strategy = GridStrategy(config=config, exchange_client=exchange)

    async def fake_prepare(reference_price: Decimal):
        strategy.grid_state.last_known_position = Decimal("0")
        strategy.grid_state.last_known_margin = Decimal("1200")
        strategy.grid_state.margin_ratio = Decimal("1")
        return Decimal("0"), None

    monkeypatch.setattr(strategy, "_prepare_risk_snapshot", fake_prepare)

    # We still run should_execute to trigger state refresh
    should_trade = await strategy.should_execute()
    assert should_trade is True

    result = await strategy.execute_strategy()
    assert result["action"] == "wait"
    assert "Margin cap" in result["message"]
    assert not exchange.limit_orders

    events = patch_event_notifier
    assert events
    assert events[-1]["event_type"] == "margin_cap_hit"


@pytest.mark.asyncio
async def test_stop_loss_execution_triggers_market_exit(patch_event_notifier, monkeypatch):
    config = make_config(direction="buy")
    exchange = IntegrationExchange()
    strategy = GridStrategy(config=config, exchange_client=exchange)

    assert await strategy.should_execute() is True
    open_result = await strategy.execute_strategy()
    assert open_result["action"] == "order_placed"
    open_price = exchange.limit_orders[0]["price"]
    open_size = exchange.limit_orders[0]["quantity"]

    strategy.notify_order_filled(open_price, open_size)
    close_result = await strategy.execute_strategy()
    assert close_result["action"] == "order_placed"
    close_order_id = exchange.close_orders[0]["order_id"]
    close_price = exchange.close_orders[0]["price"]

    # Seed state to simulate live position
    exchange.position_quantity = open_size
    exchange.position_entry_price = open_price
    exchange.position_margin = Decimal("100")
    exchange.active_close_order_infos = [
        OrderInfo(
            order_id=close_order_id,
            side="sell",
            size=open_size,
            price=close_price,
            status="OPEN",
            filled_size=Decimal("0"),
            remaining_size=open_size,
            cancel_reason="",
        )
    ]
    strategy.grid_state.active_close_orders = [
        GridOrder(order_id=close_order_id, price=close_price, size=open_size, side="sell")
    ]

    # Price moves against position to trigger stop-loss
    exchange.best_bid = Decimal("95")
    exchange.best_ask = Decimal("96")

    should_trade = await strategy.should_execute()
    assert should_trade is False
    assert exchange.market_orders
    assert exchange.market_orders[-1]["side"] == "sell"
    assert close_order_id in exchange.cancelled_orders

    event_types = [evt["event_type"] for evt in patch_event_notifier]
    assert "stop_loss_initiated" in event_types
    assert "stop_loss_executed" in event_types


@pytest.mark.asyncio
async def test_recovery_ladder_executes_new_orders(patch_event_notifier, monkeypatch):
    config = make_config(direction="buy")
    exchange = IntegrationExchange()
    strategy = GridStrategy(config=config, exchange_client=exchange)

    base_time = 1_000_000
    monkeypatch.setattr(grid_strategy_module.time, "time", lambda: base_time)

    tracked = TrackedPosition(
        entry_price=Decimal("100"),
        size=Decimal("1"),
        side="long",
        open_time=base_time - 7200,
        close_order_ids=["close-1"],
    )
    strategy.grid_state.tracked_positions = [tracked]
    strategy.grid_state.active_close_orders = [
        GridOrder(order_id="close-1", price=Decimal("110"), size=Decimal("1"), side="sell")
    ]
    exchange.active_close_order_infos = [
        OrderInfo(
            order_id="close-1",
            side="sell",
            size=Decimal("1"),
            price=Decimal("110"),
            status="OPEN",
            filled_size=Decimal("0"),
            remaining_size=Decimal("1"),
            cancel_reason="",
        )
    ]

    await strategy._run_recovery_checks(current_price=Decimal("90"))

    assert "close-1" in exchange.cancelled_orders
    assert len(exchange.close_orders) == 3
    assert len(strategy.grid_state.tracked_positions[0].close_order_ids) == 3

    event_types = [evt["event_type"] for evt in patch_event_notifier]
    assert "recovery_ladder_start" in event_types
    assert "recovery_ladder_orders_active" in event_types


@pytest.mark.asyncio
async def test_recovery_hedge_executes_market_exit(patch_event_notifier, monkeypatch):
    config = make_config(direction="buy")
    config.recovery_mode = "hedge"
    exchange = IntegrationExchange()
    strategy = GridStrategy(config=config, exchange_client=exchange)

    base_time = 2_000_000
    monkeypatch.setattr(grid_strategy_module.time, "time", lambda: base_time)

    tracked = TrackedPosition(
        entry_price=Decimal("100"),
        size=Decimal("2"),
        side="long",
        open_time=base_time - 7200,
        close_order_ids=["close-1"],
    )
    strategy.grid_state.tracked_positions = [tracked]
    strategy.grid_state.active_close_orders = [
        GridOrder(order_id="close-1", price=Decimal("110"), size=Decimal("2"), side="sell")
    ]
    exchange.active_close_order_infos = [
        OrderInfo(
            order_id="close-1",
            side="sell",
            size=Decimal("2"),
            price=Decimal("110"),
            status="OPEN",
            filled_size=Decimal("0"),
            remaining_size=Decimal("2"),
            cancel_reason="",
        )
    ]

    await strategy._run_recovery_checks(current_price=Decimal("90"))

    assert exchange.market_orders
    assert exchange.market_orders[-1]["side"] == "sell"
    assert strategy.grid_state.last_known_position == Decimal("0")
    assert strategy.grid_state.last_known_margin == Decimal("0")
    assert strategy.grid_state.margin_ratio is None

    event_types = [evt["event_type"] for evt in patch_event_notifier]
    assert "recovery_hedge_start" in event_types
    assert "recovery_hedge_executed" in event_types


@pytest.mark.asyncio
async def test_resume_updates_active_orders(monkeypatch, patch_event_notifier):
    config = make_config(direction="buy")
    exchange = IntegrationExchange()
    strategy = GridStrategy(config=config, exchange_client=exchange)

    exchange.active_close_order_infos = [
        OrderInfo(
            order_id="close-legacy",
            side="sell",
            size=Decimal("1"),
            price=Decimal("110"),
            status="OPEN",
            filled_size=Decimal("0"),
            remaining_size=Decimal("1"),
            cancel_reason="",
        )
    ]
    strategy.grid_state.active_close_orders = []

    should_trade = await strategy.should_execute()
    assert should_trade is False
    assert strategy.grid_state.active_close_orders
    assert strategy.grid_state.active_close_orders[0].order_id == "close-legacy"
