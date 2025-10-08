"""
Unit Tests for FundingRateAnalyzer

Tests the core funding rate analysis logic including:
- Rate normalization (per-second basis)
- Fee-adjusted profitability calculation
- Best opportunity selection
"""

import pytest
from decimal import Decimal
from strategies.implementations.funding_arbitrage.funding_analyzer import FundingRateAnalyzer


class TestFundingRateAnalyzer:
    """Test suite for FundingRateAnalyzer."""
    
    @pytest.fixture
    def analyzer(self):
        """Create FundingRateAnalyzer instance."""
        return FundingRateAnalyzer()
    
    def test_normalize_funding_rate_8h_interval(self, analyzer):
        """Test normalization for 8-hour funding interval (Binance-like)."""
        # 0.01% per 8 hours = 0.0001
        rate = Decimal('0.0001')
        interval_seconds = 8 * 60 * 60  # 8 hours
        
        normalized = analyzer.normalize_funding_rate(rate, interval_seconds)
        
        # Should be 0.0001 / (8 * 3600) = ~3.47e-9 per second
        expected = rate / Decimal(interval_seconds)
        assert normalized == expected
    
    def test_normalize_funding_rate_1h_interval(self, analyzer):
        """Test normalization for 1-hour funding interval (Hyperliquid-like)."""
        rate = Decimal('0.0001')
        interval_seconds = 1 * 60 * 60  # 1 hour
        
        normalized = analyzer.normalize_funding_rate(rate, interval_seconds)
        
        # Should be 0.0001 / 3600 = ~2.78e-8 per second
        expected = rate / Decimal(interval_seconds)
        assert normalized == expected
    
    def test_normalize_funding_rate_zero(self, analyzer):
        """Test normalization of zero funding rate."""
        normalized = analyzer.normalize_funding_rate(Decimal('0'), 3600)
        assert normalized == Decimal('0')
    
    def test_normalize_funding_rate_negative(self, analyzer):
        """Test normalization of negative funding rate."""
        rate = Decimal('-0.0002')
        interval_seconds = 8 * 60 * 60
        
        normalized = analyzer.normalize_funding_rate(rate, interval_seconds)
        
        expected = rate / Decimal(interval_seconds)
        assert normalized == expected
        assert normalized < 0
    
    def test_calculate_profitability_positive_divergence(self, analyzer):
        """Test profitability calculation for positive rate divergence."""
        # Setup: Long DEX has -0.01% (pays you), Short DEX has +0.02% (pays you)
        # Total profit = 0.01% + 0.02% = 0.03% per interval
        long_rate_per_sec = Decimal('-0.0001') / Decimal(8 * 3600)  # -0.01% per 8h
        short_rate_per_sec = Decimal('0.0002') / Decimal(8 * 3600)  # +0.02% per 8h
        
        # Entry/exit fees: 0.05% each side = 0.1% total
        entry_fee_pct = Decimal('0.0005')
        exit_fee_pct = Decimal('0.0005')
        
        # Calculate for 1 day (86400 seconds)
        time_horizon_seconds = 86400
        
        profitability = analyzer.calculate_profitability(
            long_rate_per_sec=long_rate_per_sec,
            short_rate_per_sec=short_rate_per_sec,
            entry_fee_pct=entry_fee_pct,
            exit_fee_pct=exit_fee_pct,
            time_horizon_seconds=time_horizon_seconds
        )
        
        # Expected: (|long_rate| + |short_rate|) * time - (entry_fee + exit_fee)
        expected_funding = (abs(long_rate_per_sec) + abs(short_rate_per_sec)) * Decimal(time_horizon_seconds)
        expected_fees = entry_fee_pct + exit_fee_pct
        expected = expected_funding - expected_fees
        
        assert profitability == expected
    
    def test_calculate_profitability_both_positive_rates(self, analyzer):
        """Test profitability when both rates are positive (paying funding)."""
        # Long DEX: +0.01% (you pay)
        # Short DEX: +0.03% (you receive)
        # Net profit = 0.03% - 0.01% = 0.02% per interval
        long_rate_per_sec = Decimal('0.0001') / Decimal(3600)
        short_rate_per_sec = Decimal('0.0003') / Decimal(3600)
        
        entry_fee_pct = Decimal('0.0005')
        exit_fee_pct = Decimal('0.0005')
        time_horizon_seconds = 86400
        
        profitability = analyzer.calculate_profitability(
            long_rate_per_sec=long_rate_per_sec,
            short_rate_per_sec=short_rate_per_sec,
            entry_fee_pct=entry_fee_pct,
            exit_fee_pct=exit_fee_pct,
            time_horizon_seconds=time_horizon_seconds
        )
        
        # Divergence = |short_rate - long_rate| = |0.0003 - 0.0001| = 0.0002 per hour
        divergence_per_sec = abs(short_rate_per_sec - long_rate_per_sec)
        expected_funding = divergence_per_sec * Decimal(time_horizon_seconds)
        expected_fees = entry_fee_pct + exit_fee_pct
        expected = expected_funding - expected_fees
        
        assert profitability == expected
    
    def test_calculate_profitability_negative_after_fees(self, analyzer):
        """Test that profitability can be negative if fees exceed funding."""
        # Very small funding rate
        long_rate_per_sec = Decimal('0.00001') / Decimal(86400)
        short_rate_per_sec = Decimal('0.00002') / Decimal(86400)
        
        # High fees
        entry_fee_pct = Decimal('0.005')  # 0.5%
        exit_fee_pct = Decimal('0.005')   # 0.5%
        time_horizon_seconds = 3600  # 1 hour
        
        profitability = analyzer.calculate_profitability(
            long_rate_per_sec=long_rate_per_sec,
            short_rate_per_sec=short_rate_per_sec,
            entry_fee_pct=entry_fee_pct,
            exit_fee_pct=exit_fee_pct,
            time_horizon_seconds=time_horizon_seconds
        )
        
        # Should be negative
        assert profitability < 0
    
    def test_select_best_opportunity_simple(self, analyzer):
        """Test selecting best opportunity from multiple options."""
        opportunities = [
            {
                'long_dex': 'lighter',
                'short_dex': 'backpack',
                'long_rate_per_sec': Decimal('-0.0001') / Decimal(3600),
                'short_rate_per_sec': Decimal('0.0002') / Decimal(3600),
                'entry_fee_pct': Decimal('0.0005'),
                'exit_fee_pct': Decimal('0.0005'),
            },
            {
                'long_dex': 'lighter',
                'short_dex': 'edgex',
                'long_rate_per_sec': Decimal('-0.0002') / Decimal(3600),
                'short_rate_per_sec': Decimal('0.0003') / Decimal(3600),
                'entry_fee_pct': Decimal('0.0005'),
                'exit_fee_pct': Decimal('0.0005'),
            },
            {
                'long_dex': 'backpack',
                'short_dex': 'edgex',
                'long_rate_per_sec': Decimal('-0.00005') / Decimal(3600),
                'short_rate_per_sec': Decimal('0.00008') / Decimal(3600),
                'entry_fee_pct': Decimal('0.0005'),
                'exit_fee_pct': Decimal('0.0005'),
            },
        ]
        
        best = analyzer.select_best_opportunity(opportunities, time_horizon_seconds=86400)
        
        # Second opportunity should have highest profitability
        assert best is not None
        assert best['long_dex'] == 'lighter'
        assert best['short_dex'] == 'edgex'
    
    def test_select_best_opportunity_empty_list(self, analyzer):
        """Test that empty opportunity list returns None."""
        best = analyzer.select_best_opportunity([], time_horizon_seconds=86400)
        assert best is None
    
    def test_select_best_opportunity_all_negative(self, analyzer):
        """Test that all negative profitability opportunities return None."""
        opportunities = [
            {
                'long_dex': 'lighter',
                'short_dex': 'backpack',
                'long_rate_per_sec': Decimal('0.00001') / Decimal(86400),
                'short_rate_per_sec': Decimal('0.00002') / Decimal(86400),
                'entry_fee_pct': Decimal('0.01'),  # 1% fee
                'exit_fee_pct': Decimal('0.01'),   # 1% fee
            },
        ]
        
        best = analyzer.select_best_opportunity(opportunities, time_horizon_seconds=3600)
        
        # Should return None if all are unprofitable
        assert best is None
    
    def test_calculate_divergence(self, analyzer):
        """Test funding rate divergence calculation."""
        long_rate = Decimal('0.0001')
        short_rate = Decimal('0.0003')
        
        divergence = analyzer.calculate_divergence(long_rate, short_rate)
        
        # Divergence = |short - long| = |0.0003 - 0.0001| = 0.0002
        assert divergence == Decimal('0.0002')
    
    def test_calculate_divergence_negative_rates(self, analyzer):
        """Test divergence with negative rates."""
        long_rate = Decimal('-0.0001')
        short_rate = Decimal('0.0002')
        
        divergence = analyzer.calculate_divergence(long_rate, short_rate)
        
        # Divergence = |0.0002 - (-0.0001)| = 0.0003
        assert divergence == Decimal('0.0003')
    
    def test_calculate_divergence_both_negative(self, analyzer):
        """Test divergence when both rates are negative."""
        long_rate = Decimal('-0.0003')
        short_rate = Decimal('-0.0001')
        
        divergence = analyzer.calculate_divergence(long_rate, short_rate)
        
        # Divergence = |-0.0001 - (-0.0003)| = 0.0002
        assert divergence == Decimal('0.0002')

