"""Hedge target calculation utilities for atomic multi-order execution."""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from ...contexts import OrderContext


class HedgeTarget:
    """Result of hedge target calculation."""
    
    def __init__(
        self,
        hedge_target: Decimal,
        remaining_qty: Decimal,
        used_multiplier_adjustment: bool = False
    ):
        self.hedge_target = hedge_target
        self.remaining_qty = remaining_qty
        self.used_multiplier_adjustment = used_multiplier_adjustment


class HedgeTargetCalculator:
    """Calculates and tracks hedge targets across exchanges with multiplier adjustments."""
    
    def calculate_hedge_target(
        self,
        trigger_ctx: OrderContext,
        target_ctx: OrderContext,
        logger
    ) -> Optional[HedgeTarget]:
        """
        Calculate hedge target quantity with multiplier adjustments.
        
        This handles cross-exchange quantity matching when exchanges use different
        quantity multipliers (e.g., Lighter kTOSHI vs Aster TOSHI).
        
        Args:
            trigger_ctx: The context that triggered the hedge (fully filled order)
            target_ctx: The context that needs to be hedged
            logger: Logger instance
            
        Returns:
            HedgeTarget if calculation successful, None if should skip
        """
        trigger_qty = trigger_ctx.filled_quantity
        if not isinstance(trigger_qty, Decimal):
            trigger_qty = Decimal(str(trigger_qty))
        trigger_qty = trigger_qty.copy_abs()
        
        # Account for quantity multipliers when matching across exchanges
        # Example: Lighter kTOSHI (84 units = 84k tokens) vs Aster TOSHI (84k units = 84k tokens)
        trigger_multiplier = trigger_ctx.spec.exchange_client.get_quantity_multiplier(
            trigger_ctx.spec.symbol
        )
        ctx_multiplier = target_ctx.spec.exchange_client.get_quantity_multiplier(
            target_ctx.spec.symbol
        )
        
        # Convert trigger quantity to "actual tokens" then to target exchange's units
        actual_tokens = trigger_qty * Decimal(str(trigger_multiplier))
        target_qty = actual_tokens / Decimal(str(ctx_multiplier))
        
        used_multiplier_adjustment = trigger_multiplier != ctx_multiplier
        
        if used_multiplier_adjustment:
            exchange_name = target_ctx.spec.exchange_client.get_exchange_name().upper()
            logger.debug(
                f"📊 Multiplier adjustment for {target_ctx.spec.symbol}: "
                f"trigger_qty={trigger_qty} (×{trigger_multiplier}) → "
                f"target_qty={target_qty} (×{ctx_multiplier})"
            )
        
        # Don't cap target_qty to spec.quantity when hedging after trigger fill
        # The trigger fill is the source of truth, and we need to match it exactly
        # (accounting for multipliers). spec.quantity might be from the original
        # order plan and could be wrong if there were rounding differences.
        # Only cap if target_qty exceeds spec.quantity significantly (safety check)
        spec_qty = getattr(target_ctx.spec, "quantity", None)
        if spec_qty is not None:
            spec_qty_dec = Decimal(str(spec_qty))
            # Only cap if target is significantly larger (more than 10% over)
            # This allows for small rounding differences but prevents huge errors
            if target_qty > spec_qty_dec * Decimal("1.1"):
                exchange_name = target_ctx.spec.exchange_client.get_exchange_name().upper()
                logger.warning(
                    f"⚠️ [HEDGE] Calculated hedge target {target_qty} exceeds "
                    f"spec quantity {spec_qty_dec} by >10%. Capping to spec quantity."
                )
                target_qty = spec_qty_dec
        
        if target_qty < Decimal("0"):
            target_qty = Decimal("0")
        
        # Store hedge_target_quantity for use in remaining quantity calculation
        target_ctx.hedge_target_quantity = target_qty
        
        # Calculate remaining quantity
        remaining_qty = self.calculate_remaining_quantity(
            target_ctx,
            target_qty
        )
        
        return HedgeTarget(
            hedge_target=target_qty,
            remaining_qty=remaining_qty,
            used_multiplier_adjustment=used_multiplier_adjustment
        )
    
    def calculate_remaining_quantity(
        self,
        ctx: OrderContext,
        hedge_target: Optional[Decimal] = None,
        accumulated_fills: Decimal = Decimal("0")
    ) -> Decimal:
        """
        Calculate remaining quantity after fills.
        
        Args:
            ctx: Order context
            hedge_target: Target quantity (if None, uses ctx.hedge_target_quantity or spec.quantity)
            accumulated_fills: Additional fills accumulated during hedge (for aggressive limit hedge)
            
        Returns:
            Remaining quantity to hedge
        """
        # Determine target quantity
        if hedge_target is not None:
            target_qty = hedge_target
        elif ctx.hedge_target_quantity is not None:
            target_qty = Decimal(str(ctx.hedge_target_quantity))
        else:
            # Fallback to spec.quantity
            spec_quantity = getattr(ctx.spec, "quantity", None)
            if spec_quantity is not None:
                target_qty = Decimal(str(spec_quantity))
            else:
                return Decimal("0")
        
        # Calculate remaining: target - (initial fills + accumulated fills)
        total_filled = ctx.filled_quantity + accumulated_fills
        remaining_qty = target_qty - total_filled
        
        return remaining_qty if remaining_qty > Decimal("0") else Decimal("0")
    
    def get_remaining_quantity_for_hedge(
        self,
        ctx: OrderContext,
        logger
    ) -> Decimal:
        """
        Get remaining quantity for hedge operation.
        
        This is the main entry point used by hedge methods.
        It prioritizes hedge_target_quantity if set, otherwise falls back to
        ctx.remaining_quantity property.
        
        Args:
            ctx: Order context
            logger: Logger instance
            
        Returns:
            Remaining quantity to hedge
        """
        exchange_name = ctx.spec.exchange_client.get_exchange_name().upper()
        symbol = ctx.spec.symbol
        
        # CRITICAL: When hedging after a trigger fill, prioritize hedge_target_quantity
        # This ensures we hedge the correct amount to match the trigger fill, accounting
        # for quantity multipliers across exchanges.
        # Example: Aster fills 233960 TOSHI → Lighter should hedge 233.96 (233960/1000)
        if ctx.hedge_target_quantity is not None:
            hedge_target = Decimal(str(ctx.hedge_target_quantity))
            remaining_qty = hedge_target - ctx.filled_quantity
            if remaining_qty < Decimal("0"):
                remaining_qty = Decimal("0")
            
            logger.debug(
                f"📊 [HEDGE] {exchange_name} {symbol}: "
                f"hedge_target={hedge_target}, filled={ctx.filled_quantity}, "
                f"remaining_qty={remaining_qty}"
            )
            return remaining_qty
        else:
            # Fallback to remaining_quantity property (uses spec.quantity)
            remaining_qty = ctx.remaining_quantity
            logger.debug(
                f"📊 [HEDGE] {exchange_name} {symbol}: "
                f"no hedge_target_quantity, using remaining_quantity={remaining_qty}"
            )
            return remaining_qty

