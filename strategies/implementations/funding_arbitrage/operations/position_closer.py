"""Helpers for evaluating and closing funding arbitrage positions."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, List, Optional, Tuple

if TYPE_CHECKING:
    from ..models import FundingArbPosition
    from ..strategy import FundingArbitrageStrategy


class PositionCloser:
    """Encapsulates exit-condition evaluation and close execution."""

    def __init__(self, strategy: "FundingArbitrageStrategy") -> None:
        self._strategy = strategy

    async def evaluate(self) -> List[str]:
        strategy = self._strategy
        actions: List[str] = []
        positions = await strategy.position_manager.get_open_positions()

        for position in positions:
            should_close, reason = self.should_close(position)
            if should_close:
                await self.close(position, reason)
                actions.append(f"Closed {position.symbol}: {reason}")

        return actions

    def should_close(self, position: "FundingArbPosition") -> Tuple[bool, Optional[str]]:
        strategy = self._strategy

        if position.current_divergence and position.current_divergence < 0:
            return True, "FUNDING_FLIP"

        erosion = position.get_profit_erosion()
        if erosion < strategy.config.risk_config.min_erosion_threshold:
            return True, "PROFIT_EROSION"

        if position.get_age_hours() > strategy.config.risk_config.max_position_age_hours:
            return True, "TIME_LIMIT"

        if strategy.config.risk_config.enable_better_opportunity:
            # Placeholder for future best-opportunity detection
            pass

        return False, None

    async def close(self, position: "FundingArbPosition", reason: str) -> None:
        strategy = self._strategy

        try:
            await strategy.dashboard.position_closing(position, reason)

            long_client = strategy.exchange_clients[position.long_dex]
            short_client = strategy.exchange_clients[position.short_dex]

            await long_client.close_position(position.symbol)
            await short_client.close_position(position.symbol)

            pnl = position.get_net_pnl()
            pnl_pct = position.get_net_pnl_pct()

            position.status = "closed"
            position.exit_reason = reason
            position.closed_at = datetime.now()
            await strategy.position_manager.update_position(position)

            strategy.logger.log(
                f"✅ Closed {position.symbol} ({reason}): "
                f"PnL=${pnl:.2f} ({pnl_pct*100:.2f}%), "
                f"Age={position.get_age_hours():.1f}h",
                "INFO",
            )

            await strategy.dashboard.position_closed(position, reason)

        except Exception as exc:  # pragma: no cover - defensive logging
            strategy.logger.log(
                f"Error closing position {position.id}: {exc}",
                "ERROR",
            )
            raise
