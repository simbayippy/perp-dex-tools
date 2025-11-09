"""
Order Executor - Smart order placement with tiered execution.

Provides intelligent order execution with multiple modes:
- limit_only: Place limit order, wait for fill
- limit_with_fallback: Try limit first, fallback to market if timeout
- market_only: Immediate market order
- adaptive: Choose mode based on liquidity analysis

Key features:
- Automatic fallback from limit to market
- Timeout handling
- Slippage tracking
- Execution quality metrics
"""

from typing import Dict, Optional
from decimal import Decimal
from enum import Enum
from dataclasses import dataclass
import time
import asyncio
from helpers.unified_logger import get_core_logger
from exchange_clients import BaseExchangeClient
from exchange_clients.base_models import CancelReason, is_retryable_cancellation, OrderInfo

logger = get_core_logger("order_executor")


class ExecutionMode(Enum):
    """
    Execution modes for order placement.
    
    """
    LIMIT_ONLY = "limit_only"
    LIMIT_WITH_FALLBACK = "limit_with_fallback"
    MARKET_ONLY = "market_only"
    ADAPTIVE = "adaptive"


@dataclass
class ExecutionResult:
    """
    Result of order execution.
    
    Contains all metrics needed for quality analysis.
    """
    success: bool
    filled: bool
    
    # Price & quantity
    fill_price: Optional[Decimal] = None
    filled_quantity: Optional[Decimal] = None
    
    # Quality metrics
    expected_price: Optional[Decimal] = None
    slippage_usd: Decimal = Decimal('0')
    slippage_pct: Decimal = Decimal('0')
    
    # Execution details
    execution_mode_used: str = ""
    execution_time_ms: int = 0
    
    # Error handling
    error_message: Optional[str] = None
    order_id: Optional[str] = None
    
    # Retry handling
    retryable: bool = False  # True if order failure is retryable (e.g., post-only violation)


class OrderExecutor:
    """
    Intelligent order executor with tiered execution strategy.
    
    ⭐ Inspired by Hummingbot's PositionExecutor ⭐
    
    Key Patterns:
    1. Limit orders for better pricing (maker orders)
    2. Market fallback if limit times out
    3. Configurable timeout per order
    4. Automatic price selection (mid-market with buffer)
    
    Example:
        executor = OrderExecutor()
        
        # Try limit, fallback to market after 30s
        result = await executor.execute_order(
            exchange_client=client,
            symbol="BTC-PERP",
            side="buy",
            size_usd=Decimal("1000"),
            mode=ExecutionMode.LIMIT_WITH_FALLBACK,
                    timeout_seconds=40.0
        )
        
        if result.filled:
            print(f"Filled at ${result.fill_price}, slippage: {result.slippage_pct}%")
    """
    
    DEFAULT_LIMIT_PRICE_OFFSET_PCT = Decimal("0.0001")  # 1 basis point

    def __init__(
        self,
        default_timeout: float = 40.0,
        price_provider = None,  # Optional PriceProvider for shared BBO lookups
        default_limit_price_offset_pct: Decimal = DEFAULT_LIMIT_PRICE_OFFSET_PCT
    ):
        """
        Initialize order executor.
        
        Args:
            default_timeout: Default timeout for limit orders (seconds)
            price_provider: Optional PriceProvider for retrieving shared BBO data
            default_limit_price_offset_pct: Default maker improvement for limit orders
        """
        self.default_timeout = default_timeout
        self.price_provider = price_provider
        self.default_limit_price_offset_pct = default_limit_price_offset_pct
        self.logger = get_core_logger("order_executor")
    
    async def execute_order(
        self,
        exchange_client: BaseExchangeClient,
        symbol: str,
        side: str,
        size_usd: Optional[Decimal] = None,
        quantity: Optional[Decimal] = None,
        mode: ExecutionMode = ExecutionMode.LIMIT_WITH_FALLBACK,
        timeout_seconds: Optional[float] = None,
        limit_price_offset_pct: Optional[Decimal] = None,
        cancel_event: Optional[asyncio.Event] = None,
        reduce_only: bool = False
    ) -> ExecutionResult:
        """
        Execute order with intelligent mode selection.
        
        Args:
            exchange_client: Exchange client instance
            symbol: Trading pair (e.g., "BTC-PERP")
            side: "buy" or "sell"
            size_usd: Order size in USD
            mode: Execution mode
            timeout_seconds: Timeout for limit orders (uses default if None)
            limit_price_offset_pct: Price improvement for limit orders (None = executor default)
            cancel_event: Optional asyncio.Event to request cancellation (only respected for limit orders)
        
        Returns:
            ExecutionResult with all execution details
        """
        if size_usd is None and quantity is None:
            raise ValueError("OrderExecutor.execute_order requires size_usd or quantity")

        start_time = time.time()
        timeout = timeout_seconds or self.default_timeout
        
        # Get exchange name for better logging
        try:
            exchange_name = exchange_client.get_exchange_name()
        except Exception:
            exchange_name = "unknown"
        
        # Choose emoji based on side
        emoji = "🟢" if side == "buy" else "🔴"
        
        size_components = []
        if size_usd is not None:
            size_components.append(f"${size_usd}")
        if quantity is not None:
            size_components.append(f"qty={quantity}")
        size_descriptor = " ".join(size_components)

        self.logger.info(
            f"{emoji} [{exchange_name.upper()}] Executing {side} {symbol} ({size_descriptor}) in mode {mode.value}"
        )
        
        try:
            offset_pct = (
                limit_price_offset_pct
                if limit_price_offset_pct is not None
                else self.default_limit_price_offset_pct
            )
            if not isinstance(offset_pct, Decimal):
                offset_pct = Decimal(str(offset_pct))

            if mode == ExecutionMode.MARKET_ONLY:
                result = await self._execute_market(
                    exchange_client, symbol, side, size_usd, quantity, reduce_only
                )
            
            elif mode == ExecutionMode.LIMIT_ONLY:
                result = await self._execute_limit(
                    exchange_client,
                    symbol,
                    side,
                    size_usd,
                    quantity,
                    timeout,
                    offset_pct,
                    cancel_event,
                    reduce_only,
                )
            
            elif mode == ExecutionMode.LIMIT_WITH_FALLBACK:
                # Try limit first
                result = await self._execute_limit(
                    exchange_client,
                    symbol,
                    side,
                    size_usd,
                    quantity,
                    timeout,
                    offset_pct,
                    cancel_event,
                    reduce_only,
                )
                
                if not result.filled:
                    # Fallback to market
                    self.logger.info(
                        f"Limit order timeout for {symbol}, falling back to market"
                    )
                    result = await self._execute_market(
                        exchange_client, symbol, side, size_usd, quantity, reduce_only
                    )
                    result.execution_mode_used = "market_fallback"
            
            elif mode == ExecutionMode.ADAPTIVE:
                # Use liquidity analyzer to decide (will implement later)
                # For now, default to limit_with_fallback
                result = await self.execute_order(
                    exchange_client=exchange_client,
                    symbol=symbol,
                    side=side,
                    size_usd=size_usd,
                    quantity=quantity,
                    mode=ExecutionMode.LIMIT_WITH_FALLBACK,
                    timeout_seconds=timeout,
                    limit_price_offset_pct=offset_pct,
                    cancel_event=cancel_event,
                )
            
            else:
                raise ValueError(f"Unknown execution mode: {mode}")
            
            # Add execution time
            result.execution_time_ms = int((time.time() - start_time) * 1000)
            
            return result
        
        except Exception as e:
            self.logger.error(f"Order execution failed: {e}", exc_info=True)
            return ExecutionResult(
                success=False,
                filled=False,
                error_message=str(e),
                execution_time_ms=int((time.time() - start_time) * 1000)
            )
    
    async def _execute_limit(
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
        
        """
        try:
            best_bid, best_ask = await self._fetch_bbo_prices(exchange_client, symbol)
            mid_price = (best_bid + best_ask) / 2
            
            # Calculate limit price (maker order with small improvement)
            if side == "buy":
                # Buy at ask - offset (better than market taker)
                limit_price = best_ask * (Decimal('1') - price_offset_pct)
            else:
                # Sell at bid + offset (better than market taker)
                limit_price = best_bid * (Decimal('1') + price_offset_pct)
            
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
                
                # NEW: Check for CANCELED status early (not just at timeout)
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
    
    async def _execute_market(
        self,
        exchange_client: BaseExchangeClient,
        symbol: str,
        side: str,
        size_usd: Optional[Decimal],
        quantity: Optional[Decimal],
        reduce_only: bool = False
    ) -> ExecutionResult:
        """
        Execute market order immediately.
        
        Args:
            reduce_only: If True, order can only reduce existing position (bypasses min notional)
        """
        try:
            # Get current price for quantity calculation & slippage tracking
            best_bid, best_ask = await self._fetch_bbo_prices(exchange_client, symbol)
            mid_price = (best_bid + best_ask) / 2
            expected_price = best_ask if side == "buy" else best_bid
            
            if quantity is not None:
                order_quantity = Decimal(str(quantity)).copy_abs()
            else:
                if size_usd is None:
                    raise ValueError("Market execution requires size_usd or quantity")
                order_quantity = (Decimal(str(size_usd)) / expected_price).copy_abs()

            order_quantity = exchange_client.round_to_step(order_quantity)
            if order_quantity <= Decimal("0"):
                raise ValueError("Order quantity rounded to zero")
            
            # Get the exchange-specific contract ID (normalized symbol)
            contract_id = exchange_client.resolve_contract_id(symbol)
            
            exchange_name = exchange_client.get_exchange_name()
            self.logger.info(
                f"[{exchange_name.upper()}] Placing market {side} {symbol} (contract_id={contract_id}): "
                f"{order_quantity} @ ~${expected_price}"
            )
            
            # Place market order using the normalized contract_id
            result = await exchange_client.place_market_order(
                contract_id=contract_id,
                quantity=float(order_quantity),
                side=side,
                reduce_only=reduce_only
            )
            
            if not result.success:
                return ExecutionResult(
                    success=False,
                    filled=False,
                    error_message=f"Market order failed: {result.error_message}",
                    execution_mode_used="market_failed"
                )
            
            order_id = result.order_id if hasattr(result, 'order_id') and result.order_id else None
            
            # Wait for order confirmation via websocket (with REST fallback)
            # Market orders should execute quickly, but we need to confirm they actually filled
            order_info = await self._wait_for_market_order_confirmation(
                exchange_client=exchange_client,
                order_id=order_id,
                expected_quantity=order_quantity,
                timeout_seconds=10.0
            )
            
            if order_info is None:
                # Timeout or no order info available - check via REST as final fallback
                if order_id:
                    try:
                        order_info = await exchange_client.get_order_info(order_id, force_refresh=True)
                    except Exception as e:
                        self.logger.warning(
                            f"[{exchange_name.upper()}] Failed to fetch order info for {order_id}: {e}"
                        )
            
            # Check order status
            if order_info is None:
                # No order info available - this is an error case
                return ExecutionResult(
                    success=False,
                    filled=False,
                    error_message="Market order placed but no order info available",
                    execution_mode_used="market_no_info",
                    order_id=order_id
                )
            
            status = order_info.status.upper()
            
            # Check for cancellation (market orders can be canceled by exchange)
            if status in {'CANCELED', 'CANCELLED', 'REJECTED', 'EXPIRED'}:
                cancel_reason = getattr(order_info, "cancel_reason", "") or "unknown"
                exchange_name = exchange_client.get_exchange_name()
                self.logger.error(
                    f"[{exchange_name.upper()}] Market order canceled: {order_id} | "
                    f"Status: {status} | Reason: {cancel_reason}"
                )
                return ExecutionResult(
                    success=False,
                    filled=False,
                    error_message=f"Market order canceled: {status} ({cancel_reason})",
                    execution_mode_used="market_canceled",
                    order_id=order_id,
                    retryable=False  # Market orders don't have post-only violations, so not retryable
                )
            
            # Check if order is filled
            if status not in {'FILLED', 'CLOSED'}:
                # Order is still pending or in unknown state
                exchange_name = exchange_client.get_exchange_name()
                self.logger.warning(
                    f"[{exchange_name.upper()}] Market order not filled: {order_id} | "
                    f"Status: {status}"
                )
                return ExecutionResult(
                    success=False,
                    filled=False,
                    error_message=f"Market order not filled: status={status}",
                    execution_mode_used="market_not_filled",
                    order_id=order_id
                )
            
            # Order is filled - extract fill details
            fill_price = Decimal(str(order_info.price)) if order_info.price > 0 else expected_price
            filled_qty = Decimal(str(order_info.filled_size)) if order_info.filled_size > 0 else order_quantity
            
            # Calculate slippage
            slippage_usd = abs(fill_price - expected_price) * filled_qty
            slippage_pct = abs(fill_price - expected_price) / expected_price if expected_price > 0 else Decimal('0')
            
            exchange_name = exchange_client.get_exchange_name()
            self.logger.info(
                f"[{exchange_name.upper()}] Market order filled: {filled_qty} @ ${fill_price} "
                f"(slippage: ${slippage_usd:.2f} / {slippage_pct*100:.3f}%)"
            )
            
            return ExecutionResult(
                success=True,
                filled=True,
                fill_price=fill_price,
                filled_quantity=filled_qty,
                expected_price=expected_price,
                slippage_usd=slippage_usd,
                slippage_pct=slippage_pct,
                execution_mode_used="market",
                order_id=order_id
            )
        
        except Exception as e:
            # Extract exchange name for better error messages
            try:
                exchange_name = exchange_client.get_exchange_name()
            except Exception:
                exchange_name = "unknown"
            
            self.logger.error(
                f"[{exchange_name.upper()}] Market order execution failed for {symbol}: {e}",
                exc_info=True
            )
            return ExecutionResult(
                success=False,
                filled=False,
                error_message=f"[{exchange_name}] Market execution error: {str(e)}",
                execution_mode_used="market_error"
            )
    
    async def _wait_for_market_order_confirmation(
        self,
        exchange_client: BaseExchangeClient,
        order_id: Optional[str],
        expected_quantity: Decimal,
        timeout_seconds: float = 10.0
    ) -> Optional[OrderInfo]:
        """
        Wait for market order confirmation via websocket (with REST fallback).
        
        This method waits specifically for FILLED status, not just any update.
        Market orders may send OPEN status first, but we need to wait for FILLED.
        
        The issue: await_order_update only waits for the FIRST websocket update (OPEN),
        but market orders need to wait for FILLED status which comes later.
        
        Solution: Poll cache/REST directly in a loop until FILLED or timeout.
        
        Args:
            exchange_client: Exchange client instance
            order_id: Order identifier (None if not available)
            expected_quantity: Expected order quantity (for validation)
            timeout_seconds: Maximum time to wait in seconds
            
        Returns:
            OrderInfo if FILLED status received, None if timeout or error
        """
        if not order_id:
            # No order ID - fallback to REST polling
            return await self._poll_order_status_rest(
                exchange_client, None, expected_quantity, timeout_seconds
            )
        
        start_time = time.time()
        poll_interval = 0.2  # Poll every 200ms
        max_polls = int(timeout_seconds / poll_interval) + 1
        
        # First, wait for initial order confirmation (OPEN status)
        # This ensures the order was actually placed
        if hasattr(exchange_client, 'await_order_update'):
            try:
                initial_info = await exchange_client.await_order_update(order_id, timeout=min(timeout_seconds, 2.0))
                if initial_info:
                    status = initial_info.status.upper()
                    # If already filled, return immediately
                    if status in {'FILLED', 'CLOSED'}:
                        return initial_info
                    # If canceled/rejected, return immediately (final state)
                    if status in {'CANCELED', 'CANCELLED', 'REJECTED', 'EXPIRED'}:
                        return initial_info
            except Exception as e:
                exchange_name = exchange_client.get_exchange_name()
                self.logger.debug(
                    f"[{exchange_name.upper()}] Initial websocket wait failed for {order_id}: {e}"
                )
        
        # Now poll until FILLED status or timeout
        # We need to check cache/REST because await_order_update only waits for first update
        for _ in range(max_polls):
            elapsed = time.time() - start_time
            if elapsed >= timeout_seconds:
                break
            
            # Check cache via get_order_info (which checks cache first, then REST)
            try:
                order_info = await exchange_client.get_order_info(order_id, force_refresh=False)
                if order_info:
                    status = order_info.status.upper()
                    # If filled, return immediately
                    if status in {'FILLED', 'CLOSED'}:
                        return order_info
                    # If canceled/rejected, return immediately (final state)
                    if status in {'CANCELED', 'CANCELLED', 'REJECTED', 'EXPIRED'}:
                        return order_info
                    # Otherwise (OPEN, PARTIALLY_FILLED), continue polling
            except Exception as e:
                exchange_name = exchange_client.get_exchange_name()
                self.logger.debug(
                    f"[{exchange_name.upper()}] Poll check failed for {order_id}: {e}"
                )
            
            # Small delay before next check
            await asyncio.sleep(poll_interval)
        
        # Final check via REST API with force_refresh (might have filled between polls)
        try:
            order_info = await exchange_client.get_order_info(order_id, force_refresh=True)
            if order_info:
                status = order_info.status.upper()
                if status in {'FILLED', 'CLOSED', 'CANCELED', 'CANCELLED', 'REJECTED', 'EXPIRED'}:
                    return order_info
        except Exception as e:
            exchange_name = exchange_client.get_exchange_name()
            self.logger.debug(
                f"[{exchange_name.upper()}] Final REST check failed for {order_id}: {e}"
            )
        
        # Fallback to REST polling (original behavior)
        return await self._poll_order_status_rest(
            exchange_client, order_id, expected_quantity, timeout_seconds
        )
    
    async def _poll_order_status_rest(
        self,
        exchange_client: BaseExchangeClient,
        order_id: Optional[str],
        expected_quantity: Decimal,
        timeout_seconds: float = 10.0
    ) -> Optional[OrderInfo]:
        """
        Poll order status via REST API (fallback when websocket not available).
        
        Similar to Aster's approach - polls REST API until order is filled/canceled
        or timeout is reached.
        
        Args:
            exchange_client: Exchange client instance
            order_id: Order identifier (None if not available)
            expected_quantity: Expected order quantity
            timeout_seconds: Maximum time to wait in seconds
            
        Returns:
            OrderInfo if status check succeeds, None if timeout or error
        """
        if not order_id:
            # No order ID - can't poll
            return None
        
        start_time = time.time()
        poll_interval = 0.2  # Poll every 200ms (like Aster)
        
        while time.time() - start_time < timeout_seconds:
            try:
                order_info = await exchange_client.get_order_info(order_id, force_refresh=True)
                if order_info:
                    status = order_info.status.upper()
                    # Return if order reached final state
                    if status in {'FILLED', 'CANCELED', 'CANCELLED', 'CLOSED', 'REJECTED', 'EXPIRED'}:
                        return order_info
            except Exception as e:
                exchange_name = exchange_client.get_exchange_name()
                self.logger.debug(
                    f"[{exchange_name.upper()}] Error polling order status for {order_id}: {e}"
                )
            
            await asyncio.sleep(poll_interval)
        
        # Timeout - return None (caller will handle)
        return None
    
    async def _fetch_bbo_prices(
        self,
        exchange_client: BaseExchangeClient,
        symbol: str
    ) -> tuple[Decimal, Decimal]:
        """
        Fetch best bid/offer prices using the configured price provider or exchange client.
        
        Returns:
            (best_bid, best_ask) as Decimals
        """
        try:
            if self.price_provider:
                bid, ask = await self.price_provider.get_bbo_prices(
                    exchange_client=exchange_client,
                    symbol=symbol
                )
                return bid, ask

            # fallback to exchange client's fetch_bbo_prices()
            bid, ask = await exchange_client.fetch_bbo_prices(symbol)
            bid_dec = Decimal(str(bid))
            ask_dec = Decimal(str(ask))
            return bid_dec, ask_dec
        
        except Exception as e:
            self.logger.error(f"Failed to fetch BBO prices: {e}")
            raise
