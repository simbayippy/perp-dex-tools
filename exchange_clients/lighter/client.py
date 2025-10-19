"""
Lighter exchange client implementation for trading execution.
"""

import os
import asyncio
import time
from datetime import datetime, timezone
import aiohttp
from decimal import Decimal, InvalidOperation
from typing import Dict, Any, List, Optional, Tuple

from exchange_clients.base_client import BaseExchangeClient
from exchange_clients.base_models import (
    OrderResult,
    OrderInfo,
    ExchangePositionSnapshot,
    query_retry,
    MissingCredentialsError,
    validate_credentials,
)
from exchange_clients.events import LiquidationEvent
from helpers.unified_logger import get_exchange_logger

# Import official Lighter SDK for API client
import lighter
from lighter import SignerClient, ApiClient, Configuration

# Import custom WebSocket implementation
from .websocket_manager import LighterWebSocketManager


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
        self.logger = get_exchange_logger("lighter", self.config.ticker)
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
        self._min_order_notional: Dict[str, Decimal] = {}
        self._latest_orders: Dict[str, OrderInfo] = {}

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
            self.logger.warning(
                f"❌ [LIGHTER] Symbol '{symbol}' NOT found in Lighter markets. "
                f"Available symbols: {', '.join(available_symbols[:10])}{'...' if len(available_symbols) > 10 else ''}"
            )
            return None
            
        except Exception as e:
            self.logger.error(
                f"❌ [LIGHTER] Error looking up market_id for symbol '{symbol}': {e}"
            )
            import traceback
            self.logger.error(f"Traceback: {traceback.format_exc()}")
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

                    self.logger.info(
                        f"Market config for {ticker}: ID={market_id}, "
                        f"Base multiplier={base_multiplier}, Price multiplier={price_multiplier}"
                    )
                    return market_id, base_multiplier, price_multiplier

            raise Exception(f"Ticker {ticker} not found in available markets")

        except Exception as e:
            self.logger.error(f"Error getting market config: {e}")
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

                self.logger.info("Lighter client initialized successfully")
            except Exception as e:
                self.logger.error(f"Failed to initialize Lighter client: {e}")
                raise
        return self.lighter_client

    async def connect(self) -> None:
        """Connect to Lighter."""
        try:
            # Initialize shared API client
            self.api_client = ApiClient(configuration=Configuration(host=self.base_url))

            # Initialize Lighter client
            await self._initialize_lighter_client()
            
            # Initialize API instances for order management
            self.account_api = lighter.AccountApi(self.api_client)
            self.order_api = lighter.OrderApi(self.api_client)  # ✅ Initialize order_api

            # Add market config to config for WebSocket manager
            self.config.market_index = self.config.contract_id
            self.config.account_index = self.account_index
            self.config.lighter_client = self.lighter_client

            # Initialize WebSocket manager (using custom implementation)
            self.ws_manager = LighterWebSocketManager(
                config=self.config,
                order_update_callback=self._handle_websocket_order_update,
                liquidation_callback=self._handle_liquidation_notifications,
            )

            # Set logger for WebSocket manager
            self.ws_manager.set_logger(self.logger)

            # Start WebSocket connection in background task
            # (Used for real-time price updates and order tracking, not for liquidity checks)
            asyncio.create_task(self.ws_manager.connect())
            
            # Give WebSocket a moment to start connecting
            await asyncio.sleep(1)

        except Exception as e:
            self.logger.error(f"Error connecting to Lighter: {e}")
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
            self.logger.error(f"Error during Lighter disconnect: {e}")

    def get_exchange_name(self) -> str:
        """Get the exchange name."""
        return "lighter"

    def supports_liquidation_stream(self) -> bool:
        """Lighter exposes real-time liquidation notifications."""
        return True
    
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

    def _handle_websocket_order_update(self, order_data_list: List[Dict[str, Any]]):
        """Handle order updates from WebSocket."""
        for order_data in order_data_list:
            market_index = order_data.get('market_index')
            client_order_index = order_data.get('client_order_index')
            server_order_index = order_data.get('order_index')

            if market_index is None or client_order_index is None:
                continue

            if str(market_index) != str(self.config.contract_id):
                self.logger.info(
                    f"[LIGHTER] Ignoring order update for market {market_index} "
                    f"(expected {self.config.contract_id})"
                )
                continue

            side = 'sell' if order_data['is_ask'] else 'buy'
            # Let strategy determine order type - exchange client just reports the order
            order_type = "ORDER"

            order_id = str(client_order_index)
            linked_order_index = str(server_order_index) if server_order_index is not None else "?"
            status = str(order_data.get('status', '')).upper()
            filled_size = Decimal(str(order_data.get('filled_base_amount', '0')))
            size = Decimal(str(order_data.get('initial_base_amount', '0')))
            price = Decimal(str(order_data.get('price', '0')))
            remaining_size = Decimal(str(order_data.get('remaining_base_amount', '0')))

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
                self.logger.info(
                    f"[LIGHTER] [{order_type}] [{order_id}] ({linked_order_index}) {status} "
                    f"{size} @ {price}"
                )
            else:
                self.logger.info(
                    f"[LIGHTER] [{order_type}] [{order_id}] ({linked_order_index}) {status} "
                    f"{filled_size} @ {price}"
                )

            current_order = None
            if order_data.get('client_order_index') == self.current_order_client_id or order_type == 'OPEN':
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
                self._latest_orders[order_id] = current_order
                if server_order_index is not None:
                    self._latest_orders[str(server_order_index)] = current_order

            if status in ['FILLED', 'CANCELED']:
                self.logger.log_transaction(order_id, side, filled_size, price, status)
                if current_order is None:
                    current_order = self._latest_orders.get(order_id)
                    if current_order is None:
                        current_order = OrderInfo(
                            order_id=order_id,
                            side=side,
                            size=size,
                            price=price,
                            status=status,
                            filled_size=filled_size,
                            remaining_size=remaining_size,
                            cancel_reason='unknown'
                        )
                self._latest_orders[order_id] = current_order
                if server_order_index is not None:
                    self._latest_orders[str(server_order_index)] = current_order

    async def _handle_liquidation_notifications(self, notifications: List[Dict[str, Any]]) -> None:
        """Handle liquidation notifications from the Lighter notification channel."""
        for notification in notifications:
            try:
                if notification.get("kind") != "liquidation":
                    continue

                content = notification.get("content", {})
                if not content:
                    continue

                quantity = Decimal(str(content.get("size") or "0")).copy_abs()
                if quantity <= 0:
                    continue

                price_source = content.get("avg_price") or content.get("price")
                price = Decimal(str(price_source or "0"))
                side = "sell" if content.get("is_ask") else "buy"

                raw_timestamp = content.get("timestamp")
                if raw_timestamp is not None:
                    try:
                        timestamp = datetime.fromtimestamp(int(raw_timestamp), tz=timezone.utc)
                    except (ValueError, OSError, OverflowError):
                        timestamp = datetime.now(timezone.utc)
                else:
                    timestamp = datetime.now(timezone.utc)

                metadata = {
                    "notification_id": notification.get("id"),
                    "usdc_amount": content.get("usdc_amount"),
                    "market_index": content.get("market_index"),
                    "acknowledged": notification.get("ack"),
                    "raw": notification,
                }

                event = LiquidationEvent(
                    exchange=self.get_exchange_name(),
                    symbol=self.config.ticker,
                    side=side,
                    quantity=quantity,
                    price=price,
                    timestamp=timestamp,
                    metadata=metadata,
                )
                await self.emit_liquidation_event(event)
            except (InvalidOperation, TypeError) as exc:
                self.logger.warning(
                    f"Failed to parse liquidation notification: {notification} ({exc})"
                )

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
            self.logger.error(f"❌ [LIGHTER] Failed to get BBO prices: {e}")
            raise ValueError(f"Unable to fetch BBO prices for {contract_id}: {e}")

    def _get_order_book_from_websocket(self) -> Optional[Dict[str, List[Dict[str, Decimal]]]]:
        """
        Get order book from WebSocket if available (zero latency).
        
        Returns:
            Order book dict if WebSocket is connected and has data, None otherwise
        """
        try:
            # ✅ Check if ws_manager exists first (may not exist if connect() wasn't called)
            if not hasattr(self, 'ws_manager') or not self.ws_manager:
                return None
            
            if not self.ws_manager.running:
                return None
            
            if not self.ws_manager.snapshot_loaded:
                return None
            
            
            if not self.ws_manager.snapshot_loaded:
                return None
            
            # Check if order book has data
            if not self.ws_manager.order_book["bids"] or not self.ws_manager.order_book["asks"]:
                return None
            
            # Convert WebSocket order book to standard format
            bids = [
                {'price': Decimal(str(price)), 'size': Decimal(str(size))}
                for price, size in sorted(self.ws_manager.order_book["bids"].items(), reverse=True)
            ]
            asks = [
                {'price': Decimal(str(price)), 'size': Decimal(str(size))}
                for price, size in sorted(self.ws_manager.order_book["asks"].items())
            ]
            
            self.logger.info(
                f"📡 [WEBSOCKET] Using real-time order book from WebSocket "
                f"({len(bids)} bids, {len(asks)} asks)"
            )
            
            return {
                'bids': bids,
                'asks': asks
            }
            
        except Exception as e:
            self.logger.warning(f"Failed to get order book from WebSocket: {e}")
            return None

    async def get_order_book_depth(
        self, 
        contract_id: str, 
        levels: int = 10
    ) -> Dict[str, List[Dict[str, Decimal]]]:
        """
        Get order book depth for a symbol.
        
        Tries WebSocket first (real-time, zero latency), falls back to REST API.
        
        Args:
            contract_id: Contract/symbol identifier (can be symbol or market_id)
            levels: Number of price levels to fetch (default: 10, max: 100)
            
        Returns:
            Dictionary with 'bids' and 'asks' lists of dicts with 'price' and 'size'
        """
        try:
            # 🔴 Priority 1: Try WebSocket (real-time, zero latency)
            ws_book = self._get_order_book_from_websocket()
            if ws_book:
                # Limit to requested levels
                return {
                    'bids': ws_book['bids'][:levels],
                    'asks': ws_book['asks'][:levels]
                }
            
            # 🔄 Priority 2: Fall back to REST API
            self.logger.info(
                f"📞 [REST] Fetching order book via REST API (WebSocket not available)"
            )
            # Use REST API for order book (more reliable than WebSocket for one-time queries)
            url = f"{self.base_url}/api/v1/orderBookOrders"
            
            # Lighter uses integer market_id for API calls
            # Try to convert contract_id to int first (if already an int ID)
            try:
                market_id = int(contract_id)
                self.logger.info(f"📊 [LIGHTER] Using contract_id as market_id: {market_id}")
            except (ValueError, TypeError):
                # contract_id is a symbol string - normalize it first
                normalized_symbol = self.normalize_symbol(contract_id)
                market_id = await self._get_market_id_for_symbol(normalized_symbol)
                
                if market_id is None:
                    self.logger.error(
                        f"❌ [LIGHTER] Could not find market_id for symbol '{contract_id}' on Lighter. "
                        f"Symbol may not exist on this exchange."
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
            
            self.logger.info(
                f"📊 [LIGHTER] Fetching order book: market_id={market_id}, limit={params['limit']}"
            )
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params) as response:
                    if response.status != 200:
                        response_text = await response.text()
                        self.logger.error(
                            f"❌ [LIGHTER] Failed to get order book: HTTP {response.status}, "
                            f"URL: {url}, params: {params}, response: {response_text[:300]}"
                        )
                        return {'bids': [], 'asks': []}
                    
                    data = await response.json()
                    
                    if data.get('code') != 200:
                        self.logger.error(
                            f"❌ [LIGHTER] Order book API error response: {data}"
                        )
                        return {'bids': [], 'asks': []}
                    
                    # Extract bids and asks from Lighter response
                    bids_raw = data.get('bids', [])
                    asks_raw = data.get('asks', [])
                    
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
                    
                    return {
                        'bids': bids,
                        'asks': asks
                    }

        except Exception as e:
            self.logger.error(f"❌ [LIGHTER] Error fetching order book depth for {contract_id}: {e}")
            import traceback
            self.logger.error(f"Traceback: {traceback.format_exc()}")
            # Return empty order book on error
            return {'bids': [], 'asks': []}

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

        expiry_seconds = getattr(self.config, "order_expiry_seconds", 3600)
        order_expiry_ms = int((time.time() + expiry_seconds) * 1000)

        # Create order parameters
        order_params = {
            'market_index': self.config.contract_id,
            'client_order_index': client_order_index,
            'base_amount': int(quantity * self.base_amount_multiplier),
            'price': int(price * self.price_multiplier),
            'is_ask': is_ask,
            'order_type': self.lighter_client.ORDER_TYPE_LIMIT,
            'time_in_force': self.lighter_client.ORDER_TIME_IN_FORCE_POST_ONLY,
            'reduce_only': False,
            'trigger_price': 0,
            'order_expiry': order_expiry_ms,
        }

        self.logger.info(
            f"📤 [LIGHTER] Submitting order: market={order_params.get('market_index')} "
            f"client_id={order_params.get('client_order_index')} "
            f"side={'ASK' if order_params.get('is_ask') else 'BID'} "
            f"price={order_params.get('price')} amount={order_params.get('base_amount')}"
        )

        create_order, tx_hash, error = await self.lighter_client.create_order(**order_params)

        if hasattr(create_order, "to_dict"):
            try:
                raw_payload = create_order.to_dict()
            except Exception:  # pragma: no cover - defensive
                raw_payload = repr(create_order)
        else:
            raw_payload = getattr(create_order, "__dict__", repr(create_order))

        if error is not None:
            self.logger.error(f"❌ [LIGHTER] Order submission failed: {error}")
            return OrderResult(
                success=False,
                order_id=str(client_order_index),
                error_message=f"Order creation error: {error}",
            )

        return OrderResult(success=True, order_id=str(client_order_index))

    async def get_order_price(self, side: str = '') -> Decimal:
        """Get the price of an order with Lighter using official SDK."""
        # Get current market prices
        best_bid, best_ask = await self.fetch_bbo_prices(self.config.contract_id)
        if best_bid <= 0 or best_ask <= 0 or best_bid >= best_ask:
            self.logger.error("Invalid bid/ask prices")
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
            order_id_str = str(order_id)

            # First check latest updates captured from WebSocket
            cached = self._latest_orders.get(order_id_str)
            if cached is not None:
                return cached

            if not self.order_api:
                self.logger.error("Order API not initialized")
                return None
            
            # Get market ID from config (should be set during initialization)
            market_id = getattr(self.config, 'contract_id', None)
            if market_id is None:
                self.logger.error(f"Market ID not found in config for symbol {self.config.ticker}")
                return None
            
            # Generate auth token
            auth_token, error = self.lighter_client.create_auth_token_with_expiry()
            if error:
                self.logger.error(f"Error creating auth token: {error}")
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
                self.logger.debug(f"Order {order_id} not found in active orders (might be filled): {e}")
                
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
                order_id_int = int(order_id_str)
                for order in orders_response.orders:
                    try:
                        client_idx = int(getattr(order, "client_order_index", -1))
                    except Exception:
                        client_idx = -1
                    try:
                        server_idx = int(getattr(order, "order_index", -1))
                    except Exception:
                        server_idx = -1

                    if client_idx == order_id_int or server_idx == order_id_int:
                        size = Decimal(str(getattr(order, "initial_base_amount", "0")))
                        remaining = Decimal(str(getattr(order, "remaining_base_amount", "0")))
                        filled_base = Decimal(str(getattr(order, "filled_base_amount", "0")))

                        # Some payloads only provide remaining; fall back to size - remaining
                        if filled_base <= Decimal("0") and size >= remaining:
                            filled_base = size - remaining

                        if filled_base < Decimal("0"):
                            filled_base = Decimal("0")
                        if remaining < Decimal("0"):
                            remaining = Decimal("0")

                        status_raw = str(getattr(order, "status", "")).upper()
                        if status_raw not in {"FILLED", "PARTIALLY_FILLED", "OPEN", "CANCELED"}:
                            if filled_base >= size and size > 0:
                                status_raw = "FILLED"
                            elif filled_base > 0:
                                status_raw = "PARTIALLY_FILLED"
                            elif remaining >= size:
                                status_raw = "OPEN"

                        if status_raw == "FILLED":
                            remaining = Decimal("0")
                            filled_base = size if size > 0 else filled_base
                        elif status_raw == "OPEN" and filled_base > 0:
                            status_raw = "PARTIALLY_FILLED"

                        side = "sell" if getattr(order, "is_ask", False) else "buy"
                        price = Decimal(str(getattr(order, "price", "0")))

                        info = OrderInfo(
                            order_id=order_id,
                            side=side,
                            size=size,
                            price=price,
                            status=status_raw,
                            filled_size=filled_base,
                            remaining_size=remaining,
                        )
                        self._latest_orders[order_id_str] = info
                        return info
            
            # Order not found in active orders - might be filled
            return None

        except Exception as e:
            self.logger.error(f"Error getting order info: {e}")
            import traceback
            self.logger.debug(f"Traceback: {traceback.format_exc()}")
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
            self.logger.error(f"Error creating auth token: {error}")
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
            self.logger.error("Failed to get orders")
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
            self.logger.error("Failed to get positions")
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
            self.logger.error("Ticker is empty")
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
            self.logger.error(f"Ticker '{ticker}' not found in available markets")
            self.logger.error(f"Available symbols: {', '.join(available_symbols[:10])}{'...' if len(available_symbols) > 10 else ''}")
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
            self.logger.error("Failed to get tick size")
            raise ValueError("Failed to get tick size")

        try:
            min_quote_amount = Decimal(str(order_book_details.min_quote_amount))
        except Exception as exc:
            min_quote_amount = None
            self.logger.debug(f"[LIGHTER] Unable to parse min_quote_amount for {ticker}: {exc}")

        if min_quote_amount is not None:
            normalized_symbol = self.normalize_symbol(market_info.symbol)
            self._min_order_notional[normalized_symbol] = min_quote_amount
            setattr(self.config, "min_order_notional", min_quote_amount)
            self.logger.debug(
                f"[LIGHTER] Minimum order notional for {normalized_symbol}: ${min_quote_amount}"
            )

        return self.config.contract_id, self.config.tick_size

    def get_min_order_notional(self, symbol: str) -> Optional[Decimal]:
        """
        Return the minimum quote notional required for orders on the given symbol.
        """
        normalized = self.normalize_symbol(symbol)
        return self._min_order_notional.get(normalized)

    # Account monitoring methods (Lighter-specific implementations)
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
            self.logger.error(f"Error getting account balance: {e}")
            return None

    async def _get_detailed_positions(self) -> List[Dict[str, Any]]:
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
            self.logger.error(f"Error getting detailed positions: {e}")
            return []

    async def get_position_snapshot(self, symbol: str) -> Optional[ExchangePositionSnapshot]:
        """
        Retrieve detailed metrics for a specific symbol.
        """
        try:
            positions = await self._get_detailed_positions()
        except Exception as exc:
            self.logger.warning(f"[LIGHTER] Failed to fetch positions for snapshot: {exc}")
            return None

        normalized_symbol = self.normalize_symbol(symbol).upper()

        for pos in positions:
            pos_symbol = (pos.get("symbol") or "").upper()
            if pos_symbol != normalized_symbol:
                continue
 
            raw_quantity = pos.get("position") or Decimal("0")
            try:
                quantity = Decimal(raw_quantity)
            except Exception:
                quantity = Decimal(str(raw_quantity))

            sign_indicator = pos.get("sign")
            if isinstance(sign_indicator, int) and sign_indicator != 0:
                quantity = quantity.copy_abs() * (Decimal(1) if sign_indicator > 0 else Decimal(-1))

            quantity = Decimal(quantity)
            entry_price: Optional[Decimal] = pos.get("avg_entry_price")
            exposure: Optional[Decimal] = pos.get("position_value")
            if exposure is not None:
                exposure = exposure.copy_abs()

            mark_price: Optional[Decimal] = None
            if exposure is not None and quantity != 0:
                mark_price = exposure / quantity.copy_abs()

            unrealized: Optional[Decimal] = pos.get("unrealized_pnl")
            realized: Optional[Decimal] = pos.get("realized_pnl")
            margin_reserved: Optional[Decimal] = pos.get("allocated_margin")
            liquidation_price: Optional[Decimal] = pos.get("liquidation_price")

            side = None
            if isinstance(sign_indicator, int):
                if sign_indicator > 0:
                    side = "long"
                elif sign_indicator < 0:
                    side = "short"
            if side is None:
                if quantity > 0:
                    side = "long"
                elif quantity < 0:
                    side = "short"

            metadata: Dict[str, Any] = {
                "market_id": pos.get("market_id"),
                "raw_sign": pos.get("sign"),
            }

            return ExchangePositionSnapshot(
                symbol=normalized_symbol,
                quantity=quantity,
                side=side if isinstance(side, str) else None,
                entry_price=entry_price,
                mark_price=mark_price,
                exposure_usd=exposure,
                unrealized_pnl=unrealized,
                realized_pnl=realized,
                funding_accrued=None,
                margin_reserved=margin_reserved,
                leverage=None,
                liquidation_price=liquidation_price,
                timestamp=datetime.now(timezone.utc),
                metadata={k: v for k, v in metadata.items() if v is not None},
            )

        return None

    async def get_account_pnl(self) -> Optional[Decimal]:
        """Get account P&L using Lighter SDK."""
        try:
            positions = await self._get_detailed_positions()
            total_pnl = Decimal('0')
            for pos in positions:
                total_pnl += pos['unrealized_pnl']
            return total_pnl
        except Exception as e:
            self.logger.error(f"Error getting account P&L: {e}")
            return None
    
    async def get_leverage_info(self, symbol: str) -> Dict[str, Any]:
        """
        Get leverage information for Lighter by querying market configuration.
        
        Leverages Lighter SDK's order_book_details endpoint to get margin requirements.
        
        Args:
            symbol: Trading symbol (e.g., "ZORA", "BTC")
            
        Returns:
            Dictionary with leverage limits based on margin fractions
        """
        try:
            # Initialize order API if needed
            if not hasattr(self, 'order_api') or self.order_api is None:
                self.order_api = lighter.OrderApi(self.api_client)
            
            # Normalize symbol and get market ID
            normalized_symbol = self.normalize_symbol(symbol)
            market_id = await self._get_market_id_for_symbol(normalized_symbol)
            
            if market_id is None:
                self.logger.error(
                    f"[LIGHTER] Could not find market for {symbol} - symbol may not be listed"
                )
                return {
                    'max_leverage': None,
                    'max_notional': None,
                    'account_leverage': None,
                    'margin_requirement': None,
                    'brackets': None,
                    'error': f"Symbol {symbol} not found on Lighter"
                }
            
            # Query market details
            market_details_response = await self.order_api.order_book_details(
                market_id=market_id,
                _request_timeout=10
            )
            
            if not market_details_response or not market_details_response.order_book_details:
                self.logger.error(
                    f"[LIGHTER] No market details found for {symbol} (market_id={market_id})"
                )
                return {
                    'max_leverage': None,
                    'max_notional': None,
                    'account_leverage': None,
                    'margin_requirement': None,
                    'brackets': None,
                    'error': f"No market details available for {symbol} on Lighter"
                }
            
            # Get first (and should be only) market detail
            market_detail = market_details_response.order_book_details[0]
            
            # Extract margin fractions
            # ⚠️ CRITICAL FIX: Lighter uses BASIS POINTS, not 1e18!
            # Based on SDK code: imf = int(10_000 / leverage) → 10,000 = 100%
            BASIS_POINTS_DIVISOR = Decimal('10000')  # 10,000 = 100%
            
            # min_initial_margin_fraction is the minimum margin requirement
            # Max leverage = 1 / min_initial_margin_fraction
            min_margin_fraction_int = market_detail.min_initial_margin_fraction
            min_margin_fraction = Decimal(str(min_margin_fraction_int)) / BASIS_POINTS_DIVISOR
            
            # Calculate max leverage
            if min_margin_fraction > 0:
                max_leverage = Decimal('1') / min_margin_fraction
            else:
                max_leverage = Decimal('20')  # Fallback
            
            # Also get maintenance margin for reference
            maintenance_margin_int = market_detail.maintenance_margin_fraction
            maintenance_margin = Decimal(str(maintenance_margin_int)) / BASIS_POINTS_DIVISOR
            
            # Try to get account-level leverage (current usage)
            account_leverage = None
            try:
                if self.account_api:
                    account_data = await self.account_api.account(
                        by="index", 
                        value=str(self.account_index)
                    )
                    
                    if account_data and account_data.accounts:
                        account = account_data.accounts[0]
                        
                        # Look for position in this specific market
                        for position in account.positions:
                            if position.market_id == market_id:
                                # Found position - get its leverage
                                if hasattr(position, 'initial_margin_fraction'):
                                    imf_int = position.initial_margin_fraction
                                    imf = Decimal(str(imf_int)) / BASIS_POINTS_DIVISOR
                                    if imf > 0:
                                        account_leverage = Decimal('1') / imf
                                break
                        
                        # Fallback: check account-level leverage
                        if account_leverage is None and hasattr(account, 'leverage'):
                            account_leverage = Decimal(str(account.leverage))
                            
            except Exception as e:
                self.logger.debug(f"Could not get account leverage: {e}")
            
            self.logger.info(
                f"📊 [LIGHTER] Leverage info for {symbol}:\n"
                f"  - Symbol max leverage: {max_leverage:.1f}x\n"
                f"  - Account leverage: {account_leverage}x\n"
                f"  - Max notional: None\n"
                f"  - Margin requirement: {min_margin_fraction} ({min_margin_fraction*100:.1f}%)"
            )
            
            return {
                'max_leverage': max_leverage,
                'max_notional': None,  # Lighter doesn't have explicit notional limits per se
                'account_leverage': account_leverage,
                'margin_requirement': min_margin_fraction,
                'brackets': None,  # Lighter uses fixed margin, not brackets
                'error': None  # No error - successful query
            }
        
        except Exception as e:
            self.logger.error(
                f"❌ [LIGHTER] Error getting leverage info for {symbol}: {e}"
            )
            # Return error state instead of fallback
            return {
                'max_leverage': None,
                'max_notional': None,
                'account_leverage': None,
                'margin_requirement': None,
                'brackets': None,
                'error': f"Failed to query leverage info: {str(e)}"
            }

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
            self.logger.error(f"Error getting total asset value: {e}")
            return None

    async def place_market_order(self, contract_id: str, quantity: Decimal, side: str) -> OrderResult:
        """
        Place a market order with Lighter using official SDK.
        
        Uses the dedicated create_market_order() method with avg_execution_price.
        """
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

            # Get current market price for worst acceptable execution price
            # (this is the slippage tolerance for market orders)
            try:
                best_bid, best_ask = await self.fetch_bbo_prices(contract_id)
                mid_price = (best_bid + best_ask) / 2
                
                # Set worst acceptable price with 5% slippage tolerance
                slippage_tolerance = Decimal('0.05')  # 5%
                if is_ask:  # Selling
                    # Worst case: price goes down
                    avg_execution_price = mid_price * (Decimal('1') - slippage_tolerance)
                else:  # Buying
                    # Worst case: price goes up
                    avg_execution_price = mid_price * (Decimal('1') + slippage_tolerance)
                
                # Convert to Lighter's price format (integer with multiplier)
                avg_execution_price_int = int(avg_execution_price * self.price_multiplier)
                
            except Exception as price_error:
                self.logger.error(f"Failed to get market price for market order: {price_error}")
                # Use a very permissive price as fallback (10% slippage)
                avg_execution_price_int = 0  # 0 means no limit
            
            # Convert quantity to Lighter's base amount format
            base_amount = int(quantity * self.base_amount_multiplier)
            
            self.logger.info(
                f"📤 [LIGHTER] Placing market order: "
                f"market={contract_id}, "
                f"client_id={client_order_index}, "
                f"side={'SELL' if is_ask else 'BUY'}, "
                f"base_amount={base_amount}, "
                f"avg_execution_price={avg_execution_price_int}"
            )

            # ✅ Use dedicated create_market_order method (not generic create_order)
            create_order, tx_hash, error = await self.lighter_client.create_market_order(
                market_index=int(contract_id),
                client_order_index=client_order_index,
                base_amount=base_amount,
                avg_execution_price=avg_execution_price_int,
                is_ask=is_ask,
                reduce_only=False  # Allow opening or closing positions
            )
            
            if error is not None:
                self.logger.error(f"❌ [LIGHTER] Market order failed: {error}")
                return OrderResult(
                    success=False,
                    order_id=str(client_order_index),
                    error_message=f"Market order error: {error}"
                )
            
            # Extract fill price from response if available
            fill_price = None
            if tx_hash and hasattr(tx_hash, 'code'):
                self.logger.info(
                    f"✅ [LIGHTER] Market order submitted! "
                    f"client_id={client_order_index}, "
                    f"tx_hash={tx_hash}"
                )
            else:
                self.logger.warning(
                    f"⚠️ [LIGHTER] Market order submitted but no response details available"
                )
            
            return OrderResult(
                success=True,
                order_id=str(client_order_index),
                side=side,
                size=quantity,
                price=fill_price,  # Will be None until we query order status
                status='SUBMITTED'
            )

        except Exception as e:
            self.logger.error(f"❌ [LIGHTER] Error placing market order: {e}")
            import traceback
            self.logger.error(f"Traceback: {traceback.format_exc()}")
            return OrderResult(
                success=False,
                error_message=f"Market order exception: {e}"
            )
