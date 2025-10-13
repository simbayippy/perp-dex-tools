"""Helpers for scanning and filtering funding arbitrage opportunities."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from ..strategy import FundingArbitrageStrategy
    from ..models import OpportunityData


class OpportunityScanner:
    """Encapsulates opportunity discovery and filtering logic."""

    def __init__(self, strategy: "FundingArbitrageStrategy") -> None:
        self._strategy = strategy

    async def scan(self) -> List["OpportunityData"]:
        strategy = self._strategy
        candidates: List["OpportunityData"] = []

        try:
            if not self.has_capacity():
                return candidates

            from funding_rate_service.models.filters import OpportunityFilter

            available_exchanges = list(strategy.exchange_clients.keys())
            filters = OpportunityFilter(
                min_profit_percent=strategy.config.min_profit,
                max_oi_usd=strategy.config.max_oi_usd,
                whitelist_dexes=available_exchanges if available_exchanges else None,
                symbol=None,
                limit=10,
            )

            strategy.logger.log(
                f"Filters - min_profit: {strategy.config.min_profit}, "
                f"max_oi_usd: {strategy.config.max_oi_usd}, "
                f"configured_dexes: {strategy.config.exchanges}, available_dexes: {available_exchanges}",
                "INFO",
            )

            opportunities = await strategy.opportunity_finder.find_opportunities(filters)

            strategy.logger.log(
                f"Found {len(opportunities)} opportunities",
                "INFO",
            )

            max_new = strategy.config.max_new_positions_per_cycle
            for opportunity in opportunities[:max_new]:
                if opportunity.symbol in strategy.failed_symbols:
                    strategy.logger.log(
                        f"⏭️  Skipping {opportunity.symbol} - already failed validation this cycle",
                        "DEBUG",
                    )
                    continue

                if not self.has_capacity():
                    break

                if self.should_take(opportunity):
                    candidates.append(opportunity)

        except Exception as exc:  # pragma: no cover - defensive logging
            strategy.logger.log(f"Error scanning opportunities: {exc}", "ERROR")
      
        return candidates

    def should_take(self, opportunity) -> bool:
        strategy = self._strategy
        long_dex = opportunity.long_dex
        short_dex = opportunity.short_dex

        if long_dex not in strategy.exchange_clients:
            strategy.logger.log(
                f"⚠️  SAFETY CHECK: Skipping {opportunity.symbol} opportunity - "
                f"{long_dex} (long side) not in available clients",
                "WARNING",
            )
            return False

        if short_dex not in strategy.exchange_clients:
            strategy.logger.log(
                f"⚠️  SAFETY CHECK: Skipping {opportunity.symbol} opportunity - "
                f"{short_dex} (short side) not in available clients",
                "WARNING",
            )
            return False

        size_usd = strategy.config.default_position_size_usd
        if size_usd > strategy.config.max_position_size_usd:
            return False

        current_exposure = self.calculate_total_exposure()
        if current_exposure + size_usd > strategy.config.max_total_exposure_usd:
            return False

        return True

    def has_capacity(self) -> bool:
        strategy = self._strategy
        open_count = len(strategy.position_manager._positions)

        if open_count >= strategy.config.max_positions:
            if not strategy._max_position_warning_logged:
                strategy.logger.log(
                    f"🚫 Max positions reached ({open_count}/{strategy.config.max_positions}). "
                    "Skipping new opportunities until capacity frees up.",
                    "INFO",
                )
                strategy._max_position_warning_logged = True
            return False

        strategy._max_position_warning_logged = False

        if strategy.one_position_per_session and strategy.position_opened_this_session:
            if not strategy._session_limit_warning_logged:
                strategy.logger.log(
                    "📊 Session limit reached: already opened 1 position this session. "
                    "Set one_position_per_session=False to allow multiple positions.",
                    "INFO",
                )
                strategy._session_limit_warning_logged = True
            return False

        strategy._session_limit_warning_logged = False
        return True

    def calculate_total_exposure(self) -> Decimal:
        strategy = self._strategy
        positions = strategy.position_manager._positions.values()
        return sum(p.size_usd for p in positions if p.status == "open")
