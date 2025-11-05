"""
Order manager module for Aster client.

Handles order placement, cancellation, querying, and tracking.
"""

import asyncio
import time
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from typing import Any, Callable, Dict, List, Optional

from exchange_clients.base_models import OrderInfo, OrderResult, query_retry
from exchange_clients.aster.client.utils.helpers import to_decimal
from exchange_clients.aster.client.utils.converters import build_order_info_from_raw
from exchange_clients.aster.client.utils.caching import TickSizeCache
from exchange_clients.aster.common import get_aster_symbol_format, normalize_symbol


class AsterOrderManager:
    """
    Order manager for Aster exchange.
    
    Handles:
    - Order placement (limit, market)
    - Order cancellation
    - Order status queries
    - Order tracking and caching
    """
    
    def __init__(
        self,
        make_request_fn: Callable,
        config: Any,
        logger: Any,
        latest_orders: Dict[str, OrderInfo],
        tick_size_cache: TickSizeCache,
        min_order_notional: Dict[str, Decimal],
        market_data_manager: Optional[Any] = None,
        normalize_symbol_fn: Optional[Callable[[str], str]] = None,
        round_to_step_fn: Optional[Callable[[Decimal], Decimal]] = None,
        get_min_order_notional_fn: Optional[Callable[[Optional[str]], Optional[Decimal]]] = None,
    ):
        """
        Initialize order manager.
        
        Args:
            make_request_fn: Function to make authenticated API requests
            config: Trading configuration object
            logger: Logger instance
            latest_orders: Dictionary storing latest OrderInfo objects (client._latest_orders)
            tick_size_cache: Tick size cache instance
            min_order_notional: Min order notional cache dict
            market_data_manager: Optional market data manager (for BBO prices, contract attributes)
            normalize_symbol_fn: Function to normalize symbols
            round_to_step_fn: Function to round quantity to step size
            get_min_order_notional_fn: Function to get min order notional
        """
        self._make_request = make_request_fn
        self.config = config
        self.logger = logger
        self.latest_orders = latest_orders
        self.tick_size_cache = tick_size_cache
        self.min_order_notional = min_order_notional
        self.market_data = market_data_manager
        self.normalize_symbol = normalize_symbol_fn or (lambda s: s.upper())
        self.round_to_step = round_to_step_fn or (lambda q: q)
        self.get_min_order_notional = get_min_order_notional_fn or (lambda s: None)
    
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
        Place a limit order at a specific price on Aster.
        
        Args:
            contract_id: Contract identifier (can be normalized symbol or full contract_id)
            quantity: Order quantity
            price: Limit price
            side: 'buy' or 'sell'
            reduce_only: If True, order can only reduce existing position (bypasses min notional)
            
        Returns:
            OrderResult with order details
        """
        # Convert inputs to Decimal to avoid float arithmetic errors
        price = Decimal(str(price))
        quantity = Decimal(str(quantity))
        
        # ✅ CRITICAL FIX: Normalize contract_id for Aster (handles multi-symbol trading)
        # If contract_id doesn't end with USDT, add it (e.g., "PROVE" → "PROVEUSDT")
        if not contract_id.upper().endswith("USDT"):
            normalized_contract_id = get_aster_symbol_format(contract_id)
            self.logger.debug(f"Normalized contract_id: '{contract_id}' → '{normalized_contract_id}'")
        else:
            normalized_contract_id = contract_id.upper()
        
        self.logger.debug(f"Using contract_id for order: '{normalized_contract_id}'")
        
        # Round quantity to step size (e.g., 941.8750094 → 941.875 or 941 depending on stepSize)
        rounded_quantity = self.round_to_step(quantity)
        
        self.logger.debug(
            f"Rounded quantity: {quantity} → {rounded_quantity} "
            f"(step_size={getattr(self.config, 'step_size', 'unknown')})"
        )
        
        # Round price to tick_size precision to satisfy: (price - minPrice) % tickSize == 0
        # Look up symbol-specific tick_size from cache (for multi-symbol trading)
        tick_size = self.tick_size_cache.get(normalized_contract_id) or self.tick_size_cache.get(contract_id)
        if not tick_size:
            # Fallback to self.config.tick_size if cache miss
            tick_size = getattr(self.config, 'tick_size', None)
        
        # If still no tick_size, fetch contract attributes to populate cache
        if not tick_size and self.market_data:
            self.logger.warning(
                f"⚠️  [ASTER] tick_size not cached for {normalized_contract_id}, fetching contract attributes..."
            )
            try:
                # Extract ticker from normalized_contract_id (e.g., "AVNTUSDT" -> "AVNT")
                ticker = normalize_symbol(normalized_contract_id)
                
                # Temporarily update config ticker to fetch the right contract
                original_ticker = self.config.ticker
                self.config.ticker = ticker
                
                # Fetch contract attributes (this will populate the cache)
                await self.market_data.get_contract_attributes()
                
                # Restore original ticker
                self.config.ticker = original_ticker
                
                # Try cache lookup again
                tick_size = self.tick_size_cache.get(normalized_contract_id) or self.tick_size_cache.get(ticker.upper())
                
                if tick_size:
                    self.logger.info(f"✅ [ASTER] Fetched tick_size for {normalized_contract_id}: {tick_size}")
            except Exception as e:
                self.logger.error(f"❌ [ASTER] Failed to fetch tick_size for {normalized_contract_id}: {e}")
        
        if tick_size:
            # Round price to nearest tick_size increment
            # Formula: price = round(price / tick_size) * tick_size
            price_in_ticks = (price / tick_size).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
            rounded_price = price_in_ticks * tick_size
            self.logger.debug(
                f"Rounded price to tick_size: {price} → {rounded_price} "
                f"(tick_size={tick_size}, symbol={normalized_contract_id})"
            )
        else:
            # Fallback: no tick_size available, use price as-is and let exchange reject if invalid
            rounded_price = price
            self.logger.warning(
                f"No tick_size available for {normalized_contract_id}, using price as-is: {rounded_price}"
            )

        # 🛡️ DEFENSIVE CHECK: Min notional should already be validated in pre-flight checks
        # This is a last-resort safety net to catch bugs where pre-flight was bypassed
        # ⚠️ SKIP for reduce_only orders (closing positions) - these may be below min notional
        if not reduce_only:
            min_notional = self.get_min_order_notional(normalized_contract_id) or self.get_min_order_notional(getattr(self.config, "ticker", None))
            if min_notional is not None:
                order_notional = rounded_quantity * rounded_price
                if order_notional < min_notional:
                    # This should NEVER happen if pre-flight checks ran correctly
                    message = (
                        f"[ASTER] UNEXPECTED: Order notional ${order_notional} below minimum ${min_notional}. "
                        f"This should have been caught in pre-flight checks!"
                    )
                    self.logger.error(message)
                    raise ValueError(message)
        else:
            # Reduce-only order - allowed to be below min notional (closing dust positions)
            order_notional = rounded_quantity * rounded_price
            self.logger.debug(
                f"[ASTER] Reduce-only order: ${order_notional:.2f} notional "
                f"(min notional check skipped for position closing)"
            )

        # Place limit order with post-only (GTX) for maker fees
        order_data = {
            'symbol': normalized_contract_id,  # Aster format (e.g., "PROVEUSDT")
            'side': side.upper(),
            'type': 'LIMIT',
            'quantity': str(rounded_quantity),
            'price': str(rounded_price),
            'timeInForce': 'GTX'  # GTX is Good Till Crossing (Post Only)
        }

        # Add reduceOnly flag if this is a closing operation
        if reduce_only:
            order_data['reduceOnly'] = 'true'
        if client_order_id is not None:
            order_data['newClientOrderId'] = str(client_order_id)
        
        self.logger.debug(f"Placing {side.upper()} limit order: {rounded_quantity} @ {rounded_price}")

        try:
            result = await self._make_request('POST', '/fapi/v1/order', data=order_data)
        except Exception as e:
            self.logger.error(
                f"Failed to place limit order for {normalized_contract_id} "
                f"({side.upper()}, qty={quantity}, price={price}): {e}"
            )
            raise
        order_status = result.get('status', '')
        order_id = result.get('orderId', '')
        order_id_str = str(order_id)

        if order_id_str and order_id_str not in self.latest_orders:
            self.latest_orders[order_id_str] = OrderInfo(
                order_id=order_id_str,
                side=side,
                size=quantity,
                price=price,
                status=order_status or 'NEW',
                filled_size=Decimal("0"),
                remaining_size=quantity,
            )

        # Wait briefly to confirm order status using WebSocket cache (with REST fallback)
        start_time = time.time()
        order_status_upper = (order_status or '').upper()
        final_info: Optional[OrderInfo] = None
        while order_status_upper in {'NEW', 'PARTIALLY_FILLED', 'OPEN'} and time.time() - start_time < 2:
            cached = self.latest_orders.get(order_id_str)
            if cached:
                order_status_upper = (cached.status or '').upper()
                final_info = cached
                if order_status_upper in {'FILLED', 'CANCELED', 'EXPIRED', 'REJECTED', 'PARTIALLY_FILLED'}:
                    break
            await asyncio.sleep(0.1)
            order_info = await self.get_order_info(order_id)
            if order_info is not None:
                final_info = order_info
                order_status_upper = (order_info.status or '').upper()
                if order_status_upper in {'FILLED', 'CANCELED', 'EXPIRED', 'REJECTED', 'PARTIALLY_FILLED'}:
                    break

        if final_info is None:
            final_info = self.latest_orders.get(order_id_str)

        if final_info is not None:
            order_status_upper = (final_info.status or '').upper()

        if order_status_upper in {'NEW', 'PARTIALLY_FILLED', 'OPEN'}:
            return OrderResult(
                success=True, 
                order_id=order_id, 
                side=side, 
                size=quantity, 
                price=price, 
                status='OPEN'
            )
        elif order_status_upper == 'FILLED':
            return OrderResult(
                success=True, 
                order_id=order_id, 
                side=side, 
                size=quantity, 
                price=price, 
                status='FILLED'
            )
        elif order_status_upper in {'CANCELED', 'EXPIRED', 'REJECTED'}:
            return OrderResult(
                success=False, 
                error_message=f'Limit order did not remain open: {order_status_upper}'
            )
        else:
            return OrderResult(
                success=False, 
                error_message=f'Unknown order status: {order_status_upper or order_status}'
            )

    async def place_market_order(
        self,
        contract_id: str,
        quantity: Decimal,
        side: str,
        reduce_only: bool = False,
        client_order_id: Optional[int] = None,
    ) -> OrderResult:
        """
        Place a market order on Aster (true market order for immediate execution).
        
        Args:
            contract_id: Contract identifier
            quantity: Order quantity
            side: 'buy' or 'sell'
            reduce_only: If True, order can only reduce existing position (bypasses min notional)
        """
        try:
            # Convert inputs to Decimal to avoid float arithmetic errors
            quantity = Decimal(str(quantity))
            
            # ✅ CRITICAL FIX: Normalize contract_id for Aster (handles multi-symbol trading)
            # If contract_id doesn't end with USDT, add it (e.g., "MON" → "MONUSDT")
            if not contract_id.upper().endswith("USDT"):
                normalized_contract_id = get_aster_symbol_format(contract_id)
                self.logger.debug(f"Normalized contract_id: '{contract_id}' → '{normalized_contract_id}'")
            else:
                normalized_contract_id = contract_id.upper()
            
            self.logger.debug(
                f"🔍 [ASTER] Using contract_id for market order: '{normalized_contract_id}'"
            )
            
            # Validate side
            if side.lower() not in ['buy', 'sell']:
                return OrderResult(success=False, error_message=f'Invalid side: {side}')

            # Round quantity to step size
            rounded_quantity = self.round_to_step(quantity)
            
            self.logger.debug(
                f"📐 [ASTER] Rounded quantity: {quantity} → {rounded_quantity}"
            )

            # Fetch BBO with explicit error handling
            if not self.market_data:
                return OrderResult(
                    success=False, 
                    error_message="Market data manager not available for BBO prices"
                )
            
            try:
                best_bid, best_ask = await self.market_data.fetch_bbo_prices(normalized_contract_id)
                self.logger.info(
                    f"📊 [ASTER] Market order BBO check: bid={best_bid}, ask={best_ask}"
                )
            except Exception as bbo_error:
                self.logger.error(
                    f"❌ [ASTER] Failed to fetch BBO for market order: {bbo_error}"
                )
                return OrderResult(
                    success=False, 
                    error_message=f"Failed to fetch market prices: {bbo_error}"
                )
            
            if best_bid <= 0 or best_ask <= 0:
                self.logger.error(
                    f"❌ [ASTER] Invalid BBO prices for market order: bid={best_bid}, ask={best_ask}"
                )
                return OrderResult(
                    success=False, 
                    error_message=f"Invalid bid/ask prices: bid={best_bid}, ask={best_ask}"
                )

            expected_price = best_ask if side.lower() == 'buy' else best_bid

            # 🛡️ DEFENSIVE CHECK: Min notional should already be validated in pre-flight checks
            # ⚠️ SKIP for reduce_only orders (closing positions) - these may be below min notional
            if not reduce_only:
                min_notional = self.get_min_order_notional(normalized_contract_id) or self.get_min_order_notional(getattr(self.config, "ticker", None))
                if min_notional is not None and expected_price > 0:
                    order_notional = rounded_quantity * expected_price
                    if order_notional < min_notional:
                        message = (
                            f"[ASTER] UNEXPECTED: Market order notional ${order_notional} below minimum ${min_notional}. "
                            f"This should have been caught in pre-flight checks!"
                        )
                        self.logger.error(message)
                        return OrderResult(success=False, error_message=message)
            else:
                # Reduce-only order - allowed to be below min notional (closing dust positions)
                order_notional = rounded_quantity * expected_price
                self.logger.debug(
                    f"[ASTER] Reduce-only market order: ${order_notional:.2f} notional "
                    f"(min notional check skipped for position closing)"
                )

            # Place the market order
            order_data = {
                'symbol': normalized_contract_id,  # Aster format (e.g., "MONUSDT")
                'side': side.upper(),
                'type': 'MARKET',
                'quantity': str(rounded_quantity)
            }
            
            # Add reduceOnly flag if this is a closing operation
            if reduce_only:
                order_data['reduceOnly'] = 'true'
            if client_order_id is not None:
                order_data['newClientOrderId'] = str(client_order_id)
            
            self.logger.info(
                f"📤 [ASTER] Placing market {side.upper()} order: {rounded_quantity} @ ~${expected_price}"
            )

            result = await self._make_request('POST', '/fapi/v1/order', data=order_data)
            order_status = result.get('status', '')
            order_id = result.get('orderId', '')

            # Wait for order to fill
            start_time = time.time()
            order_info = None
            while order_status != 'FILLED' and time.time() - start_time < 10:
                await asyncio.sleep(0.2)
                order_info = await self.get_order_info(order_id)
                if order_info is not None:
                    order_status = order_info.status

            if order_status == 'FILLED':
                self.logger.info(
                    f"✅ [ASTER] Market order filled: {order_id}"
                )
                return OrderResult(
                    success=True,
                    order_id=order_id,
                    side=side.lower(),
                    size=quantity,
                    price=order_info.price if order_info else Decimal(0),
                    status='FILLED'
                )
            else:
                self.logger.error(
                    f"❌ [ASTER] Market order not filled: status={order_status}"
                )
                return OrderResult(
                    success=False,
                    error_message=f'Market order failed with status: {order_status}'
                )
                
        except Exception as e:
            self.logger.error(f"❌ [ASTER] Error placing market order: {e}")
            import traceback
            self.logger.debug(f"Traceback: {traceback.format_exc()}")
            return OrderResult(success=False, error_message=str(e))

    async def cancel_order(self, order_id: str, contract_id: str) -> OrderResult:
        """Cancel an order with Aster."""
        try:
            result = await self._make_request('DELETE', '/fapi/v1/order', {
                'symbol': contract_id,
                'orderId': order_id
            })

            if 'orderId' in result:
                order_id_str = str(result.get('orderId', order_id))
                filled_size = to_decimal(result.get('executedQty'), Decimal("0"))
                status = result.get('status') or 'CANCELED'

                cached = self.latest_orders.get(order_id_str)
                if cached:
                    remaining_size = cached.size - (filled_size or Decimal("0"))
                    if remaining_size < Decimal("0"):
                        remaining_size = Decimal("0")
                    updated = OrderInfo(
                        order_id=cached.order_id,
                        side=cached.side,
                        size=cached.size,
                        price=cached.price,
                        status=status,
                        filled_size=filled_size or cached.filled_size,
                        remaining_size=remaining_size,
                    )
                else:
                    updated = OrderInfo(
                        order_id=order_id_str,
                        side="",
                        size=filled_size or Decimal("0"),
                        price=Decimal("0"),
                        status=status,
                        filled_size=filled_size or Decimal("0"),
                        remaining_size=Decimal("0"),
                    )
                self.latest_orders[order_id_str] = updated

                return OrderResult(success=True, order_id=order_id_str, status=status, filled_size=filled_size)
            else:
                return OrderResult(success=False, error_message=result.get('msg', 'Unknown error'))

        except Exception as e:
            return OrderResult(success=False, error_message=str(e))

    @query_retry()
    async def get_order_info(self, order_id: str, contract_id: str, *, force_refresh: bool = False) -> Optional[OrderInfo]:
        """Get order information from Aster."""
        order_id_str = str(order_id)
        cached = self.latest_orders.get(order_id_str)
        if not force_refresh and cached is not None:
            status_upper = (cached.status or "").upper()
            if status_upper in {"FILLED", "CANCELED", "REJECTED", "EXPIRED"}:
                return cached

        result = await self._make_request('GET', '/fapi/v1/order', {
            'symbol': contract_id,
            'orderId': order_id
        })

        # Use converter to build OrderInfo
        info = build_order_info_from_raw(result, order_id_str)
        if info:
            self.latest_orders[order_id_str] = info
            return info
        
        return cached

    @query_retry(default_return=[])
    async def get_active_orders(self, contract_id: str) -> List[OrderInfo]:
        """Get active orders for a contract from Aster."""
        result = await self._make_request('GET', '/fapi/v1/openOrders', {'symbol': contract_id})

        orders = []
        for order in result:
            order_id_str = str(order.get('orderId', ''))
            size = to_decimal(order.get('origQty'), Decimal("0"))
            filled = to_decimal(order.get('executedQty'), Decimal("0"))
            remaining = None
            if size is not None and filled is not None:
                remaining = size - filled

            info = OrderInfo(
                order_id=order_id_str,
                side=(order.get('side', '') or '').lower(),
                size=size or Decimal("0"),
                price=to_decimal(order.get('price'), Decimal("0")) or Decimal("0"),
                status=order.get('status', ''),
                filled_size=filled or Decimal("0"),
                remaining_size=remaining or Decimal("0"),
            )
            orders.append(info)
            if order_id_str:
                self.latest_orders[order_id_str] = info

        return orders

