"""
Order manager module for Paradex client.

Handles order placement, cancellation, querying, and tracking.
"""

import asyncio
import time
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional

from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type

from exchange_clients.base_models import OrderInfo, OrderResult, query_retry
from exchange_clients.paradex.client.utils.converters import build_order_info_from_paradex
from exchange_clients.paradex.client.utils.helpers import to_decimal, normalize_order_side
from exchange_clients.paradex.common import normalize_symbol


class ParadexOrderManager:
    """
    Order manager for Paradex exchange.
    
    Handles:
    - Order placement (limit, market)
    - Order cancellation
    - Order status queries
    - Order tracking and caching
    """
    
    def __init__(
        self,
        paradex_client: Any,
        api_client: Any,
        config: Any,
        logger: Any,
        latest_orders: Dict[str, OrderInfo],
        market_data_manager: Optional[Any] = None,
        normalize_symbol_fn: Optional[Any] = None,
    ):
        """
        Initialize order manager.
        
        Args:
            paradex_client: Paradex SDK client instance
            api_client: Paradex API client instance (paradex.api_client)
            config: Trading configuration object
            logger: Logger instance
            latest_orders: Dictionary storing latest OrderInfo objects (client._latest_orders)
            market_data_manager: Optional market data manager (for BBO prices, metadata)
            normalize_symbol_fn: Function to normalize symbols
        """
        self.paradex_client = paradex_client
        self.api_client = api_client
        self.config = config
        self.logger = logger
        self.latest_orders = latest_orders
        self.market_data = market_data_manager
        self.normalize_symbol = normalize_symbol_fn or normalize_symbol
    
    @retry(
        stop=stop_after_attempt(5),
        wait=wait_fixed(3),
        retry=retry_if_exception_type(Exception),
        reraise=True
    )
    def _submit_order_sync(self, order: Any) -> Dict[str, Any]:
        """
        Submit an order synchronously (SDK is blocking).
        
        Args:
            order: Paradex Order object
            
        Returns:
            Order result dictionary from API
        """
        return self.api_client.submit_order(order)
    
    async def place_limit_order(
        self,
        contract_id: str,
        quantity: Decimal,
        price: Decimal,
        side: str,
        reduce_only: bool = False,
    ) -> OrderResult:
        """
        Place a limit order at a specific price on Paradex.
        
        Paradex limit orders are post-only by default (instruction="POST_ONLY").
        
        Args:
            contract_id: Contract/symbol identifier (e.g., "BTC-USD-PERP")
            quantity: Order quantity
            price: Limit price
            side: 'buy' or 'sell'
            reduce_only: If True, order can only reduce existing position
            
        Returns:
            OrderResult with order details
        """
        from paradex_py.common.order import Order, OrderType, OrderSide
        
        try:
            # Ensure market metadata is loaded (for order_size_increment)
            if self.market_data:
                await self.market_data.ensure_market_metadata(contract_id)
                metadata = self.market_data._market_metadata.get(contract_id, {})
                order_size_increment = metadata.get('order_size_increment')
            else:
                order_size_increment = getattr(self.config, 'order_size_increment', Decimal('0.001'))
            
            # Round quantity to order size increment
            if order_size_increment:
                # Ensure order_size_increment is Decimal
                if not isinstance(order_size_increment, Decimal):
                    order_size_increment = Decimal(str(order_size_increment))
                quantity = quantity.quantize(order_size_increment, rounding=ROUND_HALF_UP)
            
            # Round price to tick size
            # Ensure price and tick_size are Decimal before quantizing
            price = Decimal(str(price)) if not isinstance(price, Decimal) else price
            tick_size = getattr(self.config, 'tick_size', None)
            if tick_size:
                # Ensure tick_size is Decimal
                if not isinstance(tick_size, Decimal):
                    tick_size = Decimal(str(tick_size))
                if tick_size > 0:
                    price = price.quantize(tick_size, rounding=ROUND_HALF_UP)
            
            # Convert side string to OrderSide enum
            side_upper = side.upper()
            if side_upper == 'BUY':
                order_side = OrderSide.Buy
            elif side_upper == 'SELL':
                order_side = OrderSide.Sell
            else:
                raise ValueError(f"Invalid side: {side}")
            
            # Create order using Paradex SDK
            order = Order(
                market=contract_id,
                order_type=OrderType.Limit,
                order_side=order_side,
                size=quantity,
                limit_price=price,
                instruction="POST_ONLY",  # Paradex limit orders are post-only
                reduce_only=reduce_only,
            )
            
            # Submit order (synchronous SDK call in executor)
            loop = asyncio.get_event_loop()
            order_result = await loop.run_in_executor(
                None,
                self._submit_order_sync,
                order
            )
            
            order_id = order_result.get('id')
            if not order_id:
                return OrderResult(
                    success=False,
                    error_message='No order ID in response'
                )
            
            # Wait briefly for order status to stabilize
            order_status = order_result.get('status', 'NEW')
            order_status_start_time = time.time()
            
            # Poll order status until it's no longer NEW (max 10 seconds)
            while order_status == 'NEW' and time.time() - order_status_start_time < 10:
                await asyncio.sleep(0.1)
                order_info = await self.get_order_info(order_id, force_refresh=True)
                if order_info is not None:
                    order_status = order_info.status
                    if order_status != 'NEW':
                        break
            
            if order_status == 'NEW':
                self.logger.warning(
                    f"Order {order_id} still in NEW status after 10 seconds. "
                    "This may indicate a server processing delay."
                )
            
            # Build OrderResult from order_info if available
            order_info = await self.get_order_info(order_id, force_refresh=False)
            if order_info:
                return OrderResult(
                    success=True,
                    order_id=str(order_id),
                    side=side,
                    size=quantity,
                    price=price,
                    status=order_info.status,
                    filled_size=order_info.filled_size,
                )
            else:
                # Fallback to basic OrderResult
                return OrderResult(
                    success=True,
                    order_id=str(order_id),
                    side=side,
                    size=quantity,
                    price=price,
                    status=order_status,
                )
                
        except Exception as e:
            self.logger.error(f"Failed to place limit order: {e}")
            return OrderResult(
                success=False,
                error_message=str(e)
            )
    
    async def place_market_order(
        self,
        contract_id: str,
        quantity: Decimal,
        side: str,
        reduce_only: bool = False,
    ) -> OrderResult:
        """
        Place a market order for immediate execution on Paradex.
        
        Args:
            contract_id: Contract/symbol identifier (e.g., "BTC-USD-PERP")
            quantity: Order quantity
            side: 'buy' or 'sell'
            reduce_only: If True, order can only reduce existing position
            
        Returns:
            OrderResult with order details and execution price
        """
        from paradex_py.common.order import Order, OrderType, OrderSide
        
        try:
            # Ensure market metadata is loaded (for order_size_increment)
            if self.market_data:
                await self.market_data.ensure_market_metadata(contract_id)
                metadata = self.market_data._market_metadata.get(contract_id, {})
                order_size_increment = metadata.get('order_size_increment')
            else:
                order_size_increment = getattr(self.config, 'order_size_increment', Decimal('0.001'))
            
            # Round quantity to order size increment
            if order_size_increment:
                quantity = quantity.quantize(order_size_increment, rounding=ROUND_HALF_UP)
            
            # Convert side string to OrderSide enum
            side_upper = side.upper()
            if side_upper == 'BUY':
                order_side = OrderSide.Buy
            elif side_upper == 'SELL':
                order_side = OrderSide.Sell
            else:
                raise ValueError(f"Invalid side: {side}")
            
            # Create market order (limit_price=0 for market orders)
            order = Order(
                market=contract_id,
                order_type=OrderType.Market,
                order_side=order_side,
                size=quantity,
                limit_price=Decimal("0"),  # Market orders use 0 price
                reduce_only=reduce_only,
            )
            
            # Submit order (synchronous SDK call in executor)
            loop = asyncio.get_event_loop()
            order_result = await loop.run_in_executor(
                None,
                self._submit_order_sync,
                order
            )
            
            order_id = order_result.get('id')
            if not order_id:
                return OrderResult(
                    success=False,
                    error_message='No order ID in response'
                )
            
            # Market orders should execute quickly - wait briefly for fill
            await asyncio.sleep(0.5)
            
            # Get order info to check execution price
            order_info = await self.get_order_info(order_id, force_refresh=True)
            if order_info:
                # Calculate average execution price from filled size and price
                execution_price = order_info.price if order_info.price > 0 else None
                
                return OrderResult(
                    success=True,
                    order_id=str(order_id),
                    side=side,
                    size=quantity,
                    price=execution_price or Decimal("0"),
                    status=order_info.status,
                    filled_size=order_info.filled_size,
                )
            else:
                # Fallback - assume filled if we can't get info
                return OrderResult(
                    success=True,
                    order_id=str(order_id),
                    side=side,
                    size=quantity,
                    price=Decimal("0"),  # Will be updated when order info is available
                    status='FILLED',
                    filled_size=quantity,
                )
                
        except Exception as e:
            self.logger.error(f"Failed to place market order: {e}")
            return OrderResult(
                success=False,
                error_message=str(e)
            )
    
    async def cancel_order(self, order_id: str) -> OrderResult:
        """
        Cancel an order on Paradex.
        
        Args:
            order_id: Order identifier to cancel
            
        Returns:
            OrderResult indicating success/failure and any partial fills
        """
        try:
            # Get order info before canceling to check filled_size
            order_info = await self.get_order_info(order_id, force_refresh=False)
            filled_size = order_info.filled_size if order_info else Decimal("0")
            
            # Cancel order (synchronous SDK call in executor)
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                self.api_client.cancel_order,
                order_id
            )
            
            return OrderResult(
                success=True,
                order_id=order_id,
                filled_size=filled_size,
            )
            
        except Exception as e:
            self.logger.error(f"Failed to cancel order {order_id}: {e}")
            return OrderResult(
                success=False,
                order_id=order_id,
                error_message=str(e)
            )
    
    @query_retry(default_return=None)
    async def get_order_info(self, order_id: str, *, force_refresh: bool = False) -> Optional[OrderInfo]:
        """
        Get detailed information about a specific order.
        
        Args:
            order_id: Order identifier
            force_refresh: If True, bypass cache and fetch fresh from API
            
        Returns:
            OrderInfo with order details, or None if order not found
        """
        # Check cache first (unless force_refresh)
        if not force_refresh and order_id in self.latest_orders:
            return self.latest_orders[order_id]
        
        try:
            # Fetch order from API (synchronous SDK call in executor)
            loop = asyncio.get_event_loop()
            order_data = await loop.run_in_executor(
                None,
                self.api_client.fetch_order,
                order_id
            )
            
            # Convert to OrderInfo
            order_info = build_order_info_from_paradex(order_data, order_id)
            
            if order_info:
                # Cache it
                self.latest_orders[order_id] = order_info
            
            return order_info
            
        except Exception as e:
            self.logger.error(f"Failed to get order info for {order_id}: {e}")
            return None
    
    @query_retry(default_return=[])
    async def get_active_orders(self, contract_id: str) -> List[OrderInfo]:
        """
        Get all active (open) orders for a contract.
        
        Args:
            contract_id: Contract/symbol identifier
            
        Returns:
            List of OrderInfo for active orders
        """
        try:
            # Fetch active orders (synchronous SDK call in executor)
            loop = asyncio.get_event_loop()
            orders_response = await loop.run_in_executor(
                None,
                lambda: self.api_client.fetch_orders({"market": contract_id, "status": "OPEN"})
            )
            
            if not orders_response or 'results' not in orders_response:
                return []
            
            # Convert to OrderInfo list
            orders = []
            for order_data in orders_response['results']:
                order_info = build_order_info_from_paradex(order_data)
                if order_info:
                    orders.append(order_info)
                    # Cache it
                    self.latest_orders[order_info.order_id] = order_info
            
            return orders
            
        except Exception as e:
            self.logger.error(f"Failed to get active orders for {contract_id}: {e}")
            return []

