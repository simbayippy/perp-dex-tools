"""
GRVT exchange client implementation for trading execution.
"""

import os
import asyncio
import time
from decimal import Decimal
from typing import Dict, Any, List, Optional, Tuple
from pysdk.grvt_ccxt import GrvtCcxt
from pysdk.grvt_ccxt_ws import GrvtCcxtWS
from pysdk.grvt_ccxt_env import GrvtEnv, GrvtWSEndpointType

from exchange_clients.base_client import BaseExchangeClient
from exchange_clients.base_models import (
    OrderResult,
    OrderInfo,
    query_retry,
    MissingCredentialsError,
    ExchangePositionSnapshot,
    validate_credentials,
)
from helpers.unified_logger import get_exchange_logger


class GrvtClient(BaseExchangeClient):
    """GRVT exchange client implementation."""

    def __init__(self, config: Dict[str, Any]):
        """Initialize GRVT client."""
        super().__init__(config)

        # GRVT credentials from environment (validation happens in _validate_config)
        self.trading_account_id = os.getenv('GRVT_TRADING_ACCOUNT_ID')
        self.private_key = os.getenv('GRVT_PRIVATE_KEY')
        self.api_key = os.getenv('GRVT_API_KEY')
        self.environment = os.getenv('GRVT_ENVIRONMENT', 'prod')

        # Convert environment string to proper enum
        env_map = {
            'prod': GrvtEnv.PROD,
            'testnet': GrvtEnv.TESTNET,
            'staging': GrvtEnv.STAGING,
            'dev': GrvtEnv.DEV
        }
        self.env = env_map.get(self.environment.lower(), GrvtEnv.PROD)

        # Initialize logger
        self.logger = get_exchange_logger("grvt", self.config.ticker)

        # Initialize GRVT clients
        self._initialize_grvt_clients()

        self._order_update_handler = None
        self._ws_client = None
        self._order_update_callback = None

    def _initialize_grvt_clients(self) -> None:
        """Initialize the GRVT REST and WebSocket clients."""
        try:
            # Parameters for GRVT SDK
            parameters = {
                'trading_account_id': self.trading_account_id,
                'private_key': self.private_key,
                'api_key': self.api_key
            }

            # Initialize REST client
            self.rest_client = GrvtCcxt(
                env=self.env,
                parameters=parameters
            )

        except Exception as e:
            # If SDK fails to initialize due to invalid credentials, raise as credential error
            if any(keyword in str(e).lower() for keyword in ['invalid', 'credential', 'auth', 'key']):
                raise MissingCredentialsError(f"Invalid GRVT credentials format: {e}")
            raise ValueError(f"Failed to initialize GRVT client: {e}")

    def _validate_config(self) -> None:
        """Validate GRVT configuration."""
        # Use base validation helper (reduces code duplication)
        validate_credentials('GRVT_TRADING_ACCOUNT_ID', os.getenv('GRVT_TRADING_ACCOUNT_ID'))
        validate_credentials('GRVT_PRIVATE_KEY', os.getenv('GRVT_PRIVATE_KEY'))
        validate_credentials('GRVT_API_KEY', os.getenv('GRVT_API_KEY'))

    async def connect(self) -> None:
        """Connect to GRVT WebSocket."""
        try:
            # Initialize WebSocket client - match the working test implementation
            loop = asyncio.get_running_loop()

            # Import logger from pysdk like in the test file
            from pysdk.grvt_ccxt_logging_selector import logger

            # Parameters for GRVT SDK - match test file structure
            parameters = {
                'api_key': self.api_key,
                'trading_account_id': self.trading_account_id,
                'api_ws_version': 'v1',
                'private_key': self.private_key
            }

            self._ws_client = GrvtCcxtWS(
                env=self.env,
                loop=loop,
                logger=logger,  # Add logger parameter like in test file
                parameters=parameters
            )

            # Initialize and connect
            await self._ws_client.initialize()
            await asyncio.sleep(2)  # Wait for connection to establish

            # If an order update callback was set before connect, subscribe now
            if self._order_update_callback is not None:
                asyncio.create_task(self._subscribe_to_orders(self._order_update_callback))
                self.logger.info(f"Deferred subscription started for {self.config.contract_id}")

        except Exception as e:
            self.logger.error(f"Error connecting to GRVT WebSocket: {e}")
            raise

    async def disconnect(self) -> None:
        """Disconnect from GRVT."""
        try:
            if self._ws_client:
                await self._ws_client.__aexit__()
        except Exception as e:
            self.logger.error(f"Error during GRVT disconnect: {e}")

    def get_exchange_name(self) -> str:
        """Get the exchange name."""
        return "grvt"

    async def _subscribe_to_orders(self, callback):
        """Subscribe to order updates asynchronously."""
        try:
            await self._ws_client.subscribe(
                stream="order",
                callback=callback,
                ws_end_point_type=GrvtWSEndpointType.TRADE_DATA_RPC_FULL,
                params={"instrument": self.config.contract_id}
            )
            await asyncio.sleep(0)  # Small delay like in test file
            self.logger.info(f"Successfully subscribed to order updates for {self.config.contract_id}")
        except Exception as e:
            self.logger.error(f"Error in subscription task: {e}")

    @query_retry(reraise=True)
    async def fetch_bbo_prices(self, contract_id: str) -> Tuple[Decimal, Decimal]:
        """Fetch best bid and offer prices for a contract."""
        # Get order book from GRVT
        order_book = self.rest_client.fetch_order_book(contract_id, limit=10)

        if not order_book or 'bids' not in order_book or 'asks' not in order_book:
            raise ValueError(f"Unable to get order book: {order_book}")

        bids = order_book.get('bids', [])
        asks = order_book.get('asks', [])

        best_bid = Decimal(bids[0]['price']) if bids and len(bids) > 0 else Decimal(0)
        best_ask = Decimal(asks[0]['price']) if asks and len(asks) > 0 else Decimal(0)

        return best_bid, best_ask

    async def get_order_book_depth(
        self, 
        contract_id: str, 
        levels: int = 10
    ) -> Dict[str, List[Dict[str, Decimal]]]:
        """
        Get order book depth from GRVT.
        
        Args:
            contract_id: Contract/symbol identifier
            levels: Number of price levels to fetch (default: 10)
            
        Returns:
            Dictionary with 'bids' and 'asks' lists of dicts with 'price' and 'size'
        """
        try:
            # Get order book from GRVT REST client
            order_book = self.rest_client.fetch_order_book(contract_id, limit=levels)

            if not order_book or 'bids' not in order_book or 'asks' not in order_book:
                self.logger.warning("Unable to get order book from GRVT")
                return {'bids': [], 'asks': []}

            # Extract bids and asks
            # GRVT format (via ccxt): [{'price': '50000', 'amount': '1.5'}, ...]
            bids_raw = order_book.get('bids', [])
            asks_raw = order_book.get('asks', [])

            # Convert to standardized format (limit to requested levels)
            bids = [{'price': Decimal(str(bid['price'])), 
                    'size': Decimal(str(bid.get('amount', bid.get('size', 0))))} 
                   for bid in bids_raw[:levels]]
            asks = [{'price': Decimal(str(ask['price'])), 
                    'size': Decimal(str(ask.get('amount', ask.get('size', 0))))} 
                   for ask in asks_raw[:levels]]

            return {
                'bids': bids,
                'asks': asks
            }

        except Exception as e:
            self.logger.error(f"Error fetching order book depth: {e}")
            # Return empty order book on error
            return {'bids': [], 'asks': []}

    async def place_limit_order(
        self,
        contract_id: str,
        quantity: Decimal,
        price: Decimal,
        side: str
    ) -> OrderResult:
        """
        Place a limit order at a specific price on GRVT.
        
        Args:
            contract_id: Contract identifier
            quantity: Order quantity
            price: Limit price
            side: 'buy' or 'sell'
            
        Returns:
            OrderResult with order details
        """
        # Place the order using GRVT SDK with post_only for maker fees
        order_result = self.rest_client.create_limit_order(
            symbol=contract_id,
            side=side,
            amount=quantity,
            price=price,
            params={'post_only': True}
        )
        
        if not order_result:
            raise Exception(f"[LIMIT] Error placing order")

        client_order_id = order_result.get('metadata').get('client_order_id')
        order_status = order_result.get('state').get('status')
        order_status_start_time = time.time()
        order_info = await self.get_order_info(client_order_id=client_order_id)
        if order_info is not None:
            order_status = order_info.status

        while order_status in ['PENDING'] and time.time() - order_status_start_time < 10:
            # Check order status after a short delay
            await asyncio.sleep(0.05)
            order_info = await self.get_order_info(client_order_id=client_order_id)
            if order_info is not None:
                order_status = order_info.status

        if order_status == 'PENDING':
            raise Exception('GRVT Server Error: Order not processed after 10 seconds')
        else:
            return order_info

    async def get_order_price(self, direction: str) -> Decimal:
        """Get the price of an order with GRVT using official SDK."""
        best_bid, best_ask = await self.fetch_bbo_prices(self.config.contract_id)
        if best_bid <= 0 or best_ask <= 0:
            raise ValueError("Invalid bid/ask prices")

        if direction == 'buy':
            return best_ask - self.config.tick_size
        elif direction == 'sell':
            return best_bid + self.config.tick_size
        else:
            raise ValueError("Invalid direction")


    async def place_market_order(self, contract_id: str, quantity: Decimal, side: str) -> OrderResult:
        """
        Place a market order on GRVT (uses limit order without post_only to ensure fill).
        This acts as a true market order by using an aggressive limit price.
        """
        try:
            # Get aggressive price
            best_bid, best_ask = await self.fetch_bbo_prices(contract_id)
            
            if best_bid <= 0 or best_ask <= 0:
                return OrderResult(success=False, error_message='Invalid bid/ask prices')
            
            # Use very aggressive pricing to ensure immediate fill
            if side.lower() == 'buy':
                # Buy at ask price
                order_price = best_ask
            else:
                # Sell at bid price
                order_price = best_bid
            
            order_price = self.round_to_tick(order_price)
            
            # Place limit order WITHOUT post_only (allows taker execution)
            order_result = self.rest_client.create_limit_order(
                symbol=contract_id,
                side=side,
                amount=quantity,
                price=order_price,
                params={'post_only': False}  # Allow taker order for immediate fill
            )
            
            if not order_result:
                return OrderResult(success=False, error_message='Failed to place market order')
            
            client_order_id = order_result.get('metadata').get('client_order_id')
            order_status = order_result.get('state').get('status')
            
            # Wait for order confirmation
            order_status_start_time = time.time()
            order_info = await self.get_order_info(client_order_id=client_order_id)
            if order_info is not None:
                order_status = order_info.status

            while order_status in ['PENDING'] and time.time() - order_status_start_time < 10:
                await asyncio.sleep(0.05)
                order_info = await self.get_order_info(client_order_id=client_order_id)
                if order_info is not None:
                    order_status = order_info.status

            if order_status == 'PENDING':
                return OrderResult(success=False, error_message='GRVT Server Error: Order not processed after 10 seconds')
            
            return OrderResult(
                success=True,
                order_id=order_info.order_id if order_info else None,
                side=side,
                size=quantity,
                price=order_price,
                status=order_status
            )
                
        except Exception as e:
            self.logger.error(f"Error placing market order: {e}")
            return OrderResult(success=False, error_message=str(e))

    async def cancel_order(self, order_id: str) -> OrderResult:
        """Cancel an order with GRVT."""
        try:
            # Cancel the order using GRVT SDK
            cancel_result = self.rest_client.cancel_order(id=order_id)

            if cancel_result:
                return OrderResult(success=True)
            else:
                return OrderResult(success=False, error_message='Failed to cancel order')

        except Exception as e:
            return OrderResult(success=False, error_message=str(e))

    @query_retry(reraise=True)
    async def get_order_info(self, order_id: str = None, client_order_id: str = None) -> Optional[OrderInfo]:
        """Get order information from GRVT."""
        # Get order information using GRVT SDK
        if order_id is not None:
            order_data = self.rest_client.fetch_order(id=order_id)
        elif client_order_id is not None:
            order_data = self.rest_client.fetch_order(params={'client_order_id': client_order_id})
        else:
            raise ValueError("Either order_id or client_order_id must be provided")

        if not order_data or 'result' not in order_data:
            raise ValueError(f"Unable to get order info: {order_id}")

        order = order_data['result']
        legs = order.get('legs', [])
        if not legs:
            raise ValueError(f"Unable to get order info: {order_id}")

        leg = legs[0]  # Get first leg
        state = order.get('state', {})

        return OrderInfo(
            order_id=order.get('order_id', ''),
            side=leg.get('is_buying_asset', False) and 'buy' or 'sell',
            size=Decimal(leg.get('size', 0)),
            price=Decimal(leg.get('limit_price', 0)),
            status=state.get('status', ''),
            filled_size=(Decimal(state.get('traded_size', ['0'])[0])
                         if isinstance(state.get('traded_size'), list) else Decimal(0)),
            remaining_size=(Decimal(state.get('book_size', ['0'])[0])
                            if isinstance(state.get('book_size'), list) else Decimal(0))
        )

    @query_retry(reraise=True)
    async def get_active_orders(self, contract_id: str) -> List[OrderInfo]:
        """Get active orders for a contract."""
        # Get active orders using GRVT SDK
        orders = self.rest_client.fetch_open_orders(symbol=contract_id)

        if not orders:
            return []

        order_list = []
        for order in orders:
            legs = order.get('legs', [])
            if not legs:
                continue

            leg = legs[0]  # Get first leg
            state = order.get('state', {})

            order_list.append(OrderInfo(
                order_id=order.get('order_id', ''),
                side=leg.get('is_buying_asset', False) and 'buy' or 'sell',
                size=Decimal(leg.get('size', 0)),
                price=Decimal(leg.get('limit_price', 0)),
                status=state.get('status', ''),
                filled_size=(Decimal(state.get('traded_size', ['0'])[0])
                             if isinstance(state.get('traded_size'), list) else Decimal(0)),
                remaining_size=(Decimal(state.get('book_size', ['0'])[0])
                                if isinstance(state.get('book_size'), list) else Decimal(0))
            ))

        return order_list

    @query_retry(reraise=True)
    async def get_account_positions(self) -> Decimal:
        """Get account positions."""
        # Get positions using GRVT SDK
        positions = self.rest_client.fetch_positions()

        for position in positions:
            if position.get('instrument') == self.config.contract_id:
                return abs(Decimal(position.get('size', 0)))

        return Decimal(0)
    
    async def get_account_balance(self) -> Optional[Decimal]:
        """
        Get available account balance from GRVT.
        
        TODO: Implement when GRVT trading is in production.
        Need to query GRVT API for available balance.
        
        Returns:
            None (not yet implemented)
        """
        self.logger.log("[GRVT] get_account_balance not yet implemented", "DEBUG")
        return None
    
    async def get_position_snapshot(self, symbol: str) -> Optional[ExchangePositionSnapshot]:
        """
        Get position snapshot for a symbol using official SDK.
        """
        # TODO
        return None

    async def get_leverage_info(self, symbol: str) -> Dict[str, Any]:
        """
        Get leverage information for GRVT.
        
        TODO: Implement actual API query when GRVT trading is in production.
        Currently returns conservative defaults.
        """
        self.logger.log(
            f"[GRVT] get_leverage_info not yet implemented, using defaults for {symbol}",
            "DEBUG"
        )
        return {
            'max_leverage': Decimal('10'),
            'max_notional': None,
            'margin_requirement': Decimal('0.10'),  # 10% margin = 10x leverage
            'brackets': None,
            'error': None  # Using default values (not queried from API)
        }

    async def get_contract_attributes(self) -> Tuple[str, Decimal]:
        """Get contract ID and tick size for a ticker."""
        ticker = self.config.ticker
        if not ticker:
            raise ValueError("Ticker is empty")

        # Get markets from GRVT
        markets = self.rest_client.fetch_markets()

        for market in markets:
            if (market.get('base') == ticker and
                    market.get('quote') == 'USDT' and
                    market.get('kind') == 'PERPETUAL'):

                self.config.contract_id = market.get('instrument', '')
                self.config.tick_size = Decimal(market.get('tick_size', 0))

                # Validate minimum quantity
                min_size = Decimal(market.get('min_size', 0))
                if self.config.quantity < min_size:
                    raise ValueError(
                        f"Order quantity is less than min quantity: {self.config.quantity} < {min_size}"
                    )

                return self.config.contract_id, self.config.tick_size

        raise ValueError(f"Contract not found for ticker: {ticker}")
