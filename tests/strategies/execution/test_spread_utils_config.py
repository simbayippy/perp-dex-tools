"""Tests for spread_utils configuration from strategy config."""

import pytest
from decimal import Decimal
from strategies.execution.core.spread_utils import (
    configure_spread_thresholds,
    is_spread_acceptable,
    SpreadCheckType,
    _SPREAD_THRESHOLDS,
)


@pytest.fixture(autouse=True)
def reset_thresholds():
    """Reset spread thresholds to defaults before each test."""
    global _SPREAD_THRESHOLDS
    # Store original thresholds
    original = _SPREAD_THRESHOLDS.copy()

    yield

    # Restore original thresholds after test
    _SPREAD_THRESHOLDS.update(original)


def test_configure_spread_thresholds():
    """Test that configure_spread_thresholds updates module-level constants."""
    # Configure custom thresholds
    configure_spread_thresholds(
        entry_threshold=Decimal("0.005"),  # 0.5%
        exit_threshold=Decimal("0.01"),    # 1%
        emergency_threshold=Decimal("0.02"),  # 2%
        hedge_threshold=Decimal("0.001"),  # 0.1%
    )

    # Verify constants were updated
    assert _SPREAD_THRESHOLDS[SpreadCheckType.ENTRY] == Decimal("0.005")
    assert _SPREAD_THRESHOLDS[SpreadCheckType.EXIT] == Decimal("0.01")
    assert _SPREAD_THRESHOLDS[SpreadCheckType.EMERGENCY_CLOSE] == Decimal("0.02")
    assert _SPREAD_THRESHOLDS[SpreadCheckType.AGGRESSIVE_HEDGE] == Decimal("0.001")


def test_is_spread_acceptable_uses_configured_thresholds():
    """Test that is_spread_acceptable uses configured thresholds."""
    # Configure stricter threshold for exit
    configure_spread_thresholds(exit_threshold=Decimal("0.0005"))  # 0.05%

    bid = Decimal("100")
    ask = Decimal("100.06")  # 0.06% spread

    # Should fail with stricter 0.05% threshold
    is_ok, spread_pct, reason = is_spread_acceptable(bid, ask, SpreadCheckType.EXIT)
    assert not is_ok
    assert spread_pct is not None
    assert spread_pct > Decimal("0.0005")

    # Should pass with looser 0.1% threshold
    configure_spread_thresholds(exit_threshold=Decimal("0.001"))  # 0.1%
    is_ok, spread_pct, reason = is_spread_acceptable(bid, ask, SpreadCheckType.EXIT)
    assert is_ok


def test_partial_configuration():
    """Test that configure_spread_thresholds supports partial updates."""
    # Configure only entry threshold
    configure_spread_thresholds(entry_threshold=Decimal("0.002"))

    # Entry threshold updated
    assert _SPREAD_THRESHOLDS[SpreadCheckType.ENTRY] == Decimal("0.002")

    # Other thresholds unchanged
    assert _SPREAD_THRESHOLDS[SpreadCheckType.EXIT] == Decimal("0.001")
    assert _SPREAD_THRESHOLDS[SpreadCheckType.EMERGENCY_CLOSE] == Decimal("0.002")


def test_funding_arbitrage_typical_config():
    """Test typical funding arbitrage configuration (from config.py defaults)."""
    # Configure with funding arbitrage defaults
    configure_spread_thresholds(
        entry_threshold=Decimal("0.001"),   # 0.1%
        exit_threshold=Decimal("0.001"),    # 0.1%
        emergency_threshold=Decimal("0.002"),  # 0.2%
    )

    # Test entry with 0.08% spread (should pass)
    bid = Decimal("100")
    ask = Decimal("100.08")
    is_ok, _, _ = is_spread_acceptable(bid, ask, SpreadCheckType.ENTRY)
    assert is_ok

    # Test exit with 0.15% spread (should fail)
    ask = Decimal("100.15")
    is_ok, _, _ = is_spread_acceptable(bid, ask, SpreadCheckType.EXIT)
    assert not is_ok

    # Test emergency with 0.15% spread (should pass - uses 0.2% threshold)
    is_ok, _, _ = is_spread_acceptable(bid, ask, SpreadCheckType.EMERGENCY_CLOSE)
    assert is_ok
