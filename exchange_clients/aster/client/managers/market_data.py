"""
Market data module for Aster client.

Handles order book fetching, BBO prices, contract attributes, and market configuration.
"""

from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Dict, List, Optional, Tuple

from exchange_clients.base_models import query_retry
from exchange_clients.aster.client.utils.caching import TickSizeCache, ContractIdCache


class AsterMarketData:
    """
    Market data manager for Aster exchange.
    
    Handles:
    - Order book depth fetching (WebSocket + REST fallback)
    - BBO prices (WebSocket + REST fallback)
    - Contract attributes (tick size, step size, min notional)
    - Order price calculation
    """
    
    def __init__(
        self,
        make_request_fn: Callable,
        config: Any,
        logger: Any,
        tick_size_cache: TickSizeCache,
        contract_id_cache: ContractIdCache,
        min_order_notional: Dict[str, Decimal],
        ws_manager: Optional[Any] = None,
        normalize_symbol_fn: Optional[Callable[[str], str]] = None,
    ):
        """
        Initialize market data manager.
        
        Args:
            make_request_fn: Function to make authenticated API requests
            config: Trading configuration object (will be modified)
            logger: Logger instance
            tick_size_cache: Tick size cache instance
            contract_id_cache: Contract ID cache instance
            min_order_notional: Min order notional cache dict
            ws_manager: Optional WebSocket manager for real-time data
            normalize_symbol_fn: Function to normalize symbols
        """
        self._make_request = make_request_fn
        self.config = config
        self.logger = logger
        self.tick_size_cache = tick_size_cache
        self.contract_id_cache = contract_id_cache
        self.min_order_notional = min_order_notional
        self.ws_manager = ws_manager
        self.normalize_symbol = normalize_symbol_fn or (lambda s: s.upper())
    
    @query_retry(default_return=(Decimal("0"), Decimal("0")))
    async def fetch_bbo_prices(self, contract_id: str) -> Tuple[Decimal, Decimal]:
        """
        Fetch best bid and ask prices from Aster.
        
        Tries WebSocket book ticker first (real-time), falls back to REST API.
        """
        # Efficient: Direct access to cached BBO from WebSocket (same pattern as Lighter)
        if self.ws_manager and self.ws_manager.best_bid is not None and self.ws_manager.best_ask is not None:
            # Validate BBO at client level
            if self.ws_manager.best_bid > 0 and self.ws_manager.best_ask > 0 and self.ws_manager.best_bid < self.ws_manager.best_ask:
                self.logger.info(f"📡 [ASTER] Using real-time BBO from WebSocket")
                return Decimal(str(self.ws_manager.best_bid)), Decimal(str(self.ws_manager.best_ask))
            else:
                # WebSocket has data but it's invalid
                self.logger.warning(
                    f"⚠️  [ASTER] WebSocket BBO invalid: bid={self.ws_manager.best_bid}, "
                    f"ask={self.ws_manager.best_ask}"
                )
        elif self.ws_manager:
            # Log why WebSocket BBO is not available
            self.logger.info(
                f"📊 [ASTER] WebSocket BBO not ready: bid={self.ws_manager.best_bid}, "
                f"ask={self.ws_manager.best_ask}, running={getattr(self.ws_manager, 'running', False)}"
            )
        
        # DRY: Fall back to REST API via order book depth (more reliable)
        self.logger.info(f"📞 [REST][ASTER] Using REST API fallback")
        try:
            # Aster requires minimum depth limit of 5 (Binance-compatible API)
            order_book = await self.get_order_book_depth(contract_id, levels=5)
            
            if not order_book['bids'] or not order_book['asks']:
                raise ValueError(f"Empty order book for {contract_id}")
            
            best_bid = order_book['bids'][0]['price']
            best_ask = order_book['asks'][0]['price']
            
            if best_bid <= 0 or best_ask <= 0:
                raise ValueError(f"Invalid BBO prices: bid={best_bid}, ask={best_ask}")
            
            self.logger.info(f"✅ [ASTER] BBO: bid={best_bid}, ask={best_ask}")
            return best_bid, best_ask
            
        except Exception as e:
            self.logger.error(f"❌ [ASTER] Failed to get BBO prices for {contract_id}: {e}")
            raise ValueError(f"Unable to fetch BBO prices for {contract_id}: {e}")

    async def get_order_book_depth(
        self, 
        contract_id: str, 
        levels: int = 10
    ) -> Dict[str, List[Dict[str, Decimal]]]:
        """
        Get order book depth from Aster.
        
        Tries WebSocket depth stream first (100ms snapshots), falls back to REST API.
        
        Args:
            contract_id: Contract/symbol identifier
            levels: Number of price levels to fetch (default: 10)
            
        Returns:
            Dictionary with 'bids' and 'asks' lists of dicts with 'price' and 'size'
        """
        # 🔴 Priority 1: Try WebSocket depth stream (100ms snapshots, zero latency)
        if self.ws_manager:
            ws_book = self.ws_manager.get_order_book(levels)
            if ws_book and ws_book.get('bids') and ws_book.get('asks'):
                self.logger.info(
                    f"📡 [ASTER] Using real-time order book from WebSocket "
                    f"({len(ws_book['bids'])} bids, {len(ws_book['asks'])} asks)"
                )
                return ws_book
        
        # 🔄 Priority 2: Fall back to REST API
        # Normalize symbol to Aster's format (e.g., "ZORA" → "ZORAUSDT")
        # But don't double-normalize if it already ends with USDT
        if contract_id.upper().endswith("USDT"):
            normalized_symbol = contract_id.upper()
            self.logger.debug(f"🔍 [ASTER] Symbol already normalized: '{contract_id}'")
        else:
            normalized_symbol = self.normalize_symbol(contract_id)
            self.logger.debug(f"🔍 [ASTER] Symbol normalization: '{contract_id}' → '{normalized_symbol}'")
        try:
            self.logger.info(
                f"📞 [REST][ASTER] Fetching order book: symbol={normalized_symbol}, limit={levels}"
            )
            
            # Call Aster API: GET /fapi/v1/depth
            # Note: Aster expects symbols with quote currency (e.g., "BTCUSDT", not "BTC")
            result = await self._make_request('GET', '/fapi/v1/depth', {
                'symbol': normalized_symbol,
                'limit': levels
            })
            
            # Parse response
            # Aster returns: {"bids": [["price", "qty"], ...], "asks": [["price", "qty"], ...]}
            bids_raw = result.get('bids', [])
            asks_raw = result.get('asks', [])
            
            if not bids_raw or not asks_raw:
                self.logger.warning(
                    f"⚠️  [ASTER] Order book for {normalized_symbol} is empty or incomplete "
                    f"(bids={len(bids_raw)}, asks={len(asks_raw)})"
                )
                return {'bids': [], 'asks': []}

            # Convert to standardized format
            bids = [{'price': Decimal(bid[0]), 'size': Decimal(bid[1])} for bid in bids_raw]
            asks = [{'price': Decimal(ask[0]), 'size': Decimal(ask[1])} for ask in asks_raw]
            
            self.logger.debug(f"📚 [ASTER] Depth update: {len(bids)} bids, {len(asks)} asks")

            return {
                'bids': bids,
                'asks': asks
            }
            
        except Exception as e:
            self.logger.error(f"❌ [ASTER] Error fetching order book for '{contract_id}': {e}")
            self.logger.error(f"   Hint: Aster expects symbols with quote currency (e.g., 'BTCUSDT' not 'BTC')")
            import traceback
            self.logger.debug(f"Traceback: {traceback.format_exc()}")
            # Return empty order book on error
            return {'bids': [], 'asks': []}

    async def get_order_price(self, direction: str, contract_id: str) -> Decimal:
        """
        Get the price of an order with Aster.
        
        Args:
            direction: 'buy' or 'sell'
            contract_id: Contract identifier
            
        Returns:
            Calculated order price
        """
        best_bid, best_ask = await self.fetch_bbo_prices(contract_id)
        if best_bid <= 0 or best_ask <= 0:
            self.logger.error("Invalid bid/ask prices")
            raise ValueError("Invalid bid/ask prices")

        if direction == 'buy':
            # For buy orders, place slightly below best ask to ensure execution
            order_price = best_ask - self.config.tick_size
        else:
            # For sell orders, place slightly above best bid to ensure execution
            order_price = best_bid + self.config.tick_size
        return order_price

    async def get_contract_attributes(self) -> Tuple[str, Decimal]:
        """
        Get contract ID and tick size for a ticker.
        
        Fetches exchange info and extracts:
        - Contract ID (symbol format)
        - Tick size (price precision)
        - Step size (quantity precision)
        - Min notional
        
        Returns:
            Tuple of (contract_id, tick_size)
        """
        ticker = self.config.ticker
        if len(ticker) == 0:
            self.logger.error("Ticker is empty")
            raise ValueError("Ticker is empty")

        try:
            result = await self._make_request('GET', '/fapi/v1/exchangeInfo')

            # Check all symbols to find matching base asset
            found_symbol = None
            symbol_status = None
            
            # Debug: List all available symbols
            available_symbols = [s.get('symbol') for s in result['symbols'] if s.get('status') == 'TRADING']
            self.logger.debug(
                f"🔍 [ASTER] Found {len(available_symbols)} tradeable symbols. "
                f"Looking for {ticker}USDT..."
            )
            
            for symbol_info in result['symbols']:
                if (symbol_info.get('baseAsset') == ticker and
                        symbol_info.get('quoteAsset') == 'USDT'):
                    found_symbol = symbol_info
                    symbol_status = symbol_info.get('status', 'UNKNOWN')
                    
                    self.logger.debug(
                        f"🔍 [ASTER] Found {ticker}USDT with status: {symbol_status}"
                    )
                    
                    # Only accept TRADING status
                    if symbol_status == 'TRADING':
                        contract_id_value = symbol_info.get('symbol', '')
                        self.config.contract_id = contract_id_value
                        
                        # Cache contract_id for this symbol (multi-symbol trading support)
                        self.contract_id_cache[ticker.upper()] = contract_id_value

                        # Get tick size from filters
                        for filter_info in symbol_info.get('filters', []):
                            if filter_info.get('filterType') == 'PRICE_FILTER':
                                tick_size_value = Decimal(filter_info['tickSize'].strip('0'))
                                self.config.tick_size = tick_size_value
                                # Cache per-symbol for multi-symbol trading
                                self.tick_size_cache.set(ticker.upper(), tick_size_value)
                                self.tick_size_cache.set(contract_id_value, tick_size_value)
                                break

                        # Get LOT_SIZE filter (quantity precision)
                        min_quantity = Decimal(0)
                        step_size = Decimal('1')  # Default to whole numbers
                        min_notional: Optional[Decimal] = None
                        for filter_info in symbol_info.get('filters', []):
                            if filter_info.get('filterType') == 'LOT_SIZE':
                                min_quantity = Decimal(filter_info.get('minQty', 0))
                                step_size_str = filter_info.get('stepSize', '1')
                                step_size = Decimal(step_size_str.strip('0') if step_size_str.strip('0') else '1')
                                break
                        for filter_info in symbol_info.get('filters', []):
                            if filter_info.get('filterType') == 'MIN_NOTIONAL':
                                notional_raw = filter_info.get('notional')
                                if notional_raw is not None:
                                    try:
                                        min_notional = Decimal(str(notional_raw))
                                    except (InvalidOperation, TypeError, ValueError):
                                        min_notional = None
                                break
                        
                        # Store step_size in config for quantity rounding
                        self.config.step_size = step_size
                        
                        self.logger.debug(
                            f"📐 [ASTER] {ticker}USDT filters: "
                            f"tick_size={self.config.tick_size}, step_size={step_size}, "
                            f"min_qty={min_quantity}, min_notional={min_notional}"
                        )

                        if self.config.quantity < min_quantity:
                            self.logger.error(
                                f"Order quantity is less than min quantity: "
                                f"{self.config.quantity} < {min_quantity}"
                            )
                            raise ValueError(
                                f"Order quantity is less than min quantity: "
                                f"{self.config.quantity} < {min_quantity}"
                            )

                        if self.config.tick_size == 0:
                            self.logger.error("Failed to get tick size for ticker")
                            raise ValueError("Failed to get tick size for ticker")

                        if min_notional is not None:
                            setattr(self.config, "min_order_notional", min_notional)
                            ticker_key = ticker.upper()
                            contract_key = (self.config.contract_id or "").upper()
                            self.min_order_notional[ticker_key] = min_notional
                            if contract_key:
                                self.min_order_notional[contract_key] = min_notional

                        return self.config.contract_id, self.config.tick_size
                    else:
                        # Symbol found but not trading
                        break
            
            # Improved error message
            if found_symbol:
                self.logger.error(
                    f"Symbol {ticker}USDT exists on Aster but is not tradeable (status: {symbol_status})"
                )
                raise ValueError(
                    f"Symbol {ticker}USDT is not tradeable on Aster (status: {symbol_status})"
                )
            else:
                self.logger.error(
                    f"Symbol {ticker}USDT not found on Aster"
                )
                raise ValueError(f"Symbol {ticker}USDT not found on Aster")

        except Exception as e:
            self.logger.error(f"Error getting contract attributes: {e}")
            raise

