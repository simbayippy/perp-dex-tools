"""
Exposure verifier for atomic multi-order execution.

Verifies post-trade exposure by querying actual position snapshots from exchanges.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Dict, List, Optional

from helpers.unified_logger import get_core_logger

from ..contexts import OrderContext


class ExposureVerifier:
    """Verifies post-trade exposure by querying exchange position snapshots."""

    def __init__(self, logger=None):
        self.logger = logger or get_core_logger("exposure_verifier")

    async def verify_post_trade_exposure(
        self, contexts: List[OrderContext]
    ) -> Optional[Dict[str, Decimal]]:
        """
        Pull live position snapshots and detect any residual exposure.

        Args:
            contexts: List of order contexts to verify

        Returns:
            Dictionary with exposure metrics, or None if verification not possible
        """
        unique_keys = set()
        tasks = []

        for ctx in contexts:
            client = ctx.spec.exchange_client
            symbol = ctx.spec.symbol
            key = (id(client), symbol)
            if key in unique_keys:
                continue
            getter = getattr(client, "get_position_snapshot", None)
            if getter is None or not callable(getter):
                continue
            unique_keys.add(key)

            async def fetch_snapshot(exchange_client=client, sym=symbol):
                try:
                    snapshot = await exchange_client.get_position_snapshot(sym)
                except Exception as exc:  # pragma: no cover - defensive
                    self.logger.warning(
                        f"⚠️ [{exchange_client.get_exchange_name().upper()}] Position snapshot fetch failed for {sym}: {exc}"
                    )
                    return None
                return snapshot

            tasks.append(fetch_snapshot())

        if not tasks:
            return None

        snapshots = await asyncio.gather(*tasks, return_exceptions=True)

        total_long_qty = Decimal("0")
        total_short_qty = Decimal("0")
        total_long_usd = Decimal("0")
        total_short_usd = Decimal("0")

        for idx, snapshot in enumerate(snapshots):
            if isinstance(snapshot, Exception) or snapshot is None:
                continue
            quantity = snapshot.quantity or Decimal("0")
            exposure_usd = snapshot.exposure_usd
            mark_price = snapshot.mark_price or snapshot.entry_price

            abs_qty = quantity.copy_abs()
            if exposure_usd is None and mark_price is not None:
                exposure_usd = abs_qty * mark_price
            elif exposure_usd is None:
                exposure_usd = Decimal("0")

            if quantity > Decimal("0"):
                total_long_qty += abs_qty
                total_long_usd += exposure_usd or Decimal("0")
            elif quantity < Decimal("0"):
                total_short_qty += abs_qty
                total_short_usd += exposure_usd or Decimal("0")

        net_qty = (total_long_qty - total_short_qty).copy_abs()
        net_usd = (total_long_usd - total_short_usd).copy_abs()

        max_usd = max(total_long_usd, total_short_usd)
        net_pct = net_usd / max_usd if max_usd > Decimal("0") else Decimal("0")

        return {
            "net_qty": net_qty,
            "net_usd": net_usd,
            "net_pct": net_pct,
            "long_usd": total_long_usd,
            "short_usd": total_short_usd,
        }

