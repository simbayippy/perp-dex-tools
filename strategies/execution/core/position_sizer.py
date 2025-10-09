"""
Position Sizer - USD to contract quantity conversion.

⭐ Standard pattern across all Hummingbot executors ⭐

Converts between USD amounts and contract quantities, accounting for:
- Current market prices
- Exchange-specific tick sizes
- Minimum order sizes
- Quantity precision rules

Key features:
- USD → Quantity conversion
- Quantity → USD conversion
- Tick size rounding
- Min/max size validation
"""

from typing import Any, Optional
from decimal import Decimal, ROUND_DOWN, ROUND_UP
import logging

logger = logging.getLogger(__name__)


class PositionSizer:
    """
    Converts between USD and contract quantities.
    
    ⭐ Used in all Hummingbot executors ⭐
    
    Handles:
    - Price-based quantity calculation
    - Exchange precision rounding
    - Minimum/maximum order size enforcement
    
    Example:
        sizer = PositionSizer()
        
        # Convert $1000 to BTC quantity
        quantity = await sizer.usd_to_quantity(
            exchange_client=client,
            symbol="BTC-PERP",
            size_usd=Decimal("1000"),
            side="buy"
        )
        # → 0.02 BTC (if BTC price is $50,000)
        
        # Convert back to USD
        usd_value = await sizer.quantity_to_usd(
            exchange_client=client,
            symbol="BTC-PERP",
            quantity=Decimal("0.02")
        )
        # → $1000
    """
    
    def __init__(self):
        """Initialize position sizer."""
        self.logger = logging.getLogger(__name__)
    
    async def usd_to_quantity(
        self,
        exchange_client: Any,
        symbol: str,
        size_usd: Decimal,
        side: str,
        use_mid_price: bool = False
    ) -> Decimal:
        """
        Convert USD amount to contract quantity.
        
        Args:
            exchange_client: Exchange client instance
            symbol: Trading pair (e.g., "BTC-PERP")
            size_usd: Order size in USD (e.g., Decimal("1000"))
            side: "buy" or "sell"
            use_mid_price: If True, use mid price; if False, use ask/bid
        
        Returns:
            Quantity in contracts (e.g., Decimal("0.02"))
        """
        try:
            # Get current price
            if use_mid_price:
                price = await self._fetch_mid_price(exchange_client, symbol)
            else:
                # Use price that would be paid (ask for buy, bid for sell)
                best_bid, best_ask = await self._fetch_bbo_prices(exchange_client, symbol)
                price = best_ask if side == "buy" else best_bid
            
            # Calculate quantity
            quantity = size_usd / price
            
            # Round to exchange precision
            quantity = self._round_to_precision(
                quantity=quantity,
                exchange_client=exchange_client,
                symbol=symbol,
                round_down=(side == "buy")  # Round down for buys to avoid overspend
            )
            
            self.logger.debug(
                f"USD to quantity: ${size_usd} → {quantity} {symbol} @ ${price}"
            )
            
            return quantity
        
        except Exception as e:
            self.logger.error(f"USD to quantity conversion failed: {e}")
            raise
    
    async def quantity_to_usd(
        self,
        exchange_client: Any,
        symbol: str,
        quantity: Decimal,
        use_mid_price: bool = True
    ) -> Decimal:
        """
        Convert contract quantity to USD value.
        
        Args:
            exchange_client: Exchange client instance
            symbol: Trading pair (e.g., "BTC-PERP")
            quantity: Contract quantity (e.g., Decimal("0.02"))
            use_mid_price: If True, use mid price; if False, use bid
        
        Returns:
            USD value (e.g., Decimal("1000"))
        """
        try:
            # Get current price
            if use_mid_price:
                price = await self._fetch_mid_price(exchange_client, symbol)
            else:
                best_bid, _ = await self._fetch_bbo_prices(exchange_client, symbol)
                price = best_bid
            
            # Calculate USD value
            usd_value = quantity * price
            
            self.logger.debug(
                f"Quantity to USD: {quantity} {symbol} → ${usd_value} @ ${price}"
            )
            
            return usd_value
        
        except Exception as e:
            self.logger.error(f"Quantity to USD conversion failed: {e}")
            raise
    
    def _round_to_precision(
        self,
        quantity: Decimal,
        exchange_client: Any,
        symbol: str,
        round_down: bool = True
    ) -> Decimal:
        """
        Round quantity to exchange's precision requirements.
        
        Args:
            quantity: Raw quantity
            exchange_client: Exchange client
            symbol: Trading pair
            round_down: If True, round down; if False, round up
        
        Returns:
            Rounded quantity
        """
        try:
            # Try to get tick size from exchange config
            if hasattr(exchange_client, 'get_quantity_precision'):
                precision = exchange_client.get_quantity_precision(symbol)
                
                # Round to precision
                rounding_mode = ROUND_DOWN if round_down else ROUND_UP
                quantized = quantity.quantize(
                    Decimal(f"1e-{precision}"),
                    rounding=rounding_mode
                )
                
                return quantized
            
            # Fallback: Round to 8 decimal places (standard for crypto)
            rounding_mode = ROUND_DOWN if round_down else ROUND_UP
            return quantity.quantize(Decimal("1e-8"), rounding=rounding_mode)
        
        except Exception as e:
            self.logger.warning(
                f"Precision rounding failed, using default: {e}"
            )
            # Safe fallback
            rounding_mode = ROUND_DOWN if round_down else ROUND_UP
            return quantity.quantize(Decimal("1e-8"), rounding=rounding_mode)
    
    async def _fetch_mid_price(
        self,
        exchange_client: Any,
        symbol: str
    ) -> Decimal:
        """Fetch mid-market price."""
        best_bid, best_ask = await self._fetch_bbo_prices(exchange_client, symbol)
        return (best_bid + best_ask) / 2
    
    async def _fetch_bbo_prices(
        self,
        exchange_client: Any,
        symbol: str
    ) -> tuple[Decimal, Decimal]:
        """
        Fetch best bid/offer prices.
        
        Returns:
            (best_bid, best_ask) as Decimals
        """
        try:
            # Try dedicated BBO method if available
            if hasattr(exchange_client, 'fetch_bbo_prices'):
                bid, ask = await exchange_client.fetch_bbo_prices(symbol)
                return Decimal(str(bid)), Decimal(str(ask))
            
            # Fallback: Get from order book
            if hasattr(exchange_client, 'get_order_book_depth'):
                book = await exchange_client.get_order_book_depth(symbol, levels=1)
                best_bid = Decimal(str(book['bids'][0]['price']))
                best_ask = Decimal(str(book['asks'][0]['price']))
                return best_bid, best_ask
            
            raise NotImplementedError(
                "Exchange client must implement fetch_bbo_prices() or get_order_book_depth()"
            )
        
        except Exception as e:
            self.logger.error(f"Failed to fetch BBO prices: {e}")
            raise
    
    async def validate_order_size(
        self,
        exchange_client: Any,
        symbol: str,
        quantity: Decimal
    ) -> tuple[bool, Optional[str]]:
        """
        Validate if quantity meets exchange requirements.
        
        Args:
            exchange_client: Exchange client
            symbol: Trading pair
            quantity: Contract quantity
        
        Returns:
            (is_valid, error_message)
        """
        try:
            # Check minimum order size
            if hasattr(exchange_client, 'get_min_order_size'):
                min_size = exchange_client.get_min_order_size(symbol)
                if quantity < Decimal(str(min_size)):
                    return False, f"Order size {quantity} below minimum {min_size}"
            
            # Check maximum order size
            if hasattr(exchange_client, 'get_max_order_size'):
                max_size = exchange_client.get_max_order_size(symbol)
                if quantity > Decimal(str(max_size)):
                    return False, f"Order size {quantity} exceeds maximum {max_size}"
            
            return True, None
        
        except Exception as e:
            self.logger.warning(f"Order size validation failed: {e}")
            # Allow order to proceed on validation error (exchange will reject if invalid)
            return True, None

