"""
Base exchange client and funding adapter interfaces.
All exchange implementations should inherit from these classes.

Architecture:
    - BaseExchangeClient: Trading execution interface (used by trading bots)
    - BaseFundingAdapter: Funding rate collection interface (used by funding rate service)
    
Each exchange implementation typically has:
    - client.py: Trading execution (inherits BaseExchangeClient)
    - funding_adapter.py: Funding rate collection (inherits BaseFundingAdapter)
    - common.py: Shared utilities (symbol normalization, etc.)
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple, Type, Union
from decimal import Decimal, ROUND_HALF_UP
from tenacity import RetryCallState, retry, retry_if_exception_type, stop_after_attempt, wait_exponential
import aiohttp
import asyncio

from .events import LiquidationEvent, LiquidationEventDispatcher


# ============================================================================
# EXCEPTIONS & ERRORS
# ============================================================================

class MissingCredentialsError(Exception):
    """Raised when exchange credentials are missing or invalid (placeholders)."""
    pass


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def validate_credentials(credential_name: str, credential_value: Optional[str], 
                        placeholder_values: Optional[list] = None) -> None:
    """
    Validate exchange credentials to ensure they're not missing or placeholders.
    
    This is a standalone utility function (not a method) because:
    - It's used during __init__ before the object is fully constructed
    - It's stateless and doesn't need access to instance data
    - It can be used by multiple exchange clients consistently
    
    Args:
        credential_name: Name of the credential (e.g., 'API_KEY')
        credential_value: Value of the credential from environment
        placeholder_values: List of placeholder values to reject (default: common placeholders)
        
    Raises:
        MissingCredentialsError: If credential is missing or is a placeholder
        
    Example:
        >>> validate_credentials('EDGEX_ACCOUNT_ID', os.getenv('EDGEX_ACCOUNT_ID'))
    """
    # Default placeholder values
    if placeholder_values is None:
        placeholder_values = [
            'your_account_id_here',
            'your_api_key_here', 
            'your_secret_key_here',
            'your_private_key_here',
            'your_public_key_here',
            'your_trading_account_id_here',
            'your_stark_private_key_here',
            'PLACEHOLDER',
            'placeholder',
            ''
        ]
    
    # Check if credential exists
    if not credential_value:
        raise MissingCredentialsError(f"Missing {credential_name} environment variable")
    
    # Check for placeholder values
    if credential_value in placeholder_values:
        raise MissingCredentialsError(f"{credential_name} is not configured (placeholder or empty)")


def query_retry(
    default_return: Any = None,
    exception_type: Union[Type[Exception], Tuple[Type[Exception], ...]] = (Exception,),
    max_attempts: int = 5,
    min_wait: float = 1,
    max_wait: float = 10,
    reraise: bool = False
):
    """
    Retry decorator for query operations with exponential backoff.
    
    Args:
        default_return: Value to return if all retries fail
        exception_type: Exception types to retry on
        max_attempts: Maximum number of retry attempts
        min_wait: Minimum wait time between retries
        max_wait: Maximum wait time between retries
        reraise: Whether to reraise the exception after retries
    """
    def retry_error_callback(retry_state: RetryCallState):
        print(f"Operation: [{retry_state.fn.__name__}] failed after {retry_state.attempt_number} retries, "
              f"exception: {str(retry_state.outcome.exception())}")
        return default_return

    return retry(
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=1, min=min_wait, max=max_wait),
        retry=retry_if_exception_type(exception_type),
        retry_error_callback=retry_error_callback,
        reraise=reraise
    )


# ============================================================================
# DATA CLASSES
# ============================================================================

@dataclass
class OrderResult:
    """Standardized order result structure returned by order placement methods."""
    success: bool
    order_id: Optional[str] = None
    side: Optional[str] = None
    size: Optional[Decimal] = None
    price: Optional[Decimal] = None
    status: Optional[str] = None
    error_message: Optional[str] = None
    filled_size: Optional[Decimal] = None


@dataclass
class OrderInfo:
    """Standardized order information structure returned by order queries."""
    order_id: str
    side: str
    size: Decimal
    price: Decimal
    status: str
    filled_size: Decimal = 0.0
    remaining_size: Decimal = 0.0
    cancel_reason: str = ''

@dataclass
class ExchangePositionSnapshot:
    """
    Normalized position snapshot for a single trading symbol on an exchange.

    All numeric fields use Decimal for precision and are optional unless noted.
    """

    symbol: str
    quantity: Decimal = Decimal("0")
    side: Optional[str] = None
    entry_price: Optional[Decimal] = None
    mark_price: Optional[Decimal] = None
    exposure_usd: Optional[Decimal] = None
    unrealized_pnl: Optional[Decimal] = None
    realized_pnl: Optional[Decimal] = None
    funding_accrued: Optional[Decimal] = None
    margin_reserved: Optional[Decimal] = None
    leverage: Optional[Decimal] = None
    liquidation_price: Optional[Decimal] = None
    timestamp: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class FundingRateSample:
    """
    Standardized funding rate payload returned by funding adapters.
    """
    
    normalized_rate: Decimal
    raw_rate: Decimal
    interval_hours: Decimal
    next_funding_time: Optional[datetime] = None
    source_timestamp: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


# ============================================================================
# TRADING EXECUTION INTERFACE
# ============================================================================

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
    # LIQUIDATION EVENT STREAMS (OPTIONAL)
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
            
        Example Implementation (Query from API):
            ```python
            async def get_leverage_info(self, symbol: str) -> Dict[str, Any]:
                try:
                    normalized_symbol = f"{symbol}USDT"
                    result = await self._make_request('GET', '/api/v1/leverageBracket', 
                                                       {'symbol': normalized_symbol})
                    brackets = result.get('brackets', [])
                    
                    if not brackets:
                        return {
                            'max_leverage': None,
                            'max_notional': None,
                            'margin_requirement': None,
                            'brackets': None,
                            'error': f'No leverage data available for {symbol}'
                        }
                    
                    first_bracket = brackets[0]
                    return {
                        'max_leverage': Decimal(str(first_bracket.get('initialLeverage', 10))),
                        'max_notional': Decimal(str(first_bracket.get('notionalCap'))) if first_bracket.get('notionalCap') else None,
                        'margin_requirement': Decimal('1') / Decimal(str(first_bracket.get('initialLeverage', 10))),
                        'brackets': brackets,
                        'error': None
                    }
                except Exception as e:
                    return {
                        'max_leverage': None,
                        'max_notional': None,
                        'margin_requirement': None,
                        'brackets': None,
                        'error': f'Failed to query leverage info: {str(e)}'
                    }
            ```
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

    @abstractmethod 
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


# ============================================================================
# FUNDING RATE COLLECTION INTERFACE
# ============================================================================

class BaseFundingAdapter(ABC):
    """
    Base interface for funding rate collection from perpetual DEXs.
    
    This interface is used by the funding rate service to collect funding rates
    and market data from exchanges. It's read-only and doesn't require authentication
    in most cases (uses public endpoints).
    
    Key Responsibilities:
        - Fetch funding rates for all available symbols
        - Fetch market data (volume, open interest)
        - Normalize symbol formats across exchanges
        - Handle exchange-specific API quirks
        - Retry logic and error handling
    
    Implementation Pattern:
        Each exchange should implement this interface in a funding_adapter.py file:
        
        ```python
        class AsterFundingAdapter(BaseFundingAdapter):
            def __init__(self, api_base_url: str = "https://fapi.asterdex.com", timeout: int = 10):
                super().__init__(dex_name="aster", api_base_url=api_base_url, timeout=timeout)
                
            async def fetch_funding_rates(self) -> Dict[str, FundingRateSample]:
                # Fetch from exchange API
                result = await self._make_request("/api/v1/fundingRate")
                # Parse and normalize
                return {
                    \"BTC\": FundingRateSample(
                        normalized_rate=Decimal(result[\"BTC\"][\"rate\"]),
                        raw_rate=Decimal(result[\"BTC\"][\"rate\"]),
                        interval_hours=self.CANONICAL_INTERVAL_HOURS,
                    )
                }
        ```
    """
    
    CANONICAL_INTERVAL_HOURS: Decimal = Decimal("8")
    
    def __init__(self, dex_name: str, api_base_url: str, timeout: int = 10):
        """
        Initialize base funding adapter.
        
        Args:
            dex_name: Name of the DEX (e.g., "lighter", "edgex", "aster")
            api_base_url: Base URL for the DEX API
            timeout: Request timeout in seconds
        """
        self.dex_name = dex_name
        self.api_base_url = api_base_url
        self.timeout = timeout
        self._session: Optional[aiohttp.ClientSession] = None
    
    # ========================================================================
    # CORE DATA FETCHING
    # ========================================================================
    
    @abstractmethod
    async def fetch_funding_rates(self) -> Dict[str, FundingRateSample]:
        """
        Fetch all funding rates from this DEX.
        
        Returns:
            Dictionary mapping normalized symbols to `FundingRateSample` entries.
            
        Example:
            {
                "BTC": FundingRateSample(
                    normalized_rate=Decimal("0.0001"),  # 0.01% per 8h
                    raw_rate=Decimal("0.0001"),
                    interval_hours=Decimal("8")
                )
            }
            
        Raises:
            Exception: If fetching fails after retries
        """
        pass
    
    @abstractmethod
    async def fetch_market_data(self) -> Dict[str, Dict[str, Decimal]]:
        """
        Fetch market data (volume, open interest) for all symbols.
        
        Returns:
            Dictionary mapping normalized symbols to market data
            
        Example:
            {
                "BTC": {
                    "volume_24h": Decimal("1500000.0"),      # $1.5M daily volume
                    "open_interest": Decimal("5000000.0")    # $5M open interest
                },
                "ETH": {
                    "volume_24h": Decimal("800000.0"),
                    "open_interest": Decimal("2000000.0")
                }
            }
            
        Note:
            - volume_24h should be in USD
            - open_interest should be in USD
            - Both fields are optional (can be None or omitted)
            - Spread is NOT included here (too volatile, fetch client-side)
            
        Raises:
            Exception: If fetching fails after retries
        """
        pass
    
    # ========================================================================
    # SYMBOL NORMALIZATION
    # ========================================================================
    
    @abstractmethod
    def normalize_symbol(self, dex_symbol: str) -> str:
        """
        Normalize DEX-specific symbol format to standard format.
        
        Standard format: Base asset only, uppercase (e.g., "BTC", "ETH", "ZORA")
        
        Args:
            dex_symbol: DEX-specific format
            
        Returns:
            Normalized symbol
            
        Examples:
            - "BTC-PERP" -> "BTC"
            - "PERP_BTC_USDC" -> "BTC"
            - "BTCUSDT" -> "BTC"
            - "1000PEPEUSDT" -> "PEPE" (handle multipliers)
        """
        pass
    
    @abstractmethod
    def get_dex_symbol_format(self, normalized_symbol: str) -> str:
        """
        Convert normalized symbol back to DEX-specific format.
        
        Args:
            normalized_symbol: Normalized symbol (e.g., "BTC")
            
        Returns:
            DEX-specific format
            
        Examples:
            - "BTC" -> "BTC-PERP"
            - "BTC" -> "PERP_BTC_USDC"
            - "BTC" -> "BTCUSDT"
        """
        pass
    
    # ========================================================================
    # HTTP SESSION MANAGEMENT
    # ========================================================================
    
    async def get_session(self) -> aiohttp.ClientSession:
        """
        Get or create aiohttp session for HTTP requests.
        
        Returns:
            Active aiohttp ClientSession
        """
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout)
            )
        return self._session
    
    async def close(self) -> None:
        """
        Close the HTTP session and cleanup resources.
        """
        if self._session and not self._session.closed:
            await self._session.close()
    
    # ========================================================================
    # HTTP REQUEST UTILITIES
    # ========================================================================
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((asyncio.TimeoutError, aiohttp.ClientError))
    )
    async def _make_request(
        self,
        endpoint: str,
        method: str = "GET",
        params: Optional[Dict] = None,
        json_data: Optional[Dict] = None
    ) -> Dict:
        """
        Make HTTP request with automatic retry logic.
        
        Args:
            endpoint: API endpoint (will be appended to base_url)
            method: HTTP method (GET, POST, etc.)
            params: Query parameters
            json_data: JSON body data
            
        Returns:
            Response JSON as dictionary
            
        Raises:
            aiohttp.ClientError: On connection/HTTP errors (after retries)
            asyncio.TimeoutError: On timeout (after retries)
        """
        session = await self.get_session()
        url = f"{self.api_base_url}{endpoint}"
        
        try:
            async with session.request(
                method,
                url,
                params=params,
                json=json_data
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    raise aiohttp.ClientError(
                        f"API returned {response.status}: {error_text}"
                    )
                
                return await response.json()
        
        except asyncio.TimeoutError:
            raise
        
        except aiohttp.ClientError as e:
            raise
    
    # ========================================================================
    # METRICS & MONITORING
    # ========================================================================
    
    async def fetch_with_metrics(self) -> tuple[Dict[str, FundingRateSample], int]:
        """
        Fetch funding rates with collection latency metrics.
        
        Returns:
            Tuple of (rates_dict, latency_ms)
            
        Example:
            >>> rates, latency = await adapter.fetch_with_metrics()
            >>> print(f"Fetched {len(rates)} rates in {latency}ms")
        """
        start_time = asyncio.get_event_loop().time()
        
        try:
            rates = await self.fetch_funding_rates()
            latency_ms = int((asyncio.get_event_loop().time() - start_time) * 1000)
            return rates, latency_ms
        
        except Exception as e:
            latency_ms = int((asyncio.get_event_loop().time() - start_time) * 1000)
            raise
    
    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} dex={self.dex_name}>"
