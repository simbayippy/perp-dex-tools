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
        
        # WebSocket order book state (price -> size mapping)
        self._order_book_bids: Dict[str, Dict[Decimal, Decimal]] = {}  # contract_id -> {price: size}
        self._order_book_asks: Dict[str, Dict[Decimal, Decimal]] = {}  # contract_id -> {price: size}
        self._order_book_ready: Dict[str, bool] = {}  # contract_id -> ready flag
    
    @query_retry(default_return=(Decimal("0"), Decimal("0")))
    async def fetch_bbo_prices(self, contract_id: str) -> Tuple[Decimal, Decimal]:
        """
        Get best bid/offer prices, preferring WebSocket data when available.
        
        Args:
            contract_id: Contract/symbol identifier (e.g., "BTC-USD-PERP")
            
        Returns:
            Tuple of (best_bid, best_ask) as Decimals
            
        Raises:
            ValueError: If fetching fails or data is invalid
        """
        # Try WebSocket order book first (real-time, zero latency)
        if contract_id in self._order_book_bids and contract_id in self._order_book_asks:
            bids = self._order_book_bids[contract_id]
            asks = self._order_book_asks[contract_id]
            
            if bids and asks:
                # Get best bid (highest price) and best ask (lowest price)
                best_bid = max(bids.keys())
                best_ask = min(asks.keys())
                
                if best_bid > 0 and best_ask > 0:
                    self.logger.debug(f"Using WebSocket order book for BBO: {best_bid}/{best_ask}")
                    return best_bid, best_ask
        
        # Fall back to REST API
        try:
            # Use fetch_bbo endpoint (more efficient than full orderbook)
            bbo_data = self.api_client.fetch_bbo(contract_id)
            
            if not bbo_data:
                raise ValueError(f"Empty BBO response for {contract_id}")
            
            # Extract bid and ask
            bid = to_decimal(bbo_data.get('bid') or bbo_data.get('best_bid'))
            ask = to_decimal(bbo_data.get('ask') or bbo_data.get('best_ask'))
            
            if bid is None or ask is None:
                # Fallback: use orderbook with depth=1
                orderbook_data = self.api_client.fetch_orderbook(contract_id, {"depth": 1})
                bids = orderbook_data.get('bids', [])
                asks = orderbook_data.get('asks', [])
                
                if not bids or not asks:
                    raise ValueError(f"Failed to get bid/ask data for {contract_id}")
                
                bid = Decimal(str(bids[0][0]))
                ask = Decimal(str(asks[0][0]))
            
            if bid is None or ask is None or bid <= 0 or ask <= 0:
                raise ValueError(f"Invalid bid/ask prices for {contract_id}")
            
            return bid, ask
            
        except Exception as e:
            self.logger.error(f"Failed to fetch BBO prices for {contract_id}: {e}")
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
            contract_id: Contract/symbol identifier (e.g., "BTC-USD-PERP")
            levels: Number of price levels to fetch (default: 10)
            
        Returns:
            Dictionary with 'bids' and 'asks' lists of dicts with 'price' and 'size'
        """
        try:
            # Try WebSocket order book first (real-time, zero latency)
            if contract_id in self._order_book_bids and contract_id in self._order_book_asks:
                bids_dict = self._order_book_bids[contract_id]
                asks_dict = self._order_book_asks[contract_id]
                
                if bids_dict and asks_dict:
                    # Convert dict to sorted lists
                    bids_sorted = sorted(bids_dict.items(), reverse=True)[:levels]
                    asks_sorted = sorted(asks_dict.items())[:levels]
                    
                    bids = [{'price': price, 'size': size} for price, size in bids_sorted]
                    asks = [{'price': price, 'size': size} for price, size in asks_sorted]
                    
                    if bids and asks:
                        self.logger.debug(
                            f"Using WebSocket order book: {contract_id} "
                            f"({len(bids)} bids, {len(asks)} asks)"
                        )
                        return {'bids': bids, 'asks': asks}
            
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
    
    def handle_order_book_update(self, contract_id: str, order_book_data: Dict[str, Any]) -> None:
        """
        Handle order book update from WebSocket.
        
        Paradex sends order book updates with:
        - update_type: 's' (snapshot) or 'd' (delta)
        - inserts: array of {price, side, size} to add
        - updates: array of {price, side, size} to update
        - deletes: array of {price, side, size} to remove
        
        Args:
            contract_id: Contract/symbol identifier
            order_book_data: Order book update data from WebSocket
        """
        try:
            update_type = order_book_data.get('update_type', 'd')
            
            # Initialize order book dicts for this contract if needed
            if contract_id not in self._order_book_bids:
                self._order_book_bids[contract_id] = {}
            if contract_id not in self._order_book_asks:
                self._order_book_asks[contract_id] = {}
            
            bids_dict = self._order_book_bids[contract_id]
            asks_dict = self._order_book_asks[contract_id]
            
            # If snapshot, clear existing state
            if update_type == 's':
                bids_dict.clear()
                asks_dict.clear()
            
            # Process deletes
            for delete in order_book_data.get('deletes', []):
                price = to_decimal(delete.get('price'))
                side = delete.get('side', '').upper()
                if price:
                    if side == 'BUY':
                        bids_dict.pop(price, None)
                    elif side == 'SELL':
                        asks_dict.pop(price, None)
            
            # Process updates
            for update in order_book_data.get('updates', []):
                price = to_decimal(update.get('price'))
                side = update.get('side', '').upper()
                size = to_decimal(update.get('size'))
                if price and size is not None:
                    if side == 'BUY':
                        if size > 0:
                            bids_dict[price] = size
                        else:
                            bids_dict.pop(price, None)
                    elif side == 'SELL':
                        if size > 0:
                            asks_dict[price] = size
                        else:
                            asks_dict.pop(price, None)
            
            # Process inserts
            for insert in order_book_data.get('inserts', []):
                price = to_decimal(insert.get('price'))
                side = insert.get('side', '').upper()
                size = to_decimal(insert.get('size'))
                if price and size is not None and size > 0:
                    if side == 'BUY':
                        bids_dict[price] = size
                    elif side == 'SELL':
                        asks_dict[price] = size
            
            # Mark as ready after first snapshot
            if update_type == 's' or not self._order_book_ready.get(contract_id, False):
                self._order_book_ready[contract_id] = True
            
        except Exception as e:
            self.logger.error(f"Error handling order book update for {contract_id}: {e}")

