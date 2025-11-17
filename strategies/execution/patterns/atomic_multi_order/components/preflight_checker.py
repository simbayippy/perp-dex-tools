"""
Pre-flight checker for atomic multi-order execution.

Validates leverage, margin, balance, liquidity, and minimum notional requirements
before executing orders.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from helpers.unified_logger import get_core_logger, log_stage
from strategies.execution.core.liquidity_analyzer import LiquidityAnalyzer

if TYPE_CHECKING:
    from ..executor import OrderSpec


class PreFlightChecker:
    """Validates orders before execution."""

    def __init__(
        self,
        price_provider=None,
        leverage_validator=None,
        notification_service=None,
        logger=None,
    ):
        self.price_provider = price_provider
        self.leverage_validator = leverage_validator
        self.notification_service = notification_service
        self.logger = logger or get_core_logger("preflight_checker")

    def _compose_stage_id(self, stage_prefix: Optional[str], *parts: str) -> Optional[str]:
        """Compose stage ID from prefix and parts."""
        if stage_prefix:
            if parts:
                return ".".join([stage_prefix, *parts])
            return stage_prefix
        if parts:
            return ".".join(parts)
        return None

    async def check(
        self,
        orders: List["OrderSpec"],
        skip_leverage_check: bool,
        stage_prefix: Optional[str],
        normalized_leverage: Dict[Tuple[str, str], int],  # In/out parameter
        margin_error_notified: Dict[Tuple[str, str], bool],  # In/out parameter
    ) -> tuple[bool, Optional[str]]:
        """
        Run all pre-flight checks.

        Args:
            orders: List of orders to validate
            skip_leverage_check: If True, skip leverage validation
            stage_prefix: Optional prefix for stage IDs
            normalized_leverage: Dict to store normalized leverage (in/out parameter)
            margin_error_notified: Dict to track notification state (in/out parameter)

        Returns:
            Tuple of (success: bool, error_message: Optional[str])
        """
        try:
            compose_stage = lambda *parts: self._compose_stage_id(stage_prefix, *parts)
            symbols_to_check: Dict[str, List[OrderSpec]] = {}
            for order_spec in orders:
                symbol = order_spec.symbol
                if symbol not in symbols_to_check:
                    symbols_to_check[symbol] = []
                symbols_to_check[symbol].append(order_spec)

            if not skip_leverage_check:
                log_stage(self.logger, "Leverage Validation", icon="📐", stage_id=compose_stage("1"))
                from strategies.execution.core.leverage_validator import LeverageValidator

                # Use shared leverage validator if available, otherwise create new instance
                leverage_validator = self.leverage_validator or LeverageValidator()

                for symbol, symbol_orders in symbols_to_check.items():
                    exchange_clients = [order.exchange_client for order in symbol_orders]
                    requested_size = symbol_orders[0].size_usd

                    max_size, limiting_exchange = await leverage_validator.get_max_position_size(
                        exchange_clients=exchange_clients,
                        symbol=symbol,
                        requested_size_usd=requested_size,
                        check_balance=True,
                    )

                    if max_size < requested_size:
                        error_msg = (
                            f"Position size too large for {symbol}: "
                            f"Requested ${requested_size:.2f}, "
                            f"maximum supported: ${max_size:.2f} "
                            f"(limited by {limiting_exchange})"
                        )
                        self.logger.warning(f"⚠️  {error_msg}")
                        return False, error_msg

                for symbol, symbol_orders in symbols_to_check.items():
                    exchange_clients = [order.exchange_client for order in symbol_orders]
                    requested_size = symbol_orders[0].size_usd

                    self.logger.info(f"Normalizing leverage for {symbol}...")
                    min_leverage, limiting = await leverage_validator.normalize_and_set_leverage(
                        exchange_clients=exchange_clients,
                        symbol=symbol,
                        requested_size_usd=requested_size,
                    )

                    if min_leverage is not None:
                        self.logger.info(
                            f"✅ [LEVERAGE] {symbol} normalized to {min_leverage}x "
                            f"(limited by {limiting})"
                        )
                        # Store normalized leverage for each exchange to use in margin calculations
                        for order in symbol_orders:
                            exchange_name = order.exchange_client.get_exchange_name()
                            cache_key = (exchange_name, symbol)
                            normalized_leverage[cache_key] = min_leverage
                    else:
                        self.logger.warning(
                            f"⚠️  [LEVERAGE] Could not normalize leverage for {symbol}. "
                            f"Orders may execute with different leverage!"
                        )

            log_stage(self.logger, "Margin & Balance Checks", icon="💰", stage_id=compose_stage("2"))
            self.logger.info("Running balance checks...")

            # Cache leverage info to avoid duplicate API calls
            leverage_info_cache: Dict[tuple, Any] = {}

            exchange_margin_required: Dict[str, Decimal] = {}
            exchange_leverage_info: Dict[str, Dict[str, Any]] = {}  # Store leverage info for notifications

            for order_spec in orders:
                exchange_name = order_spec.exchange_client.get_exchange_name()
                estimated_margin = await self.estimate_required_margin(
                    order_spec, normalized_leverage, leverage_info_cache
                )
                exchange_margin_required.setdefault(exchange_name, Decimal("0"))
                exchange_margin_required[exchange_name] += estimated_margin

                # Store leverage info for potential notifications
                cache_key = (exchange_name, order_spec.symbol)
                if cache_key in leverage_info_cache:
                    if exchange_name not in exchange_leverage_info:
                        exchange_leverage_info[exchange_name] = {}
                    exchange_leverage_info[exchange_name][order_spec.symbol] = leverage_info_cache[cache_key]

            for exchange_name, required_margin in exchange_margin_required.items():
                exchange_client = next(
                    (
                        order.exchange_client
                        for order in orders
                        if order.exchange_client.get_exchange_name() == exchange_name
                    ),
                    None,
                )
                if not exchange_client:
                    continue

                # Get orders for this specific exchange to determine symbol(s)
                exchange_orders = [
                    order for order in orders
                    if order.exchange_client.get_exchange_name() == exchange_name
                ]
                # Get symbol from exchange orders (use first order's symbol)
                # For funding arb, all orders for same exchange should have same symbol
                symbol = exchange_orders[0].symbol if exchange_orders else "UNKNOWN"

                try:
                    available_balance = await exchange_client.get_account_balance()
                except Exception as exc:  # pragma: no cover - defensive
                    self.logger.warning(
                        f"⚠️ Balance check failed for {exchange_name}: {exc}"
                    )
                    continue

                if available_balance is None:
                    self.logger.warning(
                        f"⚠️ Cannot verify balance for {exchange_name} (required: ~${required_margin:.2f})"
                    )
                    continue

                required_with_buffer = required_margin * Decimal("1.05")
                if available_balance < required_with_buffer:
                    error_msg = (
                        f"Insufficient balance on {exchange_name}: "
                        f"available=${available_balance:.2f}, required=${required_with_buffer:.2f} "
                        f"(${required_margin:.2f} + 5% buffer)"
                    )
                    self.logger.error(f"❌ {error_msg}")

                    # Check if we've already notified for this (exchange, symbol) combination
                    error_key = (exchange_name.lower(), symbol)
                    already_notified = margin_error_notified.get(error_key, False)

                    # Only send notification if we haven't notified for this error yet
                    if not already_notified:
                        # Attempt to send notification
                        await self._send_insufficient_margin_notification(
                            exchange_name=exchange_name,
                            available_balance=available_balance,
                            required_margin=required_margin,
                            exchange_leverage_info=exchange_leverage_info.get(exchange_name, {}),
                            orders=exchange_orders  # Pass only orders for this exchange
                        )
                        # Mark as notified
                        margin_error_notified[error_key] = True
                        self.logger.info(
                            f"📢 Sent insufficient margin notification for {exchange_name.upper()}/{symbol}"
                        )
                    else:
                        self.logger.debug(
                            f"⏭️ Skipping notification for {exchange_name.upper()}/{symbol} "
                            f"(already notified, margin still insufficient)"
                        )

                    return False, error_msg
                else:
                    # Margin is sufficient - reset notification state for this (exchange, symbol)
                    error_key = (exchange_name.lower(), symbol)
                    if margin_error_notified.get(error_key, False):
                        # Margin was insufficient before, but now it's sufficient - reset state
                        del margin_error_notified[error_key]
                        self.logger.info(
                            f"✅ Margin sufficient for {exchange_name.upper()}/{symbol} - "
                            f"notification state reset"
                        )

                self.logger.info(
                    f"✅ {exchange_name} balance OK: ${available_balance:.2f} >= ${required_with_buffer:.2f}"
                )

            log_stage(self.logger, "Order Book Liquidity", icon="🌊", stage_id=compose_stage("3"))
            self.logger.info("Running liquidity checks...")

            analyzer = LiquidityAnalyzer(price_provider=self.price_provider, max_spread_bps=100)

            for i, order_spec in enumerate(orders):
                self.logger.debug(
                    f"Checking liquidity for order {i}: {order_spec.side} {order_spec.symbol} ${order_spec.size_usd}"
                )
                report = await analyzer.check_execution_feasibility(
                    exchange_client=order_spec.exchange_client,
                    symbol=order_spec.symbol,
                    side=order_spec.side,
                    size_usd=order_spec.size_usd,
                )

                if not analyzer.is_execution_acceptable(report):
                    error_msg = (
                        f"Order {i} ({order_spec.side} {order_spec.symbol}) "
                        f"failed liquidity check: {report.recommendation}"
                    )
                    self.logger.warning(f"❌ {error_msg}")
                    return False, error_msg

            log_stage(
                self.logger,
                "Minimum Order Notional",
                icon="💵",
                stage_id=compose_stage("4"),
            )
            self.logger.info("Validating minimum notional requirements...")

            for order_spec in orders:
                planned_notional = order_spec.size_usd
                if planned_notional is None:
                    continue
                if not isinstance(planned_notional, Decimal):
                    planned_notional = Decimal(str(planned_notional))

                exchange_client = order_spec.exchange_client
                try:
                    min_notional = exchange_client.get_min_order_notional(order_spec.symbol)
                except Exception as exc:  # pragma: no cover - defensive
                    self.logger.debug(
                        f"Skipping min notional check for "
                        f"{exchange_client.get_exchange_name().upper()}:{order_spec.symbol} "
                        f"(error: {exc})"
                    )
                    continue

                if min_notional is None or min_notional <= Decimal("0"):
                    continue

                exchange_name = exchange_client.get_exchange_name().upper()
                if planned_notional < min_notional:
                    error_msg = (
                        f"[{exchange_name}] {order_spec.symbol} order notional "
                        f"${planned_notional:.2f} below minimum ${min_notional:.2f}"
                    )
                    self.logger.warning(f"❌ {error_msg}")
                    return False, error_msg

                self.logger.info(
                    f"✅ [{exchange_name}] {order_spec.symbol} notional ${planned_notional:.2f} "
                    f"meets minimum ${min_notional:.2f}"
                )

            self.logger.info("✅ All pre-flight checks passed")
            return True, None

        except Exception as exc:
            self.logger.error(f"Pre-flight check error: {exc}")
            self.logger.warning("⚠️ Continuing despite pre-flight check error")
            return True, None

    async def estimate_required_margin(
        self,
        order_spec: "OrderSpec",
        normalized_leverage: Dict[Tuple[str, str], int],
        leverage_info_cache: Optional[Dict[tuple, Any]] = None,
    ) -> Decimal:
        """
        Estimate required margin based on normalized leverage (if set) or leverage info for the symbol/exchange.

        ⭐ CRITICAL: Uses normalized leverage if available (from normalize_and_set_leverage),
        otherwise falls back to querying get_leverage_info. This ensures balance checks
        use the correct leverage (e.g., 5x normalized) instead of the symbol's max leverage (e.g., 20x).

        Args:
            order_spec: Order specification with exchange_client, symbol, and size_usd
            normalized_leverage: Dict of normalized leverage per (exchange_name, symbol)
            leverage_info_cache: Optional cache dict keyed by (exchange_name, symbol) to avoid duplicate API calls

        Returns:
            Estimated margin required in USD
        """
        from strategies.execution.core.leverage_validator import LeverageValidator

        exchange_name = order_spec.exchange_client.get_exchange_name()
        symbol = order_spec.symbol
        cache_key = (exchange_name, symbol)

        # ⭐ PRIORITY 1: Use normalized leverage if available (set during normalize_and_set_leverage)
        if cache_key in normalized_leverage:
            normalized_leverage_val = Decimal(str(normalized_leverage[cache_key]))
            estimated_margin = order_spec.size_usd / normalized_leverage_val
            self.logger.debug(
                f"📊 [{exchange_name}] Margin for {symbol}: ${estimated_margin:.2f} "
                f"(${order_spec.size_usd:.2f} / {normalized_leverage_val}x normalized leverage)"
            )
            return estimated_margin

        # ⭐ PRIORITY 2: Try to get leverage info from cache or fetch it
        # NOTE: This should rarely be needed if normalized leverage is properly set.
        leverage_info = None
        if leverage_info_cache and cache_key in leverage_info_cache:
            leverage_info = leverage_info_cache[cache_key]
        else:
            # Use shared leverage validator if available (benefits from caching),
            # otherwise create a new instance
            leverage_validator = self.leverage_validator or LeverageValidator()
            try:
                leverage_info = await leverage_validator.get_leverage_info(
                    order_spec.exchange_client, symbol
                )
                if leverage_info_cache is not None:
                    leverage_info_cache[cache_key] = leverage_info
            except Exception as exc:
                self.logger.warning(
                    f"⚠️ Could not fetch leverage info for {exchange_name}:{symbol}: {exc}. "
                    "Using conservative 20% margin estimate."
                )

        # Calculate margin based on leverage info
        if leverage_info and leverage_info.margin_requirement is not None:
            # Use margin requirement directly (e.g., 0.3333 for 3x leverage)
            margin_requirement = leverage_info.margin_requirement
            estimated_margin = order_spec.size_usd * margin_requirement
            self.logger.debug(
                f"📊 [{exchange_name}] Margin for {symbol}: ${estimated_margin:.2f} "
                f"({margin_requirement*100:.2f}% of ${order_spec.size_usd:.2f})"
            )
            return estimated_margin
        elif leverage_info and leverage_info.max_leverage is not None:
            # Calculate from max leverage (margin = size / leverage)
            max_leverage = leverage_info.max_leverage
            estimated_margin = order_spec.size_usd / max_leverage
            self.logger.debug(
                f"📊 [{exchange_name}] Margin for {symbol}: ${estimated_margin:.2f} "
                f"(${order_spec.size_usd:.2f} / {max_leverage}x leverage)"
            )
            return estimated_margin
        else:
            # Fallback to conservative 20% estimate if leverage info unavailable
            self.logger.warning(
                f"⚠️ No leverage info available for {exchange_name}:{symbol}, "
                "using conservative 20% margin estimate"
            )
            return order_spec.size_usd * Decimal("0.20")

    async def _send_insufficient_margin_notification(
        self,
        exchange_name: str,
        available_balance: Decimal,
        required_margin: Decimal,
        exchange_leverage_info: Dict[str, Any],
        orders: List["OrderSpec"],
    ) -> None:
        """
        Attempt to send insufficient margin notification via notification service.

        Args:
            exchange_name: Name of the exchange with insufficient margin
            available_balance: Available balance on the exchange
            required_margin: Required margin amount
            exchange_leverage_info: Dict of symbol -> LeverageInfo for this exchange
            orders: List of orders that failed margin check
        """
        if not self.notification_service:
            return

        try:
            # Get symbol from orders (use first order's symbol)
            symbol = orders[0].symbol if orders else "UNKNOWN"

            # Get leverage info for the symbol
            leverage_info = exchange_leverage_info.get(symbol)
            leverage_str = "N/A"
            if leverage_info:
                if hasattr(leverage_info, 'max_leverage') and leverage_info.max_leverage:
                    leverage_str = f"{leverage_info.max_leverage}x"
                elif hasattr(leverage_info, 'margin_requirement') and leverage_info.margin_requirement:
                    calculated_leverage = Decimal("1") / leverage_info.margin_requirement
                    leverage_str = f"{calculated_leverage:.1f}x"

            # Call notification service
            await self.notification_service.notify_insufficient_margin(
                symbol=symbol,
                exchange_name=exchange_name,
                available_balance=available_balance,
                required_margin=required_margin,
                leverage_info=leverage_str
            )
        except Exception as exc:
            # Don't fail the preflight check if notification fails
            self.logger.debug(f"Could not send insufficient margin notification: {exc}")

