"""
Leverage Validator - Ensures position sizes are compatible with exchange leverage limits.

Critical for delta-neutral strategies where both sides must execute with identical sizes.
If one exchange has lower leverage, we need to reduce the position size for both sides.

Example:
    # Check if both exchanges support $100 position
    validator = LeverageValidator()
    
    max_size = await validator.get_max_position_size(
        exchange_clients=[aster_client, lighter_client],
        symbol="ZORA",
        requested_size_usd=Decimal("100")
    )
    
    if max_size < requested_size_usd:
        logger.warning(f"Reduced position size from ${requested_size_usd} to ${max_size} due to leverage limits")
"""

from typing import Any, List, Optional, Dict, Tuple
from decimal import Decimal
import logging

logger = logging.getLogger(__name__)


class LeverageInfo:
    """Leverage information for a symbol on an exchange."""
    
    def __init__(
        self,
        exchange_name: str,
        symbol: str,
        max_leverage: Optional[Decimal] = None,
        max_notional: Optional[Decimal] = None,  # Max position value
        max_position_size: Optional[Decimal] = None,  # Max position in base asset
        margin_requirement: Optional[Decimal] = None  # e.g., 0.20 = 20% = 5x leverage
    ):
        self.exchange_name = exchange_name
        self.symbol = symbol
        self.max_leverage = max_leverage
        self.max_notional = max_notional
        self.max_position_size = max_position_size
        self.margin_requirement = margin_requirement
    
    def get_max_size_usd(self, available_balance: Optional[Decimal] = None) -> Optional[Decimal]:
        """
        Calculate maximum position size in USD based on leverage limits.
        
        Args:
            available_balance: Available margin balance (optional)
            
        Returns:
            Maximum position size in USD, or None if no limit
        """
        limits = []
        
        # Limit 1: Max notional (direct position value limit)
        if self.max_notional is not None:
            limits.append(self.max_notional)
        
        # Limit 2: Max leverage * available balance
        if self.max_leverage is not None and available_balance is not None:
            max_from_leverage = self.max_leverage * available_balance
            limits.append(max_from_leverage)
        
        # Limit 3: Available balance / margin requirement
        if self.margin_requirement is not None and available_balance is not None:
            max_from_margin = available_balance / self.margin_requirement
            limits.append(max_from_margin)
        
        # Return the most restrictive limit
        return min(limits) if limits else None
    
    def __repr__(self):
        parts = [f"{self.exchange_name}:{self.symbol}"]
        if self.max_leverage:
            parts.append(f"leverage={self.max_leverage}x")
        if self.max_notional:
            parts.append(f"max_notional=${self.max_notional}")
        if self.margin_requirement:
            parts.append(f"margin={self.margin_requirement*100:.1f}%")
        return f"LeverageInfo({', '.join(parts)})"


class LeverageValidator:
    """
    Validates that position sizes are compatible with exchange leverage limits.
    
    ⭐ Critical for delta-neutral strategies ⭐
    
    When opening positions on multiple exchanges simultaneously (like funding arb),
    both sides must execute with identical position sizes. If one exchange has lower
    leverage limits, we need to reduce the size for BOTH sides.
    """
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self._leverage_cache: Dict[Tuple[str, str], LeverageInfo] = {}
    
    async def get_leverage_info(
        self,
        exchange_client: Any,
        symbol: str
    ) -> LeverageInfo:
        """
        Fetch leverage information for a symbol on an exchange.
        
        Args:
            exchange_client: Exchange client instance
            symbol: Trading symbol (e.g., "ZORA", "BTC")
            
        Returns:
            LeverageInfo with limits
        """
        exchange_name = exchange_client.get_exchange_name()
        cache_key = (exchange_name, symbol)
        
        # Check cache first
        if cache_key in self._leverage_cache:
            return self._leverage_cache[cache_key]
        
        # Query exchange-specific leverage info
        leverage_info = await self._query_exchange_leverage(exchange_client, symbol)
        
        # Cache for future use
        self._leverage_cache[cache_key] = leverage_info
        
        return leverage_info
    
    async def _query_exchange_leverage(
        self,
        exchange_client: Any,
        symbol: str
    ) -> LeverageInfo:
        """
        Query exchange-specific leverage information.
        
        Calls the exchange client's get_leverage_info() method.
        All exchange clients implement this via BaseExchangeClient.
        """
        exchange_name = exchange_client.get_exchange_name()
        
        try:
            # Call the exchange's get_leverage_info method
            # This is implemented in BaseExchangeClient (with default) and can be overridden
            leverage_data = await exchange_client.get_leverage_info(symbol)
            
            # Convert to LeverageInfo object
            return LeverageInfo(
                exchange_name=exchange_name,
                symbol=symbol,
                max_leverage=leverage_data.get('max_leverage'),
                max_notional=leverage_data.get('max_notional'),
                margin_requirement=leverage_data.get('margin_requirement')
            )
        
        except Exception as e:
            self.logger.error(f"Error querying leverage for {exchange_name}:{symbol}: {e}")
            # Return conservative default
            return LeverageInfo(
                exchange_name=exchange_name,
                symbol=symbol,
                max_leverage=Decimal('10'),
                margin_requirement=Decimal('0.10')
            )
    
    async def get_max_position_size(
        self,
        exchange_clients: List[Any],
        symbol: str,
        requested_size_usd: Decimal,
        check_balance: bool = True
    ) -> Tuple[Decimal, Optional[str]]:
        """
        Get maximum position size that ALL exchanges can support.
        
        This is critical for delta-neutral strategies - we need to ensure
        BOTH sides can execute with the same position size.
        
        Args:
            exchange_clients: List of exchange clients
            symbol: Trading symbol
            requested_size_usd: Desired position size in USD
            check_balance: Whether to check available balance
            
        Returns:
            Tuple of (max_size_usd, limiting_exchange_name)
            - max_size_usd: Maximum size all exchanges can support
            - limiting_exchange_name: Which exchange is limiting (or None)
        """
        max_size = requested_size_usd
        limiting_exchange = None
        
        for client in exchange_clients:
            exchange_name = client.get_exchange_name()
            
            # Get leverage info
            leverage_info = await self.get_leverage_info(client, symbol)
            
            # Get available balance if requested
            available_balance = None
            if check_balance:
                try:
                    available_balance = await client.get_account_balance()
                except:
                    self.logger.warning(f"Could not get balance for {exchange_name}")
            
            # Calculate max size for this exchange
            exchange_max = leverage_info.get_max_size_usd(available_balance)
            
            if exchange_max is not None and exchange_max < max_size:
                self.logger.warning(
                    f"⚠️  [LEVERAGE] {exchange_name} limits position to ${exchange_max:.2f} "
                    f"(requested: ${requested_size_usd:.2f}) | {leverage_info}"
                )
                max_size = exchange_max
                limiting_exchange = exchange_name
        
        if limiting_exchange:
            self.logger.info(
                f"📊 [LEVERAGE] Position size adjusted: ${requested_size_usd:.2f} → ${max_size:.2f} "
                f"(limited by {limiting_exchange})"
            )
        else:
            self.logger.info(
                f"✅ [LEVERAGE] Position size ${requested_size_usd:.2f} supported by all exchanges"
            )
        
        return max_size, limiting_exchange
    
    def clear_cache(self):
        """Clear leverage info cache."""
        self._leverage_cache.clear()

