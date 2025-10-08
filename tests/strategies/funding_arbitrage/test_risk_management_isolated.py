"""
Unit Tests for Risk Management Strategies - ISOLATED VERSION

Tests the pluggable risk management system without importing the full strategy stack.
"""

import pytest
from decimal import Decimal
from dataclasses import dataclass
from typing import Optional, Tuple, List
from enum import Enum


# ============================================================================
# ISOLATED RISK MANAGEMENT CLASSES (copied from implementation)
# ============================================================================

@dataclass
class MockFundingArbPosition:
    """Mock position for testing (simplified version)."""
    id: str
    symbol: str
    long_dex: str
    short_dex: str
    size: Decimal
    entry_divergence: Decimal
    current_divergence: Decimal
    cumulative_funding: Decimal = Decimal('0')
    unrealized_pnl: Decimal = Decimal('0')


class IsolatedBaseRebalanceStrategy:
    """Base class for rebalance strategies."""
    
    def should_rebalance(self, position: MockFundingArbPosition) -> Tuple[bool, Optional[str]]:
        """
        Check if position should be rebalanced.
        
        Returns:
            (should_rebalance, reason)
        """
        raise NotImplementedError


class IsolatedProfitErosionStrategy(IsolatedBaseRebalanceStrategy):
    """Trigger rebalance when profit erodes below threshold."""
    
    def __init__(self, threshold_pct: Decimal = Decimal('0.5')):
        """
        Args:
            threshold_pct: Trigger when current divergence < threshold * entry_divergence
        """
        self.threshold_pct = threshold_pct
    
    def should_rebalance(self, position: MockFundingArbPosition) -> Tuple[bool, Optional[str]]:
        """Check if profit has eroded below threshold."""
        if position.entry_divergence <= 0:
            return False, None
        
        # Calculate erosion ratio
        erosion_ratio = position.current_divergence / position.entry_divergence
        
        if erosion_ratio <= self.threshold_pct:
            return True, f"Profit erosion: {erosion_ratio:.2%} <= {self.threshold_pct:.2%}"
        
        return False, None


class IsolatedDivergenceFlipStrategy(IsolatedBaseRebalanceStrategy):
    """Trigger urgent rebalance when divergence flips negative."""
    
    def should_rebalance(self, position: MockFundingArbPosition) -> Tuple[bool, Optional[str]]:
        """Check if divergence has flipped negative."""
        if position.current_divergence <= 0:
            return True, "URGENT: Divergence flip - funding rates reversed"
        
        return False, None


class IsolatedCombinedRebalanceStrategy(IsolatedBaseRebalanceStrategy):
    """Combine multiple rebalance strategies with priority order."""
    
    def __init__(self, strategies: List[IsolatedBaseRebalanceStrategy]):
        """
        Args:
            strategies: List of strategies in priority order (first wins)
        """
        self.strategies = strategies
    
    def should_rebalance(self, position: MockFundingArbPosition) -> Tuple[bool, Optional[str]]:
        """Check strategies in priority order - first trigger wins."""
        for strategy in self.strategies:
            should_rebalance, reason = strategy.should_rebalance(position)
            if should_rebalance:
                return True, reason
        
        return False, None


def isolated_get_rebalance_strategy(name: str, config: dict) -> IsolatedBaseRebalanceStrategy:
    """Factory function for creating rebalance strategies."""
    if name == 'profit_erosion':
        threshold_pct = config.get('threshold_pct', Decimal('0.5'))
        return IsolatedProfitErosionStrategy(threshold_pct=threshold_pct)
    
    elif name == 'divergence_flip':
        return IsolatedDivergenceFlipStrategy()
    
    elif name == 'combined':
        strategies = []
        for strategy_config in config.get('strategies', []):
            sub_strategy = isolated_get_rebalance_strategy(
                strategy_config['name'], 
                strategy_config['config']
            )
            strategies.append(sub_strategy)
        return IsolatedCombinedRebalanceStrategy(strategies)
    
    else:
        raise ValueError(f"Unknown rebalance strategy: {name}")


# ============================================================================
# TESTS
# ============================================================================

class TestIsolatedProfitErosionStrategy:
    """Test suite for ProfitErosionStrategy."""
    
    @pytest.fixture
    def strategy(self):
        """Create ProfitErosionStrategy with 50% threshold."""
        return IsolatedProfitErosionStrategy(threshold_pct=Decimal('0.5'))
    
    @pytest.fixture
    def position(self):
        """Create a sample position."""
        return MockFundingArbPosition(
            id="pos_1",
            symbol="BTC",
            long_dex="lighter",
            short_dex="backpack",
            size=Decimal('1000'),
            entry_divergence=Decimal('0.0004'),  # 0.04%
            current_divergence=Decimal('0.0004'),
            cumulative_funding=Decimal('0'),
            unrealized_pnl=Decimal('0')
        )
    
    def test_no_rebalance_at_entry(self, strategy, position):
        """Test that no rebalance is needed at entry divergence."""
        needs_rebalance, reason = strategy.should_rebalance(position)
        
        assert needs_rebalance is False
        assert reason is None
    
    def test_no_rebalance_above_threshold(self, strategy, position):
        """Test that no rebalance when divergence is above threshold."""
        # 60% of entry divergence (above 50% threshold)
        position.current_divergence = position.entry_divergence * Decimal('0.6')
        
        needs_rebalance, reason = strategy.should_rebalance(position)
        
        assert needs_rebalance is False
        assert reason is None
    
    def test_rebalance_at_threshold(self, strategy, position):
        """Test rebalance triggers exactly at threshold."""
        # 50% of entry divergence
        position.current_divergence = position.entry_divergence * Decimal('0.5')
        
        needs_rebalance, reason = strategy.should_rebalance(position)
        
        assert needs_rebalance is True
        assert "profit erosion" in reason.lower()
    
    def test_rebalance_below_threshold(self, strategy, position):
        """Test rebalance triggers below threshold."""
        # 30% of entry divergence (below 50% threshold)
        position.current_divergence = position.entry_divergence * Decimal('0.3')
        
        needs_rebalance, reason = strategy.should_rebalance(position)
        
        assert needs_rebalance is True
        assert "profit erosion" in reason.lower()
    
    def test_higher_threshold(self, position):
        """Test with higher threshold (75%)."""
        strategy = IsolatedProfitErosionStrategy(threshold_pct=Decimal('0.75'))
        
        # 80% of entry (should not trigger)
        position.current_divergence = position.entry_divergence * Decimal('0.8')
        needs_rebalance, _ = strategy.should_rebalance(position)
        assert needs_rebalance is False
        
        # 70% of entry (should trigger)
        position.current_divergence = position.entry_divergence * Decimal('0.7')
        needs_rebalance, reason = strategy.should_rebalance(position)
        assert needs_rebalance is True


class TestIsolatedDivergenceFlipStrategy:
    """Test suite for DivergenceFlipStrategy."""
    
    @pytest.fixture
    def strategy(self):
        """Create DivergenceFlipStrategy."""
        return IsolatedDivergenceFlipStrategy()
    
    @pytest.fixture
    def position(self):
        """Create a sample position."""
        return MockFundingArbPosition(
            id="pos_1",
            symbol="BTC",
            long_dex="lighter",
            short_dex="backpack",
            size=Decimal('1000'),
            entry_divergence=Decimal('0.0004'),
            current_divergence=Decimal('0.0004'),
            cumulative_funding=Decimal('0'),
            unrealized_pnl=Decimal('0')
        )
    
    def test_no_rebalance_positive_divergence(self, strategy, position):
        """Test that no rebalance when divergence is positive."""
        position.current_divergence = Decimal('0.0002')
        
        needs_rebalance, reason = strategy.should_rebalance(position)
        
        assert needs_rebalance is False
        assert reason is None
    
    def test_rebalance_zero_divergence(self, strategy, position):
        """Test rebalance triggers at zero divergence."""
        position.current_divergence = Decimal('0')
        
        needs_rebalance, reason = strategy.should_rebalance(position)
        
        assert needs_rebalance is True
        assert "divergence flip" in reason.lower()
        assert "urgent" in reason.lower()
    
    def test_rebalance_negative_divergence(self, strategy, position):
        """Test rebalance triggers for negative divergence."""
        position.current_divergence = Decimal('-0.0001')
        
        needs_rebalance, reason = strategy.should_rebalance(position)
        
        assert needs_rebalance is True
        assert "divergence flip" in reason.lower()
        assert "urgent" in reason.lower()
    
    def test_small_positive_divergence_no_trigger(self, strategy, position):
        """Test that small positive divergence doesn't trigger."""
        position.current_divergence = Decimal('0.00001')
        
        needs_rebalance, reason = strategy.should_rebalance(position)
        
        assert needs_rebalance is False


class TestIsolatedCombinedRebalanceStrategy:
    """Test suite for CombinedRebalanceStrategy."""
    
    @pytest.fixture
    def combined_strategy(self):
        """Create CombinedRebalanceStrategy with multiple sub-strategies."""
        return IsolatedCombinedRebalanceStrategy([
            IsolatedDivergenceFlipStrategy(),
            IsolatedProfitErosionStrategy(threshold_pct=Decimal('0.5')),
        ])
    
    @pytest.fixture
    def position(self):
        """Create a sample position."""
        return MockFundingArbPosition(
            id="pos_1",
            symbol="BTC",
            long_dex="lighter",
            short_dex="backpack",
            size=Decimal('1000'),
            entry_divergence=Decimal('0.0004'),
            current_divergence=Decimal('0.0004'),
            cumulative_funding=Decimal('0'),
            unrealized_pnl=Decimal('0')
        )
    
    def test_no_trigger_when_all_pass(self, combined_strategy, position):
        """Test that no rebalance when all strategies pass."""
        # Divergence is positive and above erosion threshold
        position.current_divergence = Decimal('0.0003')
        
        needs_rebalance, reason = combined_strategy.should_rebalance(position)
        
        assert needs_rebalance is False
        assert reason is None
    
    def test_trigger_divergence_flip(self, combined_strategy, position):
        """Test that divergence flip triggers (highest priority)."""
        position.current_divergence = Decimal('-0.0001')
        
        needs_rebalance, reason = combined_strategy.should_rebalance(position)
        
        assert needs_rebalance is True
        assert "divergence flip" in reason.lower()
        assert "urgent" in reason.lower()
    
    def test_trigger_profit_erosion(self, combined_strategy, position):
        """Test that profit erosion triggers when divergence flip doesn't."""
        # Positive divergence (no flip) but below 50% threshold
        position.current_divergence = position.entry_divergence * Decimal('0.4')
        
        needs_rebalance, reason = combined_strategy.should_rebalance(position)
        
        assert needs_rebalance is True
        assert "profit erosion" in reason.lower()
    
    def test_priority_order(self, combined_strategy, position):
        """Test that first trigger wins (priority order)."""
        # Both conditions met: negative divergence AND erosion
        position.current_divergence = Decimal('-0.0001')
        
        needs_rebalance, reason = combined_strategy.should_rebalance(position)
        
        # Should return divergence flip (first in list)
        assert needs_rebalance is True
        assert "divergence flip" in reason.lower()


class TestIsolatedRebalanceStrategyFactory:
    """Test suite for get_rebalance_strategy factory function."""
    
    def test_create_profit_erosion_strategy(self):
        """Test creating ProfitErosionStrategy via factory."""
        config = {'threshold_pct': Decimal('0.6')}
        strategy = isolated_get_rebalance_strategy('profit_erosion', config)
        
        assert isinstance(strategy, IsolatedProfitErosionStrategy)
        assert strategy.threshold_pct == Decimal('0.6')
    
    def test_create_divergence_flip_strategy(self):
        """Test creating DivergenceFlipStrategy via factory."""
        strategy = isolated_get_rebalance_strategy('divergence_flip', {})
        
        assert isinstance(strategy, IsolatedDivergenceFlipStrategy)
    
    def test_create_combined_strategy(self):
        """Test creating CombinedRebalanceStrategy via factory."""
        config = {
            'strategies': [
                {'name': 'divergence_flip', 'config': {}},
                {'name': 'profit_erosion', 'config': {'threshold_pct': Decimal('0.5')}},
            ]
        }
        strategy = isolated_get_rebalance_strategy('combined', config)
        
        assert isinstance(strategy, IsolatedCombinedRebalanceStrategy)
        assert len(strategy.strategies) == 2
    
    def test_unknown_strategy_raises_error(self):
        """Test that unknown strategy name raises ValueError."""
        with pytest.raises(ValueError, match="Unknown rebalance strategy"):
            isolated_get_rebalance_strategy('unknown_strategy', {})
    
    def test_default_profit_erosion_threshold(self):
        """Test default threshold for ProfitErosionStrategy."""
        strategy = isolated_get_rebalance_strategy('profit_erosion', {})
        
        assert isinstance(strategy, IsolatedProfitErosionStrategy)
        assert strategy.threshold_pct == Decimal('0.5')  # Default
