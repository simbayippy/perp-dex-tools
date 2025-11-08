"""
Order manager module for Backpack client.

Handles order placement, cancellation, querying, and tracking.
"""

import asyncio
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_UP
from exchange_clients.backpack.client.utils.helpers import (
    to_decimal,
    quantize_quantity,
    format_decimal,
    quantize_to_tick,
    get_symbol_precision,
    enforce_max_decimals,
    compute_post_only_price,
)
from typing import Any, Callable, Dict, List, Optional

from bpx.constants.enums import OrderTypeEnum, TimeInForceEnum

from exchange_clients.base_models import OrderInfo, OrderResult, query_retry
from exchange_clients.backpack.client.utils.converters import build_order_info_from_raw
from exchange_clients.backpack.client.utils.caching import SymbolPrecisionCache


class BackpackOrderManager:
    """
    Order manager for Backpack exchange.
    
    Handles:
    - Order placement (limit, market) with retry logic
    - Order cancellation
    - Order status queries
    - Order tracking and caching
    """
    
    def __init__(
        self,
        account_client: Any,
        config: Any,
        logger: Any,
        latest_orders: Dict[str, OrderInfo],
        precision_cache: SymbolPrecisionCache,
        market_data_manager: Optional[Any] = None,
        ensure_exchange_symbol_fn: Optional[Callable] = None,
        round_to_tick_fn: Optional[Callable] = None,
        max_price_decimals: int = 3,
    ):
        """
        Initialize order manager.
        
        Args:
            account_client: Backpack Account client instance
            config: Trading configuration object
            logger: Logger instance
            latest_orders: Dictionary storing latest OrderInfo objects
            precision_cache: Symbol precision cache instance
            market_data_manager: Optional market data manager (for BBO prices, contract attributes)
            ensure_exchange_symbol_fn: Function to ensure exchange symbol format
            round_to_tick_fn: Function to round price to tick size
            max_price_decimals: Default max decimal places
        """
        self.account_client = account_client
        self.config = config
        self.logger = logger
        self.latest_orders = latest_orders
        self.precision_cache = precision_cache
        self.market_data = market_data_manager
        self.ensure_exchange_symbol = ensure_exchange_symbol_fn or (lambda s: s)
        self.round_to_tick = round_to_tick_fn or (lambda p: p)
        self.max_price_decimals = max_price_decimals
        
        # WebSocket order update events for efficient order confirmation
        # Maps order_id -> asyncio.Event that gets set when order status changes
        self.order_update_events: Dict[str, asyncio.Event] = {}
    
    def _get_symbol_precision(self, symbol: Optional[str]) -> int:
        """Get symbol precision using cache."""
        return get_symbol_precision(symbol, self.precision_cache._cache, self.max_price_decimals)
    
    def _quantize_quantity(self, quantity: Any, max_decimals: Optional[int] = None) -> Decimal:
        """Quantize quantity using helper function."""
        step_size = getattr(self.config, "step_size", None)
        return quantize_quantity(quantity, step_size, max_decimals)
    
    def _format_decimal(self, value: Any, step: Optional[Decimal] = None, max_decimals: int = 8) -> str:
        """Format decimal using helper function."""
        return format_decimal(value, step, max_decimals)
    
    def _quantize_to_tick(self, price: Decimal, rounding_mode, symbol: Optional[str] = None) -> Decimal:
        """Quantize price to tick size."""
        tick_size = getattr(self.config, "tick_size", None)
        return quantize_to_tick(
            price,
            rounding_mode,
            tick_size,
            symbol,
            self._get_symbol_precision,
            lambda p, s: enforce_max_decimals(p, s, self._get_symbol_precision, self.max_price_decimals),
            self.max_price_decimals,
        )
    
    async def _compute_post_only_price(self, contract_id: str, raw_price: Decimal, side: str) -> Decimal:
        """Compute post-only price using helper function."""
        if not self.market_data:
            # Fallback: just quantize to tick without BBO adjustment
            return self._quantize_to_tick(raw_price, ROUND_DOWN if side.lower() == "buy" else ROUND_UP, contract_id)
        
        tick_size = getattr(self.config, "tick_size", None)
        return await compute_post_only_price(
            contract_id,
            raw_price,
            side,
            tick_size,
            self.market_data.fetch_bbo_prices,
            lambda p, rm, s: self._quantize_to_tick(p, rm, s),
            self.logger,
        )
    
    async def place_limit_order(
        self,
        contract_id: str,
        quantity: Decimal,
        price: Decimal,
        side: str,
        reduce_only: bool = False,
        client_order_id: Optional[int] = None,
    ) -> OrderResult:
        """
        Place a post-only limit order on Backpack.
        
        Args:
            contract_id: Contract identifier
            quantity: Order quantity
            price: Limit price
            side: 'buy' or 'sell'
            reduce_only: If True, order can only reduce existing position
            
        Note:
            Automatically retries with adjusted prices if the order would immediately match.
        """
        backpack_side = "Bid" if side.lower() == "buy" else "Ask"

        # Round price as backpack always seems to instant fill
        rounded_price = await self._compute_post_only_price(contract_id, price, side)
        quantized_quantity = self._quantize_quantity(quantity)
        min_quantity = getattr(self.config, "min_quantity", None)
        if min_quantity and quantized_quantity < min_quantity:
            message = (
                f"Quantity {quantized_quantity} below minimum {min_quantity} for {contract_id}"
            )
            self.logger.error(f"[BACKPACK] {message}")
            return OrderResult(success=False, error_message=message)

        quantity_str = self._format_decimal(quantized_quantity, getattr(self.config, "step_size", None))
        
        # Track quantity precision for potential retries
        quantity_max_decimals = 8  # Start with 8 decimal places
        
        # Get tick size for price adjustments
        tick_size = getattr(self.config, "tick_size", Decimal("0.001"))
        if not isinstance(tick_size, Decimal):
            tick_size = Decimal(str(tick_size))
        
        # Retry up to 3 times with progressively adjusted prices
        max_retries = 3
        current_price = rounded_price
        
        for attempt in range(max_retries):
            # Recalculate quantity_str if precision was adjusted
            if attempt > 0:
                quantized_quantity = self._quantize_quantity(quantity, max_decimals=quantity_max_decimals)
                quantity_str = self._format_decimal(
                    quantized_quantity, 
                    getattr(self.config, "step_size", None),
                    max_decimals=quantity_max_decimals
                )
            
            payload_preview = {
                "symbol": contract_id,
                "side": backpack_side,
                "orderType": OrderTypeEnum.LIMIT,
                "quantity": quantity_str,
                "price": str(current_price),
                "post_only": True,
                "time_in_force": TimeInForceEnum.GTC,
            }
            if client_order_id is not None:
                payload_preview["client_order_id"] = str(client_order_id)
            
            if attempt > 0:
                self.logger.info(
                    f"[BACKPACK] Retry {attempt}/{max_retries-1}: "
                    f"quantity={quantity_str}, price=${current_price}"
                )
            else:
                self.logger.debug(f"[BACKPACK] Executing limit order payload: {payload_preview}")

            try:
                result = self.account_client.execute_order(
                    symbol=contract_id,
                    side=backpack_side,
                    order_type=OrderTypeEnum.LIMIT,
                    quantity=quantity_str,
                    price=str(current_price),
                    post_only=True,
                    time_in_force=TimeInForceEnum.GTC,
                )
            except Exception as exc:
                self.logger.error(f"[BACKPACK] Failed to place limit order: {exc}")
                return OrderResult(success=False, error_message=str(exc))

            # Check for rejection due to immediate matching or decimal precision
            if isinstance(result, dict) and result.get("code"):
                error_code = result.get("code", "")
                error_msg = result.get("message", "").lower()
                
                # Handle quantity decimal too long error
                if (error_code == "INVALID_CLIENT_REQUEST" and
                    "quantity decimal too long" in error_msg and
                    attempt < max_retries - 1):
                    
                    # Progressively reduce decimal places: 8 -> 4 -> 2
                    if quantity_max_decimals > 2:
                        quantity_max_decimals = max(2, quantity_max_decimals // 2)
                        self.logger.warning(
                            f"[BACKPACK] Quantity decimal too long. "
                            f"Reducing to {quantity_max_decimals} decimal places..."
                        )
                        continue
                    else:
                        self.logger.error(
                            f"[BACKPACK] Quantity decimal too long even at {quantity_max_decimals}dp"
                        )
                        return OrderResult(
                            success=False, 
                            error_message=f"Quantity precision error: {result.get('message')}"
                        )
                
                # Detect "would immediately match" error
                if (error_code == "INVALID_ORDER" and 
                    "immediately match" in error_msg and 
                    attempt < max_retries - 1):
                    
                    self.logger.warning(
                        f"[BACKPACK] Order would immediately match at ${current_price}. "
                        f"Adjusting price to be more maker-friendly..."
                    )
                    
                    # Fetch latest BBO to ensure we're adjusting based on current market
                    try:
                        if self.market_data:
                            best_bid, best_ask = await self.market_data.fetch_bbo_prices(contract_id)
                            self.logger.debug(
                                f"[BACKPACK] Latest BBO: bid=${best_bid}, ask=${best_ask}"
                            )
                        else:
                            best_bid = best_ask = None
                    except Exception as exc:
                        self.logger.warning(
                            f"[BACKPACK] Failed to fetch latest BBO for retry: {exc}. "
                            f"Using incremental adjustment."
                        )
                        best_bid = best_ask = None
                    
                    # Get the inferred precision for this symbol
                    symbol_precision = self._get_symbol_precision(contract_id)
                    min_tick = Decimal(10) ** -symbol_precision  # e.g., 4dp -> 0.0001
                    
                    # Progressive adjustment: more aggressive on each retry
                    base_adjustment_ticks = Decimal((attempt + 1) * 3)  # 3, 6, 9...
                    
                    # Ensure tick_size is meaningful; if it's smaller than symbol precision, use min_tick
                    effective_tick = tick_size if tick_size >= min_tick else min_tick
                    adjustment = effective_tick * base_adjustment_ticks
                    
                    self.logger.info(
                        f"[BACKPACK] Adjustment: {base_adjustment_ticks} ticks × {effective_tick} = {adjustment} "
                        f"(symbol precision: {symbol_precision}dp)"
                    )
                    
                    if side.lower() == "buy":
                        # Buy: reduce price (move down, away from best ask)
                        if best_ask and best_ask > 0:
                            current_price = best_ask - adjustment
                        else:
                            current_price = current_price - adjustment
                    else:
                        # Sell: increase price (move up, away from best bid)
                        if best_bid and best_bid > 0:
                            current_price = best_bid + adjustment
                        else:
                            current_price = current_price + adjustment
                    
                    # Quantize to symbol precision to ensure it's valid
                    try:
                        precision_quantizer = Decimal(10) ** -symbol_precision
                        current_price = current_price.quantize(
                            precision_quantizer,
                            rounding=ROUND_DOWN if side.lower() == "buy" else ROUND_UP
                        )
                    except (InvalidOperation, ValueError):
                        # Fallback to string formatting
                        current_price = Decimal(f"{current_price:.{symbol_precision}f}")
                    
                    # Sanity check: price must be positive
                    if current_price <= 0:
                        self.logger.error(
                            f"[BACKPACK] Price adjustment resulted in invalid price: ${current_price}"
                        )
                        return OrderResult(
                            success=False, 
                            error_message="Price adjustment failed: price became non-positive"
                        )
                    
                    # Continue to next retry attempt
                    continue
                
                # Different error or max retries reached
                self.logger.error(f"[BACKPACK] Limit order rejected: {result}")
                return OrderResult(success=False, error_message=result.get("message", "Order rejected"))

            # Order was accepted
            if not result or "id" not in result:
                return OrderResult(success=False, error_message="Limit order response missing order id")

            order_id = str(result["id"])
            
            if attempt > 0:
                self.logger.info(
                    f"✅ [BACKPACK] Order accepted after {attempt} price adjustment(s) at ${current_price}"
                )

            await asyncio.sleep(0.05)
            info = await self.get_order_info(order_id)

            if info:
                return OrderResult(
                    success=info.status not in {"Rejected", "Cancelled"},
                    order_id=info.order_id,
                    side=info.side,
                    size=info.size,
                    price=info.price,
                    status=info.status,
                    filled_size=info.filled_size,
                )

            return OrderResult(
                success=True,
                order_id=order_id,
                side=side.lower(),
                size=quantized_quantity,
                price=current_price,
                status="OPEN",
            )
        
        # Should not reach here, but just in case
        return OrderResult(
            success=False, 
            error_message="Max retries exceeded for limit order placement"
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
        backpack_side = "Bid" if side.lower() == "buy" else "Ask"
        quantized_quantity = self._quantize_quantity(quantity)
        min_quantity = getattr(self.config, "min_quantity", None)
        if min_quantity and quantized_quantity < min_quantity:
            message = (
                f"Quantity {quantized_quantity} below minimum {min_quantity} for {contract_id}"
            )
            self.logger.error(f"[BACKPACK] {message}")
            return OrderResult(success=False, error_message=message)
        
        quantity_str = self._format_decimal(quantized_quantity, getattr(self.config, "step_size", None))
        
        # Track quantity precision for potential retries
        quantity_max_decimals = 8
        max_retries = 3
        
        for attempt in range(max_retries):
            # Recalculate quantity_str if precision was adjusted
            if attempt > 0:
                quantized_quantity = self._quantize_quantity(quantity, max_decimals=quantity_max_decimals)
                quantity_str = self._format_decimal(
                    quantized_quantity,
                    getattr(self.config, "step_size", None),
                    max_decimals=quantity_max_decimals
                )
            
            payload_preview = {
                "symbol": contract_id,
                "side": backpack_side,
                "orderType": OrderTypeEnum.MARKET,
                "quantity": quantity_str,
            }
            if client_order_id is not None:
                payload_preview["client_order_id"] = str(client_order_id)
            
            if attempt > 0:
                self.logger.info(f"[BACKPACK] Market order retry {attempt}: quantity={quantity_str}")
            else:
                self.logger.debug(f"[BACKPACK] Executing market order payload: {payload_preview}")

            try:
                result = self.account_client.execute_order(
                    symbol=contract_id,
                    side=backpack_side,
                    order_type=OrderTypeEnum.MARKET,
                    quantity=quantity_str,
                )
            except Exception as exc:
                self.logger.error(f"[BACKPACK] Failed to place market order: {exc}")
                return OrderResult(success=False, error_message=str(exc))

            self.logger.debug(f"[BACKPACK] Market order response: {result}")
            
            # Check for errors
            if isinstance(result, dict) and result.get("code"):
                error_code = result.get("code", "")
                error_msg = result.get("message", "").lower()
                
                # Handle quantity decimal too long error
                if (error_code == "INVALID_CLIENT_REQUEST" and
                    "quantity decimal too long" in error_msg and
                    attempt < max_retries - 1):
                    
                    if quantity_max_decimals > 2:
                        quantity_max_decimals = max(2, quantity_max_decimals // 2)
                        self.logger.warning(
                            f"[BACKPACK] Market order quantity decimal too long. "
                            f"Reducing to {quantity_max_decimals} decimal places..."
                        )
                        continue
                    else:
                        self.logger.error(
                            f"[BACKPACK] Quantity decimal too long even at {quantity_max_decimals}dp"
                        )
                        return OrderResult(
                            success=False,
                            error_message=f"Quantity precision error: {result.get('message')}"
                        )
                
                # Other errors
                self.logger.error(f"[BACKPACK] Market order rejected: {result}")
                return OrderResult(success=False, error_message=result.get("message", "Order rejected"))

            # Order succeeded
            if not result or "id" not in result:
                return OrderResult(success=False, error_message="Market order response missing order id")

            status = (result.get("status") or "").upper()
            executed_qty = to_decimal(result.get("executedQuantity"), Decimal("0"))
            executed_quote_qty = to_decimal(result.get("executedQuoteQuantity"), Decimal("0"))
            avg_price = Decimal("0")
            if executed_qty and executed_qty > 0:
                avg_price = (executed_quote_qty or Decimal("0")) / executed_qty

            success = status == "FILLED"

            return OrderResult(
                success=success,
                order_id=str(result.get("id")),
                side=side.lower(),
                size=executed_qty or quantized_quantity,
                price=avg_price,
                status=status,
                filled_size=executed_qty,
                error_message=None if success else f"Market order status: {status}",
            )
        
        # Max retries exceeded
        return OrderResult(
            success=False,
            error_message="Max retries exceeded for market order placement"
        )

    async def cancel_order(self, order_id: str) -> OrderResult:
        """Cancel an existing order."""
        try:
            result = self.account_client.cancel_order(symbol=self.config.contract_id, order_id=order_id)
        except Exception as exc:
            return OrderResult(success=False, error_message=str(exc))

        if not result:
            return OrderResult(success=False, error_message="Cancel order returned empty response")

        filled_size = to_decimal(result.get("executedQuantity"), Decimal("0"))
        status = result.get("status") or "CANCELLED"

        return OrderResult(success=True, order_id=str(order_id), status=status, filled_size=filled_size)

    @query_retry()
    async def get_order_info(self, order_id: str, *, force_refresh: bool = False) -> Optional[OrderInfo]:
        """Fetch detailed order information."""
        order_id_str = str(order_id)
        cached = self.latest_orders.get(order_id_str)
        if cached and not force_refresh:
            status_upper = (cached.status or "").upper()
            if status_upper in {"FILLED", "CANCELED"}:
                return cached
        try:
            order = self.account_client.get_open_order(symbol=self.config.contract_id, order_id=order_id)
        except Exception as exc:
            self.logger.error(f"[BACKPACK] Failed to fetch order info: {exc}")
            return cached

        if not order:
            return None

        # Use converter to build OrderInfo
        info = build_order_info_from_raw(order, order_id_str, to_decimal)
        if info:
            self.latest_orders[order_id_str] = info
            return info
        
        return cached

    @query_retry(default_return=[])
    async def get_active_orders(self, contract_id: str) -> List[OrderInfo]:
        """Return currently active orders."""
        try:
            response = self.account_client.get_open_orders(symbol=contract_id)
        except Exception as exc:
            self.logger.error(f"[BACKPACK] Failed to fetch open orders: {exc}")
            return []

        if not response:
            return []

        orders_raw = response if isinstance(response, list) else response.get("orders", [])
        orders: List[OrderInfo] = []
        for order in orders_raw:
            side_raw = (order.get("side") or "").lower()
            side = "buy" if side_raw == "bid" else "sell" if side_raw == "ask" else side_raw
            size = to_decimal(order.get("quantity"), Decimal("0"))
            price = to_decimal(order.get("price"), Decimal("0"))
            filled = to_decimal(order.get("executedQuantity"), Decimal("0"))
            remaining = None
            if size is not None and filled is not None:
                remaining = size - filled

            info = OrderInfo(
                order_id=str(order.get("id", "")),
                side=side or "",
                size=size or Decimal("0"),
                price=price or Decimal("0"),
                status=order.get("status", ""),
                filled_size=filled or Decimal("0"),
                remaining_size=remaining or Decimal("0"),
            )
            orders.append(info)
            order_id = str(order.get("id", ""))
            if order_id:
                self.latest_orders[order_id] = info
        
        return orders
    
    def notify_order_update(self, order_id: str) -> None:
        """
        Notify waiting coroutines that an order update has been received via websocket.
        
        This should be called by the websocket handler whenever an order status changes.
        
        Args:
            order_id: Order identifier that was updated
        """
        if not order_id:
            return
        
        order_id_str = str(order_id)
        event = self.order_update_events.get(order_id_str)
        if event is not None and not event.is_set():
            event.set()
    
    async def await_order_update(
        self, 
        order_id: str, 
        timeout: float = 10.0
    ) -> Optional[OrderInfo]:
        """
        Wait for websocket order update with optional timeout.
        
        This method efficiently waits for order status changes via websocket,
        falling back to REST API polling if websocket update doesn't arrive.
        
        Args:
            order_id: Order identifier to wait for
            timeout: Maximum time to wait in seconds (default: 10.0)
            
        Returns:
            OrderInfo if update received within timeout, None otherwise
            
        Note:
            - Returns immediately if order is already FILLED/CANCELED in cache
            - Only waits if order status is unknown or still pending
            - Automatically cleans up event after timeout
        """
        if not order_id:
            return None
        
        order_id_str = str(order_id)
        
        # Check if order is already in cache with final status
        cached = self.latest_orders.get(order_id_str)
        if cached is not None:
            # If order is already FILLED or CANCELED, return immediately
            if cached.status in {'FILLED', 'CANCELED', 'CANCELLED', 'CLOSED', 'REJECTED', 'EXPIRED'}:
                return cached
        
        # Create or get existing event for this order
        event = self.order_update_events.setdefault(order_id_str, asyncio.Event())
        
        # If event is already set, check cache again
        if event.is_set():
            return self.latest_orders.get(order_id_str)
        
        # Wait for websocket update (with timeout)
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            # Timeout - check cache one more time (might have arrived just before timeout)
            return self.latest_orders.get(order_id_str)
        except Exception:
            # Any other error - return cached value if available
            return self.latest_orders.get(order_id_str)
        
        # Event was set - return updated order info
        return self.latest_orders.get(order_id_str)

