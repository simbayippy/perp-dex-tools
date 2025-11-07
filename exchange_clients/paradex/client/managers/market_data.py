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
        
        # Try WebSocket manager's order book first (if available and ready)
        if self.ws_manager and self.ws_manager.order_book_ready:
            order_book = self.ws_manager.get_order_book(levels=1)
            if order_book and order_book.get('bids') and order_book.get('asks'):
                best_bid = order_book['bids'][0]['price']
                best_ask = order_book['asks'][0]['price']
                if best_bid > 0 and best_ask > 0:
                    self.logger.debug(f"Using WebSocket manager order book for BBO: {best_bid}/{best_ask}")
                    return best_bid, best_ask
        
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
        
        Args:
            contract_id: Contract/symbol identifier (e.g., "BTC-USD-PERP")
            levels: Number of price levels to fetch (default: 10)
            
        Returns:
            Dictionary with 'bids' and 'asks' lists of dicts with 'price' and 'size'
        """
        try:
            # Try WebSocket manager's order book first (real-time, zero latency)
            if self.ws_manager and self.ws_manager.order_book_ready:
                order_book = self.ws_manager.get_order_book(levels=levels)
                if order_book and order_book.get('bids') and order_book.get('asks'):
                    self.logger.debug(
                        f"Using WebSocket manager order book: {contract_id} "
                        f"({len(order_book['bids'])} bids, {len(order_book['asks'])} asks)"
                    )
                    return order_book
            
            # Fall back to REST API
            self.logger.debug(f"Fetching order book via REST API: {contract_id}, levels={levels}")
            
            # Fetch order book from API
            orderbook_data = self.api_client.fetch_orderbook(contract_id, {"depth": levels})
            
            if not orderbook_data:
                return {'bids': [], 'asks': []}
            
            bids_raw = orderbook_data.get('bids', [])
            asks_raw = orderbook_data.get('asks', [])
            
            # Parse bids (sorted descending by price)
            bids = []
            for bid in bids_raw[:levels]:
                if isinstance(bid, (list, tuple)) and len(bid) >= 2:
                    price = to_decimal(bid[0], Decimal("0")) or Decimal("0")
                    size = to_decimal(bid[1], Decimal("0")) or Decimal("0")
                    bids.append({'price': price, 'size': size})
            
            # Parse asks (sorted ascending by price)
            asks = []
            for ask in asks_raw[:levels]:
                if isinstance(ask, (list, tuple)) and len(ask) >= 2:
                    price = to_decimal(ask[0], Decimal("0")) or Decimal("0")
                    size = to_decimal(ask[1], Decimal("0")) or Decimal("0")
                    asks.append({'price': price, 'size': size})
            
            return {'bids': bids, 'asks': asks}
            
        except Exception as e:
            self.logger.error(f"Failed to fetch order book depth for {contract_id}: {e}")
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
            self.logger.error(f"Ticker '{ticker}' not found in Paradex markets")
            raise ValueError(f"Ticker '{ticker}' not found in Paradex markets")
        
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

