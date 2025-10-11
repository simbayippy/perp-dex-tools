"""
Backpack exchange client implementation.
"""

import os
import asyncio
import time
from decimal import Decimal
from typing import Dict, Any, List, Optional, Tuple
from bpx.public import Public
from bpx.account import Account
from bpx.constants.enums import OrderTypeEnum, TimeInForceEnum

from exchange_clients.base import BaseExchangeClient, OrderResult, OrderInfo, query_retry, MissingCredentialsError, validate_credentials
from exchange_clients.backpack.websocket_manager import BackpackWebSocketManager
from helpers.unified_logger import get_exchange_logger


class BackpackClient(BaseExchangeClient):
    """Backpack exchange client implementation."""

    def __init__(self, config: Dict[str, Any]):
        """Initialize Backpack client."""
        super().__init__(config)

        # Backpack credentials from environment (validation happens in _validate_config)
        self.public_key = os.getenv('BACKPACK_PUBLIC_KEY')
        self.secret_key = os.getenv('BACKPACK_SECRET_KEY')

        # Initialize Backpack clients using official SDK
        # Wrap in try-catch to convert SDK credential errors to MissingCredentialsError
        try:
            self.public_client = Public()
            self.account_client = Account(
                public_key=self.public_key,
                secret_key=self.secret_key
            )
        except Exception as e:
            # If SDK fails to initialize due to invalid credentials, raise as credential error
            if 'base64' in str(e).lower() or 'invalid' in str(e).lower():
                raise MissingCredentialsError(f"Invalid Backpack credentials format: {e}")
            raise

        self._order_update_handler = None

    def _validate_config(self) -> None:
        """Validate Backpack configuration."""
        # Use base validation helper (reduces code duplication)
        validate_credentials('BACKPACK_PUBLIC_KEY', os.getenv('BACKPACK_PUBLIC_KEY'))
        validate_credentials('BACKPACK_SECRET_KEY', os.getenv('BACKPACK_SECRET_KEY'))

    async def connect(self) -> None:
        """Connect to Backpack WebSocket."""
        # Initialize WebSocket manager
        self.ws_manager = BackpackWebSocketManager(
            public_key=self.public_key,
            secret_key=self.secret_key,
            symbol=self.config.contract_id,  # Use contract_id as symbol for Backpack
            order_update_callback=self._handle_websocket_order_update
        )
        # Pass config to WebSocket manager for order type determination
        self.ws_manager.config = self.config

        # Initialize logger using the same format as helpers
        self.logger = get_exchange_logger("backpack", self.config.ticker)
        self.ws_manager.set_logger(self.logger)

        try:
            # Start WebSocket connection in background task
            asyncio.create_task(self.ws_manager.connect())
            # Wait a moment for connection to establish
            await asyncio.sleep(2)
        except Exception as e:
            self.logger.error(f"Error connecting to Backpack WebSocket: {e}")
            raise

    async def disconnect(self) -> None:
        """Disconnect from Backpack."""
        try:
            if hasattr(self, 'ws_manager') and self.ws_manager:
                await self.ws_manager.disconnect()
        except Exception as e:
            self.logger.error(f"Error during Backpack disconnect: {e}")

    def get_exchange_name(self) -> str:
        """Get the exchange name."""
        return "backpack"

    def setup_order_update_handler(self, handler) -> None:
        """Setup order update handler for WebSocket."""
        self._order_update_handler = handler

    async def _handle_websocket_order_update(self, order_data: Dict[str, Any]):
        """Handle order updates from WebSocket."""
        try:
            event_type = order_data.get('e', '')
            order_id = order_data.get('i', '')
            symbol = order_data.get('s', '')
            side = order_data.get('S', '')
            quantity = order_data.get('q', '0')
            price = order_data.get('p', '0')
            fill_quantity = order_data.get('z', '0')

            # Only process orders for our symbol
            if symbol != self.config.contract_id:
                return

            # Determine order side
            if side.upper() == 'BID':
                order_side = 'buy'
            elif side.upper() == 'ASK':
                order_side = 'sell'
            else:
                self.logger.error(f"Unexpected order side: {side}")
                sys.exit(1)

            # Let strategy determine order type
            order_type = "ORDER"

            if event_type == 'orderFill' and quantity == fill_quantity:
                if self._order_update_handler:
                    self._order_update_handler({
                        'order_id': order_id,
                        'side': order_side,
                        'order_type': order_type,
                        'status': 'FILLED',
                        'size': quantity,
                        'price': price,
                        'contract_id': symbol,
                        'filled_size': fill_quantity
                    })

            elif event_type in ['orderFill', 'orderAccepted', 'orderCancelled', 'orderExpired']:
                if event_type == 'orderFill':
                    status = 'PARTIALLY_FILLED'
                elif event_type == 'orderAccepted':
                    status = 'OPEN'
                elif event_type in ['orderCancelled', 'orderExpired']:
                    status = 'CANCELED'

                if self._order_update_handler:
                    self._order_update_handler({
                        'order_id': order_id,
                        'side': order_side,
                        'order_type': order_type,
                        'status': status,
                        'size': quantity,
                        'price': price,
                        'contract_id': symbol,
                        'filled_size': fill_quantity
                    })

        except Exception as e:
            self.logger.error(f"Error handling WebSocket order update: {e}")

    async def get_order_price(self, direction: str) -> Decimal:
        """Get the price of an order with Backpack using official SDK."""
        best_bid, best_ask = await self.fetch_bbo_prices(self.config.contract_id)
        if best_bid <= 0 or best_ask <= 0:
            self.logger.error("Invalid bid/ask prices")
            raise ValueError("Invalid bid/ask prices")

        if direction == 'buy':
            # For buy orders, place slightly below best ask to ensure execution
            order_price = best_ask - self.config.tick_size
        else:
            # For sell orders, place slightly above best bid to ensure execution
            order_price = best_bid + self.config.tick_size
        return self.round_to_tick(order_price)

    @query_retry(default_return=(0, 0))
    async def fetch_bbo_prices(self, contract_id: str) -> Tuple[Decimal, Decimal]:
        # Get order book depth from Backpack
        order_book = self.public_client.get_depth(contract_id)

        # Extract bids and asks directly from Backpack response
        bids = order_book.get('bids', [])
        asks = order_book.get('asks', [])

        # Sort bids and asks
        bids = sorted(bids, key=lambda x: Decimal(x[0]), reverse=True)  # (highest price first)
        asks = sorted(asks, key=lambda x: Decimal(x[0]))                # (lowest price first)

        # Best bid is the highest price someone is willing to buy at
        best_bid = Decimal(bids[0][0]) if bids and len(bids) > 0 else 0
        # Best ask is the lowest price someone is willing to sell at
        best_ask = Decimal(asks[0][0]) if asks and len(asks) > 0 else 0

        return best_bid, best_ask

    async def get_order_book_depth(
        self, 
        contract_id: str, 
        levels: int = 10
    ) -> Dict[str, List[Dict[str, Decimal]]]:
        """
        Get order book depth from Backpack.
        
        Args:
            contract_id: Contract/symbol identifier
            levels: Number of price levels to fetch (default: 10)
            
        Returns:
            Dictionary with 'bids' and 'asks' lists of dicts with 'price' and 'size'
        """
        try:
            # Get order book from Backpack public API
            order_book = self.public_client.get_depth(contract_id)

            # Extract bids and asks
            # Backpack format: [["price", "quantity"], ...]
            bids_raw = order_book.get('bids', [])
            asks_raw = order_book.get('asks', [])

            # Sort and limit to requested levels
            sorted_bids = sorted(bids_raw, key=lambda x: Decimal(x[0]), reverse=True)[:levels]
            sorted_asks = sorted(asks_raw, key=lambda x: Decimal(x[0]))[:levels]

            # Convert to standardized format
            bids = [{'price': Decimal(bid[0]), 'size': Decimal(bid[1])} 
                   for bid in sorted_bids]
            asks = [{'price': Decimal(ask[0]), 'size': Decimal(ask[1])} 
                   for ask in sorted_asks]

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
        Place a limit order at a specific price on Backpack.
        
        Args:
            contract_id: Contract identifier
            quantity: Order quantity
            price: Limit price
            side: 'buy' or 'sell'
            
        Returns:
            OrderResult with order details
        """
        try:
            # Convert side to Backpack format
            backpack_side = 'Bid' if side.lower() == 'buy' else 'Ask'
            
            # Place limit order with post_only for maker fees
            order_result = self.account_client.execute_order(
                symbol=contract_id,
                side=backpack_side,
                order_type=OrderTypeEnum.LIMIT,
                quantity=str(quantity),
                price=str(self.round_to_tick(price)),
                post_only=True,
                time_in_force=TimeInForceEnum.GTC
            )
            
            if not order_result:
                return OrderResult(success=False, error_message='Failed to place limit order')
            
            # Check if order was rejected
            if 'code' in order_result:
                message = order_result.get('message', 'Unknown error')
                return OrderResult(success=False, error_message=f'Limit order rejected: {message}')
            
            # Extract order ID
            order_id = order_result.get('id')
            if not order_id:
                return OrderResult(success=False, error_message='No order ID in response')
            
            # Check order status
            await asyncio.sleep(0.01)
            order_info = await self.get_order_info(order_id)
            
            if order_info and order_info.status in ['Open', 'PartiallyFilled', 'Filled']:
                return OrderResult(
                    success=True,
                    order_id=order_id,
                    side=side,
                    size=quantity,
                    price=price,
                    status=order_info.status
                )
            elif order_info and order_info.status == 'Cancelled':
                return OrderResult(success=False, error_message='Limit order was rejected (post-only constraint)')
            else:
                # Assume success if we can't verify
                return OrderResult(
                    success=True,
                    order_id=order_id,
                    side=side,
                    size=quantity,
                    price=price,
                    status='Open'
                )
                
        except Exception as e:
            self.logger.error(f"Error placing limit order: {e}")
            return OrderResult(
                success=False,
                error_message=f"Failed to place limit order: {str(e)}"
            )


    async def place_market_order(self, contract_id: str, quantity: Decimal, side: str) -> OrderResult:
        """
        Place a market order on Backpack (true market order for immediate execution).
        """
        try:
            # Convert side to Backpack format
            if side.lower() == 'buy':
                backpack_side = 'Bid'
            elif side.lower() == 'sell':
                backpack_side = 'Ask'
            else:
                raise ValueError(f"Invalid side: {side}")

            result = self.account_client.execute_order(
                symbol=contract_id,
                side=backpack_side,
                order_type=OrderTypeEnum.MARKET,
                quantity=str(quantity)
            )

            if not result:
                return OrderResult(success=False, error_message='Failed to place market order')

            order_id = result.get('id')
            order_status = result.get('status', '').upper()

            if order_status == 'FILLED':
                # Calculate average fill price
                price = Decimal(result.get('executedQuoteQuantity', '0')) / Decimal(result.get('executedQuantity', '1'))
                return OrderResult(
                    success=True,
                    order_id=order_id,
                    side=side.lower(),
                    size=quantity,
                    price=price,
                    status='FILLED'
                )
            else:
                return OrderResult(
                    success=False, 
                    error_message=f'Market order failed with status: {order_status}'
                )
                
        except Exception as e:
            self.logger.error(f"Error placing market order: {e}")
            return OrderResult(success=False, error_message=str(e))

    async def place_close_order(self, contract_id: str, quantity: Decimal, price: Decimal, side: str) -> OrderResult:
        """Place a close order with Backpack using official SDK with retry logic for POST_ONLY rejections."""
        max_retries = 15
        retry_count = 0

        while retry_count < max_retries:
            retry_count += 1
            # Get current market prices to adjust order price if needed
            best_bid, best_ask = await self.fetch_bbo_prices(contract_id)

            if best_bid <= 0 or best_ask <= 0:
                return OrderResult(success=False, error_message='No bid/ask data available')

            # Adjust order price based on market conditions and side
            adjusted_price = price
            if side.lower() == 'sell':
                order_side = 'Ask'
                # For sell orders, ensure price is above best bid to be a maker order
                if price <= best_bid:
                    adjusted_price = best_bid + self.config.tick_size
            elif side.lower() == 'buy':
                order_side = 'Bid'
                # For buy orders, ensure price is below best ask to be a maker order
                if price >= best_ask:
                    adjusted_price = best_ask - self.config.tick_size

            adjusted_price = self.round_to_tick(adjusted_price)
            # Place the order using Backpack SDK (post-only to avoid taker fees)
            order_result = self.account_client.execute_order(
                symbol=contract_id,
                side=order_side,
                order_type=OrderTypeEnum.LIMIT,
                quantity=str(quantity),
                price=str(adjusted_price),
                post_only=True,
                time_in_force=TimeInForceEnum.GTC
            )

            if not order_result:
                return OrderResult(success=False, error_message='Failed to place order')

            if 'code' in order_result:
                message = order_result.get('message', 'Unknown error')
                self.logger.error(f"[CLOSE] Error placing order: {message}")
                continue

            # Extract order ID from response
            order_id = order_result.get('id')
            if not order_id:
                self.logger.error(f"[CLOSE] No order ID in response: {order_result}")
                return OrderResult(success=False, error_message='No order ID in response')

            # Order successfully placed
            return OrderResult(
                success=True,
                order_id=order_id,
                side=side.lower(),
                size=quantity,
                price=adjusted_price,
                status='New'
            )

        return OrderResult(success=False, error_message='Max retries exceeded for close order')

    async def cancel_order(self, order_id: str) -> OrderResult:
        """Cancel an order with Backpack using official SDK."""
        try:
            # Cancel the order using Backpack SDK
            cancel_result = self.account_client.cancel_order(
                symbol=self.config.contract_id,
                order_id=order_id
            )

            if not cancel_result:
                return OrderResult(success=False, error_message='Failed to cancel order')
            if 'code' in cancel_result:
                self.logger.error(
                    f"[CLOSE] Failed to cancel order {order_id}: {cancel_result.get('message', 'Unknown error')}")
                filled_size = self.config.quantity
            else:
                filled_size = Decimal(cancel_result.get('executedQuantity', 0))
            return OrderResult(success=True, filled_size=filled_size)

        except Exception as e:
            return OrderResult(success=False, error_message=str(e))

    @query_retry()
    async def get_order_info(self, order_id: str) -> Optional[OrderInfo]:
        """Get order information from Backpack using official SDK."""
        # Get order information using Backpack SDK
        order_result = self.account_client.get_open_order(
            symbol=self.config.contract_id,
            order_id=order_id
        )

        if not order_result:
            return None

        # Return the order data as OrderInfo
        return OrderInfo(
            order_id=order_result.get('id', ''),
            side=order_result.get('side', '').lower(),
            size=Decimal(order_result.get('quantity', 0)),
            price=Decimal(order_result.get('price', 0)),
            status=order_result.get('status', ''),
            filled_size=Decimal(order_result.get('executedQuantity', 0)),
            remaining_size=Decimal(order_result.get('quantity', 0)) - Decimal(order_result.get('executedQuantity', 0))
        )

    @query_retry(default_return=[])
    async def get_active_orders(self, contract_id: str) -> List[OrderInfo]:
        """Get active orders for a contract using official SDK."""
        # Get active orders using Backpack SDK
        active_orders = self.account_client.get_open_orders(symbol=contract_id)

        if not active_orders:
            return []

        # Return the orders list as OrderInfo objects
        order_list = active_orders if isinstance(active_orders, list) else active_orders.get('orders', [])
        orders = []

        for order in order_list:
            if isinstance(order, dict):
                if order.get('side', '') == 'Bid':
                    side = 'buy'
                elif order.get('side', '') == 'Ask':
                    side = 'sell'
                orders.append(OrderInfo(
                    order_id=order.get('id', ''),
                    side=side,
                    size=Decimal(order.get('quantity', 0)),
                    price=Decimal(order.get('price', 0)),
                    status=order.get('status', ''),
                    filled_size=Decimal(order.get('executedQuantity', 0)),
                    remaining_size=Decimal(order.get('quantity', 0)) - Decimal(order.get('executedQuantity', 0))
                ))

        return orders

    @query_retry(default_return=0)
    async def get_account_positions(self) -> Decimal:
        """Get account positions using official SDK."""
        positions_data = self.account_client.get_open_positions()
        position_amt = 0
        for position in positions_data:
            if position.get('symbol', '') == self.config.contract_id:
                position_amt = abs(Decimal(position.get('netQuantity', 0)))
                break
        return position_amt
    
    async def get_leverage_info(self, symbol: str) -> Dict[str, Any]:
        """
        Get leverage information for Backpack.
        
        TODO: Implement actual API query when Backpack trading is in production.
        Currently returns conservative defaults.
        """
        self.logger.debug(
            f"[BACKPACK] get_leverage_info not yet implemented, using defaults for {symbol}"
        )
        return {
            'max_leverage': Decimal('10'),
            'max_notional': None,
            'margin_requirement': Decimal('0.10'),  # 10% margin = 10x leverage
            'brackets': None
        }

    async def get_contract_attributes(self) -> Tuple[str, Decimal]:
        """Get contract ID for a ticker."""
        ticker = self.config.ticker
        if len(ticker) == 0:
            self.logger.error("Ticker is empty")
            raise ValueError("Ticker is empty")

        markets = self.public_client.get_markets()
        for market in markets:
            if (market.get('marketType', '') == 'PERP' and market.get('baseSymbol', '') == ticker and
                    market.get('quoteSymbol', '') == 'USDC'):
                self.config.contract_id = market.get('symbol', '')
                min_quantity = Decimal(market.get('filters', {}).get('quantity', {}).get('minQuantity', 0))
                self.config.tick_size = Decimal(market.get('filters', {}).get('price', {}).get('tickSize', 0))
                break

        if self.config.contract_id == '':
            self.logger.error("Failed to get contract ID for ticker")
            raise ValueError("Failed to get contract ID for ticker")

        if self.config.quantity < min_quantity:
            self.logger.error(f"Order quantity is less than min quantity: {self.config.quantity} < {min_quantity}")
            raise ValueError(f"Order quantity is less than min quantity: {self.config.quantity} < {min_quantity}")

        if self.config.tick_size == 0:
            self.logger.error("Failed to get tick size for ticker")
            raise ValueError("Failed to get tick size for ticker")

        return self.config.contract_id, self.config.tick_size
