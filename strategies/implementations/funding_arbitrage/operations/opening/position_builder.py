"""Position building utilities."""

from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple
from uuid import uuid4

from ..core.decimal_utils import add_decimal

if TYPE_CHECKING:
    from ...models import FundingArbPosition


class PositionBuilder:
    """Handles building and merging positions."""
    
    def build_new_position(
        self,
        *,
        symbol: str,
        long_dex: str,
        short_dex: str,
        size_usd: Decimal,
        opportunity: Any,
        entry_fees: Decimal,
        total_cost: Decimal,
        long_fill: dict,
        short_fill: dict,
        total_slippage: Decimal,
        long_exposure: Decimal,
        short_exposure: Decimal,
        imbalance_usd: Decimal,
        planned_quantity: Decimal,
        normalized_leverage: Optional[int] = None,
    ) -> Tuple["FundingArbPosition", str]:
        """
        Instantiate a FundingArbPosition populated with initial metadata.
        
        Args:
            symbol: Trading symbol
            long_dex: Long DEX name
            short_dex: Short DEX name
            size_usd: Position size in USD
            opportunity: Trading opportunity
            entry_fees: Entry fees paid
            total_cost: Total cost (fees + slippage)
            long_fill: Long leg fill data
            short_fill: Short leg fill data
            total_slippage: Total slippage
            long_exposure: Long leg exposure
            short_exposure: Short leg exposure
            imbalance_usd: Quantity imbalance (tokens, not USD)
            planned_quantity: Planned quantity
            normalized_leverage: Normalized leverage
            
        Returns:
            Tuple of (position, timestamp_iso)
        """
        from ...models import FundingArbPosition
        
        partial_fee = entry_fees / Decimal("2") if entry_fees else Decimal("0")
        timestamp_iso = datetime.now(timezone.utc).isoformat()

        position = FundingArbPosition(
            id=uuid4(),
            symbol=symbol,
            long_dex=long_dex,
            short_dex=short_dex,
            size_usd=size_usd,
            entry_long_rate=opportunity.long_rate,
            entry_short_rate=opportunity.short_rate,
            entry_divergence=opportunity.divergence,
            opened_at=datetime.now(),
            total_fees_paid=total_cost,
        )

        margin_used = None
        if normalized_leverage and normalized_leverage > 0:
            margin_used = size_usd / Decimal(str(normalized_leverage))

        position.metadata.update(
            {
                "legs": {
                    long_dex: {
                        "side": "long",
                        "entry_price": long_fill.get("fill_price"),
                        "quantity": long_fill.get("filled_quantity"),
                        "order_id": long_fill.get("order_id"),
                        "fees_paid": partial_fee,
                        "slippage_usd": long_fill.get("slippage_usd"),
                        "execution_mode": long_fill.get("execution_mode_used"),
                        "exposure_usd": long_exposure,
                        "last_updated": timestamp_iso,
                    },
                    short_dex: {
                        "side": "short",
                        "entry_price": short_fill.get("fill_price"),
                        "quantity": short_fill.get("filled_quantity"),
                        "order_id": short_fill.get("order_id"),
                        "fees_paid": partial_fee,
                        "slippage_usd": short_fill.get("slippage_usd"),
                        "execution_mode": short_fill.get("execution_mode_used"),
                        "exposure_usd": short_exposure,
                        "last_updated": timestamp_iso,
                    },
                },
                "total_slippage_usd": total_slippage,
                "planned_quantity": planned_quantity,
                "residual_imbalance_usd": imbalance_usd,
                "normalized_leverage": normalized_leverage,
                "margin_used": float(margin_used) if margin_used else None,
            }
        )

        return position, timestamp_iso
    
    def merge_existing_position(
        self,
        *,
        existing_position: "FundingArbPosition",
        new_position: "FundingArbPosition",
        total_cost: Decimal,
        entry_fees: Decimal,
        total_slippage: Decimal,
        timestamp_iso: str,
    ) -> Optional[Tuple["FundingArbPosition", Decimal, Decimal]]:
        """
        Merge a new fill into an existing logical position.

        Returns:
            Tuple of (updated_position, updated_size, additional_size) or None if merge skipped.
        """
        existing_size = existing_position.size_usd or Decimal("0")
        additional_size = new_position.size_usd or Decimal("0")
        updated_size = existing_size + additional_size

        if updated_size <= 0:
            return None

        existing_long_rate = existing_position.entry_long_rate or Decimal("0")
        existing_short_rate = existing_position.entry_short_rate or Decimal("0")

        weighted_long = (existing_long_rate * existing_size) + (
            new_position.entry_long_rate * additional_size
        )
        weighted_short = (existing_short_rate * existing_size) + (
            new_position.entry_short_rate * additional_size
        )

        existing_position.size_usd = updated_size
        existing_position.entry_long_rate = weighted_long / updated_size
        existing_position.entry_short_rate = weighted_short / updated_size
        existing_position.entry_divergence = (
            existing_position.entry_short_rate - existing_position.entry_long_rate
        )
        existing_position.total_fees_paid = add_decimal(
            existing_position.total_fees_paid,
            total_cost,
        ) or Decimal("0")

        existing_metadata = existing_position.metadata or {}
        new_metadata = new_position.metadata or {}

        existing_legs = existing_metadata.setdefault("legs", {})
        for dex, leg_meta in new_metadata.get("legs", {}).items():
            current_leg = existing_legs.get(dex, {}).copy()
            for key, value in leg_meta.items():
                if key in {"quantity", "fees_paid", "slippage_usd", "exposure_usd"}:
                    current_leg[key] = add_decimal(current_leg.get(key), value)
                else:
                    current_leg[key] = value
            existing_legs[dex] = current_leg
        existing_metadata["legs"] = existing_legs
        existing_metadata["total_slippage_usd"] = add_decimal(
            existing_metadata.get("total_slippage_usd"),
            new_metadata.get("total_slippage_usd"),
        )

        new_legs = new_metadata.get("legs", {})
        long_leg_meta = new_legs.get(existing_position.long_dex, {})
        short_leg_meta = new_legs.get(existing_position.short_dex, {})

        fills = existing_metadata.setdefault("fills", [])
        fills.append(
            {
                "id": str(uuid4()),
                "timestamp": timestamp_iso,
                "size_usd": additional_size,
                "long_fill_price": long_leg_meta.get("entry_price"),
                "short_fill_price": short_leg_meta.get("entry_price"),
                "slippage_usd": total_slippage,
                "fees_usd": entry_fees,
            }
        )
        existing_metadata["last_update"] = timestamp_iso
        existing_position.metadata = existing_metadata

        return existing_position, updated_size, additional_size

