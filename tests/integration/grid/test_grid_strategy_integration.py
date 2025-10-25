from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional

import pytest

from exchange_clients.base_models import OrderInfo, OrderResult

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


def make_config(direction: str = "buy") -> GridConfig:
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

    def get_exchange_name(self) -> str:
        return "dummy"

    async def fetch_bbo_prices(self, _contract_id: str):
        return self.best_bid, self.best_ask

    async def get_active_orders(self, _contract_id: str) -> List[OrderInfo]:
        return list(self.active_close_order_infos)

    async def get_account_positions(self) -> Decimal:
        return Decimal("0")

    def round_to_tick(self, price: Decimal) -> Decimal:
        tick = self.config.tick_size
        return Decimal(price).quantize(tick, rounding=ROUND_HALF_UP)

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
