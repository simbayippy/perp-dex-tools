"""
Backpack exchange client implementation for trading execution.
"""

import os
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional

from bpx.public import Public
from bpx.account import Account

from exchange_clients.base_client import BaseExchangeClient, OrderFillCallback
from exchange_clients.base_models import (
    ExchangePositionSnapshot,
    MissingCredentialsError,
    OrderInfo,
    OrderResult,
    TradeData,
    validate_credentials,
)
from exchange_clients.backpack.common import (
    get_backpack_symbol_format,
    normalize_symbol as normalize_backpack_symbol,
)
from exchange_clients.backpack.websocket import BackpackWebSocketManager
from helpers.unified_logger import get_exchange_logger

from .utils import (
    to_decimal,
    ensure_exchange_symbol,
    quantize_to_tick,
    enforce_max_decimals,
    SymbolPrecisionCache,
    MarketSymbolMapCache,
)
from .managers.market_data import BackpackMarketData
from .managers.order_manager import BackpackOrderManager
from .managers.position_manager import BackpackPositionManager
from .managers.account_manager import BackpackAccountManager
from .managers.websocket_handlers import BackpackWebSocketHandlers


class BackpackClient(BaseExchangeClient):
    """Backpack exchange client implementation."""

    # Default maximum decimal places (fallback if we can't infer from market data)
    MAX_PRICE_DECIMALS = 3

    def __init__(
        self, 
        config: Dict[str, Any],
        public_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        order_fill_callback: OrderFillCallback = None,
    ):
        """
        Initialize Backpack client.
        
        Args:
            config: Trading configuration dictionary
            public_key: Optional public key (falls back to env var)
            secret_key: Optional secret key (falls back to env var)
            order_fill_callback: Optional callback for order fills
        """
        # Set credentials BEFORE calling super().__init__() because it triggers _validate_config()
        self.public_key = public_key or os.getenv("BACKPACK_PUBLIC_KEY")
        self.secret_key = secret_key or os.getenv("BACKPACK_SECRET_KEY")
        
        super().__init__(config)
        if order_fill_callback is not None:
            self.order_fill_callback = order_fill_callback

        self.logger = get_exchange_logger("backpack", getattr(self.config, "ticker", "UNKNOWN"))

        self.ws_manager: Optional[BackpackWebSocketManager] = None
        self._order_update_handler: Optional[Callable[[Dict[str, Any]], None]] = None
        
        # Caches
        self._precision_cache = SymbolPrecisionCache(max_price_decimals=self.MAX_PRICE_DECIMALS)
        self._market_symbol_map = MarketSymbolMapCache()
        self._latest_orders: Dict[str, OrderInfo] = {}

        # Initialize Backpack SDK clients
        try:
            self.public_client = Public()
            self.account_client = Account(public_key=self.public_key, secret_key=self.secret_key)
        except Exception as exc:
            message = str(exc).lower()
            if "base64" in message or "invalid" in message:
                raise MissingCredentialsError(f"Invalid Backpack credentials format: {exc}") from exc
            raise
        
        # Managers (will be initialized in connect())
        self.market_data: Optional[BackpackMarketData] = None
        self.order_manager: Optional[BackpackOrderManager] = None
        self.position_manager: Optional[BackpackPositionManager] = None
        self.account_manager: Optional[BackpackAccountManager] = None
        self.ws_handlers: Optional[BackpackWebSocketHandlers] = None

    def _validate_config(self) -> None:
        """Validate Backpack configuration."""
        # Validate the instance attributes (which may come from params or env)
        validate_credentials("BACKPACK_PUBLIC_KEY", self.public_key)
        validate_credentials("BACKPACK_SECRET_KEY", self.secret_key)

    async def connect(self) -> None:
        """Connect to Backpack WebSocket for order updates."""
        raw_symbol = getattr(self.config, "contract_id", None)
        ws_symbol: Optional[str] = None
        if raw_symbol and raw_symbol.upper() not in {"MULTI_SYMBOL", "MULTI"}:
            ws_symbol = self._ensure_exchange_symbol(raw_symbol)

        # Initialize market data manager
        self.market_data = BackpackMarketData(
            public_client=self.public_client,
            config=self.config,
            logger=self.logger,
            precision_cache=self._precision_cache,
            market_symbol_map=self._market_symbol_map,
            ws_manager=None,  # Will be set after ws_manager is created
            ensure_exchange_symbol_fn=self._ensure_exchange_symbol,
            max_price_decimals=self.MAX_PRICE_DECIMALS,
        )

        # Initialize order manager
        self.order_manager = BackpackOrderManager(
            account_client=self.account_client,
            config=self.config,
            logger=self.logger,
            latest_orders=self._latest_orders,
            precision_cache=self._precision_cache,
            market_data_manager=self.market_data,
            ensure_exchange_symbol_fn=self._ensure_exchange_symbol,
            round_to_tick_fn=self.round_to_tick,
            max_price_decimals=self.MAX_PRICE_DECIMALS,
        )

        # Initialize position manager
        self.position_manager = BackpackPositionManager(
            account_client=self.account_client,
            config=self.config,
            logger=self.logger,
            normalize_symbol_fn=self.normalize_symbol,
        )

        # Initialize account manager
        self.account_manager = BackpackAccountManager(
            account_client=self.account_client,
            public_client=self.public_client,
            config=self.config,
            logger=self.logger,
            ensure_exchange_symbol_fn=self._ensure_exchange_symbol,
        )

        # Initialize WebSocket handlers
        self.ws_handlers = BackpackWebSocketHandlers(
            config=self.config,
            logger=self.logger,
            latest_orders=self._latest_orders,
            order_update_handler=self._order_update_handler,
            order_fill_callback=self.order_fill_callback,
            order_manager=self.order_manager,
            emit_liquidation_event_fn=self.emit_liquidation_event,
            get_exchange_name_fn=self.get_exchange_name,
        )

        # Initialize WebSocket manager
        if not self.ws_manager:
            self.ws_manager = BackpackWebSocketManager(
                public_key=self.public_key,
                secret_key=self.secret_key,
                symbol=ws_symbol,
                order_update_callback=self.ws_handlers.handle_order_update,
                liquidation_callback=self.ws_handlers.handle_liquidation_notification,
                depth_fetcher=self.market_data.fetch_depth_snapshot,
                symbol_formatter=self._ensure_exchange_symbol,
            )
            self.ws_manager.set_logger(self.logger)
        else:
            self.ws_manager.update_symbol(ws_symbol)

        # Update market data manager with ws_manager reference
        if self.market_data:
            self.market_data.ws_manager = self.ws_manager

        await self.ws_manager.connect()

        if ws_symbol:
            ready = await self.ws_manager.wait_until_ready(timeout=5.0)
            if not ready and self.logger:
                self.logger.warning("[BACKPACK] Timed out waiting for account stream readiness")
            await self.ws_manager.wait_for_order_book(timeout=5.0)
        else:
            if self.logger:
                self.logger.debug("[BACKPACK] No contract symbol provided; WebSocket subscriptions deferred")

    async def disconnect(self) -> None:
        """Disconnect from Backpack WebSocket and cleanup."""
        if self.ws_manager:
            await self.ws_manager.disconnect()

    def get_exchange_name(self) -> str:
        """Return exchange identifier."""
        return "backpack"

    def supports_liquidation_stream(self) -> bool:
        """Backpack exposes liquidation-origin events on the order update stream."""
        return True

    # --------------------------------------------------------------------- #
    # Utility helpers (delegated)
    # --------------------------------------------------------------------- #

    def _ensure_exchange_symbol(self, identifier: Optional[str]) -> Optional[str]:
        """Normalize symbol/contract inputs to Backpack's expected wire format."""
        return ensure_exchange_symbol(
            identifier,
            self._market_symbol_map._cache,
            self.ws_manager,
        )

    def normalize_symbol(self, symbol: str) -> str:
        """
        Convert normalized symbol (e.g., 'BTC') to Backpack format.
        """
        return self._ensure_exchange_symbol(symbol) or symbol

    def round_to_tick(self, price: Decimal) -> Decimal:
        """Round price to tick size."""
        from decimal import ROUND_DOWN
        tick_size = getattr(self.config, "tick_size", None)
        return quantize_to_tick(
            price,
            ROUND_DOWN,
            tick_size,
            None,  # symbol not needed for rounding
            lambda s: self._precision_cache.get(s),
            lambda p, s: enforce_max_decimals(p, s, lambda sym: self._precision_cache.get(sym), self.MAX_PRICE_DECIMALS),
            self.MAX_PRICE_DECIMALS,
        )

    # --------------------------------------------------------------------- #
    # Market data (delegated)
    # --------------------------------------------------------------------- #

    async def fetch_bbo_prices(self, contract_id: str):
        """Fetch best bid/offer, preferring WebSocket data when available."""
        return await self.market_data.fetch_bbo_prices(contract_id)

    async def get_order_book_depth(
        self,
        contract_id: str,
        levels: int = 10,
    ):
        """Fetch order book depth, preferring WebSocket data when available."""
        return await self.market_data.get_order_book_depth(contract_id, levels)

    async def get_order_price(self, direction: str) -> Decimal:
        """Determine a maker-friendly order price."""
        return await self.market_data.get_order_price(direction, round_to_tick_fn=self.round_to_tick)

    async def get_contract_attributes(self):
        """Populate contract_id and tick_size for current ticker."""
        return await self.market_data.get_contract_attributes()

    # --------------------------------------------------------------------- #
    # Order placement & management (delegated)
    # --------------------------------------------------------------------- #

    async def place_limit_order(
        self,
        contract_id: str,
        quantity: Decimal,
        price: Decimal,
        side: str,
        reduce_only: bool = False,
        client_order_id: Optional[int] = None,
    ) -> OrderResult:
        """Place a post-only limit order on Backpack."""
        return await self.order_manager.place_limit_order(
            contract_id=contract_id,
            quantity=quantity,
            price=price,
            side=side,
            reduce_only=reduce_only,
            client_order_id=client_order_id,
        )

    async def place_market_order(
        self,
        contract_id: str,
        quantity: Decimal,
        side: str,
        reduce_only: bool = False,
        client_order_id: Optional[int] = None,
    ) -> OrderResult:
        """Place a market order for immediate execution."""
        return await self.order_manager.place_market_order(
            contract_id=contract_id,
            quantity=quantity,
            side=side,
            reduce_only=reduce_only,
            client_order_id=client_order_id,
        )

    async def cancel_order(self, order_id: str) -> OrderResult:
        """Cancel an existing order."""
        return await self.order_manager.cancel_order(order_id)

    async def get_order_info(self, order_id: str, *, force_refresh: bool = False) -> Optional[OrderInfo]:
        """Fetch detailed order information."""
        return await self.order_manager.get_order_info(order_id, force_refresh=force_refresh)
    
    async def await_order_update(self, order_id: str, timeout: float = 10.0) -> Optional[OrderInfo]:
        """
        Wait for websocket order update with optional timeout.
        
        This method efficiently waits for order status changes via websocket,
        falling back to REST API polling if websocket update doesn't arrive.
        
        Args:
            order_id: Order identifier to wait for
            timeout: Maximum time to wait in seconds (default: 10.0)
            
        Returns:
            OrderInfo if update received within timeout, None otherwise
        """
        if not self.order_manager:
            raise RuntimeError("Order manager not initialized. Call connect() first.")
        return await self.order_manager.await_order_update(order_id, timeout)

    async def get_active_orders(self, contract_id: str):
        """Return currently active orders."""
        return await self.order_manager.get_active_orders(contract_id)
    
    def get_quantity_multiplier(self, symbol: str) -> int:
        """
        Get the quantity multiplier for a symbol on Backpack.
        
        Backpack's k-prefix tokens (kPEPE, kSHIB, kBONK) represent bundles of 1000 tokens.
        So 1 contract unit = 1000 actual tokens.
        
        Args:
            symbol: Normalized symbol (e.g., "PEPE", "BTC")
            
        Returns:
            1000 for k-prefix tokens, 1 for others
        """
        from exchange_clients.backpack.common import get_quantity_multiplier
        return get_quantity_multiplier(symbol)

    # --------------------------------------------------------------------- #
    # Position management (delegated)
    # --------------------------------------------------------------------- #

    async def get_account_positions(self) -> Decimal:
        """Return absolute position size for configured contract."""
        return await self.position_manager.get_account_positions()

    async def get_position_snapshot(
        self, 
        symbol: str,
        position_opened_at: Optional[float] = None,
    ) -> Optional[ExchangePositionSnapshot]:
        """Return a normalized position snapshot for a given symbol."""
        return await self.position_manager.get_position_snapshot(symbol, position_opened_at=position_opened_at)

    # --------------------------------------------------------------------- #
    # Account management (delegated)
    # --------------------------------------------------------------------- #

    async def get_account_balance(self) -> Optional[Decimal]:
        """Fetch available account balance."""
        return await self.account_manager.get_account_balance()

    async def get_leverage_info(self, symbol: str) -> Dict[str, Any]:
        """Fetch leverage limits for symbol."""
        return await self.account_manager.get_leverage_info(symbol)
    
    async def get_user_trade_history(
        self,
        symbol: str,
        start_time: float,
        end_time: float,
        order_id: Optional[str] = None,
    ) -> List[TradeData]:
        """
        Get user trade history for Backpack using account_client.get_fill_history().
        
        Args:
            symbol: Trading symbol (normalized format, e.g., "BTC", "TOSHI")
            start_time: Start timestamp (Unix seconds)
            end_time: End timestamp (Unix seconds)
            order_id: Optional order ID to filter fills
            
        Returns:
            List of TradeData objects, or empty list on error
        """
        try:
            from datetime import datetime
            
            self.logger.info(
                f"[BACKPACK] Fetching trade history for {symbol} "
                f"(time_range={start_time:.0f}-{end_time:.0f}, "
                f"order_id={order_id if order_id else 'None'})"
            )
            
            if not self.account_client:
                self.logger.warning("[BACKPACK] Account client not available for trade history")
                return []
            
            # Resolve contract_id for the symbol (Backpack format)
            # Use base implementation first (checks cache and config.contract_id)
            contract_id = self.resolve_contract_id(symbol)
            
            # If resolve_contract_id returned symbol as-is (cache miss), try to resolve from markets
            if contract_id == symbol.upper():
                # Try market_symbol_map cache first (populated by get_contract_attributes)
                if hasattr(self, '_market_symbol_map') and self._market_symbol_map:
                    cached_symbol = self._market_symbol_map.get(symbol.upper())
                    if cached_symbol:
                        contract_id = cached_symbol
                        self.logger.debug(f"[BACKPACK] Found symbol in market_symbol_map cache: {contract_id}")
                
                # If still not found, try to fetch from markets API
                if contract_id == symbol.upper() and hasattr(self, 'public_client') and self.public_client:
                    try:
                        self.logger.debug(f"[BACKPACK] Looking up symbol '{symbol}' in markets API...")
                        markets = self.public_client.get_markets()
                        for market in markets or []:
                            if (
                                market.get("marketType") == "PERP"
                                and market.get("baseSymbol", "").upper() == symbol.upper()
                                and market.get("quoteSymbol") == "USDC"
                            ):
                                contract_id = market.get("symbol", "")
                                self.logger.info(f"[BACKPACK] Found symbol in markets API: {contract_id}")
                                # Cache it for future use
                                if hasattr(self, '_market_symbol_map') and self._market_symbol_map:
                                    self._market_symbol_map.set(symbol.upper(), contract_id)
                                break
                    except Exception as e:
                        self.logger.debug(f"[BACKPACK] Failed to fetch markets for symbol lookup: {e}")
                
                # Final fallback: use normalize_symbol (constructs format like "RESOLV_USDC_PERP")
                if contract_id == symbol.upper():
                    contract_id = self.normalize_symbol(symbol)
                    self.logger.debug(f"[BACKPACK] Using normalized symbol format: {contract_id}")
            
            self.logger.info(f"[BACKPACK] Symbol '{symbol}' resolved to contract_id: '{contract_id}'")
            
            # Build request parameters
            # Backpack's get_fill_history accepts: symbol, limit, offset, from_, to, fill_type, market_type
            params = {
                'symbol': contract_id,
                'limit': 100,  # Maximum limit (can be up to 1000)
                'offset': 0,
                'from': int(start_time * 1000),  # Convert to milliseconds
                'to': int(end_time * 1000),
            }
            
            self.logger.info(f"[BACKPACK] Using account_client.get_fill_history()")
            self.logger.debug(f"[BACKPACK] API request parameters: {params}")
            self.logger.debug(
                f"[BACKPACK] Time range: "
                f"{datetime.fromtimestamp(start_time).strftime('%Y-%m-%d %H:%M:%S')} "
                f"to {datetime.fromtimestamp(end_time).strftime('%Y-%m-%d %H:%M:%S')}"
            )
            
            fills_response = self.account_client.get_fill_history(
                symbol=contract_id,
                limit=100,
                offset=0,
                from_=int(start_time * 1000),
                to=int(end_time * 1000),
            )
            
            # Log raw response details
            self.logger.info(f"[BACKPACK] Raw API response type: {type(fills_response)}")
            if fills_response:
                if isinstance(fills_response, dict):
                    self.logger.debug(f"[BACKPACK] Raw API response keys: {list(fills_response.keys())}")
                    self.logger.debug(f"[BACKPACK] Raw API response: {fills_response}")
                    
                    # Check for error response (has 'code' and 'message' keys)
                    if 'code' in fills_response or 'message' in fills_response:
                        error_code = fills_response.get('code', 'N/A')
                        error_message = fills_response.get('message', 'N/A')
                        self.logger.warning(
                            f"[BACKPACK] API returned error response: code={error_code}, message={error_message}"
                        )
                        # Log full response for debugging
                        self.logger.debug(f"[BACKPACK] Full error response: {fills_response}")
                        return []
                    
                    # Check if response is wrapped in a dict (some SDKs wrap arrays)
                    if 'results' in fills_response:
                        fills = fills_response['results']
                    elif 'data' in fills_response:
                        fills = fills_response['data']
                    elif 'fills' in fills_response:
                        fills = fills_response['fills']
                    else:
                        # If it's a dict but not a known wrapper, log and try to extract
                        self.logger.warning(f"[BACKPACK] Response is dict but no known wrapper key found. Keys: {list(fills_response.keys())}")
                        fills = []
                elif isinstance(fills_response, list):
                    fills = fills_response
                else:
                    self.logger.warning(f"[BACKPACK] Unexpected fills response format: {type(fills_response)}")
                    return []
            else:
                self.logger.warning("[BACKPACK] Empty fills_response from API")
                return []
            
            if not isinstance(fills, list):
                self.logger.warning(f"[BACKPACK] Extracted fills is not a list: {type(fills)}")
                return []
            
            self.logger.info(f"[BACKPACK] Received {len(fills)} fills from API (before filtering)")
            
            # Log first few fills with details for debugging
            for i, fill in enumerate(fills[:5]):
                fill_info = {
                    "index": i,
                    "tradeId": fill.get('tradeId', 'N/A'),
                    "orderId": fill.get('orderId', 'N/A'),
                    "symbol": fill.get('symbol', 'N/A'),
                    "side": fill.get('side', 'N/A'),
                    "quantity": fill.get('quantity', 'N/A'),
                    "price": fill.get('price', 'N/A'),
                    "fee": fill.get('fee', 'N/A'),
                    "feeSymbol": fill.get('feeSymbol', 'N/A'),
                    "timestamp": fill.get('timestamp', 'N/A'),
                    "isMaker": fill.get('isMaker', 'N/A'),
                }
                if fill_info['timestamp'] != 'N/A' and fill_info['timestamp']:
                    try:
                        # Backpack timestamps might be in different formats
                        timestamp_val = fill_info['timestamp']
                        if isinstance(timestamp_val, str):
                            # Try parsing as milliseconds
                            timestamp_val = int(timestamp_val)
                        fill_time_seconds = timestamp_val / 1000 if timestamp_val > 1e10 else timestamp_val
                        fill_time_str = datetime.fromtimestamp(fill_time_seconds).strftime('%Y-%m-%d %H:%M:%S')
                        fill_info['timestamp_str'] = fill_time_str
                        fill_info['timestamp_seconds'] = fill_time_seconds
                    except (ValueError, OSError, TypeError) as e:
                        fill_info['timestamp_error'] = str(e)
                self.logger.info(f"[BACKPACK] Fill {i}: {fill_info}")
            
            # Parse fills into TradeData objects
            result = []
            filtered_by_order_id = 0
            filtered_by_time = 0
            
            for fill in fills:
                # Filter by order_id client-side
                if order_id:
                    fill_order_id = fill.get('orderId') or fill.get('order_id')
                    if str(fill_order_id) != order_id:
                        filtered_by_order_id += 1
                        continue
                
                # Filter by timestamp range (API may not filter precisely)
                fill_time = fill.get('timestamp') or fill.get('time', 0)
                if fill_time:
                    # Handle timestamp conversion (could be string or int, ms or seconds)
                    if isinstance(fill_time, str):
                        fill_time = int(fill_time)
                    fill_time_seconds = fill_time / 1000 if fill_time > 1e10 else fill_time
                    if fill_time_seconds < start_time or fill_time_seconds > end_time:
                        filtered_by_time += 1
                        continue
                else:
                    fill_time_seconds = float(start_time)
                
                # Extract fill data according to Backpack API format:
                # tradeId, orderId, symbol, side, quantity, price, fee, feeSymbol, timestamp, isMaker
                trade_id = str(fill.get('tradeId', fill.get('id', fill.get('fillId', ''))))
                quantity = Decimal(str(fill.get('quantity', fill.get('size', '0'))))
                price = Decimal(str(fill.get('price', '0')))
                fee = Decimal(str(fill.get('fee', '0')))
                fee_currency = fill.get('feeSymbol', fill.get('feeCurrency', fill.get('fee_currency', 'USDC')))
                
                # Handle side: Backpack uses "Bid" (buy) or "Ask" (sell)
                side_raw = fill.get('side', '')
                if side_raw.upper() == 'BID':
                    side = 'buy'
                elif side_raw.upper() == 'ASK':
                    side = 'sell'
                else:
                    side = side_raw.lower()  # Fallback to lowercase
                
                trade_order_id = str(fill.get('orderId', fill.get('order_id', ''))) if fill.get('orderId') or fill.get('order_id') else None
                
                result.append(TradeData(
                    trade_id=trade_id,
                    timestamp=fill_time_seconds,
                    symbol=symbol,
                    side=side,
                    quantity=quantity,
                    price=price,
                    fee=fee,
                    fee_currency=fee_currency,
                    order_id=trade_order_id,
                ))
            
            self.logger.info(
                f"[BACKPACK] Filtering results: "
                f"total_fills={len(fills)}, "
                f"filtered_by_order_id={filtered_by_order_id}, "
                f"filtered_by_time={filtered_by_time}, "
                f"final_count={len(result)}"
            )
            
            return result
            
        except Exception as e:
            self.logger.warning(f"[BACKPACK] Failed to get trade history for {symbol}: {e}")
            import traceback
            self.logger.debug(f"[BACKPACK] Trade history error traceback: {traceback.format_exc()}")
            return []

