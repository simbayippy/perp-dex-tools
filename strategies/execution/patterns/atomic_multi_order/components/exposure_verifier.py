"""
Exposure verifier for atomic multi-order execution.

Verifies post-trade exposure using websocket-updated context data as PRIMARY source,
with exchange snapshots as validation/fallback.

Websocket callbacks are the source of truth - they update OrderContext.filled_quantity
in real-time. Exchange snapshots may lag behind fills, so we trust context data first.
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

    async def verify_post_trade_exposure(
        self, contexts: List[OrderContext]
    ) -> Optional[Dict[str, Decimal]]:
        """
        Verify post-trade exposure using websocket-updated context data as PRIMARY source.
        
        Websocket callbacks update OrderContext.filled_quantity in real-time, so we use
        context data as the source of truth. Exchange snapshots are used for validation
        and to detect discrepancies (which may indicate timing issues or exchange bugs).
        
        Args:
            contexts: List of order contexts to verify (websocket-updated)
        
        Returns:
            Dictionary with exposure metrics, or None if verification not possible
        """
        # PRIMARY SOURCE: Calculate exposure from context data (websocket-updated)
        context_long_qty = Decimal("0")
        context_short_qty = Decimal("0")
        context_long_usd = Decimal("0")
        context_short_usd = Decimal("0")
        
        # Map to track (exchange, symbol) pairs for USD calculation
        exchange_symbol_map: Dict[tuple, List[OrderContext]] = {}
        
        for ctx in contexts:
            if ctx.filled_quantity and ctx.filled_quantity > Decimal("0"):
                # Get multiplier for this exchange/symbol
                try:
                    multiplier = Decimal(str(ctx.spec.exchange_client.get_quantity_multiplier(ctx.spec.symbol)))
                except Exception:
                    multiplier = Decimal("1")
                
                actual_tokens = ctx.filled_quantity.copy_abs() * multiplier
                
                # Track by exchange/symbol for USD calculation
                key = (id(ctx.spec.exchange_client), ctx.spec.symbol)
                if key not in exchange_symbol_map:
                    exchange_symbol_map[key] = []
                exchange_symbol_map[key].append(ctx)
                
                if ctx.spec.side == "buy":
                    context_long_qty += actual_tokens
                    # USD will be calculated from snapshots below
                elif ctx.spec.side == "sell":
                    context_short_qty += actual_tokens
                    # USD will be calculated from snapshots below
        
        # SECONDARY SOURCE: Fetch exchange snapshots for USD values and validation
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
                    return None, None, None
                return snapshot, exchange_client, sym

            tasks.append(fetch_snapshot())

        snapshot_long_qty = Decimal("0")
        snapshot_short_qty = Decimal("0")
        snapshot_long_usd = Decimal("0")
        snapshot_short_usd = Decimal("0")
        
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for result in results:
                if isinstance(result, Exception) or result is None:
                    continue
                
                snapshot, client, symbol = result
                if snapshot is None or client is None or symbol is None:
                    continue
                
                quantity = snapshot.quantity or Decimal("0")
                exposure_usd = snapshot.exposure_usd

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
                    snapshot_long_usd += exposure_usd or Decimal("0")
                elif side == "short":
                    snapshot_short_qty += actual_tokens
                    snapshot_short_usd += exposure_usd or Decimal("0")
        
        # Use context data (websocket-updated) as PRIMARY source for quantities
        # Use snapshot data for USD values (context doesn't track USD per exchange)
        total_long_qty = context_long_qty  # PRIMARY: websocket-updated
        total_short_qty = context_short_qty  # PRIMARY: websocket-updated
        
        # For USD, prefer snapshot if available, otherwise estimate from context
        if snapshot_long_usd > Decimal("0") or snapshot_short_usd > Decimal("0"):
            total_long_usd = snapshot_long_usd
            total_short_usd = snapshot_short_usd
        else:
            # Fallback: estimate USD from context fills (less accurate)
            # This is a fallback - snapshot is preferred for USD
            for key, ctx_list in exchange_symbol_map.items():
                for ctx in ctx_list:
                    if ctx.filled_quantity > Decimal("0") and ctx.result:
                        fill_price = ctx.result.get("fill_price")
                        if fill_price:
                            multiplier = Decimal(str(ctx.spec.exchange_client.get_quantity_multiplier(ctx.spec.symbol)))
                            actual_tokens = ctx.filled_quantity.copy_abs() * multiplier
                            usd_value = actual_tokens * fill_price
                            if ctx.spec.side == "buy":
                                context_long_usd += usd_value
                            elif ctx.spec.side == "sell":
                                context_short_usd += usd_value
            total_long_usd = context_long_usd
            total_short_usd = context_short_usd
        
        # VALIDATION: Compare context (websocket) vs snapshot (REST API)
        # If they differ significantly, log warning (may indicate timing issue or exchange bug)
        context_net_qty = (context_long_qty - context_short_qty).copy_abs()
        snapshot_net_qty = (snapshot_long_qty - snapshot_short_qty).copy_abs()
        
        if abs(context_net_qty - snapshot_net_qty) > Decimal("0.01"):  # More than 0.01 token difference
            self.logger.debug(
                f"⚠️ Exposure check discrepancy: Context (websocket) shows net_qty={context_net_qty:.6f}, "
                f"snapshot (REST) shows net_qty={snapshot_net_qty:.6f}. "
                f"Using context data (websocket is source of truth)."
            )

        # Calculate net exposure using PRIMARY source (context/websocket data)
        net_qty = context_net_qty  # PRIMARY: websocket-updated context
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

