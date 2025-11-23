"""
Unit tests for wide spread protection on exit.

Tests spread calculation, deferral logic, and execution mode selection.
"""

import asyncio
from decimal import Decimal
from datetime import datetime
from types import SimpleNamespace
from typing import Dict
from uuid import uuid4
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from strategies.execution.core.spread_utils import (
    calculate_spread_pct,
    SpreadCheckType,
    is_spread_acceptable,
)
from strategies.implementations.funding_arbitrage.operations.closing.order_builder import (
    OrderBuilder,
    WideSpreadException,
)
from strategies.implementations.funding_arbitrage.operations.closing.close_executor import CloseExecutor
from strategies.implementations.funding_arbitrage.models import FundingArbPosition
from exchange_clients.base_models import ExchangePositionSnapshot

# UPDATED: New tight thresholds
MAX_EXIT_SPREAD_PCT = Decimal("0.001")      # 0.1%
MAX_EMERGENCY_CLOSE_SPREAD_PCT = Decimal("0.002")  # 0.2%


class TestSpreadCalculation:
    """Tests for calculate_spread_pct() function."""
    
    def test_calculate_spread_pct_valid(self):
        """Test spread calculation with valid inputs."""
        bid = Decimal("100")
        ask = Decimal("102")
        spread_pct = calculate_spread_pct(bid, ask)
        
        mid_price = (bid + ask) / 2
        expected_spread = (ask - bid) / mid_price
        assert spread_pct == expected_spread
        # Use approximate comparison due to Decimal precision
        assert abs(float(spread_pct) - 0.019801980198019802) < 0.0001  # ~1.98%
    
    def test_calculate_spread_pct_small_spread(self):
        """Test spread calculation with small spread."""
        bid = Decimal("100")
        ask = Decimal("100.1")
        spread_pct = calculate_spread_pct(bid, ask)
        
        # Use approximate comparison due to Decimal precision
        assert abs(float(spread_pct) - 0.001) < 0.0001  # ~0.1%
    
    def test_calculate_spread_pct_wide_spread(self):
        """Test spread calculation with wide spread (like Paradex)."""
        bid = Decimal("369.64")
        ask = Decimal("378.2")
        spread_pct = calculate_spread_pct(bid, ask)
        
        # Should be ~2.3%
        assert spread_pct > Decimal("0.02")
        assert spread_pct < Decimal("0.025")
    
    def test_calculate_spread_pct_invalid_bid_zero(self):
        """Test spread calculation with zero bid."""
        bid = Decimal("0")
        ask = Decimal("100")
        spread_pct = calculate_spread_pct(bid, ask)
        
        assert spread_pct is None
    
    def test_calculate_spread_pct_invalid_ask_zero(self):
        """Test spread calculation with zero ask."""
        bid = Decimal("100")
        ask = Decimal("0")
        spread_pct = calculate_spread_pct(bid, ask)
        
        assert spread_pct is None
    
    def test_calculate_spread_pct_invalid_bid_greater_than_ask(self):
        """Test spread calculation with invalid BBO (bid > ask)."""
        bid = Decimal("102")
        ask = Decimal("100")
        spread_pct = calculate_spread_pct(bid, ask)
        
        assert spread_pct is None
    
    def test_calculate_spread_pct_equal_bid_ask(self):
        """Test spread calculation with equal bid and ask."""
        bid = Decimal("100")
        ask = Decimal("100")
        spread_pct = calculate_spread_pct(bid, ask)
        
        assert spread_pct == Decimal("0")


class TestWideSpreadException:
    """Tests for WideSpreadException class."""
    
    def test_wide_spread_exception_creation(self):
        """Test WideSpreadException creation and attributes."""
        spread_pct = Decimal("0.025")
        bid = Decimal("369.64")
        ask = Decimal("378.2")
        exchange = "paradex"
        symbol = "XMR-PERP"
        
        exc = WideSpreadException(spread_pct, bid, ask, exchange, symbol)
        
        assert exc.spread_pct == spread_pct
        assert exc.bid == bid
        assert exc.ask == ask
        assert exc.exchange == exchange
        assert exc.symbol == symbol
        assert "Wide spread detected" in str(exc)
        assert "paradex" in str(exc)
        assert "XMR-PERP" in str(exc)


class TestOrderBuilderSpreadCheck:
    """Tests for spread checking in OrderBuilder."""
    
    def _make_strategy(self, price_provider_bbo=None):
        """Create a mock strategy."""
        if price_provider_bbo is None:
            price_provider_bbo = (Decimal("100"), Decimal("100.05"))  # UPDATED: Default 0.05% spread
        
        price_provider = SimpleNamespace(
            get_bbo_prices=AsyncMock(return_value=price_provider_bbo)
        )
        
        config = SimpleNamespace(
            enable_wide_spread_protection=True,
            max_exit_spread_pct=MAX_EXIT_SPREAD_PCT,
            max_emergency_close_spread_pct=MAX_EMERGENCY_CLOSE_SPREAD_PCT,
            limit_order_offset_pct=Decimal("0.0001"),
        )
        
        return SimpleNamespace(
            config=config,
            price_provider=price_provider,
            logger=SimpleNamespace(
                info=MagicMock(),
                warning=MagicMock(),
                error=MagicMock(),
            ),
        )
    
    def _make_leg(self, dex="aster", quantity=Decimal("1")):
        """Create a mock leg dictionary."""
        snapshot = ExchangePositionSnapshot(
            symbol="BTC-PERP",
            quantity=quantity,
            side="long",
            mark_price=Decimal("100"),
        )
        
        client = SimpleNamespace(
            get_exchange_name=lambda: dex,
            fetch_bbo_prices=AsyncMock(return_value=(Decimal("100"), Decimal("100.05"))),  # UPDATED
        )
        
        return {
            "dex": dex,
            "client": client,
            "snapshot": snapshot,
            "side": "sell",
            "quantity": quantity,
            "metadata": {},
        }
    
    @pytest.mark.asyncio
    async def test_non_critical_exit_defer_on_wide_spread(self):
        """Test that non-critical exits are deferred when spread > 0.1%."""
        # UPDATED: 0.3% spread (bid=100, ask=100.3)
        strategy = self._make_strategy(price_provider_bbo=(Decimal("100"), Decimal("100.3")))
        builder = OrderBuilder(strategy)
        
        leg = self._make_leg()
        reason = "PROFIT_EROSION"  # Non-critical
        
        with pytest.raises(WideSpreadException) as exc_info:
            await builder.build_order_spec("BTC-PERP", leg, reason=reason)
        
        assert exc_info.value.spread_pct > MAX_EXIT_SPREAD_PCT
        assert exc_info.value.exchange == "aster"
        assert exc_info.value.symbol == "BTC-PERP"
    
    @pytest.mark.asyncio
    async def test_non_critical_exit_proceed_on_acceptable_spread(self):
        """Test that non-critical exits proceed when spread <= 0.1%."""
        # UPDATED: 0.08% spread (bid=100, ask=100.08) - within 0.1% threshold
        strategy = self._make_strategy(price_provider_bbo=(Decimal("100"), Decimal("100.08")))
        builder = OrderBuilder(strategy)
        
        leg = self._make_leg()
        reason = "PROFIT_EROSION"  # Non-critical
        
        spec = await builder.build_order_spec("BTC-PERP", leg, reason=reason)

        assert spec is not None
        # Non-critical exits now use limit_only for passive maker orders
        assert spec.execution_mode == "limit_only"
    
    @pytest.mark.asyncio
    async def test_critical_exit_proceed_despite_wide_spread(self):
        """Test that critical exits proceed even with wide spread."""
        # UPDATED: 0.5% spread (bid=100, ask=100.5) - exceeds both thresholds
        strategy = self._make_strategy(price_provider_bbo=(Decimal("100"), Decimal("100.5")))
        builder = OrderBuilder(strategy)
        
        leg = self._make_leg()
        reason = "LEG_LIQUIDATED"  # Critical
        
        spec = await builder.build_order_spec("BTC-PERP", leg, reason=reason)
        
        assert spec is not None
        # Should use market_only for critical reasons by default
        assert spec.execution_mode == "market_only"
    
    @pytest.mark.asyncio
    async def test_user_manual_close_warns_but_proceeds(self):
        """Test that user manual closes warn but don't defer."""
        # UPDATED: 0.25% spread (bid=100, ask=100.25)
        strategy = self._make_strategy(price_provider_bbo=(Decimal("100"), Decimal("100.25")))
        builder = OrderBuilder(strategy)
        
        leg = self._make_leg()
        reason = "telegram_manual_close"
        order_type = "market"
        
        spec = await builder.build_order_spec("BTC-PERP", leg, reason=reason, order_type=order_type)
        
        assert spec is not None
        assert spec.execution_mode == "market_only"
        # Should have logged warning
        assert strategy.logger.warning.called
    
    @pytest.mark.asyncio
    async def test_spread_check_disabled(self):
        """Test that spread check is skipped when protection is disabled."""
        # UPDATED: 0.5% spread
        strategy = self._make_strategy(price_provider_bbo=(Decimal("100"), Decimal("100.5")))
        strategy.config.enable_wide_spread_protection = False
        builder = OrderBuilder(strategy)
        
        leg = self._make_leg()
        reason = "PROFIT_EROSION"  # Non-critical
        
        spec = await builder.build_order_spec("BTC-PERP", leg, reason=reason)
        
        assert spec is not None
        # Should proceed without checking spread
    
    @pytest.mark.asyncio
    async def test_bbo_fetch_failure_fallback(self):
        """Test that BBO fetch failure doesn't block closing."""
        strategy = self._make_strategy()
        strategy.price_provider.get_bbo_prices = AsyncMock(side_effect=Exception("API error"))
        builder = OrderBuilder(strategy)
        
        leg = self._make_leg()
        reason = "PROFIT_EROSION"
        
        spec = await builder.build_order_spec("BTC-PERP", leg, reason=reason)
        
        assert spec is not None
        # Should proceed with original logic
        assert strategy.logger.warning.called


class TestCloseExecutorSpreadCheck:
    """Tests for spread checking in CloseExecutor."""
    
    def _make_strategy(self, price_provider_bbo=None):
        """Create a mock strategy."""
        if price_provider_bbo is None:
            price_provider_bbo = (Decimal("100"), Decimal("100.05"))  # UPDATED: Default 0.05% spread
        
        price_provider = SimpleNamespace(
            get_bbo_prices=AsyncMock(return_value=price_provider_bbo)
        )
        
        config = SimpleNamespace(
            enable_wide_spread_protection=True,
            max_exit_spread_pct=MAX_EXIT_SPREAD_PCT,
            max_emergency_close_spread_pct=MAX_EMERGENCY_CLOSE_SPREAD_PCT,
        )
        
        order_executor = SimpleNamespace(
            execute_order=AsyncMock(return_value=SimpleNamespace(
                success=True,
                filled=True,
                filled_quantity=Decimal("1"),
                fill_price=Decimal("100"),
            ))
        )
        
        return SimpleNamespace(
            config=config,
            price_provider=price_provider,
            order_executor=order_executor,
            logger=SimpleNamespace(
                info=MagicMock(),
                warning=MagicMock(),
                error=MagicMock(),
            ),
            _contract_preparer=SimpleNamespace(
                prepare_contract_context=AsyncMock(return_value="CONTRACT-123")
            ),
        )
    
    def _make_position(self):
        """Create a mock position."""
        return FundingArbPosition(
            id=uuid4(),
            symbol="BTC-PERP",
            long_dex="aster",
            short_dex="lighter",
            size_usd=Decimal("1000"),
            entry_long_rate=Decimal("-0.01"),
            entry_short_rate=Decimal("0.03"),
            entry_divergence=Decimal("0.04"),
            opened_at=datetime.now(),
            metadata={
                "legs": {
                    "aster": {
                        "entry_price": Decimal("100"),
                    }
                }
            }
        )
    
    def _make_leg(self, dex="aster"):
        """Create a mock leg dictionary."""
        snapshot = ExchangePositionSnapshot(
            symbol="BTC-PERP",
            quantity=Decimal("1"),
            side="long",
            mark_price=Decimal("100"),
        )
        
        client = SimpleNamespace(
            get_exchange_name=lambda: dex,
            fetch_bbo_prices=AsyncMock(return_value=(Decimal("100"), Decimal("100.05"))),  # UPDATED
        )
        
        return {
            "dex": dex,
            "client": client,
            "snapshot": snapshot,
            "side": "sell",
            "quantity": Decimal("1"),
            "metadata": {},
        }
    
    @pytest.mark.asyncio
    async def test_emergency_close_uses_aggressive_limit_on_wide_spread(self):
        """Test that emergency close uses aggressive_limit when spread is wide."""
        # UPDATED: 0.4% spread (bid=100, ask=100.4) - exceeds 0.2% emergency threshold
        strategy = self._make_strategy(price_provider_bbo=(Decimal("100"), Decimal("100.4")))
        executor = CloseExecutor(strategy)
        executor._order_executor = strategy.order_executor
        
        position = self._make_position()
        leg = self._make_leg()
        reason = "LEG_LIQUIDATED"  # Critical
        
        await executor._force_close_leg(position, leg, reason=reason)
        
        # Should have called execute_order with AGGRESSIVE_LIMIT mode
        call_args = strategy.order_executor.execute_order.await_args
        assert call_args is not None
        assert call_args.kwargs.get("mode").value == "aggressive_limit"
        assert call_args.kwargs.get("reduce_only") is True
    
    @pytest.mark.asyncio
    async def test_emergency_close_uses_market_on_acceptable_spread(self):
        """Test that emergency close uses market when spread is acceptable."""
        # UPDATED: 0.15% spread (bid=100, ask=100.15) - within 0.2% emergency threshold
        strategy = self._make_strategy(price_provider_bbo=(Decimal("100"), Decimal("100.15")))
        executor = CloseExecutor(strategy)
        executor._order_executor = strategy.order_executor
        
        position = self._make_position()
        leg = self._make_leg()
        reason = "LEG_LIQUIDATED"
        
        await executor._force_close_leg(position, leg, reason=reason)
        
        # Should use market_only for critical reasons with acceptable spread
        call_args = strategy.order_executor.execute_order.await_args
        assert call_args is not None
        assert call_args.kwargs.get("mode").value == "market_only"
    
    @pytest.mark.asyncio
    async def test_emergency_close_proceeds_despite_very_wide_spread(self):
        """Test that emergency close proceeds even with very wide spread."""
        # UPDATED: 1% spread (bid=100, ask=101) - way exceeds emergency threshold
        strategy = self._make_strategy(price_provider_bbo=(Decimal("100"), Decimal("101")))
        executor = CloseExecutor(strategy)
        executor._order_executor = strategy.order_executor
        
        position = self._make_position()
        leg = self._make_leg()
        reason = "LEG_LIQUIDATED"
        
        await executor._force_close_leg(position, leg, reason=reason)
        
        # Should still proceed (emergency)
        assert strategy.order_executor.execute_order.called
        assert strategy.logger.warning.called  # Should log warning
    
    @pytest.mark.asyncio
    async def test_emergency_close_bbo_fetch_failure_fallback(self):
        """Test that BBO fetch failure doesn't block emergency close."""
        strategy = self._make_strategy()
        strategy.price_provider.get_bbo_prices = AsyncMock(side_effect=Exception("API error"))
        executor = CloseExecutor(strategy)
        executor._order_executor = strategy.order_executor
        
        position = self._make_position()
        leg = self._make_leg()
        reason = "LEG_LIQUIDATED"
        
        await executor._force_close_leg(position, leg, reason=reason)
        
        # Should proceed with fallback (market order)
        assert strategy.order_executor.execute_order.called
        assert strategy.logger.warning.called  # Should log warning about BBO fetch failure