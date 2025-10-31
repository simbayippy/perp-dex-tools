"""
Unit tests for AtomicMultiOrderExecutor.

Tests cover:
1. Successful atomic execution (all orders fill)
2. Partial fill detection and rollback
3. CRITICAL: Rollback race condition fix
4. CRITICAL: Balance validation in pre-flight checks
5. Pre-flight check failures
6. Error handling and edge cases
"""

import pytest
import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock, MagicMock, patch
from typing import List, Dict

from strategies.execution.patterns.atomic_multi_order import (
    AtomicMultiOrderExecutor,
    OrderSpec,
    AtomicExecutionResult,
    _OrderContext,
)
from strategies.execution.patterns.atomic_multi_order.hedge_manager import HedgeManager
from exchange_clients.base_models import OrderResult, OrderInfo


class MockExchangeClient:
    """Mock exchange client for testing."""
    
    def __init__(self, name: str = "test_exchange", should_fill: bool = True):
        self.name = name
        self.should_fill = should_fill
        self.placed_orders = []
        self.canceled_orders = []
        self.order_info_calls = []
        self._balance = Decimal('10000')  # $10k default balance
        
    def get_exchange_name(self) -> str:
        return self.name
    
    async def get_account_balance(self) -> Decimal:
        """Return mock balance."""
        return self._balance
    
    async def place_market_order(self, contract_id: str, quantity: float, side: str) -> OrderResult:
        """Mock market order placement."""
        self.placed_orders.append({
            'type': 'market',
            'contract_id': contract_id,
            'quantity': quantity,
            'side': side
        })
        
        return OrderResult(
            success=True,
            order_id=f"order_{len(self.placed_orders)}",
            side=side,
            size=Decimal(str(quantity)),
            price=Decimal('50000'),
            status='FILLED',
            filled_size=Decimal(str(quantity))
        )
    
    async def cancel_order(self, order_id: str) -> OrderResult:
        """Mock order cancellation."""
        self.canceled_orders.append(order_id)
        return OrderResult(success=True, order_id=order_id, status='CANCELED')
    
    async def get_order_info(self, order_id: str, *, force_refresh: bool = False) -> OrderInfo:
        """Mock order info retrieval."""
        self.order_info_calls.append(order_id)
        
        # Return filled size (can be modified for race condition tests)
        return OrderInfo(
            order_id=order_id,
            side='buy',
            size=Decimal('0.5'),
            price=Decimal('50000'),
            status='FILLED',
            filled_size=Decimal('0.5'),
            remaining_size=Decimal('0')
        )


def _filled_result(exchange_client, symbol, side, order_id="order_1"):
    return {
        'success': True,
        'filled': True,
        'fill_price': Decimal('50000'),
        'filled_quantity': Decimal('1.0'),
        'slippage_usd': Decimal('5.0'),
        'execution_mode_used': 'limit',
        'order_id': order_id,
        'exchange_client': exchange_client,
        'symbol': symbol,
        'side': side
    }


def _unfilled_result(exchange_client, symbol, side):
    return {
        'success': False,
        'filled': False,
        'fill_price': None,
        'filled_quantity': Decimal('0'),
        'slippage_usd': Decimal('0'),
        'execution_mode_used': None,
        'order_id': None,
        'exchange_client': exchange_client,
        'symbol': symbol,
        'side': side
    }


@pytest.fixture
def mock_exchange_client():
    """Fixture for mock exchange client."""
    return MockExchangeClient()


@pytest.fixture
def executor():
    """Fixture for AtomicMultiOrderExecutor."""
    return AtomicMultiOrderExecutor()


# =============================================================================
# TEST: Successful Atomic Execution
# =============================================================================

@pytest.mark.asyncio
async def test_atomic_execution_success(executor, mock_exchange_client):
    """
    Test successful atomic execution where all orders fill.
    
    Scenario:
    - Place 2 orders (long + short)
    - Both fill successfully
    - No rollback needed
    - Result: success=True, all_filled=True
    """
    orders = [
        OrderSpec(
            exchange_client=mock_exchange_client,
            symbol='BTC-PERP',
            side='buy',
            size_usd=Decimal('50000'),
            execution_mode='limit_only'
        ),
        OrderSpec(
            exchange_client=MockExchangeClient("exchange2"),
            symbol='BTC-PERP',
            side='sell',
            size_usd=Decimal('50000'),
            execution_mode='limit_only'
        )
    ]

    executor._place_single_order = AsyncMock(side_effect=[
        _filled_result(orders[0].exchange_client, 'BTC-PERP', 'buy', 'order_1'),
        _filled_result(orders[1].exchange_client, 'BTC-PERP', 'sell', 'order_2'),
    ])

    result = await executor.execute_atomically(
        orders=orders,
        rollback_on_partial=True,
        pre_flight_check=False
    )

    assert result.success is True
    assert result.all_filled is True
    assert len(result.filled_orders) == 2
    assert len(result.partial_fills) == 0
    assert result.rollback_performed is False
    assert result.rollback_cost_usd == Decimal('0')


# =============================================================================
# TEST: Partial Fill Handling (Market Hedge & Failure)
# =============================================================================

@pytest.mark.asyncio
async def test_partial_fill_triggers_market_hedge(executor):
    long_client = MockExchangeClient("exchange1")
    short_client = MockExchangeClient("exchange2")

    orders = [
        OrderSpec(exchange_client=long_client, symbol='BTC-PERP', side='buy', size_usd=Decimal('50000')),
        OrderSpec(exchange_client=short_client, symbol='BTC-PERP', side='sell', size_usd=Decimal('50000')),
    ]

    executor._place_single_order = AsyncMock(side_effect=[
        _filled_result(long_client, 'BTC-PERP', 'buy', 'order_1'),
        _unfilled_result(short_client, 'BTC-PERP', 'sell')
    ])

    with patch('strategies.execution.patterns.atomic_multi_order.hedge_manager.OrderExecutor') as mock_exec_cls:
        hedge_executor = AsyncMock()
        mock_exec_cls.return_value = hedge_executor
        hedge_executor.execute_order.return_value = SimpleNamespace(
            success=True,
            filled=True,
            fill_price=Decimal('50000'),
            filled_quantity=Decimal('1.0'),
            slippage_usd=Decimal('2.0'),
            execution_mode_used='market',
            order_id='hedge_1'
        )

        result = await executor.execute_atomically(
            orders=orders,
            rollback_on_partial=True,
            pre_flight_check=False
        )

    hedge_executor.execute_order.assert_awaited()
    assert result.success is True
    assert result.all_filled is True
    assert result.rollback_performed is False
    assert any(fill.get('hedge') for fill in result.filled_orders)


@pytest.mark.asyncio
async def test_market_hedge_failure_triggers_rollback(executor):
    long_client = MockExchangeClient("exchange1")
    short_client = MockExchangeClient("exchange2")

    orders = [
        OrderSpec(exchange_client=long_client, symbol='BTC-PERP', side='buy', size_usd=Decimal('50000')),
        OrderSpec(exchange_client=short_client, symbol='BTC-PERP', side='sell', size_usd=Decimal('50000')),
    ]

    executor._place_single_order = AsyncMock(side_effect=[
        _filled_result(long_client, 'BTC-PERP', 'buy', 'order_1'),
        _unfilled_result(short_client, 'BTC-PERP', 'sell')
    ])

    with patch('strategies.execution.patterns.atomic_multi_order.hedge_manager.OrderExecutor') as mock_exec_cls:
        hedge_executor = AsyncMock()
        mock_exec_cls.return_value = hedge_executor
        hedge_executor.execute_order.return_value = SimpleNamespace(
            success=False,
            filled=False,
            fill_price=None,
            filled_quantity=Decimal('0'),
            slippage_usd=Decimal('0'),
            execution_mode_used='market',
            order_id='hedge_fail',
            error_message='hedge failed'
        )

        executor._rollback_filled_orders = AsyncMock(return_value=Decimal('3.0'))

        result = await executor.execute_atomically(
            orders=orders,
            rollback_on_partial=True,
            pre_flight_check=False
        )

    hedge_executor.execute_order.assert_awaited()
    executor._rollback_filled_orders.assert_awaited_once()
    assert result.success is False
    assert result.all_filled is False
    assert result.rollback_performed is True
    assert result.rollback_cost_usd == Decimal('3.0')
    assert 'hedge failed' in result.error_message


@pytest.mark.asyncio
async def test_execute_market_hedge_places_market_orders():
    manager = HedgeManager()
    executor = AtomicMultiOrderExecutor()
    long_client = MockExchangeClient("exchange1")
    short_client = MockExchangeClient("exchange2")

    trigger_task = asyncio.create_task(asyncio.sleep(0))
    trigger_ctx_result = _filled_result(long_client, "BTC-PERP", "buy", "order_1")
    trigger_ctx = _OrderContext(
        spec=OrderSpec(exchange_client=long_client, symbol="BTC-PERP", side="buy", size_usd=Decimal("50000")),
        cancel_event=asyncio.Event(),
        task=trigger_task,
        result=trigger_ctx_result,
        completed=True,
    )
    trigger_ctx.record_fill(Decimal("1.0"), Decimal("50000"))

    other_task = asyncio.create_task(asyncio.sleep(0))
    other_ctx = _OrderContext(
        spec=OrderSpec(exchange_client=short_client, symbol="BTC-PERP", side="sell", size_usd=Decimal("50000")),
        cancel_event=asyncio.Event(),
        task=other_task,
    )

    with patch(
        "strategies.execution.patterns.atomic_multi_order.hedge_manager.OrderExecutor"
    ) as mock_exec_cls:
        hedge_executor = AsyncMock()
        mock_exec_cls.return_value = hedge_executor
        hedge_executor.execute_order.return_value = SimpleNamespace(
            success=True,
            filled=True,
            fill_price=Decimal("50010"),
            filled_quantity=Decimal("1.0"),
            slippage_usd=Decimal("1.5"),
            execution_mode_used="market",
            order_id="hedge_order",
        )

        success, error = await manager.hedge(trigger_ctx, [trigger_ctx, other_ctx], executor.logger)

    assert success is True
    assert error is None
    assert other_ctx.completed is True
    assert other_ctx.filled_quantity > Decimal("0")

    await asyncio.gather(trigger_task, other_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_execute_market_hedge_skips_already_filled_contexts():
    manager = HedgeManager()
    executor = AtomicMultiOrderExecutor()
    long_client = MockExchangeClient("exchange1")
    short_client = MockExchangeClient("exchange2")

    trigger_task = asyncio.create_task(asyncio.sleep(0))
    trigger_ctx_result = _filled_result(long_client, "BTC-PERP", "buy", "order_1")
    trigger_ctx = _OrderContext(
        spec=OrderSpec(exchange_client=long_client, symbol="BTC-PERP", side="buy", size_usd=Decimal("50000")),
        cancel_event=asyncio.Event(),
        task=trigger_task,
        result=trigger_ctx_result,
        completed=True,
    )
    trigger_ctx.record_fill(Decimal("1.0"), Decimal("50000"))

    other_task = asyncio.create_task(asyncio.sleep(0))
    other_ctx = _OrderContext(
        spec=OrderSpec(exchange_client=short_client, symbol="BTC-PERP", side="sell", size_usd=Decimal("50000")),
        cancel_event=asyncio.Event(),
        task=other_task,
        result=_filled_result(short_client, "BTC-PERP", "sell", "order_2"),
        completed=True,
    )
    other_ctx.record_fill(Decimal("1.0"), Decimal("50000"))

    with patch(
        "strategies.execution.patterns.atomic_multi_order.hedge_manager.OrderExecutor"
    ) as mock_exec_cls:
        hedge_executor = AsyncMock()
        mock_exec_cls.return_value = hedge_executor

        success, error = await manager.hedge(trigger_ctx, [trigger_ctx, other_ctx], executor.logger)

    assert success is True
    assert error is None
    hedge_executor.execute_order.assert_not_called()

    await asyncio.gather(trigger_task, other_task, return_exceptions=True)
# =============================================================================
# TEST: CRITICAL FIX #1 - Rollback Race Condition
# =============================================================================

@pytest.mark.asyncio
async def test_rollback_race_condition_protection():
    """
    🔒 CRITICAL TEST: Rollback race condition protection.
    
    Scenario:
    - Order shows 0.5 BTC filled initially
    - Order fills another 0.3 BTC during rollback check
    - System should:
      1. Cancel order immediately
      2. Query actual filled amount (0.8 BTC)
      3. Close 0.8 BTC (not 0.5 BTC)
    
    This test ensures we don't leave directional exposure.
    """
    executor = AtomicMultiOrderExecutor()
    mock_client = MockExchangeClient("test_exchange")
    
    # Track fill amounts - simulates order filling more during rollback
    # The rollback will call get_order_info, which should return 0.8 BTC
    async def mock_get_order_info(order_id: str, *, force_refresh: bool = False) -> OrderInfo:
        """Mock that returns the ACTUAL fill amount (0.8 BTC - race condition!)."""
        # Track the call
        mock_client.order_info_calls.append(order_id)
        
        # Always return 0.8 BTC filled (order filled more during rollback)
        return OrderInfo(
            order_id=order_id,
            side='buy',
            size=Decimal('1.0'),
            price=Decimal('50000'),
            status='PARTIALLY_FILLED',
            filled_size=Decimal('0.8'),  # Actual fill after race condition
            remaining_size=Decimal('0.2')
        )
    
    mock_client.get_order_info = mock_get_order_info
    
    # Create filled order with initial amount
    filled_orders = [{
        'exchange_client': mock_client,
        'symbol': 'BTC-PERP',
        'side': 'buy',
        'filled_quantity': Decimal('0.5'),  # Initial fill
        'fill_price': Decimal('50000'),
        'order_id': 'test_order_1'
    }]
    
    # Execute rollback
    rollback_cost = await executor._rollback_filled_orders(filled_orders)
    
    # Assertions
    # 1. Order should be canceled
    assert 'test_order_1' in mock_client.canceled_orders
    
    # 2. get_order_info should be called (to get actual fill)
    assert 'test_order_1' in mock_client.order_info_calls
    
    # 3. Market close order should be placed for ACTUAL filled amount (0.8 BTC)
    market_orders = [o for o in mock_client.placed_orders if o['type'] == 'market']
    assert len(market_orders) == 1
    assert market_orders[0]['quantity'] == 0.8  # Closes actual fill, not initial
    assert market_orders[0]['side'] == 'sell'  # Opposite side
    
    # 4. Rollback cost should be calculated
    assert rollback_cost >= Decimal('0')


# =============================================================================
# TEST: CRITICAL FIX #2 - Balance Validation
# =============================================================================

@pytest.mark.asyncio
async def test_preflight_balance_validation_success():
    """
    🔒 CRITICAL TEST: Pre-flight balance validation.
    
    Scenario:
    - Account has $10,000 balance
    - Order requires ~$2,000 margin (20% of $10k position)
    - Should pass validation
    """
    executor = AtomicMultiOrderExecutor()
    mock_client = MockExchangeClient("test_exchange")
    mock_client._balance = Decimal('10000')  # $10k balance
    
    with patch('strategies.execution.patterns.atomic_multi_order.executor.LiquidityAnalyzer') as mock_analyzer_class:
        mock_analyzer = Mock()
        mock_analyzer_class.return_value = mock_analyzer
        
        # Mock liquidity check to pass
        mock_report = Mock()
        mock_report.recommendation = 'acceptable'
        mock_analyzer.check_execution_feasibility = AsyncMock(return_value=mock_report)
        mock_analyzer.is_execution_acceptable = Mock(return_value=True)
        
        orders = [
            OrderSpec(
                exchange_client=mock_client,
                symbol='BTC-PERP',
                side='buy',
                size_usd=Decimal('5000')  # $5k position = ~$1k margin
            )
        ]
        
        # Run pre-flight checks
        passed, error = await executor._run_preflight_checks(orders)
        
        # Should pass
        assert passed is True
        assert error is None


@pytest.mark.asyncio
async def test_preflight_balance_validation_failure():
    """
    🔒 CRITICAL TEST: Pre-flight balance validation fails with insufficient funds.
    
    Scenario:
    - Account has $1,000 balance
    - Order requires ~$2,000 margin (20% of $10k position)
    - Should FAIL validation
    """
    executor = AtomicMultiOrderExecutor()
    mock_client = MockExchangeClient("test_exchange")
    mock_client._balance = Decimal('1000')  # Only $1k balance
    
    with patch('strategies.execution.patterns.atomic_multi_order.executor.LiquidityAnalyzer') as mock_analyzer_class:
        mock_analyzer = Mock()
        mock_analyzer_class.return_value = mock_analyzer
        
        # Mock liquidity check (won't even get here if balance fails)
        mock_report = Mock()
        mock_analyzer.check_execution_feasibility = AsyncMock(return_value=mock_report)
        mock_analyzer.is_execution_acceptable = Mock(return_value=True)
        
        orders = [
            OrderSpec(
                exchange_client=mock_client,
                symbol='BTC-PERP',
                side='buy',
                size_usd=Decimal('10000')  # $10k position = ~$2k margin needed
            )
        ]
        
        # Run pre-flight checks
        passed, error = await executor._run_preflight_checks(orders)
        
        # Should FAIL
        assert passed is False
        assert error is not None
        assert 'Insufficient balance' in error
        assert 'test_exchange' in error


@pytest.mark.asyncio
async def test_preflight_multiple_exchanges_balance_check():
    """
    Test balance validation across multiple exchanges.
    
    Scenario:
    - Exchange 1: $5k balance, needs ~$1k (OK)
    - Exchange 2: $500 balance, needs ~$1k (FAIL)
    - Should fail overall
    """
    executor = AtomicMultiOrderExecutor()
    mock_client_1 = MockExchangeClient("exchange1")
    mock_client_1._balance = Decimal('5000')
    
    mock_client_2 = MockExchangeClient("exchange2")
    mock_client_2._balance = Decimal('500')  # Insufficient!
    
    with patch('strategies.execution.patterns.atomic_multi_order.executor.LiquidityAnalyzer') as mock_analyzer_class:
        mock_analyzer = Mock()
        mock_analyzer_class.return_value = mock_analyzer
        mock_report = Mock()
        mock_analyzer.check_execution_feasibility = AsyncMock(return_value=mock_report)
        mock_analyzer.is_execution_acceptable = Mock(return_value=True)
        
        orders = [
            OrderSpec(
                exchange_client=mock_client_1,
                symbol='BTC-PERP',
                side='buy',
                size_usd=Decimal('5000')  # ~$1k margin
            ),
            OrderSpec(
                exchange_client=mock_client_2,
                symbol='BTC-PERP',
                side='sell',
                size_usd=Decimal('5000')  # ~$1k margin (but only has $500!)
            )
        ]
        
        passed, error = await executor._run_preflight_checks(orders)
        
        # Should fail on exchange2
        assert passed is False
        assert 'exchange2' in error
        assert 'Insufficient balance' in error


# =============================================================================
# TEST: Rollback Cost Calculation
# =============================================================================

@pytest.mark.asyncio
async def test_rollback_cost_calculation():
    """
    Test that rollback cost is calculated correctly.
    
    Scenario:
    - Entry price: $50,000
    - Exit price: $50,100 (slippage)
    - Quantity: 1.0 BTC
    - Expected cost: $100
    """
    executor = AtomicMultiOrderExecutor()
    mock_client = MockExchangeClient("test_exchange")
    
    # Mock get_order_info to return 1.0 BTC filled
    async def mock_get_order_info(order_id, *, force_refresh: bool = False):
        return OrderInfo(
            order_id=order_id,
            side='buy',
            size=Decimal('1.0'),
            price=Decimal('50000'),
            status='FILLED',
            filled_size=Decimal('1.0'),  # Full fill
            remaining_size=Decimal('0')
        )
    
    # Mock market order to return with slippage
    async def mock_place_market_order(contract_id, quantity, side):
        return OrderResult(
            success=True,
            order_id='close_order',
            side=side,
            size=Decimal(str(quantity)),
            price=Decimal('50100'),  # $100 slippage
            status='FILLED',
            filled_size=Decimal(str(quantity))
        )
    
    mock_client.get_order_info = mock_get_order_info
    mock_client.place_market_order = mock_place_market_order
    
    filled_orders = [{
        'exchange_client': mock_client,
        'symbol': 'BTC-PERP',
        'side': 'buy',
        'filled_quantity': Decimal('1.0'),
        'fill_price': Decimal('50000'),
        'order_id': 'entry_order'
    }]
    
    rollback_cost = await executor._rollback_filled_orders(filled_orders)
    
    # Expected: |50100 - 50000| * 1.0 = $100
    assert rollback_cost == Decimal('100')


# =============================================================================
# TEST: Edge Cases
# =============================================================================

@pytest.mark.asyncio
async def test_empty_orders_list():
    """Test handling of empty orders list."""
    executor = AtomicMultiOrderExecutor()
    
    result = await executor.execute_atomically(
        orders=[],
        rollback_on_partial=True,
        pre_flight_check=False
    )
    
    # Should succeed with no orders
    assert result.success is True
    assert result.all_filled is True
    assert len(result.filled_orders) == 0


@pytest.mark.asyncio
async def test_rollback_on_partial_false():
    """
    Test that with rollback_on_partial=False, partial fills are accepted.
    """
    executor = AtomicMultiOrderExecutor()
    mock_client_1 = MockExchangeClient("exchange1")
    mock_client_2 = MockExchangeClient("exchange2")
    
    with patch('strategies.execution.core.order_executor.OrderExecutor') as mock_executor_class:
        mock_executor = AsyncMock()
        mock_executor_class.return_value = mock_executor
        
        # First order succeeds, second fails
        mock_executor.execute_order.side_effect = [
            type('ExecutionResult', (), {
                'success': True,
                'filled': True,
                'fill_price': Decimal('50000'),
                'filled_quantity': Decimal('1.0'),
                'slippage_usd': Decimal('5.0'),
                'execution_mode_used': 'limit',
                'order_id': 'order_1'
            })(),
            type('ExecutionResult', (), {
                'success': False,
                'filled': False,
                'fill_price': None,
                'filled_quantity': Decimal('0'),
                'slippage_usd': Decimal('0'),
                'execution_mode_used': None,
                'order_id': None
            })()
        ]
        
        orders = [
            OrderSpec(exchange_client=mock_client_1, symbol='BTC-PERP', side='buy', size_usd=Decimal('50000')),
            OrderSpec(exchange_client=mock_client_2, symbol='BTC-PERP', side='sell', size_usd=Decimal('50000'))
        ]
        
        result = await executor.execute_atomically(
            orders=orders,
            rollback_on_partial=False,  # Don't rollback
            pre_flight_check=False
        )
        
        # Should not rollback
        assert result.success is False  # Not all filled
        assert result.all_filled is False
        assert result.rollback_performed is False
        assert len(result.filled_orders) == 1  # One order succeeded
        assert len(mock_client_1.placed_orders) == 0  # No rollback orders


@pytest.mark.asyncio
async def test_rollback_handles_missing_order_id():
    """
    Test rollback handles orders without order_id gracefully.
    """
    executor = AtomicMultiOrderExecutor()
    mock_client = MockExchangeClient("test_exchange")
    
    filled_orders = [{
        'exchange_client': mock_client,
        'symbol': 'BTC-PERP',
        'side': 'buy',
        'filled_quantity': Decimal('1.0'),
        'fill_price': Decimal('50000'),
        'order_id': None  # No order ID!
    }]
    
    # Should not crash
    rollback_cost = await executor._rollback_filled_orders(filled_orders)
    
    # Should still attempt to close position
    assert len(mock_client.placed_orders) == 1  # Market close order
    assert rollback_cost >= Decimal('0')


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
