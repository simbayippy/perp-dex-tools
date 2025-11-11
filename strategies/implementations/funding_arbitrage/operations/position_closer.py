"""Helpers for evaluating and closing funding arbitrage positions."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from exchange_clients.events import LiquidationEvent
from exchange_clients.base_client import BaseExchangeClient
from ..risk_management import get_risk_manager
from strategies.execution.core.order_executor import OrderExecutor, ExecutionMode
from strategies.execution.patterns.atomic_multi_order import OrderSpec

if TYPE_CHECKING:
    from exchange_clients.base_models import ExchangePositionSnapshot
    from ..models import FundingArbPosition
    from ..strategy import FundingArbitrageStrategy


class PositionCloser:
    """Encapsulates exit-condition evaluation and close execution."""

    _ZERO_TOLERANCE = Decimal("0")
    _IMBALANCE_THRESHOLD = Decimal("0.05")  # 5% maximum allowed difference

    @staticmethod
    def _to_decimal(value: Any) -> Decimal:
        """Convert value to Decimal safely, handling None, float, int, and Decimal."""
        if value is None:
            return Decimal("0")
        if isinstance(value, Decimal):
            return value
        try:
            return Decimal(str(value))
        except (ValueError, TypeError, Exception):
            return Decimal("0")

    def __init__(self, strategy: "FundingArbitrageStrategy") -> None:
        self._strategy = strategy
        self._risk_manager = self._build_risk_manager()
        self._order_executor = OrderExecutor(price_provider=strategy.price_provider)
        self._ws_prepared: Dict[str, str] = {}

    async def evaluateAndClosePositions(self) -> List[str]:
        strategy = self._strategy
        actions: List[str] = []
        positions = await strategy.position_manager.get_open_positions()

        for position in positions:
            snapshots = await self._fetch_leg_snapshots(position)

            liquidation_reason = self._detect_liquidation(position, snapshots)
            if liquidation_reason is not None:
                await self.close(position, liquidation_reason, live_snapshots=snapshots)
                strategy.logger.warning(
                    f"Closed {position.symbol}: {liquidation_reason}"
                )
                actions.append(f"Closed {position.symbol}: {liquidation_reason}")
                continue

            imbalance_reason = self._detect_imbalance(position, snapshots)
            if imbalance_reason is not None:
                await self.close(position, imbalance_reason, live_snapshots=snapshots)
                strategy.logger.warning(
                    f"Closed {position.symbol}: {imbalance_reason}"
                )
                actions.append(f"Closed {position.symbol}: {imbalance_reason}")
                continue

            should_close, reason = await self._should_close(position, snapshots)
            if should_close:
                await self.close(position, reason or "UNKNOWN", live_snapshots=snapshots)
                strategy.logger.info(f"Closed {position.symbol}: {reason}")
                actions.append(f"Closed {position.symbol}: {reason}")
            else:
                strategy.logger.debug(
                    f"Position {position.symbol} not closing: {reason}"
                )

        return actions

    async def handle_liquidation_event(self, event: LiquidationEvent) -> None:
        """
        React to liquidation notifications by immediately unwinding impacted positions.
        """
        strategy = self._strategy
        positions = await strategy.position_manager.get_open_positions()

        for position in positions:
            if not self._symbols_match(position.symbol, event.symbol):
                continue

            if event.exchange not in {position.long_dex, position.short_dex}:
                continue

            strategy.logger.error(
                f"🚨 Liquidation event detected on {event.exchange.upper()} for {event.symbol} "
                f"(side={event.side}, qty={event.quantity}, price={event.price})."
            )

            snapshots = await self._fetch_leg_snapshots(position)
            reason = f"LIQUIDATION_{event.exchange.upper()}"
            await self.close(position, reason, live_snapshots=snapshots)

    async def _should_close(
        self,
        position: "FundingArbPosition",
        snapshots: Dict[str, Optional["ExchangePositionSnapshot"]],
    ) -> Tuple[bool, Optional[str]]:
        strategy = self._strategy
        age_hours = position.get_age_hours()
        min_hold_hours = getattr(strategy.config.risk_config, "min_hold_hours", 0) or 0

        # Defer non-critical exits until the minimum hold window expires
        if min_hold_hours > 0 and age_hours < min_hold_hours:
            return False, "MIN_HOLD_ACTIVE"

        current_rates = await self._gather_current_rates(position)
        if current_rates is not None and self._risk_manager is not None:
            try:
                should_exit, reason = self._risk_manager.should_exit(
                    position, current_rates
                )
                if should_exit:
                    if await self._should_skip_erosion_exit(position, reason):
                        return False, "HOLD_TOP_OPPORTUNITY"
                    return True, reason
            except Exception as exc:  # pragma: no cover - defensive logging
                strategy.logger.error(
                    f"Risk manager evaluation failed for {position.symbol}: {exc}"
                )

        # Fallback heuristics if risk manager unavailable or declined
        if position.current_divergence and position.current_divergence < 0:
            return True, "DIVERGENCE_FLIPPED"

        erosion = position.get_profit_erosion()
        if erosion < strategy.config.risk_config.min_erosion_threshold:
            if await self._should_skip_erosion_exit(position, "PROFIT_EROSION"):
                return False, "HOLD_TOP_OPPORTUNITY"
            return True, "PROFIT_EROSION"

        if age_hours > strategy.config.risk_config.max_position_age_hours:
            return True, "TIME_LIMIT"

        return False, None

    async def close(
        self,
        position: "FundingArbPosition",
        reason: str,
        *,
        live_snapshots: Optional[
            Dict[str, Optional["ExchangePositionSnapshot"]]
        ] = None,
        order_type: Optional[str] = None,
    ) -> None:
        strategy = self._strategy

        try:
            # Capture realized PnL BEFORE closing (while positions are still open)
            # Position snapshots only work for OPEN positions, not closed ones
            pre_close_snapshots = live_snapshots or await self._fetch_leg_snapshots(position)
            
            # Calculate PnL from exchange snapshots BEFORE closing
            # Exchange realized_pnl includes cumulative realized PnL (price movement + funding)
            total_realized_pnl = Decimal("0")
            for dex in [position.long_dex, position.short_dex]:
                snapshot = pre_close_snapshots.get(dex) or pre_close_snapshots.get(dex.lower())
                if snapshot and snapshot.realized_pnl is not None:
                    realized_pnl_decimal = self._to_decimal(snapshot.realized_pnl)
                    total_realized_pnl += realized_pnl_decimal
                    strategy.logger.debug(
                        f"[{dex}] Pre-close Realized PnL: ${realized_pnl_decimal:.2f}"
                    )
            
            # Close positions on exchanges
            await self._close_exchange_positions(
                position,
                reason=reason,
                live_snapshots=pre_close_snapshots,
                order_type=order_type,
            )

            # Wait a moment for exchanges to process the close
            await asyncio.sleep(1.0)

            # Try to get updated realized PnL after closing (some exchanges update it immediately)
            # But don't rely on this - we already have it from pre-close snapshots
            post_close_snapshots = await self._fetch_leg_snapshots(position)
            post_close_realized_pnl = Decimal("0")
            for dex in [position.long_dex, position.short_dex]:
                snapshot = post_close_snapshots.get(dex) or post_close_snapshots.get(dex.lower())
                if snapshot and snapshot.realized_pnl is not None:
                    realized_pnl_decimal = self._to_decimal(snapshot.realized_pnl)
                    post_close_realized_pnl += realized_pnl_decimal
            
            # Use post-close PnL if available and different (exchange updated it), otherwise use pre-close
            if post_close_realized_pnl != 0 and post_close_realized_pnl != total_realized_pnl:
                old_pnl = total_realized_pnl
                total_realized_pnl = post_close_realized_pnl
                strategy.logger.debug(
                    f"Using post-close realized PnL: ${total_realized_pnl:.2f} "
                    f"(pre-close was ${old_pnl:.2f})"
                )
            
            # If we couldn't get realized PnL from snapshots, use execution result from websocket fills
            if total_realized_pnl == 0:
                # Check if we have close execution result stored in metadata
                # This contains fill prices from websocket updates (atomic executor waits for fills)
                close_result = position.metadata.get("close_execution_result")
                if close_result and close_result.get("filled_orders"):
                    strategy.logger.debug(
                        f"Using close execution result (websocket fills) for PnL: {position.symbol}"
                    )
                    # Get cumulative funding from database (includes all funding payments)
                    cumulative_funding = await strategy.position_manager.get_cumulative_funding(position.id)
                    position.cumulative_funding = cumulative_funding
                    
                    # Calculate price PnL from fill prices vs entry prices
                    # Get entry prices from position metadata
                    legs_metadata = position.metadata.get("legs", {})
                    price_pnl = Decimal("0")
                    
                    for fill_info in close_result["filled_orders"]:
                        dex = fill_info["dex"]
                        fill_price = fill_info.get("fill_price")
                        filled_qty = fill_info.get("filled_quantity")
                        
                        if fill_price and filled_qty:
                            leg_meta = legs_metadata.get(dex, {})
                            entry_price = leg_meta.get("entry_price")
                            
                            if entry_price:
                                # Convert to Decimal to avoid type mismatch errors
                                fill_price_decimal = self._to_decimal(fill_price)
                                entry_price_decimal = self._to_decimal(entry_price)
                                filled_qty_decimal = self._to_decimal(filled_qty)
                                
                                # Calculate PnL for this leg
                                # For long: PnL = (exit_price - entry_price) * quantity
                                # For short: PnL = (entry_price - exit_price) * quantity
                                side = leg_meta.get("side", "long")
                                if side == "long":
                                    leg_pnl = (fill_price_decimal - entry_price_decimal) * filled_qty_decimal
                                else:  # short
                                    leg_pnl = (entry_price_decimal - fill_price_decimal) * filled_qty_decimal
                                price_pnl += leg_pnl
                                strategy.logger.debug(
                                    f"[{dex}] Price PnL from websocket fills: ${leg_pnl:.2f} "
                                    f"(entry=${entry_price_decimal:.6f}, exit=${fill_price_decimal:.6f}, qty={filled_qty_decimal})"
                                )
                    
                    # Total PnL = price movement + funding - fees
                    # Ensure all values are Decimal before arithmetic
                    cumulative_funding_decimal = self._to_decimal(cumulative_funding)
                    total_fees_decimal = self._to_decimal(position.total_fees_paid)
                    pnl = price_pnl + cumulative_funding_decimal - total_fees_decimal
                    strategy.logger.debug(
                        f"Calculated PnL from websocket fills: price=${price_pnl:.2f}, "
                        f"funding=${cumulative_funding:.2f}, fees=${position.total_fees_paid:.2f}, "
                        f"total=${pnl:.2f}"
                    )
                else:
                    # Fall back to cumulative funding method
                    strategy.logger.warning(
                        f"Could not get realized PnL from exchanges for {position.symbol}, "
                        f"falling back to cumulative funding"
                    )
                    cumulative_funding = await strategy.position_manager.get_cumulative_funding(position.id)
                    position.cumulative_funding = cumulative_funding
                    pnl = position.get_net_pnl()
            else:
                # Use exchange realized PnL from snapshots, subtract our tracked fees
                # Note: Exchange realized_pnl already includes funding, so we don't add cumulative_funding
                # Ensure both values are Decimal before arithmetic
                total_fees_decimal = self._to_decimal(position.total_fees_paid)
                pnl = total_realized_pnl - total_fees_decimal
                strategy.logger.debug(
                    f"Using exchange realized PnL from snapshots: ${total_realized_pnl:.2f}, "
                    f"fees=${position.total_fees_paid:.2f}, net=${pnl:.2f}"
                )
            
            if position.size_usd and position.size_usd > Decimal("0"):
                try:
                    pnl_pct = pnl / position.size_usd
                except Exception:
                    # Defensive fallback if size_usd is corrupted or zero-like
                    pnl_pct = Decimal("0")
            else:
                pnl_pct = Decimal("0")

            await strategy.position_manager.close(
                position.id,
                exit_reason=reason,
                pnl_usd=pnl,
            )

            refreshed = await strategy.position_manager.get(position.id)
            if refreshed:
                position = refreshed

            strategy.logger.info(
                f"✅ Closed {position.symbol} ({reason}): "
                f"PnL=${pnl:.2f} ({pnl_pct*100:.2f}%), "
                f"Age={position.get_age_hours():.1f}h"
            )
            
            # Send Telegram notification
            try:
                await strategy.notification_service.notify_position_closed(
                    symbol=position.symbol,
                    reason=reason,
                    pnl_usd=pnl,
                    pnl_pct=pnl_pct,
                    age_hours=position.get_age_hours(),
                    size_usd=position.size_usd,
                )
            except Exception as exc:
                # Don't fail position closing if notification fails
                strategy.logger.warning(f"Failed to send position closed notification: {exc}")

        except Exception as exc:  # pragma: no cover - defensive logging
            strategy.logger.error(
                f"Error closing position {position.id}: {exc}"
            )
            raise

    def _build_risk_manager(self):
        strategy = self._strategy
        risk_cfg = strategy.config.risk_config

        try:
            config_payload = {
                "min_erosion_ratio": float(risk_cfg.min_erosion_threshold),
                "severe_erosion_ratio": float(
                    getattr(risk_cfg, "severe_erosion_ratio", Decimal("0.2"))
                ),
                "max_position_age_hours": risk_cfg.max_position_age_hours,
                "flip_margin": float(getattr(risk_cfg, "flip_margin", Decimal("0"))),
            }
            return get_risk_manager(risk_cfg.strategy, config_payload)
        except Exception as exc:
            strategy.logger.error(
                f"Failed to initialize risk manager '{risk_cfg.strategy}': {exc}"
            )
            return None

    async def _gather_current_rates(
        self, position: "FundingArbPosition"
    ) -> Optional[Dict[str, Decimal]]:
        """
        Fetch latest funding rates for both legs.
        """
        repo = getattr(self._strategy, "funding_rate_repo", None)
        if repo is None:
            return None

        try:
            long_rate_row = await repo.get_latest_specific(
                position.long_dex, position.symbol
            )
            short_rate_row = await repo.get_latest_specific(
                position.short_dex, position.symbol
            )
        except Exception as exc:
            self._strategy.logger.error(
                f"Failed to fetch funding rates for {position.symbol}: {exc}"
            )
            return None

        if not long_rate_row or not short_rate_row:
            return None

        def _extract(row, key: str) -> Optional[Decimal]:
            value = None
            if isinstance(row, dict):
                value = row.get(key)
            elif hasattr(row, "_mapping"):
                value = row._mapping.get(key)
            else:
                value = getattr(row, key, None)
            if value is None:
                return None
            try:
                return Decimal(str(value))
            except Exception:
                return None

        long_rate = _extract(long_rate_row, "funding_rate")
        short_rate = _extract(short_rate_row, "funding_rate")
        if long_rate is None or short_rate is None:
            self._strategy.logger.warning(
                f"Funding rate data missing for {position.symbol}: "
                f"long={long_rate_row}, short={short_rate_row}"
            )
            return None
        divergence = short_rate - long_rate
        position.current_divergence = divergence

        return {
            "divergence": divergence,
            "long_rate": long_rate,
            "short_rate": short_rate,
            "long_oi_usd": _extract(long_rate_row, "open_interest_usd") or Decimal("0"),
            "short_oi_usd": _extract(short_rate_row, "open_interest_usd") or Decimal("0"),
        }

    async def _fetch_leg_snapshots(
        self, position: "FundingArbPosition"
    ) -> Dict[str, Optional["ExchangePositionSnapshot"]]:
        """Fetch up-to-date exchange snapshots for both legs."""
        snapshots: Dict[str, Optional["ExchangePositionSnapshot"]] = {}

        legs_metadata = (position.metadata or {}).get("legs", {})

        for dex in filter(None, [position.long_dex, position.short_dex]):
            client = self._strategy.exchange_clients.get(dex)
            if client is None:
                self._strategy.logger.error(
                    f"No exchange client for {dex} while evaluating {position.symbol}"
                )
                snapshots[dex] = None
                continue

            leg_metadata = legs_metadata.get(dex, {}) if isinstance(legs_metadata, dict) else {}
            await self._prepare_contract_context(
                client,
                position.symbol,
                metadata=leg_metadata,
                contract_hint=leg_metadata.get("market_id"),
            )
            await self._ensure_market_feed_once(client, position.symbol)

            try:
                # THIS IS PROBABLY PROBLAMATIC (or the risk_controlelr) -> SPAMS GETTING POSITION SNAPSHOTS
                snapshots[dex] = await client.get_position_snapshot(position.symbol)
            except Exception as exc:  # pragma: no cover - defensive logging
                self._strategy.logger.error(
                    f"[{dex}] Failed to fetch position snapshot for {position.symbol}: {exc}"
                )
                snapshots[dex] = None

        return snapshots

    async def _ensure_market_feed_once(self, client, symbol: str) -> None:
        """
        Prepare the client's websocket feed for the target symbol once per session run.
        """
        exchange_name = client.get_exchange_name().upper()
        symbol_key = symbol.upper()
        previous_symbol = self._ws_prepared.get(exchange_name)
        should_prepare = previous_symbol != symbol_key

        ws_manager = getattr(client, "ws_manager", None)
        if not should_prepare and ws_manager is not None:
            ws_symbol = getattr(ws_manager, "symbol", None)
            if isinstance(ws_symbol, str):
                should_prepare = ws_symbol.upper() != symbol_key

        try:
            if should_prepare:
                await client.ensure_market_feed(symbol)
                # Note: ensure_market_feed now waits for book ticker to be ready
        except Exception as exc:  # pragma: no cover - defensive logging
            self._strategy.logger.debug(
                f"⚠️ [{exchange_name}] WebSocket prep error during close: {exc}"
            )
        else:
            self._ws_prepared[exchange_name] = symbol_key

    def _detect_liquidation(
        self,
        position: "FundingArbPosition",
        snapshots: Dict[str, Optional["ExchangePositionSnapshot"]],
    ) -> Optional[str]:
        """Detect if either leg has been liquidated or otherwise removed."""
        missing_legs = [
            dex
            for dex, snapshot in snapshots.items()
            if not self._has_open_position(snapshot)
        ]

        if not missing_legs:
            return None

        # Only flag liquidation if at least one leg is still open (directional exposure)
        active_legs = [
            dex
            for dex, snapshot in snapshots.items()
            if self._has_open_position(snapshot)
        ]

        if not active_legs and len(missing_legs) == len(snapshots):
            return "ALL_LEGS_CLOSED"

        leg_list = ", ".join(sorted(missing_legs))
        self._strategy.logger.warning(
            f"⚠️ Detected missing legs {leg_list} for {position.symbol}; initiating emergency close."
        )
        return "LEG_LIQUIDATED"

    def _detect_imbalance(
        self,
        position: "FundingArbPosition",
        snapshots: Dict[str, Optional["ExchangePositionSnapshot"]],
    ) -> Optional[str]:
        """
        Detect severe imbalance between legs (> 5% difference).
        
        Checks if one leg's quantity is significantly different from the other,
        indicating a delta-neutral hedge has become unbalanced (e.g., 72k tokens vs 1k tokens).
        
        Quantities are normalized to actual token amounts to handle exchange multipliers:
        - Lighter 104 units (×1000) = 104,000 tokens
        - Aster 104,000 units (×1) = 104,000 tokens
        
        Args:
            position: Position to check
            snapshots: Live snapshots from exchanges
            
        Returns:
            "SEVERE_IMBALANCE" if imbalance detected, None otherwise
        """
        long_tokens, short_tokens = self._extract_leg_quantities(position, snapshots)
        
        # If we can't extract quantities, skip check (handled by _detect_liquidation)
        if long_tokens is None or short_tokens is None:
            return None
        
        # If either leg is zero, skip (handled by _detect_liquidation)
        if long_tokens <= self._ZERO_TOLERANCE or short_tokens <= self._ZERO_TOLERANCE:
            return None
        
        # Calculate percentage difference: (max - min) / max
        min_tokens = min(long_tokens, short_tokens)
        max_tokens = max(long_tokens, short_tokens)
        diff_pct = (max_tokens - min_tokens) / max_tokens
        
        if diff_pct > self._IMBALANCE_THRESHOLD:
            self._strategy.logger.warning(
                f"⚠️ Severe imbalance detected for {position.symbol}: "
                f"{position.long_dex}={long_tokens:.0f} tokens vs {position.short_dex}={short_tokens:.0f} tokens "
                f"(diff={diff_pct*100:.1f}%, threshold={self._IMBALANCE_THRESHOLD*100:.1f}%)"
            )
            return "SEVERE_IMBALANCE"
        
        return None

    def _extract_leg_quantities(
        self,
        position: "FundingArbPosition",
        snapshots: Dict[str, Optional["ExchangePositionSnapshot"]],
    ) -> Tuple[Optional[Decimal], Optional[Decimal]]:
        """
        Extract absolute quantities for both legs from snapshots.
        
        Converts exchange-specific units to actual token amounts for accurate comparison.
        Example: Lighter's 104 units (×1000 multiplier) = 104,000 tokens
                 Aster's 104,000 units (×1 multiplier) = 104,000 tokens
        
        Args:
            position: Position being evaluated
            snapshots: Exchange snapshots
            
        Returns:
            Tuple of (long_actual_tokens, short_actual_tokens) or (None, None) if unavailable
        """
        long_snapshot = snapshots.get(position.long_dex)
        short_snapshot = snapshots.get(position.short_dex)
        
        if not long_snapshot or not short_snapshot:
            return None, None
        
        if long_snapshot.quantity is None or short_snapshot.quantity is None:
            return None, None
        
        # Get exchange-specific quantities (in their own units)
        long_qty_exchange = long_snapshot.quantity.copy_abs()
        short_qty_exchange = short_snapshot.quantity.copy_abs()
        
        # Get the exchange clients and their quantity multipliers
        long_client = self._strategy.exchange_clients.get(position.long_dex)
        short_client = self._strategy.exchange_clients.get(position.short_dex)
        
        if not long_client or not short_client:
            return None, None
        
        # Get multipliers (e.g., Lighter 1000TOSHI = 1000x, Aster TOSHI = 1x)
        long_multiplier = Decimal(str(long_client.get_quantity_multiplier(position.symbol)))
        short_multiplier = Decimal(str(short_client.get_quantity_multiplier(position.symbol)))
        
        # Convert to actual token amounts for fair comparison
        long_actual_tokens = long_qty_exchange * long_multiplier
        short_actual_tokens = short_qty_exchange * short_multiplier
        
        return long_actual_tokens, short_actual_tokens

    @classmethod
    def _has_open_position(cls, snapshot: Optional["ExchangePositionSnapshot"]) -> bool:
        if snapshot is None or snapshot.quantity is None:
            return False
        return snapshot.quantity.copy_abs() > cls._ZERO_TOLERANCE

    async def _should_skip_erosion_exit(
        self,
        position: "FundingArbPosition",
        trigger_reason: Optional[str],
    ) -> bool:
        """
        Guard against closing/re-opening the same opportunity when erosion triggers.
        """
        if trigger_reason != "PROFIT_EROSION":
            return False

        strategy = self._strategy
        opportunity_finder = getattr(strategy, "opportunity_finder", None)
        if opportunity_finder is None:
            return False

        try:
            from funding_rate_service.models.filters import OpportunityFilter
        except Exception:
            return False

        available_exchanges = list(strategy.exchange_clients.keys())
        whitelist_dexes = [dex.lower() for dex in available_exchanges] if available_exchanges else None
        required_dex = getattr(strategy.config, "mandatory_exchange", None)
        if not required_dex:
            required_dex = getattr(strategy.config, "primary_exchange", None)
        if isinstance(required_dex, str) and required_dex.strip():
            required_dex = required_dex.strip().lower()
        else:
            required_dex = None

        max_oi_cap = strategy.config.max_oi_usd if required_dex else None

        filters = OpportunityFilter(
            min_profit_percent=strategy.config.min_profit,
            max_oi_usd=max_oi_cap,
            whitelist_dexes=whitelist_dexes,
            required_dex=required_dex,
            symbol=None,
            limit=1,
        )

        try:
            opportunities = await opportunity_finder.find_opportunities(filters)
        except Exception as exc:
            strategy.logger.error(
                f"Failed to score opportunities while checking erosion guard for "
                f"{position.symbol}: {exc}"
            )
            return False

        if not opportunities:
            return False

        best = opportunities[0]
        try:
            net_profit = best.net_profit_percent
        except AttributeError:
            net_profit = None

        if (
            best
            and self._symbols_match(position.symbol, best.symbol)
            and best.long_dex.lower() == position.long_dex.lower()
            and best.short_dex.lower() == position.short_dex.lower()
            and net_profit is not None
            and net_profit >= strategy.config.min_profit
        ):
            try:
                net_display = net_profit * Decimal("100")
            except Exception:
                net_display = net_profit

            strategy.logger.info(
                f"Holding {position.symbol}: erosion trigger fired but opportunity "
                f"still ranks highest ({net_display}% net)."
            )
            return True

        return False

    async def _close_exchange_positions(
        self,
        position: "FundingArbPosition",
        *,
        reason: str = "UNKNOWN",
        live_snapshots: Optional[
            Dict[str, Optional["ExchangePositionSnapshot"]]
        ] = None,
        order_type: Optional[str] = None,
    ) -> None:
        """
        Close legs on the exchanges, skipping those already flat.
        
        Args:
            position: Position to close
            reason: Reason for closing
            live_snapshots: Optional pre-fetched snapshots
            order_type: Optional order type override ("market" or "limit")
        """
        strategy = self._strategy
        legs: List[Dict[str, Any]] = []
        live_snapshots = live_snapshots or {}

        position_legs = (position.metadata or {}).get("legs", {})

        for dex in filter(None, [position.long_dex, position.short_dex]):
            client = strategy.exchange_clients.get(dex)
            if client is None:
                strategy.logger.error(
                    f"Skipping close for {dex}: no exchange client available"
                )
                continue

            leg_hint = position_legs.get(dex, {}) if isinstance(position_legs, dict) else {}
            await self._prepare_contract_context(
                client,
                position.symbol,
                metadata=leg_hint,
                contract_hint=leg_hint.get("market_id"),
            )
            await self._ensure_market_feed_once(client, position.symbol)

            snapshot = live_snapshots.get(dex) or live_snapshots.get(dex.lower())
            if snapshot is None:
                try:
                    snapshot = await client.get_position_snapshot(position.symbol)
                except Exception as exc:
                    strategy.logger.error(
                        f"[{dex}] Failed to fetch position snapshot for close: {exc}"
                    )
                    continue

            if not self._has_open_position(snapshot):
                strategy.logger.debug(
                    f"[{dex}] No open position detected for {position.symbol}; skipping close call."
                )
                continue

            quantity = snapshot.quantity.copy_abs() if snapshot.quantity is not None else Decimal("0")
            if quantity <= self._ZERO_TOLERANCE:
                strategy.logger.debug(
                    f"[{dex}] Snapshot quantity zero for {position.symbol}; skipping."
                )
                continue

            # Determine side: use snapshot.side if available, otherwise infer from quantity sign
            # Note: Some exchanges (e.g., Paradex) store short positions as positive quantities,
            # so we must rely on snapshot.side rather than quantity sign
            if snapshot.side:
                # Use explicit side from snapshot (most reliable)
                side = "sell" if snapshot.side == "long" else "buy"
            else:
                # Fallback: infer from quantity sign (negative = short = need to buy)
                side = "sell" if snapshot.quantity > 0 else "buy"
            metadata: Dict[str, Any] = getattr(snapshot, "metadata", {}) or {}

            if metadata:
                await self._prepare_contract_context(
                    client,
                    position.symbol,
                    metadata=metadata,
                    contract_hint=metadata.get("market_id"),
                )

            contract_id = await self._prepare_contract_context(
                client,
                position.symbol,
                metadata=metadata,
                contract_hint=metadata.get("market_id"),
            )
            legs.append(
                {
                    "dex": dex,
                    "client": client,
                    "snapshot": snapshot,
                    "side": side,
                    "quantity": quantity,
                    "contract_id": contract_id,
                    "metadata": metadata,
                }
            )

        if not legs:
            strategy.logger.debug(
                f"No exchange legs to close for {position.symbol}"
            )
            return

        if len(legs) == 1:
            await self._force_close_leg(position.symbol, legs[0], reason=reason, order_type=order_type)
            return

        await self._close_legs_atomically(position, legs, reason=reason, order_type=order_type)

    async def _close_legs_atomically(
        self,
        position: "FundingArbPosition",
        legs: List[Dict[str, Any]],
        reason: str = "UNKNOWN",
        order_type: Optional[str] = None,
    ) -> None:
        strategy = self._strategy
        
        # Log the close operation with details
        leg_summary = []
        for leg in legs:
            dex = leg.get("dex", "UNKNOWN")
            side = leg.get("side", "?")
            quantity = leg.get("quantity", Decimal("0"))
            leg_summary.append(f"{dex.upper()}:{side}:{quantity}")
        
        strategy.logger.info(
            f"🔒 Closing position {position.symbol} atomically | "
            f"Reason: {reason} | "
            f"Legs: [{', '.join(leg_summary)}]"
        )
        
        order_specs: List[OrderSpec] = []

        for leg in legs:
            try:
                spec = await self._build_order_spec(position.symbol, leg, reason=reason, order_type=order_type)
            except Exception as exc:
                strategy.logger.error(
                    f"[{leg['dex']}] Unable to prepare close order for {position.symbol}: {exc}"
                )
                raise
            order_specs.append(spec)

        result = await strategy.atomic_executor.execute_atomically(
            orders=order_specs,
            rollback_on_partial=True,
            pre_flight_check=False,
            skip_preflight_leverage=True,
            stage_prefix="close",
        )

        if not result.all_filled:
            error = result.error_message or "Incomplete fills during close"
            raise RuntimeError(
                f"Atomic close failed for {position.symbol}: {error}"
            )
        
        # Store execution result for PnL calculation
        # The filled_orders contain the actual fill prices from websocket updates
        position.metadata["close_execution_result"] = {
            "filled_orders": [
                {
                    "dex": leg["dex"],
                    "fill_price": order.get("fill_price"),
                    "filled_quantity": order.get("filled_quantity"),
                    "slippage_usd": order.get("slippage_usd", Decimal("0")),
                }
                for leg, order in zip(legs, result.filled_orders)
                if order.get("filled")
            ],
            "total_slippage_usd": result.total_slippage_usd or Decimal("0"),
        }
        
        # After successful atomic close, check for and close any residual positions
        # This ensures we fully close to 0 size, even if hedging left small residuals
        await self._cleanup_residual_positions(position, legs, reason=reason)

    async def _cleanup_residual_positions(
        self,
        position: "FundingArbPosition",
        legs: List[Dict[str, Any]],
        reason: str = "UNKNOWN",
    ) -> None:
        """
        After atomic close, check for and close any residual positions to ensure full closure.
        
        This handles cases where hedging operations may leave small residual quantities
        that need to be fully closed to 0.
        """
        strategy = self._strategy
        symbol = position.symbol
        
        # Fetch current snapshots for all legs
        residual_legs = []
        for leg in legs:
            dex = leg.get("dex", "UNKNOWN")
            client = leg.get("client")
            if not client:
                continue
            
            try:
                snapshot = await client.get_position_snapshot(symbol)
                if snapshot is None:
                    continue
                
                quantity = snapshot.quantity or Decimal("0")
                abs_quantity = quantity.copy_abs()
                
                # If there's any residual quantity, we need to close it
                if abs_quantity > self._ZERO_TOLERANCE:
                    # Determine side to close: opposite of current position
                    # If quantity > 0 (long), we need to sell
                    # If quantity < 0 (short), we need to buy
                    if snapshot.side:
                        # Use explicit side from snapshot (most reliable)
                        close_side = "sell" if snapshot.side == "long" else "buy"
                    else:
                        # Fallback: infer from quantity sign
                        close_side = "sell" if quantity > 0 else "buy"
                    
                    residual_legs.append({
                        "dex": dex,
                        "client": client,
                        "snapshot": snapshot,
                        "quantity": abs_quantity,
                        "side": close_side,
                        "contract_id": leg.get("contract_id"),
                        "metadata": leg.get("metadata", {}),
                    })
            except Exception as exc:
                strategy.logger.warning(
                    f"[{dex}] Failed to fetch snapshot for residual cleanup on {symbol}: {exc}"
                )
                continue
        
        if not residual_legs:
            # No residual positions - we're fully closed!
            return
        
        # Log residual positions found
        residual_summary = []
        for leg in residual_legs:
            residual_summary.append(
                f"{leg['dex'].upper()}:{leg['side']}:{leg['quantity']}"
            )
        strategy.logger.info(
            f"🧹 Cleaning up residual positions for {symbol}: [{', '.join(residual_summary)}]"
        )
        
        # Close each residual leg with market orders
        for leg in residual_legs:
            dex = leg["dex"]
            client = leg["client"]
            quantity = leg["quantity"]
            side = leg["side"]
            
            try:
                # Prepare contract context
                contract_id = await self._prepare_contract_context(
                    client,
                    symbol,
                    metadata=leg.get("metadata", {}),
                    contract_hint=leg.get("contract_id"),
                )
                
                # Get current price for size calculation
                price = self._extract_snapshot_price(leg["snapshot"])
                if price is None or price <= Decimal("0"):
                    price = await self._fetch_mid_price(client, symbol)
                
                if price is None or price <= Decimal("0"):
                    strategy.logger.warning(
                        f"[{dex}] Unable to determine price for residual cleanup on {symbol}, skipping"
                    )
                    continue
                
                size_usd = quantity * price
                
                strategy.logger.info(
                    f"🧹 Closing residual {symbol} on {dex.upper()}: "
                    f"{side} {quantity} @ ~${price:.6f} (${size_usd:.2f})"
                )
                
                # Use market order to ensure quick execution
                # reduce_only=True ensures we can only close, not open new positions
                execution = await self._order_executor.execute_order(
                    exchange_client=client,
                    symbol=symbol,
                    side=side,
                    size_usd=size_usd,
                    quantity=quantity,
                    mode=ExecutionMode.MARKET_ONLY,
                    timeout_seconds=10.0,
                    reduce_only=True,  # Critical: only allow closing, not opening
                )
                
                if execution.success and execution.filled:
                    strategy.logger.info(
                        f"✅ Residual position closed on {dex.upper()}: "
                        f"{execution.filled_quantity} @ {execution.fill_price or 'N/A'}"
                    )
                else:
                    error = execution.error_message or "Unknown error"
                    strategy.logger.warning(
                        f"⚠️ Failed to close residual position on {dex.upper()}: {error}"
                    )
                    
            except Exception as exc:
                strategy.logger.error(
                    f"[{dex}] Error closing residual position on {symbol}: {exc}"
                )
                continue

    async def _force_close_leg(
        self,
        symbol: str,
        leg: Dict[str, Any],
        reason: str = "UNKNOWN",
        order_type: Optional[str] = None,
    ) -> None:
        strategy = self._strategy
        
        dex = leg.get("dex", "UNKNOWN")
        side = leg.get("side", "?")
        quantity = leg.get("quantity", Decimal("0"))
        
        strategy.logger.info(
            f"🔒 Closing single leg {symbol} | "
            f"Reason: {reason} | "
            f"Leg: {dex.upper()}:{side}:{quantity}"
        )
        
        leg["contract_id"] = await self._prepare_contract_context(
            leg["client"],
            symbol,
            metadata=leg.get("metadata") or {},
            contract_hint=leg.get("contract_id"),
        )
        price = self._extract_snapshot_price(leg["snapshot"])
        if price is None or price <= Decimal("0"):
            price = await self._fetch_mid_price(leg["client"], symbol)

        size_usd = leg["quantity"] * price if price is not None else None

        strategy.logger.warning(
            f"[{leg['dex']}] Emergency close {symbol} qty={leg['quantity']} via market order"
        )

        # Use order_type if provided, otherwise default to market for single leg closes
        if order_type == "limit":
            mode = ExecutionMode.LIMIT_ONLY
        else:
            mode = ExecutionMode.MARKET_ONLY
        
        execution = await self._order_executor.execute_order(
            exchange_client=leg["client"],
            symbol=symbol,
            side=leg["side"],
            size_usd=size_usd,
            quantity=leg["quantity"],
            mode=mode,
            timeout_seconds=10.0,
        )

        if not execution.success or not execution.filled:
            error = execution.error_message or "market close failed"
            raise RuntimeError(f"[{leg['dex']}] Emergency close failed: {error}")

        # Update snapshot info for downstream logging/tests
        leg_snapshot = leg.get("snapshot")
        if leg_snapshot is not None:
            leg_snapshot.quantity = Decimal("0")

    async def _build_order_spec(
        self,
        symbol: str,
        leg: Dict[str, Any],
        reason: str = "UNKNOWN",
        order_type: Optional[str] = None,
    ) -> OrderSpec:
        leg["contract_id"] = await self._prepare_contract_context(
            leg["client"],
            symbol,
            metadata=leg.get("metadata") or {},
            contract_hint=leg.get("contract_id"),
        )
        price = self._extract_snapshot_price(leg["snapshot"])
        if price is None or price <= Decimal("0"):
            price = await self._fetch_mid_price(leg["client"], symbol)

        if price is None or price <= Decimal("0"):
            raise RuntimeError("Unable to determine price for close order")

        quantity = leg["quantity"]
        notional = quantity * price
        
        # Determine execution mode:
        # 1. Use order_type if explicitly provided (from API)
        # 2. Use market for critical reasons
        # 3. Default to limit for normal exits
        critical_reasons = {
            "SEVERE_IMBALANCE",
            "LEG_LIQUIDATED", 
            "LIQUIDATION_ASTER",
            "LIQUIDATION_LIGHTER",
            "LIQUIDATION_BACKPACK",
            "LIQUIDATION_PARADEX",
        }
        
        if order_type:
            # Explicit order type from API
            use_market = order_type.lower() == "market"
        else:
            # Fall back to reason-based logic
            use_market = reason in critical_reasons
        
        execution_mode = "market_only" if use_market else "limit_only"
        limit_offset_pct = None if use_market else self._resolve_limit_offset_pct()

        return OrderSpec(
            exchange_client=leg["client"],
            symbol=symbol,
            side=leg["side"],
            size_usd=notional,
            quantity=quantity,
            execution_mode=execution_mode,
            timeout_seconds=30.0,
            limit_price_offset_pct=limit_offset_pct,
            reduce_only=True,  # ✅ Allows closing dust positions below min notional
        )

    def _resolve_limit_offset_pct(self) -> Optional[Decimal]:
        value = getattr(self._strategy.config, "limit_order_offset_pct", None)
        if value is None:
            return None
        if isinstance(value, Decimal):
            return value
        try:
            return Decimal(str(value))
        except Exception:
            return None

    @staticmethod
    def _extract_snapshot_price(snapshot: "ExchangePositionSnapshot") -> Optional[Decimal]:
        for attr in ("mark_price", "entry_price"):
            value = getattr(snapshot, attr, None)
            if value is not None and value > 0:
                return value

        exposure = getattr(snapshot, "exposure_usd", None)
        quantity = getattr(snapshot, "quantity", None)
        if exposure is not None and quantity:
            try:
                return (exposure / quantity.copy_abs()).copy_abs()
            except Exception:
                return None
        return None

    async def _fetch_mid_price(
        self,
        client,
        symbol: str,
    ) -> Optional[Decimal]:
        try:
            best_bid, best_ask = await client.fetch_bbo_prices(symbol)
        except Exception as exc:
            self._strategy.logger.warning(
                f"[{client.get_exchange_name()}] Failed to fetch BBO for {symbol}: {exc}"
            )
            return None

        try:
            bid = Decimal(str(best_bid))
            ask = Decimal(str(best_ask))
        except Exception:
            return None

        if bid <= 0 or ask <= 0:
            return None

        return (bid + ask) / 2

    async def _prepare_contract_context(
        self,
        client,
        symbol: str,
        *,
        metadata: Optional[Dict[str, Any]] = None,
        contract_hint: Optional[Any] = None,
    ) -> Optional[Any]:
        """
        Ensure the exchange client is configured with the correct contract metadata.

        Closing legs often happens long after a position was opened. Some exchange
        clients reset their cached contract identifiers (contract_id, ticker, base
        multipliers) between runs, so we re-hydrate them on demand using the live
        snapshot metadata and connector helpers.
        """
        metadata = metadata or {}
        config = getattr(client, "config", None)

        def _is_valid_contract(value: Any) -> bool:
            if value is None:
                return False
            if isinstance(value, str):
                stripped = value.strip()
                if not stripped:
                    return False
                if stripped.upper() in {"ALL", "MULTI", "MULTI_SYMBOL"}:
                    return False
                return True
            if isinstance(value, (int, Decimal)):
                return value != 0
            return True

        candidate_ids: List[Any] = [
            contract_hint,
            metadata.get("contract_id"),
            metadata.get("market_id"),
            metadata.get("backpack_symbol"),
            metadata.get("exchange_symbol"),
        ]
        if config is not None:
            candidate_ids.append(getattr(config, "contract_id", None))

        resolved_contract: Optional[Any] = next(
            (cid for cid in candidate_ids if _is_valid_contract(cid)), None
        )

        if not _is_valid_contract(resolved_contract) and hasattr(client, "normalize_symbol"):
            try:
                normalized = client.normalize_symbol(symbol)
            except Exception:
                normalized = None
            if _is_valid_contract(normalized):
                resolved_contract = normalized

        # Try to restore multipliers from metadata cache (saves 300 weight REST call!)
        if metadata:
            if hasattr(client, "base_amount_multiplier") and getattr(client, "base_amount_multiplier", None) is None:
                cached_base = metadata.get("base_amount_multiplier")
                if cached_base is not None:
                    try:
                        setattr(client, "base_amount_multiplier", cached_base)
                    except Exception:
                        pass
            
            if hasattr(client, "price_multiplier") and getattr(client, "price_multiplier", None) is None:
                cached_price = metadata.get("price_multiplier")
                if cached_price is not None:
                    try:
                        setattr(client, "price_multiplier", cached_price)
                    except Exception:
                        pass
        
        base_multiplier_missing = hasattr(client, "base_amount_multiplier") and getattr(
            client, "base_amount_multiplier", None
        ) is None
        price_multiplier_missing = hasattr(client, "price_multiplier") and getattr(
            client, "price_multiplier", None
        ) is None
        needs_refresh = not _is_valid_contract(resolved_contract)

        if (needs_refresh or base_multiplier_missing or price_multiplier_missing) and hasattr(
            client, "get_contract_attributes"
        ):
            ticker_restore = None
            candidate_ticker = (
                metadata.get("symbol")
                or metadata.get("backpack_symbol")
                or metadata.get("exchange_symbol")
                or symbol
            )
            if config is not None and candidate_ticker:
                ticker_restore = getattr(config, "ticker", None)
                try:
                    setattr(config, "ticker", candidate_ticker)
                except Exception:
                    ticker_restore = None

            try:
                attr = await client.get_contract_attributes()
                refreshed_id: Optional[Any]
                if isinstance(attr, tuple):
                    refreshed_id = attr[0]
                else:
                    refreshed_id = attr
                if _is_valid_contract(refreshed_id):
                    resolved_contract = refreshed_id
            except Exception as exc:
                self._strategy.logger.warning(
                    f"⚠️ [{client.get_exchange_name().upper()}] Failed to refresh contract attributes "
                    f"for {symbol}: {exc}"
                )
            finally:
                if ticker_restore is not None and config is not None:
                    try:
                        setattr(config, "ticker", ticker_restore)
                    except Exception:
                        pass

        if config is not None:
            try:
                if _is_valid_contract(resolved_contract):
                    setattr(config, "contract_id", resolved_contract)
                ticker_value = getattr(config, "ticker", None)
                if not ticker_value or str(ticker_value).upper() in {"ALL", "MULTI", "MULTI_SYMBOL"}:
                    setattr(config, "ticker", symbol)
            except Exception:
                pass

        # Surface the resolved contract_id to callers and leg metadata
        if _is_valid_contract(resolved_contract):
            metadata.setdefault("contract_id", resolved_contract)
            
            # ⚡ Cache multipliers to metadata to avoid expensive get_contract_attributes() on next session
            if hasattr(client, "base_amount_multiplier"):
                base_mult = getattr(client, "base_amount_multiplier", None)
                if base_mult is not None:
                    metadata.setdefault("base_amount_multiplier", base_mult)
            
            if hasattr(client, "price_multiplier"):
                price_mult = getattr(client, "price_multiplier", None)
                if price_mult is not None:
                    metadata.setdefault("price_multiplier", price_mult)
            
            return resolved_contract
        return None

    @staticmethod
    def _symbols_match(position_symbol: Optional[str], event_symbol: Optional[str]) -> bool:
        pos_upper = (position_symbol or "").upper()
        event_upper = (event_symbol or "").upper()

        if not pos_upper or not event_upper:
            return False

        if pos_upper == event_upper:
            return True

        if event_upper.endswith(pos_upper) or event_upper.startswith(pos_upper):
            # e.g., BTCUSDT vs BTC, BTC-USD vs BTC
            return True

        if pos_upper.endswith(event_upper) or pos_upper.startswith(event_upper):
            return True

        return False
