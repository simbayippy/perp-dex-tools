"""
Aster exchange client implementation for trading execution.
"""

import os
import asyncio
import time
import hmac
import hashlib
from decimal import Decimal, ROUND_DOWN
from typing import Dict, Any, List, Optional, Tuple, Callable, Awaitable
from urllib.parse import urlencode
import aiohttp

from exchange_clients.base_client import BaseExchangeClient, OrderFillCallback, OrderStatusCallback
from exchange_clients.base_models import (
    OrderResult,
    OrderInfo,
    ExchangePositionSnapshot,
    TradeData,
    validate_credentials,
)
from exchange_clients.aster.common import get_aster_symbol_format, get_quantity_multiplier
from exchange_clients.aster.websocket import AsterWebSocketManager
from helpers.unified_logger import get_exchange_logger

from .utils import to_decimal
from .utils.caching import TickSizeCache, ContractIdCache
from .managers.market_data import AsterMarketData
from .managers.order_manager import AsterOrderManager
from .managers.position_manager import AsterPositionManager
from .managers.account_manager import AsterAccountManager
from .managers.websocket_handlers import AsterWebSocketHandlers


class AsterClient(BaseExchangeClient):
    """Aster exchange client implementation."""

    def __init__(
        self, 
        config: Dict[str, Any],
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        order_fill_callback: OrderFillCallback = None,
        order_status_callback: OrderStatusCallback = None,
    ):
        """
        Initialize Aster client.
        
        Args:
            config: Trading configuration dictionary
            api_key: Optional API key (falls back to env var)
            secret_key: Optional secret key (falls back to env var)
            order_fill_callback: Optional callback for order fills
            order_status_callback: Optional callback for order status changes (FILLED/CANCELED)
        """
        # Set credentials BEFORE calling super().__init__() because it triggers _validate_config()
        self.api_key = api_key or os.getenv('ASTER_API_KEY')
        self.secret_key = secret_key or os.getenv('ASTER_SECRET_KEY')
        self.base_url = 'https://fapi.asterdex.com'
        
        super().__init__(config)
        if order_fill_callback is not None:
            self.order_fill_callback = order_fill_callback
        if order_status_callback is not None:
            self.order_status_callback = order_status_callback

        # Initialize logger early
        self.logger = get_exchange_logger("aster", self.config.ticker)
        self._order_update_handler = None
        self._latest_orders: Dict[str, OrderInfo] = {}
        self._min_order_notional: Dict[str, Decimal] = {}
        
        # Caches (single source of truth)
        self._tick_size_cache = TickSizeCache()
        self._contract_id_cache = ContractIdCache()
        
        # Manager references (initialized in connect())
        self.market_data: Optional[AsterMarketData] = None
        self.order_manager: Optional[AsterOrderManager] = None
        self.position_manager: Optional[AsterPositionManager] = None
        self.account_manager: Optional[AsterAccountManager] = None
        self.ws_handlers: Optional[AsterWebSocketHandlers] = None
        self.ws_manager: Optional[AsterWebSocketManager] = None

    def _validate_config(self) -> None:
        """Validate Aster configuration."""
        # Validate the instance attributes (which may come from params or env)
        validate_credentials('ASTER_API_KEY', self.api_key)
        validate_credentials('ASTER_SECRET_KEY', self.secret_key)

    def _generate_signature(self, params: Dict[str, Any]) -> str:
        """Generate HMAC SHA256 signature for Aster API authentication."""
        # Use urlencode to properly format the query string
        query_string = urlencode(params)

        # Generate HMAC SHA256 signature
        signature = hmac.new(
            self.secret_key.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

        return signature

    async def _make_request(
        self, method: str, endpoint: str, params: Dict[str, Any] = None, data: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """Make authenticated request to Aster API."""
        if params is None:
            params = {}
        if data is None:
            data = {}

        # Add timestamp and recvWindow
        timestamp = int(time.time() * 1000)
        params['timestamp'] = timestamp
        params['recvWindow'] = 5000

        url = f"{self.base_url}{endpoint}"
        headers = {
            'X-MBX-APIKEY': self.api_key,
            'Content-Type': 'application/x-www-form-urlencoded'
        }

        async with aiohttp.ClientSession() as session:
            if method.upper() == 'GET':
                # For GET requests, signature is based on query parameters only
                signature = self._generate_signature(params)
                params['signature'] = signature

                async with session.get(url, params=params, headers=headers) as response:
                    result = await response.json()
                    if response.status != 200:
                        raise Exception(f"API request failed: {result}")
                    return result
            elif method.upper() == 'POST':
                # For POST requests, signature must include both query string and request body
                # According to Aster API docs: totalParams = queryString + requestBody
                all_params = {**params, **data}
                
                self.logger.debug(
                    f"POST {endpoint} - Params: {params}, Data: {data}"
                )
                
                signature = self._generate_signature(all_params)
                all_params['signature'] = signature

                async with session.post(url, data=all_params, headers=headers) as response:
                    result = await response.json()
                    self.logger.debug(
                        f"Response {response.status}: {result.get('orderId', result.get('status', 'N/A'))}"
                    )
                    if response.status != 200:
                        raise Exception(f"API request failed: {result}")
                    return result
            elif method.upper() == 'DELETE':
                # For DELETE requests, signature is based on query parameters only
                signature = self._generate_signature(params)
                params['signature'] = signature

                async with session.delete(url, params=params, headers=headers) as response:
                    result = await response.json()
                    if response.status != 200:
                        raise Exception(f"API request failed: {result}")
                    return result

    async def connect(self) -> None:
        """Connect to Aster and initialize managers."""
        try:
            # Initialize market data manager (needed before other managers)
            self.market_data = AsterMarketData(
                make_request_fn=self._make_request,
                config=self.config,
                logger=self.logger,
                tick_size_cache=self._tick_size_cache,
                contract_id_cache=self._contract_id_cache,
                min_order_notional=self._min_order_notional,
                ws_manager=None,  # Will be set after ws_manager is created
                normalize_symbol_fn=self.normalize_symbol,
            )

            # Initialize order manager
            self.order_manager = AsterOrderManager(
                make_request_fn=self._make_request,
                config=self.config,
                logger=self.logger,
                latest_orders=self._latest_orders,
                tick_size_cache=self._tick_size_cache,
                min_order_notional=self._min_order_notional,
                market_data_manager=self.market_data,
                normalize_symbol_fn=self.normalize_symbol,
                round_to_step_fn=self.round_to_step,
                get_min_order_notional_fn=self.get_min_order_notional,
            )
            
            # Initialize position manager
            self.position_manager = AsterPositionManager(
                make_request_fn=self._make_request,
                config=self.config,
                logger=self.logger,
                normalize_symbol_fn=self.normalize_symbol,
            )
            
            # Initialize account manager
            self.account_manager = AsterAccountManager(
                make_request_fn=self._make_request,
                config=self.config,
                logger=self.logger,
                min_order_notional=self._min_order_notional,
                normalize_symbol_fn=self.normalize_symbol,
            )
            
            # Initialize WebSocket handlers (after all managers are created)
            self.ws_handlers = AsterWebSocketHandlers(
                config=self.config,
                logger=self.logger,
                latest_orders=self._latest_orders,
                order_update_handler=self._order_update_handler,
                order_fill_callback=self.order_fill_callback,
                order_status_callback=self.order_status_callback,
                order_manager=self.order_manager,
                position_manager=self.position_manager,
                emit_liquidation_event_fn=self.emit_liquidation_event,
                get_exchange_name_fn=self.get_exchange_name,
                normalize_symbol_fn=self.normalize_symbol,
            )
            
            # Initialize WebSocket manager
            self.ws_manager = AsterWebSocketManager(
                config=self.config,
                api_key=self.api_key,
                secret_key=self.secret_key,
                order_update_callback=self.ws_handlers.handle_websocket_order_update,
                liquidation_callback=self.ws_handlers.handle_liquidation_notification,
                symbol_formatter=self.normalize_symbol,
            )

            # Set logger for WebSocket manager
            self.ws_manager.set_logger(self.logger)
            
            # Update market_data with ws_manager reference
            self.market_data.ws_manager = self.ws_manager

            # Start WebSocket connection in background task
            asyncio.create_task(self.ws_manager.connect())
            # Wait a moment for connection to establish
            await asyncio.sleep(2)
            
        except Exception as e:
            self.logger.error(f"Error connecting to Aster: {e}")
            raise

    async def disconnect(self) -> None:
        """Disconnect from Aster."""
        try:
            if hasattr(self, 'ws_manager') and self.ws_manager:
                await self.ws_manager.disconnect()
        except Exception as e:
            self.logger.error(f"Error during Aster disconnect: {e}")

    def get_exchange_name(self) -> str:
        """Get the exchange name."""
        return "aster"

    def supports_liquidation_stream(self) -> bool:
        """Aster user data streams emit forceOrder events for account liquidations."""
        return True
    
    def normalize_symbol(self, symbol: str) -> str:
        """
        Convert normalized symbol to Aster's expected format.
        
        Uses the existing `get_aster_symbol_format()` from common.py.
        
        Args:
            symbol: Normalized symbol (e.g., "BTC", "ETH", "ZORA")
            
        Returns:
            Aster-formatted symbol (e.g., "BTCUSDT", "ETHUSDT", "ZORAUSDT")
        """
        return get_aster_symbol_format(symbol)
    
    def get_quantity_multiplier(self, symbol: str) -> int:
        """
        Get the quantity multiplier for a symbol on Aster.
        
        Aster's 1000-prefix tokens (1000BONKUSDT, etc.) have a 1000x multiplier,
        similar to Lighter's k-prefix tokens.
        
        The price shown is 1000x the actual token price.
        Example: 1000BONKUSDT shows $0.01467, CoinGecko shows $0.00001467 (1000x)
        
        Args:
            symbol: Normalized symbol (e.g., "TOSHI", "BTC")
            
        Returns:
            1000 for 1000-prefix tokens, 1 for others
        """
        return get_quantity_multiplier(symbol)
    
    def round_to_step(self, quantity: Decimal) -> Decimal:
        """
        Round quantity to the exchange's step size.
        
        Args:
            quantity: Raw quantity
            
        Returns:
            Rounded quantity that meets step size requirements
        """
        step_size = getattr(self.config, 'step_size', Decimal('1'))
        
        # Round down to nearest step size
        return (quantity / step_size).quantize(Decimal('1'), rounding=ROUND_DOWN) * step_size

    # WebSocket handler delegates
    async def _handle_websocket_order_update(self, order_data: Dict[str, Any]):
        """Handle order updates from WebSocket. Delegates to ws_handlers."""
        await self.ws_handlers.handle_websocket_order_update(order_data)

    async def handle_liquidation_notification(self, payload: Dict[str, Any]) -> None:
        """Normalize liquidation notifications. Delegates to ws_handlers."""
        await self.ws_handlers.handle_liquidation_notification(payload)

    # Market data delegates
    async def fetch_bbo_prices(self, contract_id: str) -> Tuple[Decimal, Decimal]:
        """Get best bid/offer prices, preferring WebSocket data when available."""
        return await self.market_data.fetch_bbo_prices(contract_id)

    async def get_order_book_depth(
        self, 
        contract_id: str, 
        levels: int = 10
    ) -> Dict[str, List[Dict[str, Decimal]]]:
        """Get order book depth for a symbol."""
        return await self.market_data.get_order_book_depth(contract_id, levels)

    async def get_order_price(self, direction: str) -> Decimal:
        """Get the price of an order with Aster."""
        return await self.market_data.get_order_price(direction, self.config.contract_id)

    async def get_contract_attributes(self) -> Tuple[str, Decimal]:
        """Get contract ID and tick size for a ticker."""
        return await self.market_data.get_contract_attributes()

    # Order management delegates
    async def place_limit_order(
        self,
        contract_id: str,
        quantity: Decimal,
        price: Decimal,
        side: str,
        reduce_only: bool = False,
        client_order_id: Optional[int] = None,
    ) -> OrderResult:
        """Place a limit order at a specific price on Aster."""
        return await self.order_manager.place_limit_order(
            contract_id=contract_id,
            quantity=quantity,
            price=price,
            side=side,
            reduce_only=reduce_only,
            client_order_id=client_order_id,
        )

    async def place_market_order(
        self,
        contract_id: str,
        quantity: Decimal,
        side: str,
        reduce_only: bool = False,
        client_order_id: Optional[int] = None,
    ) -> OrderResult:
        """Place a market order on Aster."""
        return await self.order_manager.place_market_order(
            contract_id=contract_id,
            quantity=quantity,
            side=side,
            reduce_only=reduce_only,
            client_order_id=client_order_id,
        )

    async def cancel_order(self, order_id: str) -> OrderResult:
        """Cancel an order with Aster."""
        return await self.order_manager.cancel_order(order_id, self.config.contract_id)

    async def get_order_info(self, order_id: str, *, force_refresh: bool = False) -> Optional[OrderInfo]:
        """Get order information from Aster."""
        return await self.order_manager.get_order_info(order_id, self.config.contract_id, force_refresh=force_refresh)
    
    async def await_order_update(self, order_id: str, timeout: float = 10.0) -> Optional[OrderInfo]:
        """
        Wait for websocket order update with optional timeout.
        
        This method efficiently waits for order status changes via websocket,
        falling back to REST API polling if websocket update doesn't arrive.
        
        Args:
            order_id: Order identifier to wait for
            timeout: Maximum time to wait in seconds (default: 10.0)
            
        Returns:
            OrderInfo if update received within timeout, None otherwise
        """
        if not self.order_manager:
            raise RuntimeError("Order manager not initialized. Call connect() first.")
        return await self.order_manager.await_order_update(order_id, timeout)

    async def get_active_orders(self, contract_id: str) -> List[OrderInfo]:
        """Get active orders for a contract from Aster."""
        return await self.order_manager.get_active_orders(contract_id)

    # Position management delegates
    async def get_account_positions(self) -> Decimal:
        """Get account positions from Aster."""
        return await self.position_manager.get_account_positions(self.config.contract_id)

    async def get_position_snapshot(
        self, 
        symbol: str,
        position_opened_at: Optional[float] = None,
    ) -> Optional[ExchangePositionSnapshot]:
        """Return the current position snapshot for a symbol."""
        return await self.position_manager.get_position_snapshot(symbol, position_opened_at=position_opened_at)

    # Account management delegates
    async def get_account_balance(self) -> Optional[Decimal]:
        """Get available account balance from Aster."""
        return await self.account_manager.get_account_balance()

    async def get_account_leverage(self, symbol: str) -> Optional[int]:
        """Get current account leverage setting for a symbol from Aster."""
        return await self.account_manager.get_account_leverage(symbol)

    async def set_account_leverage(self, symbol: str, leverage: int) -> bool:
        """Set account leverage for a symbol on Aster."""
        return await self.account_manager.set_account_leverage(symbol, leverage)

    async def get_leverage_info(self, symbol: str) -> Dict[str, Any]:
        """Get leverage and position limit information for a symbol."""
        return await self.account_manager.get_leverage_info(symbol)

    def get_min_order_notional(self, symbol: Optional[str]) -> Optional[Decimal]:
        """Return the minimum notional requirement for the given symbol if known."""
        return self.account_manager.get_min_order_notional(symbol)
    
    async def get_user_trade_history(
        self,
        symbol: str,
        start_time: float,
        end_time: float,
        order_id: Optional[str] = None,
    ) -> List[TradeData]:
        """
        Get user trade history for Aster using /fapi/v1/userTrades endpoint.
        
        Args:
            symbol: Trading symbol (normalized format, e.g., "BTC", "TOSHI")
            start_time: Start timestamp (Unix seconds)
            end_time: End timestamp (Unix seconds)
            order_id: Optional order ID to filter trades
            
        Returns:
            List of TradeData objects, or empty list on error
        """
        try:
            # Convert normalized symbol to Aster format (e.g., "BTC" -> "BTCUSDT")
            aster_symbol = self.normalize_symbol(symbol)
            
            # Resolve contract_id for the symbol (may use cache, but normalize first)
            contract_id = self.resolve_contract_id(symbol)
            
            # Use Aster-formatted symbol if resolve_contract_id didn't find a cached value
            # (resolve_contract_id falls back to symbol as-is if not in cache)
            if contract_id == symbol.upper():
                contract_id = aster_symbol
            
            # Build request parameters
            params = {
                'symbol': contract_id,
                'startTime': int(start_time * 1000),  # Convert to milliseconds
                'endTime': int(end_time * 1000),
                'limit': 1000,  # Maximum limit for Aster API
            }
            
            # Try to use orderId parameter if provided (Binance-style API may support it)
            if order_id:
                params['orderId'] = order_id
            
            # Make authenticated request
            trades = await self._make_request('GET', '/fapi/v1/userTrades', params)
            
            if not isinstance(trades, list):
                self.logger.warning(f"[ASTER] Unexpected trade history response format: {type(trades)}")
                return []
            
            # Parse trades into TradeData objects
            result = []
            for trade in trades:
                # Filter by order_id client-side if orderId parameter didn't work or wasn't used
                if order_id and trade.get('orderId') != order_id:
                    continue
                
                # Filter by timestamp range (API may not filter precisely)
                trade_time = trade.get('time', 0) / 1000  # Convert ms to seconds
                if trade_time < start_time or trade_time > end_time:
                    continue
                
                # Determine side
                is_buyer = trade.get('buyer', False)
                side = "buy" if is_buyer else "sell"
                
                # Extract trade data
                trade_id = str(trade.get('id', ''))
                quantity = Decimal(str(trade.get('qty', '0')))
                price = Decimal(str(trade.get('price', '0')))
                fee = Decimal(str(trade.get('commission', '0')))
                fee_currency = trade.get('commissionAsset', 'USDT')
                trade_order_id = str(trade.get('orderId', ''))
                
                result.append(TradeData(
                    trade_id=trade_id,
                    timestamp=trade_time,
                    symbol=symbol,
                    side=side,
                    quantity=quantity,
                    price=price,
                    fee=fee,
                    fee_currency=fee_currency,
                    order_id=trade_order_id if trade_order_id else None,
                ))
            
            return result
            
        except Exception as e:
            self.logger.warning(f"[ASTER] Failed to get trade history for {symbol}: {e}")
            import traceback
            self.logger.debug(f"[ASTER] Trade history error traceback: {traceback.format_exc()}")
            return []

