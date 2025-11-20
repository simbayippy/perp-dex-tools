"""Close execution for position closing."""

from decimal import Decimal
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from strategies.execution.core.order_executor import OrderExecutor, ExecutionMode
from strategies.execution.patterns.atomic_multi_order import OrderSpec

from ..core.contract_preparer import ContractPreparer
from ..core.websocket_manager import WebSocketManager
from ..core.price_utils import (
    extract_snapshot_price,
    fetch_mid_price,
    calculate_spread_pct,
    MAX_EXIT_SPREAD_PCT,
    MAX_EMERGENCY_CLOSE_SPREAD_PCT,
)
from ..core.decimal_utils import to_decimal

if TYPE_CHECKING:
    from exchange_clients.base_models import ExchangePositionSnapshot
    from ...models import FundingArbPosition
    from ...strategy import FundingArbitrageStrategy


class CloseExecutor:
    """Handles closing execution on exchanges."""
    
    _ZERO_TOLERANCE = Decimal("0")
    
    def __init__(self, strategy: "FundingArbitrageStrategy"):
        self._strategy = strategy
        self._order_executor = OrderExecutor(price_provider=strategy.price_provider)
        self._contract_preparer = ContractPreparer()
        self._ws_manager = WebSocketManager()
    
    async def close_exchange_positions(
        self,
        position: "FundingArbPosition",
        *,
        reason: str = "UNKNOWN",
        live_snapshots: Optional[Dict[str, Optional["ExchangePositionSnapshot"]]] = None,
        order_type: Optional[str] = None,
        order_builder: Any,
    ) -> None:
        """
        Close legs on the exchanges, skipping those already flat.
        
        Args:
            position: Position to close
            reason: Reason for closing
            live_snapshots: Optional pre-fetched snapshots
            order_type: Optional order type override ("market" or "limit")
            order_builder: Order builder instance
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
            await self._contract_preparer.prepare_contract_context(
                client,
                position.symbol,
                metadata=leg_hint,
                contract_hint=leg_hint.get("market_id"),
                logger=strategy.logger,
            )
            await self._ws_manager.ensure_market_feed_once(client, position.symbol, strategy.logger)

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

            if snapshot.side:
                side = "sell" if snapshot.side == "long" else "buy"
            else:
                side = "sell" if snapshot.quantity > 0 else "buy"
            metadata: Dict[str, Any] = getattr(snapshot, "metadata", {}) or {}

            if metadata:
                await self._contract_preparer.prepare_contract_context(
                    client,
                    position.symbol,
                    metadata=metadata,
                    contract_hint=metadata.get("market_id"),
                    logger=strategy.logger,
                )

            contract_id = await self._contract_preparer.prepare_contract_context(
                client,
                position.symbol,
                metadata=metadata,
                contract_hint=metadata.get("market_id"),
                logger=strategy.logger,
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
            await self._force_close_leg(
                position, legs[0], reason=reason, order_type=order_type
            )
            return

        await self._close_legs_atomically(position, legs, reason=reason, order_type=order_type, order_builder=order_builder)
    
    async def _close_legs_atomically(
        self,
        position: "FundingArbPosition",
        legs: List[Dict[str, Any]],
        reason: str = "UNKNOWN",
        order_type: Optional[str] = None,
        order_builder: Any = None,
    ) -> None:
        """Close legs atomically."""
        strategy = self._strategy
        
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
                spec = await order_builder.build_order_spec(
                    position.symbol, leg, reason=reason, order_type=order_type
                )
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
        
        position.metadata["close_execution_result"] = {
            "filled_orders": [
                {
                    "dex": leg["dex"],
                    "fill_price": order.get("fill_price"),
                    "filled_quantity": order.get("filled_quantity"),
                    "slippage_usd": order.get("slippage_usd", Decimal("0")),
                    "order_id": order.get("order_id"),
                }
                for leg, order in zip(legs, result.filled_orders)
                if order.get("filled")
            ],
            "total_slippage_usd": result.total_slippage_usd or Decimal("0"),
        }
        
        await self._cleanup_residual_positions(position, legs, reason=reason)
    
    async def _cleanup_residual_positions(
        self,
        position: "FundingArbPosition",
        legs: List[Dict[str, Any]],
        reason: str = "UNKNOWN",
    ) -> None:
        """After atomic close, check for and close any residual positions."""
        strategy = self._strategy
        symbol = position.symbol
        
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
                
                if abs_quantity > self._ZERO_TOLERANCE:
                    if snapshot.side:
                        close_side = "sell" if snapshot.side == "long" else "buy"
                    else:
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
            return
        
        residual_summary = []
        for leg in residual_legs:
            residual_summary.append(
                f"{leg['dex'].upper()}:{leg['side']}:{leg['quantity']}"
            )
        strategy.logger.info(
            f"🧹 Cleaning up residual positions for {symbol}: [{', '.join(residual_summary)}]"
        )
        
        for leg in residual_legs:
            dex = leg["dex"]
            client = leg["client"]
            quantity = leg["quantity"]
            side = leg["side"]
            
            try:
                contract_id = await self._contract_preparer.prepare_contract_context(
                    client,
                    symbol,
                    metadata=leg.get("metadata", {}),
                    contract_hint=leg.get("contract_id"),
                    logger=strategy.logger,
                )
                
                price = extract_snapshot_price(leg["snapshot"])
                if price is None or price <= Decimal("0"):
                    price = await fetch_mid_price(client, symbol, strategy.logger)
                
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
                
                execution = await self._order_executor.execute_order(
                    exchange_client=client,
                    symbol=symbol,
                    side=side,
                    size_usd=size_usd,
                    quantity=quantity,
                    mode=ExecutionMode.MARKET_ONLY,
                    timeout_seconds=10.0,
                    reduce_only=True,
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
        position: "FundingArbPosition",
        leg: Dict[str, Any],
        reason: str = "UNKNOWN",
        order_type: Optional[str] = None,
    ) -> None:
        """Force close a single leg with spread protection and adaptive pricing."""
        strategy = self._strategy
        
        symbol = position.symbol
        dex = leg.get("dex", "UNKNOWN")
        side = leg.get("side", "?")
        quantity = leg.get("quantity", Decimal("0"))
        
        strategy.logger.info(
            f"🔒 Closing single leg {symbol} | "
            f"Reason: {reason} | "
            f"Leg: {dex.upper()}:{side}:{quantity}"
        )
        
        leg["contract_id"] = await self._contract_preparer.prepare_contract_context(
            leg["client"],
            symbol,
            metadata=leg.get("metadata") or {},
            contract_hint=leg.get("contract_id"),
            logger=strategy.logger,
        )
        price = extract_snapshot_price(leg["snapshot"])
        if price is None or price <= Decimal("0"):
            price = await fetch_mid_price(leg["client"], symbol, strategy.logger)

        size_usd = leg["quantity"] * price if price is not None else None

        # Check spread and determine execution mode
        critical_reasons = {
            "SEVERE_IMBALANCE",
            "LEG_LIQUIDATED",
            "LIQUIDATION_ASTER",
            "LIQUIDATION_LIGHTER",
            "LIQUIDATION_BACKPACK",
            "LIQUIDATION_PARADEX",
        }
        is_critical = reason in critical_reasons
        
        # Check spread if protection is enabled
        use_aggressive_limit = False
        if getattr(strategy.config, "enable_wide_spread_protection", True):
            try:
                price_provider = strategy.price_provider
                bid, ask = await price_provider.get_bbo_prices(leg["client"], symbol)
                spread_pct = calculate_spread_pct(bid, ask)
                
                if spread_pct is not None:
                    threshold = MAX_EMERGENCY_CLOSE_SPREAD_PCT if is_critical else MAX_EXIT_SPREAD_PCT
                    
                    if spread_pct > threshold:
                        strategy.logger.warning(
                            f"⚠️  Wide spread detected on {dex.upper()} {symbol}: "
                            f"{spread_pct*100:.2f}% (bid={bid}, ask={ask}). "
                            f"Using aggressive limit execution for better fills."
                        )
                        use_aggressive_limit = True
                    elif spread_pct > MAX_EXIT_SPREAD_PCT:
                        # Even if below emergency threshold, use aggressive limit for better execution
                        use_aggressive_limit = True
            except Exception as exc:
                strategy.logger.warning(
                    f"⚠️  Failed to check spread for {symbol} on {dex}: {exc}. "
                    f"Proceeding with original execution mode."
                )
        
        # Determine execution mode
        if use_aggressive_limit:
            # Use aggressive limit with adaptive pricing (inside spread, touch, etc.)
            mode = ExecutionMode.AGGRESSIVE_LIMIT
            strategy.logger.info(
                f"Using aggressive limit execution for {symbol} on {dex.upper()} "
                f"(adaptive pricing: inside spread → touch → cross spread)"
            )
        elif order_type == "limit":
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
            timeout_seconds=8.0 if use_aggressive_limit else 10.0,
            reduce_only=True,
            max_retries=5 if use_aggressive_limit else None,
            total_timeout_seconds=8.0 if use_aggressive_limit else None,
            inside_tick_retries=2 if use_aggressive_limit else None,
        )

        if not execution.success or not execution.filled:
            error = execution.error_message or "close failed"
            raise RuntimeError(f"[{dex}] Emergency close failed: {error}")

        leg_snapshot = leg.get("snapshot")
        if leg_snapshot is not None:
            leg_snapshot.quantity = Decimal("0")
    
    @classmethod
    def _has_open_position(cls, snapshot: Optional["ExchangePositionSnapshot"]) -> bool:
        """Check if snapshot indicates an open position."""
        if snapshot is None or snapshot.quantity is None:
            return False
        return snapshot.quantity.copy_abs() > cls._ZERO_TOLERANCE

