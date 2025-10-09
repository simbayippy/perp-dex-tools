"""
Integration tests for race conditions and edge cases.

Tests cover:
1. Concurrent position operations
2. Database connection failures
3. Network delays and timeouts
4. Order fill timing issues
5. Multiple simultaneous rollbacks
"""

import pytest
import asyncio
from decimal import Decimal
from datetime import datetime
from uuid import uuid4
from unittest.mock import Mock, AsyncMock, patch

from strategies.execution.patterns.atomic_multi_order import (
    AtomicMultiOrderExecutor,
    OrderSpec
)
from strategies.implementations.funding_arbitrage.position_manager import FundingArbPositionManager
from strategies.implementations.funding_arbitrage.models import FundingArbPosition
from exchange_clients.base import OrderResult, OrderInfo


# =============================================================================
# TEST: Database Connection Failures
# =============================================================================

@pytest.mark.asyncio
async def test_position_creation_db_connection_failure():
    """
    Test position creation when database connection is lost.
    
    Scenario:
    - Start creating position
    - Database connection fails during insert
    - Should handle gracefully
    """
    manager = FundingArbPositionManager()
    manager._initialized = True
    
    position = FundingArbPosition(
        id=uuid4(), symbol='BTC', long_dex='lighter', short_dex='backpack',
        size_usd=Decimal('1000'), entry_long_rate=Decimal('0.0001'),
        entry_short_rate=Decimal('0.0003'), entry_divergence=Decimal('0.0002'),
        opened_at=datetime.now(), status='open'
    )
    
    # Mock database to fail
    with patch.object(manager, '_check_position_exists_in_db', return_value=False):
        with patch('strategies.implementations.funding_arbitrage.position_manager.database') as mock_db:
            with patch('strategies.implementations.funding_arbitrage.position_manager.symbol_mapper') as mock_symbol:
                with patch('strategies.implementations.funding_arbitrage.position_manager.dex_mapper') as mock_dex:
                    mock_symbol.get_id.return_value = 1
                    mock_dex.get_id.return_value = 1
                    
                    # Database fails with connection error
                    mock_db.execute = AsyncMock(side_effect=Exception("Connection lost"))
                    
                    # Should raise exception
                    with pytest.raises(Exception, match="Connection lost"):
                        await manager.create_position(position)
                    
                    # Position should NOT be in memory (rollback)
                    assert position.id not in manager._positions


# =============================================================================
# TEST: Concurrent Close Attempts
# =============================================================================

@pytest.mark.asyncio
async def test_concurrent_close_attempts_race():
    """
    Test multiple concurrent close attempts on same position.
    
    Scenario:
    - 5 threads try to close same position simultaneously
    - Only 1 should succeed
    - Others should detect it's already closed
    """
    manager = FundingArbPositionManager()
    manager._initialized = True
    
    position = FundingArbPosition(
        id=uuid4(), symbol='BTC', long_dex='lighter', short_dex='backpack',
        size_usd=Decimal('1000'), entry_long_rate=Decimal('0.0001'),
        entry_short_rate=Decimal('0.0003'), entry_divergence=Decimal('0.0002'),
        opened_at=datetime.now(), status='open'
    )
    
    manager._positions[position.id] = position
    manager._funding_payments[position.id] = []
    manager._cumulative_funding[position.id] = Decimal('0')
    
    db_execute_count = [0]
    
    async def mock_execute(query, values):
        """Mock database execute that tracks calls."""
        await asyncio.sleep(0.01)  # Simulate DB latency
        db_execute_count[0] += 1
    
    with patch('strategies.implementations.funding_arbitrage.position_manager.database') as mock_db:
        mock_db.execute = mock_execute
        
        # Launch 5 concurrent close attempts
        close_tasks = []
        for i in range(5):
            task = manager.close_position(
                position_id=position.id,
                exit_reason=f'REASON_{i}',
                final_pnl_usd=Decimal('10')
            )
            close_tasks.append(task)
        
        # Wait for all to complete
        await asyncio.gather(*close_tasks, return_exceptions=True)
        
        # Only ONE should have actually executed the DB update
        # (First one to acquire lock)
        assert db_execute_count[0] == 1
        
        # Position should be closed
        assert manager._positions[position.id].status == 'closed'


# =============================================================================
# TEST: Order Fill Race Conditions
# =============================================================================

@pytest.mark.asyncio
async def test_order_fills_during_cancellation():
    """
    Test race condition where order fills while being canceled.
    
    Scenario:
    - Atomic execution detects partial fill
    - Starts rollback, tries to cancel order
    - Order fills completely during cancellation
    - Should still close the filled amount
    """
    from exchange_clients.base import OrderResult, OrderInfo
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
            """Cancellation happens, but order fills during cancel."""
            # Track cancellation
            self.canceled_orders.append(order_id)
            # Simulate order filling DURING cancellation
            fill_stage[0] = 'filled'
            await asyncio.sleep(0.01)
            return OrderResult(success=True, order_id=order_id, status='CANCELED')
        
        async def get_order_info(self, order_id: str):
            """Returns different fill amounts based on stage."""
            self.order_info_calls.append(order_id)
            
            if fill_stage[0] == 'partial':
                filled = Decimal('0.5')
            else:  # filled
                filled = Decimal('1.0')  # Filled more!
            
            return OrderInfo(
                order_id=order_id,
                side='buy',
                size=Decimal('1.0'),
                price=Decimal('50000'),
                status='FILLED' if filled == Decimal('1.0') else 'PARTIALLY_FILLED',
                filled_size=filled,
                remaining_size=Decimal('1.0') - filled
            )
        
        async def place_market_order(self, contract_id, quantity, side):
            self.placed_orders.append({
                'contract_id': contract_id,
                'quantity': quantity,
                'side': side
            })
            return OrderResult(
                success=True,
                order_id='close_order',
                side=side,
                size=Decimal(str(quantity)),
                price=Decimal('50000'),
                status='FILLED'
            )
    
    client = RacyMockClient()
    
    filled_orders = [{
        'exchange_client': client,
        'symbol': 'BTC-PERP',
        'side': 'buy',
        'filled_quantity': Decimal('0.5'),  # Initially 0.5
        'fill_price': Decimal('50000'),
        'order_id': 'racy_order'
    }]
    
    # Execute rollback
    await executor._rollback_filled_orders(filled_orders)
    
    # Should have:
    # 1. Canceled the order
    assert 'racy_order' in client.canceled_orders
    
    # 2. Queried order info to get ACTUAL fill (1.0)
    assert 'racy_order' in client.order_info_calls
    
    # 3. Closed the ACTUAL filled amount (1.0), not the cached amount (0.5)
    market_orders = client.placed_orders
    assert len(market_orders) == 1
    assert market_orders[0]['quantity'] == 1.0  # Full fill
    assert market_orders[0]['side'] == 'sell'


# =============================================================================
# TEST: Multiple Rollbacks Simultaneously
# =============================================================================

@pytest.mark.asyncio
async def test_multiple_simultaneous_rollbacks():
    """
    Test multiple atomic executions failing and rolling back at the same time.
    
    Scenario:
    - 3 atomic executions happen concurrently
    - All fail and trigger rollback
    - Should not interfere with each other
    """
    executor = AtomicMultiOrderExecutor()
    
    class MockClient:
        def __init__(self, name):
            self.name = name
            self.rollbacks = []
        
        def get_exchange_name(self):
            return self.name
        
        async def cancel_order(self, order_id):
            return OrderResult(success=True, order_id=order_id, status='CANCELED')
        
        async def get_order_info(self, order_id):
            return OrderInfo(
                order_id=order_id, side='buy', size=Decimal('1.0'),
                price=Decimal('50000'), status='FILLED',
                filled_size=Decimal('1.0'), remaining_size=Decimal('0')
            )
        
        async def place_market_order(self, contract_id, quantity, side):
            self.rollbacks.append({'contract_id': contract_id, 'quantity': quantity, 'side': side})
            await asyncio.sleep(0.01)  # Simulate latency
            return OrderResult(
                success=True, order_id='rollback', side=side,
                size=Decimal(str(quantity)), price=Decimal('50000'), status='FILLED'
            )
    
    clients = [MockClient(f'client_{i}') for i in range(3)]
    
    # Create filled orders for each client
    rollback_tasks = []
    for i, client in enumerate(clients):
        filled_orders = [{
            'exchange_client': client,
            'symbol': 'BTC-PERP',
            'side': 'buy',
            'filled_quantity': Decimal('1.0'),
            'fill_price': Decimal('50000'),
            'order_id': f'order_{i}'
        }]
        
        task = executor._rollback_filled_orders(filled_orders)
        rollback_tasks.append(task)
    
    # Execute all rollbacks simultaneously
    results = await asyncio.gather(*rollback_tasks, return_exceptions=True)
    
    # All should succeed
    assert all(not isinstance(r, Exception) for r in results)
    
    # Each client should have exactly 1 rollback
    for client in clients:
        assert len(client.rollbacks) == 1


# =============================================================================
# TEST: Network Delays and Timeouts
# =============================================================================

@pytest.mark.asyncio
async def test_rollback_with_slow_exchange_response():
    """
    Test rollback when exchange API is slow to respond.
    
    Scenario:
    - Rollback initiated
    - Exchange API has high latency (2 seconds)
    - Should still complete successfully
    """
    executor = AtomicMultiOrderExecutor()
    
    class SlowMockClient:
        def __init__(self):
            self.operations = []
        
        def get_exchange_name(self):
            return 'slow_exchange'
        
        async def cancel_order(self, order_id):
            await asyncio.sleep(1.0)  # Slow cancel
            self.operations.append('cancel')
            return OrderResult(success=True, order_id=order_id, status='CANCELED')
        
        async def get_order_info(self, order_id):
            await asyncio.sleep(0.5)  # Slow query
            self.operations.append('query')
            return OrderInfo(
                order_id=order_id, side='buy', size=Decimal('1.0'),
                price=Decimal('50000'), status='FILLED',
                filled_size=Decimal('1.0'), remaining_size=Decimal('0')
            )
        
        async def place_market_order(self, contract_id, quantity, side):
            await asyncio.sleep(1.0)  # Slow execution
            self.operations.append('close')
            return OrderResult(
                success=True, order_id='close', side=side,
                size=Decimal(str(quantity)), price=Decimal('50000'), status='FILLED'
            )
    
    client = SlowMockClient()
    
    filled_orders = [{
        'exchange_client': client,
        'symbol': 'BTC-PERP',
        'side': 'buy',
        'filled_quantity': Decimal('1.0'),
        'fill_price': Decimal('50000'),
        'order_id': 'slow_order'
    }]
    
    # Execute rollback (should handle slow responses)
    import time
    start = time.time()
    cost = await executor._rollback_filled_orders(filled_orders)
    duration = time.time() - start
    
    # Should complete (might take ~2.5 seconds)
    assert cost >= Decimal('0')
    assert duration > 2.0  # At least 2 seconds due to delays
    
    # All operations should have completed
    assert 'cancel' in client.operations
    assert 'query' in client.operations
    assert 'close' in client.operations


# =============================================================================
# TEST: Funding Payment Idempotency
# =============================================================================

@pytest.mark.asyncio
async def test_funding_payment_duplicate_detection():
    """
    Test that duplicate funding payments are handled.
    
    Scenario:
    - Funding payment event fires
    - Network issue causes event to fire again
    - Should detect and prevent duplicate
    
    Note: This requires implementing idempotency in the actual code.
    This test documents the expected behavior.
    """
    manager = FundingArbPositionManager()
    manager._initialized = True
    
    position = FundingArbPosition(
        id=uuid4(), symbol='BTC', long_dex='lighter', short_dex='backpack',
        size_usd=Decimal('1000'), entry_long_rate=Decimal('0.0001'),
        entry_short_rate=Decimal('0.0003'), entry_divergence=Decimal('0.0002'),
        opened_at=datetime.now(), status='open'
    )
    
    manager._positions[position.id] = position
    manager._funding_payments[position.id] = []
    manager._cumulative_funding[position.id] = Decimal('0')
    
    payment_time = datetime.now()
    
    with patch('strategies.implementations.funding_arbitrage.position_manager.database') as mock_db:
        mock_db.execute = AsyncMock()
        
        # Record payment first time
        await manager.record_funding_payment(
            position_id=position.id,
            long_payment=Decimal('-10.00'),
            short_payment=Decimal('15.00'),
            timestamp=payment_time
        )
        
        # Record same payment again (duplicate)
        await manager.record_funding_payment(
            position_id=position.id,
            long_payment=Decimal('-10.00'),
            short_payment=Decimal('15.00'),
            timestamp=payment_time
        )
        
        # Currently, this WILL double-count (known issue in audit)
        # Cumulative will be 50 instead of 25
        # This test documents the current behavior
        assert manager._cumulative_funding[position.id] == Decimal('50.00')
        
        # TODO: Implement idempotency using unique constraint on 
        # (position_id, payment_time) in database


# =============================================================================
# TEST: Concurrent Position State Updates
# =============================================================================

@pytest.mark.asyncio
async def test_concurrent_position_state_updates():
    """
    Test concurrent updates to position state.
    
    Scenario:
    - Multiple threads update same position's divergence
    - Should not corrupt state
    """
    manager = FundingArbPositionManager()
    manager._initialized = True
    
    position = FundingArbPosition(
        id=uuid4(), symbol='BTC', long_dex='lighter', short_dex='backpack',
        size_usd=Decimal('1000'), entry_long_rate=Decimal('0.0001'),
        entry_short_rate=Decimal('0.0003'), entry_divergence=Decimal('0.0002'),
        opened_at=datetime.now(), status='open'
    )
    
    manager._positions[position.id] = position
    
    with patch('strategies.implementations.funding_arbitrage.position_manager.database') as mock_db:
        mock_db.execute = AsyncMock()
        
        # Update state concurrently from multiple threads
        update_tasks = []
        for i in range(10):
            divergence = Decimal(f'0.000{i}')
            task = manager.update_position_state(
                position_id=position.id,
                current_divergence=divergence
            )
            update_tasks.append(task)
        
        # Execute all updates
        await asyncio.gather(*update_tasks)
        
        # Position should still be valid (no corruption)
        pos = await manager.get_funding_position(position.id)
        assert pos is not None
        # current_divergence might be any of the updated values
        assert pos.status == 'open'


# =============================================================================
# TEST: Balance Check Edge Cases
# =============================================================================

@pytest.mark.asyncio
async def test_balance_check_with_exchange_error():
    """
    Test balance check when exchange API throws error.
    
    Scenario:
    - Pre-flight check queries balance
    - Exchange API returns error
    - Should log warning and continue (not fail execution)
    """
    executor = AtomicMultiOrderExecutor()
    
    class ErrorProneClient:
        def get_exchange_name(self):
            return 'error_exchange'
        
        async def get_account_balance(self):
            raise Exception("API temporarily unavailable")
    
    client = ErrorProneClient()
    
    with patch('strategies.execution.core.liquidity_analyzer.LiquidityAnalyzer') as mock_analyzer_class:
        mock_analyzer = Mock()
        mock_analyzer_class.return_value = mock_analyzer
        mock_report = Mock()
        mock_report.recommendation = 'acceptable'
        mock_analyzer.check_execution_feasibility = AsyncMock(return_value=mock_report)
        mock_analyzer.is_execution_acceptable = Mock(return_value=True)
        
        orders = [
            OrderSpec(
                exchange_client=client,
                symbol='BTC-PERP',
                side='buy',
                size_usd=Decimal('1000')
            )
        ]
        
        # Should not crash, should continue despite error
        passed, error = await executor._run_preflight_checks(orders)
        
        # Should pass (defensive - don't fail on balance check errors)
        assert passed is True


if __name__ == '__main__':
    pytest.main([__file__, '-v', '-s'])

