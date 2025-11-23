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
    
    def check_liquidation_risk(
        self,
        position: "FundingArbPosition",
        snapshots: Dict[str, Optional["ExchangePositionSnapshot"]],
    ) -> Optional[str]:
        """
        Check if position is at risk of liquidation and should be closed proactively.
        
        Args:
            position: Position to check
            snapshots: Exchange snapshots
            
        Returns:
            Liquidation risk reason string if risk detected, None otherwise
        """
        strategy = self._strategy
        risk_config = strategy.config.risk_config
        
        # Check if liquidation prevention is enabled
        if not getattr(risk_config, "enable_liquidation_prevention", True):
            return None
        
        min_distance_pct = getattr(risk_config, "min_liquidation_distance_pct", Decimal("0.05"))
        
        for dex in [position.long_dex, position.short_dex]:
            snapshot = snapshots.get(dex) or snapshots.get(dex.lower())
            if not snapshot:
                continue
            
            liquidation_price = snapshot.liquidation_price
            mark_price = snapshot.mark_price
            
            # Skip if missing required data
            if liquidation_price is None or mark_price is None:
                continue
            
            # Determine side
            side = snapshot.side
            if side is None:
                if snapshot.quantity is not None:
                    side = "long" if snapshot.quantity > 0 else "short"
                else:
                    # Fallback: use position metadata
                    if dex == position.long_dex:
                        side = "long"
                    else:
                        side = "short"
            
            # Calculate distance to liquidation
            distance_pct = self._calculate_liquidation_distance(
                mark_price, liquidation_price, side
            )
            
            if distance_pct is None:
                continue
            
            # Check if too close to liquidation
            if distance_pct < min_distance_pct:
                self._strategy.logger.warning(
                    f"⚠️ Liquidation risk detected for {position.symbol} on {dex.upper()}: "
                    f"distance={distance_pct*100:.2f}% < threshold={min_distance_pct*100:.2f}% "
                    f"(mark=${mark_price:.6f}, liquidation=${liquidation_price:.6f})"
                )
                return f"LIQUIDATION_RISK_{dex.upper()}"
        
        return None
    
    def _calculate_liquidation_distance(
        self,
        mark_price: Decimal,
        liquidation_price: Decimal,
        side: str,
    ) -> Optional[Decimal]:
        """
        Calculate distance to liquidation as a percentage.
        
        Args:
            mark_price: Current mark price
            liquidation_price: Liquidation price
            side: Position side ("long" or "short")
            
        Returns:
            Distance percentage (0.05 = 5%), or None if calculation fails
        """
        if mark_price <= 0 or liquidation_price <= 0:
            return None
        
        try:
            if side == "long":
                # Long: liquidation_price < mark_price
                # Distance = (mark_price - liquidation_price) / mark_price
                if mark_price <= liquidation_price:
                    # Already at or past liquidation
                    return Decimal("0")
                distance = (mark_price - liquidation_price) / mark_price
            else:  # short
                # Short: liquidation_price > mark_price
                # Distance = (liquidation_price - mark_price) / mark_price
                if mark_price >= liquidation_price:
                    # Already at or past liquidation
                    return Decimal("0")
                distance = (liquidation_price - mark_price) / mark_price
            
            return distance if distance >= 0 else None
        except Exception:
            return None
    
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
    
    def can_exit_at_break_even(
        self,
        position: "FundingArbPosition",
        snapshots: Dict[str, Optional["ExchangePositionSnapshot"]],
    ) -> bool:
        """
        Check if position can exit at break-even or better.
        
        Uses unrealized PnL from snapshots to determine if net exit would be break-even
        after accounting for estimated closing fees.
        
        Args:
            position: Position to check
            snapshots: Exchange snapshots with unrealized PnL
            
        Returns:
            True if net unrealized PnL >= estimated closing fees (break-even or better)
        """
        strategy = self._strategy
        
        # Get unrealized PnL from snapshots for both legs
        total_unrealized_pnl = Decimal("0")
        has_unrealized_data = False
        
        for dex in [position.long_dex, position.short_dex]:
            snapshot = snapshots.get(dex) or snapshots.get(dex.lower())
            if not snapshot:
                continue
            
            if snapshot.unrealized_pnl is not None:
                total_unrealized_pnl += to_decimal(snapshot.unrealized_pnl)
                has_unrealized_data = True
            elif snapshot.mark_price is not None and snapshot.entry_price is not None:
                # Fallback: calculate from entry_price and mark_price
                entry_price = to_decimal(snapshot.entry_price)
                mark_price = to_decimal(snapshot.mark_price)
                quantity = snapshot.quantity.copy_abs() if snapshot.quantity else Decimal("0")
                
                if quantity > 0:
                    side = snapshot.side or ("long" if dex == position.long_dex else "short")
                    if side == "long":
                        leg_pnl = (mark_price - entry_price) * quantity
                    else:  # short
                        leg_pnl = (entry_price - mark_price) * quantity
                    total_unrealized_pnl += leg_pnl
                    has_unrealized_data = True
        
        if not has_unrealized_data:
            # If we can't determine unrealized PnL, assume we can't exit at break-even
            strategy.logger.debug(
                f"Cannot determine break-even for {position.symbol}: missing unrealized PnL data"
            )
            return False
        
        # Estimate closing fees (using limit orders for better execution)
        estimated_closing_fees = Decimal("0")
        try:
            fee_calculator = getattr(strategy, "fee_calculator", None)
            if fee_calculator:
                for dex in [position.long_dex, position.short_dex]:
                    snapshot = snapshots.get(dex) or snapshots.get(dex.lower())
                    if not snapshot:
                        continue
                    
                    quantity = snapshot.quantity.copy_abs() if snapshot.quantity else Decimal("0")
                    mark_price = snapshot.mark_price
                    if quantity > 0 and mark_price:
                        mark_price_decimal = to_decimal(mark_price)
                        order_value_usd = quantity * mark_price_decimal
                        
                        fee_structure = fee_calculator.get_fee_structure(dex)
                        # Use maker fee (limit orders) for estimation
                        fee_rate = fee_structure.maker_fee
                        estimated_closing_fees += order_value_usd * to_decimal(fee_rate)
        except Exception as exc:
            strategy.logger.warning(
                f"Failed to estimate closing fees for {position.symbol}: {exc}. "
                f"Assuming break-even check passes."
            )
            # If we can't estimate fees, be conservative and assume we can exit
            return True
        
        # Break-even if total_unrealized_pnl >= estimated_closing_fees
        can_exit = total_unrealized_pnl >= estimated_closing_fees
        
        strategy.logger.debug(
            f"Break-even check for {position.symbol}: "
            f"unrealized_pnl=${total_unrealized_pnl:.2f}, "
            f"estimated_closing_fees=${estimated_closing_fees:.2f}, "
            f"can_exit={can_exit}"
        )
        
        return can_exit
    
    async def check_immediate_profit_opportunity(
        self,
        position: "FundingArbPosition",
        snapshots: Dict[str, Optional["ExchangePositionSnapshot"]],
    ) -> Tuple[bool, Optional[str]]:
        """
        Check if position can be closed at a profit RIGHT NOW.

        This captures cross-exchange basis spread opportunities by:
        1. Calculating net unrealized PnL from current snapshots
        2. Estimating closing fees (using maker fees for limit orders)
        3. Checking if net profit > 0

        No other safety checks - we're closing the position, so:
        - Liquidation risk disappears (both legs closed)
        - Funding doesn't matter (no longer in position)
        - Min hold time irrelevant (profit is profit)

        Returns:
            Tuple[bool, Optional[str]]: (should_close, reason)
        """
        strategy = self._strategy

        # Check if feature is enabled
        if not getattr(strategy.config, "enable_immediate_profit_taking", True):
            return False, None

        # Get snapshots for both legs
        long_snapshot = snapshots.get(position.long_dex)
        short_snapshot = snapshots.get(position.short_dex)

        if not long_snapshot or not short_snapshot:
            strategy.logger.warning(
                f"Missing snapshot for profit check on {position.symbol}: "
                f"long={long_snapshot is not None}, short={short_snapshot is not None}"
            )
            return False, None

        # Calculate current unrealized PnL (including funding accrued)
        # For funding arb, funding_accrued is a PRIMARY profit source
        long_price_pnl = to_decimal(long_snapshot.unrealized_pnl) if long_snapshot.unrealized_pnl is not None else Decimal("0")
        short_price_pnl = to_decimal(short_snapshot.unrealized_pnl) if short_snapshot.unrealized_pnl is not None else Decimal("0")

        long_funding = to_decimal(long_snapshot.funding_accrued) if long_snapshot.funding_accrued is not None else Decimal("0")
        short_funding = to_decimal(short_snapshot.funding_accrued) if short_snapshot.funding_accrued is not None else Decimal("0")

        long_pnl = long_price_pnl + long_funding
        short_pnl = short_price_pnl + short_funding
        net_unrealized_pnl = long_pnl + short_pnl

        # Estimate closing fees (using maker fees since we'll use limit orders)
        estimated_closing_fees = self._estimate_closing_fees_maker(position, snapshots)

        # Calculate net profit
        net_profit = net_unrealized_pnl - estimated_closing_fees

        # Validate position size
        if position.size_usd <= 0:
            strategy.logger.warning(
                f"Invalid position size for {position.symbol}: {position.size_usd}. Skipping profit check."
            )
            return False, None

        # Profit threshold: minimum 0.2% of position notional value
        # Get threshold from config or default to 0.2%
        min_profit_pct = getattr(strategy.config, "min_immediate_profit_taking_pct", Decimal("0.002"))  # 0.2%
        min_profit_threshold = position.size_usd * min_profit_pct

        if net_profit > min_profit_threshold:
            # Calculate profit percentage for logging
            profit_pct = (net_profit / position.size_usd * Decimal("100")) if position.size_usd > 0 else Decimal("0")

            # Log profit opportunity details
            strategy.logger.info(
                f"💰 Immediate profit opportunity for {position.symbol} (age: {position.get_age_hours()*60:.1f}m): "
                f"net_profit=${net_profit:.2f} ({profit_pct:.3f}%), threshold=${min_profit_threshold:.2f}, "
                f"long_pnl=${long_pnl:.2f}, short_pnl=${short_pnl:.2f}, fees=${estimated_closing_fees:.2f}",
                extra={
                    "long_dex": position.long_dex,
                    "short_dex": position.short_dex,
                    "long_pnl": float(long_pnl),
                    "short_pnl": float(short_pnl),
                    "net_unrealized_pnl": float(net_unrealized_pnl),
                    "estimated_closing_fees": float(estimated_closing_fees),
                    "net_profit": float(net_profit),
                    "net_profit_pct": float(profit_pct),
                    "long_mark_price": float(long_snapshot.mark_price) if long_snapshot.mark_price else None,
                    "short_mark_price": float(short_snapshot.mark_price) if short_snapshot.mark_price else None,
                    "position_age_minutes": position.get_age_hours() * 60,
                }
            )

            reason = f"IMMEDIATE_PROFIT: ${net_profit:.2f} ({profit_pct:.3f}%)"
            return True, reason

        return False, None

    def _estimate_closing_fees_maker(
        self,
        position: "FundingArbPosition",
        snapshots: Dict[str, Optional["ExchangePositionSnapshot"]],
    ) -> Decimal:
        """
        Estimate closing fees using MAKER fees (since we'll use limit orders).

        Returns total fees for closing both legs with limit orders.
        """
        strategy = self._strategy
        total_fees = Decimal("0")

        for dex in [position.long_dex, position.short_dex]:
            snapshot = snapshots.get(dex)
            if not snapshot:
                continue

            # Get fee structure for this exchange
            client = strategy.exchange_clients.get(dex)
            if not client:
                continue

            fee_structure = getattr(client, 'fee_structure', None)
            if not fee_structure:
                # Fallback: assume 0.02% maker fee (typical for most DEXs)
                maker_fee = Decimal("0.0002")
            else:
                maker_fee = to_decimal(fee_structure.maker_fee)

            # Calculate fee for this leg
            # order_value = mark_price * quantity
            if snapshot.mark_price and snapshot.quantity:
                mark_price = to_decimal(snapshot.mark_price)
                quantity = snapshot.quantity.copy_abs()
                order_value_usd = mark_price * quantity
                leg_fee = order_value_usd * maker_fee
                total_fees += leg_fee

        return total_fees

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

