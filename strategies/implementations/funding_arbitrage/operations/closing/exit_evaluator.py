"""Exit condition evaluation for position closing."""

from decimal import Decimal
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple

from ..core.decimal_utils import to_decimal

if TYPE_CHECKING:
    from exchange_clients.base_models import ExchangePositionSnapshot
    from ...models import FundingArbPosition
    from ...strategy import FundingArbitrageStrategy


class ExitEvaluator:
    """Evaluates exit conditions for positions."""
    
    _ZERO_TOLERANCE = Decimal("0")
    _IMBALANCE_THRESHOLD = Decimal("0.05")  # 5% maximum allowed difference
    
    def __init__(self, strategy: "FundingArbitrageStrategy", risk_manager: Any):
        self._strategy = strategy
        self._risk_manager = risk_manager
    
    async def should_close(
        self,
        position: "FundingArbPosition",
        snapshots: Dict[str, Optional["ExchangePositionSnapshot"]],
        gather_current_rates: Any,
        should_skip_erosion_exit: Any,
    ) -> Tuple[bool, Optional[str]]:
        """
        Determine if a position should be closed.
        
        Args:
            position: Position to evaluate
            snapshots: Exchange snapshots
            gather_current_rates: Function to gather current funding rates
            should_skip_erosion_exit: Function to check if erosion exit should be skipped
            
        Returns:
            Tuple of (should_close, reason)
        """
        strategy = self._strategy
        age_hours = position.get_age_hours()
        min_hold_hours = getattr(strategy.config.risk_config, "min_hold_hours", 0) or 0

        if min_hold_hours > 0 and age_hours < min_hold_hours:
            return False, "MIN_HOLD_ACTIVE"

        current_rates = await gather_current_rates(position)
        if current_rates is not None and self._risk_manager is not None:
            try:
                should_exit, reason = self._risk_manager.should_exit(
                    position, current_rates
                )
                if should_exit:
                    if await should_skip_erosion_exit(position, reason):
                        return False, "HOLD_TOP_OPPORTUNITY"
                    return True, reason
            except Exception as exc:
                strategy.logger.error(
                    f"Risk manager evaluation failed for {position.symbol}: {exc}"
                )

        if position.current_divergence and position.current_divergence < 0:
            return True, "DIVERGENCE_FLIPPED"

        erosion = position.get_profit_erosion()
        if erosion < strategy.config.risk_config.min_erosion_threshold:
            if await should_skip_erosion_exit(position, "PROFIT_EROSION"):
                return False, "HOLD_TOP_OPPORTUNITY"
            return True, "PROFIT_EROSION"

        if age_hours > strategy.config.risk_config.max_position_age_hours:
            return True, "TIME_LIMIT"

        return False, None
    
    def detect_liquidation(
        self,
        position: "FundingArbPosition",
        snapshots: Dict[str, Optional["ExchangePositionSnapshot"]],
    ) -> Optional[str]:
        """
        Detect if either leg has been liquidated or otherwise removed.
        
        Args:
            position: Position to check
            snapshots: Exchange snapshots
            
        Returns:
            Liquidation reason string if detected, None otherwise
        """
        missing_legs = [
            dex
            for dex, snapshot in snapshots.items()
            if not self._has_open_position(snapshot)
        ]

        if not missing_legs:
            return None

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
    
    def detect_imbalance(
        self,
        position: "FundingArbPosition",
        snapshots: Dict[str, Optional["ExchangePositionSnapshot"]],
    ) -> Optional[str]:
        """
        Detect severe imbalance between legs (> 5% difference).
        
        Args:
            position: Position to check
            snapshots: Live snapshots from exchanges
            
        Returns:
            "SEVERE_IMBALANCE" if imbalance detected, None otherwise
        """
        long_tokens, short_tokens = self._extract_leg_quantities(position, snapshots)
        
        if long_tokens is None or short_tokens is None:
            return None
        
        if long_tokens <= self._ZERO_TOLERANCE or short_tokens <= self._ZERO_TOLERANCE:
            return None
        
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
        
        Returns:
            Tuple of (long_actual_tokens, short_actual_tokens) or (None, None) if unavailable
        """
        long_snapshot = snapshots.get(position.long_dex)
        short_snapshot = snapshots.get(position.short_dex)
        
        if not long_snapshot or not short_snapshot:
            return None, None
        
        if long_snapshot.quantity is None or short_snapshot.quantity is None:
            return None, None
        
        long_qty_exchange = long_snapshot.quantity.copy_abs()
        short_qty_exchange = short_snapshot.quantity.copy_abs()
        
        long_client = self._strategy.exchange_clients.get(position.long_dex)
        short_client = self._strategy.exchange_clients.get(position.short_dex)
        
        if not long_client or not short_client:
            return None, None
        
        long_multiplier = Decimal(str(long_client.get_quantity_multiplier(position.symbol)))
        short_multiplier = Decimal(str(short_client.get_quantity_multiplier(position.symbol)))
        
        long_actual_tokens = long_qty_exchange * long_multiplier
        short_actual_tokens = short_qty_exchange * short_multiplier
        
        return long_actual_tokens, short_actual_tokens
    
    @classmethod
    def _has_open_position(cls, snapshot: Optional["ExchangePositionSnapshot"]) -> bool:
        """Check if snapshot indicates an open position."""
        if snapshot is None or snapshot.quantity is None:
            return False
        return snapshot.quantity.copy_abs() > cls._ZERO_TOLERANCE
    
    async def should_skip_erosion_exit(
        self,
        position: "FundingArbPosition",
        trigger_reason: Optional[str],
        is_opportunity_tradeable: Any,
    ) -> bool:
        """
        Guard against closing/re-opening the same opportunity when erosion triggers.
        
        Args:
            position: Position to check
            trigger_reason: Reason for the trigger
            is_opportunity_tradeable: Function to check if opportunity is tradeable
            
        Returns:
            True if erosion exit should be skipped, False otherwise
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
            min_volume_24h=strategy.config.min_volume_24h,
            min_oi_usd=strategy.config.min_oi_usd,
            whitelist_dexes=whitelist_dexes,
            required_dex=required_dex,
            symbol=None,
            limit=10,
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

        best_tradeable = None
        for opp in opportunities:
            if await is_opportunity_tradeable(opp):
                best_tradeable = opp
                break
        
        if best_tradeable is None:
            strategy.logger.debug(
                f"No tradable opportunities found - proceeding with close for {position.symbol}"
            )
            return False

        try:
            net_profit = best_tradeable.net_profit_percent
        except AttributeError:
            net_profit = None

        if (
            best_tradeable
            and self._symbols_match(position.symbol, best_tradeable.symbol)
            and best_tradeable.long_dex.lower() == position.long_dex.lower()
            and best_tradeable.short_dex.lower() == position.short_dex.lower()
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
    
    @staticmethod
    def _symbols_match(position_symbol: Optional[str], event_symbol: Optional[str]) -> bool:
        """Check if two symbols match (handles variations like BTC vs BTCUSDT)."""
        pos_upper = (position_symbol or "").upper()
        event_upper = (event_symbol or "").upper()

        if not pos_upper or not event_upper:
            return False

        if pos_upper == event_upper:
            return True

        if event_upper.endswith(pos_upper) or event_upper.startswith(pos_upper):
            return True

        if pos_upper.endswith(event_upper) or pos_upper.startswith(event_upper):
            return True

        return False

