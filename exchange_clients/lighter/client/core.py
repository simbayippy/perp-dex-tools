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
    TradeData,
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
from ..websocket import LighterWebSocketManager
from .utils import decimal_or_none, build_order_info_from_payload, build_snapshot_from_raw, MarketIdCache
from .managers.market_data import LighterMarketData
from .managers.order_manager import LighterOrderManager
from .managers.position_manager import LighterPositionManager
from .managers.account_manager import LighterAccountManager
from .managers.websocket_handlers import LighterWebSocketHandlers
from networking import SessionProxyManager


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
        self._market_id_cache = MarketIdCache()
        self._contract_id_cache: Dict[str, str] = {}
        self._market_metadata: Dict[str, Dict[str, Any]] = {}
        
        # Market data manager (will be initialized in connect())
        self.market_data: Optional[LighterMarketData] = None
        
        # Order manager (will be initialized in connect())
        self.order_manager: Optional[LighterOrderManager] = None
        
        # Position manager (will be initialized in connect())
        self.position_manager: Optional[LighterPositionManager] = None
        
        # Account manager (will be initialized in connect())
        self.account_manager: Optional[LighterAccountManager] = None
        
        # WebSocket handlers (will be initialized in connect())
        self.ws_handlers: Optional[LighterWebSocketHandlers] = None

    def _validate_config(self) -> None:
        """Validate Lighter configuration."""
        # Validate the instance attributes (which may come from params or env)
        validate_credentials('API_KEY_PRIVATE_KEY', self.api_key_private_key)
        
        # Note: account_index and api_key_index have defaults of 0
        # which are valid values, so we don't need to validate them


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
            if SessionProxyManager.is_active():
                proxy_info = SessionProxyManager.describe(mask_password=True)
                if proxy_info:
                    self.logger.info(f"[LIGHTER] Session proxy configured: {proxy_info}")

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

            # Initialize market data manager (needed before other managers)
            self.market_data = LighterMarketData(
                api_client=self.api_client,
                config=self.config,
                logger=self.logger,
                market_id_cache=self._market_id_cache,
                ws_manager=None,  # Will be set after ws_manager is created
                normalize_symbol_fn=self.normalize_symbol,
            )
            self.market_data.set_client_references(
                contract_id_cache=self._contract_id_cache,
                market_metadata=self._market_metadata,
                min_order_notional=self._min_order_notional,
                client_instance=self,
            )

            # Initialize order manager
            self.order_manager = LighterOrderManager(
                lighter_client=self.lighter_client,
                order_api=self.order_api,
                account_api=self.account_api,
                config=self.config,
                logger=self.logger,
                account_index=self.account_index,
                api_key_index=self.api_key_index,
                latest_orders=self._latest_orders,
                order_update_events=self._order_update_events,
                client_to_server_order_index=self._client_to_server_order_index,
                market_data_manager=self.market_data,
                ws_manager=None,  # Will be set after ws_manager is created
            )
            self.order_manager.set_client_references(
                base_amount_multiplier_ref=self,
                price_multiplier_ref=self,
                current_order_client_id_ref=self,
                contract_id_cache=self._contract_id_cache,
                market_id_cache=self._market_id_cache,
                inactive_lookup_window_seconds=self._inactive_lookup_window_seconds,
                inactive_lookup_limit=self._inactive_lookup_limit,
            )

            # Initialize position manager (needed before WebSocket handlers)
            self.position_manager = LighterPositionManager(
                account_api=self.account_api,
                order_api=self.order_api,
                lighter_client=self.lighter_client,
                config=self.config,
                logger=self.logger,
                account_index=self.account_index,
                raw_positions=self._raw_positions,
                positions_lock=self._positions_lock,
                positions_ready=self._positions_ready,
                ws_manager=None,  # Will be set after ws_manager is created
                normalize_symbol_fn=self.normalize_symbol,
                market_data=self.market_data,  # For REST fallback when websocket unavailable
            )
            
            # Initialize account manager
            self.account_manager = LighterAccountManager(
                account_api=self.account_api,
                order_api=self.order_api,
                api_client=self.api_client,
                config=self.config,
                logger=self.logger,
                account_index=self.account_index,
                user_stats=self._user_stats,
                user_stats_lock=self._user_stats_lock,
                user_stats_ready=self._user_stats_ready,
                min_order_notional=self._min_order_notional,
                market_data_manager=self.market_data,
                normalize_symbol_fn=self.normalize_symbol,
            )
            
            # Initialize WebSocket handlers (after all managers are created)
            self.ws_handlers = LighterWebSocketHandlers(
                config=self.config,
                logger=self.logger,
                latest_orders=self._latest_orders,
                client_to_server_order_index=self._client_to_server_order_index,
                current_order_client_id_ref=self,
                current_order_ref=self,
                order_fill_callback=self.order_fill_callback,
                order_manager=self.order_manager,
                position_manager=self.position_manager,
                account_manager=self.account_manager,
                emit_liquidation_event_fn=self.emit_liquidation_event,
                get_exchange_name_fn=self.get_exchange_name,
                normalize_symbol_fn=self.normalize_symbol,
            )
            
            # Initialize WebSocket manager (using custom implementation)
            self.ws_manager = LighterWebSocketManager(
                config=self.config,
                order_update_callback=self.ws_handlers.handle_websocket_order_update,
                liquidation_callback=self.ws_handlers.handle_liquidation_notification,
                positions_callback=self.ws_handlers.handle_positions_stream_update,
                user_stats_callback=self._handle_user_stats_update,
            )

            # Set logger for WebSocket manager
            self.ws_manager.set_logger(self.logger)
            
            # Update managers with ws_manager reference
            if self.market_data:
                self.market_data.ws_manager = self.ws_manager
            if self.order_manager:
                self.order_manager.ws_manager = self.ws_manager
            if self.position_manager:
                self.position_manager.ws_manager = self.ws_manager

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
        """Handle order updates from WebSocket. Delegates to ws_handlers."""
        self.ws_handlers.handle_websocket_order_update(order_data_list)

    async def handle_liquidation_notification(self, notifications: List[Dict[str, Any]]) -> None:
        """Normalize liquidation notifications from the Lighter stream. Delegates to ws_handlers."""
        await self.ws_handlers.handle_liquidation_notification(notifications)

    @query_retry(default_return=(Decimal("0"), Decimal("0")))
    async def fetch_bbo_prices(self, contract_id: str) -> Tuple[Decimal, Decimal]:
        """Get best bid/offer prices, preferring WebSocket data when available."""
        return await self.market_data.fetch_bbo_prices(contract_id)

    async def get_order_book_depth(
        self, 
        contract_id: str, 
        levels: int = 10
    ) -> Dict[str, List[Dict[str, Decimal]]]:
        """Get order book depth for a symbol."""
        return await self.market_data.get_order_book_depth(contract_id, levels)

    async def place_limit_order(
        self,
        contract_id: str,
        quantity: Decimal,
        price: Decimal,
        side: str,
        reduce_only: bool = False,
        client_order_id: Optional[int] = None,
    ) -> OrderResult:
        """Place a post only order with Lighter using official SDK."""
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
        client_order_id: Optional[int] = None
    ) -> OrderResult:
        """Place a market order with Lighter using official SDK."""
        return await self.order_manager.place_market_order(
            contract_id=contract_id,
            quantity=quantity,
            side=side,
            reduce_only=reduce_only,
            client_order_id=client_order_id,
            normalize_symbol_fn=self.normalize_symbol,
        )

    async def get_order_price(self, side: str = '') -> Decimal:
        """Get the price of an order with Lighter using official SDK."""
        return await self.market_data.get_order_price(self.config.contract_id, side)

    def resolve_client_order_id(self, client_order_id: str) -> Optional[str]:
        """Resolve a client order index to the server-side order index, if known."""
        return self.order_manager.resolve_client_order_id(client_order_id)

    async def cancel_order(self, order_id: str) -> OrderResult:
        """Cancel an order with Lighter."""
        return await self.order_manager.cancel_order(order_id, self.config.contract_id)

    async def await_order_update(self, order_id: str, timeout: float = 10.0) -> Optional[OrderInfo]:
        """
        Wait for websocket order update with optional timeout.
        
        This method efficiently waits for order status changes via websocket,
        falling back to REST API polling if websocket update doesn't arrive.
        
        Args:
            order_id: Order identifier to wait for (client_order_index or server_order_index)
            timeout: Maximum time to wait in seconds (default: 10.0)
            
        Returns:
            OrderInfo if update received within timeout, None otherwise
        """
        return await self.order_manager.await_order_update(order_id, timeout)

    async def get_order_info(self, order_id: str, *, force_refresh: bool = False) -> Optional[OrderInfo]:
        """Get order information from Lighter using official SDK."""
        return await self.order_manager.get_order_info(
            order_id=order_id,
            market_id=self.config.contract_id,
            ticker=getattr(self.config, "ticker", ""),
            force_refresh=force_refresh,
        )
    
    async def _handle_user_stats_update(self, payload: Dict[str, Any]) -> None:
        """Process user stats update from WebSocket (includes real-time balance)."""
        await self.account_manager.handle_user_stats_update(payload)
    
    async def _handle_positions_stream_update(self, payload: Dict[str, Any]) -> None:
        """Process account positions update received from websocket stream."""
        await self.ws_handlers.handle_positions_stream_update(payload)

    async def get_active_orders(self, contract_id: str) -> List[OrderInfo]:
        """Get active orders for a contract using official SDK."""
        return await self.order_manager.get_active_orders(contract_id)

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
        return await self.market_data.get_contract_attributes(ticker)

    def get_min_order_notional(self, symbol: str) -> Optional[Decimal]:
        """Return the minimum quote notional required for orders on the given symbol."""
        return self.account_manager.get_min_order_notional(symbol)

    # Account monitoring methods (Lighter-specific implementations)
    async def get_account_balance(self) -> Optional[Decimal]:
        """Get current account balance (WebSocket-first for 0 weight)."""
        return await self.account_manager.get_account_balance()

    async def get_position_snapshot(
        self, 
        symbol: str,
        position_opened_at: Optional[float] = None,
    ) -> Optional[ExchangePositionSnapshot]:
        """Return the latest cached position snapshot for a symbol, falling back to REST if required."""
        return await self.position_manager.get_position_snapshot(symbol, position_opened_at=position_opened_at)

    async def get_account_pnl(self) -> Optional[Decimal]:
        """Get account P&L using Lighter SDK."""
        return await self.position_manager.get_account_pnl()
    
    async def get_leverage_info(self, symbol: str) -> Dict[str, Any]:
        """Get leverage information for Lighter by querying market configuration."""
        return await self.account_manager.get_leverage_info(symbol)

    async def get_total_asset_value(self) -> Optional[Decimal]:
        """Get total account asset value using Lighter SDK."""
        return await self.account_manager.get_total_asset_value()
    
    async def get_user_trade_history(
        self,
        symbol: str,
        start_time: float,
        end_time: float,
        order_id: Optional[str] = None,
    ) -> List[TradeData]:
        """
        Get user trade history for Lighter using OrderApi.trades().
        
        Args:
            symbol: Trading symbol (normalized format, e.g., "BTC", "TOSHI")
            start_time: Start timestamp (Unix seconds)
            end_time: End timestamp (Unix seconds)
            order_id: Optional order ID (client_order_index or server order_index)
            
        Returns:
            List of TradeData objects, or empty list on error
        """
        try:
            if not self.order_api:
                self.logger.warning("[LIGHTER] OrderApi not available for trade history")
                return []
            
            # Get market_id for the symbol
            normalized_symbol = self.normalize_symbol(symbol)
            market_id = None
            if self.market_data:
                market_id = await self.market_data.get_market_id_for_symbol(normalized_symbol)
            
            if market_id is None:
                self.logger.warning(f"[LIGHTER] Could not find market_id for {symbol}")
                return []
            
            # Resolve order_id to order_index if needed
            # Lighter uses order_index (server-side) for filtering trades
            order_index = None
            if order_id:
                # Try to resolve client_order_index to server order_index
                resolved_order_id = self.order_manager.resolve_client_order_id(order_id)
                if resolved_order_id:
                    try:
                        order_index = int(resolved_order_id)
                    except (ValueError, TypeError):
                        # If resolution failed, try using order_id directly as order_index
                        try:
                            order_index = int(order_id)
                        except (ValueError, TypeError):
                            self.logger.debug(f"[LIGHTER] Could not convert order_id {order_id} to order_index")
                else:
                    # No resolution found, try using order_id directly
                    try:
                        order_index = int(order_id)
                    except (ValueError, TypeError):
                        self.logger.debug(f"[LIGHTER] Could not convert order_id {order_id} to order_index")
            
            # Generate auth token for API call
            auth_token = None
            if self.lighter_client:
                auth_token, error = self.lighter_client.create_auth_token_with_expiry()
                if error:
                    self.logger.debug(f"[LIGHTER] Error creating auth token for trade history: {error}")
                    # Continue without auth token - API might still work for some endpoints
            
            # Call trades API
            # Using OrderApi.trades() which calls GET /api/v1/trades (user-specific endpoint)
            # This is different from /api/v1/recentTrades (public endpoint)
            # The /api/v1/trades endpoint supports account_index filtering and has max limit of 100
            # We fetch the most recent 100 trades and filter client-side by time range
            # Using sort_dir='desc' to get newest trades first, then we'll filter by time range
            trades_kwargs = {
                "sort_by": "timestamp",
                "sort_dir": "desc",  # Newest first
                "limit": 100,  # Maximum allowed by Lighter API (1-100 range)
                "market_id": market_id,
                "account_index": self.account_index,
            }
            
            # Only add order_index if provided (filters server-side when available)
            if order_index is not None:
                trades_kwargs["order_index"] = order_index
            
            # Add auth token (required for account-specific queries)
            if auth_token:
                trades_kwargs["auth"] = auth_token
                trades_kwargs["authorization"] = auth_token
            else:
                self.logger.warning("[LIGHTER] No auth token available for trade history query")
            
            order_filter_info = f"order_index={order_index}" if order_index else "no order filter"
            self.logger.info(
                f"[LIGHTER] Fetching trade history for {symbol} (normalized: {normalized_symbol}, market_id={market_id}, "
                f"account_index={self.account_index}, {order_filter_info}, time_range={start_time:.0f}-{end_time:.0f})"
            )
            
            trades_response = await self.order_api.trades(**trades_kwargs)
            
            if not trades_response:
                self.logger.warning("[LIGHTER] Empty trades_response from API")
                # If order_id was provided but couldn't be resolved, try without order filter
                if order_id and order_index is None:
                    self.logger.debug(f"[LIGHTER] Retrying without order_index filter (order_id={order_id} couldn't be resolved)")
                    trades_kwargs_no_order = {k: v for k, v in trades_kwargs.items() if k != "order_index"}
                    trades_response = await self.order_api.trades(**trades_kwargs_no_order)
                    if not trades_response:
                        return []
            
            if not hasattr(trades_response, 'trades'):
                self.logger.warning(f"[LIGHTER] Response has no 'trades' attribute. Response type: {type(trades_response)}, attributes: {dir(trades_response)}")
                return []
            
            if not trades_response.trades:
                # If we have an order_id but no trades, try fetching without order filter
                # This handles cases where order_id resolution failed or trades aren't indexed yet
                if order_id:
                    if order_index is None:
                        self.logger.debug(f"[LIGHTER] No trades found with order filter, retrying without order_index (order_id={order_id})")
                    else:
                        self.logger.debug(f"[LIGHTER] No trades found with order_index={order_index}, retrying without order filter (order_id={order_id})")
                    
                    trades_kwargs_no_order = {k: v for k, v in trades_kwargs.items() if k != "order_index"}
                    trades_response_retry = await self.order_api.trades(**trades_kwargs_no_order)
                    if trades_response_retry and hasattr(trades_response_retry, 'trades') and trades_response_retry.trades:
                        trades_response = trades_response_retry
                    else:
                        self.logger.warning(
                            f"[LIGHTER] trades_response.trades is empty or None (type: {type(trades_response.trades)}). "
                            f"This may be due to API indexing delay - trades may appear shortly."
                        )
                        return []
                else:
                    self.logger.warning(f"[LIGHTER] trades_response.trades is empty or None (type: {type(trades_response.trades)})")
                    return []
            
            
            # Log first few trades with details for debugging
            for i, trade in enumerate(trades_response.trades[:5]):
                trade_info = {
                    "index": i,
                    "trade_id": getattr(trade, 'trade_id', 'N/A'),
                    "timestamp": getattr(trade, 'timestamp', 'N/A'),
                    "size": getattr(trade, 'size', 'N/A'),
                    "price": getattr(trade, 'price', 'N/A'),
                    "usd_amount": getattr(trade, 'usd_amount', 'N/A'),
                    "taker_fee": getattr(trade, 'taker_fee', 'N/A'),
                    "maker_fee": getattr(trade, 'maker_fee', 'N/A'),
                    "is_maker_ask": getattr(trade, 'is_maker_ask', 'N/A'),
                    "ask_account_id": getattr(trade, 'ask_account_id', 'N/A'),
                    "bid_account_id": getattr(trade, 'bid_account_id', 'N/A'),
                    "ask_id": getattr(trade, 'ask_id', 'N/A'),
                    "bid_id": getattr(trade, 'bid_id', 'N/A'),
                }
                if trade_info['timestamp'] != 'N/A':
                    try:
                        # Try to convert timestamp - handle different formats
                        raw_timestamp = trade_info['timestamp']
                        # Timestamp format detection:
                        # - > 1e13 (14+ digits): nanoseconds
                        # - > 1e10 (11-13 digits): milliseconds  
                        # - <= 1e10 (<=10 digits): seconds
                        if raw_timestamp > 1e13:  # Nanoseconds (16 digits for current time)
                            timestamp_seconds = raw_timestamp / 1e9
                        elif raw_timestamp > 1e10:  # Milliseconds (13 digits for current time)
                            timestamp_seconds = raw_timestamp / 1000
                        else:
                            timestamp_seconds = raw_timestamp  # Seconds (10 digits for current time)
                        
                        from datetime import datetime
                        trade_time_str = datetime.fromtimestamp(timestamp_seconds).strftime('%Y-%m-%d %H:%M:%S')
                        trade_info['timestamp_str'] = trade_time_str
                        trade_info['timestamp_seconds'] = timestamp_seconds
                    except (ValueError, OSError) as e:
                        trade_info['timestamp_error'] = str(e)
                        trade_info['raw_timestamp'] = raw_timestamp
            
            # Parse trades into TradeData objects
            # Note: We filter by timestamp client-side since API doesn't reliably support time filtering
            result = []
            filtered_out_count = 0
            for idx, trade in enumerate(trades_response.trades):
                # Filter by timestamp range (client-side filtering)
                raw_timestamp = trade.timestamp
                
                # Convert timestamp to seconds (handle nanoseconds/milliseconds)
                # Lighter API returns timestamps in milliseconds
                # Timestamp format detection:
                # - > 1e13 (14+ digits): nanoseconds
                # - > 1e10 (11-13 digits): milliseconds  
                # - <= 1e10 (<=10 digits): seconds
                if raw_timestamp > 1e13:  # Nanoseconds (16 digits for current time)
                    trade_timestamp = raw_timestamp / 1e9
                elif raw_timestamp > 1e10:  # Milliseconds (13 digits for current time)
                    trade_timestamp = raw_timestamp / 1000
                else:
                    trade_timestamp = raw_timestamp  # Seconds (10 digits for current time)
                
                # Log timestamp comparison for first few trades
                if idx < 5:
                    try:
                        from datetime import datetime
                        trade_time_str = datetime.fromtimestamp(trade_timestamp).strftime('%Y-%m-%d %H:%M:%S')
                        start_time_str = datetime.fromtimestamp(start_time).strftime('%Y-%m-%d %H:%M:%S')
                        end_time_str = datetime.fromtimestamp(end_time).strftime('%Y-%m-%d %H:%M:%S')
                        self.logger.debug(
                            f"[LIGHTER] Trade {idx} timestamp check: raw={raw_timestamp:.0f}, "
                            f"converted={trade_timestamp:.0f} ({trade_time_str}) "
                            f"vs range [{start_time:.0f} ({start_time_str}) - {end_time:.0f} ({end_time_str})]"
                        )
                    except (ValueError, OSError) as e:
                        self.logger.warning(f"[LIGHTER] Trade {idx} timestamp conversion error: {e}, raw={raw_timestamp}")
                
                if trade_timestamp < start_time or trade_timestamp > end_time:
                    filtered_out_count += 1
                    if idx < 5:
                        self.logger.debug(f"[LIGHTER] Trade {idx} filtered out (outside time range)")
                    continue
                
                # Filter by order_id client-side if order_index filtering didn't work
                if order_id and order_index is None:
                    # Check if this trade matches the order_id
                    # Lighter trades have ask_id and bid_id fields
                    trade_order_id = None
                    if hasattr(trade, 'ask_id'):
                        trade_order_id = str(trade.ask_id)
                    elif hasattr(trade, 'bid_id'):
                        trade_order_id = str(trade.bid_id)
                    
                    if trade_order_id != order_id:
                        # Also check client_order_index mapping
                        resolved = self.order_manager.resolve_client_order_id(order_id)
                        if resolved != str(trade.ask_id) and resolved != str(trade.bid_id):
                            continue
                
                # Determine side from trade type or ask/bid IDs
                # For Lighter, we need to check if we were the ask (sell) or bid (buy) side
                # This requires checking account_index against ask_account_id and bid_account_id
                side = "unknown"
                if hasattr(trade, 'ask_account_id') and hasattr(trade, 'bid_account_id'):
                    if trade.ask_account_id == self.account_index:
                        side = "sell"  # We were the ask side (selling)
                    elif trade.bid_account_id == self.account_index:
                        side = "buy"  # We were the bid side (buying)
                
                # Calculate fee (taker_fee or maker_fee depending on our role)
                # Lighter API returns fees as int32, likely in basis points (1 = 0.01%)
                # We need to calculate actual fee from trade value
                fee = Decimal("0")
                trade_value = Decimal(str(trade.usd_amount)) if hasattr(trade, 'usd_amount') else (
                    Decimal(str(trade.size)) * Decimal(str(trade.price))
                )
                
                if hasattr(trade, 'taker_fee') and trade.taker_fee is not None:
                    # Determine if we were maker or taker
                    is_maker = False
                    fee_basis_points = None
                    
                    if hasattr(trade, 'is_maker_ask'):
                        if trade.is_maker_ask and trade.ask_account_id == self.account_index:
                            # We were maker on ask side
                            is_maker = True
                            fee_basis_points = trade.maker_fee if hasattr(trade, 'maker_fee') and trade.maker_fee is not None else None
                        elif not trade.is_maker_ask and trade.bid_account_id == self.account_index:
                            # We were maker on bid side
                            is_maker = True
                            fee_basis_points = trade.maker_fee if hasattr(trade, 'maker_fee') and trade.maker_fee is not None else None
                        else:
                            # We were taker
                            is_maker = False
                            fee_basis_points = trade.taker_fee
                    else:
                        # Fallback: assume taker if we can't determine
                        fee_basis_points = trade.taker_fee
                    
                    # Convert fee to actual amount
                    # Lighter API returns fees as int32 in "hundredths of basis points" format
                    # Fee structure: Maker = 0.2 bps (0.002%), Taker = 2 bps (0.02%)
                    # Format: API value represents hundredths of basis points
                    #   - 1 unit = 0.01 bp = 0.0001%
                    #   - Maker: 20 units = 0.2 bps = 0.002% (20/1000000 = 0.00002)
                    #   - Taker: 200 units = 2 bps = 0.02% (200/1000000 = 0.0002)
                    if fee_basis_points is not None:
                        fee_bps = Decimal(str(fee_basis_points))
                        
                        # Convert: divide by 1,000,000 to get fee rate as decimal
                        # This converts "hundredths of basis points" to percentage
                        fee_rate = fee_bps / Decimal("1000000")
                        fee = trade_value * fee_rate
                        
                        # Log fee calculation for first trades
                        if idx < 1:
                            fee_bps_calculated = fee_rate * Decimal("10000")  # Convert to basis points for display
                            expected_maker_fee = trade_value * Decimal("0.000002")  # 0.2 bps
                            expected_taker_fee = trade_value * Decimal("0.00002")   # 2 bps
                            self.logger.debug(
                                f"[LIGHTER] Trade {idx} fee calculation: "
                                f"value=${trade_value:.2f}, "
                                f"fee_raw={fee_basis_points}, "
                                f"fee_rate={fee_rate:.6f} ({fee_rate*100:.4f}% = {fee_bps_calculated:.2f} bps), "
                                f"fee=${fee:.4f}, "
                                f"role={'maker' if is_maker else 'taker'}, "
                                f"expected_{'maker' if is_maker else 'taker'}_fee=${expected_maker_fee if is_maker else expected_taker_fee:.4f}"
                            )
                
                # Get order_id from trade (ask_id or bid_id depending on our side)
                trade_order_id = None
                if side == "sell" and hasattr(trade, 'ask_id'):
                    trade_order_id = str(trade.ask_id)
                elif side == "buy" and hasattr(trade, 'bid_id'):
                    trade_order_id = str(trade.bid_id)
                
                result.append(TradeData(
                    trade_id=str(trade.trade_id),
                    timestamp=float(trade_timestamp),  # Use converted timestamp
                    symbol=symbol,
                    side=side,
                    quantity=Decimal(str(trade.size)),
                    price=Decimal(str(trade.price)),
                    fee=fee,
                    fee_currency="USDC",  # Lighter typically uses USDC for fees
                    order_id=trade_order_id,
                ))
            
            self.logger.debug(
                f"[LIGHTER] Trade history filtering complete: {len(result)} trades in range, "
                f"{filtered_out_count} filtered out (outside time range)"
            )
            
            # Log summary of what we found
            if len(result) == 0 and len(trades_response.trades) > 0:
                # We got trades but they were all filtered out - log why
                try:
                    # Convert timestamps properly
                    def convert_timestamp(ts):
                        if ts > 1e13:  # Nanoseconds
                            return ts / 1e9
                        elif ts > 1e10:  # Milliseconds
                            return ts / 1000
                        return ts  # Seconds
                    
                    oldest_raw = min(t.timestamp for t in trades_response.trades)
                    newest_raw = max(t.timestamp for t in trades_response.trades)
                    oldest_trade = convert_timestamp(oldest_raw)
                    newest_trade = convert_timestamp(newest_raw)
                    
                    from datetime import datetime
                    self.logger.warning(
                        f"[LIGHTER] All {len(trades_response.trades)} trades filtered out. "
                        f"Trade time range: {datetime.fromtimestamp(oldest_trade).strftime('%Y-%m-%d %H:%M:%S')} "
                        f"to {datetime.fromtimestamp(newest_trade).strftime('%Y-%m-%d %H:%M:%S')}, "
                        f"Requested range: {datetime.fromtimestamp(start_time).strftime('%Y-%m-%d %H:%M:%S')} "
                        f"to {datetime.fromtimestamp(end_time).strftime('%Y-%m-%d %H:%M:%S')}"
                    )
                except Exception as e:
                    self.logger.warning(f"[LIGHTER] Could not log trade time range: {e}")
            
            return result
            
        except Exception as e:
            self.logger.warning(f"[LIGHTER] Failed to get trade history for {symbol}: {e}")
            import traceback
            self.logger.debug(f"[LIGHTER] Trade history error traceback: {traceback.format_exc()}")
            return []

