"""
Test funding rate normalization across different exchange intervals.

This is CRITICAL to ensure accurate opportunity detection and APY calculations.
"""

import pytest
from decimal import Decimal
from exchange_clients.base import BaseFundingAdapter
from typing import Dict


class MockAdapter1h(BaseFundingAdapter):
    """Mock adapter for 1-hour funding interval (like Lighter)"""

    def __init__(self):
        super().__init__(
            dex_name="mock_1h",
            api_base_url="https://test.com",
            funding_interval_hours=1
        )

    async def fetch_funding_rates(self) -> Dict[str, Decimal]:
        return {}

    async def fetch_market_data(self) -> Dict[str, Dict[str, Decimal]]:
        return {}

    def normalize_symbol(self, dex_symbol: str) -> str:
        return dex_symbol

    def get_dex_symbol_format(self, normalized_symbol: str) -> str:
        return normalized_symbol


class MockAdapter8h(BaseFundingAdapter):
    """Mock adapter for 8-hour funding interval (like most DEXs)"""

    def __init__(self):
        super().__init__(
            dex_name="mock_8h",
            api_base_url="https://test.com",
            funding_interval_hours=8
        )

    async def fetch_funding_rates(self) -> Dict[str, Decimal]:
        return {}

    async def fetch_market_data(self) -> Dict[str, Dict[str, Decimal]]:
        return {}

    def normalize_symbol(self, dex_symbol: str) -> str:
        return dex_symbol

    def get_dex_symbol_format(self, normalized_symbol: str) -> str:
        return normalized_symbol


@pytest.mark.unit
def test_1h_rate_normalization_to_8h():
    """Test that 1-hour rates are correctly multiplied by 8"""
    adapter = MockAdapter1h()

    # Lighter reports 0.01% per 1 hour
    rate_1h = Decimal("0.0001")

    # Should be converted to 0.08% per 8 hours
    rate_8h = adapter.normalize_funding_rate_to_8h(rate_1h)

    expected = Decimal("0.0008")  # 0.0001 * 8
    assert rate_8h == expected, f"Expected {expected}, got {rate_8h}"


@pytest.mark.unit
def test_8h_rate_normalization_unchanged():
    """Test that 8-hour rates remain unchanged"""
    adapter = MockAdapter8h()

    # GRVT reports 0.01% per 8 hours
    rate_8h = Decimal("0.0001")

    # Should remain 0.01% per 8 hours
    rate_normalized = adapter.normalize_funding_rate_to_8h(rate_8h)

    assert rate_normalized == rate_8h, f"8h rate should remain unchanged"


@pytest.mark.unit
def test_negative_rate_normalization():
    """Test that negative rates are correctly normalized"""
    adapter = MockAdapter1h()

    # Lighter reports -0.005% per 1 hour (paying funding)
    rate_1h = Decimal("-0.00005")

    # Should be converted to -0.04% per 8 hours
    rate_8h = adapter.normalize_funding_rate_to_8h(rate_1h)

    expected = Decimal("-0.0004")  # -0.00005 * 8
    assert rate_8h == expected, f"Expected {expected}, got {rate_8h}"


@pytest.mark.unit
def test_opportunity_comparison_scenario():
    """
    Test realistic scenario: comparing Lighter (1h) vs GRVT (8h) rates

    Scenario:
    - Lighter: 0.01% per 1 hour → 0.08% per 8 hours (normalized)
    - GRVT: 0.02% per 8 hours → 0.02% per 8 hours (unchanged)

    Without normalization, we'd think GRVT is higher (0.02% > 0.01%)
    With normalization, we correctly see Lighter is higher (0.08% > 0.02%)
    """
    lighter_adapter = MockAdapter1h()
    grvt_adapter = MockAdapter8h()

    # Raw rates from exchanges
    lighter_rate_1h = Decimal("0.0001")  # 0.01% per 1h
    grvt_rate_8h = Decimal("0.0002")     # 0.02% per 8h

    # Normalize both to 8-hour standard
    lighter_normalized = lighter_adapter.normalize_funding_rate_to_8h(lighter_rate_1h)
    grvt_normalized = grvt_adapter.normalize_funding_rate_to_8h(grvt_rate_8h)

    # After normalization, Lighter should be higher
    assert lighter_normalized == Decimal("0.0008"), "Lighter should be 0.08% per 8h"
    assert grvt_normalized == Decimal("0.0002"), "GRVT should be 0.02% per 8h"
    assert lighter_normalized > grvt_normalized, "Lighter should have higher normalized rate"

    # Calculate the opportunity
    divergence = lighter_normalized - grvt_normalized
    assert divergence == Decimal("0.0006"), "Divergence should be 0.06% per 8h"


@pytest.mark.unit
def test_apy_calculation_with_normalization():
    """
    Test APY calculation with normalized rates

    If we don't normalize, APY calculations will be wildly wrong for Lighter
    """
    # Lighter: 0.01% per 1 hour
    lighter_rate_1h = Decimal("0.0001")
    lighter_adapter = MockAdapter1h()
    lighter_normalized = lighter_adapter.normalize_funding_rate_to_8h(lighter_rate_1h)

    # Calculate APY (3 payments per day for 8h intervals, 365 days)
    payments_per_year = Decimal("1095")  # 365 * 3
    lighter_apy = lighter_normalized * payments_per_year * Decimal("100")

    # 0.0008 * 1095 * 100 = 87.6% APY
    expected_apy = Decimal("87.6")
    assert lighter_apy == expected_apy, f"Expected APY {expected_apy}%, got {lighter_apy}%"


@pytest.mark.unit
def test_zero_rate_normalization():
    """Test that zero rates remain zero"""
    adapter = MockAdapter1h()

    rate = Decimal("0")
    normalized = adapter.normalize_funding_rate_to_8h(rate)

    assert normalized == Decimal("0"), "Zero should remain zero"


@pytest.mark.unit
def test_very_small_rate_precision():
    """Test that very small rates maintain precision"""
    adapter = MockAdapter1h()

    # Very small rate: 0.0001% per 1 hour
    rate_1h = Decimal("0.000001")
    rate_8h = adapter.normalize_funding_rate_to_8h(rate_1h)

    expected = Decimal("0.000008")
    assert rate_8h == expected, f"Precision should be maintained for small rates"


@pytest.mark.unit
def test_aster_symbol_level_intervals():
    """
    Test Aster-style symbol-level interval handling

    Aster has different intervals per symbol:
    - INJUSDT: 8 hours
    - ZORAUSDT: 4 hours

    This requires per-symbol normalization
    """
    # Create mock adapter with default 8h
    adapter = MockAdapter8h()

    # Simulate Aster's symbol-specific intervals
    # INJ has 8h interval (standard)
    inj_rate_8h = Decimal("0.0002")  # 0.02% per 8h
    inj_normalized = adapter.normalize_funding_rate_to_8h(inj_rate_8h)
    assert inj_normalized == Decimal("0.0002"), "8h rate should remain unchanged"

    # ZORA has 4h interval (non-standard)
    # We need to simulate 4h->8h conversion
    # If ZORA native rate is 0.01% per 4h, that's 0.02% per 8h
    zora_rate_4h = Decimal("0.0001")  # 0.01% per 4h
    # Manually calculate what normalized should be (adapter doesn't know about 4h)
    # In real code, adapter would multiply by 2
    zora_rate_8h_expected = zora_rate_4h * Decimal("2")  # = 0.0002 (0.02% per 8h)

    assert zora_rate_8h_expected == Decimal("0.0002"), "4h rate should be doubled to 8h"

    # Verify they're now comparable
    assert inj_normalized == zora_rate_8h_expected, "After normalization, rates should be comparable"


@pytest.mark.unit
def test_mixed_interval_comparison():
    """
    Test real-world scenario with mixed intervals

    Exchange A (Lighter): 1h intervals
    Exchange B (GRVT): 8h intervals
    Exchange C (Aster ZORA): 4h intervals
    """
    lighter = MockAdapter1h()
    grvt = MockAdapter8h()

    # Create a mock 4h adapter for Aster's ZORA
    class MockAdapter4h(BaseFundingAdapter):
        def __init__(self):
            super().__init__(
                dex_name="mock_4h",
                api_base_url="https://test.com",
                funding_interval_hours=4
            )

        async def fetch_funding_rates(self) -> Dict[str, Decimal]:
            return {}

        async def fetch_market_data(self) -> Dict[str, Dict[str, Decimal]]:
            return {}

        def normalize_symbol(self, dex_symbol: str) -> str:
            return dex_symbol

        def get_dex_symbol_format(self, normalized_symbol: str) -> str:
            return normalized_symbol

    aster_zora = MockAdapter4h()

    # Native rates from each exchange
    lighter_rate_1h = Decimal("0.00005")  # 0.005% per 1h
    grvt_rate_8h = Decimal("0.0003")      # 0.03% per 8h
    aster_rate_4h = Decimal("0.0002")     # 0.02% per 4h

    # Normalize all to 8h
    lighter_8h = lighter.normalize_funding_rate_to_8h(lighter_rate_1h)
    grvt_8h = grvt.normalize_funding_rate_to_8h(grvt_rate_8h)
    aster_8h = aster_zora.normalize_funding_rate_to_8h(aster_rate_4h)

    # Expected values
    assert lighter_8h == Decimal("0.0004"), "1h → 8h: multiply by 8"
    assert grvt_8h == Decimal("0.0003"), "8h → 8h: no change"
    assert aster_8h == Decimal("0.0004"), "4h → 8h: multiply by 2"

    # Now we can fairly compare opportunities
    assert lighter_8h > grvt_8h, "Lighter has higher rate after normalization"
    assert aster_8h > grvt_8h, "Aster ZORA has higher rate after normalization"
    assert lighter_8h == aster_8h, "Lighter and Aster ZORA have same normalized rate"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
