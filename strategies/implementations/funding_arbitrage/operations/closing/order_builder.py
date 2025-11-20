"""Order building for position closing."""

from decimal import Decimal
from typing import TYPE_CHECKING, Any, Dict, Optional

from strategies.execution.patterns.atomic_multi_order import OrderSpec

from ..core.contract_preparer import ContractPreparer
from ..core.price_utils import (
    extract_snapshot_price,
    fetch_mid_price,
    calculate_spread_pct,
    MAX_EXIT_SPREAD_PCT,
    MAX_EMERGENCY_CLOSE_SPREAD_PCT,
)

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
                        spread_pct = calculate_spread_pct(bid, ask)
                        
                        if spread_pct is not None:
                            # Determine threshold based on exit type
                            threshold = MAX_EMERGENCY_CLOSE_SPREAD_PCT if is_critical else MAX_EXIT_SPREAD_PCT
                            
                            if spread_pct > threshold:
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
        else:
            use_market = reason in critical_reasons
        
        # For critical exits with wide spread, use aggressive_limit instead of market
        # For non-critical exits with acceptable spread, use aggressive_limit for better execution
        # Aggressive limit uses adaptive pricing (inside spread → touch → cross spread) for optimal fills
        if is_critical and not use_market:
            # Critical exits: use aggressive_limit for better execution with adaptive pricing
            execution_mode = "aggressive_limit"
        elif not use_market:
            # Non-critical exits: use aggressive_limit for better execution with adaptive pricing
            execution_mode = "aggressive_limit"
        else:
            execution_mode = "market_only"
        
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

