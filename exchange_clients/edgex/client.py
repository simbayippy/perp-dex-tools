"""
EdgeX exchange client implementation.
"""

import os
import asyncio
import json
import traceback
from decimal import Decimal
from typing import Dict, Any, List, Optional, Tuple
from edgex_sdk import Client, OrderSide, WebSocketManager, CancelOrderParams, GetOrderBookDepthParams, GetActiveOrderParams

from exchange_clients.base_client import BaseExchangeClient
from exchange_clients.base_models import (
    OrderResult,
    OrderInfo,
    query_retry,
    ExchangePositionSnapshot,
    MissingCredentialsError,
    validate_credentials,
)
from helpers.unified_logger import get_exchange_logger


class EdgeXClient(BaseExchangeClient):
    """EdgeX exchange client implementation."""

    def __init__(self, config: Dict[str, Any]):
        """Initialize EdgeX client."""
        super().__init__(config)

        # EdgeX credentials from environment (validation happens in _validate_config)
        self.account_id = os.getenv('EDGEX_ACCOUNT_ID')
        self.stark_private_key = os.getenv('EDGEX_STARK_PRIVATE_KEY')
        self.base_url = os.getenv('EDGEX_BASE_URL', 'https://pro.edgex.exchange')
        self.ws_url = os.getenv('EDGEX_WS_URL', 'wss://quote.edgex.exchange')

        # Initialize EdgeX client using official SDK
        # Wrap in try-catch to convert SDK credential errors to MissingCredentialsError
        try:
            self.client = Client(
                base_url=self.base_url,
                account_id=int(self.account_id),
                stark_private_key=self.stark_private_key
            )

            # Initialize WebSocket manager using official SDK
            self.ws_manager = WebSocketManager(
                base_url=self.ws_url,
                account_id=int(self.account_id),
                stark_pri_key=self.stark_private_key
            )
        except Exception as e:
            # If SDK fails to initialize due to invalid credentials, raise as credential error
            if any(keyword in str(e).lower() for keyword in ['invalid', 'credential', 'auth', 'key']):
                raise MissingCredentialsError(f"Invalid EdgeX credentials format: {e}")
            raise

        # Initialize logger
        self.logger = get_exchange_logger("edgex", self.config.ticker)

        self._order_update_handler = None

        # --- reconnection state ---
        self._ws_task: Optional[asyncio.Task] = None
        self._ws_stop = asyncio.Event()
        self._ws_disconnected = asyncio.Event()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def _validate_config(self) -> None:
        """Validate EdgeX configuration."""
        # Use base validation helper (reduces code duplication)
        validate_credentials('EDGEX_ACCOUNT_ID', os.getenv('EDGEX_ACCOUNT_ID'))
        validate_credentials('EDGEX_STARK_PRIVATE_KEY', os.getenv('EDGEX_STARK_PRIVATE_KEY'))

    # ---------------------------
    # Connection / Reconnect
    # ---------------------------

    async def connect(self) -> None:
        """Connect private WS and keep it alive with auto-reconnect."""
        self._loop = asyncio.get_running_loop()

        # Hook disconnect/connect once (SDK calls these from threads)
        try:
            private_client = self.ws_manager.get_private_client()
            private_client.on_disconnect(
                lambda exc: self._loop.call_soon_threadsafe(self._ws_disconnected.set)
            )
            private_client.on_connect(
                lambda: self.logger.log("[WS] private connected", "INFO")
            )
        except Exception as e:
            self.logger.error(f"[WS] failed to set hooks: {e}")

        if not self._ws_task or self._ws_task.done():
            self._ws_task = asyncio.create_task(self._run_private_ws())

        # give first connection a moment (optional)
        await asyncio.sleep(0.5)

    async def _run_private_ws(self):
        """Tiny reconnect loop with exponential backoff."""
        backoff = 1.0
        while not self._ws_stop.is_set():
            try:
                # connect
                self.ws_manager.connect_private()
                self.logger.info("[WS] connected")
                backoff = 1.0

                # wait until either disconnect or stop
                self._ws_disconnected.clear()
                done, _ = await asyncio.wait(
                    {asyncio.create_task(self._ws_stop.wait()),
                    asyncio.create_task(self._ws_disconnected.wait()),},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if self._ws_stop.is_set():
                    break

                self.logger.warning(
                    "[WS] disconnected; attempting to reconnect…")
            except Exception as e:
                self.logger.error(f"[WS] connect error: {e}")
            finally:
                # ensure socket is closed before retry
                try:
                    self.ws_manager.disconnect_private()
                except Exception:
                    pass

            # backoff and retry
            await asyncio.sleep(backoff)
            backoff = min(60.0, backoff * 2)

        # Final cleanup (on stop)
        try:
            self.ws_manager.disconnect_private()
        except Exception:
            pass

    async def disconnect(self) -> None:
        """Disconnect from EdgeX."""
        try:
            self._ws_stop.set()
            if self._ws_task:
                await self._ws_task
        except Exception:
            pass

        try:
            if hasattr(self, "client") and self.client:
                await self.client.close()
            if hasattr(self, "ws_manager"):
                self.ws_manager.disconnect_all()
        except Exception as e:
            self.logger.error(f"Error during EdgeX disconnect: {e}")

    # ---------------------------
    # Utility / Name
    # ---------------------------

    def get_exchange_name(self) -> str:
        """Get the exchange name."""
        return "edgex"

    # ---------------------------
    # REST-ish helpers
    # ---------------------------

    @query_retry(default_return=(0, 0))
    async def fetch_bbo_prices(self, contract_id: str) -> Tuple[Decimal, Decimal]:
        depth_params = GetOrderBookDepthParams(contract_id=contract_id, limit=15)
        order_book = await self.client.quote.get_order_book_depth(depth_params)
        order_book_data = order_book['data']

        # Get the first (and should be only) order book entry
        order_book_entry = order_book_data[0]

        # Extract bids and asks from the entry
        bids = order_book_entry.get('bids', [])
        asks = order_book_entry.get('asks', [])

        # Best bid is the highest price someone is willing to buy at
        best_bid = Decimal(bids[0]['price']) if bids and len(bids) > 0 else 0
        # Best ask is the lowest price someone is willing to sell at
        best_ask = Decimal(asks[0]['price']) if asks and len(asks) > 0 else 0
        return best_bid, best_ask

    async def get_order_book_depth(
        self, 
        contract_id: str, 
        levels: int = 10
    ) -> Dict[str, List[Dict[str, Decimal]]]:
        """
        Get order book depth from EdgeX.
        
        Args:
            contract_id: Contract/symbol identifier
            levels: Number of price levels to fetch (default: 10)
            
        Returns:
            Dictionary with 'bids' and 'asks' lists of dicts with 'price' and 'size'
        """
        try:
            depth_params = GetOrderBookDepthParams(contract_id=contract_id, limit=levels)
            order_book = await self.client.quote.get_order_book_depth(depth_params)
            order_book_data = order_book.get('data', [])

            if not order_book_data:
                return {'bids': [], 'asks': []}

            # Get the first (and should be only) order book entry
            order_book_entry = order_book_data[0]

            # Extract bids and asks from the entry
            bids_raw = order_book_entry.get('bids', [])
            asks_raw = order_book_entry.get('asks', [])

            # Convert to standardized format
            # EdgeX format: [{'price': '50000', 'size': '1.5'}, ...]
            bids = [{'price': Decimal(bid['price']), 'size': Decimal(bid['size'])} 
                   for bid in bids_raw]
            asks = [{'price': Decimal(ask['price']), 'size': Decimal(ask['size'])} 
                   for ask in asks_raw]

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
        Place a limit order at a specific price on EdgeX.
        
        Args:
            contract_id: Contract identifier
            quantity: Order quantity
            price: Limit price
            side: 'buy' or 'sell'
            
        Returns:
            OrderResult with order details
        """
        try:
            # Convert side to OrderSide enum
            order_side = OrderSide.BUY if side.lower() == 'buy' else OrderSide.SELL
            
            # Place limit order with post_only for maker fees
            order_result = await self.client.create_limit_order(
                contract_id=contract_id,
                size=str(quantity),
                price=str(self.round_to_tick(price)),
                side=order_side,
                post_only=True
            )
            
            if not order_result or 'data' not in order_result:
                return OrderResult(success=False, error_message='Failed to place limit order')
            
            # Extract order ID
            order_id = order_result['data'].get('orderId')
            if not order_id:
                return OrderResult(success=False, error_message='No order ID in response')
            
            # Check order status
            await asyncio.sleep(0.01)
            order_info = await self.get_order_info(order_id)
            
            if order_info and order_info.status in ['OPEN', 'PARTIALLY_FILLED', 'FILLED']:
                return OrderResult(
                    success=True,
                    order_id=order_id,
                    side=side,
                    size=quantity,
                    price=price,
                    status=order_info.status
                )
            elif order_info and order_info.status == 'CANCELED':
                return OrderResult(success=False, error_message='Limit order was rejected (post-only constraint)')
            else:
                # Assume success if we can't verify
                return OrderResult(
                    success=True,
                    order_id=order_id,
                    side=side,
                    size=quantity,
                    price=price,
                    status='OPEN'
                )
                
        except Exception as e:
            self.logger.error(f"Error placing limit order: {e}")
            return OrderResult(
                success=False,
                error_message=f"Failed to place limit order: {str(e)}"
            )

    async def get_order_price(self, direction: str) -> Decimal:
        """Get the price of an order with EdgeX using official SDK."""
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


    async def place_market_order(self, contract_id: str, quantity: Decimal, side: str) -> OrderResult:
        """
        Place a market order on EdgeX (uses limit order without post_only to ensure fill).
        This acts as a true market order by using an aggressive limit price.
        """
        try:
            # Get aggressive price
            best_bid, best_ask = await self.fetch_bbo_prices(contract_id)
            
            if best_bid <= 0 or best_ask <= 0:
                return OrderResult(success=False, error_message='Invalid bid/ask prices')
            
            # Use very aggressive pricing to ensure immediate fill
            if side.lower() == 'buy':
                # Buy at ask price (or slightly above)
                order_price = best_ask
                order_side = OrderSide.BUY
            else:
                # Sell at bid price (or slightly below)
                order_price = best_bid
                order_side = OrderSide.SELL
            
            order_price = self.round_to_tick(order_price)
            
            # Place limit order WITHOUT post_only (allows taker execution)
            order_result = await self.client.create_limit_order(
                contract_id=contract_id,
                size=str(quantity),
                price=str(order_price),
                side=order_side,
                post_only=False  # Allow taker order for immediate fill
            )
            
            if not order_result or 'data' not in order_result:
                return OrderResult(success=False, error_message='Failed to place market order')
            
            order_id = order_result['data'].get('orderId')
            if not order_id:
                return OrderResult(success=False, error_message='No order ID in response')
            
            # Check order status
            await asyncio.sleep(0.05)
            order_info = await self.get_order_info(order_id)
            
            if order_info:
                return OrderResult(
                    success=True,
                    order_id=order_id,
                    side=side,
                    size=quantity,
                    price=order_price,
                    status=order_info.status
                )
            else:
                return OrderResult(
                    success=True,
                    order_id=order_id,
                    side=side,
                    size=quantity,
                    price=order_price,
                    status='FILLED'
                )
                
        except Exception as e:
            self.logger.error(f"Error placing market order: {e}")
            return OrderResult(success=False, error_message=str(e))

    async def cancel_order(self, order_id: str) -> OrderResult:
        """Cancel an order with EdgeX using official SDK."""
        try:
            # Create cancel parameters using official SDK
            cancel_params = CancelOrderParams(order_id=order_id)

            # Cancel the order using official SDK
            cancel_result = await self.client.cancel_order(cancel_params)

            if not cancel_result or 'data' not in cancel_result:
                return OrderResult(success=False, error_message='Failed to cancel order')

            return OrderResult(success=True)

        except Exception as e:
            return OrderResult(success=False, error_message=str(e))

    @query_retry()
    async def get_order_info(self, order_id: str) -> Optional[OrderInfo]:
        """Get order information from EdgeX using official SDK."""
        # Use the newly created get_order_by_id method
        order_result = await self.client.order.get_order_by_id(order_id_list=[order_id])

        if not order_result or 'data' not in order_result:
            return None

        # The API returns a list of orders, get the first (and should be only) one
        order_list = order_result['data']
        if order_list and len(order_list) > 0:
            order_data = order_list[0]
            return OrderInfo(
                order_id=order_data.get('id', ''),
                side=order_data.get('side', '').lower(),
                size=Decimal(order_data.get('size', 0)),
                price=Decimal(order_data.get('price', 0)),
                status=order_data.get('status', ''),
                filled_size=Decimal(order_data.get('cumMatchSize', 0)),
                remaining_size=Decimal(order_data.get('size', 0)) - Decimal(order_data.get('cumMatchSize', 0))
            )

        return None

    @query_retry(default_return=[])
    async def get_active_orders(self, contract_id: str) -> List[OrderInfo]:
        """Get active orders for a contract using official SDK."""
        # Get active orders using official SDK
        params = GetActiveOrderParams(size="200", offset_data="", filter_contract_id_list=[contract_id])
        active_orders = await self.client.get_active_orders(params)

        if not active_orders or 'data' not in active_orders:
            return []

        # Filter orders for the specific contract and ensure they are dictionaries
        # The API returns orders under 'dataList' key, not 'orderList'
        order_list = active_orders['data'].get('dataList', [])
        contract_orders = []

        for order in order_list:
            if isinstance(order, dict) and order.get('contractId') == contract_id:
                contract_orders.append(OrderInfo(
                    order_id=order.get('id', ''),
                    side=order.get('side', '').lower(),
                    size=Decimal(order.get('size', 0)),
                    price=Decimal(order.get('price', 0)),
                    status=order.get('status', ''),
                    filled_size=Decimal(order.get('cumMatchSize', 0)),
                    remaining_size=Decimal(order.get('size', 0)) - Decimal(order.get('cumMatchSize', 0))
                ))

        return contract_orders

    @query_retry(default_return=0)
    async def get_account_positions(self) -> Decimal:
        """Get account positions using official SDK."""
        positions_data = await self.client.get_account_positions()
        if not positions_data or 'data' not in positions_data:
            self.logger.warning("No positions or failed to get positions")
            position_amt = 0
        else:
            # The API returns positions under data.positionList
            positions = positions_data.get('data', {}).get('positionList', [])
            if positions:
                # Find position for current contract
                position = None
                for p in positions:
                    if isinstance(p, dict) and p.get('contractId') == self.config.contract_id:
                        position = p
                        break

                if position:
                    position_amt = abs(Decimal(position.get('openSize', 0)))
                else:
                    position_amt = 0
            else:
                position_amt = 0
        return position_amt
    
    async def get_account_balance(self) -> Optional[Decimal]:
        """
        Get available account balance from EdgeX.
        
        TODO: Implement when EdgeX trading is in production.
        Need to query EdgeX API for available balance.
        
        Returns:
            None (not yet implemented)
        """
        self.logger.log("[EDGEX] get_account_balance not yet implemented", "DEBUG")
        return None


    async def get_position_snapshot(self, symbol: str) -> Optional[ExchangePositionSnapshot]:
        """
        Get position snapshot for a symbol using official SDK.
        """
        # TODO
        return None
    
    async def get_leverage_info(self, symbol: str) -> Dict[str, Any]:
        """
        Get leverage information for EdgeX.
        
        TODO: Implement actual API query when EdgeX trading is in production.
        Currently returns conservative defaults.
        """
        self.logger.log(
            f"[EDGEX] get_leverage_info not yet implemented, using defaults for {symbol}",
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
        """Get contract ID for a ticker."""
        ticker = self.config.ticker
        if len(ticker) == 0:
            self.logger.error("Ticker is empty")
            raise ValueError("Ticker is empty")

        response = await self.client.get_metadata()
        data = response.get('data', {})
        if not data:
            self.logger.error("Failed to get metadata")
            raise ValueError("Failed to get metadata")

        contract_list = data.get('contractList', [])
        if not contract_list:
            self.logger.error("Failed to get contract list")
            raise ValueError("Failed to get contract list")

        current_contract = None
        for c in contract_list:
            if c.get('contractName') == ticker+'USD':
                current_contract = c
                break

        if not current_contract:
            self.logger.error("Failed to get contract ID for ticker")
            raise ValueError("Failed to get contract ID for ticker")

        self.config.contract_id = current_contract.get('contractId')
        min_quantity = Decimal(current_contract.get('minOrderSize'))
        if self.config.quantity < min_quantity:
            self.logger.error(f"Order quantity is less than min quantity: {self.config.quantity} < {min_quantity}")
            raise ValueError(f"Order quantity is less than min quantity: {self.config.quantity} < {min_quantity}")

        self.config.tick_size = Decimal(current_contract.get('tickSize'))

        return self.config.contract_id, self.config.tick_size
