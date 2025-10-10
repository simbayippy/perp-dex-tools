"""
Lighter exchange client implementation for trading execution.
"""

import os
import asyncio
import time
import logging
import aiohttp
from decimal import Decimal
from typing import Dict, Any, List, Optional, Tuple

from exchange_clients.base import BaseExchangeClient, OrderResult, OrderInfo, query_retry, MissingCredentialsError, validate_credentials
from helpers.logger import TradingLogger

# Import official Lighter SDK for API client
import lighter
from lighter import SignerClient, ApiClient, Configuration

# Import custom WebSocket implementation
from .lighter_custom_websocket import LighterCustomWebSocketManager

# Suppress Lighter SDK debug logs
logging.getLogger('lighter').setLevel(logging.WARNING)
# Also suppress root logger DEBUG messages that might be coming from Lighter SDK
root_logger = logging.getLogger()
if root_logger.level == logging.DEBUG:
    root_logger.setLevel(logging.WARNING)


class LighterClient(BaseExchangeClient):
    """Lighter exchange client implementation."""

    def __init__(self, config: Dict[str, Any]):
        """Initialize Lighter client."""
        super().__init__(config)

        # Lighter credentials from environment (validation happens in _validate_config)
        self.api_key_private_key = os.getenv('API_KEY_PRIVATE_KEY')
        
        # Get indices with defaults (will be validated in _validate_config)
        account_index_str = os.getenv('LIGHTER_ACCOUNT_INDEX', '0')
        api_key_index_str = os.getenv('LIGHTER_API_KEY_INDEX', '0')
        
        # Only convert to int if not empty (to avoid errors before validation)
        self.account_index = int(account_index_str) if account_index_str else 0
        self.api_key_index = int(api_key_index_str) if api_key_index_str else 0
        self.base_url = "https://mainnet.zklighter.elliot.ai"

        # Initialize logger
        self.logger = TradingLogger(exchange="lighter", ticker=self.config.ticker, log_to_console=True)
        self._order_update_handler = None

        # Initialize Lighter client (will be done in connect)
        self.lighter_client = None

        # Initialize API client (will be done in connect)
        self.api_client = None
        
        # Initialize account API (will be done in connect)
        self.account_api = None

        # Market configuration
        self.base_amount_multiplier = None
        self.price_multiplier = None
        self.orders_cache = {}
        self.current_order_client_id = None
        self.current_order = None

    def _validate_config(self) -> None:
        """Validate Lighter configuration."""
        # Use base validation helper (reduces code duplication)
        validate_credentials('API_KEY_PRIVATE_KEY', os.getenv('API_KEY_PRIVATE_KEY'))
        
        # Note: LIGHTER_ACCOUNT_INDEX and LIGHTER_API_KEY_INDEX have defaults of '0'
        # which are valid values, so we don't need to validate them

    async def _get_market_id_for_symbol(self, symbol: str) -> Optional[int]:
        """
        Get Lighter market_id for a given symbol.
        
        Args:
            symbol: Trading symbol (e.g., 'BTC', 'ETH', 'G')
            
        Returns:
            Integer market_id, or None if not found
        """
        try:
            order_api = lighter.OrderApi(self.api_client)
            order_books = await order_api.order_books()
            
            # Collect all available symbols for better error messages
            available_symbols = []
            
            for market in order_books.order_books:
                available_symbols.append(market.symbol)
                # Try exact match
                if market.symbol == symbol:
                    return market.market_id
                # Try case-insensitive match
                elif market.symbol.upper() == symbol.upper():
                    return market.market_id
            
            # Symbol not found - provide helpful error message
            self.logger.log(
                f"❌ [LIGHTER] Symbol '{symbol}' NOT found in Lighter markets. "
                f"Available symbols: {', '.join(available_symbols[:10])}{'...' if len(available_symbols) > 10 else ''}",
                "WARNING"
            )
            return None
            
        except Exception as e:
            self.logger.log(
                f"❌ [LIGHTER] Error looking up market_id for symbol '{symbol}': {e}",
                "ERROR"
            )
            import traceback
            self.logger.log(f"Traceback: {traceback.format_exc()}", "ERROR")
            return None
    
    async def _get_market_config(self, ticker: str) -> Tuple[int, int, int]:
        """Get market configuration for a ticker using official SDK."""
        try:
            # Use shared API client
            order_api = lighter.OrderApi(self.api_client)

            # Get order books to find market info
            order_books = await order_api.order_books()

            for market in order_books.order_books:
                if market.symbol == ticker:
                    market_id = market.market_id
                    base_multiplier = pow(10, market.supported_size_decimals)
                    price_multiplier = pow(10, market.supported_price_decimals)

                    # Store market info for later use
                    self.config.market_info = market

                    self.logger.log(
                        f"Market config for {ticker}: ID={market_id}, "
                        f"Base multiplier={base_multiplier}, Price multiplier={price_multiplier}",
                        "INFO"
                    )
                    return market_id, base_multiplier, price_multiplier

            raise Exception(f"Ticker {ticker} not found in available markets")

        except Exception as e:
            self.logger.log(f"Error getting market config: {e}", "ERROR")
            raise

    async def _initialize_lighter_client(self):
        """Initialize the Lighter client using official SDK."""
        if self.lighter_client is None:
            try:
                self.lighter_client = SignerClient(
                    url=self.base_url,
                    private_key=self.api_key_private_key,
                    account_index=self.account_index,
                    api_key_index=self.api_key_index,
                )

                # Check client
                err = self.lighter_client.check_client()
                if err is not None:
                    raise Exception(f"CheckClient error: {err}")

                self.logger.log("Lighter client initialized successfully", "INFO")
            except Exception as e:
                self.logger.log(f"Failed to initialize Lighter client: {e}", "ERROR")
                raise
        return self.lighter_client

    async def connect(self) -> None:
        """Connect to Lighter."""
        try:
            # Initialize shared API client
            self.api_client = ApiClient(configuration=Configuration(host=self.base_url))

            # Initialize Lighter client
            await self._initialize_lighter_client()
            
            # Initialize account API for risk management
            self.account_api = lighter.AccountApi(self.api_client)

            # Add market config to config for WebSocket manager
            self.config.market_index = self.config.contract_id
            self.config.account_index = self.account_index
            self.config.lighter_client = self.lighter_client

            # Initialize WebSocket manager (using custom implementation)
            self.ws_manager = LighterCustomWebSocketManager(
                config=self.config,
                order_update_callback=self._handle_websocket_order_update
            )

            # Set logger for WebSocket manager
            self.ws_manager.set_logger(self.logger)

            # Start WebSocket connection in background task
            # (Used for real-time price updates and order tracking, not for liquidity checks)
            asyncio.create_task(self.ws_manager.connect())
            
            # Give WebSocket a moment to start connecting
            await asyncio.sleep(1)

        except Exception as e:
            self.logger.log(f"Error connecting to Lighter: {e}", "ERROR")
            raise

    async def disconnect(self) -> None:
        """Disconnect from Lighter."""
        try:
            if hasattr(self, 'ws_manager') and self.ws_manager:
                await self.ws_manager.disconnect()

            # Close shared API client
            if self.api_client:
                await self.api_client.close()
                self.api_client = None
        except Exception as e:
            self.logger.log(f"Error during Lighter disconnect: {e}", "ERROR")

    def get_exchange_name(self) -> str:
        """Get the exchange name."""
        return "lighter"
    
    def normalize_symbol(self, symbol: str) -> str:
        """
        Convert normalized symbol to Lighter's expected format.
        
        Lighter accepts base asset format (e.g., "BTC", "ETH", "ZORA").
        The market_id lookup handles both "BTC" and "BTC-PERP" formats.
        
        Args:
            symbol: Normalized symbol (e.g., "BTC", "ETH", "ZORA")
            
        Returns:
            Lighter-formatted symbol (base asset only, uppercase)
        """
        # Strip common quote currencies if present
        symbol_upper = symbol.upper()
        for suffix in ['USDT', 'USDC', '-PERP', 'PERP']:
            if symbol_upper.endswith(suffix):
                symbol_upper = symbol_upper[:-len(suffix)]
        
        # Clean up any trailing separators
        return symbol_upper.strip('-_/')

    def setup_order_update_handler(self, handler) -> None:
        """Setup order update handler for WebSocket."""
        self._order_update_handler = handler

    def _handle_websocket_order_update(self, order_data_list: List[Dict[str, Any]]):
        """Handle order updates from WebSocket."""
        for order_data in order_data_list:
            if order_data['market_index'] != self.config.contract_id:
                continue

            side = 'sell' if order_data['is_ask'] else 'buy'
            # Let strategy determine order type - exchange client just reports the order
            order_type = "ORDER"

            order_id = order_data['order_index']
            status = order_data['status'].upper()
            filled_size = Decimal(order_data['filled_base_amount'])
            size = Decimal(order_data['initial_base_amount'])
            price = Decimal(order_data['price'])
            remaining_size = Decimal(order_data['remaining_base_amount'])

            if order_id in self.orders_cache.keys():
                if (self.orders_cache[order_id]['status'] == 'OPEN' and
                        status == 'OPEN' and
                        filled_size == self.orders_cache[order_id]['filled_size']):
                    continue
                elif status in ['FILLED', 'CANCELED']:
                    del self.orders_cache[order_id]
                else:
                    self.orders_cache[order_id]['status'] = status
                    self.orders_cache[order_id]['filled_size'] = filled_size
            elif status == 'OPEN':
                self.orders_cache[order_id] = {'status': status, 'filled_size': filled_size}

            if status == 'OPEN' and filled_size > 0:
                status = 'PARTIALLY_FILLED'

            if status == 'OPEN':
                self.logger.log(f"[{order_type}] [{order_id}] {status} "
                                f"{size} @ {price}", "INFO")
            else:
                self.logger.log(f"[{order_type}] [{order_id}] {status} "
                                f"{filled_size} @ {price}", "INFO")

            if order_data['client_order_index'] == self.current_order_client_id or order_type == 'OPEN':
                current_order = OrderInfo(
                    order_id=order_id,
                    side=side,
                    size=size,
                    price=price,
                    status=status,
                    filled_size=filled_size,
                    remaining_size=remaining_size,
                    cancel_reason=''
                )
                self.current_order = current_order

            if status in ['FILLED', 'CANCELED']:
                self.logger.log_transaction(order_id, side, filled_size, price, status)

    @query_retry(default_return=(0, 0))
    async def fetch_bbo_prices(self, contract_id: str) -> Tuple[Decimal, Decimal]:
        """
        Get best bid/offer prices using REST API.
        
        Note: This method is kept for backward compatibility with legacy code
        that calls it directly. New code should use PriceProvider instead.
        
        For real-time monitoring, WebSocket data is available via ws_manager.
        For order execution, use PriceProvider which intelligently caches data.
        """
        try:
            order_book = await self.get_order_book_depth(contract_id, levels=1)
            
            if not order_book['bids'] or not order_book['asks']:
                raise ValueError(f"Empty order book for {contract_id}")
            
            best_bid = order_book['bids'][0]['price']
            best_ask = order_book['asks'][0]['price']
            
            return best_bid, best_ask
            
        except Exception as e:
            self.logger.log(f"❌ [LIGHTER] Failed to get BBO prices: {e}", "ERROR")
            raise ValueError(f"Unable to fetch BBO prices for {contract_id}: {e}")

    async def get_order_book_depth(
        self, 
        contract_id: str, 
        levels: int = 10
    ) -> Dict[str, List[Dict[str, Decimal]]]:
        """
        Get order book depth from Lighter REST API.
        
        Uses /api/v1/orderBookOrders endpoint for reliable order book data.
        This is more reliable than WebSocket for pre-trade liquidity checks.
        
        Args:
            contract_id: Contract/symbol identifier (can be symbol or market_id)
            levels: Number of price levels to fetch (default: 10, max: 100)
            
        Returns:
            Dictionary with 'bids' and 'asks' lists of dicts with 'price' and 'size'
        """
        try:
            # Use REST API for order book (more reliable than WebSocket for one-time queries)
            url = f"{self.base_url}/api/v1/orderBookOrders"
            
            # Lighter uses integer market_id for API calls
            # Try to convert contract_id to int first (if already an int ID)
            try:
                market_id = int(contract_id)
                self.logger.log(f"📊 [LIGHTER] Using contract_id as market_id: {market_id}", "INFO")
            except (ValueError, TypeError):
                # contract_id is a symbol string - normalize it first
                normalized_symbol = self.normalize_symbol(contract_id)
                market_id = await self._get_market_id_for_symbol(normalized_symbol)
                
                if market_id is None:
                    self.logger.log(
                        f"❌ [LIGHTER] Could not find market_id for symbol '{contract_id}' on Lighter. "
                        f"Symbol may not exist on this exchange.",
                        "ERROR"
                    )
                    return {'bids': [], 'asks': []}

            if levels < 100:
                # API max is 100 for lighter, while default is set to 20
                # so we use the highest of the 2
                levels = 100 #lighter specific

            params = {
                'market_id': market_id,
                'limit': levels  # API max is 100
            }
            
            self.logger.log(
                f"📊 [LIGHTER] Fetching order book: market_id={market_id}, limit={params['limit']}",
                "INFO"
            )
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params) as response:
                    if response.status != 200:
                        response_text = await response.text()
                        self.logger.log(
                            f"❌ [LIGHTER] Failed to get order book: HTTP {response.status}, "
                            f"URL: {url}, params: {params}, response: {response_text[:300]}",
                            "ERROR"
                        )
                        return {'bids': [], 'asks': []}
                    
                    data = await response.json()
                    
                    if data.get('code') != 200:
                        self.logger.log(
                            f"❌ [LIGHTER] Order book API error response: {data}",
                            "ERROR"
                        )
                        return {'bids': [], 'asks': []}
                    
                    # Extract bids and asks from Lighter response
                    bids_raw = data.get('bids', [])
                    asks_raw = data.get('asks', [])
                    
                    self.logger.log(
                        f"📊 [LIGHTER] Order book received: {len(bids_raw)} bids, {len(asks_raw)} asks for market_id={market_id}",
                        "INFO"
                    )
                    
                    # Convert to standardized format
                    # Lighter format: [{'price': '1243.5281', 'remaining_base_amount': '0.20'}, ...]
                    bids = [
                        {
                            'price': Decimal(bid['price']), 
                            'size': Decimal(bid['remaining_base_amount'])
                        } 
                        for bid in bids_raw
                    ]
                    asks = [
                        {
                            'price': Decimal(ask['price']), 
                            'size': Decimal(ask['remaining_base_amount'])
                        } 
                        for ask in asks_raw
                    ]
                    
                    self.logger.log(
                        f"✅ [LIGHTER] get_order_book_depth finished executing for {contract_id}",
                        "INFO"
                    )
                    
                    return {
                        'bids': bids,
                        'asks': asks
                    }

        except Exception as e:
            self.logger.log(f"❌ [LIGHTER] Error fetching order book depth for {contract_id}: {e}", "ERROR")
            import traceback
            self.logger.log(f"Traceback: {traceback.format_exc()}", "ERROR")
            # Return empty order book on error
            return {'bids': [], 'asks': []}

    async def _submit_order_with_retry(self, order_params: Dict[str, Any]) -> OrderResult:
        """Submit an order with Lighter using official SDK."""
        # Ensure client is initialized
        if self.lighter_client is None:
            # This is a sync method, so we need to handle this differently
            # For now, raise an error if client is not initialized
            raise ValueError("Lighter client not initialized. Call connect() first.")

        # Create order using official SDK
        create_order, tx_hash, error = await self.lighter_client.create_order(**order_params)
        if error is not None:
            return OrderResult(
                success=False, order_id=str(order_params['client_order_index']),
                error_message=f"Order creation error: {error}")

        else:
            return OrderResult(success=True, order_id=str(order_params['client_order_index']))

    async def place_limit_order(self, contract_id: str, quantity: Decimal, price: Decimal,
                                side: str) -> OrderResult:
        """Place a post only order with Lighter using official SDK."""
        # Ensure client is initialized
        if self.lighter_client is None:
            await self._initialize_lighter_client()

        # Determine order side and price
        if side.lower() == 'buy':
            is_ask = False
        elif side.lower() == 'sell':
            is_ask = True
        else:
            raise Exception(f"Invalid side: {side}")

        # Generate unique client order index
        client_order_index = int(time.time() * 1000) % 1000000  # Simple unique ID
        self.current_order_client_id = client_order_index

        # Create order parameters
        order_params = {
            'market_index': self.config.contract_id,
            'client_order_index': client_order_index,
            'base_amount': int(quantity * self.base_amount_multiplier),
            'price': int(price * self.price_multiplier),
            'is_ask': is_ask,
            'order_type': self.lighter_client.ORDER_TYPE_LIMIT,
            'time_in_force': self.lighter_client.ORDER_TIME_IN_FORCE_GOOD_TILL_TIME,
            'reduce_only': False,
            'trigger_price': 0,
        }

        order_result = await self._submit_order_with_retry(order_params)
        return order_result

    async def place_open_order(self, contract_id: str, quantity: Decimal, direction: str) -> OrderResult:
        """Place an open order with Lighter using official SDK."""

        self.current_order = None
        self.current_order_client_id = None
        order_price = await self.get_order_price(direction)

        order_price = self.round_to_tick(order_price)
        order_result = await self.place_limit_order(contract_id, quantity, order_price, direction)
        if not order_result.success:
            raise Exception(f"[OPEN] Error placing order: {order_result.error_message}")

        start_time = time.time()
        order_status = 'OPEN'

        # While waiting for order to be filled
        while time.time() - start_time < 10 and order_status != 'FILLED':
            await asyncio.sleep(0.1)
            if self.current_order is not None:
                order_status = self.current_order.status

        # Handle case where current_order might be None
        if self.current_order is not None:
            return OrderResult(
                success=True,
                order_id=self.current_order.order_id,
                side=direction,
                size=quantity,
                price=order_price,
                status=self.current_order.status
            )
        else:
            # Fallback when current_order is None
            return OrderResult(
                success=True,
                order_id=order_result.order_id if order_result else None,
                side=direction,
                size=quantity,
                price=order_price,
                status='OPEN'
            )

    async def _get_active_close_orders(self, contract_id: str) -> int:
        """Get active orders count for a contract using official SDK."""
        active_orders = await self.get_active_orders(contract_id)
        return len(active_orders)

    async def place_close_order(self, contract_id: str, quantity: Decimal, price: Decimal, side: str) -> OrderResult:
        """Place a close order with Lighter using official SDK."""
        self.current_order = None
        self.current_order_client_id = None
        order_result = await self.place_limit_order(contract_id, quantity, price, side)

        # wait for 5 seconds to ensure order is placed
        await asyncio.sleep(5)
        if order_result.success:
            return OrderResult(
                success=True,
                order_id=order_result.order_id,
                side=side,
                size=quantity,
                price=price,
                status='OPEN'
            )
        else:
            raise Exception(f"[CLOSE] Error placing order: {order_result.error_message}")
    
    async def get_order_price(self, side: str = '') -> Decimal:
        """Get the price of an order with Lighter using official SDK."""
        # Get current market prices
        best_bid, best_ask = await self.fetch_bbo_prices(self.config.contract_id)
        if best_bid <= 0 or best_ask <= 0 or best_bid >= best_ask:
            self.logger.log("Invalid bid/ask prices", "ERROR")
            raise ValueError("Invalid bid/ask prices")

        order_price = (best_bid + best_ask) / 2

        # Simple mid-price calculation - let strategy handle order placement logic
        # (removed strategy-specific close order logic)

        return order_price

    async def cancel_order(self, order_id: str) -> OrderResult:
        """Cancel an order with Lighter."""
        # Ensure client is initialized
        if self.lighter_client is None:
            await self._initialize_lighter_client()

        # Cancel order using official SDK
        cancel_order, tx_hash, error = await self.lighter_client.cancel_order(
            market_index=self.config.contract_id,
            order_index=int(order_id)  # Assuming order_id is the order index
        )

        if error is not None:
            return OrderResult(success=False, error_message=f"Cancel order error: {error}")

        if tx_hash:
            return OrderResult(success=True)
        else:
            return OrderResult(success=False, error_message='Failed to send cancellation transaction')

    async def get_order_info(self, order_id: str) -> Optional[OrderInfo]:
        """
        Get order information from Lighter using official SDK.
        
        Note: Lighter uses order_index (int) as order_id, and we need market_id to query orders.
        """
        try:
            if not self.order_api:
                self.logger.log("Order API not initialized", "ERROR")
                return None
            
            # Get market ID from config (should be set during initialization)
            market_id = getattr(self.config, 'contract_id', None)
            if market_id is None:
                self.logger.log(f"Market ID not found in config for symbol {self.config.ticker}", "ERROR")
                return None
            
            # Generate auth token
            auth_token, error = self.lighter_client.create_auth_token_with_expiry()
            if error:
                self.logger.log(f"Error creating auth token: {error}", "ERROR")
                return None
            
            # Query active orders for this market
            try:
                orders_response = await self.order_api.account_active_orders(
                    account_index=self.account_index,
                    market_id=int(market_id),
                    auth=auth_token,
                    _request_timeout=10
                )
            except Exception as e:
                # Order might not be active anymore (filled or cancelled)
                self.logger.log(f"Order {order_id} not found in active orders (might be filled): {e}", "DEBUG")
                
                # Check if order was filled by looking at positions
                account_data = await self.account_api.account(by="index", value=str(self.account_index))
                if account_data and account_data.accounts and account_data.accounts[0].positions:
                    for position in account_data.accounts[0].positions:
                        if position.symbol == self.config.ticker:
                            position_amt = abs(float(position.position))
                            if position_amt > 0.001:  # Only include significant positions
                                return OrderInfo(
                                    order_id=order_id,
                                    side="buy" if float(position.position) > 0 else "sell",
                                    size=Decimal(str(position_amt)),
                                    price=Decimal(str(position.avg_price)),
                                    status="FILLED",
                                    filled_size=Decimal(str(position_amt)),
                                    remaining_size=Decimal('0')
                                )
                return None
            
            # Look for the specific order by order_index
            if orders_response and orders_response.orders:
                order_id_int = int(order_id)
                for order in orders_response.orders:
                    if order.order_index == order_id_int:
                        # Found the order!
                        size = Decimal(str(order.size_base))
                        filled = Decimal(str(order.matched_base))
                        remaining = size - filled
                        
                        # Determine status
                        if filled >= size:
                            status = "FILLED"
                        elif filled > 0:
                            status = "PARTIALLY_FILLED"
                        else:
                            status = "OPEN"
                        
                        return OrderInfo(
                            order_id=order_id,
                            side="buy" if order.is_bid else "sell",
                            size=size,
                            price=Decimal(str(order.price)),
                            status=status,
                            filled_size=filled,
                            remaining_size=remaining
                        )
            
            # Order not found in active orders - might be filled
            return None

        except Exception as e:
            self.logger.log(f"Error getting order info: {e}", "ERROR")
            import traceback
            self.logger.log(f"Traceback: {traceback.format_exc()}", "DEBUG")
            return None

    @query_retry(reraise=True)
    async def _fetch_orders_with_retry(self) -> List[Dict[str, Any]]:
        """Get orders using official SDK."""
        # Ensure client is initialized
        if self.lighter_client is None:
            await self._initialize_lighter_client()

        # Generate auth token for API call
        auth_token, error = self.lighter_client.create_auth_token_with_expiry()
        if error is not None:
            self.logger.log(f"Error creating auth token: {error}", "ERROR")
            raise ValueError(f"Error creating auth token: {error}")

        # Use OrderApi to get active orders
        order_api = lighter.OrderApi(self.api_client)

        # Get active orders for the specific market
        orders_response = await order_api.account_active_orders(
            account_index=self.account_index,
            market_id=self.config.contract_id,
            auth=auth_token
        )

        if not orders_response:
            self.logger.log("Failed to get orders", "ERROR")
            raise ValueError("Failed to get orders")

        return orders_response.orders

    async def get_active_orders(self, contract_id: str) -> List[OrderInfo]:
        """Get active orders for a contract using official SDK."""
        order_list = await self._fetch_orders_with_retry()

        # Filter orders for the specific market
        contract_orders = []
        for order in order_list:
            # Convert Lighter Order to OrderInfo
            side = "sell" if order.is_ask else "buy"
            size = Decimal(order.initial_base_amount)
            price = Decimal(order.price)

            # Only include orders with remaining size > 0
            if size > 0:
                contract_orders.append(OrderInfo(
                    order_id=str(order.order_index),
                    side=side,
                    size=Decimal(order.remaining_base_amount),  # FIXME: This is wrong. Should be size
                    price=price,
                    status=order.status.upper(),
                    filled_size=Decimal(order.filled_base_amount),
                    remaining_size=Decimal(order.remaining_base_amount)
                ))

        return contract_orders

    @query_retry(reraise=True)
    async def _fetch_positions_with_retry(self) -> List[Dict[str, Any]]:
        """Get positions using official SDK."""
        # Use shared API client
        account_api = lighter.AccountApi(self.api_client)

        # Get account info
        account_data = await account_api.account(by="index", value=str(self.account_index))

        if not account_data or not account_data.accounts:
            self.logger.log("Failed to get positions", "ERROR")
            raise ValueError("Failed to get positions")

        return account_data.accounts[0].positions

    async def get_account_positions(self) -> Decimal:
        """Get account positions using official SDK."""
        # Get account info which includes positions
        positions = await self._fetch_positions_with_retry()

        # Find position for current market
        for position in positions:
            if position.market_id == self.config.contract_id:
                return Decimal(position.position)

        return Decimal(0)

    async def get_contract_attributes(self) -> Tuple[str, Decimal]:
        """Get contract ID for a ticker."""
        ticker = self.config.ticker
        if len(ticker) == 0:
            self.logger.log("Ticker is empty", "ERROR")
            raise ValueError("Ticker is empty")

        order_api = lighter.OrderApi(self.api_client)
        # Get all order books to find the market for our ticker
        order_books = await order_api.order_books()

        # Find the market that matches our ticker
        market_info = None
        available_symbols = []
        
        for market in order_books.order_books:
            available_symbols.append(market.symbol)
            # Try exact match first
            if market.symbol == ticker:
                market_info = market
                break
            # Try case-insensitive match
            elif market.symbol.upper() == ticker.upper():
                market_info = market
                break
            # Try common variations (APEX-USD, APEX-USDC, etc.)
            elif market.symbol.upper().startswith(ticker.upper() + '-'):
                market_info = market
                break
            elif market.symbol.upper().startswith(ticker.upper() + 'USD'):
                market_info = market
                break

        if market_info is None:
            self.logger.log(f"Ticker '{ticker}' not found in available markets", "ERROR")
            self.logger.log(f"Available symbols: {', '.join(available_symbols[:10])}{'...' if len(available_symbols) > 10 else ''}", "ERROR")
            raise ValueError(f"Ticker '{ticker}' not found in available markets. Available: {', '.join(available_symbols[:5])}")

        market_summary = await order_api.order_book_details(market_id=market_info.market_id)
        order_book_details = market_summary.order_book_details[0]
        # Set contract_id to market name (Lighter uses market IDs as identifiers)
        self.config.contract_id = market_info.market_id
        self.base_amount_multiplier = pow(10, market_info.supported_size_decimals)
        self.price_multiplier = pow(10, market_info.supported_price_decimals)

        try:
            self.config.tick_size = Decimal("1") / (Decimal("10") ** order_book_details.price_decimals)
        except Exception:
            self.logger.log("Failed to get tick size", "ERROR")
            raise ValueError("Failed to get tick size")

        return self.config.contract_id, self.config.tick_size

    # Risk Management Methods (Lighter-specific implementations)
    def supports_risk_management(self) -> bool:
        """Lighter supports advanced risk management."""
        return True

    async def get_account_balance(self) -> Optional[Decimal]:
        """Get current account balance using Lighter SDK."""
        try:
            if not self.account_api:
                return None
                
            account_data = await self.account_api.account(by="index", value=str(self.account_index))
            if account_data and account_data.accounts:
                return Decimal(account_data.accounts[0].available_balance or "0")
            return None
        except Exception as e:
            self.logger.log(f"Error getting account balance: {e}", "ERROR")
            return None

    async def get_detailed_positions(self) -> List[Dict[str, Any]]:
        """Get detailed position info using Lighter SDK."""
        try:
            if not self.account_api:
                return []
                
            account_data = await self.account_api.account(by="index", value=str(self.account_index))
            if account_data and account_data.accounts:
                positions = []
                for pos in account_data.accounts[0].positions:
                    positions.append({
                        'market_id': pos.market_id,
                        'symbol': pos.symbol,
                        'position': Decimal(pos.position),
                        'avg_entry_price': Decimal(pos.avg_entry_price),
                        'position_value': Decimal(pos.position_value),
                        'unrealized_pnl': Decimal(pos.unrealized_pnl),
                        'realized_pnl': Decimal(pos.realized_pnl),
                        'liquidation_price': Decimal(pos.liquidation_price),
                        'allocated_margin': Decimal(pos.allocated_margin),
                        'sign': pos.sign  # 1 for Long, -1 for Short
                    })
                return positions
            return []
        except Exception as e:
            self.logger.log(f"Error getting detailed positions: {e}", "ERROR")
            return []

    async def get_account_pnl(self) -> Optional[Decimal]:
        """Get account P&L using Lighter SDK."""
        try:
            positions = await self.get_detailed_positions()
            total_pnl = Decimal('0')
            for pos in positions:
                total_pnl += pos['unrealized_pnl']
            return total_pnl
        except Exception as e:
            self.logger.log(f"Error getting account P&L: {e}", "ERROR")
            return None

    async def get_total_asset_value(self) -> Optional[Decimal]:
        """Get total account asset value using Lighter SDK."""
        try:
            if not self.account_api:
                return None
                
            account_data = await self.account_api.account(by="index", value=str(self.account_index))
            if account_data and account_data.accounts:
                return Decimal(account_data.accounts[0].total_asset_value or "0")
            return None
        except Exception as e:
            self.logger.log(f"Error getting total asset value: {e}", "ERROR")
            return None

    async def place_market_order(self, contract_id: str, quantity: Decimal, side: str) -> OrderResult:
        """Place a market order with Lighter using official SDK."""
        try:
            # Ensure client is initialized
            if self.lighter_client is None:
                raise ValueError("Lighter client not initialized. Call connect() first.")

            # Determine order side
            if side.lower() == 'buy':
                is_ask = False
            elif side.lower() == 'sell':
                is_ask = True
            else:
                raise Exception(f"Invalid side: {side}")

            # Generate unique client order index
            client_order_index = int(time.time() * 1000) % 1000000

            # Create market order parameters
            order_params = {
                'market_index': int(contract_id),
                'client_order_index': client_order_index,
                'base_amount': int(quantity * self.base_amount_multiplier),
                'price': 0,  # Market order
                'is_ask': is_ask,
                'order_type': self.lighter_client.ORDER_TYPE_MARKET,
                'time_in_force': self.lighter_client.TIME_IN_FORCE_IOC,  # Immediate or Cancel
                'reduce_only': True  # For closing positions
            }

            # Submit order
            create_order, tx_hash, error = await self.lighter_client.create_order(**order_params)
            
            if error is not None:
                return OrderResult(
                    success=False,
                    order_id=str(client_order_index),
                    error_message=f"Market order error: {error}"
                )
            else:
                return OrderResult(
                    success=True,
                    order_id=str(client_order_index),
                    side=side,
                    size=quantity,
                    status='SUBMITTED'
                )

        except Exception as e:
            self.logger.log(f"Error placing market order: {e}", "ERROR")
            return OrderResult(
                success=False,
                error_message=f"Market order exception: {e}"
            )



