"""
Base exchange client and funding adapter interfaces.
All exchange implementations should inherit from these classes.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional, Tuple, Type, Union
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from tenacity import RetryCallState, retry, retry_if_exception_type, stop_after_attempt, wait_exponential
import aiohttp
import asyncio


class MissingCredentialsError(Exception):
    """Raised when exchange credentials are missing or invalid (placeholders)."""
    pass


def validate_credentials(credential_name: str, credential_value: Optional[str], 
                        placeholder_values: Optional[list] = None) -> None:
    """
    Helper function to validate exchange credentials.
    
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
    Retry decorator for query operations
    
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


@dataclass
class OrderResult:
    """Standardized order result structure."""
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
    """Standardized order information structure."""
    order_id: str
    side: str
    size: Decimal
    price: Decimal
    status: str
    filled_size: Decimal = 0.0
    remaining_size: Decimal = 0.0
    cancel_reason: str = ''


# ============================================================================
# TRADING EXECUTION INTERFACE
# ============================================================================

class BaseExchangeClient(ABC):
    """
    Base interface for trading execution
    
    This interface is used by the trading bot to execute orders, manage positions,
    and interact with exchange trading APIs.
    """

    def __init__(self, config: Dict[str, Any]):
        """Initialize the exchange client with configuration."""
        self.config = config
        self._validate_config()

    def round_to_tick(self, price) -> Decimal:
        """Round price to tick size"""
        price = Decimal(price)
        tick = self.config.tick_size
        return price.quantize(tick, rounding=ROUND_HALF_UP)

    @abstractmethod
    def _validate_config(self) -> None:
        """Validate the exchange-specific configuration."""
        pass

    @abstractmethod
    async def connect(self) -> None:
        """Connect to the exchange (WebSocket, etc.)."""
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect from the exchange."""
        pass

    @abstractmethod
    async def fetch_bbo_prices(self, contract_id: str) -> Tuple[Decimal, Decimal]:
        """
        Fetch best bid and offer prices for a contract.
        
        Args:
            contract_id: Contract/symbol identifier
            
        Returns:
            Tuple of (best_bid, best_ask)
            
        Raises:
            Exception: If fetching fails
        """
        pass

    @abstractmethod
    async def place_limit_order(
        self, 
        contract_id: str, 
        quantity: Decimal, 
        price: Decimal, 
        side: str
    ) -> OrderResult:
        """
        Place a limit order.
        
        Args:
            contract_id: Contract/symbol identifier
            quantity: Order size
            price: Limit price
            side: 'buy' or 'sell'
            
        Returns:
            OrderResult with order details
        """
        pass

    @abstractmethod
    async def place_open_order(self, contract_id: str, quantity: Decimal, direction: str) -> OrderResult:
        """Place an open order."""
        pass

    @abstractmethod
    async def place_close_order(self, contract_id: str, quantity: Decimal, price: Decimal, side: str) -> OrderResult:
        """Place a close order."""
        pass

    @abstractmethod
    async def place_market_order(self, contract_id: str, quantity: Decimal, side: str) -> OrderResult:
        """
        Place a market order (taker order for immediate execution).
        
        Args:
            contract_id: Contract/symbol identifier
            quantity: Order size
            side: 'buy' or 'sell'
            
        Returns:
            OrderResult with order details
            
        Note:
            If exchange doesn't support true market orders, implement using
            aggressive limit order priced to execute immediately.
        """
        pass

    @abstractmethod
    async def cancel_order(self, order_id: str) -> OrderResult:
        """Cancel an order."""
        pass

    @abstractmethod
    async def get_order_info(self, order_id: str) -> Optional[OrderInfo]:
        """Get order information."""
        pass

    @abstractmethod
    async def get_active_orders(self, contract_id: str) -> List[OrderInfo]:
        """Get active orders for a contract."""
        pass

    @abstractmethod
    async def fetch_bbo_prices(self, contract_id: str) -> Tuple[Decimal, Decimal]:
        """
        Fetch best bid and offer prices for a contract.
        
        Args:
            contract_id: Contract/symbol identifier
            
        Returns:
            Tuple of (best_bid, best_ask)
            
        Raises:
            Exception: If fetching fails
        """
        pass

    @abstractmethod
    async def place_limit_order(
        self, 
        contract_id: str, 
        quantity: Decimal, 
        price: Decimal, 
        side: str
    ) -> OrderResult:
        """
        Place a limit order.
        
        Args:
            contract_id: Contract/symbol identifier
            quantity: Order size
            price: Limit price
            side: 'buy' or 'sell'
            
        Returns:
            OrderResult with order details
        """
        pass

    @abstractmethod
    async def get_account_positions(self) -> Decimal:
        """Get account positions."""
        pass

    @abstractmethod
    async def get_order_book_depth(
        self, 
        contract_id: str, 
        levels: int = 10
    ) -> Dict[str, List[Dict[str, Decimal]]]:
        """
        Get order book depth for liquidity analysis.
        
        Args:
            contract_id: Contract/symbol identifier
            levels: Number of price levels to fetch (default: 10)
            
        Returns:
            Dictionary with 'bids' and 'asks' lists of dicts with 'price' and 'size'
            Example: {
                'bids': [{'price': Decimal('50000'), 'size': Decimal('1.5')}, ...],
                'asks': [{'price': Decimal('50001'), 'size': Decimal('2.0')}, ...]
            }
        """
        pass
    
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

    @abstractmethod
    def setup_order_update_handler(self, handler) -> None:
        """Setup order update handler for WebSocket."""
        pass

    @abstractmethod
    def get_exchange_name(self) -> str:
        """Get the exchange name."""
        pass

    # Optional risk management methods (exchanges can override if supported)
    async def get_account_balance(self) -> Optional[Decimal]:
        """Get current account balance. Override if exchange supports it."""
        return None
    
    async def get_detailed_positions(self) -> List[Dict[str, Any]]:
        """Get detailed position info. Override if exchange supports it."""
        return []
    
    async def get_account_pnl(self) -> Optional[Decimal]:
        """Get account P&L. Override if exchange supports it."""
        return None
    
    async def get_total_asset_value(self) -> Optional[Decimal]:
        """Get total account asset value. Override if exchange supports it."""
        return None
    
    def supports_risk_management(self) -> bool:
        """Check if exchange supports advanced risk management."""
        return False
    
    async def get_leverage_info(self, symbol: str) -> Dict[str, Any]:
        """
        Get leverage and position limit information for a symbol.
        
        ⚠️ IMPORTANT for delta-neutral strategies: Different exchanges have different
        leverage limits for the same symbol. This method allows pre-flight validation
        to ensure both sides of a delta-neutral trade can execute with the same size.
        
        Args:
            symbol: Trading symbol (normalized format, e.g., "ZORA", "BTC")
            
        Returns:
            Dictionary with leverage limits:
            {
                'max_leverage': Decimal or None,  # e.g., Decimal('10') for 10x
                'max_notional': Decimal or None,  # Max position value in USD
                'margin_requirement': Decimal or None,  # e.g., Decimal('0.1') = 10% = 10x leverage
                'brackets': List or None  # Leverage brackets if available
            }
            
        Default Implementation:
            Returns conservative defaults (10x leverage, 10% margin requirement).
            Override this method in exchange clients to query actual limits from APIs.
            
        Example Override:
            ```python
            async def get_leverage_info(self, symbol: str) -> Dict[str, Any]:
                result = await self._make_request('GET', '/api/v1/leverage_info')
                return {
                    'max_leverage': Decimal(str(result['maxLeverage'])),
                    'max_notional': Decimal(str(result['maxPositionSize'])),
                    'margin_requirement': Decimal('1') / Decimal(str(result['maxLeverage'])),
                    'brackets': result.get('brackets')
                }
            ```
        """
        # Default implementation: Conservative 10x leverage
        return {
            'max_leverage': Decimal('10'),
            'max_notional': None,
            'margin_requirement': Decimal('0.10'),  # 10% margin = 10x leverage
            'brackets': None
        }


# ============================================================================
# FUNDING RATE COLLECTION INTERFACE
# ============================================================================

class BaseFundingAdapter(ABC):
    """
    Base interface for funding rate collection
    
    This interface is used by the funding rate service to collect funding rates
    and market data from exchanges. It's read-only and doesn't require authentication
    in most cases.
    
    Each DEX adapter is responsible for:
    1. Fetching funding rates from the DEX API
    2. Parsing the API response into a standard format
    3. Handling DEX-specific API quirks
    4. Error handling and retries
    """
    
    def __init__(self, dex_name: str, api_base_url: str, timeout: int = 10):
        """
        Initialize base adapter
        
        Args:
            dex_name: Name of the DEX (e.g., "lighter", "edgex")
            api_base_url: Base URL for the DEX API
            timeout: Request timeout in seconds
        """
        self.dex_name = dex_name
        self.api_base_url = api_base_url
        self.timeout = timeout
        self._session: Optional[aiohttp.ClientSession] = None
    
    @abstractmethod
    async def fetch_funding_rates(self) -> Dict[str, Decimal]:
        """
        Fetch all funding rates from this DEX
        
        Returns:
            Dictionary mapping normalized symbols to funding rates
            Example: {"BTC": Decimal("0.0001"), "ETH": Decimal("0.00008")}
            
        Raises:
            Exception: If fetching fails after retries
        """
        pass
    
    @abstractmethod
    async def fetch_market_data(self) -> Dict[str, Dict[str, Decimal]]:
        """
        Fetch market data (volume, open interest) for all symbols
        
        Returns:
            Dictionary mapping normalized symbols to market data
            Example: {
                "BTC": {
                    "volume_24h": Decimal("1500000.0"),
                    "open_interest": Decimal("5000000.0")
                },
                "ETH": {...}
            }
            
        Note:
            - volume_24h should be in USD
            - open_interest should be in USD
            - Both fields are optional (can be None)
            - Spread is NOT included here (too volatile, fetch client-side)
            
        Raises:
            Exception: If fetching fails after retries
        """
        pass
    
    @abstractmethod
    def normalize_symbol(self, dex_symbol: str) -> str:
        """
        Normalize DEX-specific symbol format to standard format
        
        Args:
            dex_symbol: DEX-specific format (e.g., "BTC-PERP", "PERP_BTC_USDC")
            
        Returns:
            Normalized symbol (e.g., "BTC")
        """
        pass
    
    @abstractmethod
    def get_dex_symbol_format(self, normalized_symbol: str) -> str:
        """
        Convert normalized symbol back to DEX-specific format
        
        Args:
            normalized_symbol: Normalized symbol (e.g., "BTC")
            
        Returns:
            DEX-specific format (e.g., "BTC-PERP")
        """
        pass
    
    async def get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout)
            )
        return self._session
    
    async def close(self) -> None:
        """Close the HTTP session"""
        if self._session and not self._session.closed:
            await self._session.close()
    
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
        Make HTTP request with retry logic
        
        Args:
            endpoint: API endpoint (will be appended to base_url)
            method: HTTP method
            params: Query parameters
            json_data: JSON body data
            
        Returns:
            Response JSON as dictionary
            
        Raises:
            aiohttp.ClientError: On connection/HTTP errors
            asyncio.TimeoutError: On timeout
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
    
    async def fetch_with_metrics(self) -> tuple[Dict[str, Decimal], int]:
        """
        Fetch funding rates with collection latency metrics
        
        Returns:
            Tuple of (rates_dict, latency_ms)
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

