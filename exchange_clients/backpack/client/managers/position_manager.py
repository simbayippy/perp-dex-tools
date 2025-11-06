"""
Position manager module for Backpack client.

Handles position queries and snapshots.
"""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional

from exchange_clients.base_models import ExchangePositionSnapshot, query_retry
from exchange_clients.backpack.client.utils.helpers import to_decimal
from exchange_clients.backpack.common import normalize_symbol as normalize_backpack_symbol


class BackpackPositionManager:
    """
    Position manager for Backpack exchange.
    
    Handles:
    - Position size queries
    - Position snapshots
    """
    
    def __init__(
        self,
        account_client: Any,
        config: Any,
        logger: Any,
        normalize_symbol_fn: Optional[Any] = None,
    ):
        """
        Initialize position manager.
        
        Args:
            account_client: Backpack Account client instance
            config: Trading configuration object
            logger: Logger instance
            normalize_symbol_fn: Function to normalize symbols
        """
        self.account_client = account_client
        self.config = config
        self.logger = logger
        self.normalize_symbol = normalize_symbol_fn or (lambda s: s.upper())

    @query_retry(default_return=Decimal("0"))
    async def get_account_positions(self) -> Decimal:
        """
        Return absolute position size for configured contract.
        
        Returns:
            Absolute position size
        """
        try:
            positions = await asyncio.to_thread(self.account_client.get_open_positions)
        except Exception as exc:
            self.logger.error(f"[BACKPACK] Failed to fetch open positions: {exc}")
            return Decimal("0")

        contract_id = getattr(self.config, "contract_id", None)
        if not positions or not contract_id:
            return Decimal("0")
        
        # Validate response type
        if not isinstance(positions, list):
            self.logger.warning(
                f"[BACKPACK] Unexpected response type from get_open_positions: {type(positions).__name__}"
            )
            return Decimal("0")

        for position in positions:
            if (position.get("symbol") or "").upper() == contract_id.upper():
                quantity = to_decimal(position.get("netQuantity"), Decimal("0"))
                return quantity.copy_abs() if isinstance(quantity, Decimal) else Decimal("0")

        return Decimal("0")

    async def get_position_snapshot(
        self, 
        symbol: str,
        position_opened_at: Optional[float] = None,
    ) -> Optional[ExchangePositionSnapshot]:
        """
        Return a normalized position snapshot for a given symbol.
        
        Args:
            symbol: Symbol to get snapshot for
            
        Returns:
            ExchangePositionSnapshot or None
        """
        normalized_symbol = symbol.upper()
        target_symbol = self.normalize_symbol(normalized_symbol)

        try:
            # Wrap synchronous SDK call in asyncio.to_thread
            positions = await asyncio.to_thread(self.account_client.get_open_positions)
        except Exception as exc:
            self.logger.warning(f"[BACKPACK] Failed to fetch positions for snapshot: {exc}")
            return None

        # Validate response is actually a list, not an error string/dict
        if not positions:
            return None
        
        # Handle API error responses (e.g., {"code": "ERROR", "message": "..."})
        if isinstance(positions, dict):
            if positions.get("code") or positions.get("error"):
                self.logger.warning(
                    f"[BACKPACK] API error fetching positions: {positions.get('message') or positions}"
                )
                return None
        
        # Handle unexpected string responses
        if isinstance(positions, str):
            self.logger.warning(f"[BACKPACK] Unexpected string response from API: {positions}")
            return None
        
        # Ensure it's iterable (list)
        if not isinstance(positions, list):
            self.logger.warning(
                f"[BACKPACK] Unexpected response type: {type(positions).__name__}"
            )
            return None

        for position in positions:
            raw_symbol = (position.get("symbol") or "").upper()
            if raw_symbol != target_symbol:
                # As a fallback, normalize Backpack symbol (handles legacy formats)
                if normalize_backpack_symbol(raw_symbol) != normalized_symbol:
                    continue

            quantity = to_decimal(
                position.get("netQuantity")
                or position.get("quantity")
                or position.get("position")
                or position.get("contracts"),
                Decimal("0"),
            )

            entry_price = to_decimal(
                position.get("entryPrice")  # Backpack's actual field name
                or position.get("averageEntryPrice")
                or position.get("avgEntryPrice"),
            )

            mark_price = to_decimal(
                position.get("markPrice")
                or position.get("marketPrice")
                or position.get("indexPrice")
                or position.get("oraclePrice"),
            )

            notional = to_decimal(
                position.get("notional")
                or position.get("positionValue")
                or position.get("grossPositionValue"),
            )

            exposure = notional.copy_abs() if isinstance(notional, Decimal) else None
            if exposure is None and mark_price is not None and quantity:
                exposure = mark_price * quantity.copy_abs()

            unrealized = to_decimal(
                position.get("pnlUnrealized")  # Backpack's actual field name
                or position.get("unrealizedPnl")
                or position.get("unrealizedPnlUsd")
                or position.get("unrealizedPnL")
                or position.get("pnl"),
            )

            realized = to_decimal(
                position.get("pnlRealized")  # Backpack's actual field name
                or position.get("realizedPnl")
                or position.get("realizedPnlUsd")
            )
            funding_accrued = to_decimal(
                position.get("cumulativeFundingPayment")  # Backpack's actual field name
                or position.get("fundingFees")
                or position.get("fundingAccrued")
            )
            margin_reserved = to_decimal(
                position.get("initialMargin")
                or position.get("marginUsed")
                or position.get("allocatedMargin")
            )
            leverage = to_decimal(position.get("leverage"))
            liquidation_price = to_decimal(
                position.get("estLiquidationPrice")  # Backpack's actual field name
                or position.get("liquidationPrice")
            )

            side = None
            if isinstance(quantity, Decimal):
                if quantity > 0:
                    side = "long"
                elif quantity < 0:
                    side = "short"

            metadata: Dict[str, Any] = {
                "backpack_symbol": raw_symbol,
                "position_id": position.get("positionId") or position.get("id"),  # Backpack uses 'positionId'
                "updated_at": position.get("updatedAt"),
            }
            if notional is not None:
                metadata["notional"] = notional

            return ExchangePositionSnapshot(
                symbol=normalized_symbol,
                quantity=quantity or Decimal("0"),
                side=side,
                entry_price=entry_price,
                mark_price=mark_price,
                exposure_usd=exposure,
                unrealized_pnl=unrealized,
                realized_pnl=realized,
                funding_accrued=funding_accrued,
                margin_reserved=margin_reserved,
                leverage=leverage,
                liquidation_price=liquidation_price,
                timestamp=datetime.now(timezone.utc),
                metadata={k: v for k, v in metadata.items() if v is not None},
            )

        return None

