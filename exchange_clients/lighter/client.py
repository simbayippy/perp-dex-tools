"""
Lighter exchange client implementation for trading execution.
"""

import os
import asyncio
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Dict, Any, List, Optional, Tuple, Callable, Awaitable

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

    def __init__(
        self, 
        config: Dict[str, Any],
        api_key_private_key: Optional[str] = None,
        account_index: Optional[int] = None,
        api_key_index: Optional[int] = None,
        order_fill_callback: Optional[Callable[[str, Decimal, Decimal, Optional[int]], Awaitable[None]]] = None,
    ):
        """
        Initialize Lighter client.
        
        Args:
            config: Trading configuration dictionary
            api_key_private_key: Optional API private key (falls back to env var)
            account_index: Optional account index (falls back to env var, default 0)
            api_key_index: Optional API key index (falls back to env var, default 0)
        """
        # Set credentials BEFORE calling super().__init__() because it triggers _validate_config()
        self.api_key_private_key = api_key_private_key or os.getenv('API_KEY_PRIVATE_KEY')
        
        # Get indices: use params if provided, else env vars, else defaults
        # Always convert to int since Lighter SDK requires integers for arithmetic
        if account_index is not None:
            self.account_index = int(account_index)
        else:
            account_index_str = os.getenv('LIGHTER_ACCOUNT_INDEX', '0')
            self.account_index = int(account_index_str) if account_index_str else 0
        
        if api_key_index is not None:
            self.api_key_index = int(api_key_index)
        else:
            api_key_index_str = os.getenv('LIGHTER_API_KEY_INDEX', '0')
            self.api_key_index = int(api_key_index_str) if api_key_index_str else 0
        
        self.base_url = "https://mainnet.zklighter.elliot.ai"
        
        super().__init__(config)

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
        self._order_update_events: Dict[str, asyncio.Event] = {}
        self.order_fill_callback = order_fill_callback
        default_window = 6 * 3600
        try:
            window_value = int(getattr(config, "inactive_order_lookup_window_seconds", default_window))
        except Exception:
            window_value = default_window
        self._inactive_lookup_window_seconds = max(60, window_value)

        default_limit = 50
        try:
            limit_value = int(getattr(config, "inactive_order_lookup_limit", default_limit))
        except Exception:
            limit_value = default_limit
        self._inactive_lookup_limit = min(100, max(1, limit_value))
        self._positions_lock = asyncio.Lock()
        self._positions_ready = asyncio.Event()
        self._raw_positions: Dict[str, Dict[str, Any]] = {}
        self._client_to_server_order_index: Dict[str, str] = {}
        
        # User stats caching (real-time balance from WebSocket - 0 weight!)
        self._user_stats_lock = asyncio.Lock()
        self._user_stats_ready = asyncio.Event()
        self._user_stats: Optional[Dict[str, Any]] = None
        
        # Market ID caching (to avoid expensive order_books() calls - saves 300 weight per lookup!)
        self._market_id_cache: Dict[str, int] = {}
        self._contract_id_cache: Dict[str, str] = {}
        self._market_metadata: Dict[str, Dict[str, Any]] = {}

    def _validate_config(self) -> None:
        """Validate Lighter configuration."""
        # Validate the instance attributes (which may come from params or env)
        validate_credentials('API_KEY_PRIVATE_KEY', self.api_key_private_key)
        
        # Note: account_index and api_key_index have defaults of 0
        # which are valid values, so we don't need to validate them

    async def _get_market_id_for_symbol(self, symbol: str) -> Optional[int]:
        """
        Get Lighter market_id for a given symbol (cached to save 300 weight per lookup).
        
        ⚡ OPTIMIZATION: Caches market_id after first lookup to avoid expensive order_books() calls.
        The order_books() endpoint fetches ALL markets (300 weight), so caching is critical.
        
        Args:
            symbol: Trading symbol (e.g., 'BTC', 'ETH', 'TOSHI')
            
        Returns:
            Integer market_id, or None if not found
        """
        # Normalize symbol for cache key consistency
        cache_key = symbol.upper()
        
        # Check cache first (0 weight!)
        if cache_key in self._market_id_cache:
            self.logger.debug(f"[LIGHTER] Using cached market_id for {symbol} (saved 300 weight)")
            return self._market_id_cache[cache_key]
        
        try:
            # Convert normalized symbol to Lighter's format (e.g., "TOSHI" -> "1000TOSHI")
            from exchange_clients.lighter.common import get_lighter_symbol_format
            lighter_symbol = get_lighter_symbol_format(symbol)
            
            # Cache miss - fetch ALL markets (300 weight)
            self.logger.debug(f"[LIGHTER] Cache miss for {symbol}, fetching all markets (300 weight)")
            order_api = lighter.OrderApi(self.api_client)
            order_books = await order_api.order_books()
            
            # Collect all available symbols for better error messages
            available_symbols = []
            found_market_id = None
            
            for market in order_books.order_books:
                available_symbols.append(market.symbol)
                
                # Cache ALL markets while we have them (amortize the 300 weight cost!)
                market_cache_key = market.symbol.upper()
                self._market_id_cache[market_cache_key] = market.market_id
                
                # Try Lighter-specific format first (e.g., "1000TOSHI")
                if market.symbol.upper() == lighter_symbol.upper():
                    found_market_id = market.market_id
                # Try exact match with original symbol
                elif market.symbol == symbol:
                    found_market_id = market.market_id
                # Try case-insensitive match
                elif market.symbol.upper() == symbol.upper():
                    found_market_id = market.market_id
            
            if found_market_id is not None:
                # Cache the lookup key we used (not just the exact symbol match)
                self._market_id_cache[cache_key] = found_market_id
                self._market_id_cache[lighter_symbol.upper()] = found_market_id
                self.logger.debug(
                    f"[LIGHTER] Cached market_id={found_market_id} for {symbol} "
                    f"(and {len(available_symbols)} other markets)"
                )
                return found_market_id
            
            # Symbol not found - provide helpful error message
            self.logger.warning(
                f"❌ [LIGHTER] Symbol '{symbol}' (looking for '{lighter_symbol}') NOT found in Lighter markets. "
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
    
    def _cache_market_metadata(self, normalized_symbol: str, metadata: Dict[str, Any]) -> None:
        """
        Persist market metadata for a symbol so multi-symbol sessions reuse correct precision.
        """
        cache_key = normalized_symbol.upper()
        self._market_metadata[cache_key] = metadata
        contract_id = metadata.get("contract_id")
        if contract_id is not None:
            self._contract_id_cache[cache_key] = str(contract_id)
    
    def _apply_market_metadata(self, normalized_symbol: str) -> Optional[Tuple[Any, Decimal]]:
        """
        Load cached market metadata into the client and config.
        """
        cache_key = normalized_symbol.upper()
        metadata = self._market_metadata.get(cache_key)
        if metadata is None:
            return None
        
        base_mult = metadata.get("base_amount_multiplier")
        price_mult = metadata.get("price_multiplier")
        contract_id = metadata.get("contract_id")
        tick_size = metadata.get("tick_size")
        min_notional = metadata.get("min_notional")
        
        if base_mult is not None:
            self.base_amount_multiplier = base_mult
        if price_mult is not None:
            self.price_multiplier = price_mult
        if contract_id is not None:
            self.config.contract_id = contract_id
            self._contract_id_cache[cache_key] = str(contract_id)
        if tick_size is not None:
            setattr(self.config, "tick_size", tick_size)
        if min_notional is not None:
            self._min_order_notional[cache_key] = min_notional
            setattr(self.config, "min_order_notional", min_notional)
        
        if contract_id is not None and tick_size is not None:
            return contract_id, tick_size
        return None

    def _build_order_info_from_payload(self, order_obj: Any, order_id: str) -> Optional[OrderInfo]:
        """
        Convert Lighter order payload (active or inactive) into OrderInfo.
        """
        try:
            size = Decimal(str(getattr(order_obj, "initial_base_amount", "0")))
        except Exception:
            size = Decimal("0")

        try:
            remaining = Decimal(str(getattr(order_obj, "remaining_base_amount", "0")))
        except Exception:
            remaining = Decimal("0")

        try:
            filled_base = Decimal(str(getattr(order_obj, "filled_base_amount", "0")))
        except Exception:
            filled_base = Decimal("0")

        if filled_base <= Decimal("0") and size >= remaining:
            filled_base = size - remaining

        if filled_base < Decimal("0"):
            filled_base = Decimal("0")
        if remaining < Decimal("0"):
            remaining = Decimal("0")

        status_raw = str(getattr(order_obj, "status", "")).upper()
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

        side = "sell" if getattr(order_obj, "is_ask", False) else "buy"
        try:
            price = Decimal(str(getattr(order_obj, "price", "0")))
        except Exception:
            price = Decimal("0")

        return OrderInfo(
            order_id=str(order_id),
            side=side,
            size=size,
            price=price,
            status=status_raw,
            filled_size=filled_base,
            remaining_size=remaining,
        )

    async def _lookup_inactive_order(
        self,
        order_id_str: str,
        market_id: int,
    ) -> Optional[OrderInfo]:
        """
        Fetch historical order details from Lighter's accountInactiveOrders endpoint.
        """
        if not self.order_api:
            return None

        auth_token, error = self.lighter_client.create_auth_token_with_expiry()
        if error:
            self.logger.error(f"Error creating auth token for inactive orders: {error}")
            return None

        now = int(time.time())
        start_ts = max(0, now - self._inactive_lookup_window_seconds)
        between = f"{start_ts}-{now}"

        try:
            response = await self.order_api.account_inactive_orders(
                account_index=self.account_index,
                limit=self._inactive_lookup_limit,
                auth=auth_token,
                market_id=int(market_id),
                between_timestamps=between,
            )
        except Exception as exc:
            self.logger.warning(
                f"[LIGHTER] Failed inactive order lookup for {order_id_str}: {exc}"
            )
            return None

        if response is None or not getattr(response, "orders", None):
            return None

        order_id_int = None
        try:
            order_id_int = int(order_id_str)
        except Exception:
            pass

        for order in response.orders:
            try:
                client_idx = int(getattr(order, "client_order_index", -1))
            except Exception:
                client_idx = -1
            try:
                server_idx = int(getattr(order, "order_index", -1))
            except Exception:
                server_idx = -1

            matches_lookup = False
            if order_id_int is not None:
                matches_lookup = client_idx == order_id_int or server_idx == order_id_int
            else:
                matches_lookup = str(getattr(order, "client_order_index", "")) == order_id_str

            if not matches_lookup:
                continue

            info = self._build_order_info_from_payload(order, order_id_str)
            if info is not None:
                if server_idx >= 0:
                    self._latest_orders[str(server_idx)] = info
                    self._notify_order_update(str(server_idx))
                self._latest_orders[order_id_str] = info
                self._notify_order_update(order_id_str)
                return info

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
                
                # ⚡ OPTIMIZATION: Removed check_client() call (saves 150 weight / 250% of rate limit!)
                # API key validity will be verified on first order attempt with clear error message
                self.logger.debug("[LIGHTER] Client initialized (skipping API key validation to save rate limit)")

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
            self.config.api_client = self.api_client  # Pass api_client for market lookups

            # Initialize WebSocket manager (using custom implementation)
            self.ws_manager = LighterWebSocketManager(
                config=self.config,
                order_update_callback=self._handle_websocket_order_update,
                liquidation_callback=self.handle_liquidation_notification,
                positions_callback=self._handle_positions_stream_update,
                user_stats_callback=self._handle_user_stats_update,
            )

            # Set logger for WebSocket manager
            self.ws_manager.set_logger(self.logger)

            # Await WebSocket connection (real-time price updates and order tracking)
            await self.ws_manager.connect()
            
            # ⚡ OPTIMIZATION: Wait for WebSocket positions instead of REST call (saves 300 weight/500% of rate limit!)
            # WebSocket subscribes to 'account_all_positions' and will populate positions within 1-2 seconds
            # If positions are urgently needed, strategies can call get_position_snapshot() which will
            # fall back to REST only if WebSocket data isn't available yet
            self.logger.debug("[LIGHTER] Waiting for WebSocket positions stream (saves 300 weight REST call)")
            
            # Give WebSocket a moment to receive initial positions (usually instant)
            # If not received, strategies will trigger REST fallback only when actually needed
            try:
                await asyncio.wait_for(self._positions_ready.wait(), timeout=2.0)
                self.logger.debug("[LIGHTER] Initial positions received via WebSocket ✅")
            except asyncio.TimeoutError:
                self.logger.debug("[LIGHTER] WebSocket positions not ready yet (will use REST fallback when needed)")

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
        Normalize Lighter symbol to standard format.
        
        Uses shared normalization logic that handles:
        - "BTC" -> "BTC"
        - "1000TOSHI" -> "TOSHI" (1000-prefix removal)
        - "1000FLOKI" -> "FLOKI"
        - "-PERP" suffix removal
        
        Args:
            symbol: Lighter symbol (e.g., "BTC", "1000TOSHI", "BTC-PERP")
            
        Returns:
            Normalized symbol (base asset only, uppercase)
        """
        # Use shared implementation from common.py which handles 1000-prefix
        from exchange_clients.lighter.common import normalize_symbol as normalize_lighter_symbol
        return normalize_lighter_symbol(symbol)
    
    def get_quantity_multiplier(self, symbol: str) -> int:
        """
        Get the quantity multiplier for a symbol on Lighter.
        
        Lighter's k-prefix tokens (kTOSHI, kFLOKI, etc.) use a 1000x multiplier:
        - 1 contract unit = 1000 actual tokens
        - Price is per 1000 tokens
        
        Example: kTOSHI at $0.7655 means 1000 TOSHI tokens cost $0.7655
        
        Args:
            symbol: Normalized symbol (e.g., "TOSHI", "BTC")
            
        Returns:
            1000 for k-prefix tokens, 1 for others
        """
        from exchange_clients.lighter.common import get_quantity_multiplier
        return get_quantity_multiplier(symbol)

    def _handle_websocket_order_update(self, order_data_list: List[Dict[str, Any]]):
        """Handle order updates from WebSocket."""
        for order_data in order_data_list:
            market_index = order_data.get('market_index')
            client_order_index = order_data.get('client_order_index')
            server_order_index = order_data.get('order_index')

            if market_index is None or client_order_index is None:
                continue

            if server_order_index is not None:
                self._client_to_server_order_index[str(client_order_index)] = str(server_order_index)

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
                    self._client_to_server_order_index.pop(order_id, None)
                else:
                    self.orders_cache[order_id]['status'] = status
                    self.orders_cache[order_id]['filled_size'] = filled_size
            elif status == 'OPEN':
                self.orders_cache[order_id] = {'status': status, 'filled_size': filled_size}

            if status == 'OPEN' and filled_size > 0:
                status = 'PARTIALLY_FILLED'

            # log websocket order update
            if status == 'OPEN':
                self.logger.info(
                    f"[WEBSOCKET] [LIGHTER] {status} "
                    f"{size} @ {price}"
                )
            else:
                self.logger.info(
                    f"[WEBSOCKET] [LIGHTER] {status} "
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
                self._notify_order_update(order_id)
                if server_order_index is not None:
                    server_key = str(server_order_index)
                    self._latest_orders[server_key] = current_order
                    self._notify_order_update(server_key)

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
                self._notify_order_update(order_id)
                if server_order_index is not None:
                    server_key = str(server_order_index)
                    self._latest_orders[server_key] = current_order
                    self._notify_order_update(server_key)

                if status == 'FILLED' and self.order_fill_callback:
                    try:
                        sequence = getattr(order_data, 'offset', None)
                    except Exception:
                        sequence = None
                    asyncio.get_running_loop().create_task(
                        self.order_fill_callback(
                            order_id,
                            price,
                            filled_size,
                            sequence,
                        )
                    )

    def _notify_order_update(self, order_key: str) -> None:
        """Unblock coroutines waiting for a specific order id."""
        if not order_key:
            return
        event = self._order_update_events.get(str(order_key))
        if event is not None and not event.is_set():
            event.set()

    async def handle_liquidation_notification(self, notifications: List[Dict[str, Any]]) -> None:
        """Normalize liquidation notifications from the Lighter stream."""
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

    @query_retry(default_return=(Decimal("0"), Decimal("0")))
    async def fetch_bbo_prices(self, contract_id: str) -> Tuple[Decimal, Decimal]:
        """
        Get best bid/offer prices, preferring WebSocket data when available.
        
        Note: This method is kept for backward compatibility with legacy code
        that calls it directly. New code should use PriceProvider instead.
        
        For real-time monitoring, WebSocket data is available via ws_manager.
        For order execution, use PriceProvider which orchestrates fresh data retrieval.
        """
        # Efficient: Direct access to cached BBO from WebSocket
        if self.ws_manager and self.ws_manager.best_bid is not None and self.ws_manager.best_ask is not None:
            # self.logger.info(f"📡 [LIGHTER] Using real-time BBO from WebSocket")
            return Decimal(str(self.ws_manager.best_bid)), Decimal(str(self.ws_manager.best_ask))
        
        # DRY: Reuse existing orderbook logic for REST fallback
        self.logger.info(f"📞 [REST][LIGHTER] Using REST API fallback")
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
            if self.ws_manager:
                ws_book = self.ws_manager.get_order_book(levels)
                if ws_book:
                    self.logger.info(
                        f"📡 [LIGHTER] Using real-time order book from WebSocket "
                        f"({len(ws_book['bids'])} bids, {len(ws_book['asks'])} asks)"
                    )
                    return ws_book
            
            # 🔄 Priority 2: Fall back to REST API using SDK
            self.logger.info(
                f"📞 [REST][LIGHTER] Fetching order book via REST API (WebSocket not available)"
            )
            
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

            # API max is 100 for Lighter
            if levels < 100:
                levels = 100  # Lighter specific: use max to get full depth
            
            self.logger.info(
                f"📊 [LIGHTER] Fetching order book: market_id={market_id}, limit={levels}"
            )
            
            # Use SDK to fetch order book
            try:
                order_api = lighter.OrderApi(self.api_client)
                result = await order_api.order_book_orders(
                    market_id=market_id,
                    limit=levels,
                    _request_timeout=10,
                )

                if result.code != 200:
                    self.logger.error(
                        f"❌ [LIGHTER] Order book API error: code={result.code}, message={result.message}"
                    )
                    return {'bids': [], 'asks': []}

                bids = [
                    {
                        'price': Decimal(bid.price),
                        'size': Decimal(bid.remaining_base_amount),
                    }
                    for bid in result.bids
                ]
                asks = [
                    {
                        'price': Decimal(ask.price),
                        'size': Decimal(ask.remaining_base_amount),
                    }
                    for ask in result.asks
                ]

                return {'bids': bids, 'asks': asks}

            except Exception as api_error:
                self.logger.error(
                    f"❌ [LIGHTER] SDK order book fetch failed: {api_error}"
                )
                import traceback
                self.logger.debug(f"Traceback: {traceback.format_exc()}")
                return {'bids': [], 'asks': []}

        except Exception as e:
            self.logger.error(f"❌ [LIGHTER] Error fetching order book depth for {contract_id}: {e}")
            import traceback
            self.logger.error(f"Traceback: {traceback.format_exc()}")
            # Return empty order book on error
            return {'bids': [], 'asks': []}

    async def place_limit_order(
        self,
        contract_id: str,
        quantity: Decimal,
        price: Decimal,
        side: str,
        reduce_only: bool = False,
        client_order_id: Optional[int] = None,
    ) -> OrderResult:
        """
        Place a post only order with Lighter using official SDK.
        
        Args:
            contract_id: Market identifier
            quantity: Order quantity
            price: Limit price
            side: 'buy' or 'sell'
            reduce_only: If True, order can only reduce existing position
        """
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

        # Generate client order index (allow caller override)
        if client_order_id is not None:
            client_order_index = int(client_order_id)
        else:
            client_order_index = int(time.time() * 1000) % 1000000  # Simple unique ID
        self.current_order_client_id = client_order_index

        expiry_seconds = getattr(self.config, "order_expiry_seconds", 3600)
        order_expiry_ms = int((time.time() + expiry_seconds) * 1000)

        # Create order parameters
        order_params = {
            'market_index': self.config.contract_id,
            'client_order_index': client_order_index,
            'base_amount': round(quantity * self.base_amount_multiplier),
            'price': round(price * self.price_multiplier),
            'is_ask': is_ask,
            'order_type': self.lighter_client.ORDER_TYPE_LIMIT,
            'time_in_force': self.lighter_client.ORDER_TIME_IN_FORCE_POST_ONLY,
            'reduce_only': reduce_only,  # ✅ Use parameter (allows closing dust positions)
            'trigger_price': 0,
            'order_expiry': order_expiry_ms,
        }

        self.logger.info(
            f"📤 [LIGHTER] Submitting order: market={order_params.get('market_index')} "
            f"client_id={order_params.get('client_order_index')} "
            f"side={'ASK' if order_params.get('is_ask') else 'BID'} "
            f"price={order_params.get('price')} amount={order_params.get('base_amount')}"
        )

        # Retry only when Lighter returns its nonce-mismatch error
        nonce_retry_tokens = ("code=21104", "invalid nonce")
        max_attempts = 2
        error = None
        for attempt in range(1, max_attempts + 1):
            create_order, tx_hash, error = await self.lighter_client.create_order(**order_params)
            if error is None:
                break

            error_text = str(error)
            if attempt < max_attempts and any(token in error_text.lower() for token in nonce_retry_tokens):
                self.logger.warning(
                    f"⚠️ [LIGHTER] Nonce mismatch detected (attempt {attempt}/{max_attempts}): {error_text}. "
                    "Refreshing nonce via SDK and retrying..."
                )
                if hasattr(self.lighter_client, 'nonce_manager'):
                    try:
                        self.lighter_client.nonce_manager.hard_refresh_nonce(self.api_key_index)
                    except Exception as refresh_exc:
                        self.logger.debug(f"[LIGHTER] Failed to refresh nonce proactively: {refresh_exc}")
                await asyncio.sleep(0)
                continue

            self.logger.error(f"❌ [LIGHTER] Order submission failed: {error}")
            return OrderResult(
                success=False,
                order_id=str(client_order_index),
                error_message=f"Order creation error: {error}",
            )
        else:
            self.logger.error("❌ [LIGHTER] Order submission failed: unknown error (nonce retry exhausted)")
            return OrderResult(
                success=False,
                order_id=str(client_order_index),
                error_message="Order creation error: nonce retry exhausted",
            )

        if hasattr(create_order, "to_dict"):
            try:
                raw_payload = create_order.to_dict()
            except Exception:  # pragma: no cover - defensive
                raw_payload = repr(create_order)
        else:
            raw_payload = getattr(create_order, "__dict__", repr(create_order))

        # 🛡️ DEFENSIVE CHECK: Min notional should already be validated in pre-flight checks
        # ⚠️ SKIP for reduce_only orders (closing positions) - these may be below min notional
        if not reduce_only:
            min_notional = getattr(self.config, "min_order_notional", None)
            if min_notional is not None:
                notional = Decimal(quantity) * Decimal(price)
                if notional < min_notional:
                    self.logger.error(
                        f"[LIGHTER] UNEXPECTED: Order notional ${notional} below minimum ${min_notional}. "
                        f"This should have been caught in pre-flight checks!"
                    )
                    return OrderResult(
                        success=False,
                        order_id=str(client_order_index),
                        error_message=f"Order notional below minimum ${min_notional}",
                    )
        else:
            # Reduce-only order - allowed to be below min notional (closing dust positions)
            notional = Decimal(quantity) * Decimal(price)
            self.logger.debug(
                f"[LIGHTER] Reduce-only order: ${notional:.2f} notional "
                f"(min notional check skipped for position closing)"
            )

        # error is guaranteed to be None here (success path)

        # Convert back to Decimal for logging/consumers
        normalized_price = Decimal(order_params['price']) / self.price_multiplier
        normalized_size = Decimal(order_params['base_amount']) / self.base_amount_multiplier

        return OrderResult(
            success=True,
            order_id=str(client_order_index),
            side=side,
            size=normalized_size,
            price=normalized_price,
            status="OPEN",
            filled_size=Decimal("0"),
        )

    async def place_market_order(
        self,
        contract_id: str,
        quantity: Decimal,
        side: str,
        reduce_only: bool = False,
        client_order_id: Optional[int] = None
    ) -> OrderResult:
        """
        Place a market order with Lighter using official SDK.
        
        Args:
            contract_id: Market identifier
            quantity: Order quantity
            side: 'buy' or 'sell'
            reduce_only: If True, order can only reduce existing position
        
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

            if client_order_id is not None:
                client_order_index = int(client_order_id)
            else:
                client_order_index = int(time.time() * 1000) % 1000000
            self.current_order_client_id = client_order_index

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
                avg_execution_price_int = round(avg_execution_price * self.price_multiplier)
                
            except Exception as price_error:
                self.logger.error(f"Failed to get market price for market order: {price_error}")
                # Use a very permissive price as fallback (10% slippage)
                avg_execution_price_int = 0  # 0 means no limit
            
            # Resolve numeric market index from provided contract identifier
            try:
                market_index = int(contract_id)
            except (ValueError, TypeError):
                normalized_symbol = self.normalize_symbol(contract_id)
                cache_key = normalized_symbol.upper()

                cached_market_id = (
                    self._contract_id_cache.get(cache_key)
                    or self._market_id_cache.get(cache_key)
                )

                if cached_market_id is None:
                    current_ticker = getattr(self.config, "ticker", "")
                    if current_ticker:
                        current_cache_key = self.normalize_symbol(current_ticker).upper()
                        if current_cache_key == cache_key:
                            cached_market_id = getattr(self.config, "contract_id", None)

                if cached_market_id is None:
                    market_id = await self._get_market_id_for_symbol(normalized_symbol)
                    if market_id is None:
                        raise ValueError(
                            f"Could not resolve market identifier for '{contract_id}' on Lighter"
                        )
                    cached_market_id = market_id

                market_index = int(cached_market_id)
                original_key = str(contract_id).upper()
                self._contract_id_cache[cache_key] = str(market_index)
                self._contract_id_cache[original_key] = str(market_index)
                self._market_id_cache[cache_key] = market_index
                # Preserve original lookup key (e.g., PROVE-PERP) for future cache hits
                self._market_id_cache[original_key] = market_index
            contract_display = f"{contract_id} (id={market_index})" if str(contract_id) != str(market_index) else str(market_index)

            # Convert quantity to Lighter's base amount format
            base_amount = round(quantity * self.base_amount_multiplier)
            
            self.logger.info(
                f"📤 [LIGHTER] Placing market order: "
                f"market={contract_display}, "
                f"client_id={client_order_index}, "
                f"side={'SELL' if is_ask else 'BUY'}, "
                f"base_amount={base_amount}, "
                f"avg_execution_price={avg_execution_price_int}"
            )

            # ✅ Use dedicated create_market_order method (not generic create_order)
            create_order, tx_hash, error = await self.lighter_client.create_market_order(
                market_index=market_index,
                client_order_index=client_order_index,
                base_amount=base_amount,
                avg_execution_price=avg_execution_price_int,
                is_ask=is_ask,
                reduce_only=reduce_only  # ✅ Use parameter (allows closing dust positions)
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

    def resolve_client_order_id(self, client_order_id: str) -> Optional[str]:
        """Resolve a client order index to the server-side order index, if known."""
        return self._client_to_server_order_index.get(str(client_order_id))

    async def cancel_order(self, order_id: str) -> OrderResult:
        """Cancel an order with Lighter."""
        # Ensure client is initialized
        if self.lighter_client is None:
            await self._initialize_lighter_client()

        # Map client order indices to server order indices if available
        order_key = str(order_id)
        server_index = self._client_to_server_order_index.get(order_key, order_key)

        try:
            order_index_int = int(server_index)
        except (TypeError, ValueError):
            return OrderResult(success=False, error_message=f"Invalid order id: {order_id}")

        # Cancel order using official SDK
        cancel_order, tx_hash, error = await self.lighter_client.cancel_order(
            market_index=self.config.contract_id,
            order_index=order_index_int
        )

        if error is not None:
            return OrderResult(success=False, error_message=f"Cancel order error: {error}")

        if tx_hash:
            return OrderResult(success=True)
        else:
            return OrderResult(success=False, error_message='Failed to send cancellation transaction')

    async def _await_order_update(self, order_key: str, timeout: float = 1.0) -> Optional[OrderInfo]:
        """Wait briefly for a websocket update before falling back to REST."""
        if not order_key:
            return None

        order_key_str = str(order_key)
        cached = self._latest_orders.get(order_key_str)
        if cached is not None:
            return cached

        if self.ws_manager is None:
            return None

        event = self._order_update_events.setdefault(order_key_str, asyncio.Event())
        if event.is_set():
            return self._latest_orders.get(order_key_str)

        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return self._latest_orders.get(order_key_str)
        except Exception:
            return self._latest_orders.get(order_key_str)

        return self._latest_orders.get(order_key_str)

    async def get_order_info(self, order_id: str, *, force_refresh: bool = False) -> Optional[OrderInfo]:
        """
        Get order information from Lighter using official SDK.
        
        Note: Lighter uses order_index (int) as order_id, and we need market_id to query orders.

        Args:
            order_id: Client or server order identifier.
            force_refresh: When True, bypass websocket caches and fetch fresh data via REST.
        """
        try:
            order_id_str = str(order_id)

            # Check latest updates captured from WebSocket (client & server ids)
            server_order_id = self._client_to_server_order_index.get(order_id_str)

            cached_primary = self._latest_orders.get(order_id_str)
            cached_server = self._latest_orders.get(str(server_order_id)) if server_order_id else None
            cached_fallback = cached_primary or cached_server

            if not force_refresh:
                if cached_primary is not None:
                    return cached_primary

                if cached_server is not None:
                    return cached_server

                # Wait briefly for websocket state before hitting REST endpoints
                websocket_snapshot = await self._await_order_update(order_id_str)
                if websocket_snapshot is not None:
                    return websocket_snapshot

                if server_order_id:
                    server_snapshot = await self._await_order_update(str(server_order_id), timeout=0.5)
                    if server_snapshot is not None:
                        return server_snapshot

            if not self.order_api:
                self.logger.error("Order API not initialized")
                return cached_fallback
            
            # Get market ID from config (should be set during initialization)
            market_id = getattr(self.config, 'contract_id', None)
            if market_id is None:
                self.logger.error(f"Market ID not found in config for symbol {self.config.ticker}")
                return cached_fallback
            
            # Generate auth token
            auth_token, error = self.lighter_client.create_auth_token_with_expiry()
            if error:
                self.logger.error(f"Error creating auth token: {error}")
                return cached_fallback
            
            # Query active orders for this market
            orders_response = None
            try:
                orders_response = await self.order_api.account_active_orders(
                    account_index=self.account_index,
                    market_id=int(market_id),
                    auth=auth_token,
                    _request_timeout=10
                )
            except Exception as e:
                status = getattr(e, "status", None)
                if status == 429 or "Too Many Requests" in str(e):
                    self.logger.warning(
                        f"Rate limited while fetching order info for {order_id_str}; "
                        "falling back to cached websocket state"
                    )
                    return self._latest_orders.get(order_id_str)

                # Order might not be active anymore (filled or cancelled)
                self.logger.debug(f"Order {order_id} not found in active orders (might be filled): {e}")
                orders_response = None
        
            # Look for the specific order by order_index
            if orders_response and getattr(orders_response, "orders", None):
                order_id_int = None
                try:
                    order_id_int = int(order_id_str)
                except Exception:
                    pass
                for order in orders_response.orders:
                    try:
                        client_idx = int(getattr(order, "client_order_index", -1))
                    except Exception:
                        client_idx = -1
                    try:
                        server_idx = int(getattr(order, "order_index", -1))
                    except Exception:
                        server_idx = -1

                    matches = False
                    if order_id_int is not None:
                        matches = client_idx == order_id_int or server_idx == order_id_int
                    else:
                        matches = str(getattr(order, "client_order_index", "")) == order_id_str

                    if matches:
                        info = self._build_order_info_from_payload(order, order_id)
                        self._latest_orders[order_id_str] = info
                        self._notify_order_update(order_id_str)
                        if server_idx >= 0:
                            server_key = str(server_idx)
                            self._latest_orders[server_key] = info
                            self._notify_order_update(server_key)
                        return info

            # Active lookup missing—check inactive order history for exact fills
            inactive_info = await self._lookup_inactive_order(order_id_str, int(market_id))
            if inactive_info is not None:
                return inactive_info

            # Fall back to position snapshot as last resort
            if self.account_api:
                account_data = await self.account_api.account(
                    by="index",
                    value=str(self.account_index),
                )
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
            
            # Order not found in active orders - might be filled
            return cached_fallback

        except Exception as e:
            self.logger.error(f"Error getting order info: {e}")
            import traceback
            self.logger.debug(f"Traceback: {traceback.format_exc()}")
            return self._latest_orders.get(order_id_str)

    async def _handle_user_stats_update(self, payload: Dict[str, Any]) -> None:
        """
        Process user stats update from WebSocket (includes real-time balance).
        
        ⚡ OPTIMIZATION: WebSocket balance updates are FREE (0 weight) vs 300 weight REST call!
        """
        stats = payload.get("stats")
        if not stats:
            return
        
        # Check if this is first update (before setting the flag)
        is_first_update = not self._user_stats_ready.is_set()
        
        async with self._user_stats_lock:
            self._user_stats = stats
            self._user_stats_ready.set()
        
        # Log first balance update for visibility
        if is_first_update:
            available_balance = stats.get("available_balance", "N/A")
            self.logger.debug(
                f"[LIGHTER] Received user stats via WebSocket: balance={available_balance} (0 weight)"
            )
    
    async def _handle_positions_stream_update(self, payload: Dict[str, Any]) -> None:
        """Process account positions update received from websocket stream."""
        positions_map = payload.get("positions") or {}
        if not isinstance(positions_map, dict):
            return

        updates: Dict[str, Dict[str, Any]] = {}

        for market_idx, raw_position in positions_map.items():
            if raw_position is None:
                continue

            position_dict = dict(raw_position)
            market_id = position_dict.get("market_id")
            if market_id is None:
                try:
                    market_id = int(market_idx)
                    position_dict["market_id"] = market_id
                except (TypeError, ValueError):
                    position_dict["market_id"] = market_idx

            symbol_raw = position_dict.get("symbol")
            if not symbol_raw:
                if market_id == getattr(self.config, "contract_id", None):
                    symbol_raw = getattr(self.config, "ticker", None)
            if not symbol_raw:
                symbol_raw = str(market_idx)

            position_dict["symbol"] = symbol_raw
            
            # WebSocket provides "total_funding_paid_out", but _snapshot_from_cache() expects "funding_accrued"
            # Note: total_funding_paid_out is optional (omitted when empty/None)
            if "total_funding_paid_out" in position_dict and position_dict["total_funding_paid_out"] is not None:
                position_dict["funding_accrued"] = position_dict["total_funding_paid_out"]
            
            normalized_symbol = self.normalize_symbol(str(symbol_raw)).upper()
            updates[normalized_symbol] = position_dict

        async with self._positions_lock:
            self._raw_positions = updates
            self._positions_ready.set()

    def _decimal_or_none(self, value: Any) -> Optional[Decimal]:
        """Convert raw numeric values to Decimal when possible."""
        if value is None:
            return None
        if isinstance(value, Decimal):
            return value
        try:
            return Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            return None

    def _build_snapshot_from_raw(self, normalized_symbol: str, raw: Dict[str, Any]) -> Optional[ExchangePositionSnapshot]:
        """Construct an ExchangePositionSnapshot from cached raw position data."""
        if raw is None:
            return None

        raw_quantity = raw.get("position") or raw.get("quantity") or Decimal("0")
        try:
            quantity = Decimal(raw_quantity)
        except Exception:
            quantity = self._decimal_or_none(raw_quantity) or Decimal("0")

        sign_indicator = raw.get("sign")
        if isinstance(sign_indicator, int) and sign_indicator != 0:
            quantity = quantity.copy_abs() * (Decimal(1) if sign_indicator > 0 else Decimal(-1))

        entry_price = self._decimal_or_none(raw.get("avg_entry_price"))
        exposure = self._decimal_or_none(raw.get("position_value"))
        if exposure is not None:
            exposure = exposure.copy_abs()

        mark_price = self._decimal_or_none(raw.get("mark_price"))
        if mark_price is None and exposure is not None and quantity != 0:
            mark_price = exposure / quantity.copy_abs()

        unrealized = self._decimal_or_none(raw.get("unrealized_pnl"))
        realized = self._decimal_or_none(raw.get("realized_pnl"))
        margin_reserved = self._decimal_or_none(raw.get("allocated_margin"))
        liquidation_price = self._decimal_or_none(raw.get("liquidation_price"))

        side: Optional[str] = None
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

        snapshot = ExchangePositionSnapshot(
            symbol=normalized_symbol,
            quantity=quantity,
            side=side,
            entry_price=entry_price,
            mark_price=mark_price,
            exposure_usd=exposure,
            unrealized_pnl=unrealized,
            realized_pnl=realized,
            funding_accrued=self._decimal_or_none(raw.get("funding_accrued")),
            margin_reserved=margin_reserved,
            leverage=None,
            liquidation_price=liquidation_price,
            timestamp=datetime.now(timezone.utc),
            metadata={
                "market_id": raw.get("market_id"),
                "raw_sign": raw.get("sign"),
            },
        )

        return snapshot

    def _get_live_mark_price(self, normalized_symbol: str) -> Optional[Decimal]:
        """
        Return a real-time mark price using the active order-book feed.

        Lighter's account positions stream is event-driven, so the cached mark price only
        changes when the exchange pushes a fresh position update. We reuse the best bid/ask
        tracked by the WebSocket to keep the mark current without hitting the heavy REST
        endpoint.
        """
        ws_manager = getattr(self, "ws_manager", None)
        if ws_manager is None:
            self.logger.warning("[LIGHTER] Skipping live mark enrichment – websocket manager unavailable")
            return None

        config_symbol = getattr(self.config, "ticker", None)
        if config_symbol:
            active_symbol = self.normalize_symbol(str(config_symbol)).upper()
            if normalized_symbol != active_symbol:
                return None

        midpoint_candidates: List[Decimal] = []
        for price in (ws_manager.best_bid, ws_manager.best_ask):
            if price is None:
                continue
            try:
                midpoint_candidates.append(Decimal(str(price)))
            except (InvalidOperation, TypeError, ValueError):
                continue

        if not midpoint_candidates:
            return None

        if len(midpoint_candidates) == 2:
            return (midpoint_candidates[0] + midpoint_candidates[1]) / Decimal("2")

        return midpoint_candidates[0]

    async def _enrich_snapshot_with_live_market_data(
        self,
        normalized_symbol: str,
        raw: Dict[str, Any],
        snapshot: ExchangePositionSnapshot,
    ) -> None:
        """
        Refresh mark price, exposure, and unrealized PnL using the latest order-book data.
        """
        live_mark = self._get_live_mark_price(normalized_symbol)
        if live_mark is None:
            return

        snapshot.mark_price = live_mark

        quantity = snapshot.quantity or Decimal("0")
        quantity_abs = quantity.copy_abs()
        if quantity_abs != 0:
            snapshot.exposure_usd = quantity_abs * live_mark

        entry_price = snapshot.entry_price
        if entry_price is not None and quantity != 0:
            try:
                snapshot.unrealized_pnl = (live_mark - entry_price) * quantity
            except Exception:
                pass

        async with self._positions_lock:
            cached = self._raw_positions.get(normalized_symbol)
            if cached is None:
                return

            cached["mark_price"] = str(live_mark)
            if snapshot.exposure_usd is not None:
                cached["position_value"] = str(snapshot.exposure_usd)
            if snapshot.unrealized_pnl is not None:
                cached["unrealized_pnl"] = str(snapshot.unrealized_pnl)

    async def _snapshot_from_cache(self, normalized_symbol: str) -> Optional[ExchangePositionSnapshot]:
        """Retrieve a cached snapshot if available, optionally enriching with funding data."""
        async with self._positions_lock:
            raw = self._raw_positions.get(normalized_symbol)
            needs_funding = False
            if raw is not None:
                # raw from websocket data
                needs_funding = raw.get("funding_accrued") is None

        if raw is None:
            return None

        snapshot = self._build_snapshot_from_raw(normalized_symbol, raw)
        if snapshot is None:
            return None

        await self._enrich_snapshot_with_live_market_data(normalized_symbol, raw, snapshot)

        if (
            needs_funding # ⚠️ Only True if funding_accrued is None
            and snapshot.side
            and snapshot.quantity != 0
            and raw.get("market_id") is not None
        ):
            try:
                funding = await self._get_cumulative_funding(
                    raw.get("market_id"),
                    snapshot.side,
                    quantity=snapshot.quantity,
                )
            except Exception as exc:
                self.logger.debug(f"[LIGHTER] Funding lookup failed for {normalized_symbol}: {exc}")
                funding = None

            snapshot.funding_accrued = funding
            if funding is not None:
                async with self._positions_lock:
                    cached = self._raw_positions.get(normalized_symbol)
                    if cached is not None:
                        cached["funding_accrued"] = funding
        else:
            snapshot.funding_accrued = snapshot.funding_accrued or self._decimal_or_none(raw.get("funding_accrued"))

        return snapshot

    async def _refresh_positions_via_rest(self) -> None:
        """Refresh cached positions via REST as a fallback."""
        try:
            self.logger.debug("[LIGHTER] Refreshing positions via REST fallback")
            positions = await self._get_detailed_positions()
        except Exception as exc:
            self.logger.warning(f"[LIGHTER] Failed to refresh positions via REST: {exc}")
            return

        updates: Dict[str, Dict[str, Any]] = {}
        for pos in positions:
            if pos is None:
                continue
            position_dict = dict(pos)
            symbol_raw = position_dict.get("symbol") or getattr(self.config, "ticker", None)
            if not symbol_raw and position_dict.get("market_id") is not None:
                symbol_raw = str(position_dict["market_id"])
            if not symbol_raw:
                continue
            normalized_symbol = self.normalize_symbol(str(symbol_raw)).upper()
            updates[normalized_symbol] = position_dict

        async with self._positions_lock:
            self._raw_positions = updates
            self._positions_ready.set()

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
            market_idx = getattr(order, "market_id", None)
            if market_idx is None:
                market_idx = getattr(order, "market_index", None)
            if market_idx is not None and str(market_idx) != str(contract_id):
                continue

            client_idx = getattr(order, "client_order_index", None)
            server_idx = getattr(order, "order_index", None)
            order_id = None
            if client_idx not in (None, 0):
                order_id = str(client_idx)
                if server_idx is not None:
                    self._client_to_server_order_index[order_id] = str(server_idx)
            elif server_idx is not None:
                server_id_str = str(server_idx)
                # Attempt to reuse an existing client index mapped to this server index
                for client_key, mapped_server in self._client_to_server_order_index.items():
                    if mapped_server == server_id_str:
                        order_id = client_key
                        break
                else:
                    order_id = server_id_str

            if order_id is None:
                continue

            # Convert Lighter Order to OrderInfo
            side = "sell" if order.is_ask else "buy"
            size = Decimal(str(order.initial_base_amount))
            remaining = Decimal(str(order.remaining_base_amount))
            price = Decimal(str(order.price))
            filled = Decimal(str(order.filled_base_amount))

            if size <= 0:
                continue

            contract_orders.append(OrderInfo(
                order_id=order_id,
                side=side,
                size=size,
                price=price,
                status=str(order.status).upper(),
                filled_size=filled,
                remaining_size=remaining,
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
        """
        Get account positions (WebSocket-first for rate limit efficiency).
        
        ⚡ OPTIMIZATION: Uses WebSocket cached positions to save 300 weight REST call per query!
        Grid strategy calls this every cycle (~40s), so this saves 450 weight/min (7.5x rate limit).
        """
        # Try WebSocket cached positions first (zero weight!)
        ticker = getattr(self.config, "ticker", None)
        if ticker:
            normalized_symbol = self.normalize_symbol(ticker).upper()
            async with self._positions_lock:
                raw = self._raw_positions.get(normalized_symbol)
            
            if raw is not None:
                quantity = raw.get("position") or raw.get("quantity") or Decimal("0")
                try:
                    return Decimal(str(quantity))
                except Exception:
                    pass
        
        # Wait briefly for WebSocket data if not ready yet
        try:
            await asyncio.wait_for(self._positions_ready.wait(), timeout=0.5)
            # Try cache again after waiting
            if ticker:
                normalized_symbol = self.normalize_symbol(ticker).upper()
                async with self._positions_lock:
                    raw = self._raw_positions.get(normalized_symbol)
                if raw is not None:
                    quantity = raw.get("position") or raw.get("quantity") or Decimal("0")
                    try:
                        return Decimal(str(quantity))
                    except Exception:
                        pass
        except asyncio.TimeoutError:
            pass
        
        # Fall back to REST only if WebSocket data not available (300 weight)
        self.logger.info("[LIGHTER] get_account_positions WebSocket positions not available, using REST fallback (300 weight)")
        positions = await self._fetch_positions_with_retry()

        # Find position for current market
        for position in positions:
            if position.market_id == self.config.contract_id:
                return Decimal(position.position)

        return Decimal(0)

    async def get_contract_attributes(self) -> Tuple[str, Decimal]:
        """Get contract ID for a ticker."""
        ticker = getattr(self.config, "ticker", "")
        if not ticker:
            self.logger.error("Ticker is empty")
            raise ValueError("Ticker is empty")

        normalized_ticker = self.normalize_symbol(ticker)
        cached_metadata = self._apply_market_metadata(normalized_ticker)
        if cached_metadata is not None:
            contract_id, tick_size = cached_metadata
            return contract_id, tick_size

        # Convert normalized ticker to Lighter's format (e.g., "TOSHI" -> "1000TOSHI")
        from exchange_clients.lighter.common import get_lighter_symbol_format
        lighter_symbol = get_lighter_symbol_format(ticker)
        
        self.logger.debug(
            f"[LIGHTER] Looking for ticker '{ticker}' as '{lighter_symbol}' in Lighter markets"
        )
        
        order_api = lighter.OrderApi(self.api_client)
        # Get all order books to find the market for our ticker
        order_books = await order_api.order_books()

        # Find the market that matches our ticker
        market_info = None
        available_symbols = []
        
        for market in order_books.order_books:
            available_symbols.append(market.symbol)
            # Try Lighter-specific format first (e.g., "1000TOSHI")
            if market.symbol.upper() == lighter_symbol.upper():
                market_info = market
                break
            # Try exact match with original ticker
            elif market.symbol == ticker:
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
        market_id_value = market_info.market_id
        self.config.contract_id = market_id_value
        
        # Cache contract_id for this symbol (multi-symbol trading support)
        # Use normalized symbol as key for consistency
        normalized_ticker = self.normalize_symbol(ticker)
        cache_key = normalized_ticker.upper()
        self._contract_id_cache[cache_key] = str(market_id_value)
        
        base_amount_multiplier = pow(10, market_info.supported_size_decimals)
        price_multiplier = pow(10, market_info.supported_price_decimals)
        self.base_amount_multiplier = base_amount_multiplier
        self.price_multiplier = price_multiplier

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

        metadata = {
            "symbol": market_info.symbol,
            "normalized_symbol": normalized_ticker,
            "contract_id": market_id_value,
            "base_amount_multiplier": base_amount_multiplier,
            "price_multiplier": price_multiplier,
            "tick_size": getattr(self.config, "tick_size", None),
            "min_notional": min_quote_amount,
        }
        self._cache_market_metadata(normalized_ticker, metadata)

        return self.config.contract_id, self.config.tick_size

    def get_min_order_notional(self, symbol: str) -> Optional[Decimal]:
        """
        Return the minimum quote notional required for orders on the given symbol.
        """
        normalized = self.normalize_symbol(symbol)
        return self._min_order_notional.get(normalized)

    # Account monitoring methods (Lighter-specific implementations)
    async def get_account_balance(self) -> Optional[Decimal]:
        """
        Get current account balance (WebSocket-first for 0 weight).
        
        ⚡ OPTIMIZATION: Uses WebSocket user_stats stream to save 300 weight REST call!
        The user_stats WebSocket provides real-time balance updates for FREE.
        """
        try:
            # Try WebSocket user stats first (0 weight!)
            async with self._user_stats_lock:
                if self._user_stats is not None:
                    available_balance = self._user_stats.get("available_balance")
                    if available_balance is not None:
                        try:
                            return Decimal(str(available_balance))
                        except Exception:
                            pass
            
            # Wait briefly for WebSocket data if not ready yet
            try:
                await asyncio.wait_for(self._user_stats_ready.wait(), timeout=0.5)
                # Try again after waiting
                async with self._user_stats_lock:
                    if self._user_stats is not None:
                        available_balance = self._user_stats.get("available_balance")
                        if available_balance is not None:
                            try:
                                return Decimal(str(available_balance))
                            except Exception:
                                pass
            except asyncio.TimeoutError:
                pass
            
            # Fall back to REST only if WebSocket not available (300 weight)
            if not self.account_api:
                return None
            
            self.logger.info("[LIGHTER] get_account_balance WebSocket user_stats not available, using REST fallback (300 weight)")
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
                    pos_dict = {
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
                    }
                    
                    # Map funding field (same as WebSocket) to avoid REST funding lookup
                    # Note: total_funding_paid_out is optional (omitted when empty/None)
                    if hasattr(pos, 'total_funding_paid_out') and pos.total_funding_paid_out is not None:
                        pos_dict['funding_accrued'] = Decimal(pos.total_funding_paid_out)
                    
                    positions.append(pos_dict)
                return positions
            return []
        except Exception as e:
            self.logger.error(f"Error getting detailed positions: {e}")
            return []

    async def _get_position_open_time(self, market_id: int, current_quantity: Decimal) -> Optional[int]:
        """
        Estimate when the current position was opened by analyzing recent trade history.
        
        Args:
            market_id: The market ID for the position
            current_quantity: Current position quantity (to correlate with trades)
            
        Returns:
            Timestamp (seconds) when position was likely opened, or None if unknown
        """
        try:
            # Generate auth token for API call
            if not hasattr(self, 'lighter_client') or self.lighter_client is None:
                return None
            
            auth_token, error = self.lighter_client.create_auth_token_with_expiry()
            if error:
                self.logger.debug(f"[LIGHTER] Error creating auth token for trade history: {error}")
                return None
            
            account_index = getattr(self.config, "account_index", None)
            if account_index is None:
                return None
            
            # Fetch recent trades for this account and market using OrderApi
            # ✅ Correct API: order_api.trades() with account_index filter
            if not hasattr(self, 'order_api') or self.order_api is None:
                self.logger.debug("[LIGHTER] OrderApi not available for trade history")
                return None
            
            trades_response = await self.order_api.trades(
                account_index=account_index,
                market_id=market_id,
                sort_by='timestamp',  # Sort by time
                sort_dir='desc',  # Descending (newest first) 
                limit=100,  # Last 100 trades should cover most position opens
                auth=auth_token,
                authorization=auth_token,
                _request_timeout=10,
            )
            
            if not trades_response or not hasattr(trades_response, 'trades'):
                return None
            
            trades = trades_response.trades
            if not trades:
                return None
            
            # Reverse to get chronological order (oldest first)
            trades = list(reversed(trades))
            
            # Track running position to find when current position started
            running_qty = Decimal("0")
            position_start_time = None
            
            for trade in trades:
                try:
                    # Lighter trades have: size, ask_account_id, bid_account_id, timestamp
                    trade_size = Decimal(str(getattr(trade, 'size', '0')))
                    ask_account_id = getattr(trade, 'ask_account_id', None)
                    bid_account_id = getattr(trade, 'bid_account_id', None)
                    
                    # Determine if this account was on the ask (sell) or bid (buy) side
                    # Note: account_id is different from account_index, but for tracking position
                    # we need to check which side matches our account
                    # Since we filtered by account_index in the API call, all trades belong to us
                    # We need to check if we were the maker or taker, and which side
                    
                    # For position tracking: if bid_account_id matches our account, we bought (+)
                    # if ask_account_id matches our account, we sold (-)
                    # Since the API already filtered by account_index, we can infer from the IDs
                    
                    # Simplified: Check position_size_before fields to understand direction
                    taker_size_before = Decimal(str(getattr(trade, 'taker_position_size_before', '0')))
                    maker_size_before = Decimal(str(getattr(trade, 'maker_position_size_before', '0')))
                    
                    # If we have position data, use it to track running quantity
                    # Otherwise, fall back to inferring from ask/bid account IDs
                    # For now, use the simple heuristic: track cumulative trades
                    # and detect position direction changes
                    
                    # Get timestamp (Lighter uses seconds for timestamp)
                    trade_timestamp = getattr(trade, 'timestamp', None)
                    
                    # Since API filtered by account_index, we need to determine our side
                    # Check is_maker_ask to understand the trade structure
                    is_maker_ask = getattr(trade, 'is_maker_ask', False)
                    
                    # Determine trade direction for our account
                    # Need more context - let's just track when position changes sign
                    # by checking the taker/maker position_sign_changed fields
                    taker_sign_changed = getattr(trade, 'taker_position_sign_changed', False)
                    maker_sign_changed = getattr(trade, 'maker_position_sign_changed', False)
                    
                    # If position sign changed, this marks a new position
                    if taker_sign_changed or maker_sign_changed:
                        position_start_time = trade_timestamp
                    
                except Exception as exc:
                    self.logger.debug(f"[LIGHTER] Error processing trade: {exc}")
                    continue
            
            return position_start_time
            
        except Exception as exc:
            self.logger.debug(
                f"[LIGHTER] Failed to determine position open time for market_id={market_id}: {exc}"
            )
            return None

    async def _get_cumulative_funding(
        self,
        market_id: int,
        side: Optional[str] = None,
        quantity: Optional[Decimal] = None,
    ) -> Optional[Decimal]:
        """
        Fetch cumulative funding fees for the CURRENT position only (not historical positions).
        
        Args:
            market_id: The market ID for the position
            side: Position side ('long' or 'short'), optional filter
            quantity: Current position quantity (used to determine when position was opened)
            
        Returns:
            Cumulative funding fees as Decimal, None if unavailable
        """
        if not hasattr(self, 'account_api') or self.account_api is None:
            return None
        
        account_index = getattr(self.config, "account_index", None)
        if account_index is None:
            self.logger.debug("[LIGHTER] No account_index configured, cannot fetch funding")
            return None
        
        # Generate auth token for API call
        if not hasattr(self, 'lighter_client') or self.lighter_client is None:
            self.logger.debug("[LIGHTER] No lighter_client available for auth")
            return None
        
        try:
            auth_token, error = self.lighter_client.create_auth_token_with_expiry()
            if error:
                self.logger.debug(f"[LIGHTER] Error creating auth token for funding: {error}")
                return None
        except Exception as exc:
            self.logger.debug(f"[LIGHTER] Failed to create auth token: {exc}")
            return None
        
        # Try to determine when the current position was opened
        position_start_time = None
        if quantity is not None and quantity != Decimal("0"):
            position_start_time = await self._get_position_open_time(market_id, quantity)
            if position_start_time:
                self.logger.debug(
                    f"[LIGHTER] Filtering funding to only after position opened at timestamp {position_start_time}"
                )
        
        try:
            # Fetch position funding history with authentication
            # Lighter requires BOTH auth (query param) and authorization (header) for main accounts
            response = await self.account_api.position_funding(
                account_index=account_index,
                market_id=market_id,
                limit=100,  # Get recent funding payments
                side=side if side else 'all',
                auth=auth_token,  # Query parameter
                authorization=auth_token,  # Header parameter - required for main accounts
                _request_timeout=10,
            )
            
            if not response or not hasattr(response, 'position_fundings'):
                return None
            
            fundings = response.position_fundings
            if not fundings:
                return Decimal("0")  # No funding yet for this position
            
            # Sum up funding 'change' values for this position only
            cumulative = Decimal("0")
            for funding in fundings:
                try:
                    # If we have position start time, only count funding after position opened
                    if position_start_time:
                        funding_timestamp = getattr(funding, 'timestamp', None)
                        if funding_timestamp and funding_timestamp < position_start_time:
                            continue
                    
                    change = Decimal(str(funding.change))
                    cumulative += change
                except Exception as exc:
                    self.logger.debug(f"[LIGHTER] Failed to parse funding change: {exc}")
                    continue
            
            self.logger.debug(
                f"[LIGHTER] Funding for current position (market_id={market_id}): ${cumulative:.4f} "
                f"(from {len(fundings)} records{' after position opened' if position_start_time else ' (all history)'})"
            )
            
            return cumulative if cumulative != Decimal("0") else None
            
        except Exception as exc:
            self.logger.debug(
                f"[LIGHTER] Error fetching funding for market_id={market_id}: {exc}"
            )
            return None

    async def get_position_snapshot(self, symbol: str) -> Optional[ExchangePositionSnapshot]:
        """Return the latest cached position snapshot for a symbol, falling back to REST if required."""
        normalized_symbol = self.normalize_symbol(symbol).upper()

        snapshot = await self._snapshot_from_cache(normalized_symbol)
        if snapshot:
            return snapshot

        try:
            await asyncio.wait_for(self._positions_ready.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            pass

        snapshot = await self._snapshot_from_cache(normalized_symbol)
        if snapshot:
            return snapshot

        await self._refresh_positions_via_rest()
        return await self._snapshot_from_cache(normalized_symbol)

    async def get_account_pnl(self) -> Optional[Decimal]:
        """Get account P&L using Lighter SDK."""
        async with self._positions_lock:
            raw_positions = list(self._raw_positions.values())

        if not raw_positions:
            await self._refresh_positions_via_rest()
            async with self._positions_lock:
                raw_positions = list(self._raw_positions.values())

        total_pnl = Decimal("0")
        for raw in raw_positions:
            unrealized = raw.get("unrealized_pnl")
            value = self._decimal_or_none(unrealized)
            if value is not None:
                total_pnl += value

        return total_pnl
    
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
