"""Order building for position closing."""

from decimal import Decimal
from typing import TYPE_CHECKING, Any, Dict, Optional

from strategies.execution.patterns.atomic_multi_order import OrderSpec

from ..core.contract_preparer import ContractPreparer
from ..core.price_utils import extract_snapshot_price, fetch_mid_price

if TYPE_CHECKING:
    from ...strategy import FundingArbitrageStrategy


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
        
        if order_type:
            use_market = order_type.lower() == "market"
        else:
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

