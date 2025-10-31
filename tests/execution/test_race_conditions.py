"""Race-condition oriented tests for execution and position management."""

import asyncio
from decimal import Decimal
from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from unittest.mock import AsyncMock

from strategies.execution.patterns.atomic_multi_order import AtomicMultiOrderExecutor
from strategies.implementations.funding_arbitrage.models import FundingArbPosition
import strategies.implementations.funding_arbitrage.position_manager as pm


# ============================================================================
# Atomic executor rollback behaviours
# ============================================================================

@pytest.mark.asyncio
async def test_order_fills_during_cancellation():
    executor = AtomicMultiOrderExecutor()

    fill_stage = ['partial']  # Tracks fill state

    class RacyMockClient:
        def __init__(self):
            self.placed_orders = []
            self.canceled_orders = []
            self.order_info_calls = []

        def get_exchange_name(self):
            return 'racy_exchange'

        async def cancel_order(self, order_id: str):
            self.canceled_orders.append(order_id)
            fill_stage[0] = 'filled'
            await asyncio.sleep(0.01)
            return SimpleNamespace(success=True, order_id=order_id, status='CANCELED')

        async def get_order_info(self, order_id: str, *, force_refresh: bool = False):
            self.order_info_calls.append(order_id)
            filled = Decimal('0.5') if fill_stage[0] == 'partial' else Decimal('1.0')
            return SimpleNamespace(
                order_id=order_id,
                side='buy',
                size=Decimal('1.0'),
                price=Decimal('50000'),
                status='FILLED' if filled == Decimal('1.0') else 'PARTIALLY_FILLED',
                filled_size=filled,
                remaining_size=Decimal('1.0') - filled,
            )

        async def place_market_order(self, contract_id, quantity, side):
            self.placed_orders.append({
                'contract_id': contract_id,
                'quantity': quantity,
                'side': side,
            })
            return SimpleNamespace(
                success=True,
                order_id='close_order',
                side=side,
                size=Decimal(str(quantity)),
                price=Decimal('50000'),
                status='FILLED',
            )

    client = RacyMockClient()
    filled_orders = [{
        'exchange_client': client,
        'symbol': 'BTC-PERP',
        'side': 'buy',
        'filled_quantity': Decimal('0.5'),
        'fill_price': Decimal('50000'),
        'order_id': 'racy_order',
    }]

    await executor._rollback_filled_orders(filled_orders)

    assert 'racy_order' in client.canceled_orders
    assert 'racy_order' in client.order_info_calls
    assert client.placed_orders[0]['quantity'] == 1.0


@pytest.mark.asyncio
async def test_multiple_simultaneous_rollbacks():
    executor = AtomicMultiOrderExecutor()

    class MockClient:
        def __init__(self, name):
            self.name = name
            self.rollbacks = []

        def get_exchange_name(self):
            return self.name

        async def cancel_order(self, order_id):
            return SimpleNamespace(success=True, order_id=order_id, status='CANCELED')

        async def get_order_info(self, order_id, *, force_refresh: bool = False):
            return SimpleNamespace(
                order_id=order_id,
                side='buy',
                size=Decimal('1.0'),
                price=Decimal('50000'),
                status='FILLED',
                filled_size=Decimal('1.0'),
                remaining_size=Decimal('0'),
            )

        async def place_market_order(self, contract_id, quantity, side):
            self.rollbacks.append({'contract_id': contract_id, 'quantity': quantity, 'side': side})
            await asyncio.sleep(0.01)
            return SimpleNamespace(
                success=True,
                order_id='rollback',
                side=side,
                size=Decimal(str(quantity)),
                price=Decimal('50000'),
                status='FILLED',
            )

    clients = [MockClient(f'client_{i}') for i in range(3)]

    tasks = []
    for i, client in enumerate(clients):
        filled_orders = [{
            'exchange_client': client,
            'symbol': 'BTC-PERP',
            'side': 'buy',
            'filled_quantity': Decimal('1.0'),
            'fill_price': Decimal('50000'),
            'order_id': f'order_{i}',
        }]
        tasks.append(executor._rollback_filled_orders(filled_orders))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    assert all(not isinstance(r, Exception) for r in results)
    for client in clients:
        assert len(client.rollbacks) == 1


@pytest.mark.asyncio
async def test_rollback_with_slow_exchange_response():
    executor = AtomicMultiOrderExecutor()

    class SlowClient:
        def __init__(self):
            self.operations = []

        def get_exchange_name(self):
            return 'slow_exchange'

        async def cancel_order(self, order_id):
            await asyncio.sleep(0.5)
            return SimpleNamespace(success=True, order_id=order_id, status='CANCELED')

        async def get_order_info(self, order_id, *, force_refresh: bool = False):
            return SimpleNamespace(
                order_id=order_id,
                side='buy',
                size=Decimal('1.0'),
                price=Decimal('50000'),
                status='FILLED',
                filled_size=Decimal('1.0'),
                remaining_size=Decimal('0'),
            )

        async def place_market_order(self, contract_id, quantity, side):
            self.operations.append((contract_id, quantity, side))
            await asyncio.sleep(0.5)
            return SimpleNamespace(success=True, order_id='rollback', side=side, price=Decimal('50000'))

    client = SlowClient()
    filled_orders = [{
        'exchange_client': client,
        'symbol': 'BTC-PERP',
        'side': 'buy',
        'filled_quantity': Decimal('1.0'),
        'fill_price': Decimal('50000'),
        'order_id': 'slow_order',
    }]

    await executor._rollback_filled_orders(filled_orders)
    assert client.operations
