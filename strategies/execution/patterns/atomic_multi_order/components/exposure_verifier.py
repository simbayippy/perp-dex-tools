"""
Exposure verifier for atomic multi-order execution.

Verifies post-trade exposure using websocket-updated context data as PRIMARY source,
with exchange snapshots as fallback if websocket data is unavailable.

Websocket callbacks are the source of truth - they update OrderContext.filled_quantity
in real-time. Exchange snapshots are only used as fallback when websocket data is not available.

Exposure is verified purely on quantity (delta-neutrality), not USD values.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Dict, List, Optional

from helpers.unified_logger import get_core_logger

from ..contexts import OrderContext


class ExposureVerifier:
    """Verifies post-trade exposure using websocket-updated context data as primary source."""

    def __init__(self, logger=None):
        self.logger = logger or get_core_logger("exposure_verifier")

    async def _calculate_exposure_from_snapshots(
        self, contexts: List[OrderContext]
    ) -> Optional[Decimal]:
        """
        Calculate exposure from exchange snapshots (fallback when websocket data unavailable).
        
        Args:
            contexts: List of order contexts to verify
        
        Returns:
            Net quantity imbalance, or None if snapshot fetch fails
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
                except Exception as exc:
                    self.logger.warning(
                        f"⚠️ [{exchange_client.get_exchange_name().upper()}] Position snapshot fetch failed for {sym}: {exc}"
                    )
                    return None, None, None
                return snapshot, exchange_client, sym

            tasks.append(fetch_snapshot())

        if not tasks:
            return None

        snapshot_long_qty = Decimal("0")
        snapshot_short_qty = Decimal("0")
        
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, Exception) or result is None:
                continue
            
            snapshot, client, symbol = result
            if snapshot is None or client is None or symbol is None:
                continue
            
            quantity = snapshot.quantity or Decimal("0")

            # Normalize quantity to actual tokens using multiplier
            try:
                multiplier = Decimal(str(client.get_quantity_multiplier(symbol)))
            except Exception as exc:
                self.logger.warning(
                    f"Failed to get multiplier for {symbol} on "
                    f"{client.get_exchange_name()}: {exc}. Using 1."
                )
                multiplier = Decimal("1")
            
            actual_tokens = quantity.copy_abs() * multiplier

            # Use snapshot.side if available
            side = snapshot.side
            if side is None:
                if quantity > Decimal("0"):
                    side = "long"
                elif quantity < Decimal("0"):
                    side = "short"
            
            if side == "long":
                snapshot_long_qty += actual_tokens
            elif side == "short":
                snapshot_short_qty += actual_tokens
        
        net_qty = (snapshot_long_qty - snapshot_short_qty).copy_abs()
        return net_qty

    async def verify_post_trade_exposure(
        self, contexts: List[OrderContext]
    ) -> Optional[Dict[str, Decimal]]:
        """
        Verify post-trade exposure using websocket-updated context data as PRIMARY source.
        
        Websocket callbacks update OrderContext.filled_quantity in real-time, so we use
        context data as the source of truth. Exchange snapshots are used as fallback
        only when websocket data is unavailable.
        
        Exposure is verified purely on quantity (delta-neutrality), not USD values.
        
        Args:
            contexts: List of order contexts to verify (websocket-updated)
        
        Returns:
            Dictionary with net_qty (net quantity imbalance), or None if verification not possible
        """
        # PRIMARY: Calculate exposure from context data (websocket-updated, source of truth)
        context_long_qty = Decimal("0")
        context_short_qty = Decimal("0")
        has_context_data = False
        
        for ctx in contexts:
            if ctx.filled_quantity and ctx.filled_quantity > Decimal("0"):
                has_context_data = True
                # Get multiplier for this exchange/symbol
                try:
                    multiplier = Decimal(str(ctx.spec.exchange_client.get_quantity_multiplier(ctx.spec.symbol)))
                except Exception:
                    multiplier = Decimal("1")
                
                actual_tokens = ctx.filled_quantity.copy_abs() * multiplier
                
                if ctx.spec.side == "buy":
                    context_long_qty += actual_tokens
                elif ctx.spec.side == "sell":
                    context_short_qty += actual_tokens
        
        if has_context_data:
            # Use websocket-updated context data (source of truth)
            net_qty = (context_long_qty - context_short_qty).copy_abs()
            return {
                "net_qty": net_qty,
            }
        else:
            # FALLBACK: Websocket data not available, use exchange snapshots
            self.logger.debug(
                "Websocket data not available for exposure check, falling back to exchange snapshots"
            )
            net_qty = await self._calculate_exposure_from_snapshots(contexts)
            if net_qty is not None:
                return {
                    "net_qty": net_qty,
                }
            return None

