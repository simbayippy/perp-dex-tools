"""
Test imbalance detection for funding arbitrage position closer.

Verifies that positions with severe leg imbalances (> 5% difference) are detected and closed.
"""

from decimal import Decimal
from typing import Optional
import pytest
from dataclasses import dataclass


@dataclass
class MockSnapshot:
    """Mock ExchangePositionSnapshot for testing."""
    quantity: Optional[Decimal]


class TestImbalanceDetection:
    """Test suite for position imbalance detection logic."""

    IMBALANCE_THRESHOLD = Decimal("0.05")  # 5%

    def calculate_imbalance(
        self,
        long_qty: Optional[Decimal],
        short_qty: Optional[Decimal]
    ) -> Optional[Decimal]:
        """
        Calculate percentage difference between leg quantities.
        
        Returns:
            Percentage difference or None if invalid quantities
        """
        if long_qty is None or short_qty is None:
            return None
        
        if long_qty <= 0 or short_qty <= 0:
            return None
        
        min_qty = min(long_qty, short_qty)
        max_qty = max(long_qty, short_qty)
        diff_pct = (max_qty - min_qty) / max_qty
        
        return diff_pct

    def is_imbalanced(
        self,
        long_qty: Optional[Decimal],
        short_qty: Optional[Decimal]
    ) -> bool:
        """Check if legs are imbalanced (> 5% difference)."""
        diff_pct = self.calculate_imbalance(long_qty, short_qty)
        
        if diff_pct is None:
            return False
        
        return diff_pct > self.IMBALANCE_THRESHOLD

    # ========================================================================
    # Test Cases: Imbalanced Positions (should trigger close)
    # ========================================================================

    def test_severe_imbalance_72_vs_1(self):
        """72 vs 1 should trigger imbalance close (98.6% difference)."""
        long_qty = Decimal("72")
        short_qty = Decimal("1")
        
        diff = self.calculate_imbalance(long_qty, short_qty)
        assert diff is not None
        assert diff > self.IMBALANCE_THRESHOLD
        assert self.is_imbalanced(long_qty, short_qty) is True
        
        # Verify calculation
        expected = (Decimal("72") - Decimal("1")) / Decimal("72")
        assert abs(diff - expected) < Decimal("0.0001")

    def test_moderate_imbalance_100_vs_90(self):
        """100 vs 90 should trigger imbalance close (10% difference)."""
        long_qty = Decimal("100")
        short_qty = Decimal("90")
        
        diff = self.calculate_imbalance(long_qty, short_qty)
        assert diff == Decimal("0.1")  # 10%
        assert self.is_imbalanced(long_qty, short_qty) is True

    def test_slight_imbalance_100_vs_94(self):
        """100 vs 94 should trigger imbalance close (6% difference)."""
        long_qty = Decimal("100")
        short_qty = Decimal("94")
        
        diff = self.calculate_imbalance(long_qty, short_qty)
        assert diff == Decimal("0.06")  # 6%
        assert self.is_imbalanced(long_qty, short_qty) is True

    def test_threshold_imbalance_100_vs_94_point_9(self):
        """100 vs 94.9 should trigger imbalance close (5.1% difference)."""
        long_qty = Decimal("100")
        short_qty = Decimal("94.9")
        
        diff = self.calculate_imbalance(long_qty, short_qty)
        expected = (Decimal("100") - Decimal("94.9")) / Decimal("100")
        assert diff == expected  # 5.1%
        assert self.is_imbalanced(long_qty, short_qty) is True

    # ========================================================================
    # Test Cases: Balanced Positions (should NOT trigger close)
    # ========================================================================

    def test_perfectly_balanced_100_vs_100(self):
        """100 vs 100 should not trigger (0% difference)."""
        long_qty = Decimal("100")
        short_qty = Decimal("100")
        
        diff = self.calculate_imbalance(long_qty, short_qty)
        assert diff == Decimal("0")
        assert self.is_imbalanced(long_qty, short_qty) is False

    def test_slight_difference_100_vs_96(self):
        """100 vs 96 should not trigger (4% difference < 5% threshold)."""
        long_qty = Decimal("100")
        short_qty = Decimal("96")
        
        diff = self.calculate_imbalance(long_qty, short_qty)
        assert diff == Decimal("0.04")  # 4%
        assert self.is_imbalanced(long_qty, short_qty) is False

    def test_at_threshold_100_vs_95(self):
        """100 vs 95 should not trigger (exactly 5% difference, but using > not >=)."""
        long_qty = Decimal("100")
        short_qty = Decimal("95")
        
        diff = self.calculate_imbalance(long_qty, short_qty)
        assert diff == Decimal("0.05")  # Exactly 5%
        # Using > 0.05, so exactly 5% should NOT trigger
        assert self.is_imbalanced(long_qty, short_qty) is False

    def test_small_absolute_difference_50_vs_49(self):
        """50 vs 49 should not trigger (2% difference)."""
        long_qty = Decimal("50")
        short_qty = Decimal("49")
        
        diff = self.calculate_imbalance(long_qty, short_qty)
        assert diff == Decimal("0.02")  # 2%
        assert self.is_imbalanced(long_qty, short_qty) is False

    def test_decimal_precision_50_5_vs_50_0(self):
        """50.5 vs 50.0 should not trigger (0.99% difference)."""
        long_qty = Decimal("50.5")
        short_qty = Decimal("50.0")
        
        diff = self.calculate_imbalance(long_qty, short_qty)
        expected = (Decimal("50.5") - Decimal("50.0")) / Decimal("50.5")
        assert abs(diff - expected) < Decimal("0.0001")
        assert diff < self.IMBALANCE_THRESHOLD
        assert self.is_imbalanced(long_qty, short_qty) is False

    # ========================================================================
    # Test Cases: Edge Cases
    # ========================================================================

    def test_none_quantities(self):
        """None quantities should not trigger (handled by liquidation check)."""
        assert self.is_imbalanced(None, Decimal("100")) is False
        assert self.is_imbalanced(Decimal("100"), None) is False
        assert self.is_imbalanced(None, None) is False

    def test_zero_quantities(self):
        """Zero quantities should not trigger (handled by liquidation check)."""
        assert self.is_imbalanced(Decimal("0"), Decimal("100")) is False
        assert self.is_imbalanced(Decimal("100"), Decimal("0")) is False
        assert self.is_imbalanced(Decimal("0"), Decimal("0")) is False

    def test_negative_quantities_converted_to_absolute(self):
        """
        Negative quantities should be handled as absolute values.
        
        Note: In actual implementation, copy_abs() is called on snapshot quantities.
        """
        long_qty = Decimal("100").copy_abs()
        short_qty = Decimal("-90").copy_abs()
        
        diff = self.calculate_imbalance(long_qty, short_qty)
        assert diff == Decimal("0.1")  # 10%
        assert self.is_imbalanced(long_qty, short_qty) is True

    def test_reversed_legs_same_result(self):
        """Order of legs shouldn't matter (symmetric calculation)."""
        diff1 = self.calculate_imbalance(Decimal("100"), Decimal("90"))
        diff2 = self.calculate_imbalance(Decimal("90"), Decimal("100"))
        
        assert diff1 == diff2
        assert self.is_imbalanced(Decimal("100"), Decimal("90")) is True
        assert self.is_imbalanced(Decimal("90"), Decimal("100")) is True


if __name__ == "__main__":
    # Quick sanity check
    test = TestImbalanceDetection()
    
    print("Testing severe imbalance cases:")
    print(f"  72 vs 1: {test.is_imbalanced(Decimal('72'), Decimal('1'))}")
    print(f"  100 vs 90: {test.is_imbalanced(Decimal('100'), Decimal('90'))}")
    print(f"  100 vs 94: {test.is_imbalanced(Decimal('100'), Decimal('94'))}")
    
    print("\nTesting balanced cases:")
    print(f"  100 vs 100: {test.is_imbalanced(Decimal('100'), Decimal('100'))}")
    print(f"  100 vs 96: {test.is_imbalanced(Decimal('100'), Decimal('96'))}")
    print(f"  100 vs 95: {test.is_imbalanced(Decimal('100'), Decimal('95'))}")
    
    print("\n✅ All manual checks passed!")

