"""Hedge price calculation utilities for atomic multi-order execution."""

from __future__ import annotations

from decimal import Decimal
from typing import Optional, Tuple

from strategies.execution.core.price_alignment import BreakEvenPriceAligner

from ...contexts import OrderContext


class HedgePriceResult:
    """Result of hedge price calculation."""
    
    def __init__(
        self,
        best_bid: Decimal,
        best_ask: Decimal,
        limit_price: Decimal,
        pricing_strategy: str,
        break_even_strategy: Optional[str] = None
    ):
        self.best_bid = best_bid
        self.best_ask = best_ask
        self.limit_price = limit_price
        self.pricing_strategy = pricing_strategy
        self.break_even_strategy = break_even_strategy


class HedgePricer:
    """Calculates hedge prices using various strategies."""
    
    def __init__(self, price_provider=None):
        self._price_provider = price_provider
    
    async def calculate_aggressive_limit_price(
        self,
        spec,
        trigger_ctx: Optional[OrderContext],
        retry_count: int,
        inside_tick_retries: int,
        max_deviation_pct: Optional[Decimal],
        logger,
        exchange_name: str,
        symbol: str,
    ) -> HedgePriceResult:
        """
        Calculate hedge price using break-even or adaptive pricing strategy.
        
        Strategy:
        - First attempts break-even pricing relative to trigger fill price
        - Falls back to adaptive pricing (inside spread → touch)
        - Inside spread: 1 tick away from best bid/ask (safer, avoids post-only violations)
        - Touch: At best bid/ask (more aggressive)
        
        Args:
            spec: OrderSpec for the hedge order
            trigger_ctx: The context that triggered the hedge (fully filled order)
            retry_count: Current retry attempt (0-indexed)
            inside_tick_retries: Number of retries using "inside spread" pricing
            max_deviation_pct: Max market movement % to attempt break-even hedge
            logger: Logger instance
            exchange_name: Exchange name for logging
            symbol: Trading symbol
            
        Returns:
            HedgePriceResult with pricing details
        """
        # Fetch fresh BBO
        best_bid, best_ask = await self._price_provider.get_bbo_prices(
            spec.exchange_client, symbol
        )
        
        if best_bid <= Decimal("0") or best_ask <= Decimal("0"):
            raise ValueError(f"Invalid BBO for {exchange_name} {symbol}: bid={best_bid}, ask={best_ask}")
        
        # Get tick_size with fallback
        tick_size = getattr(spec.exchange_client.config, 'tick_size', None)
        if tick_size is None:
            # Fallback: use 0.01% of price (1 basis point)
            tick_size = best_ask * Decimal('0.0001')
        else:
            tick_size = Decimal(str(tick_size))
        
        # Attempt break-even pricing relative to trigger fill price
        limit_price = None
        pricing_strategy = None
        break_even_strategy = None
        
        # Get trigger fill price if available
        trigger_fill_price = None
        trigger_side = None
        if trigger_ctx and trigger_ctx.result:
            trigger_fill_price_raw = trigger_ctx.result.get("fill_price")
            if trigger_fill_price_raw:
                try:
                    trigger_fill_price = Decimal(str(trigger_fill_price_raw))
                    trigger_side = trigger_ctx.spec.side
                except (ValueError, TypeError):
                    pass
        
        # Try break-even pricing if trigger fill price available
        if trigger_fill_price and trigger_side:
            # Use provided max_deviation_pct or default (0.5%)
            if max_deviation_pct is None:
                max_deviation_pct = BreakEvenPriceAligner.DEFAULT_MAX_DEVIATION_PCT
            
            break_even_price, break_even_strategy = BreakEvenPriceAligner.calculate_break_even_hedge_price(
                trigger_fill_price=trigger_fill_price,
                trigger_side=trigger_side,
                hedge_bid=best_bid,
                hedge_ask=best_ask,
                hedge_side=spec.side,
                tick_size=tick_size,
                max_deviation_pct=max_deviation_pct,
            )
            
            if break_even_strategy == "break_even":
                # Use break-even price
                limit_price = break_even_price
                pricing_strategy = "break_even"
                # Determine comparison operator based on sides
                if trigger_side == "buy" and spec.side == "sell":
                    comparison = "<"  # short < long
                elif trigger_side == "sell" and spec.side == "buy":
                    comparison = "<"  # long < short
                else:
                    comparison = "?"
                
                logger.info(
                    f"✅ [{exchange_name}] Using break-even hedge price: {limit_price:.6f} "
                    f"{comparison} trigger {trigger_fill_price:.6f} for {symbol} "
                )
            else:
                # Break-even not feasible, use BBO-based adaptive pricing
                logger.debug(
                    f"ℹ️ [{exchange_name}] Break-even not feasible for {symbol}. "
                    f"Using BBO-based pricing to prioritize fill probability"
                )
        
        # If break-even not attempted or not feasible, use adaptive pricing strategy
        if limit_price is None:
            if retry_count < inside_tick_retries:
                # Start inside spread (1 tick away from touch)
                pricing_strategy = "inside_spread"
                if spec.side == "buy":
                    limit_price = best_ask - tick_size
                else:
                    limit_price = best_bid + tick_size
            else:
                # Move to touch (at best bid/ask)
                pricing_strategy = "touch"
                if spec.side == "buy":
                    limit_price = best_ask
                else:
                    limit_price = best_bid
        
        # Round price to tick size
        limit_price = spec.exchange_client.round_to_tick(limit_price)
        
        return HedgePriceResult(
            best_bid=best_bid,
            best_ask=best_ask,
            limit_price=limit_price,
            pricing_strategy=pricing_strategy,
            break_even_strategy=break_even_strategy
        )

