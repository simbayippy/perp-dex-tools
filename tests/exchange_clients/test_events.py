import asyncio
from decimal import Decimal

import pytest

from exchange_clients.events import LiquidationEvent, LiquidationEventDispatcher


def test_liquidation_event_dispatcher_fanout():
    async def runner():
        dispatcher = LiquidationEventDispatcher()
        queue_a = dispatcher.register()
        queue_b = dispatcher.register()

        event = LiquidationEvent(
            exchange="lighter",
            symbol="BTC",
            side="sell",
            quantity=Decimal("1"),
            price=Decimal("25000"),
        )

        await dispatcher.emit(event)

        received_a = await asyncio.wait_for(queue_a.get(), timeout=0.1)
        received_b = await asyncio.wait_for(queue_b.get(), timeout=0.1)

        assert received_a == event
        assert received_b == event

    asyncio.run(runner())


def test_liquidation_event_dispatcher_unregister():
    async def runner():
        dispatcher = LiquidationEventDispatcher()
        queue = dispatcher.register()
        dispatcher.unregister(queue)

        event = LiquidationEvent(
            exchange="aster",
            symbol="ETH",
            side="buy",
            quantity=Decimal("2"),
            price=Decimal("1500"),
        )

        await dispatcher.emit(event)

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(queue.get(), timeout=0.05)

    asyncio.run(runner())
