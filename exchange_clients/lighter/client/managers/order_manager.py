"""
Order manager module for Lighter client.

Handles order placement, cancellation, querying, and tracking.
"""

import asyncio
import time
from decimal import Decimal
from typing import Any, Dict, List, Optional

import lighter

from exchange_clients.base_models import OrderInfo, OrderResult, query_retry
from exchange_clients.lighter.client.utils.converters import build_order_info_from_payload


class LighterOrderManager:
    """
    Order manager for Lighter exchange.
    
    Handles:
    - Order placement (limit, market)
    - Order cancellation
    - Order status queries
    - Order tracking and caching
    """
    
    def __init__(
        self,
        lighter_client: Any,
        order_api: Any,
        account_api: Any,
        config: Any,
        logger: Any,
        account_index: int,
        api_key_index: int,
        latest_orders: Dict[str, OrderInfo],
        order_update_events: Dict[str, asyncio.Event],
        client_to_server_order_index: Dict[str, str],
        market_data_manager: Optional[Any] = None,
        ws_manager: Optional[Any] = None,
    ):
        """
        Initialize order manager.
        
        Args:
            lighter_client: Lighter SignerClient instance
            order_api: Lighter OrderApi instance
            account_api: Lighter AccountApi instance (for fallback order info)
            config: Trading configuration object
            logger: Logger instance
            account_index: Account index
            api_key_index: API key index
            latest_orders: Dictionary storing latest OrderInfo objects (client._latest_orders)
            order_update_events: Dictionary of events for order updates (client._order_update_events)
            client_to_server_order_index: Mapping from client to server order IDs
            market_data_manager: Optional market data manager (for BBO prices)
            ws_manager: Optional WebSocket manager (for order updates)
        """
        self.lighter_client = lighter_client
        self.order_api = order_api
        self.account_api = account_api
        self.config = config
        self.logger = logger
        self.account_index = account_index
        self.api_key_index = api_key_index
        self.latest_orders = latest_orders
        self.order_update_events = order_update_events
        self.client_to_server_order_index = client_to_server_order_index
        self.market_data = market_data_manager
        self.ws_manager = ws_manager
        
        # These will be set via set_client_references
        self._base_amount_multiplier_ref: Optional[Any] = None
        self._price_multiplier_ref: Optional[Any] = None
        self._current_order_client_id_ref: Optional[Any] = None
        self._contract_id_cache_ref: Optional[Dict[str, str]] = None
        self._market_id_cache_ref: Optional[Any] = None
        self._inactive_lookup_window_seconds: Optional[int] = None
        self._inactive_lookup_limit: Optional[int] = None
    
    def set_client_references(
        self,
        base_amount_multiplier_ref: Any,
        price_multiplier_ref: Any,
        current_order_client_id_ref: Any,
        contract_id_cache: Dict[str, str],
        market_id_cache: Any,
        inactive_lookup_window_seconds: int,
        inactive_lookup_limit: int,
    ) -> None:
        """
        Set references to client attributes that need to be updated.
        
        Args:
            base_amount_multiplier_ref: Reference to client.base_amount_multiplier
            price_multiplier_ref: Reference to client.price_multiplier
            current_order_client_id_ref: Reference to client.current_order_client_id
            contract_id_cache: Client's contract ID cache dict
            market_id_cache: Client's market ID cache
            inactive_lookup_window_seconds: Window for inactive order lookup
            inactive_lookup_limit: Limit for inactive order lookup
        """
        self._base_amount_multiplier_ref = base_amount_multiplier_ref
        self._price_multiplier_ref = price_multiplier_ref
        self._current_order_client_id_ref = current_order_client_id_ref
        self._contract_id_cache_ref = contract_id_cache
        self._market_id_cache_ref = market_id_cache
        self._inactive_lookup_window_seconds = inactive_lookup_window_seconds
        self._inactive_lookup_limit = inactive_lookup_limit
    
    @property
    def base_amount_multiplier(self) -> int:
        """Get base amount multiplier from client reference."""
        if self._base_amount_multiplier_ref is None:
            raise RuntimeError("Base amount multiplier reference not set")
        return getattr(self._base_amount_multiplier_ref, 'base_amount_multiplier', 1)
    
    @property
    def price_multiplier(self) -> int:
        """Get price multiplier from client reference."""
        if self._price_multiplier_ref is None:
            raise RuntimeError("Price multiplier reference not set")
        return getattr(self._price_multiplier_ref, 'price_multiplier', 1)
    
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
            client_order_id: Optional client order ID override
        """
        # Ensure client is initialized
        if self.lighter_client is None:
            raise ValueError("Lighter client not initialized. Call connect() first.")

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
        
        if self._current_order_client_id_ref is not None:
            setattr(self._current_order_client_id_ref, 'current_order_client_id', client_order_index)

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
            'reduce_only': reduce_only,
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
        client_order_id: Optional[int] = None,
        normalize_symbol_fn: Optional[Any] = None,
    ) -> OrderResult:
        """
        Place a market order with Lighter using official SDK.
        
        Args:
            contract_id: Market identifier
            quantity: Order quantity
            side: 'buy' or 'sell'
            reduce_only: If True, order can only reduce existing position
            client_order_id: Optional client order ID override
            normalize_symbol_fn: Function to normalize symbols
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
            
            if self._current_order_client_id_ref is not None:
                setattr(self._current_order_client_id_ref, 'current_order_client_id', client_order_index)

            # Get current market price for worst acceptable execution price
            # (this is the slippage tolerance for market orders)
            try:
                if self.market_data:
                    best_bid, best_ask = await self.market_data.fetch_bbo_prices(contract_id)
                else:
                    raise ValueError("Market data manager not available")
                    
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
                if normalize_symbol_fn is None:
                    normalize_symbol_fn = lambda s: s.upper()
                    
                normalized_symbol = normalize_symbol_fn(contract_id)
                cache_key = normalized_symbol.upper()

                cached_market_id = None
                if self._contract_id_cache_ref:
                    cached_market_id = self._contract_id_cache_ref.get(cache_key)
                
                if cached_market_id is None and self._market_id_cache_ref:
                    cached_market_id = self._market_id_cache_ref.get(cache_key)

                if cached_market_id is None:
                    current_ticker = getattr(self.config, "ticker", "")
                    if current_ticker:
                        current_cache_key = normalize_symbol_fn(current_ticker).upper()
                        if current_cache_key == cache_key:
                            cached_market_id = getattr(self.config, "contract_id", None)

                if cached_market_id is None:
                    if self.market_data:
                        market_id = await self.market_data.get_market_id_for_symbol(normalized_symbol)
                        if market_id is None:
                            raise ValueError(
                                f"Could not resolve market identifier for '{contract_id}' on Lighter"
                            )
                        cached_market_id = market_id
                    else:
                        raise ValueError(f"Market data manager not available for symbol lookup")

                market_index = int(cached_market_id)
                original_key = str(contract_id).upper()
                if self._contract_id_cache_ref:
                    self._contract_id_cache_ref[cache_key] = str(market_index)
                    self._contract_id_cache_ref[original_key] = str(market_index)
                if self._market_id_cache_ref:
                    self._market_id_cache_ref.set(cache_key, market_index)
                    self._market_id_cache_ref.set(original_key, market_index)
                    
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
                reduce_only=reduce_only
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
    
    async def cancel_order(self, order_id: str, contract_id: int) -> OrderResult:
        """
        Cancel an order with Lighter.
        
        Args:
            order_id: Order identifier (client or server order index)
            contract_id: Market contract ID
        """
        # Ensure client is initialized
        if self.lighter_client is None:
            raise ValueError("Lighter client not initialized. Call connect() first.")

        # Map client order indices to server order indices if available
        order_key = str(order_id)
        server_index = self.client_to_server_order_index.get(order_key, order_key)

        try:
            order_index_int = int(server_index)
        except (TypeError, ValueError):
            return OrderResult(success=False, error_message=f"Invalid order id: {order_id}")

        # Cancel order using official SDK
        cancel_order, tx_hash, error = await self.lighter_client.cancel_order(
            market_index=contract_id,
            order_index=order_index_int
        )

        if error is not None:
            return OrderResult(success=False, error_message=f"Cancel order error: {error}")

        if tx_hash:
            return OrderResult(success=True)
        else:
            return OrderResult(success=False, error_message='Failed to send cancellation transaction')
    
    def resolve_client_order_id(self, client_order_id: str) -> Optional[str]:
        """Resolve a client order index to the server-side order index, if known."""
        return self.client_to_server_order_index.get(str(client_order_id))
    
    def notify_order_update(self, order_key: str) -> None:
        """Unblock coroutines waiting for a specific order id."""
        if not order_key:
            return
        event = self.order_update_events.get(str(order_key))
        if event is not None and not event.is_set():
            event.set()
    
    async def await_order_update(self, order_key: str, timeout: float = 1.0) -> Optional[OrderInfo]:
        """Wait briefly for a websocket update before falling back to REST."""
        if not order_key:
            return None

        order_key_str = str(order_key)
        cached = self.latest_orders.get(order_key_str)
        if cached is not None:
            return cached

        if self.ws_manager is None:
            return None

        event = self.order_update_events.setdefault(order_key_str, asyncio.Event())
        if event.is_set():
            return self.latest_orders.get(order_key_str)

        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return self.latest_orders.get(order_key_str)
        except Exception:
            return self.latest_orders.get(order_key_str)

        return self.latest_orders.get(order_key_str)
    
    async def lookup_inactive_order(
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
        start_ts = max(0, now - (self._inactive_lookup_window_seconds or 21600))
        between = f"{start_ts}-{now}"

        try:
            response = await self.order_api.account_inactive_orders(
                account_index=self.account_index,
                limit=self._inactive_lookup_limit or 50,
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

            info = build_order_info_from_payload(order, order_id_str)
            if info is not None:
                if server_idx >= 0:
                    self.latest_orders[str(server_idx)] = info
                    self.notify_order_update(str(server_idx))
                self.latest_orders[order_id_str] = info
                self.notify_order_update(order_id_str)
                return info

        return None
    
    @query_retry(reraise=True)
    async def fetch_orders_with_retry(self, contract_id: int) -> List[Dict[str, Any]]:
        """Get orders using official SDK."""
        # Ensure client is initialized
        if self.lighter_client is None:
            raise ValueError("Lighter client not initialized. Call connect() first.")

        # Generate auth token for API call
        auth_token, error = self.lighter_client.create_auth_token_with_expiry()
        if error is not None:
            self.logger.error(f"Error creating auth token: {error}")
            raise ValueError(f"Error creating auth token: {error}")

        # Get active orders for the specific market
        orders_response = await self.order_api.account_active_orders(
            account_index=self.account_index,
            market_id=contract_id,
            auth=auth_token
        )

        if not orders_response:
            self.logger.error("Failed to get orders")
            raise ValueError("Failed to get orders")

        return orders_response.orders
    
    async def get_active_orders(self, contract_id: str) -> List[OrderInfo]:
        """Get active orders for a contract using official SDK."""
        try:
            contract_id_int = int(contract_id)
        except (ValueError, TypeError):
            contract_id_int = self.config.contract_id
            
        order_list = await self.fetch_orders_with_retry(contract_id_int)

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
                    self.client_to_server_order_index[order_id] = str(server_idx)
            elif server_idx is not None:
                server_id_str = str(server_idx)
                # Attempt to reuse an existing client index mapped to this server index
                for client_key, mapped_server in self.client_to_server_order_index.items():
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
    
    async def get_order_info(
        self,
        order_id: str,
        market_id: int,
        ticker: str,
        *,
        force_refresh: bool = False,
    ) -> Optional[OrderInfo]:
        """
        Get order information from Lighter using official SDK.
        
        Args:
            order_id: Client or server order identifier
            market_id: Market ID for the order
            ticker: Trading ticker symbol
            force_refresh: When True, bypass websocket caches and fetch fresh data via REST
        """
        try:
            order_id_str = str(order_id)

            # Check latest updates captured from WebSocket (client & server ids)
            server_order_id = self.client_to_server_order_index.get(order_id_str)

            cached_primary = self.latest_orders.get(order_id_str)
            cached_server = self.latest_orders.get(str(server_order_id)) if server_order_id else None
            cached_fallback = cached_primary or cached_server

            if not force_refresh:
                if cached_primary is not None:
                    return cached_primary

                if cached_server is not None:
                    return cached_server

                # Wait briefly for websocket state before hitting REST endpoints
                websocket_snapshot = await self.await_order_update(order_id_str)
                if websocket_snapshot is not None:
                    return websocket_snapshot

                if server_order_id:
                    server_snapshot = await self.await_order_update(str(server_order_id), timeout=0.5)
                    if server_snapshot is not None:
                        return server_snapshot

            if not self.order_api:
                self.logger.error("Order API not initialized")
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
                    return self.latest_orders.get(order_id_str)

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
                        info = build_order_info_from_payload(order, order_id)
                        self.latest_orders[order_id_str] = info
                        self.notify_order_update(order_id_str)
                        if server_idx >= 0:
                            server_key = str(server_idx)
                            self.latest_orders[server_key] = info
                            self.notify_order_update(server_key)
                        return info

            # Active lookup missing—check inactive order history for exact fills
            inactive_info = await self.lookup_inactive_order(order_id_str, int(market_id))
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
                        if position.symbol == ticker:
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
            return self.latest_orders.get(order_id_str)

