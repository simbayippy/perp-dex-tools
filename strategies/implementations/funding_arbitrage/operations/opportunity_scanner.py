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
            if not await self.has_capacity():
                return candidates

            from funding_rate_service.models.filters import OpportunityFilter

            available_exchanges = [name.lower() for name in strategy.exchange_clients.keys()]
            mandatory_dex = getattr(strategy.config, "mandatory_exchange", None)
            if not mandatory_dex:
                mandatory_dex = getattr(strategy.config, "primary_exchange", None)
            if isinstance(mandatory_dex, str) and mandatory_dex.strip():
                mandatory_dex = mandatory_dex.strip().lower()
            else:
                mandatory_dex = None

            max_oi_cap = strategy.config.max_oi_usd if mandatory_dex else None

            filters = OpportunityFilter(
                min_profit_percent=strategy.config.min_profit,
                max_oi_usd=max_oi_cap,
                whitelist_dexes=available_exchanges if available_exchanges else None,
                required_dex=mandatory_dex,
                symbol=None,
                limit=10,
            )

            strategy.logger.debug(
                f"Filters - min_profit: {strategy.config.min_profit}, "
                f"mandatory_dex: {mandatory_dex}, max_oi_cap: {max_oi_cap}, "
                f"configured_dexes: {strategy.config.exchanges}, available_dexes: {available_exchanges}"
            )

            opportunities = await strategy.opportunity_finder.find_opportunities(filters)

            strategy.logger.info(f"Found {len(opportunities)} opportunities")

            max_new = strategy.config.max_new_positions_per_cycle
            for opportunity in opportunities[:max_new]:
                if opportunity.symbol in strategy.failed_symbols:
                    strategy.logger.debug(
                        f"⏭️  Skipping {opportunity.symbol} - already failed validation this cycle"
                    )
                    continue

                if not await self.has_capacity():
                    break

                if await self.should_take(opportunity):
                    candidates.append(opportunity)

        except Exception as exc:  # pragma: no cover - defensive logging
            strategy.logger.error(f"Error scanning opportunities: {exc}")
      
        return candidates

    async def should_take(self, opportunity) -> bool:
        strategy = self._strategy
        long_dex = opportunity.long_dex
        short_dex = opportunity.short_dex

        if long_dex not in strategy.exchange_clients:
            strategy.logger.warning(
                f"⚠️  SAFETY CHECK: Skipping {opportunity.symbol} opportunity - "
                f"{long_dex} (long side) not in available clients"
            )
            return False

        if short_dex not in strategy.exchange_clients:
            strategy.logger.warning(
                f"⚠️  SAFETY CHECK: Skipping {opportunity.symbol} opportunity - "
                f"{short_dex} (short side) not in available clients"
            )
            return False

        size_usd = strategy.config.default_position_size_usd
        if size_usd > strategy.config.max_position_size_usd:
            return False

        current_exposure = await self.calculate_total_exposure()
        if current_exposure + size_usd > strategy.config.max_total_exposure_usd:
            return False

        return True

    async def has_capacity(self) -> bool:
        strategy = self._strategy
        open_positions = await strategy.position_manager.get_open_positions()
        open_count = len(open_positions)

        if open_count >= strategy.config.max_positions:
            if not strategy._max_position_warning_logged:
                strategy.logger.info(
                    f"🚫 Max positions reached ({open_count}/{strategy.config.max_positions}). "
                    "Skipping new opportunities until capacity frees up."
                )
                strategy._max_position_warning_logged = True
            return False

        strategy._max_position_warning_logged = False

        return True

    async def calculate_total_exposure(self) -> Decimal:
        strategy = self._strategy
        positions = await strategy.position_manager.get_open_positions()
        return sum(p.size_usd for p in positions if p.status == "open")
