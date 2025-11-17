"""Leverage validation for position opening."""

from decimal import Decimal
from typing import TYPE_CHECKING, Any, Dict, Optional

from helpers.unified_logger import log_stage

if TYPE_CHECKING:
    from exchange_clients.base_client import BaseExchangeClient
    from ...strategy import FundingArbitrageStrategy


class LeverageValidator:
    """Handles leverage validation and normalization."""
    
    def __init__(self, strategy: "FundingArbitrageStrategy"):
        self._strategy = strategy
    
    async def validate_leverage(
        self,
        *,
        symbol: str,
        long_client: Any,
        short_client: Any,
    ) -> Optional[Dict[str, Any]]:
        """
        Normalize leverage and confirm balances.
        
        Calculates requested size from target_margin internally to avoid redundant leverage calls.
        
        Returns:
            Dict with "adjusted_size" and "normalized_leverage", or None if validation fails
        """
        strategy = self._strategy
        log_stage(strategy.logger, "Leverage Validation & Normalization", icon="🔍", stage_id="2")

        target_margin = strategy.config.target_margin
        if target_margin is None:
            strategy.logger.error("target_margin not set in config")
            return None

        leverage_validator = strategy.leverage_validator

        try:
            long_leverage_info = await leverage_validator.get_leverage_info(long_client, symbol)
            short_leverage_info = await leverage_validator.get_leverage_info(short_client, symbol)
            
            min_leverage = None
            if long_leverage_info.max_leverage and short_leverage_info.max_leverage:
                min_leverage = min(long_leverage_info.max_leverage, short_leverage_info.max_leverage)
            elif long_leverage_info.max_leverage:
                min_leverage = long_leverage_info.max_leverage
            elif short_leverage_info.max_leverage:
                min_leverage = short_leverage_info.max_leverage
            
            if min_leverage:
                requested_size = target_margin * min_leverage
            else:
                strategy.logger.warning(
                    f"⚠️ Could not determine leverage for {symbol}, using conservative 5x estimate"
                )
                requested_size = target_margin * Decimal("5")
        except Exception as exc:
            strategy.logger.warning(
                f"⚠️ Error calculating requested size for {symbol}: {exc}. "
                "Falling back to conservative 5x estimate"
            )
            requested_size = target_margin * Decimal("5")

        try:
            leverage_prep = await leverage_validator.prepare_leverage(
                exchange_clients=[long_client, short_client],
                symbol=symbol,
                requested_size_usd=requested_size,
                min_position_usd=Decimal("5"),
                check_balance=True,
                normalize_leverage=True,
            )
        except Exception as exc:
            strategy.logger.warning(
                f"⛔ [SKIP] {symbol}: Leverage preparation failed - {exc}"
            )
            return None

        adjusted_size = leverage_prep.adjusted_size_usd

        if leverage_prep.below_minimum:
            strategy.logger.warning(
                f"⛔ SAFEGUARD: {symbol}: Position size too small after leverage adjustment - "
                f"${adjusted_size:.2f} < minimum ${Decimal('5'):.2f}. "
                f"This may occur if balance is insufficient or leverage limits are too restrictive."
            )
            return None

        return {
            "adjusted_size": adjusted_size,
            "normalized_leverage": leverage_prep.normalized_leverage,
        }

