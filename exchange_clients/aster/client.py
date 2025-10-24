"""
Aster exchange client implementation.
"""

import os
import asyncio
import time
import hmac
import hashlib
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Dict, Any, List, Optional, Tuple
from urllib.parse import urlencode
import aiohttp

from exchange_clients.base_client import BaseExchangeClient
from exchange_clients.base_models import (
    OrderResult,
    OrderInfo,
    ExchangePositionSnapshot,
    query_retry,
    MissingCredentialsError,
    validate_credentials,
)
from exchange_clients.aster.common import get_aster_symbol_format
from exchange_clients.aster.websocket_manager import AsterWebSocketManager
from exchange_clients.events import LiquidationEvent
from helpers.unified_logger import get_exchange_logger


class AsterClient(BaseExchangeClient):
    """Aster exchange client implementation."""

    def __init__(
        self, 
        config: Dict[str, Any],
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
    ):
        """
        Initialize Aster client.
        
        Args:
            config: Trading configuration dictionary
            api_key: Optional API key (falls back to env var)
            secret_key: Optional secret key (falls back to env var)
        """
        # Set credentials BEFORE calling super().__init__() because it triggers _validate_config()
        self.api_key = api_key or os.getenv('ASTER_API_KEY')
        self.secret_key = secret_key or os.getenv('ASTER_SECRET_KEY')
        self.base_url = 'https://fapi.asterdex.com'
        
        super().__init__(config)

        # Initialize logger early
        self.logger = get_exchange_logger("aster", self.config.ticker)
        self._order_update_handler = None
        self._latest_orders: Dict[str, OrderInfo] = {}
        self._min_order_notional: Dict[str, Decimal] = {}

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
        """Connect to Aster WebSocket."""
        # Initialize WebSocket manager
        self.ws_manager = AsterWebSocketManager(
            config=self.config,
            api_key=self.api_key,
            secret_key=self.secret_key,
            order_update_callback=self._handle_websocket_order_update,
            liquidation_callback=self.handle_liquidation_notification,
            symbol_formatter=self.normalize_symbol,
        )

        # Set logger for WebSocket manager
        self.ws_manager.set_logger(self.logger)

        try:
            # Start WebSocket connection in background task
            asyncio.create_task(self.ws_manager.connect())
            # Wait a moment for connection to establish
            await asyncio.sleep(2)
        except Exception as e:
            self.logger.error(f"Error connecting to Aster WebSocket: {e}")
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
        # Use the common utility function
        return get_aster_symbol_format(symbol)
    
    def round_to_step(self, quantity: Decimal) -> Decimal:
        """
        Round quantity to the exchange's step size.
        
        Args:
            quantity: Raw quantity
            
        Returns:
            Rounded quantity that meets step size requirements
        """
        from decimal import ROUND_DOWN
        
        step_size = getattr(self.config, 'step_size', Decimal('1'))
        
        # Round down to nearest step size
        return (quantity / step_size).quantize(Decimal('1'), rounding=ROUND_DOWN) * step_size

    @staticmethod
    def _to_decimal(value: Any, default: Optional[Decimal] = None) -> Optional[Decimal]:
        """Best-effort conversion to Decimal."""
        if value is None or value == "":
            return default
        try:
            return Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            return default

    async def _handle_websocket_order_update(self, order_data: Dict[str, Any]):
        """Handle order updates from WebSocket."""
        try:
            order_id = str(order_data.get("order_id") or order_data.get("i") or order_data.get("orderId") or "")
            if not order_id:
                return

            symbol = order_data.get("contract_id") or order_data.get("symbol") or order_data.get("s")
            expected_symbol = getattr(self.config, "contract_id", None)
            if expected_symbol and symbol and symbol != expected_symbol:
                return

            side_raw = (order_data.get("side") or order_data.get("S") or "").lower()
            side = side_raw if side_raw in {"buy", "sell"} else side_raw.lower()

            quantity = self._to_decimal(order_data.get("size"), Decimal("0"))
            filled = self._to_decimal(order_data.get("filled_size"), Decimal("0"))
            price = self._to_decimal(order_data.get("price"), Decimal("0"))

            remaining = None
            if quantity is not None and filled is not None:
                remaining = quantity - filled
                if remaining < Decimal("0"):
                    remaining = Decimal("0")

            status = (order_data.get("status") or order_data.get("X") or "").upper()
            if status == "OPEN" and filled and filled > Decimal("0"):
                status = "PARTIALLY_FILLED"

            info = OrderInfo(
                order_id=order_id,
                side=side or "",
                size=quantity or Decimal("0"),
                price=price or Decimal("0"),
                status=status,
                filled_size=filled or Decimal("0"),
                remaining_size=remaining or Decimal("0"),
            )
            self._latest_orders[order_id] = info

            if status in {"FILLED", "CANCELED"}:
                self.logger.info(
                    f"[WEBSOCKET] [ASTER] {status} "
                    f"{filled or quantity} @ {price or 'n/a'}"
                )
            else:
                self.logger.info(
                    f"[WEBSOCKET] [ASTER] {status} "
                    f"{filled or quantity} @ {price or 'n/a'}"
                )

            if self._order_update_handler:
                payload = {
                    "order_id": order_id,
                    "side": side,
                    "order_type": order_data.get("order_type") or order_data.get("o") or order_data.get("type") or "UNKNOWN",
                    "status": status,
                    "size": quantity,
                    "price": price,
                    "contract_id": symbol,
                    "filled_size": filled,
                    "raw_event": order_data,
                }
                self._order_update_handler(payload)
        except Exception as e:
            self.logger.error(f"Error handling WebSocket order update: {e}")

    async def handle_liquidation_notification(self, payload: Dict[str, Any]) -> None:
        """Normalize forceOrder messages into LiquidationEvent instances."""
        order = payload.get("o", {})
        symbol = order.get("s")
        if not symbol:
            return

        quantity_raw = order.get("z") or order.get("q") or "0"
        price_raw = order.get("ap") or order.get("p") or "0"

        try:
            quantity = Decimal(str(quantity_raw)).copy_abs()
            price = Decimal(str(price_raw))
        except (InvalidOperation, TypeError):
            return

        if quantity <= 0:
            return

        side = (order.get("S") or "sell").lower()
        timestamp_ms = order.get("T")
        if timestamp_ms is not None:
            try:
                timestamp = datetime.fromtimestamp(int(timestamp_ms) / 1000, tz=timezone.utc)
            except (ValueError, OSError, OverflowError):
                timestamp = datetime.now(timezone.utc)
        else:
            timestamp = datetime.now(timezone.utc)

        internal_symbol = self._to_internal_symbol(symbol)

        event = LiquidationEvent(
            exchange=self.get_exchange_name(),
            symbol=internal_symbol,
            side=side,
            quantity=quantity,
            price=price,
            timestamp=timestamp,
            metadata={"raw": payload},
        )

        await self.emit_liquidation_event(event)

    @staticmethod
    def _to_internal_symbol(stream_symbol: str) -> str:
        if stream_symbol.endswith("USDT"):
            return stream_symbol[:-4]
        return stream_symbol

    @query_retry(default_return=(Decimal("0"), Decimal("0")))
    async def fetch_bbo_prices(self, contract_id: str) -> Tuple[Decimal, Decimal]:
        """
        Fetch best bid and ask prices from Aster.
        
        Tries WebSocket book ticker first (real-time), falls back to REST API.
        """
        # Efficient: Direct access to cached BBO from WebSocket
        if self.ws_manager and self.ws_manager.best_bid is not None and self.ws_manager.best_ask is not None:
            self.logger.info(f"📡 [ASTER] Using real-time BBO from WebSocket")
            return Decimal(str(self.ws_manager.best_bid)), Decimal(str(self.ws_manager.best_ask))
        
        # DRY: Fall back to REST API
        self.logger.info(f"📞 [REST][ASTER] Using REST API fallback")
        # Normalize symbol to Aster's format
        normalized_symbol = self.normalize_symbol(contract_id)
        result = await self._make_request('GET', '/fapi/v1/ticker/bookTicker', {'symbol': normalized_symbol})

        best_bid = Decimal(result.get('bidPrice', 0))
        best_ask = Decimal(result.get('askPrice', 0))

        return best_bid, best_ask

    async def get_order_book_depth(
        self, 
        contract_id: str, 
        levels: int = 10
    ) -> Dict[str, List[Dict[str, Decimal]]]:
        """
        Get order book depth from Aster.
        
        Tries WebSocket depth stream first (100ms snapshots), falls back to REST API.
        
        Args:
            contract_id: Contract/symbol identifier
            levels: Number of price levels to fetch (default: 10)
            
        Returns:
            Dictionary with 'bids' and 'asks' lists of dicts with 'price' and 'size'
        """
        # 🔴 Priority 1: Try WebSocket depth stream (100ms snapshots, zero latency)
        if self.ws_manager:
            ws_book = self.ws_manager.get_order_book(levels)
            if ws_book:
                self.logger.info(
                    f"📡 [ASTER] Using real-time order book from WebSocket "
                    f"({len(ws_book['bids'])} bids, {len(ws_book['asks'])} asks)"
                )
                return ws_book
        
        # 🔄 Priority 2: Fall back to REST API
        # Normalize symbol to Aster's format (e.g., "ZORA" → "ZORAUSDT")
        normalized_symbol = self.normalize_symbol(contract_id)
        
        self.logger.debug(f"🔍 [ASTER] Symbol normalization: '{contract_id}' → '{normalized_symbol}'")
        self.logger.debug(f"🔍 [ASTER] Attempting to fetch order book for symbol='{normalized_symbol}', limit={levels}")
        try:
            self.logger.info(
                f"📊 [ASTER] Fetching order book: symbol={normalized_symbol}, limit={levels}"
            )
            
            # Call Aster API: GET /fapi/v1/depth
            # Note: Aster expects symbols with quote currency (e.g., "BTCUSDT", not "BTC")
            self.logger.debug(f"📊 [ASTER] Calling API: GET /fapi/v1/depth?symbol={normalized_symbol}&limit={levels}")
            result = await self._make_request('GET', '/fapi/v1/depth', {
                'symbol': normalized_symbol,
                'limit': levels
            })
            
            # Parse response
            # Aster returns: {"bids": [["price", "qty"], ...], "asks": [["price", "qty"], ...]}
            bids_raw = result.get('bids', [])
            asks_raw = result.get('asks', [])
            

            # Convert to standardized format
            bids = [{'price': Decimal(bid[0]), 'size': Decimal(bid[1])} for bid in bids_raw]
            asks = [{'price': Decimal(ask[0]), 'size': Decimal(ask[1])} for ask in asks_raw]

            return {
                'bids': bids,
                'asks': asks
            }
            
        except Exception as e:
            self.logger.error(f"❌ [ASTER] ERROR fetching order book for '{contract_id}': {e}")
            self.logger.error(f"   Hint: Aster expects symbols with quote currency (e.g., 'BTCUSDT' not 'BTC')")
            self.logger.error(f"❌ [ASTER] Error fetching order book for {contract_id}: {e}")
            import traceback
            self.logger.debug(f"Traceback: {traceback.format_exc()}")
            # Return empty order book on error
            return {'bids': [], 'asks': []}

    async def get_order_price(self, direction: str) -> Decimal:
        """Get the price of an order with Aster using official SDK."""
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
        return order_price

    async def place_limit_order(
        self,
        contract_id: str,
        quantity: Decimal,
        price: Decimal,
        side: str
    ) -> OrderResult:
        """
        Place a limit order at a specific price on Aster.
        
        Args:
            contract_id: Contract identifier
            quantity: Order quantity
            price: Limit price
            side: 'buy' or 'sell'
            
        Returns:
            OrderResult with order details
        """
        # Contract ID should already be in Aster format (e.g., "MONUSDT")
        # NO need to normalize again (would cause "MONUSDTUSDT")
        
        self.logger.debug(f"Using contract_id for order: '{contract_id}'")
        
        # Round quantity to step size (e.g., 941.8750094 → 941.875 or 941 depending on stepSize)
        rounded_quantity = self.round_to_step(Decimal(str(quantity)))
        
        self.logger.debug(
            f"Rounded quantity: {quantity} → {rounded_quantity} "
            f"(step_size={getattr(self.config, 'step_size', 'unknown')})"
        )
        
        rounded_price = self.round_to_tick(price)

        min_notional = self.get_min_order_notional(contract_id) or self.get_min_order_notional(getattr(self.config, "ticker", None))
        if min_notional is not None:
            order_notional = rounded_quantity * rounded_price
            if order_notional < min_notional:
                message = (
                    f"[ASTER] Order notional ${order_notional} below minimum ${min_notional}"
                )
                self.logger.error(message)
                raise ValueError(message)

        # Place limit order with post-only (GTX) for maker fees
        order_data = {
            'symbol': contract_id,  # Already normalized (e.g., "MONUSDT")
            'side': side.upper(),
            'type': 'LIMIT',
            'quantity': str(rounded_quantity),
            'price': str(rounded_price),
            'timeInForce': 'GTX'  # GTX is Good Till Crossing (Post Only)
        }
        
        self.logger.debug(f"Placing {side.upper()} limit order: {rounded_quantity} @ {rounded_price}")

        try:
            result = await self._make_request('POST', '/fapi/v1/order', data=order_data)
        except Exception as e:
            self.logger.error(
                f"Failed to place limit order for {contract_id} "
                f"({side.upper()}, qty={quantity}, price={price}): {e}"
            )
            raise
        order_status = result.get('status', '')
        order_id = result.get('orderId', '')
        order_id_str = str(order_id)

        if order_id_str and order_id_str not in self._latest_orders:
            self._latest_orders[order_id_str] = OrderInfo(
                order_id=order_id_str,
                side=side,
                size=quantity,
                price=price,
                status=order_status or 'NEW',
                filled_size=Decimal("0"),
                remaining_size=quantity,
            )

        # Wait briefly to confirm order status using WebSocket cache (with REST fallback)
        start_time = time.time()
        order_status_upper = (order_status or '').upper()
        final_info: Optional[OrderInfo] = None
        while order_status_upper in {'NEW', 'PARTIALLY_FILLED', 'OPEN'} and time.time() - start_time < 2:
            cached = self._latest_orders.get(order_id_str)
            if cached:
                order_status_upper = (cached.status or '').upper()
                final_info = cached
                if order_status_upper in {'FILLED', 'CANCELED', 'EXPIRED', 'REJECTED', 'PARTIALLY_FILLED'}:
                    break
            await asyncio.sleep(0.1)
            order_info = await self.get_order_info(order_id)
            if order_info is not None:
                final_info = order_info
                order_status_upper = (order_info.status or '').upper()
                if order_status_upper in {'FILLED', 'CANCELED', 'EXPIRED', 'REJECTED', 'PARTIALLY_FILLED'}:
                    break

        if final_info is None:
            final_info = self._latest_orders.get(order_id_str)

        if final_info is not None:
            order_status_upper = (final_info.status or '').upper()

        if order_status_upper in {'NEW', 'PARTIALLY_FILLED', 'OPEN'}:
            return OrderResult(
                success=True, 
                order_id=order_id, 
                side=side, 
                size=quantity, 
                price=price, 
                status='OPEN'
            )
        elif order_status_upper == 'FILLED':
            return OrderResult(
                success=True, 
                order_id=order_id, 
                side=side, 
                size=quantity, 
                price=price, 
                status='FILLED'
            )
        elif order_status_upper in {'CANCELED', 'EXPIRED', 'REJECTED'}:
            return OrderResult(
                success=False, 
                error_message=f'Limit order did not remain open: {order_status_upper}'
            )
        else:
            return OrderResult(
                success=False, 
                error_message=f'Unknown order status: {order_status_upper or order_status}'
            )

    async def place_market_order(self, contract_id: str, quantity: Decimal, side: str) -> OrderResult:
        """
        Place a market order on Aster (true market order for immediate execution).
        """
        try:
            # Contract ID should already be in Aster format (e.g., "MONUSDT")
            # from get_contract_attributes(), so use it directly
            
            self.logger.debug(
                f"🔍 [ASTER] Using contract_id for market order: '{contract_id}'"
            )
            
            # Validate side
            if side.lower() not in ['buy', 'sell']:
                return OrderResult(success=False, error_message=f'Invalid side: {side}')

            # Round quantity to step size
            rounded_quantity = self.round_to_step(Decimal(str(quantity)))
            
            self.logger.debug(
                f"📐 [ASTER] Rounded quantity: {quantity} → {rounded_quantity}"
            )

            best_bid, best_ask = await self.fetch_bbo_prices(contract_id)
            if best_bid <= 0 or best_ask <= 0:
                return OrderResult(success=False, error_message="Invalid bid/ask prices")

            expected_price = best_ask if side.lower() == 'buy' else best_bid

            min_notional = self.get_min_order_notional(contract_id) or self.get_min_order_notional(getattr(self.config, "ticker", None))
            if min_notional is not None and expected_price > 0:
                order_notional = rounded_quantity * expected_price
                if order_notional < min_notional:
                    message = (
                        f"[ASTER] Market order notional ${order_notional} below minimum ${min_notional}"
                    )
                    self.logger.error(message)
                    raise ValueError(message)

            # Place the market order
            order_data = {
                'symbol': contract_id,  # Already normalized (e.g., "MONUSDT")
                'side': side.upper(),
                'type': 'MARKET',
                'quantity': str(rounded_quantity)
            }
            
            self.logger.debug(
                f"📤 [ASTER] Placing market order with data: {order_data}"
            )

            result = await self._make_request('POST', '/fapi/v1/order', data=order_data)
            order_status = result.get('status', '')
            order_id = result.get('orderId', '')

            # Wait for order to fill
            start_time = time.time()
            order_info = None
            while order_status != 'FILLED' and time.time() - start_time < 10:
                await asyncio.sleep(0.2)
                order_info = await self.get_order_info(order_id)
                if order_info is not None:
                    order_status = order_info.status

            if order_status == 'FILLED':
                return OrderResult(
                    success=True,
                    order_id=order_id,
                    side=side.lower(),
                    size=quantity,
                    price=order_info.price if order_info else Decimal(0),
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

    async def cancel_order(self, order_id: str) -> OrderResult:
        """Cancel an order with Aster."""
        try:
            result = await self._make_request('DELETE', '/fapi/v1/order', {
                'symbol': self.config.contract_id,
                'orderId': order_id
            })

            if 'orderId' in result:
                order_id_str = str(result.get('orderId', order_id))
                filled_size = self._to_decimal(result.get('executedQty'), Decimal("0"))
                status = result.get('status') or 'CANCELED'

                cached = self._latest_orders.get(order_id_str)
                if cached:
                    remaining_size = cached.size - (filled_size or Decimal("0"))
                    if remaining_size < Decimal("0"):
                        remaining_size = Decimal("0")
                    updated = OrderInfo(
                        order_id=cached.order_id,
                        side=cached.side,
                        size=cached.size,
                        price=cached.price,
                        status=status,
                        filled_size=filled_size or cached.filled_size,
                        remaining_size=remaining_size,
                    )
                else:
                    updated = OrderInfo(
                        order_id=order_id_str,
                        side="",
                        size=filled_size or Decimal("0"),
                        price=Decimal("0"),
                        status=status,
                        filled_size=filled_size or Decimal("0"),
                        remaining_size=Decimal("0"),
                    )
                self._latest_orders[order_id_str] = updated

                return OrderResult(success=True, order_id=order_id_str, status=status, filled_size=filled_size)
            else:
                return OrderResult(success=False, error_message=result.get('msg', 'Unknown error'))

        except Exception as e:
            return OrderResult(success=False, error_message=str(e))

    @query_retry()
    async def get_order_info(self, order_id: str) -> Optional[OrderInfo]:
        """Get order information from Aster."""
        order_id_str = str(order_id)
        cached = self._latest_orders.get(order_id_str)
        if cached is not None:
            status_upper = (cached.status or "").upper()
            if status_upper in {"FILLED", "CANCELED", "REJECTED", "EXPIRED"}:
                return cached

        result = await self._make_request('GET', '/fapi/v1/order', {
            'symbol': self.config.contract_id,
            'orderId': order_id
        })

        order_type = result.get('type', '')
        if order_type == 'MARKET':
            price = self._to_decimal(result.get('avgPrice'), Decimal("0"))
        else:
            price = self._to_decimal(result.get('price'), Decimal("0"))

        if 'orderId' in result:
            size = self._to_decimal(result.get('origQty'), Decimal("0"))
            filled = self._to_decimal(result.get('executedQty'), Decimal("0"))
            remaining = None
            if size is not None and filled is not None:
                remaining = size - filled

            info = OrderInfo(
                order_id=str(result['orderId']),
                side=(result.get('side') or '').lower(),
                size=size or Decimal("0"),
                price=price or Decimal("0"),
                status=result.get('status', ''),
                filled_size=filled or Decimal("0"),
                remaining_size=remaining or Decimal("0"),
            )
            self._latest_orders[order_id_str] = info
            return info
        return cached

    @query_retry(default_return=[])
    async def get_active_orders(self, contract_id: str) -> List[OrderInfo]:
        """Get active orders for a contract from Aster."""
        result = await self._make_request('GET', '/fapi/v1/openOrders', {'symbol': contract_id})

        orders = []
        for order in result:
            order_id_str = str(order.get('orderId', ''))
            size = self._to_decimal(order.get('origQty'), Decimal("0"))
            filled = self._to_decimal(order.get('executedQty'), Decimal("0"))
            remaining = None
            if size is not None and filled is not None:
                remaining = size - filled

            info = OrderInfo(
                order_id=order_id_str,
                side=(order.get('side', '') or '').lower(),
                size=size or Decimal("0"),
                price=self._to_decimal(order.get('price'), Decimal("0")) or Decimal("0"),
                status=order.get('status', ''),
                filled_size=filled or Decimal("0"),
                remaining_size=remaining or Decimal("0"),
            )
            orders.append(info)
            if order_id_str:
                self._latest_orders[order_id_str] = info

        return orders

    @query_retry(reraise=True)
    async def get_account_positions(self) -> Decimal:
        """Get account positions from Aster."""
        result = await self._make_request('GET', '/fapi/v2/positionRisk', {'symbol': self.config.contract_id})

        for position in result:
            if position.get('symbol') == self.config.contract_id:
                position_amt = abs(Decimal(position.get('positionAmt', 0)))
                return position_amt

        return Decimal(0)

    async def get_position_snapshot(self, symbol: str) -> Optional[ExchangePositionSnapshot]:
        """
        Return the current position snapshot for a symbol.
        """
        formatted_symbol = symbol.upper()
        if not formatted_symbol.endswith("USDT"):
            formatted_symbol = get_aster_symbol_format(formatted_symbol)

        def to_decimal(value: Any) -> Optional[Decimal]:
            if value is None or value == "":
                return None
            try:
                return Decimal(str(value))
            except (InvalidOperation, TypeError, ValueError):
                return None

        try:
            result = await self._make_request('GET', '/fapi/v2/positionRisk', {'symbol': formatted_symbol})
        except Exception as exc:
            self.logger.warning(f"[ASTER] Failed to fetch position risk for {symbol}: {exc}")
            return None

        if not isinstance(result, list):
            return None

        for position in result:
            if position.get('symbol') != formatted_symbol:
                continue

            quantity = to_decimal(position.get('positionAmt')) or Decimal("0")
            entry_price = to_decimal(position.get('entryPrice'))
            mark_price = to_decimal(position.get('markPrice'))
            unrealized = to_decimal(position.get('unRealizedProfit'))
            leverage = to_decimal(position.get('leverage'))
            isolated_margin = to_decimal(position.get('isolatedMargin'))
            liquidation_price = to_decimal(position.get('liquidationPrice'))
            notional = to_decimal(position.get('notional'))

            exposure = notional.copy_abs() if notional is not None else None
            if exposure is None and mark_price is not None and quantity != 0:
                exposure = mark_price * quantity.copy_abs()

            metadata: Dict[str, Any] = {
                "margin_type": position.get('marginType'),
                "position_side": position.get('positionSide'),
            }
            if notional is not None:
                metadata["notional"] = notional

            side = "long" if quantity > 0 else "short" if quantity < 0 else None

            return ExchangePositionSnapshot(
                symbol=formatted_symbol,
                quantity=quantity,
                side=side,
                entry_price=entry_price,
                mark_price=mark_price,
                exposure_usd=exposure,
                unrealized_pnl=unrealized,
                realized_pnl=None,
                funding_accrued=None,
                margin_reserved=isolated_margin,
                leverage=leverage,
                liquidation_price=liquidation_price,
                timestamp=datetime.now(timezone.utc),
                metadata={k: v for k, v in metadata.items() if v is not None},
            )

        return None

    async def get_account_balance(self) -> Optional[Decimal]:
        """
        Get available account balance from Aster.
        
        Uses GET /fapi/v4/account endpoint to get account information.
        Returns available balance that can be used to open new positions.
        
        Returns:
            Available balance in USDT, or None if query fails
        """
        try:
            result = await self._make_request('GET', '/fapi/v4/account')
            
            # Get available balance from response
            # Can use either:
            # 1. availableBalance (top-level, total available across all assets)
            # 2. assets[].availableBalance (per-asset breakdown)
            
            available_balance = result.get('availableBalance')
            if available_balance is not None:
                balance = Decimal(str(available_balance))
                self.logger.debug(
                    f"[ASTER] Available balance: ${balance:.2f}"
                )
                return balance
            
            # Fallback: Sum available balance from assets array
            assets = result.get('assets', [])
            total_available = Decimal('0')
            for asset in assets:
                if asset.get('asset') == 'USDT':  # Primary trading asset
                    asset_available = asset.get('availableBalance', 0)
                    total_available += Decimal(str(asset_available))
            
            if total_available > 0:
                self.logger.debug(
                    f"[ASTER] Available balance (from assets): ${total_available:.2f}"
                )
                return total_available
            
            # No balance data available
            self.logger.warning("[ASTER] No balance data in account response")
            return None
            
        except Exception as e:
            self.logger.warning(f"[ASTER] Failed to get account balance: {e}")
            return None

    async def get_account_leverage(self, symbol: str) -> Optional[int]:
        """
        Get current account leverage setting for a symbol from Aster.
        
        Aster uses Binance Futures-compatible API.
        Endpoint: GET /fapi/v2/positionRisk
        
        Args:
            symbol: Trading symbol (e.g., "ZORA")
            
        Returns:
            Current leverage multiplier (e.g., 10 for 10x), or None if unavailable
        """
        try:
            normalized_symbol = f"{symbol}USDT"
            result = await self._make_request('GET', '/fapi/v2/positionRisk', {'symbol': normalized_symbol})
            
            if result and len(result) > 0:
                # positionRisk returns array, take first position
                position_info = result[0]
                leverage = int(position_info.get('leverage', 0))
                
                self.logger.debug(
                    f"📊 [ASTER] Account leverage for {symbol}: {leverage}x"
                )
                
                return leverage if leverage > 0 else None
            
            return None
        
        except Exception as e:
            self.logger.warning(f"Could not get account leverage for {symbol}: {e}")
            return None
    
    async def set_account_leverage(self, symbol: str, leverage: int) -> bool:
        """
        Set account leverage for a symbol on Aster.
        
        ⚠️ WARNING: Only call this if you want to change leverage settings!
        This is a TRADE endpoint that modifies account settings.
        
        Endpoint: POST /fapi/v1/leverage
        
        Args:
            symbol: Trading symbol (e.g., "ZORA")
            leverage: Target leverage (1 to 125)
            
        Returns:
            True if successful, False otherwise
        """
        try:
            if leverage < 1 or leverage > 125:
                self.logger.error(
                    f"[ASTER] Invalid leverage value: {leverage}. Must be between 1 and 125"
                )
                return False
            
            normalized_symbol = f"{symbol}USDT"
            
            self.logger.info(
                f"[ASTER] Setting leverage for {symbol} to {leverage}x..."
            )
            
            result = await self._make_request(
                'POST',
                '/fapi/v1/leverage',
                data={
                    'symbol': normalized_symbol,
                    'leverage': leverage
                }
            )
            
            # Response format:
            # {
            #   "leverage": 21,
            #   "maxNotionalValue": "1000000",
            #   "symbol": "BTCUSDT"
            # }
            
            if 'leverage' in result:
                actual_leverage = result.get('leverage')
                max_notional = result.get('maxNotionalValue')
                
                return True
            else:
                self.logger.warning(
                    f"[ASTER] Unexpected response when setting leverage: {result}"
                )
                return False
        
        except Exception as e:
            self.logger.error(
                f"[ASTER] Error setting leverage for {symbol} to {leverage}x: {e}"
            )
            return False
    
    async def get_leverage_info(self, symbol: str) -> Dict[str, Any]:
        """
        Get leverage and position limit information for a symbol.
        
        ⚠️ CRITICAL: Queries BOTH symbol limits AND account leverage settings.
        Aster requires account leverage to be manually set per symbol.
        
        Uses:
        - GET /fapi/v1/leverageBracket (for symbol-level max leverage)
        - GET /fapi/v2/positionRisk (for account leverage setting)
        
        Args:
            symbol: Trading symbol (e.g., "ZORA", "BTC")
            
        Returns:
            Dictionary with leverage limits:
            {
                'max_leverage': Decimal or None,  # From symbol config
                'max_notional': Decimal or None,  # From leverage brackets
                'account_leverage': int or None,  # Current account setting
                'margin_requirement': Decimal or None,
                'brackets': List or None
            }
        """
        try:
            normalized_symbol = f"{symbol}USDT"
            leverage_info = {
                'max_leverage': None,
                'max_notional': None,
                'account_leverage': None,
                'margin_requirement': None,
                'brackets': None
            }
            
            # Step 1: Get symbol leverage brackets (more efficient than exchangeInfo)
            # Endpoint: GET /fapi/v1/leverageBracket
            try:
                brackets_result = await self._make_request(
                    'GET', 
                    '/fapi/v1/leverageBracket',
                    {'symbol': normalized_symbol}
                )
                
                self.logger.debug(
                    f"[ASTER] Leverage brackets API response for {symbol}: {brackets_result}"
                )
                
                # Response format can be either:
                # 1. List: [{"symbol": "PROVEUSDT", "brackets": [...]}] (when symbol specified)
                # 2. Dict: {"symbol": "ETHUSDT", "brackets": [...]} (alternative format)
                
                symbol_data = None
                if isinstance(brackets_result, list) and len(brackets_result) > 0:
                    # Format 1: List response
                    symbol_data = brackets_result[0]
                elif isinstance(brackets_result, dict):
                    # Format 2: Dict response
                    symbol_data = brackets_result
                
                if symbol_data and 'brackets' in symbol_data:
                    brackets = symbol_data['brackets']
                    leverage_info['brackets'] = brackets
                    
                    if brackets and len(brackets) > 0:
                        # 🔍 CRITICAL: Find the MAXIMUM leverage across all brackets
                        # Bracket 1 typically has highest leverage (for smaller positions)
                        # But let's find the actual maximum to be safe
                        max_leverage_value = 0
                        max_notional_value = None
                        
                        for bracket in brackets:
                            initial_leverage = bracket.get('initialLeverage', 0)
                            if initial_leverage > max_leverage_value:
                                max_leverage_value = initial_leverage
                            
                            # Get the highest notional cap (from the last bracket)
                            notional_cap = bracket.get('notionalCap')
                            if notional_cap:
                                max_notional_value = max(
                                    max_notional_value or 0, 
                                    notional_cap
                                )
                        
                        if max_leverage_value > 0:
                            leverage_info['max_leverage'] = Decimal(str(max_leverage_value))
                        
                        if max_notional_value:
                            leverage_info['max_notional'] = Decimal(str(max_notional_value))
                    else:
                        self.logger.warning(
                            f"[ASTER] Symbol {symbol} has empty brackets array"
                        )
                else:
                    self.logger.warning(
                        f"[ASTER] Invalid leverage bracket response format for {symbol}"
                    )
                        
            except Exception as e:
                # Fallback: If leverageBracket endpoint fails, try exchangeInfo
                self.logger.debug(
                    f"[ASTER] leverageBracket endpoint failed for {symbol}, "
                    f"falling back to exchangeInfo: {e}"
                )
                
                result = await self._make_request('GET', '/fapi/v1/exchangeInfo')
                
                for symbol_info in result.get('symbols', []):
                    if symbol_info.get('symbol') == normalized_symbol:
                        # Extract max notional from filters
                        for filter_info in symbol_info.get('filters', []):
                            if filter_info.get('filterType') == 'NOTIONAL':
                                max_notional = filter_info.get('maxNotional')
                                if max_notional:
                                    leverage_info['max_notional'] = Decimal(str(max_notional))
                        
                        # Check for leverage brackets in symbol info
                        if 'leverageBrackets' in symbol_info:
                            leverage_info['brackets'] = symbol_info['leverageBrackets']
                            if leverage_info['brackets']:
                                leverage_info['max_leverage'] = Decimal(
                                    str(leverage_info['brackets'][0].get('initialLeverage', 10))
                                )
                        break
            
            # VALIDATION: Check if we got valid leverage data
            if leverage_info['max_leverage'] is None:
                self.logger.warning(
                    f"⚠️  [ASTER] Could not determine max leverage for {symbol}. "
                    f"This could indicate the symbol is not supported for leverage trading on Aster."
                )
                # Don't fail completely - use conservative fallback
                # But log clearly that this is estimated
                leverage_info['max_leverage'] = Decimal('5')  # Conservative for most Aster symbols
                leverage_info['margin_requirement'] = Decimal('0.20')  # 20% = 5x leverage
                
                self.logger.info(
                    f"📊 [ASTER] Using conservative fallback for {symbol}: 5x leverage"
                )
            
            # Step 2: Get ACTUAL account leverage setting (CRITICAL!)
            # This is what the exchange actually uses for margin calculations
            # Endpoint: GET /fapi/v2/positionRisk
            account_leverage = await self.get_account_leverage(symbol)
            
            if account_leverage and account_leverage > 0:
                leverage_info['account_leverage'] = account_leverage
                
                # Use account leverage as the effective limit
                # This is what actually determines your max position size
                effective_leverage = Decimal(str(account_leverage))
                leverage_info['margin_requirement'] = Decimal('1') / effective_leverage
                
            else:
                # No account leverage set - this will likely cause trading errors!
                self.logger.warning(
                    f"⚠️  [ASTER] No account leverage configured for {symbol}! "
                    f"You need to set leverage on Aster before trading. "
                    f"Use: POST /fapi/v1/leverage with symbol={normalized_symbol}"
                )
                # Use symbol max as fallback for margin requirement calculation
                if leverage_info['max_leverage']:
                    leverage_info['margin_requirement'] = Decimal('1') / leverage_info['max_leverage']
            
            # Log comprehensive info
            self.logger.info(
                f"📊 [ASTER] Leverage info for {symbol}:\n"
                f"  - Symbol max leverage: {leverage_info.get('max_leverage')}x\n"
                f"  - Account leverage: {leverage_info.get('account_leverage')}x\n"
                f"  - Max notional: ${leverage_info.get('max_notional')}\n"
                f"  - Margin requirement: {leverage_info.get('margin_requirement')} "
                f"({(leverage_info.get('margin_requirement', 0) * 100):.1f}%)"
            )
            
            return leverage_info
        
        except Exception as e:
            self.logger.error(f"Error getting leverage info for {symbol}: {e}")
            import traceback
            self.logger.debug(f"Traceback: {traceback.format_exc()}")
            
            # Conservative fallback
            return {
                'max_leverage': Decimal('10'),
                'max_notional': None,
                'account_leverage': None,
                'margin_requirement': Decimal('0.10'),
                'brackets': None
            }

    def get_min_order_notional(self, symbol: Optional[str]) -> Optional[Decimal]:
        """
        Return the minimum notional requirement for the given symbol if known.
        """
        if not symbol:
            return getattr(self.config, "min_order_notional", None)

        key = str(symbol).upper()
        if key in self._min_order_notional:
            return self._min_order_notional[key]

        # Try stripping common quote assets
        for suffix in ("USDT", "USD"):
            if key.endswith(suffix):
                alt = key[: -len(suffix)]
                if alt in self._min_order_notional:
                    return self._min_order_notional[alt]

        # Fallback to current config value if it matches this contract
        contract_key = str(getattr(self.config, "contract_id", "")).upper()
        if contract_key and key == contract_key:
            return getattr(self.config, "min_order_notional", None)

        ticker_key = str(getattr(self.config, "ticker", "")).upper()
        if ticker_key and key == ticker_key:
            return getattr(self.config, "min_order_notional", None)

        return None

    async def get_contract_attributes(self) -> Tuple[str, Decimal]:
        """Get contract ID and tick size for a ticker."""
        ticker = self.config.ticker
        if len(ticker) == 0:
            self.logger.error("Ticker is empty")
            raise ValueError("Ticker is empty")

        try:
            result = await self._make_request('GET', '/fapi/v1/exchangeInfo')

            # Check all symbols to find matching base asset
            found_symbol = None
            symbol_status = None
            
            # Debug: List all available symbols
            available_symbols = [s.get('symbol') for s in result['symbols'] if s.get('status') == 'TRADING']
            self.logger.debug(
                f"🔍 [ASTER] Found {len(available_symbols)} tradeable symbols. "
                f"Looking for {ticker}USDT..."
            )
            
            for symbol_info in result['symbols']:
                if (symbol_info.get('baseAsset') == ticker and
                        symbol_info.get('quoteAsset') == 'USDT'):
                    found_symbol = symbol_info
                    symbol_status = symbol_info.get('status', 'UNKNOWN')
                    
                    self.logger.debug(
                        f"🔍 [ASTER] Found {ticker}USDT with status: {symbol_status}"
                    )
                    
                    # Only accept TRADING status
                    if symbol_status == 'TRADING':
                        self.config.contract_id = symbol_info.get('symbol', '')

                        # Get tick size from filters
                        for filter_info in symbol_info.get('filters', []):
                            if filter_info.get('filterType') == 'PRICE_FILTER':
                                self.config.tick_size = Decimal(filter_info['tickSize'].strip('0'))
                                break

                        # Get LOT_SIZE filter (quantity precision)
                        min_quantity = Decimal(0)
                        step_size = Decimal('1')  # Default to whole numbers
                        min_notional: Optional[Decimal] = None
                        for filter_info in symbol_info.get('filters', []):
                            if filter_info.get('filterType') == 'LOT_SIZE':
                                min_quantity = Decimal(filter_info.get('minQty', 0))
                                step_size_str = filter_info.get('stepSize', '1')
                                step_size = Decimal(step_size_str.strip('0') if step_size_str.strip('0') else '1')
                                break
                        for filter_info in symbol_info.get('filters', []):
                            if filter_info.get('filterType') == 'MIN_NOTIONAL':
                                notional_raw = filter_info.get('notional')
                                if notional_raw is not None:
                                    try:
                                        min_notional = Decimal(str(notional_raw))
                                    except (InvalidOperation, TypeError, ValueError):
                                        min_notional = None
                                break
                        
                        # Store step_size in config for quantity rounding
                        self.config.step_size = step_size
                        
                        self.logger.debug(
                            f"📐 [ASTER] {ticker}USDT filters: "
                            f"tick_size={self.config.tick_size}, step_size={step_size}, "
                            f"min_qty={min_quantity}, min_notional={min_notional}"
                        )

                        if self.config.quantity < min_quantity:
                            self.logger.error(
                                f"Order quantity is less than min quantity: "
                                f"{self.config.quantity} < {min_quantity}"
                            )
                            raise ValueError(
                                f"Order quantity is less than min quantity: "
                                f"{self.config.quantity} < {min_quantity}"
                            )

                        if self.config.tick_size == 0:
                            self.logger.error("Failed to get tick size for ticker")
                            raise ValueError("Failed to get tick size for ticker")

                        if min_notional is not None:
                            setattr(self.config, "min_order_notional", min_notional)
                            ticker_key = ticker.upper()
                            contract_key = (self.config.contract_id or "").upper()
                            self._min_order_notional[ticker_key] = min_notional
                            if contract_key:
                                self._min_order_notional[contract_key] = min_notional

                        return self.config.contract_id, self.config.tick_size
                    else:
                        # Symbol found but not trading
                        break
            
            # Improved error message
            if found_symbol:
                self.logger.error(
                    f"Symbol {ticker}USDT exists on Aster but is not tradeable (status: {symbol_status})"
                )
                raise ValueError(
                    f"Symbol {ticker}USDT is not tradeable on Aster (status: {symbol_status})"
                )
            else:
                self.logger.error(
                    f"Symbol {ticker}USDT not found on Aster"
                )
                raise ValueError(f"Symbol {ticker}USDT not found on Aster")

        except Exception as e:
            self.logger.error(f"Error getting contract attributes: {e}")
            raise
