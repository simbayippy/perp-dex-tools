"""
Market data manager for Paradex client.

Handles order book depth, BBO prices, and market metadata.
"""

import asyncio
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from exchange_clients.base_models import query_retry
from exchange_clients.paradex.client.utils.helpers import to_decimal
from exchange_clients.paradex.common import normalize_symbol


class ParadexMarketData:
    """
    Market data manager for Paradex exchange.
    
    Handles:
    - Order book depth fetching
    - BBO prices
    - Market configuration and metadata (tick_size, order_size_increment, etc.)
    """
    
    def __init__(
        self,
        paradex_client: Any,
        api_client: Any,
        config: Any,
        logger: Any,
        contract_id_cache: Dict[str, str],
        ws_manager: Optional[Any] = None,
    ):
        """
        Initialize market data manager.
        
        Args:
            paradex_client: Paradex SDK client instance
            api_client: Paradex API client instance (paradex.api_client)
            config: Trading configuration object
            logger: Logger instance
            contract_id_cache: Contract ID cache dict (for multi-symbol trading)
            ws_manager: Optional WebSocket manager for real-time data
        """
        self.paradex_client = paradex_client
        self.api_client = api_client
        self.config = config
        self.logger = logger
        self.contract_id_cache = contract_id_cache
        self.ws_manager = ws_manager
        
        # Market metadata cache (tick_size, order_size_increment, etc.)
        self._market_metadata: Dict[str, Dict[str, Any]] = {}
        self._min_order_notional: Dict[str, Decimal] = {}
    
    def set_ws_manager(self, ws_manager: Any) -> None:
        """Set the WebSocket manager (called after ws_manager is created)."""
        self.ws_manager = ws_manager
    
    @query_retry(default_return=(Decimal("0"), Decimal("0")))
    async def fetch_bbo_prices(self, contract_id: str) -> Tuple[Decimal, Decimal]:
        """
        Get best bid/offer prices, preferring WebSocket data when available.
        
        Args:
            contract_id: Contract/symbol identifier (e.g., "BTC-USD-PERP" or "BTC")
                        If symbol format, will be resolved to contract_id format
            
        Returns:
            Tuple of (best_bid, best_ask) as Decimals
            
        Raises:
            ValueError: If fetching fails or data is invalid
        """
        # Resolve symbol to contract_id format if needed (e.g., "RESOLV" -> "RESOLV-USD-PERP")
        resolved_contract_id = contract_id
        if not contract_id.endswith('-USD-PERP'):
            # Try to resolve from cache first
            normalized_symbol = contract_id.upper()
            if hasattr(self.contract_id_cache, 'get'):
                cached_contract_id = self.contract_id_cache.get(normalized_symbol)
            elif isinstance(self.contract_id_cache, dict):
                cached_contract_id = self.contract_id_cache.get(normalized_symbol)
            else:
                cached_contract_id = None
            
            if cached_contract_id:
                resolved_contract_id = cached_contract_id
            else:
                # Convert to Paradex format (assume PERP)
                resolved_contract_id = f"{contract_id.upper()}-USD-PERP"
        
        # Try WebSocket manager's BBO stream first (most accurate and real-time)
        # BBO stream has correct prices (e.g., "0.07631"), unlike ORDER_BOOK stream
        if self.ws_manager:
            latest_bbo = self.ws_manager.get_latest_bbo()
            if latest_bbo:
                # Match symbol (BBO stream sends full format like "RESOLV-USD-PERP")
                bbo_symbol = latest_bbo.symbol
                if bbo_symbol == resolved_contract_id or bbo_symbol == contract_id:
                    bid = to_decimal(latest_bbo.bid)
                    ask = to_decimal(latest_bbo.ask)
                    if bid and ask and bid > 0 and ask > 0:
                        self.logger.debug(f"📡 [PARADEX] Using BBO stream for {resolved_contract_id}: {bid}/{ask}")
                        return bid, ask
        
        # Fall back to REST API
        try:
            # Use fetch_bbo endpoint (more efficient than full orderbook)
            bbo_data = self.api_client.fetch_bbo(resolved_contract_id)
            
            if not bbo_data:
                raise ValueError(f"Empty BBO response for {resolved_contract_id}")
            
            # Extract bid and ask
            bid = to_decimal(bbo_data.get('bid') or bbo_data.get('best_bid'))
            ask = to_decimal(bbo_data.get('ask') or bbo_data.get('best_ask'))
            
            if bid is None or ask is None:
                # Fallback: use orderbook with depth=1
                orderbook_data = self.api_client.fetch_orderbook(resolved_contract_id, {"depth": 1})
                bids = orderbook_data.get('bids', [])
                asks = orderbook_data.get('asks', [])
                
                if not bids or not asks:
                    raise ValueError(f"Failed to get bid/ask data for {resolved_contract_id}")
                
                bid = Decimal(str(bids[0][0]))
                ask = Decimal(str(asks[0][0]))
            
            if bid is None or ask is None or bid <= 0 or ask <= 0:
                raise ValueError(f"Invalid bid/ask prices for {resolved_contract_id}")
            
            return bid, ask
            
        except Exception as e:
            self.logger.error(f"Failed to fetch BBO prices for {resolved_contract_id}: {e}")
            raise ValueError(f"Unable to fetch BBO prices for {resolved_contract_id}: {e}")
    
    async def get_order_book_depth(
        self,
        contract_id: str,
        levels: int = 10
    ) -> Dict[str, List[Dict[str, Decimal]]]:
        """
        Get order book depth for a symbol.
        
        Tries WebSocket first (real-time, zero latency), falls back to REST API.
        Always fetches full depth (not just BBO) for liquidity analysis.
        
        Args:
            contract_id: Contract/symbol identifier (e.g., "BTC-USD-PERP" or "BTC")
                        If symbol format, will be resolved to contract_id format
            levels: Number of price levels to fetch (default: 10, liquidity checks use 20)
            
        Returns:
            Dictionary with 'bids' and 'asks' lists of dicts with 'price' and 'size'
        """
        # Resolve symbol to contract_id format if needed (e.g., "RESOLV" -> "RESOLV-USD-PERP")
        resolved_contract_id = contract_id
        if not contract_id.endswith('-USD-PERP'):
            # Try to resolve from cache first
            normalized_symbol = contract_id.upper()
            if hasattr(self.contract_id_cache, 'get'):
                cached_contract_id = self.contract_id_cache.get(normalized_symbol)
            elif isinstance(self.contract_id_cache, dict):
                cached_contract_id = self.contract_id_cache.get(normalized_symbol)
            else:
                cached_contract_id = None
            
            if cached_contract_id:
                resolved_contract_id = cached_contract_id
            else:
                # Convert to Paradex format (assume PERP)
                resolved_contract_id = f"{contract_id.upper()}-USD-PERP"
        
        try:
            # Try WebSocket manager's order book first (real-time, zero latency)
            # NOTE: ORDER_BOOK stream with price_tick="0_1" groups prices into tick buckets (0.1 increments).
            # The prices represent tick levels, not exact prices. For exact BBO prices, use BBO stream.
            # For liquidity depth analysis, tick-grouped prices are still useful to see depth at different levels.
            if self.ws_manager and self.ws_manager.order_book_ready:
                order_book = self.ws_manager.get_order_book(levels=levels)
                if order_book and order_book.get('bids') and order_book.get('asks'):
                    bids = order_book.get('bids', [])
                    asks = order_book.get('asks', [])
                    
                    if bids and asks:
                        # IMPORTANT: ORDER_BOOK stream has tick-grouped prices (not exact).
                        # Replace the best bid/ask with exact prices from BBO stream for accurate spread calculation.
                        # This ensures LiquidityAnalyzer gets exact prices without needing exchange-specific changes.
                        latest_bbo = self.ws_manager.get_latest_bbo()
                        if latest_bbo:
                            bbo_symbol = latest_bbo.symbol
                            if bbo_symbol == resolved_contract_id or bbo_symbol == contract_id:
                                exact_bid = to_decimal(latest_bbo.bid)
                                exact_ask = to_decimal(latest_bbo.ask)
                                
                                if exact_bid and exact_ask and exact_bid > 0 and exact_ask > 0:
                                    # Replace first level prices with exact BBO prices
                                    # Keep sizes from ORDER_BOOK (they're still valid for depth)
                                    bids[0] = {'price': exact_bid, 'size': bids[0].get('size', Decimal("0"))}
                                    asks[0] = {'price': exact_ask, 'size': asks[0].get('size', Decimal("0"))}
                                    
                                    spread_bps = ((exact_ask - exact_bid) / exact_bid * 10000) if exact_bid > 0 else 0
                                    self.logger.debug(
                                        f"📡 [PARADEX] Using WebSocket order book with exact BBO prices: {resolved_contract_id} "
                                        f"({len(bids)} bids, {len(asks)} asks) | "
                                        f"Best: {exact_bid}/{exact_ask} | "
                                        f"Spread: {spread_bps:.0f} bps"
                                    )
                                    return order_book
                        
                        # Fallback: Validate tick-grouped prices
                        # If we don't have BBO data, check if tick-grouped prices are reasonable
                        best_bid_price = bids[0].get('price', Decimal("0"))
                        best_ask_price = asks[0].get('price', Decimal("0"))
                        
                        if best_bid_price > 0 and best_ask_price > 0:
                            spread_bps = ((best_ask_price - best_bid_price) / best_bid_price * 10000) if best_bid_price > 0 else 0
                            
                            # If price is suspiciously small (< 0.001) or spread is too wide (> 1000 bps), use REST
                            if best_bid_price < Decimal("0.001") or best_ask_price < Decimal("0.001") or spread_bps > 1000:
                                self.logger.warning(
                                    f"⚠️ [PARADEX] WebSocket order book has suspicious prices for {resolved_contract_id}: "
                                    f"bid={best_bid_price}, ask={best_ask_price}, spread={spread_bps:.0f} bps | "
                                    f"Falling back to REST API"
                                )
                            else:
                                self.logger.info(
                                    f"📡 [PARADEX] Using WebSocket order book (tick-grouped): {resolved_contract_id} "
                                    f"({len(bids)} bids, {len(asks)} asks) | "
                                    f"Best: {best_bid_price}/{best_ask_price} | "
                                    f"Spread: {spread_bps:.0f} bps"
                                )
                                return order_book
                        else:
                            self.logger.warning(
                                f"⚠️ [PARADEX] WebSocket order book has invalid prices for {resolved_contract_id}, "
                                f"falling back to REST API"
                            )
                    else:
                        self.logger.warning(
                            f"⚠️ [PARADEX] WebSocket order book empty for {resolved_contract_id}, "
                            f"falling back to REST API"
                        )
                else:
                    self.logger.warning(
                        f"⚠️ [PARADEX] WebSocket order book not ready or empty for {resolved_contract_id}, "
                        f"falling back to REST API"
                    )
            
            # Fall back to REST API - always fetch full depth for liquidity checks
            self.logger.info(
                f"📞 [REST][PARADEX] Fetching order book via REST API: {resolved_contract_id}, levels={levels}"
            )
            
            # Fetch order book from API
            orderbook_data = self.api_client.fetch_orderbook(resolved_contract_id, {"depth": levels})
            
            if not orderbook_data:
                self.logger.warning(f"⚠️ [PARADEX] Empty order book response for {resolved_contract_id}")
                return {'bids': [], 'asks': []}
            
            bids_raw = orderbook_data.get('bids', [])
            asks_raw = orderbook_data.get('asks', [])
            
            if not bids_raw or not asks_raw:
                self.logger.warning(
                    f"⚠️ [PARADEX] Order book has no bids/asks for {resolved_contract_id}: "
                    f"bids={len(bids_raw)}, asks={len(asks_raw)}"
                )
                return {'bids': [], 'asks': []}
            
            # Debug: Log first bid/ask to understand format
            if bids_raw and len(bids_raw[0]) >= 2:
                self.logger.debug(
                    f"🔍 [PARADEX] First bid raw: {bids_raw[0]} "
                    f"(assuming [price, size] format)"
                )
            if asks_raw and len(asks_raw[0]) >= 2:
                self.logger.debug(
                    f"🔍 [PARADEX] First ask raw: {asks_raw[0]} "
                    f"(assuming [price, size] format)"
                )
            
            # Parse bids (sorted descending by price)
            # Paradex API format: [price, size] (standard order book format)
            bids = []
            for bid in bids_raw[:levels]:
                if isinstance(bid, (list, tuple)) and len(bid) >= 2:
                    # Standard format: [price, size]
                    price = to_decimal(bid[0], Decimal("0")) or Decimal("0")
                    size = to_decimal(bid[1], Decimal("0")) or Decimal("0")
                    
                    # Validate: price should be reasonable (not too small, not too large)
                    # For RESOLV, price should be around 0.08, not 0.00001
                    if price > 0 and size > 0:
                        # Additional validation: if price seems wrong (too small), try swapping
                        # This handles potential [size, price] format
                        if price < Decimal("0.001") and size > Decimal("0.01"):
                            # Likely [size, price] format - swap them
                            self.logger.warning(
                                f"⚠️ [PARADEX] Detected potential [size, price] format for bid: "
                                f"swapping {price}/{size} -> {size}/{price}"
                            )
                            price, size = size, price
                        
                        bids.append({'price': price, 'size': size})
            
            # Parse asks (sorted ascending by price)
            asks = []
            for ask in asks_raw[:levels]:
                if isinstance(ask, (list, tuple)) and len(ask) >= 2:
                    # Standard format: [price, size]
                    price = to_decimal(ask[0], Decimal("0")) or Decimal("0")
                    size = to_decimal(ask[1], Decimal("0")) or Decimal("0")
                    
                    # Validate: price should be reasonable
                    if price > 0 and size > 0:
                        # Additional validation: if price seems wrong (too small), try swapping
                        if price < Decimal("0.001") and size > Decimal("0.01"):
                            # Likely [size, price] format - swap them
                            self.logger.warning(
                                f"⚠️ [PARADEX] Detected potential [size, price] format for ask: "
                                f"swapping {price}/{size} -> {size}/{price}"
                            )
                            price, size = size, price
                        
                        asks.append({'price': price, 'size': size})
            
            # Log best bid/ask for validation
            if bids and asks:
                best_bid_price = bids[0]['price']
                best_ask_price = asks[0]['price']
                self.logger.info(
                    f"📚 [PARADEX] REST order book: {resolved_contract_id} "
                    f"({len(bids)} bids, {len(asks)} asks) | "
                    f"Best: {best_bid_price}/{best_ask_price} | "
                    f"Spread: {((best_ask_price - best_bid_price) / best_bid_price * 10000):.0f} bps"
                )
            else:
                self.logger.warning(
                    f"⚠️ [PARADEX] Empty parsed order book for {resolved_contract_id}"
                )
            
            return {'bids': bids, 'asks': asks}
            
        except Exception as e:
            self.logger.error(
                f"❌ [PARADEX] Failed to fetch order book depth for {contract_id} "
                f"(resolved: {resolved_contract_id}): {e}"
            )
            return {'bids': [], 'asks': []}
    
    async def get_market_metadata(self, contract_id: str) -> Optional[Dict[str, Any]]:
        """
        Get market metadata (tick_size, order_size_increment, min_notional, etc.).
        
        Caches metadata to avoid repeated API calls.
        
        Args:
            contract_id: Contract/symbol identifier (e.g., "BTC-USD-PERP")
            
        Returns:
            Dictionary with market metadata, or None if not found
        """
        # Check cache first
        if contract_id in self._market_metadata:
            return self._market_metadata[contract_id]
        
        try:
            # Fetch market info
            markets_response = self.api_client.fetch_markets({"market": contract_id})
            
            if not markets_response or 'results' not in markets_response:
                return None
            
            markets = markets_response['results']
            if not markets:
                return None
            
            # Find matching market
            market_data = None
            for market in markets:
                if market.get('symbol') == contract_id or market.get('market') == contract_id:
                    market_data = market
                    break
            
            if not market_data:
                return None
            
            # Extract delta1_cross_margin_params for leverage calculation
            delta1_params = market_data.get('delta1_cross_margin_params', {})
            imf_base = to_decimal(delta1_params.get('imf_base') if isinstance(delta1_params, dict) else None)
            mmf_factor = to_decimal(delta1_params.get('mmf_factor') if isinstance(delta1_params, dict) else None)
            
            # Extract metadata
            metadata = {
                'symbol': market_data.get('symbol') or contract_id,
                'tick_size': to_decimal(market_data.get('price_tick_size')),
                'order_size_increment': to_decimal(market_data.get('order_size_increment')),
                'min_notional': to_decimal(market_data.get('min_notional')),
                'max_order_size': to_decimal(market_data.get('max_order_size')),
                'position_limit': to_decimal(market_data.get('position_limit')),
                'base_currency': market_data.get('base_currency'),
                'quote_currency': market_data.get('quote_currency'),
                # IMF parameters for leverage calculation (similar to Lighter/Backpack)
                # Note: max_leverage is calculated from imf_base: max_leverage = 1 / imf_base
                'imf_base': imf_base,  # Initial Margin Base (e.g., 0.11 = 11% margin = 9.09x leverage)
                'mmf_factor': mmf_factor,  # Maintenance Margin Factor (e.g., 0.51 = 51% of initial margin)
            }
            
            # Cache it
            self._market_metadata[contract_id] = metadata
            
            # Cache min order notional
            if metadata['min_notional']:
                normalized_symbol = normalize_symbol(contract_id)
                self._min_order_notional[normalized_symbol] = metadata['min_notional']
            
            return metadata
            
        except Exception as e:
            self.logger.error(f"Failed to fetch market metadata for {contract_id}: {e}")
            return None
    
    async def ensure_market_metadata(self, contract_id: str) -> None:
        """
        Ensure market metadata is loaded and cached.
        
        This should be called before placing orders to ensure tick_size and
        order_size_increment are available.
        
        Args:
            contract_id: Contract/symbol identifier
        """
        if contract_id not in self._market_metadata:
            await self.get_market_metadata(contract_id)
    
    def get_min_order_notional(self, symbol: str) -> Optional[Decimal]:
        """
        Get minimum order notional for a symbol.
        
        Args:
            symbol: Normalized symbol (e.g., "BTC")
            
        Returns:
            Minimum order notional in USD, or None if not available
        """
        return self._min_order_notional.get(symbol.upper())
    
    async def get_contract_attributes(self, ticker: str) -> Tuple[str, Decimal]:
        """
        Get contract ID and tick size for a ticker.
        
        This method modifies client state (config, caches).
        
        Args:
            ticker: Trading symbol (e.g., "BTC", "AI16Z")
            
        Returns:
            Tuple of (contract_id, tick_size)
            
        Raises:
            ValueError: If ticker is empty, market not found, or market is not tradeable
        """
        if not ticker:
            self.logger.error("Ticker is empty")
            raise ValueError("Ticker is empty")
        
        # Convert ticker to Paradex format (e.g., "BTC" -> "BTC-USD-PERP")
        contract_id = f"{ticker.upper()}-USD-PERP"
        
        # Normalize ticker for caching (use ticker, not contract_id)
        normalized_ticker = ticker.upper()
        cache_key = normalized_ticker
        
        # Check cache first
        if contract_id in self._market_metadata:
            metadata = self._market_metadata[contract_id]
            tick_size = metadata.get('tick_size')
            if tick_size:
                # Update config
                self.config.contract_id = contract_id
                self.config.tick_size = tick_size
                
                # Cache contract_id for multi-symbol trading
                # ContractIdCache supports dict-like access, so we can use [] syntax
                if cache_key not in self.contract_id_cache:
                    self.contract_id_cache[cache_key] = contract_id
                
                return contract_id, tick_size
        
        # Fetch market metadata (this will cache it)
        metadata = await self.get_market_metadata(contract_id)
        
        if not metadata:
            self.logger.error(f"Ticker '{ticker}' not found in Paradex markets. This is an edge case in paradex {ticker} has funding but is not tradable")
            raise ValueError(f"Ticker '{ticker}' not found in Paradex markets. This is an edge case in paradex {ticker} has funding but is not tradable")
        
        tick_size = metadata.get('tick_size')
        if not tick_size:
            self.logger.error(f"Failed to get tick size for {ticker}")
            raise ValueError(f"Failed to get tick size for {ticker}")
        
        # Update config
        self.config.contract_id = contract_id
        self.config.tick_size = tick_size
        
        # Cache contract_id for multi-symbol trading
        # ContractIdCache supports dict-like access, so we can use [] syntax
        if cache_key not in self.contract_id_cache:
            self.contract_id_cache[cache_key] = contract_id
        
        # Cache min order notional if available
        min_notional = metadata.get('min_notional')
        if min_notional:
            self._min_order_notional[cache_key] = min_notional
            setattr(self.config, "min_order_notional", min_notional)
        
        self.logger.debug(
            f"[PARADEX] Contract attributes for {ticker}: "
            f"contract_id={contract_id}, tick_size={tick_size}"
        )
        
        return contract_id, tick_size

