"""
Simplified Paradex exchange client implementation - L2 credentials only.
"""

import os
import asyncio
import time
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Any, List, Optional, Tuple
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type

from exchange_clients.base_client import BaseExchangeClient
from exchange_clients.base_models import OrderResult, OrderInfo
from helpers.unified_logger import get_exchange_logger


def patch_paradex_http_client():
    """Patch Paradex SDK HttpClient to suppress unwanted print statements."""
    try:
        from paradex_py.api.http_client import HttpClient

        def patched_request(self, url, http_method, params=None, payload=None, headers=None):
            res = self.client.request(
                method=http_method.value,
                url=url,
                params=params,
                json=payload,
                headers=headers,
            )
            if res.status_code >= 300:
                from paradex_py.api.models import ApiErrorSchema
                error = ApiErrorSchema().loads(res.text)
                raise Exception(error)
            try:
                return res.json()
            except ValueError:
                # Suppress the "No response request" print statement
                # This is expected for DELETE requests that don't return JSON
                # The original code would print: f"HttpClient: No response request({url}, {http_method.value})"
                pass

        # Replace the request method
        HttpClient.request = patched_request

    except ImportError:
        # Paradex SDK not available, skip patching
        pass


class ParadexClient(BaseExchangeClient):
    """Simplified Paradex exchange client - L2 credentials only."""

    def __init__(
        self,
        config: Dict[str, Any],
        l1_address: Optional[str] = None,
        l2_private_key_hex: Optional[str] = None,
        l2_address: Optional[str] = None,
        environment: Optional[str] = None,
    ):
        """
        Initialize Paradex client with L2 credentials.
        
        Args:
            config: Trading configuration dictionary
            l1_address: Optional Ethereum L1 address (falls back to env var)
            l2_private_key_hex: Optional L2 private key in hex format (falls back to env var)
            l2_address: Optional L2 address (falls back to env var)
            environment: Optional environment name (falls back to env var, default 'prod')
        """
        # Import paradex_py modules only when this class is instantiated
        from paradex_py import Paradex
        from paradex_py.environment import Environment, TESTNET, PROD
        from paradex_py.common.order import Order, OrderType, OrderSide, OrderStatus
        from paradex_py.api.ws_client import ParadexWebsocketChannel
        # from paradex_py.common.console_logging import console_logger  # Disabled to turn off native logging

        # Apply the patch when this class is instantiated
        patch_paradex_http_client()

        # Set config first
        self.config = config

        # Paradex credentials: use provided params or fall back to environment
        self.l1_address = l1_address or os.getenv('PARADEX_L1_ADDRESS')
        self.l2_private_key_hex = l2_private_key_hex or os.getenv('PARADEX_L2_PRIVATE_KEY')
        self.l2_address = l2_address or os.getenv('PARADEX_L2_ADDRESS')
        self.environment = environment or os.getenv('PARADEX_ENVIRONMENT', 'prod')

        # Validate that required credentials are provided
        if not self.l1_address:
            raise ValueError(
                "PARADEX_L1_ADDRESS must be set in environment variables.\n"
                "This is your Ethereum L1 address."
            )

        if not self.l2_private_key_hex:
            raise ValueError(
                "PARADEX_L2_PRIVATE_KEY must be set in environment variables.\n"
                "Run 'python get_paradex_api_key.py' to generate L2 credentials from L1 credentials."
            )

        # Convert L2 private key from hex to int
        try:
            from starknet_py.common import int_from_hex
            self.l2_private_key = int_from_hex(self.l2_private_key_hex)
        except Exception as e:
            raise ValueError(f"Invalid L2 private key format: {e}")

        # Convert environment string to proper enum
        env_map = {
            'prod': PROD,
            'testnet': TESTNET,
            'nightly': TESTNET  # Use testnet for nightly
        }
        self.env = env_map.get(self.environment.lower(), TESTNET)

        # Initialize logger
        self.logger = get_exchange_logger("paradex", self.config.ticker)

        # Initialize Paradex client with L2 credentials only
        self._initialize_paradex_client()

        self._order_update_handler = None
        self.order_size_increment = ''

    def _initialize_paradex_client(self) -> None:
        """Initialize the Paradex client with L2 credentials only."""
        try:
            # Import paradex_py modules locally
            from paradex_py import Paradex

            # Initialize Paradex client without credentials first
            self.paradex = Paradex(
                env=self.env,
                logger=None  # Disabled native logging
            )

            # Initialize account with L2 private key
            self.paradex.init_account(
                l1_address=self.l1_address,
                l2_private_key=self.l2_private_key
            )

            # Log the L2 address being used
            if self.l2_address:
                self.logger.info(f"Using L2 address: {self.l2_address}")

        except Exception as e:
            raise ValueError(f"Failed to initialize Paradex client: {e}")

    def _validate_config(self) -> None:
        """Validate Paradex configuration."""
        if not self.l2_private_key_hex:
            raise ValueError("L2 private key is required for trading operations")

    async def connect(self) -> None:
        """Connect to Paradex WebSocket."""
        is_connected = False
        while not is_connected:
            is_connected = await self.paradex.ws_client.connect()
            if not is_connected:
                self.logger.log("Connection failed, retrying in 1 second...", "WARN")
                await asyncio.sleep(1)
        # Wait a moment for connection to establish
        await asyncio.sleep(2)
        self._ws_connected = True

        # Setup WebSocket subscription for order updates if handler is set
        await self._setup_websocket_subscription()

    async def disconnect(self) -> None:
        """Disconnect from Paradex."""
        try:
            if hasattr(self, 'paradex') and self.paradex:
                await self.paradex.ws_client._close_connection()
                self._ws_connected = False
        except Exception as e:
            self.logger.error(f"Error during Paradex disconnect: {e}")

    def get_exchange_name(self) -> str:
        """Get the exchange name."""
        return "paradex"

    async def _setup_websocket_subscription(self) -> None:
        """Setup WebSocket subscription for order updates."""
        if not hasattr(self, '_ws_order_update_handler'):
            return

        # Ensure WebSocket is connected
        if not hasattr(self, '_ws_connected') or not self._ws_connected:
            is_connected = False
            while not is_connected:
                is_connected = await self.paradex.ws_client.connect()
                if not is_connected:
                    self.logger.log("WebSocket connection failed, retrying in 1 second...", "WARN")
                    await asyncio.sleep(1)
            self._ws_connected = True
            self.logger.info("WebSocket connected for order monitoring")

        # Subscribe to orders channel for the specific market
        from paradex_py.api.ws_client import ParadexWebsocketChannel

        contract_id = self.config.contract_id
        try:
            await self.paradex.ws_client.subscribe(
                ParadexWebsocketChannel.ORDERS,
                callback=self._ws_order_update_handler,
                params={"market": contract_id}
            )
            self.logger.info(f"Subscribed to order updates for {contract_id}")
        except Exception as e:
            self.logger.error(f"Failed to subscribe to order updates: {e}")

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_fixed(3),
        retry=retry_if_exception_type(Exception),
        reraise=True
    )
    async def fetch_bbo_prices(self, contract_id: str) -> Dict[str, Any]:
        """Get orderbook using official SDK."""
        orderbook_data = self.paradex.api_client.fetch_orderbook(contract_id, {"depth": 1})
        if not orderbook_data:
            self.logger.error("Failed to get orderbook")
            raise ValueError("Failed to get orderbook")

        bids = orderbook_data.get('bids', [])
        asks = orderbook_data.get('asks', [])
        if not bids or not asks:
            self.logger.error("Failed to get bid/ask data")
            raise ValueError("Failed to get bid/ask data")

        # Get best bid and ask prices
        best_bid = Decimal(bids[0][0])
        best_ask = Decimal(asks[0][0])

        if best_bid <= 0 or best_ask <= 0:
            self.logger.error("Invalid bid/ask prices")
            raise ValueError("Invalid bid/ask prices")

        return best_bid, best_ask

    async def get_order_price(self, direction: str) -> Decimal:
        """Get the price of an order with Paradex using official SDK."""
        # Get current market prices
        best_bid, best_ask = await self.fetch_bbo_prices(self.config.contract_id)

        # Determine order side and price
        from paradex_py.common.order import OrderSide

        if direction == 'buy':
            # For buy orders, place slightly below best ask to ensure execution
            order_price = best_ask - self.config.tick_size
            order_side = OrderSide.Buy
        elif direction == 'sell':
            # For sell orders, place slightly above best bid to ensure execution
            order_price = best_bid + self.config.tick_size
            order_side = OrderSide.Sell
        else:
            raise Exception(f"[OPEN] Invalid direction: {direction}")

        order_price = self.round_to_tick(order_price)
        return order_price


    @retry(
        stop=stop_after_attempt(5),
        wait=wait_fixed(3),
        retry=retry_if_exception_type(Exception),
        reraise=True
    )
    def _submit_order_with_retry(self, order) -> OrderResult:
        """Submit an order with Paradex using official SDK."""
        # Submit order using official SDK
        order_result = self.paradex.api_client.submit_order(order)

        # Extract order ID from response
        order_id = order_result.get('id')
        if not order_id:
            return OrderResult(success=False, error_message='No order ID in response')
        return order_result

    async def place_post_only_order(
        self,
        contract_id: str,
        quantity: Decimal,
        price: Decimal,
        side: str,
        reduce_only: bool = False
    ) -> OrderResult:
        """Place a post only order with Paradex using official SDK."""
        from paradex_py.common.order import Order, OrderType, OrderSide, OrderStatus

        # Create order using Paradex SDK
        order = Order(
            market=contract_id,
            order_type=OrderType.Limit,
            order_side=side,
            size=quantity.quantize(self.order_size_increment, rounding=ROUND_HALF_UP),
            limit_price=price,
            instruction="POST_ONLY"
        )

        order_result = self._submit_order_with_retry(order)

        order_id = order_result.get('id')
        order_status = order_result.get('status')
        order_status_start_time = time.time()
        order_info = await self.get_order_info(order_id)
        if order_info is not None:
            order_status = order_info.status
        while order_status in ['NEW'] and time.time() - order_status_start_time < 10:
            # Check order status after a short delay
            await asyncio.sleep(0.01)
            order_info = await self.get_order_info(order_id)
            if order_info is not None:
                order_status = order_info.status

        if order_status == 'NEW':
            raise Exception('Paradex Server Error: Order not processed after 10 seconds')
        else:
            return order_info

    async def place_limit_order(
        self,
        contract_id: str,
        quantity: Decimal,
        price: Decimal,
        side: str,
        reduce_only: bool = False
    ) -> OrderResult:
        """
        Alias for place_post_only_order to match base interface.
        
        Paradex only supports post-only orders for limit orders.
        """
        return await self.place_post_only_order(contract_id, quantity, price, side, reduce_only)

    async def cancel_order(self, order_id: str) -> OrderResult:
        """Cancel an order with Paradex using official SDK."""
        try:
            # Cancel the order using official SDK
            self.paradex.api_client.cancel_order(order_id)
            return OrderResult(success=True)

        except Exception as e:
            return OrderResult(success=False, error_message=str(e))

    async def get_order_info(self, order_id: str, *, force_refresh: bool = False) -> Optional[OrderInfo]:
        """Get order information from Paradex using official SDK."""
        try:
            # Get order by ID using official SDK
            order_data = self.paradex.api_client.fetch_order(order_id)
            size = Decimal(order_data.get('size', 0)).quantize(self.order_size_increment, rounding=ROUND_HALF_UP)
            remaining_size = Decimal(order_data.get('remaining_size', 0))
            return OrderInfo(
                order_id=order_data.get('id', ''),
                side=order_data.get('side', '').lower(),
                size=size,
                price=Decimal(order_data.get('price', 0)),
                status=order_data.get('status', ''),
                filled_size=size - remaining_size,
                remaining_size=remaining_size,
                cancel_reason=order_data.get('cancel_reason', '')
            )

        except Exception as e:
            self.logger.error(f"Error getting order info: {e}")
            return None

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_fixed(3),
        retry=retry_if_exception_type(Exception),
        reraise=True
    )
    async def _fetch_orders_with_retry(self, contract_id: str) -> List[Dict[str, Any]]:
        """Get orders using official SDK."""
        orders_response = self.paradex.api_client.fetch_orders({"market": contract_id, "status": "OPEN"})
        if not orders_response or 'results' not in orders_response:
            self.logger.error("Failed to get orders")
            raise ValueError("Failed to get orders")

        return orders_response['results']

    async def get_active_orders(self, contract_id: str) -> List[OrderInfo]:
        """Get active orders for a contract using official SDK."""
        order_list = await self._fetch_orders_with_retry(contract_id)

        # Filter orders for the specific market
        contract_orders = []
        for order in order_list:
            contract_orders.append(OrderInfo(
                order_id=order.get('id', ''),
                side=order.get('side', '').lower(),
                size=Decimal(order.get('remaining_size', 0)),  # FIXME: This is wrong. Should be size
                price=Decimal(order.get('price', 0)),
                status=order.get('status', ''),
                filled_size=Decimal(order.get('size', 0)) - Decimal(order.get('remaining_size', 0)),
                remaining_size=Decimal(order.get('remaining_size', 0))
            ))

        return contract_orders

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_fixed(3),
        retry=retry_if_exception_type(Exception),
        reraise=True
    )
    async def _fetch_positions_with_retry(self) -> List[Dict[str, Any]]:
        """Get positions using official SDK."""
        positions_response = self.paradex.api_client.fetch_positions()
        if not positions_response or 'results' not in positions_response:
            self.logger.error("Failed to get positions")
            raise ValueError("Failed to get positions")

        return positions_response['results']

    async def get_account_positions(self) -> Decimal:
        """Get account positions using official SDK."""
        # Get account info which includes positions
        positions = await self._fetch_positions_with_retry()

        # Find position for current market
        for position in positions:
            if isinstance(position, dict) and position.get('market') == self.config.contract_id and position.get('status') == 'OPEN':
                if position.get('side') == 'LONG' and self.config.direction == 'sell':
                    raise ValueError("Long position found for sell direction")
                elif position.get('side') == 'SHORT' and self.config.direction == 'buy':
                    raise ValueError("Short position found for buy direction")

                return abs(Decimal(position.get('size', 0)).quantize(self.order_size_increment, rounding=ROUND_HALF_UP))

        return Decimal(0)
    
    async def get_account_balance(self) -> Optional[Decimal]:
        """
        Get available account balance from Paradex.
        
        TODO: Implement when Paradex trading is in production.
        Need to query Paradex API for available balance.
        
        Returns:
            None (not yet implemented)
        """
        self.logger.log("[PARADEX] get_account_balance not yet implemented", "DEBUG")
        return None
    
    async def get_leverage_info(self, symbol: str) -> Dict[str, Any]:
        """
        Get leverage information for Paradex.
        
        TODO: Implement actual API query when Paradex trading is in production.
        Currently returns conservative defaults.
        """
        self.logger.log(
            f"[PARADEX] get_leverage_info not yet implemented, using defaults for {symbol}",
            "DEBUG"
        )
        return {
            'max_leverage': Decimal('10'),
            'max_notional': None,
            'margin_requirement': Decimal('0.10'),  # 10% margin = 10x leverage
            'brackets': None,
            'error': None  # Using default values (not queried from API)
        }

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_fixed(3),
        retry=retry_if_exception_type(Exception),
        reraise=True
    )
    async def _fetch_market_with_retry(self, symbol: str) -> Dict[str, Any]:
        """Get market using official SDK."""
        market_response = self.paradex.api_client.fetch_markets({"market": symbol})
        if not market_response or 'results' not in market_response:
            self.logger.error("Failed to get markets")
            raise ValueError("Failed to get markets")

        if not market_response['results']:
            self.logger.error("Failed to get markets list")
            raise ValueError("Failed to get markets list")

        market = market_response['results'][0]

        return market

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_fixed(3),
        retry=retry_if_exception_type(Exception),
        reraise=True
    )
    async def _fetch_markets_summary_with_retry(self, symbol: str) -> Dict[str, Any]:
        """Get markets summary using official SDK."""
        market_summary_response = self.paradex.api_client.fetch_markets_summary({"market": symbol})
        if not market_summary_response or 'results' not in market_summary_response:
            self.logger.error("Failed to get markets summary")
            raise ValueError("Failed to get markets summary")
        market_summary = market_summary_response['results'][0]
        return market_summary

    async def get_contract_attributes(self) -> Tuple[str, Decimal]:
        """Get contract ID for a ticker."""
        ticker = self.config.ticker
        if len(ticker) == 0:
            self.logger.error("Ticker is empty")
            raise ValueError("Ticker is empty")

        symbol = f"{ticker}-USD-PERP"

        market = await self._fetch_market_with_retry(symbol)
        market_summary = await self._fetch_markets_summary_with_retry(symbol)

        last_price = Decimal(market_summary.get('mark_price', 0))

        # Set contract_id to market name (Paradex uses market names as identifiers)
        self.config.contract_id = symbol
        
        # Cache contract_id for this symbol (multi-symbol trading support)
        self._contract_id_cache[ticker.upper()] = symbol
        
        try:
            min_notional = Decimal(market.get('min_notional'))
        except Exception:
            self.logger.error("Failed to get min notional")
            raise ValueError("Failed to get min notional")

        try:
            self.order_size_increment = Decimal(market.get('order_size_increment'))
        except Exception:
            self.logger.error("Failed to get min quantity")
            raise ValueError("Failed to get min quantity")

        order_notional = last_price * self.config.quantity
        if order_notional < min_notional:
            self.logger.error(f"Order notional is less than min notional: {order_notional} < {min_notional}")
            raise ValueError(f"Order notional is less than min notional: {order_notional} < {min_notional}")

        try:
            self.config.tick_size = Decimal(market.get('price_tick_size'))
        except Exception:
            self.logger.error("Failed to get tick size")
            raise ValueError("Failed to get tick size")

        return self.config.contract_id, self.config.tick_size
