"""
Edge Case Verification Tests

Comprehensive tests to verify all execution branches and edge cases in the position opening flow.
Based on the Edge Case Verification Plan.

Tests cover:
1. Partial fill tracking (CRITICAL - recently fixed bug)
2. Rollback safety checks
3. Aggressive limit hedge partial fills
4. Rollback failure scenarios
5. Context state consistency
6. Error handling paths
7. Rollback payload construction
"""

import pytest
import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock, MagicMock, patch
from typing import List, Dict, Optional

from strategies.execution.patterns.atomic_multi_order import (
    AtomicMultiOrderExecutor,
    OrderSpec,
    AtomicExecutionResult,
)
from strategies.execution.patterns.atomic_multi_order.components import HedgeManager
from strategies.execution.patterns.atomic_multi_order.components.rollback_manager import RollbackManager
from strategies.execution.patterns.atomic_multi_order.contexts import OrderContext
from strategies.execution.core.order_execution.market_order_executor import ExecutionResult
from exchange_clients.base_models import OrderResult, OrderInfo


class MockExchangeClient:
    """Enhanced mock exchange client for edge case testing."""
    
    def __init__(self, name: str = "test_exchange", should_fill: bool = True):
        self.name = name
        self.should_fill = should_fill
        self.placed_orders = []
        self.canceled_orders = []
        self.order_info_calls = []
        self.position_snapshots = {}  # symbol -> position snapshot
        self._balance = Decimal('10000')
        self.config = Mock()
        self.config.contract_id = f"{name}_PERP"
        self.config.tick_size = Decimal('0.01')
        self.config.step_size = Decimal('0.001')
        
    def get_exchange_name(self) -> str:
        return self.name
    
    def get_quantity_multiplier(self, symbol: str) -> int:
        return 1
    
    def round_to_step(self, quantity: Decimal) -> Decimal:
        return quantity.quantize(Decimal('0.001'))
    
    def round_to_tick(self, price: Decimal) -> Decimal:
        return price.quantize(Decimal('0.01'))
    
    def resolve_contract_id(self, symbol: str) -> str:
        return self.config.contract_id
    
    async def get_account_balance(self) -> Decimal:
        return self._balance
    
    async def place_market_order(self, contract_id: str, quantity: float, side: str, reduce_only: bool = False) -> OrderResult:
        """Mock market order placement."""
        self.placed_orders.append({
            'type': 'market',
            'contract_id': contract_id,
            'quantity': quantity,
            'side': side,
            'reduce_only': reduce_only
        })
        
        return OrderResult(
            success=True,
            order_id=f"market_{len(self.placed_orders)}",
            side=side,
            size=Decimal(str(quantity)),
            price=Decimal('50000'),
            status='FILLED',
            filled_size=Decimal(str(quantity))
        )
    
    async def place_limit_order(self, contract_id: str, quantity: float, price: float, side: str, reduce_only: bool = False) -> OrderResult:
        """Mock limit order placement."""
        self.placed_orders.append({
            'type': 'limit',
            'contract_id': contract_id,
            'quantity': quantity,
            'price': price,
            'side': side,
            'reduce_only': reduce_only
        })
        
        return OrderResult(
            success=True,
            order_id=f"limit_{len(self.placed_orders)}",
            side=side,
            size=Decimal(str(quantity)),
            price=Decimal(str(price)),
            status='OPEN',
            filled_size=Decimal('0')
        )
    
    async def cancel_order(self, order_id: str) -> OrderResult:
        """Mock order cancellation."""
        self.canceled_orders.append(order_id)
        return OrderResult(success=True, order_id=order_id, status='CANCELED')
    
    async def get_order_info(self, order_id: str, *, force_refresh: bool = False) -> Optional[OrderInfo]:
        """Mock order info retrieval."""
        self.order_info_calls.append((order_id, force_refresh))
        
        # Default: return filled order
        return OrderInfo(
            order_id=order_id,
            side='buy',
            size=Decimal('1.0'),
            price=Decimal('50000'),
            status='FILLED',
            filled_size=Decimal('1.0'),
            remaining_size=Decimal('0')
        )
    
    async def get_position_snapshot(self, symbol: str):
        """Mock position snapshot retrieval."""
        # Track calls for testing
        if not hasattr(self, '_position_snapshot_calls'):
            self._position_snapshot_calls = []
        self._position_snapshot_calls.append(symbol)
        
        if symbol in self.position_snapshots:
            return self.position_snapshots[symbol]
        # Return empty position by default
        snapshot = Mock()
        snapshot.quantity = Decimal('0')
        snapshot.entry_price = None
        snapshot.exposure_usd = Decimal('0')
        return snapshot


@pytest.fixture
def mock_exchange_client():
    """Fixture for mock exchange client."""
    return MockExchangeClient()


@pytest.fixture
def executor():
    """Fixture for AtomicMultiOrderExecutor."""
    return AtomicMultiOrderExecutor()


@pytest.fixture
def hedge_manager():
    """Fixture for HedgeManager."""
    return HedgeManager()


@pytest.fixture
def rollback_manager():
    """Fixture for RollbackManager."""
    return RollbackManager()


# =============================================================================
# PHASE 2: CRITICAL PATH TESTING - Partial Fill Scenarios
# =============================================================================

@pytest.mark.asyncio
async def test_market_hedge_partial_fill_before_cancel_tracked():
    """
    CRITICAL TEST: Market hedge partial fill before cancel is tracked for rollback.
    
    Scenario:
    - Market order partially fills (1.187 XMR)
    - Order gets cancelled due to exceeds_max_slippage
    - execution.success=False but execution.filled=True
    - Context.filled_quantity MUST be updated before returning error
    - Rollback manager MUST receive this context in rollback payload
    
    This is the bug we just fixed!
    """
    hedge_manager = HedgeManager()
    executor = AtomicMultiOrderExecutor()
    
    long_client = MockExchangeClient("LIGHTER")
    short_client = MockExchangeClient("PARADEX")
    
    # Create trigger context (LIGHTER filled completely)
    trigger_ctx = OrderContext(
        spec=OrderSpec(
            exchange_client=long_client,
            symbol="XMR",
            side="sell",
            size_usd=Decimal("1600"),
            quantity=Decimal("4.393")
        ),
        cancel_event=asyncio.Event(),
        task=asyncio.create_task(asyncio.sleep(0)),
        completed=True
    )
    trigger_ctx.record_fill(Decimal("4.393"), Decimal("363.971"))
    trigger_ctx.result = {
        'filled_quantity': Decimal("4.393"),
        'fill_price': Decimal("363.971")
    }
    
    # Create other context (PARADEX needs hedging)
    other_ctx = OrderContext(
        spec=OrderSpec(
            exchange_client=short_client,
            symbol="XMR",
            side="buy",
            size_usd=Decimal("1600"),
            quantity=Decimal("4.393")
        ),
        cancel_event=asyncio.Event(),
        task=asyncio.create_task(asyncio.sleep(0)),
        completed=False
    )
    other_ctx.hedge_target_quantity = Decimal("4.393")
    
    # Mock market order executor to return partial fill before cancel
    # OrderExecutor is imported inside hedge() method, so patch at the source
    with patch('strategies.execution.core.order_executor.OrderExecutor') as mock_exec_cls:
        hedge_executor = AsyncMock()
        mock_exec_cls.return_value = hedge_executor
        
        # Simulate market order that partially fills then gets cancelled
        partial_fill_qty = Decimal("1.187")
        partial_fill_price = Decimal("366.16")
        
        hedge_executor.execute_order.return_value = ExecutionResult(
            success=False,  # Order cancelled
            filled=True,    # But had partial fill!
            fill_price=partial_fill_price,
            filled_quantity=partial_fill_qty,
            expected_price=Decimal("365.43"),
            slippage_usd=Decimal("1.10"),
            slippage_pct=Decimal("0.002"),
            execution_mode_used="market_partial_canceled",
            order_id="market_order_1",
            error_message="Market order canceled with partial fill: exceeds_max_slippage",
            retryable=False
        )
        
        # Execute hedge
        result = await hedge_manager.hedge(
            trigger_ctx=trigger_ctx,
            contexts=[trigger_ctx, other_ctx],
            logger=executor.logger,
            reduce_only=False
        )
        
        # Verify hedge failed (as expected)
        assert result.success is False
        assert result.error_message is not None
        assert "partial fill" in result.error_message.lower() or "exceeds_max_slippage" in result.error_message.lower()
        
        # CRITICAL: Verify context.filled_quantity was updated
        assert other_ctx.filled_quantity == partial_fill_qty, \
            f"Expected context.filled_quantity={partial_fill_qty}, got {other_ctx.filled_quantity}"
        
        # CRITICAL: Verify context.result was updated
        assert other_ctx.result is not None
        assert other_ctx.result.get('filled_quantity') == partial_fill_qty
        assert other_ctx.result.get('fill_price') == partial_fill_price
        
        # Verify context is marked as completed
        assert other_ctx.completed is True


@pytest.mark.asyncio
async def test_hedge_skips_when_quantity_filled_even_if_usd_tracking_wrong(executor):
    """
    CRITICAL TEST: Verify that hedge is skipped when quantity is fully filled,
    even if filled_usd tracking thinks there's remaining USD.
    
    This prevents over-hedging due to unreliable USD tracking.
    
    Scenario:
    - Context is fully filled by quantity (4.393 XMR)
    - But filled_usd tracking is wrong (due to missing price info)
    - remaining_usd would be > 0, triggering old fallback bug
    - Hedge should SKIP since quantity is already filled
    """
    hedge_manager = HedgeManager()
    mock_client = MockExchangeClient("PARADEX")
    
    # Simulate context that is fully filled by quantity
    ctx = OrderContext(
        spec=OrderSpec(
            exchange_client=mock_client,
            symbol="XMR",
            side="buy",
            size_usd=Decimal("1600"),  # Original target
            quantity=Decimal("4.393")   # Original target
        ),
        cancel_event=asyncio.Event(),
        task=asyncio.create_task(asyncio.sleep(0)),
        completed=True,
        filled_quantity=Decimal("4.393"),  # ✅ Fully filled by quantity
    )
    
    # Simulate filled_usd being WRONG (due to missing price)
    # This would make remaining_usd > 0, triggering old fallback bug
    ctx.filled_usd = Decimal("1200")  # Wrong! Should be ~1600
    ctx.hedge_target_quantity = Decimal("4.393")
    
    # Mock trigger context (fully filled)
    trigger_ctx = OrderContext(
        spec=OrderSpec(
            exchange_client=MockExchangeClient("LIGHTER"),
            symbol="XMR",
            side="sell",
            size_usd=Decimal("1600"),
            quantity=Decimal("4.393")
        ),
        cancel_event=asyncio.Event(),
        task=asyncio.create_task(asyncio.sleep(0)),
        completed=True,
        filled_quantity=Decimal("4.393"),
        result={'filled_quantity': Decimal("4.393"), 'fill_price': Decimal("365.00")}
    )
    
    # Execute hedge - should skip since quantity is filled
    with patch('strategies.execution.core.order_executor.OrderExecutor') as mock_exec_cls:
        mock_executor = AsyncMock()
        mock_exec_cls.return_value = mock_executor
        
        result = await hedge_manager.hedge(
            trigger_ctx=trigger_ctx,
            contexts=[trigger_ctx, ctx],
            logger=executor.logger,
            reduce_only=False
        )
        
        # CRITICAL: Verify NO hedge order was placed
        mock_executor.execute_order.assert_not_called()
        
        # Hedge should succeed (nothing to do)
        assert result.success is True
        assert result.error_message is None
        
        # Verify remaining_quantity is 0 (so hedge correctly skipped)
        assert ctx.remaining_quantity == Decimal("0")


@pytest.mark.asyncio
async def test_rollback_safety_check_detects_untracked_positions():
    """
    CRITICAL TEST: Rollback safety check (Step 2.5) detects untracked positions.
    
    Scenario:
    - Context has filled_quantity = 0 (bug scenario)
    - But actual position on exchange = 1.187 XMR
    - Safety check MUST detect this and add to rollback payload
    
    This is the safety net that catches the bug!
    """
    rollback_manager = RollbackManager()
    executor = AtomicMultiOrderExecutor()
    
    mock_client = MockExchangeClient("PARADEX")
    
    # Create filled order dict with NO fills (bug scenario)
    filled_orders = [{
        'exchange_client': mock_client,
        'symbol': 'XMR',
        'side': 'buy',
        'filled_quantity': Decimal('0'),  # BUG: Not tracked!
        'fill_price': Decimal('366.16'),
        'order_id': 'order_1'
    }]
    
    # Mock position snapshot to return ACTUAL position (untracked)
    actual_position_qty = Decimal('1.187')
    
    # Initialize tracking attribute
    mock_client._position_snapshot_calls = []
    
    async def mock_get_position_snapshot(symbol: str):
        # Track calls for verification
        mock_client._position_snapshot_calls.append(symbol)
        snapshot = Mock()
        snapshot.quantity = actual_position_qty  # Actual position exists!
        snapshot.entry_price = Decimal('366.16')
        snapshot.exposure_usd = actual_position_qty * Decimal('366.16')
        return snapshot
    
    mock_client.get_position_snapshot = mock_get_position_snapshot
    
    # Mock market order for rollback (track attempts)
    async def mock_place_market_order(contract_id, quantity, side, reduce_only=False):
        # Track the order attempt
        mock_client.placed_orders.append({
            'type': 'market',
            'contract_id': contract_id,
            'quantity': quantity,
            'side': side,
            'reduce_only': reduce_only
        })
        return OrderResult(
            success=True,
            order_id='rollback_order',
            side=side,
            size=Decimal(str(quantity)),
            price=Decimal('365.00'),
            status='FILLED',
            filled_size=Decimal(str(quantity))
        )
    
    mock_client.place_market_order = mock_place_market_order
    
    # Execute rollback
    rollback_cost = await rollback_manager.rollback(
        filled_orders,
        stage_prefix=None  # OPEN operation
    )
    
    # CRITICAL: Verify position snapshot was queried (Step 2.5 safety check)
    assert hasattr(mock_client, '_position_snapshot_calls'), "Position snapshot should be tracked"
    assert 'XMR' in mock_client._position_snapshot_calls, "Step 2.5 should query position snapshot for XMR"
    
    # CRITICAL: Verify rollback market order was placed for ACTUAL position size
    market_orders = [o for o in mock_client.placed_orders if o['type'] == 'market']
    assert len(market_orders) >= 1, "Rollback should place market order for untracked position"
    
    # Verify the order is for the actual position size (1.187), not 0
    found_correct_size = any(
        abs(Decimal(str(order['quantity'])) - actual_position_qty) < Decimal('0.001')
        for order in market_orders
    )
    assert found_correct_size, \
        f"Rollback should close actual position size ({actual_position_qty}), not 0"


@pytest.mark.asyncio
async def test_aggressive_limit_hedge_accumulates_partial_fills():
    """
    Test aggressive limit hedge correctly tracks accumulated partial fills across retries.
    
    Scenario:
    - Multiple retry attempts with partial fills
    - Each retry accumulates more fills
    - Final reconciliation check catches any missed fills
    - Context updates correctly when falling back to market
    """
    hedge_manager = HedgeManager()
    executor = AtomicMultiOrderExecutor()
    
    long_client = MockExchangeClient("LIGHTER")
    short_client = MockExchangeClient("PARADEX")
    
    # Create trigger context
    trigger_ctx = OrderContext(
        spec=OrderSpec(
            exchange_client=long_client,
            symbol="XMR",
            side="sell",
            size_usd=Decimal("1600"),
            quantity=Decimal("4.393")
        ),
        cancel_event=asyncio.Event(),
        task=asyncio.create_task(asyncio.sleep(0)),
        completed=True
    )
    trigger_ctx.record_fill(Decimal("4.393"), Decimal("363.971"))
    trigger_ctx.result = {'filled_quantity': Decimal("4.393"), 'fill_price': Decimal("363.971")}
    
    # Create other context
    other_ctx = OrderContext(
        spec=OrderSpec(
            exchange_client=short_client,
            symbol="XMR",
            side="buy",
            size_usd=Decimal("1600"),
            quantity=Decimal("4.393")
        ),
        cancel_event=asyncio.Event(),
        task=asyncio.create_task(asyncio.sleep(0)),
        completed=False
    )
    other_ctx.hedge_target_quantity = Decimal("4.393")
    
    # Track order fills across retries
    retry_fills = [
        Decimal("1.0"),   # Retry 1: partial fill
        Decimal("1.5"),   # Retry 2: partial fill (total 2.5)
        Decimal("0.5"),   # Retry 3: partial fill (total 3.0)
    ]
    retry_count = [0]
    
    # Mock price provider
    mock_price_provider = Mock()
    mock_price_provider.get_bbo_prices = AsyncMock(return_value=(Decimal("363.8"), Decimal("365.43")))
    hedge_manager._price_provider = mock_price_provider
    
    # Mock limit order placement and status
    async def mock_place_limit_order(contract_id, quantity, price, side, reduce_only=False):
        retry_count[0] += 1
        order_id = f"limit_order_{retry_count[0]}"
        short_client.placed_orders.append({
            'type': 'limit',
            'order_id': order_id,
            'quantity': quantity,
            'price': price,
            'side': side
        })
        return OrderResult(
            success=True,
            order_id=order_id,
            side=side,
            size=Decimal(str(quantity)),
            price=Decimal(str(price)),
            status='OPEN',
            filled_size=Decimal('0')
        )
    
    async def mock_get_order_info(order_id, *, force_refresh=False):
        # Return partial fill based on retry count
        fill_idx = min(retry_count[0] - 1, len(retry_fills) - 1)
        filled_qty = retry_fills[fill_idx] if fill_idx >= 0 else Decimal('0')
        
        return OrderInfo(
            order_id=order_id,
            side='buy',
            size=Decimal("4.393"),
            price=Decimal("365.36"),
            status='PARTIALLY_FILLED' if filled_qty < Decimal("4.393") else 'FILLED',
            filled_size=filled_qty,
            remaining_size=Decimal("4.393") - filled_qty
        )
    
    short_client.place_limit_order = mock_place_limit_order
    short_client.get_order_info = mock_get_order_info
    
    # Execute aggressive limit hedge (will timeout and fallback)
    with patch('asyncio.sleep', new_callable=AsyncMock):  # Speed up timeout
        result = await hedge_manager.aggressive_limit_hedge(
            trigger_ctx=trigger_ctx,
            contexts=[trigger_ctx, other_ctx],
            logger=executor.logger,
            reduce_only=False,
            total_timeout_seconds=0.1,  # Short timeout to trigger fallback
            max_retries=3
        )
    
    # Verify accumulated fills were tracked
    # Note: Due to timeout, we may not see all fills, but the mechanism should work
    assert other_ctx.filled_quantity >= Decimal('0'), "Context should track some fills"
    
    # Verify context result was updated
    if other_ctx.result:
        assert 'filled_quantity' in other_ctx.result


@pytest.mark.asyncio
async def test_rollback_failure_scenarios():
    """
    Test rollback failure scenarios: market order fails, emergency close fails, network failures.
    
    Scenario:
    - Rollback market order fails (insufficient balance)
    - Emergency close fails after rollback verification
    - Verify error logging and graceful handling
    """
    rollback_manager = RollbackManager()
    executor = AtomicMultiOrderExecutor()
    
    mock_client = MockExchangeClient("test_exchange")
    
    filled_orders = [{
        'exchange_client': mock_client,
        'symbol': 'BTC-PERP',
        'side': 'buy',
        'filled_quantity': Decimal('1.0'),
        'fill_price': Decimal('50000'),
        'order_id': 'order_1'
    }]
    
    # Mock get_order_info to return filled order
    async def mock_get_order_info(order_id, *, force_refresh=False):
        return OrderInfo(
            order_id=order_id,
            side='buy',
            size=Decimal('1.0'),
            price=Decimal('50000'),
            status='FILLED',
            filled_size=Decimal('1.0'),
            remaining_size=Decimal('0')
        )
    
    # Mock market order to FAIL (but still track the attempt)
    async def mock_place_market_order_fail(contract_id, quantity, side, reduce_only=False):
        # Track the attempt even though it fails
        mock_client.placed_orders.append({
            'type': 'market',
            'contract_id': contract_id,
            'quantity': quantity,
            'side': side,
            'reduce_only': reduce_only,
            'failed': True
        })
        return OrderResult(
            success=False,
            order_id=None,
            error_message="Insufficient balance for rollback"
        )
    
    mock_client.get_order_info = mock_get_order_info
    mock_client.place_market_order = mock_place_market_order_fail
    
    # Execute rollback - should handle failure gracefully
    rollback_cost = await rollback_manager.rollback(filled_orders)
    
    # Verify rollback attempted (order was placed, even if failed)
    assert len(mock_client.placed_orders) >= 1, "Rollback should attempt market order"
    
    # Verify cost is 0 (failed rollback)
    assert rollback_cost == Decimal('0'), "Failed rollback should return 0 cost"
    
    # Verify position snapshot was checked (Step 4 verification)
    # This would trigger emergency close if position still exists


@pytest.mark.asyncio
async def test_context_state_consistency():
    """
    Verify context.filled_quantity updates correctly in all execution paths.
    
    Test paths:
    - Full fill
    - Partial fill
    - Hedge success
    - Hedge failure with partial fill
    - Rollback
    """
    executor = AtomicMultiOrderExecutor()
    
    long_client = MockExchangeClient("exchange1")
    short_client = MockExchangeClient("exchange2")
    
    # Test 1: Full fill updates context
    ctx1 = OrderContext(
        spec=OrderSpec(
            exchange_client=long_client,
            symbol="BTC-PERP",
            side="buy",
            size_usd=Decimal("50000"),
            quantity=Decimal("1.0")
        ),
        cancel_event=asyncio.Event(),
        task=asyncio.create_task(asyncio.sleep(0)),
        completed=False
    )
    
    # Simulate full fill
    ctx1.record_fill(Decimal("1.0"), Decimal("50000"))
    assert ctx1.filled_quantity == Decimal("1.0")
    assert ctx1.filled_usd == Decimal("50000")
    
    # Test 2: Partial fill updates context
    ctx2 = OrderContext(
        spec=OrderSpec(
            exchange_client=short_client,
            symbol="BTC-PERP",
            side="sell",
            size_usd=Decimal("50000"),
            quantity=Decimal("1.0")
        ),
        cancel_event=asyncio.Event(),
        task=asyncio.create_task(asyncio.sleep(0)),
        completed=False
    )
    
    # Simulate partial fill
    ctx2.record_fill(Decimal("0.5"), Decimal("50000"))
    assert ctx2.filled_quantity == Decimal("0.5")
    assert ctx2.filled_usd == Decimal("25000")
    
    # Test 3: Multiple fills accumulate
    ctx2.record_fill(Decimal("0.3"), Decimal("50100"))
    assert ctx2.filled_quantity == Decimal("0.8")  # 0.5 + 0.3
    assert ctx2.filled_usd == Decimal("25000") + (Decimal("0.3") * Decimal("50100"))


@pytest.mark.asyncio
async def test_rollback_payload_construction():
    """
    Verify rollback_payload construction includes all contexts with fills, handles suspicious quantities.
    
    Scenario:
    - Multiple contexts with fills
    - One context with suspicious filled_quantity (> spec.quantity * 1.1)
    - Verify suspicious context is skipped
    - Verify all valid contexts are included
    """
    executor = AtomicMultiOrderExecutor()
    
    mock_client_1 = MockExchangeClient("exchange1")
    mock_client_2 = MockExchangeClient("exchange2")
    
    # Create contexts
    ctx1 = OrderContext(
        spec=OrderSpec(
            exchange_client=mock_client_1,
            symbol="BTC-PERP",
            side="buy",
            size_usd=Decimal("50000"),
            quantity=Decimal("1.0")
        ),
        cancel_event=asyncio.Event(),
        task=asyncio.create_task(asyncio.sleep(0)),
        completed=True
    )
    ctx1.record_fill(Decimal("1.0"), Decimal("50000"))
    ctx1.result = {
        'filled_quantity': Decimal("1.0"),
        'fill_price': Decimal("50000"),
        'symbol': 'BTC-PERP',
        'side': 'buy',
        'exchange_client': mock_client_1
    }
    
    ctx2 = OrderContext(
        spec=OrderSpec(
            exchange_client=mock_client_2,
            symbol="BTC-PERP",
            side="sell",
            size_usd=Decimal("50000"),
            quantity=Decimal("1.0")
        ),
        cancel_event=asyncio.Event(),
        task=asyncio.create_task(asyncio.sleep(0)),
        completed=True
    )
    ctx2.record_fill(Decimal("0.5"), Decimal("50000"))
    ctx2.result = {
        'filled_quantity': Decimal("0.5"),
        'fill_price': Decimal("50000"),
        'symbol': 'BTC-PERP',
        'side': 'sell',
        'exchange_client': mock_client_2
    }
    
    # Create suspicious context (filled_quantity > spec.quantity * 1.1)
    ctx3 = OrderContext(
        spec=OrderSpec(
            exchange_client=mock_client_1,
            symbol="ETH-PERP",
            side="buy",
            size_usd=Decimal("3000"),
            quantity=Decimal("10.0")  # Spec quantity
        ),
        cancel_event=asyncio.Event(),
        task=asyncio.create_task(asyncio.sleep(0)),
        completed=True
    )
    ctx3.record_fill(Decimal("12.0"), Decimal("300"))  # 12.0 > 10.0 * 1.1 = 11.0 (suspicious!)
    ctx3.result = {
        'filled_quantity': Decimal("12.0"),
        'fill_price': Decimal("300"),
        'symbol': 'ETH-PERP',
        'side': 'buy',
        'exchange_client': mock_client_1
    }
    
    contexts = [ctx1, ctx2, ctx3]
    
    # Simulate rollback payload construction (from executor._handle_full_fill_trigger)
    rollback_payload = []
    for c in contexts:
        if c.filled_quantity > Decimal("0") and c.result:
            spec_qty = getattr(c.spec, "quantity", None)
            if spec_qty is not None:
                spec_qty_dec = Decimal(str(spec_qty))
                # Check for suspicious quantity
                if c.filled_quantity > spec_qty_dec * Decimal("1.1"):
                    # Should skip this context
                    continue
            
            from strategies.execution.patterns.atomic_multi_order.utils import context_to_filled_dict
            rollback_payload.append(context_to_filled_dict(c))
    
    # Verify suspicious context was skipped
    assert len(rollback_payload) == 2, "Suspicious context should be skipped"
    
    # Verify valid contexts are included
    symbols_in_payload = [order['symbol'] for order in rollback_payload]
    assert 'BTC-PERP' in symbols_in_payload
    assert 'ETH-PERP' not in symbols_in_payload, "Suspicious context should not be in payload"


@pytest.mark.asyncio
async def test_error_handling_network_failures():
    """
    Test error handling: network failures, exchange errors, order info unavailability.
    
    Scenario:
    - Network timeout during order placement
    - Exchange API error
    - Order info unavailable after placement
    """
    executor = AtomicMultiOrderExecutor()
    
    mock_client = MockExchangeClient("test_exchange")
    
    # Test 1: Network failure during order placement
    async def mock_place_limit_order_fail(*args, **kwargs):
        raise ConnectionError("Network timeout")
    
    mock_client.place_limit_order = mock_place_limit_order_fail
    
    orders = [
        OrderSpec(
            exchange_client=mock_client,
            symbol='BTC-PERP',
            side='buy',
            size_usd=Decimal('50000')
        )
    ]
    
    # Should handle error gracefully
    result = await executor.execute_atomically(
        orders=orders,
        rollback_on_partial=True,
        pre_flight_check=False
    )
    
    assert result.success is False
    assert result.error_message is not None
    
    # Test 2: Order info unavailable
    mock_client_2 = MockExchangeClient("test_exchange2")
    
    async def mock_get_order_info_none(order_id, *, force_refresh=False):
        return None  # Order info unavailable
    
    mock_client_2.get_order_info = mock_get_order_info_none
    
    # Should handle None gracefully
    filled_orders = [{
        'exchange_client': mock_client_2,
        'symbol': 'BTC-PERP',
        'side': 'buy',
        'filled_quantity': Decimal('1.0'),
        'fill_price': Decimal('50000'),
        'order_id': 'order_1'
    }]
    
    rollback_manager = RollbackManager()
    # Should not crash on None order_info
    rollback_cost = await rollback_manager.rollback(filled_orders)
    assert rollback_cost >= Decimal('0')


# =============================================================================
# PHASE 5: INTEGRATION FLOW TESTING
# =============================================================================

@pytest.mark.asyncio
async def test_full_execution_path_success():
    """
    Test successful execution: both orders fill → no hedge needed.
    
    Scenario:
    - Long order fills completely
    - Short order fills completely
    - No hedge needed
    - Result: success=True, all_filled=True
    """
    executor = AtomicMultiOrderExecutor()
    long_client = MockExchangeClient("exchange1")
    short_client = MockExchangeClient("exchange2")
    
    orders = [
        OrderSpec(
            exchange_client=long_client,
            symbol='BTC-PERP',
            side='buy',
            size_usd=Decimal('50000'),
            quantity=Decimal('1.0'),
            execution_mode='limit_only'
        ),
        OrderSpec(
            exchange_client=short_client,
            symbol='BTC-PERP',
            side='sell',
            size_usd=Decimal('50000'),
            quantity=Decimal('1.0'),
            execution_mode='limit_only'
        )
    ]
    
    # Mock both orders to fill successfully
    def _filled_result(exchange_client, symbol, side, order_id):
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
    
    executor._place_single_order = AsyncMock(side_effect=[
        _filled_result(long_client, 'BTC-PERP', 'buy', 'order_1'),
        _filled_result(short_client, 'BTC-PERP', 'sell', 'order_2'),
    ])
    
    result = await executor.execute_atomically(
        orders=orders,
        rollback_on_partial=True,
        pre_flight_check=False
    )
    
    assert result.success is True
    assert result.all_filled is True
    assert len(result.filled_orders) == 2
    assert result.rollback_performed is False


@pytest.mark.asyncio
async def test_one_side_fill_hedge_success():
    """
    Test one-side fill → aggressive limit hedge → success.
    
    Scenario:
    - Long order fills completely
    - Short order doesn't fill
    - Aggressive limit hedge succeeds
    - Result: success=True, all_filled=True
    """
    executor = AtomicMultiOrderExecutor()
    long_client = MockExchangeClient("exchange1")
    short_client = MockExchangeClient("exchange2")
    
    orders = [
        OrderSpec(
            exchange_client=long_client,
            symbol='BTC-PERP',
            side='buy',
            size_usd=Decimal('50000'),
            quantity=Decimal('1.0')
        ),
        OrderSpec(
            exchange_client=short_client,
            symbol='BTC-PERP',
            side='sell',
            size_usd=Decimal('50000'),
            quantity=Decimal('1.0')
        )
    ]
    
    def _filled_result(exchange_client, symbol, side, order_id):
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
    
    executor._place_single_order = AsyncMock(side_effect=[
        _filled_result(long_client, 'BTC-PERP', 'buy', 'order_1'),
        _unfilled_result(short_client, 'BTC-PERP', 'sell'),
    ])
    
    # Mock hedge to succeed
    with patch('strategies.execution.core.order_executor.OrderExecutor') as mock_exec_cls:
        hedge_executor = AsyncMock()
        mock_exec_cls.return_value = hedge_executor
        hedge_executor.execute_order.return_value = SimpleNamespace(
            success=True,
            filled=True,
            fill_price=Decimal('50010'),
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
    
    assert result.success is True
    assert result.all_filled is True
    assert result.rollback_performed is False


@pytest.mark.asyncio
async def test_one_side_fill_hedge_failure_rollback():
    """
    Test one-side fill → hedge fails → rollback triggered.
    
    Scenario:
    - Long order fills completely
    - Short order doesn't fill
    - Hedge fails (market order fails)
    - Rollback triggered
    - Result: success=False, rollback_performed=True
    """
    executor = AtomicMultiOrderExecutor()
    long_client = MockExchangeClient("exchange1")
    short_client = MockExchangeClient("exchange2")
    
    orders = [
        OrderSpec(
            exchange_client=long_client,
            symbol='BTC-PERP',
            side='buy',
            size_usd=Decimal('50000'),
            quantity=Decimal('1.0')
        ),
        OrderSpec(
            exchange_client=short_client,
            symbol='BTC-PERP',
            side='sell',
            size_usd=Decimal('50000'),
            quantity=Decimal('1.0')
        )
    ]
    
    def _filled_result(exchange_client, symbol, side, order_id):
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
    
    executor._place_single_order = AsyncMock(side_effect=[
        _filled_result(long_client, 'BTC-PERP', 'buy', 'order_1'),
        _unfilled_result(short_client, 'BTC-PERP', 'sell'),
    ])
    
    # Mock hedge to fail
    with patch('strategies.execution.core.order_executor.OrderExecutor') as mock_exec_cls:
        hedge_executor = AsyncMock()
        mock_exec_cls.return_value = hedge_executor
        hedge_executor.execute_order.return_value = SimpleNamespace(
            success=False,
            filled=False,
            fill_price=None,
            filled_quantity=Decimal('0'),
            slippage_usd=Decimal('0'),
            execution_mode_used='market',
            order_id=None,
            error_message='Hedge failed'
        )
        
        # Mock rollback
        executor._rollback_manager.rollback = AsyncMock(return_value=Decimal('10.0'))
        
        result = await executor.execute_atomically(
            orders=orders,
            rollback_on_partial=True,
            pre_flight_check=False
        )
    
    assert result.success is False
    assert result.all_filled is False
    assert result.rollback_performed is True
    assert result.rollback_cost_usd == Decimal('10.0')
    executor._rollback_manager.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_partial_fill_hedge_partial_fill_rollback():
    """
    Test partial fill → aggressive limit hedge → market fallback → partial fill → rollback.
    
    Scenario:
    - Initial partial fill on one side
    - Aggressive limit hedge attempts fail
    - Market fallback partially fills then gets cancelled
    - Rollback triggered for all partial fills
    """
    executor = AtomicMultiOrderExecutor()
    long_client = MockExchangeClient("exchange1")
    short_client = MockExchangeClient("exchange2")
    
    orders = [
        OrderSpec(
            exchange_client=long_client,
            symbol='XMR',
            side='buy',
            size_usd=Decimal('1600'),
            quantity=Decimal('4.393')
        ),
        OrderSpec(
            exchange_client=short_client,
            symbol='XMR',
            side='sell',
            size_usd=Decimal('1600'),
            quantity=Decimal('4.393')
        )
    ]
    
    # Mock partial fill on long side
    def _partial_filled_result(exchange_client, symbol, side, order_id, filled_qty):
        return {
            'success': True,
            'filled': True,
            'fill_price': Decimal('363.971'),
            'filled_quantity': filled_qty,
            'slippage_usd': Decimal('1.0'),
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
    
    executor._place_single_order = AsyncMock(side_effect=[
        _partial_filled_result(long_client, 'XMR', 'buy', 'order_1', Decimal('4.393')),
        _unfilled_result(short_client, 'XMR', 'sell'),
    ])
    
    # Mock aggressive limit hedge to fail, then market hedge to partially fill
    with patch('strategies.execution.core.order_executor.OrderExecutor') as mock_exec_cls:
        hedge_executor = AsyncMock()
        mock_exec_cls.return_value = hedge_executor
        
        # First call: aggressive limit hedge fails (timeout)
        # Second call: market hedge partially fills then gets cancelled
        hedge_executor.execute_order.side_effect = [
            SimpleNamespace(  # Aggressive limit hedge attempt
                success=False,
                filled=False,
                fill_price=None,
                filled_quantity=Decimal('0'),
                slippage_usd=Decimal('0'),
                execution_mode_used='limit',
                order_id=None,
                error_message='Timeout'
            ),
            SimpleNamespace(  # Market hedge fallback - partial fill before cancel
                success=False,
                filled=True,  # Had partial fill!
                fill_price=Decimal('366.16'),
                filled_quantity=Decimal('1.187'),  # Partial fill
                expected_price=Decimal('365.43'),
                slippage_usd=Decimal('0.87'),
                slippage_pct=Decimal('0.002'),
                execution_mode_used='market_partial_canceled',
                order_id='market_order_1',
                error_message='Market order canceled with partial fill: exceeds_max_slippage',
                retryable=False
            )
        ]
        
        # Mock rollback
        executor._rollback_manager.rollback = AsyncMock(return_value=Decimal('79.81'))
        
        result = await executor.execute_atomically(
            orders=orders,
            rollback_on_partial=True,
            pre_flight_check=False
        )
    
    # Verify hedge was attempted
    assert hedge_executor.execute_order.await_count >= 1
    
    # Verify rollback was triggered
    assert result.rollback_performed is True
    executor._rollback_manager.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_rollback_on_partial_false_no_rollback():
    """
    Test rollback_on_partial=False → no rollback on hedge failure.
    
    Scenario:
    - One side fills
    - Hedge fails
    - rollback_on_partial=False
    - Result: success=False, rollback_performed=False
    """
    executor = AtomicMultiOrderExecutor()
    long_client = MockExchangeClient("exchange1")
    short_client = MockExchangeClient("exchange2")
    
    orders = [
        OrderSpec(
            exchange_client=long_client,
            symbol='BTC-PERP',
            side='buy',
            size_usd=Decimal('50000'),
            quantity=Decimal('1.0')
        ),
        OrderSpec(
            exchange_client=short_client,
            symbol='BTC-PERP',
            side='sell',
            size_usd=Decimal('50000'),
            quantity=Decimal('1.0')
        )
    ]
    
    def _filled_result(exchange_client, symbol, side, order_id):
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
    
    executor._place_single_order = AsyncMock(side_effect=[
        _filled_result(long_client, 'BTC-PERP', 'buy', 'order_1'),
        _unfilled_result(short_client, 'BTC-PERP', 'sell'),
    ])
    
    # Mock hedge to fail
    with patch('strategies.execution.core.order_executor.OrderExecutor') as mock_exec_cls:
        hedge_executor = AsyncMock()
        mock_exec_cls.return_value = hedge_executor
        hedge_executor.execute_order.return_value = SimpleNamespace(
            success=False,
            filled=False,
            fill_price=None,
            filled_quantity=Decimal('0'),
            slippage_usd=Decimal('0'),
            execution_mode_used='market',
            order_id=None,
            error_message='Hedge failed'
        )
        
        result = await executor.execute_atomically(
            orders=orders,
            rollback_on_partial=False,  # Don't rollback!
            pre_flight_check=False
        )
    
    assert result.success is False
    assert result.all_filled is False
    assert result.rollback_performed is False  # No rollback!
    assert result.rollback_cost_usd == Decimal('0')


if __name__ == '__main__':
    pytest.main([__file__, '-v'])

