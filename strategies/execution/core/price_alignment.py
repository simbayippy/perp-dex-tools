"""
Price Alignment Utility for Break-Even Delta-Neutral Positions

Provides utilities to ensure break-even pricing (long_entry < short_entry) for
delta-neutral positions across multiple exchanges.

Key Features:
- Option 3 (min mid prices) for initial entry alignment
- Feasibility-checked break-even for hedge operations
- Post-only protection validation
- Spread threshold checks
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Tuple

from helpers.unified_logger import get_core_logger

logger = get_core_logger("price_alignment")


@dataclass
class AlignedPrices:
    """Result of price alignment calculation."""
    long_price: Decimal
    short_price: Decimal
    strategy_used: str  # "aligned", "bbo_fallback", "post_only_adjusted"
    spread_pct: Decimal
    long_mid: Decimal
    short_mid: Decimal


class BreakEvenPriceAligner:
    """
    Utility class for calculating break-even aligned prices.
    
    Ensures long_entry < short_entry for delta-neutral positions while
    maintaining fill probability and post-only compliance.
    """
    
    DEFAULT_MAX_SPREAD_PCT = Decimal("0.005")  # 0.5%
    DEFAULT_MAX_DEVIATION_PCT = Decimal("0.005")  # 0.5%
    DEFAULT_OFFSET_RATIO = Decimal("0.25")  # 25% of spread
    
    @staticmethod
    def calculate_aligned_prices(
        long_bid: Decimal,
        long_ask: Decimal,
        short_bid: Decimal,
        short_ask: Decimal,
        limit_offset_pct: Optional[Decimal] = None,
        max_spread_threshold_pct: Optional[Decimal] = None,
    ) -> AlignedPrices:
        """
        Calculate aligned prices using Option 3 (min mid prices) strategy.
        
        Strategy:
        1. Calculate mid prices for both exchanges
        2. Find minimum mid price
        3. Use min_mid ± offset to ensure long < short
        4. Validate post-only compliance
        5. Fallback to BBO-based if spread too wide or validation fails
        
        Args:
            long_bid: Long exchange best bid
            long_ask: Long exchange best ask
            short_bid: Short exchange best bid
            short_ask: Short exchange best ask
            limit_offset_pct: Price offset percentage (e.g., 0.0001 for 1bp)
            max_spread_threshold_pct: Max spread % to use aligned pricing (default: 0.5%)
            
        Returns:
            AlignedPrices with calculated prices and strategy used
        """
        if limit_offset_pct is None:
            limit_offset_pct = Decimal("0.0001")  # Default 1bp
        
        if max_spread_threshold_pct is None:
            max_spread_threshold_pct = BreakEvenPriceAligner.DEFAULT_MAX_SPREAD_PCT
        
        # Calculate mid prices
        long_mid = (long_bid + long_ask) / 2
        short_mid = (short_bid + short_ask) / 2
        min_mid = min(long_mid, short_mid)
        max_mid = max(long_mid, short_mid)
        
        # Calculate spread percentage
        spread = max_mid - min_mid
        spread_pct = spread / min_mid if min_mid > 0 else Decimal("0")
        
        # Check if spread is acceptable
        if spread_pct > max_spread_threshold_pct:
            # Spread too wide, use BBO-based pricing
            long_price = long_ask
            short_price = short_bid
            logger.debug(
                f"Using BBO-based pricing (spread {spread_pct*100:.2f}% > threshold {max_spread_threshold_pct*100:.2f}%): "
                f"long={long_price:.6f}, short={short_price:.6f}"
            )
            return AlignedPrices(
                long_price=long_price,
                short_price=short_price,
                strategy_used="bbo_fallback",
                spread_pct=spread_pct,
                long_mid=long_mid,
                short_mid=short_mid,
            )
        
        # Use Option 3: min mid with offset
        offset = spread * BreakEvenPriceAligner.DEFAULT_OFFSET_RATIO
        
        # Calculate initial aligned prices
        long_price_candidate = min_mid - offset
        short_price_candidate = min_mid + offset
        
        # Validate and adjust for post-only protection
        long_price, short_price = BreakEvenPriceAligner._validate_post_only(
            long_price_candidate,
            short_price_candidate,
            long_bid,
            long_ask,
            short_bid,
            short_ask,
            limit_offset_pct,
        )
        
        # Final check: ensure long < short
        if long_price >= short_price:
            # Still not break-even, use BBO-based
            logger.warning(
                f"Price validation failed (long {long_price:.6f} >= short {short_price:.6f}). "
                f"Using BBO-based pricing."
            )
            long_price = long_ask
            short_price = short_bid
            strategy_used = "bbo_fallback"
        else:
            strategy_used = "aligned" if (long_price == long_price_candidate and short_price == short_price_candidate) else "post_only_adjusted"
        
        logger.debug(
            f"Aligned prices (spread {spread_pct*100:.2f}%): "
            f"long={long_price:.6f} < short={short_price:.6f} (strategy: {strategy_used})"
        )
        
        return AlignedPrices(
            long_price=long_price,
            short_price=short_price,
            strategy_used=strategy_used,
            spread_pct=spread_pct,
            long_mid=long_mid,
            short_mid=short_mid,
        )
    
    @staticmethod
    def _validate_post_only(
        long_price: Decimal,
        short_price: Decimal,
        long_bid: Decimal,
        long_ask: Decimal,
        short_bid: Decimal,
        short_ask: Decimal,
        limit_offset_pct: Decimal,
    ) -> Tuple[Decimal, Decimal]:
        """
        Validate and adjust prices to ensure post-only compliance.
        
        Rules:
        - Buy orders: price must be <= bid (or < ask with offset)
        - Sell orders: price must be >= ask (or > bid with offset)
        
        Args:
            long_price: Candidate long price
            short_price: Candidate short price
            long_bid: Long exchange bid
            long_ask: Long exchange ask
            short_bid: Short exchange bid
            short_ask: Short exchange ask
            limit_offset_pct: Price offset percentage
            
        Returns:
            Tuple of (adjusted_long_price, adjusted_short_price)
        """
        adjusted_long = long_price
        adjusted_short = short_price
        
        # For long (buy): ensure price <= bid (safe from post-only)
        if long_price > long_bid:
            # Too high, adjust to bid - offset
            adjusted_long = long_bid * (Decimal('1') - limit_offset_pct)
            logger.debug(
                f"Adjusted long_price from {long_price:.6f} to {adjusted_long:.6f} "
                f"(to avoid post-only, bid={long_bid:.6f})"
            )
        
        # For short (sell): ensure price >= ask (safe from post-only)
        if short_price < short_ask:
            # Too low, adjust to ask + offset
            adjusted_short = short_ask * (Decimal('1') + limit_offset_pct)
            logger.debug(
                f"Adjusted short_price from {short_price:.6f} to {adjusted_short:.6f} "
                f"(to avoid post-only, ask={short_ask:.6f})"
            )
        
        return adjusted_long, adjusted_short
    
    @staticmethod
    def calculate_break_even_hedge_price(
        trigger_fill_price: Decimal,
        trigger_side: str,
        hedge_bid: Decimal,
        hedge_ask: Decimal,
        hedge_side: str,
        tick_size: Decimal,
        max_deviation_pct: Optional[Decimal] = None,
    ) -> Tuple[Decimal, str]:
        """
        Calculate break-even hedge price relative to trigger fill price.
        
        Ensures break-even (long_entry < short_entry) if feasible given current
        market conditions. Falls back to BBO-based pricing if market moved too much.
        
        Args:
            trigger_fill_price: Price at which trigger order filled
            trigger_side: Side of trigger order ("buy" or "sell")
            hedge_bid: Current hedge exchange bid
            hedge_ask: Current hedge exchange ask
            hedge_side: Side of hedge order ("buy" or "sell")
            tick_size: Exchange tick size
            max_deviation_pct: Max market movement % to attempt break-even (default: 0.5%)
            
        Returns:
            Tuple of (hedge_price, strategy_used)
            strategy_used: "break_even", "bbo_based", or "bbo_fallback"
        """
        if max_deviation_pct is None:
            max_deviation_pct = BreakEvenPriceAligner.DEFAULT_MAX_DEVIATION_PCT
        
        # Calculate BBO-based price (fallback)
        if hedge_side == "buy":
            bbo_price = hedge_ask - tick_size
        else:
            bbo_price = hedge_bid + tick_size
        
        # Calculate break-even target
        if trigger_side == "buy" and hedge_side == "sell":
            # Long filled, hedging short
            # Need: short_entry < long_entry (break-even)
            break_even_target = trigger_fill_price * (Decimal('1') - Decimal('0.0001'))
            
            # Check feasibility: Is break-even target fillable?
            if break_even_target >= hedge_bid:
                # Check market movement
                current_mid = (hedge_bid + hedge_ask) / 2
                deviation_pct = abs(break_even_target - current_mid) / current_mid if current_mid > 0 else Decimal("0")
                
                if deviation_pct <= max_deviation_pct:
                    # Market stable, use break-even price
                    logger.info(
                        f"Using break-even hedge price: {break_even_target:.6f} < trigger {trigger_fill_price:.6f} "
                        f"(deviation: {deviation_pct*100:.2f}%)"
                    )
                    return break_even_target, "break_even"
                else:
                    # Market moved too much
                    logger.warning(
                        f"Market moved {deviation_pct*100:.2f}% since fill. "
                        f"Using BBO-based price {bbo_price:.6f} for fill probability. "
                        f"(Break-even target {break_even_target:.6f} would be stale)"
                    )
                    return bbo_price, "bbo_fallback"
            else:
                # Break-even target below bid (unfillable)
                logger.warning(
                    f"Break-even target {break_even_target:.6f} < current bid {hedge_bid:.6f}. "
                    f"Using BBO-based price {bbo_price:.6f} for fill probability."
                )
                return bbo_price, "bbo_fallback"
        
        elif trigger_side == "sell" and hedge_side == "buy":
            # Short filled, hedging long
            # Need: long_entry < short_entry (break-even)
            break_even_target = trigger_fill_price * (Decimal('1') - Decimal('0.0001'))
            
            # Check feasibility: Is break-even target fillable?
            if break_even_target <= hedge_ask:
                # Check market movement
                current_mid = (hedge_bid + hedge_ask) / 2
                deviation_pct = abs(break_even_target - current_mid) / current_mid if current_mid > 0 else Decimal("0")
                
                if deviation_pct <= max_deviation_pct:
                    # Market stable, use break-even price
                    logger.info(
                        f"Using break-even hedge price: {break_even_target:.6f} < trigger {trigger_fill_price:.6f} "
                        f"(deviation: {deviation_pct*100:.2f}%)"
                    )
                    return break_even_target, "break_even"
                else:
                    # Market moved too much
                    logger.warning(
                        f"Market moved {deviation_pct*100:.2f}% since fill. "
                        f"Using BBO-based price {bbo_price:.6f} for fill probability. "
                        f"(Break-even target {break_even_target:.6f} would be stale)"
                    )
                    return bbo_price, "bbo_fallback"
            else:
                # Break-even target above ask (unfillable)
                logger.warning(
                    f"Break-even target {break_even_target:.6f} > current ask {hedge_ask:.6f}. "
                    f"Using BBO-based price {bbo_price:.6f} for fill probability."
                )
                return bbo_price, "bbo_fallback"
        
        else:
            # Same side or invalid combination, use BBO-based
            logger.debug(
                f"Invalid trigger/hedge side combination ({trigger_side}/{hedge_side}). "
                f"Using BBO-based price {bbo_price:.6f}."
            )
            return bbo_price, "bbo_based"

