"""
Market data module for Backpack client.

Handles order book fetching, BBO prices, contract attributes, and market configuration.
"""

from decimal import Decimal, ROUND_DOWN
from typing import Any, Callable, Dict, List, Optional, Tuple

from exchange_clients.base_models import query_retry
from exchange_clients.backpack.client.utils.helpers import (
    to_decimal,
    infer_precision_from_prices,
)
from exchange_clients.backpack.client.utils.caching import SymbolPrecisionCache, MarketSymbolMapCache
from exchange_clients.backpack.common import get_backpack_symbol_format


class BackpackMarketData:
    """
    Market data manager for Backpack exchange.
    
    Handles:
    - Order book depth fetching
    - BBO prices
    - Contract attributes and metadata
    - Symbol precision inference
    """
    
    def __init__(
        self,
        public_client: Any,
        config: Any,
        logger: Any,
        precision_cache: SymbolPrecisionCache,
        market_symbol_map: MarketSymbolMapCache,
        ws_manager: Optional[Any] = None,
        ensure_exchange_symbol_fn: Optional[Any] = None,
        max_price_decimals: int = 3,
    ):
        """
        Initialize market data manager.
        
        Args:
            public_client: Backpack Public client instance
            config: Trading configuration object
            logger: Logger instance
            precision_cache: Symbol precision cache instance
            market_symbol_map: Market symbol map cache instance
            ws_manager: Optional WebSocket manager for real-time data
            ensure_exchange_symbol_fn: Function to ensure exchange symbol format
            max_price_decimals: Default max decimal places
        """
        self.public_client = public_client
        self.config = config
        self.logger = logger
        self.precision_cache = precision_cache
        self.market_symbol_map = market_symbol_map
        self.ws_manager = ws_manager
        self.ensure_exchange_symbol = ensure_exchange_symbol_fn or (lambda s: s)
        self.max_price_decimals = max_price_decimals
    
    @query_retry(default_return=(Decimal("0"), Decimal("0")))
    async def fetch_bbo_prices(self, contract_id: str) -> Tuple[Decimal, Decimal]:
        """
        Fetch best bid/offer, preferring WebSocket data when available.
        
        Args:
            contract_id: Contract identifier
            
        Returns:
            Tuple of (best_bid, best_ask)
        """
        # Efficient: Direct access to cached BBO from WebSocket
        if self.ws_manager and self.ws_manager.best_bid is not None and self.ws_manager.best_ask is not None:
            self.logger.info(f"📡 [BACKPACK] Using real-time BBO from WebSocket")
            bid, ask = self.ws_manager.best_bid, self.ws_manager.best_ask
        else:
            self.logger.info(f"📞 [REST][BACKPACK] Using REST depth snapshot")
            order_book = await self.get_order_book_depth(contract_id, levels=1)
            
            if not order_book['bids'] or not order_book['asks']:
                raise ValueError(f"Empty order book for {contract_id}")
            
            bid, ask = order_book['bids'][0]['price'], order_book['asks'][0]['price']
        
        # Infer decimal precision from observed prices
        if bid and ask and bid > 0 and ask > 0:
            infer_precision_from_prices(
                contract_id,
                bid,
                ask,
                self.precision_cache._cache,
                self.logger,
            )
        
        return bid, ask

    async def get_order_book_depth(
        self,
        contract_id: str,
        levels: int = 10,
    ) -> Dict[str, List[Dict[str, Decimal]]]:
        """
        Fetch order book depth, preferring WebSocket data when available.
        
        Args:
            contract_id: Contract identifier
            levels: Number of levels to return
            
        Returns:
            Dictionary with 'bids' and 'asks' lists
        """
        if self.ws_manager:
            ws_book = self.ws_manager.get_order_book(levels=levels)
            if ws_book:
                return ws_book

        # REST fallback
        try:
            symbol = self.ensure_exchange_symbol(contract_id)
            order_book = self.public_client.get_depth(symbol)
        except Exception as exc:
            self.logger.error(f"[BACKPACK] Failed to fetch order book depth: {exc}")
            return {"bids": [], "asks": []}

        if not isinstance(order_book, dict):
            return {"bids": [], "asks": []}

        bids_raw = order_book.get("bids", []) or []
        asks_raw = order_book.get("asks", []) or []

        bids_sorted = sorted(
            bids_raw,
            key=lambda x: to_decimal(x[0], Decimal("0")),
            reverse=True
        )[:levels]
        asks_sorted = sorted(
            asks_raw,
            key=lambda x: to_decimal(x[0], Decimal("0"))
        )[:levels]

        bids = [
            {"price": to_decimal(price, Decimal("0")), "size": to_decimal(size, Decimal("0"))}
            for price, size in bids_sorted
        ]
        asks = [
            {"price": to_decimal(price, Decimal("0")), "size": to_decimal(size, Decimal("0"))}
            for price, size in asks_sorted
        ]

        return {"bids": bids, "asks": asks}

    async def get_order_price(self, direction: str, round_to_tick_fn: Optional[Callable] = None) -> Decimal:
        """
        Determine a maker-friendly order price.
        
        Args:
            direction: 'buy' or 'sell'
            round_to_tick_fn: Optional function to round price to tick
            
        Returns:
            Maker-friendly price
        """
        best_bid, best_ask = await self.fetch_bbo_prices(self.config.contract_id)
        if best_bid <= 0 or best_ask <= 0:
            raise ValueError("Invalid bid/ask prices")

        if direction.lower() == "buy":
            price = best_ask - getattr(self.config, "tick_size", Decimal("0.01"))
        else:
            price = best_bid + getattr(self.config, "tick_size", Decimal("0.01"))

        # Round to tick if function provided
        if round_to_tick_fn:
            return round_to_tick_fn(price)
        return price

    async def get_contract_attributes(self) -> Tuple[str, Decimal]:
        """
        Populate contract_id and tick_size for current ticker.
        
        Returns:
            Tuple of (contract_id, tick_size)
        """
        ticker = getattr(self.config, "ticker", "")
        if not ticker:
            raise ValueError("Ticker is empty")

        min_quantity = Decimal("0")
        tick_size = Decimal("0")

        try:
            markets = self.public_client.get_markets()
        except Exception as exc:
            self.logger.error(f"[BACKPACK] Failed to fetch markets: {exc}")
            raise

        target_symbol = ""

        for market in markets or []:
            if (
                market.get("marketType") == "PERP"
                and market.get("baseSymbol") == ticker
                and market.get("quoteSymbol") == "USDC"
            ):
                target_symbol = market.get("symbol", "")
                quantity_filter = (market.get("filters", {}) or {}).get("quantity", {}) or {}
                price_filter = (market.get("filters", {}) or {}).get("price", {}) or {}
                min_quantity = to_decimal(quantity_filter.get("minQuantity"), Decimal("0"))
                step_size = to_decimal(quantity_filter.get("stepSize"), Decimal("0.0001"))
                tick_size = to_decimal(price_filter.get("tickSize"), Decimal("0.0001"))
                self.market_symbol_map.set(market.get("baseSymbol", "").upper(), target_symbol)
                setattr(self.config, "min_quantity", min_quantity or Decimal("0"))
                setattr(self.config, "step_size", step_size or Decimal("0.0001"))
                break

        if not target_symbol:
            raise ValueError(f"Failed to find Backpack contract for ticker {ticker}")

        self.config.contract_id = target_symbol
        
        # Cache contract_id for this symbol (multi-symbol trading support)
        if hasattr(self.config, '_contract_id_cache'):
            self.config._contract_id_cache[ticker.upper()] = target_symbol
        
        self.config.tick_size = tick_size or Decimal("0.0001")
        if not getattr(self.config, "step_size", None):
            setattr(self.config, "step_size", Decimal("0.0001"))
        if not getattr(self.config, "min_quantity", None):
            setattr(self.config, "min_quantity", Decimal("0"))

        if getattr(self.config, "quantity", Decimal("0")) < (min_quantity or Decimal("0")):
            raise ValueError(
                f"Order quantity {self.config.quantity} below Backpack minimum {min_quantity}"
            )

        return self.config.contract_id, self.config.tick_size

    def fetch_depth_snapshot(self, symbol: str) -> Dict[str, Any]:
        """
        Blocking depth snapshot fetch used by the WebSocket manager.
        
        Args:
            symbol: Symbol to fetch depth for
            
        Returns:
            Raw depth snapshot dictionary
        """
        exchange_symbol = self.ensure_exchange_symbol(symbol)
        return self.public_client.get_depth(exchange_symbol)

