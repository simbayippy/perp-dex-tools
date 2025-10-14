from decimal import Decimal
from datetime import datetime, timedelta
from uuid import uuid4

from strategies.implementations.funding_arbitrage.models import FundingArbPosition
from strategies.implementations.funding_arbitrage.risk_management import (
    CombinedRiskManager,
    DivergenceFlipRiskManager,
    ProfitErosionRiskManager,
)


def _position(entry_divergence=Decimal("0.05")):
    return FundingArbPosition(
        id=uuid4(),
        symbol="BTC",
        long_dex="aster",
        short_dex="lighter",
        size_usd=Decimal("1000"),
        entry_long_rate=Decimal("-0.01"),
        entry_short_rate=Decimal("0.04"),
        entry_divergence=entry_divergence,
        opened_at=datetime.now(),
    )


def test_divergence_flip_risk_manager_triggers_exit():
    manager = DivergenceFlipRiskManager({"flip_margin": Decimal("0")})
    position = _position()
    should_exit, reason = manager.should_exit(
        position,
        {
            "divergence": Decimal("-0.001"),
            "long_rate": Decimal("-0.01"),
            "short_rate": Decimal("0.009"),
        },
    )
    assert should_exit is True
    assert reason == "DIVERGENCE_FLIPPED"


def test_profit_erosion_risk_manager_threshold():
    manager = ProfitErosionRiskManager({"min_erosion_ratio": 0.5})
    position = _position(entry_divergence=Decimal("0.04"))
    should_exit, reason = manager.should_exit(
        position,
        {
            "divergence": Decimal("0.015"),  # 0.015/0.04 = 0.375 < 0.5
            "long_rate": Decimal("-0.01"),
            "short_rate": Decimal("0.005"),
        },
    )
    assert should_exit is True
    assert reason == "PROFIT_EROSION"


def test_combined_risk_manager_priority():
    manager = CombinedRiskManager(
        {
            "min_erosion_ratio": 0.6,
            "severe_erosion_ratio": 0.4,
            "flip_margin": Decimal("0"),
            "max_position_age_hours": 100,
        }
    )
    position = _position(entry_divergence=Decimal("0.05"))

    # Divergence flip should take priority over erosion/time
    should_exit, reason = manager.should_exit(
        position,
        {
            "divergence": Decimal("-0.0001"),
            "long_rate": Decimal("-0.01"),
            "short_rate": Decimal("0.0099"),
        },
    )
    assert should_exit is True
    assert reason == "DIVERGENCE_FLIPPED"

    # Severe erosion beats normal erosion
    should_exit, reason = manager.should_exit(
        position,
        {
            "divergence": Decimal("0.015"),  # 0.015/0.05 = 0.3 < severe threshold 0.4
            "long_rate": Decimal("-0.01"),
            "short_rate": Decimal("0.025"),
        },
    )
    assert should_exit is True
    assert reason == "SEVERE_EROSION"

    # Normal erosion path
    should_exit, reason = manager.should_exit(
        position,
        {
            "divergence": Decimal("0.028"),  # ratio 0.56 < min_erosion_ratio (0.6)
            "long_rate": Decimal("-0.01"),
            "short_rate": Decimal("0.018"),
        },
    )
    assert should_exit is True
    assert reason == "PROFIT_EROSION"

    # Time-based exit when thresholds not met
    aged_position = _position()
    aged_position.opened_at = datetime.now() - timedelta(hours=200)
    should_exit, reason = manager.should_exit(
        aged_position,
        {
            "divergence": Decimal("0.045"),
            "long_rate": Decimal("-0.01"),
            "short_rate": Decimal("0.035"),
        },
    )
    assert should_exit is True
    assert reason == "TIME_LIMIT"
