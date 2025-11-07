"""
Paradex exchange client implementation for trading execution.

Refactored to follow modular pattern with managers.
"""

import os
import asyncio
from decimal import Decimal
from typing import Dict, Any, List, Optional, Tuple, Callable, Awaitable

from exchange_clients.base_client import BaseExchangeClient, OrderFillCallback
from exchange_clients.base_models import (
    OrderResult,
    OrderInfo,
    ExchangePositionSnapshot,
    validate_credentials,
)
from exchange_clients.paradex.common import normalize_symbol, get_paradex_symbol_format
from helpers.unified_logger import get_exchange_logger

from .managers.market_data import ParadexMarketData
from .managers.order_manager import ParadexOrderManager
from .managers.position_manager import ParadexPositionManager
from .managers.account_manager import ParadexAccountManager
from .managers.websocket_handlers import ParadexWebSocketHandlers
from .utils.caching import ContractIdCache


def patch_paradex_http_client():
    """Patch Paradex SDK HttpClient to suppress unwanted print statements."""
    try:
        from paradex_py.api.http_client import HttpClient

        def patched_request(self, url, http_method, params=None, payload=None, headers=None):
            res = self.client.request(
                method=http_method.value,
                url=url,
                params=params,
                json=payload,
                headers=headers,
            )
            if res.status_code >= 300:
                from paradex_py.api.models import ApiErrorSchema
                error = ApiErrorSchema().loads(res.text)
                raise Exception(error)
            try:
                return res.json()
            except ValueError:
                # Suppress the "No response request" print statement
                # This is expected for DELETE requests that don't return JSON
                pass

        # Replace the request method
        HttpClient.request = patched_request

    except ImportError:
        # Paradex SDK not available, skip patching
        pass


class ParadexClient(BaseExchangeClient):
    """Paradex exchange client implementation."""

    def __init__(
        self,
        config: Dict[str, Any],
        l1_address: Optional[str] = None,
        l2_private_key_hex: Optional[str] = None,
        l2_address: Optional[str] = None,
        environment: Optional[str] = None,
        order_fill_callback: OrderFillCallback = None,
    ):
        """
        Initialize Paradex client with L2 credentials.
        
        Args:
            config: Trading configuration dictionary
            l1_address: Optional Ethereum L1 address (falls back to env var)
            l2_private_key_hex: Optional L2 private key in hex format (falls back to env var)
            l2_address: Optional L2 address (falls back to env var)
            environment: Optional environment name (falls back to env var, default 'prod')
            order_fill_callback: Optional callback for order fills
        """
        # Apply HTTP client patch
        patch_paradex_http_client()
        
        # Set credentials BEFORE calling super().__init__() because it triggers _validate_config()
        self.l1_address = l1_address or os.getenv('PARADEX_L1_ADDRESS')
        self.l2_private_key_hex = l2_private_key_hex or os.getenv('PARADEX_L2_PRIVATE_KEY')
        self.l2_address = l2_address or os.getenv('PARADEX_L2_ADDRESS')
        self.environment = environment or os.getenv('PARADEX_ENVIRONMENT', 'prod')
        
        super().__init__(config)
        
        if order_fill_callback is not None:
            self.order_fill_callback = order_fill_callback
        
        # Initialize logger
        self.logger = get_exchange_logger("paradex", self.config.ticker)
        
        # Initialize Paradex SDK client (will be done in connect)
        self.paradex = None
        
        # Manager references (initialized in connect())
        self.market_data: Optional[ParadexMarketData] = None
        self.order_manager: Optional[ParadexOrderManager] = None
        self.position_manager: Optional[ParadexPositionManager] = None
        self.account_manager: Optional[ParadexAccountManager] = None
        self.ws_handlers: Optional[ParadexWebSocketHandlers] = None
        
        # Order tracking
        self._latest_orders: Dict[str, OrderInfo] = {}
        self._min_order_notional: Dict[str, Decimal] = {}
        
        # Contract ID cache (for multi-symbol trading)
        self._contract_id_cache = ContractIdCache()
        
        # WebSocket connection state
        self._ws_connected = False

    def _validate_config(self) -> None:
        """Validate Paradex configuration."""
        validate_credentials('PARADEX_L1_ADDRESS', self.l1_address)
        validate_credentials('PARADEX_L2_PRIVATE_KEY', self.l2_private_key_hex)

    def _initialize_paradex_client(self) -> None:
        """Initialize the Paradex SDK client with L2 credentials."""
        try:
            from paradex_py import Paradex
            from paradex_py.environment import TESTNET, PROD
            
            # Convert environment string to proper enum
            env_map = {
                'prod': PROD,
                'testnet': TESTNET,
                'nightly': TESTNET  # Use testnet for nightly
            }
            self.env = env_map.get(self.environment.lower(), TESTNET)
            
            # Convert L2 private key from hex to int
            try:
                from starknet_py.common import int_from_hex
                self.l2_private_key = int_from_hex(self.l2_private_key_hex)
            except Exception as e:
                raise ValueError(f"Invalid L2 private key format: {e}")
            
            # Initialize Paradex client
            self.paradex = Paradex(
                env=self.env,
                logger=None  # Disabled native logging
            )
            
            # Initialize account with L2 private key
            self.paradex.init_account(
                l1_address=self.l1_address,
                l2_private_key=self.l2_private_key
            )
            
            # Log the L2 address being used
            if self.l2_address:
                self.logger.info(f"Using L2 address: {self.l2_address}")
            
        except Exception as e:
            raise ValueError(f"Failed to initialize Paradex client: {e}")

    async def connect(self) -> None:
        """Connect to Paradex and initialize managers."""
        try:
            # Initialize Paradex SDK client
            self._initialize_paradex_client()
            
            # Initialize market data manager (needed before other managers)
            self.market_data = ParadexMarketData(
                paradex_client=self.paradex,
                api_client=self.paradex.api_client,
                config=self.config,
                logger=self.logger,
                contract_id_cache=self._contract_id_cache,
                ws_manager=None,  # Will be set after ws_manager is created
            )
            
            # Initialize order manager
            self.order_manager = ParadexOrderManager(
                paradex_client=self.paradex,
                api_client=self.paradex.api_client,
                config=self.config,
                logger=self.logger,
                latest_orders=self._latest_orders,
                market_data_manager=self.market_data,
                normalize_symbol_fn=normalize_symbol,
            )
            
            # Initialize position manager
            self.position_manager = ParadexPositionManager(
                api_client=self.paradex.api_client,
                config=self.config,
                logger=self.logger,
                normalize_symbol_fn=normalize_symbol,
                market_data_manager=self.market_data,
            )
            
            # Initialize account manager
            self.account_manager = ParadexAccountManager(
                api_client=self.paradex.api_client,
                config=self.config,
                logger=self.logger,
                market_data_manager=self.market_data,
                normalize_symbol_fn=normalize_symbol,
            )
            
            # Initialize WebSocket handlers
            self.ws_handlers = ParadexWebSocketHandlers(
                config=self.config,
                logger=self.logger,
                latest_orders=self._latest_orders,
                order_fill_callback=self.order_fill_callback,
                order_manager=self.order_manager,
                position_manager=self.position_manager,
                emit_liquidation_event_fn=self.emit_liquidation_event,
                get_exchange_name_fn=self.get_exchange_name,
                normalize_symbol_fn=normalize_symbol,
            )
            
            # Connect to WebSocket
            is_connected = False
            while not is_connected:
                is_connected = await self.paradex.ws_client.connect()
                if not is_connected:
                    self.logger.log("Connection failed, retrying in 1 second...", "WARN")
                    await asyncio.sleep(1)
            
            # Wait a moment for connection to establish
            await asyncio.sleep(2)
            self._ws_connected = True
            
            # Setup WebSocket subscription for order updates
            await self._setup_websocket_subscription()
            
        except Exception as e:
            self.logger.error(f"Error connecting to Paradex: {e}")
            raise

    async def disconnect(self) -> None:
        """Disconnect from Paradex."""
        try:
            if hasattr(self, 'paradex') and self.paradex:
                await self.paradex.ws_client._close_connection()
                self._ws_connected = False
        except Exception as e:
            self.logger.error(f"Error during Paradex disconnect: {e}")

    def get_exchange_name(self) -> str:
        """Get the exchange name."""
        return "paradex"

    async def _setup_websocket_subscription(self) -> None:
        """Setup WebSocket subscription for order updates and order book."""
        if not self.ws_handlers:
            return
        
        # Ensure WebSocket is connected
        if not self._ws_connected:
            is_connected = False
            while not is_connected:
                is_connected = await self.paradex.ws_client.connect()
                if not is_connected:
                    self.logger.log("WebSocket connection failed, retrying in 1 second...", "WARN")
                    await asyncio.sleep(1)
            self._ws_connected = True
        
        # Subscribe to orders channel for the specific market
        from paradex_py.api.ws_client import ParadexWebsocketChannel
        
        contract_id = self.config.contract_id
        try:
            # Subscribe to order updates
            await self.paradex.ws_client.subscribe(
                ParadexWebsocketChannel.ORDERS,
                callback=self.ws_handlers.handle_websocket_order_update,
                params={"market": contract_id}
            )
            self.logger.info(f"Subscribed to order updates for {contract_id}")
            
            # Subscribe to order book updates
            await self.paradex.ws_client.subscribe(
                ParadexWebsocketChannel.ORDER_BOOK,
                callback=self._handle_order_book_update,
                params={
                    "market": contract_id,
                    "depth": 15,  # Paradex supports up to depth 15
                    "refresh_rate": "100ms",  # 50ms or 100ms
                    "price_tick": "0_1",  # Optional price grouping
                }
            )
            self.logger.info(f"Subscribed to order book updates for {contract_id}")
            
            # Subscribe to fills channel (includes liquidations)
            await self.paradex.ws_client.subscribe(
                ParadexWebsocketChannel.FILLS,
                callback=self._handle_fill_update,
                params={"market": contract_id}
            )
            self.logger.info(f"Subscribed to fills updates for {contract_id} (includes liquidations)")
        except Exception as e:
            self.logger.error(f"Failed to subscribe to WebSocket channels: {e}")
    
    async def _handle_order_book_update(self, ws_channel: Any, message: Dict[str, Any]) -> None:
        """
        Handle order book update from WebSocket.
        
        Args:
            ws_channel: WebSocket channel enum
            message: WebSocket message dictionary
        """
        try:
            params = message.get('params', {})
            data = params.get('data', {})
            market = data.get('market')
            
            if not market or not self.market_data:
                return
            
            # Delegate to market_data manager
            self.market_data.handle_order_book_update(market, data)
            
        except Exception as e:
            self.logger.error(f"Error handling order book update: {e}")
    
    async def _handle_fill_update(self, ws_channel: Any, message: Dict[str, Any]) -> None:
        """
        Handle fill update from WebSocket (includes liquidations).
        
        Paradex sends fills via FILLS channel. Liquidations have fill_type="LIQUIDATION".
        
        Args:
            ws_channel: WebSocket channel enum
            message: WebSocket message dictionary
        """
        try:
            params = message.get('params', {})
            data = params.get('data', {})
            
            # Check if this is a liquidation
            fill_type = data.get('fill_type') or data.get('trade_type')
            if fill_type == "LIQUIDATION":
                # Delegate to WebSocket handlers for liquidation processing
                if self.ws_handlers:
                    await self.ws_handlers.handle_liquidation_notification(data)
            
            # Note: Regular fills are handled via order updates (ORDERS channel)
            # This channel is primarily for detecting liquidations
            
        except Exception as e:
            self.logger.error(f"Error handling fill update: {e}")

    # ========================================================================
    # MARKET DATA & PRICING
    # ========================================================================

    async def fetch_bbo_prices(self, contract_id: str) -> Tuple[Decimal, Decimal]:
        """Fetch best bid and offer prices."""
        if not self.market_data:
            raise RuntimeError("Market data manager not initialized. Call connect() first.")
        return await self.market_data.fetch_bbo_prices(contract_id)

    async def get_order_book_depth(
        self,
        contract_id: str,
        levels: int = 10
    ) -> Dict[str, List[Dict[str, Decimal]]]:
        """Get order book depth for liquidity analysis."""
        if not self.market_data:
            raise RuntimeError("Market data manager not initialized. Call connect() first.")
        return await self.market_data.get_order_book_depth(contract_id, levels)

    # ========================================================================
    # ORDER MANAGEMENT
    # ========================================================================

    async def place_limit_order(
        self,
        contract_id: str,
        quantity: Decimal,
        price: Decimal,
        side: str,
        reduce_only: bool = False
    ) -> OrderResult:
        """Place a limit order at a specific price."""
        if not self.order_manager:
            raise RuntimeError("Order manager not initialized. Call connect() first.")
        return await self.order_manager.place_limit_order(
            contract_id, quantity, price, side, reduce_only
        )

    async def place_market_order(
        self,
        contract_id: str,
        quantity: Decimal,
        side: str,
        reduce_only: bool = False
    ) -> OrderResult:
        """Place a market order for immediate execution."""
        if not self.order_manager:
            raise RuntimeError("Order manager not initialized. Call connect() first.")
        return await self.order_manager.place_market_order(
            contract_id, quantity, side, reduce_only
        )

    async def cancel_order(self, order_id: str) -> OrderResult:
        """Cancel an existing order."""
        if not self.order_manager:
            raise RuntimeError("Order manager not initialized. Call connect() first.")
        return await self.order_manager.cancel_order(order_id)

    async def get_order_info(self, order_id: str, *, force_refresh: bool = False) -> Optional[OrderInfo]:
        """Get detailed information about a specific order."""
        if not self.order_manager:
            raise RuntimeError("Order manager not initialized. Call connect() first.")
        return await self.order_manager.get_order_info(order_id, force_refresh=force_refresh)

    async def get_active_orders(self, contract_id: str) -> List[OrderInfo]:
        """Get all active (open) orders for a contract."""
        if not self.order_manager:
            raise RuntimeError("Order manager not initialized. Call connect() first.")
        return await self.order_manager.get_active_orders(contract_id)

    def get_min_order_notional(self, symbol: str) -> Optional[Decimal]:
        """Return the minimum quote notional required to place an order."""
        if self.market_data:
            return self.market_data.get_min_order_notional(symbol)
        return self._min_order_notional.get(symbol.upper())

    def round_to_step(self, quantity: Decimal) -> Decimal:
        """Round quantity to order size increment."""
        # Get order size increment from market metadata if available
        if self.market_data and hasattr(self.config, 'contract_id'):
            metadata = self.market_data._market_metadata.get(self.config.contract_id, {})
            order_size_increment = metadata.get('order_size_increment')
            if order_size_increment:
                from decimal import ROUND_HALF_UP
                return quantity.quantize(order_size_increment, rounding=ROUND_HALF_UP)
        return quantity

    # ========================================================================
    # POSITION & ACCOUNT MANAGEMENT
    # ========================================================================

    async def get_account_positions(self) -> Decimal:
        """Get account position size for the configured contract."""
        if not self.position_manager:
            raise RuntimeError("Position manager not initialized. Call connect() first.")
        contract_id = getattr(self.config, 'contract_id', None)
        if not contract_id:
            return Decimal("0")
        return await self.position_manager.get_account_positions(contract_id)

    async def get_account_balance(self) -> Optional[Decimal]:
        """Get current available account balance for trading."""
        if not self.account_manager:
            raise RuntimeError("Account manager not initialized. Call connect() first.")
        return await self.account_manager.get_account_balance()

    async def get_position_snapshot(
        self,
        symbol: str,
        position_opened_at: Optional[float] = None,
    ) -> Optional[ExchangePositionSnapshot]:
        """Fetch a live snapshot for a specific symbol/contract."""
        if not self.position_manager:
            raise RuntimeError("Position manager not initialized. Call connect() first.")
        return await self.position_manager.get_position_snapshot(symbol, position_opened_at)

    async def get_account_pnl(self) -> Optional[Decimal]:
        """Get account unrealized P&L."""
        if not self.account_manager:
            raise RuntimeError("Account manager not initialized. Call connect() first.")
        return await self.account_manager.get_account_pnl()

    async def get_total_asset_value(self) -> Optional[Decimal]:
        """Get total account asset value (balance + unrealized P&L)."""
        if not self.account_manager:
            raise RuntimeError("Account manager not initialized. Call connect() first.")
        return await self.account_manager.get_total_asset_value()

    # ========================================================================
    # RISK MANAGEMENT & LEVERAGE
    # ========================================================================

    async def get_leverage_info(self, symbol: str) -> Dict[str, Any]:
        """Get leverage and position limit information for a symbol."""
        if not self.account_manager:
            raise RuntimeError("Account manager not initialized. Call connect() first.")
        return await self.account_manager.get_leverage_info(symbol)

    # ========================================================================
    # CONFIGURATION & UTILITIES
    # ========================================================================

    def normalize_symbol(self, symbol: str) -> str:
        """Convert a normalized symbol to Paradex format."""
        return get_paradex_symbol_format(symbol)

