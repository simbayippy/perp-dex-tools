"""
Backpack exchange client implementation for trading execution.
"""

import os
from decimal import Decimal
from typing import Any, Callable, Dict, Optional

from bpx.public import Public
from bpx.account import Account

from exchange_clients.base_client import BaseExchangeClient, OrderFillCallback
from exchange_clients.base_models import (
    ExchangePositionSnapshot,
    MissingCredentialsError,
    OrderInfo,
    OrderResult,
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

