"""
Profit opportunity evaluation for funding arbitrage positions.

Evaluates whether a position can be closed profitably by capturing
cross-exchange basis spread opportunities (mean-reversion trading).
"""

from decimal import Decimal
from typing import TYPE_CHECKING, Dict, Optional, Tuple

from ...core.decimal_utils import to_decimal

if TYPE_CHECKING:
    from exchange_clients.base_models import ExchangePositionSnapshot
    from exchange_clients.base_websocket import BBOData
    from ...models import FundingArbPosition
    from ...strategy import FundingArbitrageStrategy


class ProfitEvaluator:
    """
    Evaluates immediate profit opportunities for open positions.

    Focuses purely on profitability analysis - no risk management concerns.
    """

    def __init__(self, strategy: "FundingArbitrageStrategy") -> None:
        """
        Initialize profit evaluator.

        Args:
            strategy: Parent funding arbitrage strategy instance
        """
        self._strategy = strategy
        self._logger = strategy.logger

    async def check_immediate_profit_opportunity(
        self,
        position: "FundingArbPosition",
        snapshots: Dict[str, Optional["ExchangePositionSnapshot"]],
        bbo_prices: Optional[Dict[str, "BBOData"]] = None,
    ) -> Tuple[bool, Optional[str]]:
        """
        Check if position can be closed at a profit RIGHT NOW.

        This captures cross-exchange basis spread opportunities by:
        1. Calculating net unrealized PnL (using fresh BBO if available, else snapshots)
        2. Estimating closing fees (using maker fees for limit orders)
        3. Checking if net profit > minimum threshold

        No risk management checks - purely profit opportunity detection.

        Args:
            position: Position to evaluate
            snapshots: Exchange snapshots with current position data
            bbo_prices: Optional fresh BBO prices {exchange: BBOData} for accurate pricing

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

        # Calculate current unrealized PnL (using fresh BBO if available, else snapshot mark prices)
        # For funding arb, funding_accrued is a PRIMARY profit source
        if bbo_prices:
            # Use FRESH BBO prices for accurate profit calculation
            long_price_pnl, short_price_pnl = self._calculate_fresh_pnl_from_bbo(
                position, bbo_prices, long_snapshot, short_snapshot
            )
            pricing_source = "fresh BBO"
        else:
            # Fallback: use stale snapshot unrealized_pnl
            long_price_pnl = to_decimal(long_snapshot.unrealized_pnl) if long_snapshot.unrealized_pnl is not None else Decimal("0")
            short_price_pnl = to_decimal(short_snapshot.unrealized_pnl) if short_snapshot.unrealized_pnl is not None else Decimal("0")
            pricing_source = "snapshot (stale)"

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
                f"💰 Immediate profit opportunity for {position.symbol} (age: {position.get_age_hours()*60:.1f}m, pricing: {pricing_source}): "
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

        Args:
            position: Position to estimate fees for
            snapshots: Exchange snapshots with position data

        Returns:
            Total estimated closing fees
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

    def _calculate_fresh_pnl_from_bbo(
        self,
        position: "FundingArbPosition",
        bbo_prices: Dict[str, "BBOData"],
        long_snapshot: "ExchangePositionSnapshot",
        short_snapshot: "ExchangePositionSnapshot",
    ) -> Tuple[Decimal, Decimal]:
        """
        Calculate unrealized PnL using fresh BBO prices.

        For closing positions:
        - LONG leg: Sell at BID price (what buyers will pay us)
        - SHORT leg: Buy at ASK price (what sellers charge us)

        Args:
            position: Position to calculate PnL for
            bbo_prices: Fresh BBO prices {exchange: BBOData}
            long_snapshot: Snapshot for long leg (for entry price, quantity)
            short_snapshot: Snapshot for short leg (for entry price, quantity)

        Returns:
            Tuple[Decimal, Decimal]: (long_price_pnl, short_price_pnl)
        """
        long_price_pnl = Decimal("0")
        short_price_pnl = Decimal("0")

        # Calculate LONG leg PnL using fresh BID price
        long_bbo = bbo_prices.get(position.long_dex)
        if long_bbo and long_bbo.bid and long_snapshot.entry_price and long_snapshot.quantity:
            entry_price = to_decimal(long_snapshot.entry_price)
            current_price = to_decimal(long_bbo.bid)  # Sell at BID
            quantity = long_snapshot.quantity.copy_abs()

            # Long PnL = (current_price - entry_price) * quantity
            long_price_pnl = (current_price - entry_price) * quantity

            self._logger.debug(
                f"[FRESH_BBO] Long leg PnL for {position.symbol}: "
                f"bid=${current_price:.6f}, entry=${entry_price:.6f}, qty={quantity:.4f}, pnl=${long_price_pnl:.2f}"
            )
        else:
            # Fallback to snapshot unrealized_pnl if BBO not available
            long_price_pnl = to_decimal(long_snapshot.unrealized_pnl) if long_snapshot.unrealized_pnl is not None else Decimal("0")
            self._logger.debug(
                f"[FRESH_BBO] Long leg using snapshot PnL (no BBO): ${long_price_pnl:.2f}"
            )

        # Calculate SHORT leg PnL using fresh ASK price
        short_bbo = bbo_prices.get(position.short_dex)
        if short_bbo and short_bbo.ask and short_snapshot.entry_price and short_snapshot.quantity:
            entry_price = to_decimal(short_snapshot.entry_price)
            current_price = to_decimal(short_bbo.ask)  # Buy at ASK
            quantity = short_snapshot.quantity.copy_abs()

            # Short PnL = (entry_price - current_price) * quantity
            short_price_pnl = (entry_price - current_price) * quantity

            self._logger.debug(
                f"[FRESH_BBO] Short leg PnL for {position.symbol}: "
                f"ask=${current_price:.6f}, entry=${entry_price:.6f}, qty={quantity:.4f}, pnl=${short_price_pnl:.2f}"
            )
        else:
            # Fallback to snapshot unrealized_pnl if BBO not available
            short_price_pnl = to_decimal(short_snapshot.unrealized_pnl) if short_snapshot.unrealized_pnl is not None else Decimal("0")
            self._logger.debug(
                f"[FRESH_BBO] Short leg using snapshot PnL (no BBO): ${short_price_pnl:.2f}"
            )

        return long_price_pnl, short_price_pnl
