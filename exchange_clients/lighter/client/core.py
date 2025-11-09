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

