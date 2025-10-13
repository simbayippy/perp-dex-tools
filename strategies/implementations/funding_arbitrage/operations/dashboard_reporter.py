"""Dashboard helpers for the funding arbitrage strategy."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, List, Optional

from dashboard.models import (
    DashboardSnapshot,
    FundingSnapshot,
    LifecycleStage,
    PortfolioSnapshot,
    PositionLegSnapshot,
    PositionSnapshot,
    TimelineCategory,
    TimelineEvent,
)

if TYPE_CHECKING:
    from ..models import FundingArbPosition
    from ..strategy import FundingArbitrageStrategy


class DashboardReporter:
    """Encapsulates all dashboard-related interactions for the strategy."""

    def __init__(self, strategy: "FundingArbitrageStrategy") -> None:
        self._strategy = strategy

    # ------------------------------------------------------------------ #
    # Public helpers used by strategy and operations                     #
    # ------------------------------------------------------------------ #

    async def set_stage(
        self,
        stage: LifecycleStage,
        message: Optional[str] = None,
        *,
        category: TimelineCategory = TimelineCategory.STAGE,
    ) -> None:
        """Update the dashboard lifecycle stage and optionally emit an event."""
        service = getattr(self._strategy, "dashboard_service", None)
        if not service or not service.enabled:
            return

        current_stage = getattr(self._strategy, "_current_dashboard_stage", None)
        stage_changed = stage != current_stage
        if stage_changed:
            self._strategy._current_dashboard_stage = stage
            await service.update_session(lifecycle_stage=stage)

        if message and (stage_changed or category != TimelineCategory.STAGE):
            event = TimelineEvent(
                ts=datetime.now(timezone.utc),
                category=category,
                message=message,
                metadata={},
            )
            await service.publish_event(event)

    async def publish_snapshot(self, note: Optional[str] = None) -> None:
        """Publish the latest strategy snapshot to the dashboard."""
        service = getattr(self._strategy, "dashboard_service", None)
        if not service or not service.enabled:
            return

        positions = await self._strategy.position_manager.get_open_positions()
        snapshot = self._build_snapshot(positions)
        await service.publish_snapshot(snapshot)

        if note:
            event = TimelineEvent(
                ts=datetime.now(timezone.utc),
                category=TimelineCategory.INFO,
                message=note,
                metadata={},
            )
            await service.publish_event(event)

    async def position_opened(self, position: "FundingArbPosition") -> None:
        """Convenience wrapper for reporting a newly opened position."""
        await self.set_stage(
            LifecycleStage.MONITORING,
            f"Position opened {position.symbol}",
            category=TimelineCategory.EXECUTION,
        )
        await self.publish_snapshot(f"Position opened {position.symbol}")

    async def position_closing(self, position: "FundingArbPosition", reason: str) -> None:
        """Report that a position is about to close."""
        await self.set_stage(
            LifecycleStage.CLOSING,
            f"Closing {position.symbol} ({reason})",
            category=TimelineCategory.EXECUTION,
        )

    async def position_closed(self, position: "FundingArbPosition", reason: str) -> None:
        """Report a successfully closed position."""
        await self.publish_snapshot(f"Closed {position.symbol} ({reason})")

    # ------------------------------------------------------------------ #
    # Snapshot construction                                              #
    # ------------------------------------------------------------------ #

    def _build_snapshot(self, positions: List["FundingArbPosition"]) -> DashboardSnapshot:
        position_snapshots = [self._position_to_snapshot(p) for p in positions]

        total_notional = sum((p.size_usd for p in positions), start=Decimal("0"))
        net_unrealized = sum((p.get_net_pnl() for p in positions), start=Decimal("0"))
        funding_total = sum(
            (self._strategy.position_manager.get_cumulative_funding(p.id) for p in positions),
            start=Decimal("0"),
        )

        portfolio = PortfolioSnapshot(
            total_positions=len(positions),
            total_notional_usd=total_notional,
            net_unrealized_pnl=net_unrealized,
            net_realized_pnl=Decimal("0"),
            funding_accrued=funding_total,
            alerts=[],
        )

        funding_snapshot = FundingSnapshot(
            total_accrued=funding_total,
            weighted_average_rate=None,
            next_event_countdown_seconds=None,
            rates=[],
        )

        return DashboardSnapshot(
            session=self._strategy.dashboard_service.session_state,
            positions=position_snapshots,
            portfolio=portfolio,
            funding=funding_snapshot,
            recent_events=[],
            generated_at=datetime.now(timezone.utc),
        )

    def _position_to_snapshot(self, position: "FundingArbPosition") -> PositionSnapshot:
        legs_metadata = position.metadata.get("legs", {})
        leg_snapshots: List[PositionLegSnapshot] = []

        for venue, meta in legs_metadata.items():
            entry_price = meta.get("entry_price")
            if entry_price is not None and not isinstance(entry_price, Decimal):
                entry_price = Decimal(str(entry_price))

            quantity = meta.get("quantity")
            if quantity is not None and not isinstance(quantity, Decimal):
                quantity = Decimal(str(quantity))

            exposure = meta.get("exposure_usd", position.size_usd)
            if not isinstance(exposure, Decimal):
                exposure = Decimal(str(exposure))

            if quantity is None and entry_price and entry_price != 0:
                quantity = exposure / entry_price
            if quantity is None:
                quantity = Decimal("0")

            mark_price = meta.get("mark_price")
            if mark_price is not None and not isinstance(mark_price, Decimal):
                mark_price = Decimal(str(mark_price))

            leverage = meta.get("leverage")
            if leverage is not None and not isinstance(leverage, Decimal):
                leverage = Decimal(str(leverage))

            fees_paid = meta.get("fees_paid", Decimal("0"))
            if not isinstance(fees_paid, Decimal):
                fees_paid = Decimal(str(fees_paid))

            funding_accrued = meta.get("funding_accrued", Decimal("0"))
            if not isinstance(funding_accrued, Decimal):
                funding_accrued = Decimal(str(funding_accrued))

            realized_pnl = meta.get("realized_pnl", Decimal("0"))
            if not isinstance(realized_pnl, Decimal):
                realized_pnl = Decimal(str(realized_pnl))

            margin_reserved = meta.get("margin_reserved")
            if margin_reserved is not None and not isinstance(margin_reserved, Decimal):
                margin_reserved = Decimal(str(margin_reserved))

            updated_at = meta.get("last_updated", position.last_check or position.opened_at)
            if isinstance(updated_at, str):
                updated_at = datetime.fromisoformat(updated_at)

            leg_snapshots.append(
                PositionLegSnapshot(
                    venue=venue,
                    side=meta.get("side", "long"),
                    quantity=quantity,
                    exposure_usd=exposure,
                    entry_price=entry_price or Decimal("0"),
                    mark_price=mark_price,
                    leverage=leverage,
                    realized_pnl=realized_pnl,
                    fees_paid=fees_paid,
                    funding_accrued=funding_accrued,
                    margin_reserved=margin_reserved,
                    last_updated=updated_at,
                )
            )

        if not leg_snapshots:
            now = position.last_check or position.opened_at
            leg_snapshots = [
                PositionLegSnapshot(
                    venue=position.long_dex,
                    side="long",
                    quantity=Decimal("0"),
                    exposure_usd=position.size_usd,
                    entry_price=Decimal("0"),
                    last_updated=now,
                ),
                PositionLegSnapshot(
                    venue=position.short_dex,
                    side="short",
                    quantity=Decimal("0"),
                    exposure_usd=position.size_usd,
                    entry_price=Decimal("0"),
                    last_updated=now,
                ),
            ]

        erosion_ratio = position.get_profit_erosion()
        profit_erosion_pct = (Decimal("1") - erosion_ratio) * Decimal("100")

        funding_accrued = self._strategy.position_manager.get_cumulative_funding(position.id)
        lifecycle_stage = {
            "open": LifecycleStage.MONITORING,
            "pending_close": LifecycleStage.CLOSING,
            "closed": LifecycleStage.COMPLETE,
        }.get(position.status, LifecycleStage.MONITORING)

        last_update = position.last_check or position.opened_at

        return PositionSnapshot(
            position_id=position.id,
            symbol=position.symbol,
            strategy_tag="funding_arbitrage",
            opened_at=position.opened_at,
            last_update=last_update,
            lifecycle_stage=lifecycle_stage,
            legs=leg_snapshots,
            notional_exposure_usd=position.size_usd,
            entry_divergence_pct=position.entry_divergence,
            current_divergence_pct=position.current_divergence,
            profit_erosion_pct=profit_erosion_pct,
            unrealized_pnl=position.get_net_pnl(),
            realized_pnl=position.pnl_usd or Decimal("0"),
            funding_accrued=funding_accrued,
            rebalance_pending=position.rebalance_pending,
            max_position_age_seconds=int(self._strategy.config.risk_config.max_position_age_hours * 3600),
            custom_metadata={
                "rebalance_reason": position.rebalance_reason,
                "exit_reason": position.exit_reason,
            },
        )
