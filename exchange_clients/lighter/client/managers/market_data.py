"""
Market data module for Lighter client.

Handles market ID lookup, order book fetching, BBO prices, and market configuration.
"""

import lighter
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from exchange_clients.base_models import query_retry
from exchange_clients.lighter.client.utils.caching import MarketIdCache


class LighterMarketData:
    """
    Market data manager for Lighter exchange.
    
    Handles:
    - Market ID lookup with caching
    - Order book depth fetching
    - BBO prices
    - Market configuration and metadata
    """
    
    def __init__(
        self,
        api_client: Any,
        config: Any,
        logger: Any,
        market_id_cache: MarketIdCache,
        ws_manager: Optional[Any] = None,
        normalize_symbol_fn: Optional[Any] = None,
    ):
        """
        Initialize market data manager.
        
        Args:
            api_client: Lighter API client instance
            config: Trading configuration object (will be modified)
            logger: Logger instance
            market_id_cache: Market ID cache instance
            ws_manager: Optional WebSocket manager for real-time data
            normalize_symbol_fn: Function to normalize symbols
        """
        self.api_client = api_client
        self.config = config
        self.logger = logger
        self.market_id_cache = market_id_cache
        self.ws_manager = ws_manager
        self.normalize_symbol = normalize_symbol_fn or (lambda s: s.upper())
        
        # These will be set/updated by methods that modify client state
        # We store references so we can update the client's attributes
        self._contract_id_cache_ref: Optional[Dict[str, str]] = None
        self._market_metadata_ref: Optional[Dict[str, Dict[str, Any]]] = None
        self._min_order_notional_ref: Optional[Dict[str, Decimal]] = None
        self._client_instance: Optional[Any] = None  # Reference to client for updating multipliers
    
    def set_client_references(
        self,
        contract_id_cache: Dict[str, str],
        market_metadata: Dict[str, Dict[str, Any]],
        min_order_notional: Dict[str, Decimal],
        client_instance: Any,
    ) -> None:
        """
        Set references to client attributes that need to be updated.
        
        This allows the manager to update client state when fetching market data.
        
        Args:
            contract_id_cache: Client's contract ID cache dict
            market_metadata: Client's market metadata cache dict
            min_order_notional: Client's min order notional dict
            client_instance: Client instance (for updating base_amount_multiplier, price_multiplier)
        """
        self._contract_id_cache_ref = contract_id_cache
        self._market_metadata_ref = market_metadata
        self._min_order_notional_ref = min_order_notional
        self._client_instance = client_instance
    
    async def get_market_id_for_symbol(self, symbol: str) -> Optional[int]:
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
        cached_market_id = self.market_id_cache.get(cache_key)
        if cached_market_id is not None:
            self.logger.debug(f"[LIGHTER] Using cached market_id for {symbol} (saved 300 weight)")
            return cached_market_id
        
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
            markets_to_cache: Dict[str, int] = {}
            
            for market in order_books.order_books:
                available_symbols.append(market.symbol)
                
                # Cache ALL markets while we have them (amortize the 300 weight cost!)
                market_cache_key = market.symbol.upper()
                markets_to_cache[market_cache_key] = market.market_id
                
                # Try Lighter-specific format first (e.g., "1000TOSHI")
                if market.symbol.upper() == lighter_symbol.upper():
                    found_market_id = market.market_id
                # Try exact match with original symbol
                elif market.symbol == symbol:
                    found_market_id = market.market_id
                # Try case-insensitive match
                elif market.symbol.upper() == symbol.upper():
                    found_market_id = market.market_id
            
            # Cache all discovered markets at once
            self.market_id_cache.set_multiple(markets_to_cache)
            
            if found_market_id is not None:
                # Cache the lookup key we used (not just the exact symbol match)
                self.market_id_cache.set(cache_key, found_market_id)
                self.market_id_cache.set(lighter_symbol.upper(), found_market_id)
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
    
    async def get_market_config(self, ticker: str) -> Tuple[int, int, int]:
        """
        Get market configuration for a ticker using official SDK.
        
        Returns:
            Tuple of (market_id, base_multiplier, price_multiplier)
        """
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
    
    def cache_market_metadata(self, normalized_symbol: str, metadata: Dict[str, Any]) -> None:
        """
        Persist market metadata for a symbol so multi-symbol sessions reuse correct precision.
        """
        if self._market_metadata_ref is None:
            return
            
        cache_key = normalized_symbol.upper()
        self._market_metadata_ref[cache_key] = metadata
        contract_id = metadata.get("contract_id")
        if contract_id is not None and self._contract_id_cache_ref is not None:
            self._contract_id_cache_ref[cache_key] = str(contract_id)
    
    def apply_market_metadata(self, normalized_symbol: str) -> Optional[Tuple[Any, Decimal]]:
        """
        Load cached market metadata into the client and config.
        
        Returns:
            Tuple of (contract_id, tick_size) if metadata found, None otherwise
        """
        if self._market_metadata_ref is None:
            return None
            
        cache_key = normalized_symbol.upper()
        metadata = self._market_metadata_ref.get(cache_key)
        if metadata is None:
            return None
        
        base_mult = metadata.get("base_amount_multiplier")
        price_mult = metadata.get("price_multiplier")
        contract_id = metadata.get("contract_id")
        tick_size = metadata.get("tick_size")
        min_notional = metadata.get("min_notional")
        
        if base_mult is not None and self._client_instance is not None:
            self._client_instance.base_amount_multiplier = base_mult
        if price_mult is not None and self._client_instance is not None:
            self._client_instance.price_multiplier = price_mult
        if contract_id is not None:
            self.config.contract_id = contract_id
            if self._contract_id_cache_ref is not None:
                self._contract_id_cache_ref[cache_key] = str(contract_id)
        if tick_size is not None:
            setattr(self.config, "tick_size", tick_size)
        if min_notional is not None:
            if self._min_order_notional_ref is not None:
                self._min_order_notional_ref[cache_key] = min_notional
            setattr(self.config, "min_order_notional", min_notional)
        
        if contract_id is not None and tick_size is not None:
            return contract_id, tick_size
        return None
    
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
                market_id = await self.get_market_id_for_symbol(normalized_symbol)
                
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
    
    async def get_order_price(self, contract_id: str, side: str = '') -> Decimal:
        """Get the price of an order with Lighter using official SDK."""
        # Get current market prices
        best_bid, best_ask = await self.fetch_bbo_prices(contract_id)
        if best_bid <= 0 or best_ask <= 0 or best_bid >= best_ask:
            self.logger.error("Invalid bid/ask prices")
            raise ValueError("Invalid bid/ask prices")

        order_price = (best_bid + best_ask) / 2

        # Simple mid-price calculation - let strategy handle order placement logic
        # (removed strategy-specific close order logic)

        return order_price
    
    async def get_contract_attributes(self, ticker: str) -> Tuple[str, Decimal]:
        """
        Get contract ID and tick size for a ticker.
        
        This method modifies client state (config, multipliers, caches).
        """
        if not ticker:
            self.logger.error("Ticker is empty")
            raise ValueError("Ticker is empty")

        normalized_ticker = self.normalize_symbol(ticker)
        cached_metadata = self.apply_market_metadata(normalized_ticker)
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
        if self._contract_id_cache_ref is not None:
            self._contract_id_cache_ref[cache_key] = str(market_id_value)
        
        base_amount_multiplier = pow(10, market_info.supported_size_decimals)
        price_multiplier = pow(10, market_info.supported_price_decimals)
        if self._client_instance is not None:
            self._client_instance.base_amount_multiplier = base_amount_multiplier
            self._client_instance.price_multiplier = price_multiplier

        try:
            tick_size = Decimal("1") / (Decimal("10") ** order_book_details.price_decimals)
            self.config.tick_size = tick_size
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
            if self._min_order_notional_ref is not None:
                self._min_order_notional_ref[normalized_symbol] = min_quote_amount
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
        self.cache_market_metadata(normalized_ticker, metadata)

        return self.config.contract_id, self.config.tick_size

