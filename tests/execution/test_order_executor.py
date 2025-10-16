import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from strategies.execution.core.order_executor import OrderExecutor, ExecutionMode


def _client():
    client = SimpleNamespace(
        config=SimpleNamespace(contract_id="BTC"),
        get_exchange_name=lambda: "mockdex",
    )
    client.place_limit_order = AsyncMock()
    client.get_order_info = AsyncMock()
    client.cancel_order = AsyncMock()
    return client


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "offset,best_bid,best_ask",
    [
        (Decimal("0.0001"), Decimal("29950"), Decimal("30000")),
        (Decimal("0"), Decimal("29950"), Decimal("30000")),
        (Decimal("-0.0002"), Decimal("29950"), Decimal("30000")),
    ],
)
async def test_execute_limit_respects_configured_offset(offset, best_bid, best_ask):
    executor = OrderExecutor()
    executor._fetch_bbo_prices_for_limit_order = AsyncMock(return_value=(best_bid, best_ask))

    mock_client = _client()
    mock_client.place_limit_order.return_value = SimpleNamespace(success=True, order_id="abc123")
    mock_client.get_order_info.side_effect = [
        SimpleNamespace(status="FILLED", price=str(best_ask), filled_size="0.002"),
    ]

    notional = Decimal("50")
    result = await executor.execute_order(
        exchange_client=mock_client,
        symbol="BTC-PERP",
        side="buy",
        size_usd=notional,
        mode=ExecutionMode.LIMIT_ONLY,
        limit_price_offset_pct=offset,
    )

    assert result.success and result.filled
    placed_price = mock_client.place_limit_order.await_args.kwargs["price"]
    limit_price = best_ask * (Decimal("1") - offset)
    assert placed_price == pytest.approx(float(limit_price))


@pytest.mark.asyncio
async def test_execute_limit_uses_executor_default_offset():
    custom_default = Decimal("0.0003")
    executor = OrderExecutor(default_limit_price_offset_pct=custom_default)
    executor._fetch_bbo_prices_for_limit_order = AsyncMock(return_value=(Decimal("20000"), Decimal("20100")))

    mock_client = _client()
    mock_client.place_limit_order.return_value = SimpleNamespace(success=True, order_id="order-1")
    mock_client.get_order_info.side_effect = [
        SimpleNamespace(status="FILLED", price="20100", filled_size="0.01"),
    ]

    await executor.execute_order(
        exchange_client=mock_client,
        symbol="ETH-PERP",
        side="buy",
        size_usd=Decimal("100"),
        mode=ExecutionMode.LIMIT_ONLY,
    )

    placed_price = mock_client.place_limit_order.await_args.kwargs["price"]
    expected_price = Decimal("20100") * (Decimal("1") - custom_default)
    assert placed_price == pytest.approx(float(expected_price))


@pytest.mark.asyncio
async def test_execute_limit_cancellation_event_triggers_cancel_order():
    executor = OrderExecutor()
    executor._fetch_bbo_prices_for_limit_order = AsyncMock(return_value=(Decimal("20000"), Decimal("20100")))

    mock_client = _client()
    mock_client.place_limit_order.return_value = SimpleNamespace(success=True, order_id="cancel-me")
    mock_client.get_order_info.side_effect = [
        SimpleNamespace(status="OPEN", price="20100", filled_size="0"),
    ]

    cancel_event = asyncio.Event()

    task = asyncio.create_task(
        executor.execute_order(
            exchange_client=mock_client,
            symbol="ETH-PERP",
            side="buy",
            size_usd=Decimal("100"),
            mode=ExecutionMode.LIMIT_ONLY,
            cancel_event=cancel_event,
            limit_price_offset_pct=Decimal("0"),
            timeout_seconds=5.0,
        )
    )

    await asyncio.sleep(0)  # allow order placement
    cancel_event.set()
    result = await task

    mock_client.cancel_order.assert_awaited_once_with("cancel-me")
    assert result.filled is False
    assert result.execution_mode_used == "limit_cancelled"
