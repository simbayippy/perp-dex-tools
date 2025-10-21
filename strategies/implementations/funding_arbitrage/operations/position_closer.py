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
                strategy.logger.log(
                    f"Closed {position.symbol}: {liquidation_reason}", "WARNING"
                )
                actions.append(f"Closed {position.symbol}: {liquidation_reason}")
                continue

            should_close, reason = await self._should_close(position, snapshots)
            if should_close:
                await self.close(position, reason or "UNKNOWN", live_snapshots=snapshots)
                strategy.logger.log(f"Closed {position.symbol}: {reason}", "INFO")
                actions.append(f"Closed {position.symbol}: {reason}")
            else:
                strategy.logger.log(
                    f"Position {position.symbol} not closing: {reason}", "DEBUG"
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

            strategy.logger.log(
                f"🚨 Liquidation event detected on {event.exchange.upper()} for {event.symbol} "
                f"(side={event.side}, qty={event.quantity}, price={event.price}).",
                "ERROR",
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
                strategy.logger.log(
                    f"Risk manager evaluation failed for {position.symbol}: {exc}",
                    "ERROR",
                )

        # Fallback heuristics if risk manager unavailable or declined
        if position.current_divergence and position.current_divergence < 0:
            return True, "DIVERGENCE_FLIPPED"

        erosion = position.get_profit_erosion()
        if erosion < strategy.config.risk_config.min_erosion_threshold:
            if await self._should_skip_erosion_exit(position, "PROFIT_EROSION"):
                return False, "HOLD_TOP_OPPORTUNITY"
            return True, "PROFIT_EROSION"

        if position.get_age_hours() > strategy.config.risk_config.max_position_age_hours:
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
    ) -> None:
        strategy = self._strategy

        try:
            pnl = position.get_net_pnl()
            pnl_pct = position.get_net_pnl_pct()

            await self._close_exchange_positions(
                position,
                live_snapshots=live_snapshots,
            )

            await strategy.position_manager.close(
                position.id,
                exit_reason=reason,
                pnl_usd=pnl,
            )

            refreshed = await strategy.position_manager.get(position.id)
            if refreshed:
                position = refreshed

            strategy.logger.log(
                f"✅ Closed {position.symbol} ({reason}): "
                f"PnL=${pnl:.2f} ({pnl_pct*100:.2f}%), "
                f"Age={position.get_age_hours():.1f}h",
                "INFO",
            )

        except Exception as exc:  # pragma: no cover - defensive logging
            strategy.logger.log(
                f"Error closing position {position.id}: {exc}",
                "ERROR",
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
            strategy.logger.log(
                f"Failed to initialize risk manager '{risk_cfg.strategy}': {exc}",
                "ERROR",
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
            self._strategy.logger.log(
                f"Failed to fetch funding rates for {position.symbol}: {exc}",
                "ERROR",
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
            self._strategy.logger.log(
                f"Funding rate data missing for {position.symbol}: "
                f"long={long_rate_row}, short={short_rate_row}",
                "WARNING",
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
                self._strategy.logger.log(
                    f"No exchange client for {dex} while evaluating {position.symbol}",
                    "ERROR",
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
                snapshots[dex] = await client.get_position_snapshot(position.symbol)
            except Exception as exc:  # pragma: no cover - defensive logging
                self._strategy.logger.log(
                    f"[{dex}] Failed to fetch position snapshot for {position.symbol}: {exc}",
                    "ERROR",
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

            if ws_manager and getattr(ws_manager, "running", False):
                await self._await_ws_snapshot(ws_manager)
        except Exception as exc:  # pragma: no cover - defensive logging
            self._strategy.logger.log(
                f"⚠️ [{exchange_name}] WebSocket prep error during close: {exc}",
                "DEBUG",
            )
        else:
            self._ws_prepared[exchange_name] = symbol_key

    async def _await_ws_snapshot(self, ws_manager: Any, timeout: float = 1.0) -> None:
        """
        Wait briefly for websocket managers to populate top-of-book data.
        """
        if not getattr(ws_manager, "running", False):
            return

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        deadline = loop.time() + timeout
        while loop.time() < deadline:
            snapshot_ready = False
            if hasattr(ws_manager, "snapshot_loaded"):
                snapshot_ready = bool(ws_manager.snapshot_loaded)
            if getattr(ws_manager, "best_bid", None) is not None:
                snapshot_ready = True
            if getattr(ws_manager, "best_ask", None) is not None:
                snapshot_ready = True

            if snapshot_ready:
                return

            await asyncio.sleep(0.05)

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
        self._strategy.logger.log(
            f"⚠️ Detected missing legs {leg_list} for {position.symbol}; initiating emergency close.",
            "WARNING",
        )
        return "LEG_LIQUIDATED"

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
            strategy.logger.log(
                f"Failed to score opportunities while checking erosion guard for "
                f"{position.symbol}: {exc}",
                "ERROR",
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

            strategy.logger.log(
                f"Holding {position.symbol}: erosion trigger fired but opportunity "
                f"still ranks highest ({net_display}% net).",
                "INFO",
            )
            return True

        return False

    async def _close_exchange_positions(
        self,
        position: "FundingArbPosition",
        *,
        live_snapshots: Optional[
            Dict[str, Optional["ExchangePositionSnapshot"]]
        ] = None,
    ) -> None:
        """
        Close legs on the exchanges, skipping those already flat.
        """
        strategy = self._strategy
        legs: List[Dict[str, Any]] = []
        live_snapshots = live_snapshots or {}

        position_legs = (position.metadata or {}).get("legs", {})

        for dex in filter(None, [position.long_dex, position.short_dex]):
            client = strategy.exchange_clients.get(dex)
            if client is None:
                strategy.logger.log(
                    f"Skipping close for {dex}: no exchange client available",
                    "ERROR",
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
                    strategy.logger.log(
                        f"[{dex}] Failed to fetch position snapshot for close: {exc}",
                        "ERROR",
                    )
                    continue

            if not self._has_open_position(snapshot):
                strategy.logger.log(
                    f"[{dex}] No open position detected for {position.symbol}; skipping close call.",
                    "DEBUG",
                )
                continue

            quantity = snapshot.quantity.copy_abs() if snapshot.quantity is not None else Decimal("0")
            if quantity <= self._ZERO_TOLERANCE:
                strategy.logger.log(
                    f"[{dex}] Snapshot quantity zero for {position.symbol}; skipping.",
                    "DEBUG",
                )
                continue

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
            strategy.logger.log(
                f"No exchange legs to close for {position.symbol}", "DEBUG"
            )
            return

        if len(legs) == 1:
            await self._force_close_leg(position.symbol, legs[0])
            return

        await self._close_legs_atomically(position, legs)

    async def _close_legs_atomically(
        self,
        position: "FundingArbPosition",
        legs: List[Dict[str, Any]],
    ) -> None:
        strategy = self._strategy
        order_specs: List[OrderSpec] = []

        for leg in legs:
            try:
                spec = await self._build_order_spec(position.symbol, leg)
            except Exception as exc:
                strategy.logger.log(
                    f"[{leg['dex']}] Unable to prepare close order for {position.symbol}: {exc}",
                    "ERROR",
                )
                raise
            order_specs.append(spec)

        result = await strategy.atomic_executor.execute_atomically(
            orders=order_specs,
            rollback_on_partial=True,
            pre_flight_check=False,
            skip_preflight_leverage=True,
            stage_prefix="close",
            retry_policy=strategy.atomic_retry_policy,
        )

        if not result.all_filled:
            error = result.error_message or "Incomplete fills during close"
            raise RuntimeError(
                f"Atomic close failed for {position.symbol}: {error}"
            )

    async def _force_close_leg(
        self,
        symbol: str,
        leg: Dict[str, Any],
    ) -> None:
        strategy = self._strategy
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

        strategy.logger.log(
            f"[{leg['dex']}] Emergency close {symbol} qty={leg['quantity']} via market order",
            "WARNING",
        )

        execution = await self._order_executor.execute_order(
            exchange_client=leg["client"],
            symbol=symbol,
            side=leg["side"],
            size_usd=size_usd,
            quantity=leg["quantity"],
            mode=ExecutionMode.MARKET_ONLY,
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
        limit_offset_pct = self._resolve_limit_offset_pct()

        return OrderSpec(
            exchange_client=leg["client"],
            symbol=symbol,
            side=leg["side"],
            size_usd=notional,
            quantity=quantity,
            execution_mode="limit_with_fallback",
            timeout_seconds=60.0,
            limit_price_offset_pct=limit_offset_pct,
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
            self._strategy.logger.log(
                f"[{client.get_exchange_name()}] Failed to fetch BBO for {symbol}: {exc}",
                "WARNING",
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
                self._strategy.logger.log(
                    f"⚠️ [{client.get_exchange_name().upper()}] Failed to refresh contract attributes "
                    f"for {symbol}: {exc}",
                    "WARNING",
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
