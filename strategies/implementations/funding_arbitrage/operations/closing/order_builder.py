"""Order building for position closing."""

from decimal import Decimal
from typing import TYPE_CHECKING, Any, Dict, Optional

from strategies.execution.patterns.atomic_multi_order import OrderSpec

from ..core.contract_preparer import ContractPreparer
from ..core.price_utils import (
    extract_snapshot_price,
    fetch_mid_price,
)
from strategies.execution.core.spread_utils import SpreadCheckType, is_spread_acceptable

if TYPE_CHECKING:
    from ...strategy import FundingArbitrageStrategy


class WideSpreadException(Exception):
    """Exception raised when spread is too wide for non-critical exits."""
    
    def __init__(self, spread_pct: Decimal, bid: Decimal, ask: Decimal, exchange: str, symbol: str):
        self.spread_pct = spread_pct
        self.bid = bid
        self.ask = ask
        self.exchange = exchange
        self.symbol = symbol
        super().__init__(
            f"Wide spread detected on {exchange} {symbol}: {spread_pct*100:.2f}% "
            f"(bid={bid}, ask={ask}). Deferring close."
        )


class OrderBuilder:
    """Builds order specifications for closing positions."""
    
    def __init__(self, strategy: "FundingArbitrageStrategy"):
        self._strategy = strategy
        self._contract_preparer = ContractPreparer()
    
    async def build_order_spec(
        self,
        symbol: str,
        leg: Dict[str, Any],
        reason: str = "UNKNOWN",
        order_type: Optional[str] = None,
    ) -> OrderSpec:
        """
        Build an order spec for closing a leg.
        
        Args:
            symbol: Trading symbol
            leg: Leg dictionary with client, snapshot, side, quantity, etc.
            reason: Reason for closing
            order_type: Optional order type override ("market" or "limit")
            
        Returns:
            OrderSpec for the close order
        """
        leg["contract_id"] = await self._contract_preparer.prepare_contract_context(
            leg["client"],
            symbol,
            metadata=leg.get("metadata") or {},
            contract_hint=leg.get("contract_id"),
            logger=self._strategy.logger,
        )
        price = extract_snapshot_price(leg["snapshot"])
        if price is None or price <= Decimal("0"):
            price = await fetch_mid_price(leg["client"], symbol, self._strategy.logger)

        if price is None or price <= Decimal("0"):
            raise RuntimeError("Unable to determine price for close order")

        quantity = leg["quantity"]
        notional = quantity * price
        
        critical_reasons = {
            "SEVERE_IMBALANCE",
            "LEG_LIQUIDATED", 
            "LIQUIDATION_ASTER",
            "LIQUIDATION_LIGHTER",
            "LIQUIDATION_BACKPACK",
            "LIQUIDATION_PARADEX",
        }
        
        # Liquidation risk reasons (for future reference - currently uses explicit order_type="limit")
        liquidation_risk_reasons = {
            "LIQUIDATION_RISK_ASTER",
            "LIQUIDATION_RISK_LIGHTER",
            "LIQUIDATION_RISK_BACKPACK",
            "LIQUIDATION_RISK_PARADEX",
        }
        
        # Check spread if wide spread protection is enabled
        is_critical = reason in critical_reasons
        is_user_manual = order_type is not None
        
        if getattr(self._strategy.config, "enable_wide_spread_protection", True):
            try:
                price_provider = getattr(self._strategy, "price_provider", None)
                if price_provider:
                    client = leg.get("client")
                    exchange_name = client.get_exchange_name() if client else "UNKNOWN"
                    
                    try:
                        bid, ask = await price_provider.get_bbo_prices(client, symbol)

                        # Determine spread check type based on exit type
                        check_type = SpreadCheckType.EMERGENCY_CLOSE if is_critical else SpreadCheckType.EXIT
                        acceptable, spread_pct, reason_msg = is_spread_acceptable(bid, ask, check_type)

                        if not acceptable:
                            # For non-critical exits, defer closing
                            if not is_critical and not is_user_manual:
                                self._strategy.logger.info(
                                    f"⏸️  Wide spread detected on {exchange_name.upper()} {symbol}: "
                                    f"{spread_pct*100:.2f}% (bid={bid}, ask={ask}). "
                                    f"Deferring non-critical close (reason: {reason})."
                                )
                                raise WideSpreadException(spread_pct, bid, ask, exchange_name, symbol)
                            
                            # For critical exits or user manual, log warning but proceed
                            if is_critical:
                                self._strategy.logger.warning(
                                    f"⚠️  Wide spread detected on {exchange_name.upper()} {symbol}: "
                                    f"{spread_pct*100:.2f}% (bid={bid}, ask={ask}). "
                                    f"Proceeding with critical close (reason: {reason})."
                                )
                            elif is_user_manual:
                                self._strategy.logger.warning(
                                    f"⚠️  Wide spread detected on {exchange_name.upper()} {symbol}: "
                                    f"{spread_pct*100:.2f}% (bid={bid}, ask={ask}). "
                                    f"User requested {order_type} order - proceed with caution."
                                )
                    except WideSpreadException:
                        # Re-raise WideSpreadException to defer closing
                        raise
                    except Exception as exc:
                        # If BBO fetch fails, log warning and proceed with original logic
                        self._strategy.logger.warning(
                            f"⚠️  Failed to check spread for {symbol} on {exchange_name}: {exc}. "
                            f"Proceeding with original execution mode."
                        )
            except WideSpreadException:
                # Re-raise to propagate deferral
                raise
        
        if order_type:
            use_market = order_type.lower() == "market"
            use_aggressive_limit = order_type.lower() == "aggressive_limit"
        else:
            use_market = reason in critical_reasons
            use_aggressive_limit = False

        # Execution mode strategy for closing positions:
        #
        # INITIAL ATOMIC CLOSE (both legs placed simultaneously):
        # - Use limit_only for passive maker orders (ask - 0.01% for buy, bid + 0.01% for sell)
        # - Lower fees, better price execution
        # - Protected by spread check in LimitOrderExecutor (rejects if spread > 2%)
        # - If one side fills first → triggers aggressive_limit hedge (handled by HedgeManager)
        #
        # HEDGE (when one side fills first):
        # - Automatically uses aggressive_limit via HedgeManager.aggressive_limit_hedge()
        # - NOT controlled by this order_builder (hedge bypasses order_builder entirely)
        # - Adaptive pricing with break-even attempt, then touch → inside spread → cross spread
        # - Multiple retries with fallback to market
        #
        # CRITICAL EXITS (liquidation risk, severe imbalance):
        # - Use aggressive_limit for faster execution
        # - Speed is more important than optimal pricing
        #
        # PROFIT-TAKING EXITS (immediate profit opportunity):
        # - Use aggressive_limit for best execution quality
        # - Ensures profit is realized with maker fees (inside spread pricing)

        if use_market:
            # User explicitly requested market order or critical reason requires it
            execution_mode = "market_only"
        elif use_aggressive_limit or is_critical:
            # Aggressive limit: User requested OR critical exit
            # Uses adaptive pricing: inside spread → touch → cross spread
            execution_mode = "aggressive_limit"
        else:
            # Initial close attempts: Use limit_only (passive maker orders)
            # This is the first attempt to close both sides atomically
            # Hedge attempts are handled separately by HedgeManager (not through order_builder)
            execution_mode = "limit_only"
        
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
            reduce_only=True,
        )

    def _resolve_limit_offset_pct(self) -> Optional[Decimal]:
        """Resolve limit order offset percentage from config."""
        value = getattr(self._strategy.config, "limit_order_offset_pct", None)
        if value is None:
            return None
        if isinstance(value, Decimal):
            return value
        try:
            return Decimal(str(value))
        except Exception:
            return None

