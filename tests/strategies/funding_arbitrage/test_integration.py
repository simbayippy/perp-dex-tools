"""
Integration Tests for Funding Arbitrage Strategy

Tests the complete strategy lifecycle:
- Opportunity detection
- Position opening (with atomic execution)
- Funding payment tracking
- Rebalance triggering
- Position closing
- Database persistence
"""

import pytest
import asyncio
from decimal import Decimal
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from datetime import datetime

from strategies.implementations.funding_arbitrage import (
    FundingArbitrageStrategy,
    FundingArbConfig,
    FundingArbPosition
)
from strategies.execution import OrderExecutionMode, OrderResult, ExecutionResult
from strategies.components import OrderType, TradeType


class MockExchangeClient:
    """Mock exchange client for testing."""
    
    def __init__(self, name: str):
        self.name = name
        self.config = Mock()
        self.config.contract_id = "BTC-USD"
        self.config.quantity = Decimal('1.0')
        
    def get_exchange_name(self) -> str:
        return self.name
    
    async def fetch_bbo_prices(self, contract_id: str):
        """Return mock BBO prices."""
        return (Decimal('50000'), Decimal('50001'))
    
    async def place_limit_order(self, contract_id: str, quantity: Decimal, price: Decimal, side: str):
        """Mock limit order placement."""
        return OrderResult(
            success=True,
            order_id=f"order_{side}_{self.name}",
            status="FILLED",
            price=price,
            size=quantity,
            side=side
        )
    
    async def place_market_order(self, contract_id: str, quantity: Decimal, side: str):
        """Mock market order placement."""
        price = Decimal('50000') if side == 'buy' else Decimal('50001')
        return OrderResult(
            success=True,
            order_id=f"order_{side}_{self.name}",
            status="FILLED",
            price=price,
            size=quantity,
            side=side
        )
    
    async def get_order_book_depth(self, contract_id: str, levels: int = 10):
        """Mock order book depth."""
        return {
            'bids': [(Decimal('50000'), Decimal('10'))],
            'asks': [(Decimal('50001'), Decimal('10'))]
        }


@pytest.fixture
def mock_funding_rate_repository():
    """Mock FundingRateRepository."""
    repo = Mock()
    repo.get_latest_rates = AsyncMock(return_value=[
        {
            'dex': 'lighter',
            'symbol': 'BTC',
            'funding_rate': Decimal('0.0001'),
            'funding_interval_hours': 8,
            'timestamp': datetime.now()
        },
        {
            'dex': 'backpack',
            'symbol': 'BTC',
            'funding_rate': Decimal('-0.0002'),
            'funding_interval_hours': 8,
            'timestamp': datetime.now()
        },
    ])
    return repo


@pytest.fixture
def mock_opportunity_finder():
    """Mock OpportunityFinder."""
    finder = Mock()
    finder.find_opportunities = AsyncMock(return_value=[
        {
            'symbol': 'BTC',
            'long_dex': 'lighter',
            'short_dex': 'backpack',
            'divergence': Decimal('0.0003'),
            'profitability_24h': Decimal('0.0072'),  # 0.72%
            'long_rate': Decimal('0.0001'),
            'short_rate': Decimal('-0.0002'),
        }
    ])
    return finder


@pytest.fixture
def funding_arb_config():
    """Create FundingArbConfig for testing."""
    return FundingArbConfig(
        min_profitability=Decimal('0.001'),  # 0.1%
        time_horizon_hours=24,
        position_size_usd=Decimal('1000'),
        max_positions=3,
        rebalance_strategy='combined',
        rebalance_config={
            'strategies': [
                {'name': 'divergence_flip', 'config': {}},
                {'name': 'profit_erosion', 'config': {'threshold_pct': Decimal('0.5')}},
            ]
        }
    )


@pytest.fixture
def mock_exchange_clients():
    """Create mock exchange clients."""
    return {
        'lighter': MockExchangeClient('lighter'),
        'backpack': MockExchangeClient('backpack'),
    }


@pytest.fixture
async def strategy(funding_arb_config, mock_exchange_clients):
    """Create FundingArbitrageStrategy instance."""
    return FundingArbitrageStrategy(
        config=funding_arb_config,
        exchange_clients=mock_exchange_clients,
        logger=Mock()
    )


class TestFundingArbitrageIntegration:
    """Integration tests for the full strategy lifecycle."""
    
    @pytest.mark.asyncio
    async def test_full_lifecycle_profitable_opportunity(
        self, strategy, mock_opportunity_finder, mock_funding_rate_repository
    ):
        """Test complete lifecycle: detect → open → monitor → close."""
        
        # Patch the internal components
        with patch.object(strategy, 'funding_rate_repo', mock_funding_rate_repository), \
             patch.object(strategy, 'opportunity_finder', mock_opportunity_finder):
            
            # Step 1: Monitor for opportunities
            await strategy.monitor_opportunities()
            
            # Should have found one opportunity
            assert len(strategy.pending_opportunities) == 1
            opp = strategy.pending_opportunities[0]
            assert opp['symbol'] == 'BTC'
            assert opp['long_dex'] == 'lighter'
            assert opp['short_dex'] == 'backpack'
            
            # Step 2: Execute opportunity (open position)
            # Mock atomic executor to succeed
            with patch('strategies.implementations.funding_arbitrage.strategy.AtomicMultiOrderExecutor') as mock_atomic:
                mock_executor = Mock()
                mock_executor.execute = AsyncMock(return_value=ExecutionResult(
                    success=True,
                    long_order=OrderResult(
                        success=True,
                        order_id="long_1",
                        status="FILLED",
                        price=Decimal('50000'),
                        size=Decimal('1.0'),
                        side='buy'
                    ),
                    short_order=OrderResult(
                        success=True,
                        order_id="short_1",
                        status="FILLED",
                        price=Decimal('50001'),
                        size=Decimal('1.0'),
                        side='sell'
                    ),
                    execution_time_ms=150
                ))
                mock_atomic.return_value = mock_executor
                
                await strategy.execute_opportunity(opp)
            
            # Should have one active position
            assert len(strategy.active_positions) == 1
            position = strategy.active_positions['BTC']
            assert position.symbol == 'BTC'
            assert position.long_dex == 'lighter'
            assert position.short_dex == 'backpack'
            
            # Step 3: Monitor position (simulate funding payment)
            position.cumulative_funding += Decimal('5.0')  # $5 funding received
            
            # Check if rebalance needed
            rebalance_needed = await strategy.check_positions_for_rebalance()
            
            # Should not trigger rebalance yet (no divergence flip or erosion)
            assert rebalance_needed is False
            
            # Step 4: Simulate divergence flip (negative divergence)
            position.current_divergence = Decimal('-0.0001')
            
            # Now should trigger rebalance
            rebalance_needed = await strategy.check_positions_for_rebalance()
            assert rebalance_needed is True
            
            # Step 5: Close position
            with patch('strategies.implementations.funding_arbitrage.strategy.AtomicMultiOrderExecutor') as mock_atomic_close:
                mock_executor_close = Mock()
                mock_executor_close.execute = AsyncMock(return_value=ExecutionResult(
                    success=True,
                    long_order=OrderResult(success=True, order_id="close_long_1", status="FILLED", price=Decimal('50100'), size=Decimal('1.0'), side='sell'),
                    short_order=OrderResult(success=True, order_id="close_short_1", status="FILLED", price=Decimal('50099'), size=Decimal('1.0'), side='buy'),
                    execution_time_ms=120
                ))
                mock_atomic_close.return_value = mock_executor_close
                
                await strategy.close_position('BTC')
            
            # Position should be removed from active and moved to closed
            assert 'BTC' not in strategy.active_positions
            assert len(strategy.closed_positions) == 1
    
    @pytest.mark.asyncio
    async def test_atomic_execution_rollback_on_partial_fill(self, strategy):
        """Test that atomic executor rolls back on partial fill."""
        
        from strategies.execution import AtomicMultiOrderExecutor, LiquidityAnalyzer
        
        # Create real atomic executor
        liquidity_analyzer = LiquidityAnalyzer()
        atomic_executor = AtomicMultiOrderExecutor(
            long_client=strategy.exchange_clients['lighter'],
            short_client=strategy.exchange_clients['backpack'],
            liquidity_analyzer=liquidity_analyzer,
            logger=Mock()
        )
        
        # Mock long order to succeed, short order to fail
        async def mock_place_limit_long(*args, **kwargs):
            return OrderResult(
                success=True,
                order_id="long_1",
                status="FILLED",
                price=Decimal('50000'),
                size=Decimal('1.0'),
                side='buy'
            )
        
        async def mock_place_limit_short(*args, **kwargs):
            return OrderResult(
                success=False,
                order_id="short_1",
                status="REJECTED",
                error_message="Insufficient margin",
                side='sell'
            )
        
        # Mock cancel order
        async def mock_cancel_order(*args, **kwargs):
            return True
        
        strategy.exchange_clients['lighter'].place_limit_order = mock_place_limit_long
        strategy.exchange_clients['backpack'].place_limit_order = mock_place_limit_short
        strategy.exchange_clients['lighter'].cancel_order = mock_cancel_order
        
        # Execute atomic operation
        result = await atomic_executor.execute(
            contract_id="BTC-USD",
            long_quantity=Decimal('1.0'),
            short_quantity=Decimal('1.0'),
            mode=OrderExecutionMode.LIMIT
        )
        
        # Should fail and rollback
        assert result.success is False
        assert result.long_order is not None
        assert result.short_order is not None
        assert result.rolled_back is True
    
    @pytest.mark.asyncio
    async def test_database_persistence(self, strategy, funding_arb_config):
        """Test that positions are persisted to database."""
        
        # Create a position
        position = FundingArbPosition(
            id="pos_test_1",
            symbol="BTC",
            long_dex="lighter",
            short_dex="backpack",
            size=Decimal('1000'),
            entry_divergence=Decimal('0.0003'),
            current_divergence=Decimal('0.0003'),
            cumulative_funding=Decimal('5.0'),
            unrealized_pnl=Decimal('10.0')
        )
        
        # Mock database connection
        mock_db = Mock()
        mock_cursor = Mock()
        mock_db.cursor.return_value.__enter__.return_value = mock_cursor
        
        with patch('strategies.implementations.funding_arbitrage.position_manager.get_db_connection', return_value=mock_db):
            # Save position
            await strategy.position_manager.save_position(position)
            
            # Verify database insert was called
            mock_cursor.execute.assert_called()
            call_args = mock_cursor.execute.call_args[0][0]
            assert "INSERT INTO strategy_positions" in call_args or "UPDATE strategy_positions" in call_args
    
    @pytest.mark.asyncio
    async def test_no_execution_below_min_profitability(
        self, strategy, mock_opportunity_finder, mock_funding_rate_repository
    ):
        """Test that opportunities below min profitability are skipped."""
        
        # Mock opportunity with low profitability
        mock_opportunity_finder.find_opportunities = AsyncMock(return_value=[
            {
                'symbol': 'ETH',
                'long_dex': 'lighter',
                'short_dex': 'backpack',
                'divergence': Decimal('0.00005'),
                'profitability_24h': Decimal('0.00012'),  # 0.012% (below 0.1% threshold)
                'long_rate': Decimal('0.00001'),
                'short_rate': Decimal('-0.00004'),
            }
        ])
        
        with patch.object(strategy, 'funding_rate_repo', mock_funding_rate_repository), \
             patch.object(strategy, 'opportunity_finder', mock_opportunity_finder):
            
            # Monitor opportunities
            await strategy.monitor_opportunities()
            
            # Should filter out the unprofitable opportunity
            assert len(strategy.pending_opportunities) == 0
    
    @pytest.mark.asyncio
    async def test_max_positions_limit(self, strategy, mock_opportunity_finder, mock_funding_rate_repository):
        """Test that strategy respects max_positions limit."""
        
        # Mock multiple opportunities
        mock_opportunity_finder.find_opportunities = AsyncMock(return_value=[
            {'symbol': 'BTC', 'long_dex': 'lighter', 'short_dex': 'backpack', 'divergence': Decimal('0.0003'), 'profitability_24h': Decimal('0.0072')},
            {'symbol': 'ETH', 'long_dex': 'lighter', 'short_dex': 'backpack', 'divergence': Decimal('0.0004'), 'profitability_24h': Decimal('0.0096')},
            {'symbol': 'SOL', 'long_dex': 'lighter', 'short_dex': 'backpack', 'divergence': Decimal('0.0005'), 'profitability_24h': Decimal('0.012')},
            {'symbol': 'AVAX', 'long_dex': 'lighter', 'short_dex': 'backpack', 'divergence': Decimal('0.0003'), 'profitability_24h': Decimal('0.0072')},
        ])
        
        # Set max_positions to 3
        strategy.config.max_positions = 3
        
        # Fill up to max positions
        for symbol in ['BTC', 'ETH', 'SOL']:
            strategy.active_positions[symbol] = FundingArbPosition(
                id=f"pos_{symbol}",
                symbol=symbol,
                long_dex="lighter",
                short_dex="backpack",
                size=Decimal('1000'),
                entry_divergence=Decimal('0.0003'),
                current_divergence=Decimal('0.0003'),
                cumulative_funding=Decimal('0'),
                unrealized_pnl=Decimal('0')
            )
        
        with patch.object(strategy, 'funding_rate_repo', mock_funding_rate_repository), \
             patch.object(strategy, 'opportunity_finder', mock_opportunity_finder):
            
            # Try to monitor for more opportunities
            await strategy.monitor_opportunities()
            
            # Should not add AVAX opportunity (at max capacity)
            assert 'AVAX' not in strategy.pending_opportunities


@pytest.mark.asyncio
async def test_execution_quality_tracking():
    """Test that execution quality metrics are tracked."""
    
    from strategies.execution.monitoring import ExecutionTracker
    
    tracker = ExecutionTracker()
    
    # Record successful execution
    tracker.record_execution(
        symbol="BTC",
        expected_price=Decimal('50000'),
        actual_price=Decimal('50005'),
        size=Decimal('1.0'),
        side='buy',
        execution_time_ms=150
    )
    
    # Get metrics
    metrics = tracker.get_metrics("BTC")
    
    assert metrics is not None
    assert metrics['total_executions'] == 1
    assert metrics['avg_slippage_bps'] > 0  # Some slippage
    assert metrics['avg_execution_time_ms'] == 150

