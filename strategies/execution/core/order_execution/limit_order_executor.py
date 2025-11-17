"""
Limit Order Executor - Handles limit order placement and fill waiting.

Places limit orders at favorable prices (maker orders) and waits for fills
with cancellation support and partial fill tracking.
"""

import asyncio
import time
from decimal import Decimal
from typing import Optional

from exchange_clients import BaseExchangeClient
from exchange_clients.base_models import CancelReason, is_retryable_cancellation, OrderInfo

from ..order_executor import ExecutionResult
from ..price_provider import PriceProvider
from helpers.unified_logger import get_core_logger


class LimitOrderExecutor:
    """
    Executes limit orders with price calculation, fill waiting, and cancellation support.
    
    Handles:
    - Price calculation (BBO fetching, offset application, tick rounding)
    - Order placement
    - Fill waiting loop with cancellation support
    - Partial fill tracking
    - Status checking (FILLED, CANCELED, timeout, retryable cancellations)
    """
    
    def __init__(self, price_provider=None):
        """
        Initialize limit order executor.
        
        Args:
            price_provider: Optional PriceProvider for BBO price retrieval
        """
        self.price_provider = price_provider or PriceProvider()
        self.logger = get_core_logger("limit_order_executor")
    
    async def execute(
        self,
        exchange_client: BaseExchangeClient,
        symbol: str,
        side: str,
        size_usd: Optional[Decimal],
        quantity: Optional[Decimal],
        timeout_seconds: float,
        price_offset_pct: Decimal,
        cancel_event: Optional[asyncio.Event] = None,
        reduce_only: bool = False
    ) -> ExecutionResult:
        """
        Place limit order at favorable price, wait for fill.
        
        Price selection (maker order):
        - Buy: best_ask - offset (better than market for us)
        - Sell: best_bid + offset (better than market for us)
        
        Args:
            exchange_client: Exchange client instance
            symbol: Trading pair (e.g., "BTC-PERP")
            side: "buy" or "sell"
            size_usd: Order size in USD
            quantity: Order quantity
            timeout_seconds: Timeout for waiting for fill
            price_offset_pct: Price improvement offset (e.g., 0.0001 for 1bp)
            cancel_event: Optional asyncio.Event to request cancellation
            reduce_only: If True, order can only reduce existing position
            
        Returns:
            ExecutionResult with fill details
        """
        try:
            best_bid, best_ask = await self.price_provider.get_bbo_prices(exchange_client, symbol)
            mid_price = (best_bid + best_ask) / 2
            
            # Calculate limit price (maker order with small improvement)
            if side == "buy":
                # Buy at ask - offset (better than market taker)
                limit_price = best_ask * (Decimal('1') - price_offset_pct)
            else:
                # Sell at bid + offset (better than market taker)
                limit_price = best_bid * (Decimal('1') + price_offset_pct)
            
            # Align price to the exchange's tick size before we derive order size or submit
            limit_price = exchange_client.round_to_tick(limit_price)
            
            order_quantity: Decimal
            if quantity is not None:
                order_quantity = Decimal(str(quantity)).copy_abs()
            else:
                if size_usd is None:
                    raise ValueError("Limit execution requires size_usd or quantity")
                order_quantity = (Decimal(str(size_usd)) / limit_price).copy_abs()

            order_quantity = exchange_client.round_to_step(order_quantity)
            if order_quantity <= Decimal("0"):
                raise ValueError("Order quantity rounded to zero")
            
            # Get the exchange-specific contract ID (normalized symbol)
            contract_id = exchange_client.resolve_contract_id(symbol)
            
            exchange_name = exchange_client.get_exchange_name()
            self.logger.info(
                f"[{exchange_name.upper()}] Placing limit {side} {symbol} (contract_id={contract_id}): "
                f"{order_quantity} @ ${limit_price} (mid: ${mid_price}, offset: {price_offset_pct * Decimal('100')}%)"
            )
            
            # Place limit order using the normalized contract_id
            order_result = await exchange_client.place_limit_order(
                contract_id=contract_id,
                quantity=float(order_quantity),
                price=float(limit_price),
                side=side,
                reduce_only=reduce_only
            )
            
            if not order_result.success:
                return ExecutionResult(
                    success=False,
                    filled=False,
                    error_message=f"Limit order placement failed: {order_result.error_message}",
                    execution_mode_used="limit_failed"
                )
            
            order_id = order_result.order_id

            partial_filled_qty = Decimal("0")
            partial_fill_price: Optional[Decimal] = None

            def _coerce_decimal(value):
                if isinstance(value, Decimal):
                    return value
                if value is None:
                    return None
                try:
                    return Decimal(str(value))
                except Exception:
                    return None

            def _update_partial_fill(quantity_candidate, price_candidate) -> None:
                nonlocal partial_filled_qty, partial_fill_price
                qty_dec = _coerce_decimal(quantity_candidate)
                if qty_dec is None or qty_dec <= partial_filled_qty:
                    return
                partial_filled_qty = qty_dec
                price_dec = _coerce_decimal(price_candidate)
                if price_dec is not None:
                    partial_fill_price = price_dec

            def _build_partial_execution_result(
                execution_mode: str,
                message: str,
                retryable: bool = False,
            ) -> ExecutionResult:
                filled_qty = partial_filled_qty if partial_filled_qty > Decimal("0") else None
                fill_price = None
                if filled_qty is not None:
                    fill_price = partial_fill_price or limit_price
                slippage_usd = Decimal("0")
                slippage_pct = Decimal("0")
                if filled_qty is not None and fill_price is not None and limit_price > 0:
                    price_delta = abs(fill_price - limit_price)
                    slippage_usd = price_delta * filled_qty
                    slippage_pct = price_delta / limit_price
                    self.logger.info(
                        f"[{exchange_name.upper()}] Limit order {order_id} {execution_mode} after partial fill "
                        f"{filled_qty} @ ${fill_price}"
                    )

                if filled_qty is not None:
                    message = f"{message} (partial fill qty={filled_qty})"

                return ExecutionResult(
                    success=filled_qty is not None,
                    filled=False,
                    fill_price=fill_price,
                    filled_quantity=filled_qty,
                    expected_price=limit_price,
                    slippage_usd=slippage_usd,
                    slippage_pct=slippage_pct,
                    execution_mode_used=execution_mode,
                    order_id=order_id,
                    error_message=message,
                    retryable=retryable,
                )

            # Wait for fill (with timeout)
            start_wait = time.time()
            
            while time.time() - start_wait < timeout_seconds:
                if cancel_event and cancel_event.is_set():
                    self.logger.info(
                        f"[{exchange_name.upper()}] Cancellation requested for limit order {order_id}"
                    )
                    cancel_result = None
                    try:
                        cancel_result = await exchange_client.cancel_order(order_id)
                    except Exception as e:
                        self.logger.error(f"Failed to cancel order {order_id}: {e}")
                    else:
                        if cancel_result:
                            _update_partial_fill(
                                getattr(cancel_result, "filled_size", None),
                                getattr(cancel_result, "price", None),
                            )

                    try:
                        order_snapshot = await exchange_client.get_order_info(order_id)
                    except Exception as e:
                        self.logger.warning(
                            f"[{exchange_name.upper()}] Failed to fetch final order snapshot for {order_id}: {e}"
                        )
                    else:
                        if order_snapshot:
                            _update_partial_fill(
                                getattr(order_snapshot, "filled_size", None),
                                getattr(order_snapshot, "price", None),
                            )

                    return _build_partial_execution_result(
                        execution_mode="limit_cancelled",
                        message="Limit order cancelled by executor",
                    )
                # Check order status
                order_info = await exchange_client.get_order_info(order_id)
                if order_info:
                    _update_partial_fill(
                        getattr(order_info, "filled_size", None),
                        getattr(order_info, "price", None),
                    )

                if order_info and order_info.status == "FILLED":
                    fill_price = Decimal(str(order_info.price))
                    filled_qty = Decimal(str(order_info.filled_size))
                    
                    exchange_name = exchange_client.get_exchange_name()
                    self.logger.info(
                        f"[{exchange_name.upper()}] Limit order filled: {filled_qty} @ ${fill_price}"
                    )
                    
                    # Calculate slippage (should be near zero for maker orders)
                    slippage_usd = abs(fill_price - limit_price) * filled_qty
                    slippage_pct = abs(fill_price - limit_price) / limit_price if limit_price > 0 else Decimal('0')
                    
                    return ExecutionResult(
                        success=True,
                        filled=True,
                        fill_price=fill_price,
                        filled_quantity=filled_qty,
                        expected_price=limit_price,
                        slippage_usd=slippage_usd,
                        slippage_pct=slippage_pct,
                        execution_mode_used="limit",
                        order_id=order_id
                    )
                
                # Check for CANCELED status early (not just at timeout)
                elif order_info and order_info.status in {"CANCELED", "CANCELLED"}:
                    cancel_reason = getattr(order_info, "cancel_reason", "") or CancelReason.UNKNOWN
                    exchange_name = exchange_client.get_exchange_name()
                    
                    # Check if it's a retryable cancellation (e.g., post-only violation)
                    if is_retryable_cancellation(cancel_reason):
                        self.logger.warning(
                            f"[{exchange_name.upper()}] Limit order cancelled due to {cancel_reason} "
                            f"(order_id={order_id}). Order will be retried with fresh BBO."
                        )
                        # Return result indicating retry is needed
                        return _build_partial_execution_result(
                            execution_mode="limit_cancelled_post_only",
                            message=f"Order cancelled: {cancel_reason}. Retryable.",
                            retryable=True,
                        )
                    else:
                        # Non-retryable cancellation (user cancelled, expired, etc.)
                        self.logger.info(
                            f"[{exchange_name.upper()}] Limit order cancelled: {cancel_reason} "
                            f"(order_id={order_id})"
                        )
                        return _build_partial_execution_result(
                            execution_mode="limit_cancelled",
                            message=f"Limit order cancelled: {cancel_reason}",
                            retryable=False,
                        )
                
                # Check more frequently near the end
                wait_interval = 0.5 if (timeout_seconds - (time.time() - start_wait)) > 5 else 0.2
                await asyncio.sleep(wait_interval)
            
            # Timeout - cancel order
            exchange_name = exchange_client.get_exchange_name()
            self.logger.warning(
                f"[{exchange_name.upper()}] Limit order timeout after {timeout_seconds}s, canceling {order_id}"
            )
            
            cancel_result = None
            try:
                cancel_result = await exchange_client.cancel_order(order_id)
            except Exception as e:
                self.logger.error(f"Failed to cancel order {order_id}: {e}")
            else:
                if cancel_result:
                    _update_partial_fill(
                        getattr(cancel_result, "filled_size", None),
                        getattr(cancel_result, "price", None),
                    )

            try:
                order_snapshot = await exchange_client.get_order_info(order_id)
            except Exception as e:
                self.logger.warning(
                    f"[{exchange_name.upper()}] Failed to fetch final order snapshot for {order_id}: {e}"
                )
            else:
                if order_snapshot:
                    _update_partial_fill(
                        getattr(order_snapshot, "filled_size", None),
                        getattr(order_snapshot, "price", None),
                    )

            return _build_partial_execution_result(
                execution_mode="limit_timeout",
                message=f"Limit order timeout after {timeout_seconds}s",
            )
        
        except Exception as e:
            # Extract exchange name for better error messages
            try:
                exchange_name = exchange_client.get_exchange_name()
            except Exception:
                exchange_name = "unknown"
            
            self.logger.error(
                f"[{exchange_name.upper()}] Limit order execution failed for {symbol}: {e}",
                exc_info=True
            )
            return ExecutionResult(
                success=False,
                filled=False,
                error_message=f"[{exchange_name}] Limit execution error: {str(e)}",
                execution_mode_used="limit_error"
            )

