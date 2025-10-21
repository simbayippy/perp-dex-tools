"""Base interface for trading exchange clients."""

from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
import inspect
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from exchange_clients.events import LiquidationEvent, LiquidationEventDispatcher

if TYPE_CHECKING:
    from .base_websocket import BaseWebSocketManager

from .base_models import ExchangePositionSnapshot, OrderInfo, OrderResult


class BaseExchangeClient(ABC):
    """
    Base interface for trading execution on perpetual DEXs.
    
    This interface is used by trading bots to execute orders, manage positions,
    and interact with exchange trading APIs.
    
    Key Responsibilities:
        - Order placement and management (limit, market, cancel)
        - Position tracking and management
        - Market data fetching (prices, order book depth)
        - WebSocket integration for real-time updates
        - Risk management and leverage queries
    
    Implementation Pattern:
        Each exchange should implement this interface in a client.py file:
        
        ```python
        class AsterClient(BaseExchangeClient):
            def __init__(self, config: Dict[str, Any]):
                super().__init__(config)
                # Exchange-specific initialization
                
            async def connect(self) -> None:
                # Connect to exchange WebSocket/API
                pass
        ```
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize the exchange client with configuration.
        
        Args:
            config: Configuration dictionary containing trading parameters
        """
        self.config = config
        self._validate_config()
        self._liquidation_dispatcher = LiquidationEventDispatcher()
        self.ws_manager: Optional["BaseWebSocketManager"] = None

    @abstractmethod
    def _validate_config(self) -> None:
        """
        Validate the exchange-specific configuration.
        
        This method should:
        - Check for required credentials using validate_credentials()
        - Validate configuration parameters
        - Raise MissingCredentialsError if credentials are invalid
        
        Example:
            ```python
            def _validate_config(self) -> None:
                validate_credentials('ASTER_API_KEY', os.getenv('ASTER_API_KEY'))
                validate_credentials('ASTER_SECRET_KEY', os.getenv('ASTER_SECRET_KEY'))
        ```
        """
        pass

    # ========================================================================
    # EVENT STREAMS (OPTIONAL)
    # ========================================================================

    def supports_liquidation_stream(self) -> bool:
        """
        Return True if this client surfaces real-time liquidation events.

        Subclasses should override when they wire the relevant WebSocket feeds.
        """
        return False

    def liquidation_events_queue(
        self,
        queue: Optional[asyncio.Queue[LiquidationEvent]] = None,
    ) -> asyncio.Queue[LiquidationEvent]:
        """
        Register for liquidation events.

        Args:
            queue: Optional pre-created asyncio.Queue. If omitted, a new queue
                   is created and returned.
        """
        return self._liquidation_dispatcher.register(queue)

    def unregister_liquidation_queue(self, queue: asyncio.Queue[LiquidationEvent]) -> None:
        """Remove a previously registered liquidation queue."""
        self._liquidation_dispatcher.unregister(queue)

    async def emit_liquidation_event(self, event: LiquidationEvent) -> None:
        """
        Emit a liquidation event to registered listeners.

        Exchanges with native liquidation feeds should call this when events arrive.
        """
        await self._liquidation_dispatcher.emit(event)

    async def handle_liquidation_notification(self, payload: Any) -> None:
        """
        Optional normalization hook for subclasses to convert raw liquidation messages.

        Default implementation does nothing; exchanges should override when they
        subscribe to liquidation feeds and need to normalize payloads before calling
        emit_liquidation_event().
        """
        return None

    # ========================================================================
    # CONNECTION MANAGEMENT
    # ========================================================================

    @abstractmethod
    async def connect(self) -> None:
        """
        Connect to the exchange (WebSocket, HTTP session, etc.).
        
        This method should:
        - Establish WebSocket connections for real-time updates
        - Initialize HTTP sessions for API calls
        - Subscribe to necessary data streams
        """
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """
        Disconnect from the exchange and cleanup resources.
        
        This method should:
        - Close WebSocket connections
        - Close HTTP sessions
        - Cancel background tasks
        """
        pass

    async def ensure_market_feed(self, symbol: str) -> None:
        """
        Ensure the exchange's websocket subscriptions target the given symbol.

        Default implementation delegates to the attached websocket manager.
        """
        manager = self.ws_manager
        if manager is None:
            return

        try:
            result = manager.prepare_market_feed(symbol)
            if inspect.isawaitable(result):
                await result
        except Exception as exc:
            logger = getattr(self, "logger", None)
            if logger and hasattr(logger, "log"):
                logger.log(
                    f"⚠️ [{self.get_exchange_name().upper()}] "
                    f"WebSocket feed preparation failed: {exc}",
                    "DEBUG",
                )

    @abstractmethod
    def get_exchange_name(self) -> str:
        """
        Get the exchange name identifier.
        
        Returns:
            Exchange name (e.g., "aster", "lighter", "backpack")
        """
        pass

    # ========================================================================
    # MARKET DATA & PRICING
    # ========================================================================

    @abstractmethod
    async def fetch_bbo_prices(self, contract_id: str) -> Tuple[Decimal, Decimal]:
        """
        Fetch best bid and offer (BBO) prices for a contract.
        
        Args:
            contract_id: Contract/symbol identifier
            
        Returns:
            Tuple of (best_bid, best_ask) as Decimals
            
        Raises:
            Exception: If fetching fails
            
        Example:
            >>> bid, ask = await client.fetch_bbo_prices("BTC")
            >>> print(f"Spread: {ask - bid}")
        """
        pass

    @abstractmethod
    async def get_order_book_depth(
        self, 
        contract_id: str, 
        levels: int = 10
    ) -> Dict[str, List[Dict[str, Decimal]]]:
        """
        Get order book depth for liquidity analysis.
        
        🔄 Smart Implementation Pattern:
        1. Try get_order_book_from_websocket() first (zero latency)
        2. Fall back to REST API if WebSocket data not available
        
        Args:
            contract_id: Contract/symbol identifier
            levels: Number of price levels to fetch (default: 10)
            
        Returns:
            Dictionary with 'bids' and 'asks' lists containing dicts with 'price' and 'size'
            
        Example:
            {
                'bids': [
                    {'price': Decimal('50000'), 'size': Decimal('1.5')},
                    {'price': Decimal('49999'), 'size': Decimal('2.0')},
                    ...
                ],
                'asks': [
                    {'price': Decimal('50001'), 'size': Decimal('2.0')},
                    {'price': Decimal('50002'), 'size': Decimal('1.5')},
                    ...
                ]
            }
            
        Recommended Implementation:
            ```python
            async def get_order_book_depth(self, contract_id, levels=10):
                # Try WebSocket first (zero latency)
                ws_book = self.get_order_book_from_websocket()
                if ws_book:
                    return {
                        'bids': ws_book['bids'][:levels],
                        'asks': ws_book['asks'][:levels]
                    }
                
                # Fall back to REST API
                # ... REST API implementation ...
            ```
        """
        pass

    # ========================================================================
    # ORDER MANAGEMENT
    # ========================================================================

    def get_min_order_notional(self, symbol: str) -> Optional[Decimal]:
        """
        Return the minimum quote notional required to place an order on the exchange.

        Subclasses may override to surface exchange-specific requirements. The default
        implementation returns None to indicate no known limit.
        """
        # TODO: change to abstract method in future, if more exchanges have limitation
        return None

    @abstractmethod
    async def place_limit_order(
        self, 
        contract_id: str, 
        quantity: Decimal, 
        price: Decimal, 
        side: str
    ) -> OrderResult:
        """
        Place a limit order at a specific price.
        
        Limit orders are maker orders that sit on the order book until filled.
        Most exchanges offer maker fee rebates for limit orders.
        
        Args:
            contract_id: Contract/symbol identifier
            quantity: Order size
            price: Limit price
            side: 'buy' or 'sell'
            
        Returns:
            OrderResult with order details
            
        Example:
            >>> result = await client.place_limit_order("BTC", Decimal("1.0"), Decimal("50000"), "buy")
            >>> if result.success:
            ...     print(f"Order placed: {result.order_id}")
        """
        pass

    @abstractmethod
    async def place_market_order(
        self, 
        contract_id: str, 
        quantity: Decimal, 
        side: str
    ) -> OrderResult:
        """
        Place a market order for immediate execution.
        
        Market orders are taker orders that execute against the order book immediately.
        They typically incur taker fees.
        
        Args:
            contract_id: Contract/symbol identifier
            quantity: Order size
            side: 'buy' or 'sell'
            
        Returns:
            OrderResult with order details
            
        Note:
            If exchange doesn't support true market orders, implement using
            aggressive limit order priced to execute immediately.
            
        Example:
            >>> result = await client.place_market_order("BTC", Decimal("1.0"), "buy")
            >>> print(f"Executed at: {result.price}")
        """
        pass

    @abstractmethod
    async def cancel_order(self, order_id: str) -> OrderResult:
        """
        Cancel an existing order.
        
        ⚠️ CRITICAL METHOD - Required for all exchanges.
        
        This method is essential for:
        - Timeout handling in limit-with-fallback execution
        - Emergency rollback in atomic multi-order patterns
        - Risk management (canceling stale orders in fast markets)
        - Strategy order lifecycle management
        
        Args:
            order_id: Order identifier to cancel
            
        Returns:
            OrderResult indicating success/failure and any partial fills
            - success: True if cancel succeeded
            - filled_size: Amount that was filled before cancel (if any)
            - error_message: Error details if cancel failed
            
        Example:
            >>> result = await client.cancel_order("order_12345")
            >>> if result.success:
            ...     print(f"Canceled, {result.filled_size} was already filled")
        """
        pass

    def round_to_step(self, quantity: Decimal) -> Decimal:
        """
        Round a proposed quantity to the venue's supported increment.

        Exchanges with explicit step sizes should override. Default implementation
        leaves the quantity unchanged.
        """
        return quantity

    def resolve_contract_id(self, symbol: str) -> str:
        """
        Resolve the exchange-specific contract identifier for order placement.

        Returns any cached `contract_id` on the client config, or falls back to
        the provided symbol when no specialization exists.
        """
        contract_id = getattr(self.config, "contract_id", None)
        return contract_id or symbol

    @abstractmethod
    async def get_order_info(self, order_id: str) -> Optional[OrderInfo]:
        """
        Get detailed information about a specific order.
        
        Args:
            order_id: Order identifier
            
        Returns:
            OrderInfo with order details, or None if order not found
        """
        pass

    @abstractmethod
    async def get_active_orders(self, contract_id: str) -> List[OrderInfo]:
        """
        Get all active (open) orders for a contract.
        
        Args:
            contract_id: Contract/symbol identifier
            
        Returns:
            List of OrderInfo for active orders
        """
        pass

    # ========================================================================
    # POSITION & ACCOUNT MANAGEMENT
    # ========================================================================

    @abstractmethod
    async def get_account_positions(self) -> Decimal:
        """
        Get account position size for the configured contract.
        
        Returns:
            Position size as Decimal (typically absolute value)
        """
        pass

    @abstractmethod
    async def get_account_balance(self) -> Optional[Decimal]:
        """
        Get current available account balance for trading.
        
        ⚠️ IMPORTANT: This should return AVAILABLE balance (not total balance).
        Available balance = What can be used to open new positions right now.
        
        Returns:
            Available balance in USD/base currency, or None if not supported
            
        Implementation Notes:
            - Return available/free balance (not total wallet balance)
            - For cross-margin: Use crossWalletBalance or availableBalance
            - For isolated-margin: Sum available balance across all assets
            - Return None only if exchange doesn't support this query
            
        Example Implementation (with balance support):
            ```python
            async def get_account_balance(self) -> Optional[Decimal]:
                try:
                    result = await self._make_request('GET', '/api/v1/account')
                    return Decimal(str(result.get('availableBalance', 0)))
                except Exception as e:
                    self.logger.warning(f"Failed to get balance: {e}")
                    return None
            ```
            
        Example Implementation (no balance support):
            ```python
            async def get_account_balance(self) -> Optional[Decimal]:
                # Exchange doesn't support balance queries
                return None
            ```
        """
        pass

    @abstractmethod 
    async def get_position_snapshot(self, symbol: str) -> Optional[ExchangePositionSnapshot]:
        """
        Fetch a live snapshot for a specific symbol/contract.

        Exchanges that support richer position data should override this method.
        The returned instance must populate standardized fields (quantity, prices,
        PnL, margin, etc.) so strategies can consume it without venue-specific logic.

        TODO: promote to abstract once most clients implement it.
        """
        return None
    
    async def get_account_pnl(self) -> Optional[Decimal]:
        """
        Get account unrealized P&L.
        
        Returns:
            P&L in USD/base currency, or None if not supported
            
        Note:
            Override this method if exchange supports P&L queries.
            Default implementation returns None.
        """
        return None
    
    async def get_total_asset_value(self) -> Optional[Decimal]:
        """
        Get total account asset value (balance + unrealized P&L).
        
        Returns:
            Total asset value in USD/base currency, or None if not supported
            
        Note:
            Override this method if exchange supports asset value queries.
            Default implementation returns None.
        """
        return None

    # ========================================================================
    # RISK MANAGEMENT & LEVERAGE
    # ========================================================================
    
    @abstractmethod
    async def get_leverage_info(self, symbol: str) -> Dict[str, Any]:
        """
        Get leverage and position limit information for a symbol.
        
        ⚠️ CRITICAL for delta-neutral strategies: Different exchanges have different
        leverage limits for the same symbol. This method allows pre-flight validation
        to ensure both sides of a delta-neutral trade can execute with the same size.
        
        Args:
            symbol: Trading symbol (normalized format, e.g., "ZORA", "BTC")
            
        Returns:
            Dictionary with leverage limits:
            {
                'max_leverage': Decimal or None,        # e.g., Decimal('10') for 10x
                'max_notional': Decimal or None,        # Max position value in USD
                'margin_requirement': Decimal or None,  # e.g., Decimal('0.1') = 10% = 10x leverage
                'brackets': List or None,               # Leverage brackets if available
                'error': str or None                    # Error message if data unavailable (NEW)
            }
            
        Implementation Notes:
            - For production exchanges (Aster, Lighter): Query actual limits from API
            - For exchanges in development: Return conservative defaults (10x leverage)
            - Always return the same dict structure (even if values are None)
            - 🔒 NEW: If symbol is not listed or leverage info cannot be determined,
              set 'error' field with descriptive message and set other fields to None
            
        Error Handling:
            - If symbol not found on exchange: Set error="Symbol XXX not listed on {exchange}"
            - If API fails: Set error="Failed to query leverage info: {reason}"
            - If data incomplete: Set error="Unable to determine leverage limits for {symbol}"
        """
        pass

    # ========================================================================
    # CONFIGURATION & UTILITIES
    # ========================================================================

    def round_to_tick(self, price) -> Decimal:
        """
        Round price to exchange tick size.
        
        Args:
            price: Price to round
            
        Returns:
            Price rounded to tick size
        """
        price = Decimal(price)
        tick = self.config.tick_size
        return price.quantize(tick, rounding=ROUND_HALF_UP)

    def normalize_symbol(self, symbol: str) -> str:
        """
        [INTERNAL] Convert a normalized symbol to this exchange's expected format.
        
        ⚠️ This method is for internal use by exchange methods (get_order_book_depth,
        place_limit_order, etc.). Callers should NOT call this directly - just pass
        symbols to methods and let the exchange handle normalization.
        
        Different exchanges use different symbol naming conventions:
        - Lighter: "BTC", "ETH", "ZORA" (base asset only)
        - Aster: "BTCUSDT", "ETHUSDT", "ZORAUSDT" (base + quote)
        - Backpack: "BTC_USDC", "ETH-PERP" (various formats)
        
        Override this method in each exchange client to handle conversion.
        
        Args:
            symbol: Normalized symbol (usually just the base asset, e.g., "BTC")
            
        Returns:
            Exchange-specific symbol format
        
        Default implementation: Return symbol as-is (no conversion needed)
        """
        return symbol




__all__ = ["BaseExchangeClient"]
