"""
Market Order Executor - Handles market order placement with partial fill tracking and slippage fallback.

Executes market orders immediately, tracks partial fills (critical for rollback),
and falls back to limit orders when slippage protection triggers.
"""

from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from exchange_clients import BaseExchangeClient
from strategies.execution.patterns.atomic_multi_order.utils import coerce_decimal

from .limit_order_executor import LimitOrderExecutor
from .order_confirmation import OrderConfirmationWaiter
from ..order_executor import ExecutionResult
from ..price_provider import PriceProvider
from helpers.unified_logger import get_core_logger


class MarketOrderExecutor:
    """
    Executes market orders with partial fill tracking and slippage fallback.
    
    Handles:
    - Market order placement
    - Partial fill detection and tracking (critical fix for rollback)
    - Slippage fallback to limit orders
    - Order confirmation waiting
    """
    
    def __init__(
        self,
        price_provider=None,
        limit_executor: LimitOrderExecutor = None,
        confirmation_waiter: OrderConfirmationWaiter = None
    ):
        """
        Initialize market order executor.
        
        Args:
            price_provider: Optional PriceProvider for BBO price retrieval
            limit_executor: LimitOrderExecutor instance for slippage fallback
            confirmation_waiter: OrderConfirmationWaiter instance for order confirmation
        """
        self.price_provider = price_provider or PriceProvider()
        self.limit_executor = limit_executor
        self.confirmation_waiter = confirmation_waiter or OrderConfirmationWaiter()
        self.logger = get_core_logger("market_order_executor")
    
    async def execute(
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
            exchange_client: Exchange client instance
            symbol: Trading pair (e.g., "BTC-PERP")
            side: "buy" or "sell"
            size_usd: Order size in USD
            quantity: Order quantity
            reduce_only: If True, order can only reduce existing position (bypasses min notional)
            
        Returns:
            ExecutionResult with fill details
        """
        try:
            # Get current price for quantity calculation & slippage tracking
            best_bid, best_ask = await self.price_provider.get_bbo_prices(exchange_client, symbol)
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
            order_info = await self.confirmation_waiter.wait_for_confirmation(
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
                cancel_reason_lower = cancel_reason.lower()
                exchange_name = exchange_client.get_exchange_name()
                
                # ⚠️ CRITICAL FIX: Check for partial fills before cancellation
                # Some exchanges may cancel orders after partial fills (e.g., slippage protection)
                # 
                # IMPORTANT: Behavior differs for OPEN vs CLOSE operations:
                # - OPEN (reduce_only=False): Partial fill CREATES a position → must be tracked for rollback
                # - CLOSE (reduce_only=True): Partial fill REDUCES a position → rollback queries actual state
                # 
                # In both cases, we track the partial fill so rollback can handle it appropriately.
                partial_filled_qty = coerce_decimal(getattr(order_info, "filled_size", None)) or Decimal("0")
                partial_fill_price = coerce_decimal(getattr(order_info, "price", None)) or expected_price
                
                if partial_filled_qty > Decimal("0"):
                    self.logger.warning(
                        f"[{exchange_name.upper()}] ⚠️ Market order canceled with PARTIAL FILL: "
                        f"{partial_filled_qty} @ ${partial_fill_price} (order_id={order_id}, reason={cancel_reason})"
                    )
                    
                    # Calculate remaining quantity to fill
                    remaining_qty = order_quantity - partial_filled_qty
                    remaining_usd = size_usd - (partial_filled_qty * partial_fill_price) if size_usd else None
                    
                    # Check if this is a slippage-related error that we can fallback to limit orders
                    slippage_related_keywords = [
                        "exceeds_max_slippage",
                        "max_slippage",
                        "slippage",
                        "insufficient_liquidity",
                        "price_impact_too_high"
                    ]
                    
                    is_slippage_error = any(keyword in cancel_reason_lower for keyword in slippage_related_keywords)
                    
                    if is_slippage_error and remaining_qty > Decimal("0.0001"):
                        # For CLOSE operations (reduce_only=True), attempting to fill remaining quantity
                        # might not make sense if the position is already closed. However, we still try
                        # because the exchange might have only partially closed the position.
                        operation_type = "CLOSE" if reduce_only else "OPEN"
                        self.logger.warning(
                            f"[{exchange_name.upper()}] Market order canceled due to slippage with partial fill "
                            f"({operation_type} operation). Falling back to aggressive limit order for "
                            f"remaining {remaining_qty} {symbol}"
                        )
                        # Try to fill the remaining quantity with limit order
                        fallback_result = await self._fallback_to_limit_on_slippage_error(
                            exchange_client=exchange_client,
                            symbol=symbol,
                            side=side,
                            size_usd=remaining_usd,
                            quantity=remaining_qty,
                            reduce_only=reduce_only,
                            original_cancel_reason=cancel_reason
                        )
                        
                        # Combine partial fill with fallback result
                        if fallback_result.filled:
                            # Both partial fill and fallback succeeded
                            total_filled = partial_filled_qty + fallback_result.filled_quantity
                            # Weighted average price
                            total_cost = (partial_filled_qty * partial_fill_price) + (
                                fallback_result.filled_quantity * fallback_result.fill_price
                            )
                            avg_price = total_cost / total_filled if total_filled > 0 else partial_fill_price
                            
                            slippage_usd = abs(avg_price - expected_price) * total_filled
                            slippage_pct = abs(avg_price - expected_price) / expected_price if expected_price > 0 else Decimal('0')
                            
                            self.logger.info(
                                f"[{exchange_name.upper()}] Market order partially filled + limit fallback succeeded: "
                                f"{total_filled} @ ${avg_price:.6f} (partial: {partial_filled_qty} @ ${partial_fill_price}, "
                                f"fallback: {fallback_result.filled_quantity} @ ${fallback_result.fill_price})"
                            )
                            
                            return ExecutionResult(
                                success=True,
                                filled=True,
                                fill_price=avg_price,
                                filled_quantity=total_filled,
                                expected_price=expected_price,
                                slippage_usd=slippage_usd,
                                slippage_pct=slippage_pct,
                                execution_mode_used="market_partial_limit_fallback",
                                order_id=order_id
                            )
                        else:
                            # Partial fill succeeded but fallback failed
                            # Return partial fill result so it can be tracked for rollback
                            # 
                            # For OPEN operations: This partial fill created a position that MUST be closed
                            # For CLOSE operations: Rollback will query actual position state anyway, but
                            # tracking this helps ensure we don't miss anything
                            slippage_usd = abs(partial_fill_price - expected_price) * partial_filled_qty
                            slippage_pct = abs(partial_fill_price - expected_price) / expected_price if expected_price > 0 else Decimal('0')
                            
                            operation_type = "CLOSE" if reduce_only else "OPEN"
                            self.logger.warning(
                                f"[{exchange_name.upper()}] ⚠️ Market order had partial fill ({partial_filled_qty} @ ${partial_fill_price}) "
                                f"({operation_type} operation) but limit fallback failed: {fallback_result.error_message}. "
                                f"This partial fill MUST be tracked for rollback!"
                            )
                            
                            return ExecutionResult(
                                success=False,  # Overall failed because we didn't fill everything
                                filled=True,    # But we did have a partial fill
                                fill_price=partial_fill_price,
                                filled_quantity=partial_filled_qty,
                                expected_price=expected_price,
                                slippage_usd=slippage_usd,
                                slippage_pct=slippage_pct,
                                execution_mode_used="market_partial_fallback_failed",
                                order_id=order_id,
                                error_message=(
                                    f"Market order canceled with partial fill ({partial_filled_qty}/{order_quantity}). "
                                    f"Limit fallback failed: {fallback_result.error_message or 'unknown error'}"
                                ),
                                retryable=False
                            )
                    else:
                        # Partial fill but no fallback (either not slippage error or remaining_qty too small)
                        # 
                        # For OPEN operations: Partial fill created a position → must be closed
                        # For CLOSE operations: Partial fill reduced a position → rollback queries actual state
                        slippage_usd = abs(partial_fill_price - expected_price) * partial_filled_qty
                        slippage_pct = abs(partial_fill_price - expected_price) / expected_price if expected_price > 0 else Decimal('0')
                        
                        operation_type = "CLOSE" if reduce_only else "OPEN"
                        self.logger.warning(
                            f"[{exchange_name.upper()}] ⚠️ Market order canceled with partial fill ({partial_filled_qty} @ ${partial_fill_price}) "
                            f"({operation_type} operation) but cannot fallback (reason: {cancel_reason}, remaining: {remaining_qty}). "
                            f"This partial fill MUST be tracked for rollback!"
                        )
                        
                        return ExecutionResult(
                            success=False,  # Overall failed
                            filled=True,    # But we did have a partial fill
                            fill_price=partial_fill_price,
                            filled_quantity=partial_filled_qty,
                            expected_price=expected_price,
                            slippage_usd=slippage_usd,
                            slippage_pct=slippage_pct,
                            execution_mode_used="market_partial_canceled",
                            order_id=order_id,
                            error_message=f"Market order canceled with partial fill: {cancel_reason}",
                            retryable=False
                        )
                
                # No partial fill - proceed with normal cancellation handling
                # Check if this is a slippage-related error that we can fallback to limit orders
                slippage_related_keywords = [
                    "exceeds_max_slippage",
                    "max_slippage",
                    "slippage",
                    "insufficient_liquidity",
                    "price_impact_too_high"
                ]
                
                is_slippage_error = any(keyword in cancel_reason_lower for keyword in slippage_related_keywords)
                
                if is_slippage_error:
                    self.logger.warning(
                        f"[{exchange_name.upper()}] Market order canceled due to slippage: {cancel_reason}. "
                        f"Falling back to aggressive limit order for {symbol}"
                    )
                    # Fallback to limit order with aggressive pricing
                    return await self._fallback_to_limit_on_slippage_error(
                        exchange_client=exchange_client,
                        symbol=symbol,
                        side=side,
                        size_usd=size_usd,
                        quantity=quantity,
                        reduce_only=reduce_only,
                        original_cancel_reason=cancel_reason
                    )
                
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
    
    async def _fallback_to_limit_on_slippage_error(
        self,
        exchange_client: BaseExchangeClient,
        symbol: str,
        side: str,
        size_usd: Optional[Decimal],
        quantity: Optional[Decimal],
        reduce_only: bool,
        original_cancel_reason: str
    ) -> ExecutionResult:
        """
        Fallback to limit order when market order fails due to slippage.
        
        Uses aggressive pricing (at touch or slightly inside spread) to ensure fill
        while avoiding exchange-side slippage protection.
        
        Args:
            exchange_client: Exchange client instance
            symbol: Trading pair
            side: "buy" or "sell"
            size_usd: Order size in USD
            quantity: Order quantity
            reduce_only: If True, order can only reduce existing position
            original_cancel_reason: Original cancellation reason from market order
            
        Returns:
            ExecutionResult from limit order execution
        """
        try:
            exchange_name = exchange_client.get_exchange_name()
            self.logger.info(
                f"[{exchange_name.upper()}] Attempting limit order fallback for {symbol} "
                f"(original error: {original_cancel_reason})"
            )
            
            # Fetch fresh BBO prices
            best_bid, best_ask = await self.price_provider.get_bbo_prices(exchange_client, symbol)
            
            # Use aggressive pricing: at touch (best bid/ask) or slightly inside to ensure fill
            # Note: round_to_tick() handles missing tick_size gracefully (returns price as-is)
            # Exchange-specific metadata loading (if needed) is handled internally by place_limit_order()
            # For buy: use best_ask (at touch) - this ensures immediate fill
            # For sell: use best_bid (at touch) - this ensures immediate fill
            if side == "buy":
                limit_price = best_ask  # At touch for immediate fill
            else:
                limit_price = best_bid  # At touch for immediate fill
            
            # Debug: Log BBO and tick_size before rounding
            tick_size = getattr(exchange_client.config, 'tick_size', None)
            self.logger.debug(
                f"[{exchange_name.upper()}] Before rounding: best_bid={best_bid}, best_ask={best_ask}, "
                f"limit_price={limit_price}, tick_size={tick_size}"
            )
            
            # Round to tick size
            limit_price_before_rounding = limit_price
            limit_price = exchange_client.round_to_tick(limit_price)
            
            # Debug: Log after rounding
            self.logger.debug(
                f"[{exchange_name.upper()}] After rounding: limit_price={limit_price} "
                f"(was {limit_price_before_rounding}, tick_size={tick_size})"
            )
            
            # Safety check: if rounding changed price dramatically (>10%), something is wrong
            if limit_price_before_rounding > 0:
                price_change_pct = abs((limit_price - limit_price_before_rounding) / limit_price_before_rounding) * 100
                if price_change_pct > 10:
                    self.logger.error(
                        f"[{exchange_name.upper()}] ⚠️ CRITICAL: round_to_tick changed price by {price_change_pct:.1f}%! "
                        f"Before: {limit_price_before_rounding}, After: {limit_price}, tick_size: {tick_size}. "
                        f"This suggests tick_size is incorrect or not set for {symbol}!"
                    )
                    # Try to fetch correct tick_size for this symbol
                    try:
                        # Try to get tick_size from market_data manager if available
                        if hasattr(exchange_client, 'market_data') and exchange_client.market_data:
                            # Use the symbol to fetch contract attributes
                            contract_id, correct_tick_size = await exchange_client.market_data.get_contract_attributes(symbol)
                            self.logger.warning(
                                f"[{exchange_name.upper()}] Fetched correct tick_size={correct_tick_size} for {symbol} "
                                f"(contract_id={contract_id}). Re-rounding..."
                            )
                            # Update config tick_size for future use
                            exchange_client.config.tick_size = correct_tick_size
                            # Re-round with correct tick_size
                            limit_price = limit_price_before_rounding.quantize(
                                correct_tick_size, 
                                rounding=ROUND_HALF_UP
                            )
                            self.logger.info(
                                f"[{exchange_name.upper()}] Corrected limit_price={limit_price} "
                                f"(was {limit_price_before_rounding})"
                            )
                        elif hasattr(exchange_client, 'get_contract_attributes'):
                            # Fallback: try get_contract_attributes (uses config.ticker)
                            contract_id, correct_tick_size = await exchange_client.get_contract_attributes()
                            self.logger.warning(
                                f"[{exchange_name.upper()}] Fetched correct tick_size={correct_tick_size} "
                                f"(contract_id={contract_id}). Re-rounding..."
                            )
                            # Update config tick_size for future use
                            exchange_client.config.tick_size = correct_tick_size
                            # Re-round with correct tick_size
                            limit_price = limit_price_before_rounding.quantize(
                                correct_tick_size, 
                                rounding=ROUND_HALF_UP
                            )
                            self.logger.info(
                                f"[{exchange_name.upper()}] Corrected limit_price={limit_price} "
                                f"(was {limit_price_before_rounding})"
                            )
                    except Exception as e:
                        self.logger.error(
                            f"[{exchange_name.upper()}] Failed to fetch correct tick_size: {e}"
                        )
            
            # Calculate quantity
            if quantity is not None:
                order_quantity = Decimal(str(quantity)).copy_abs()
            else:
                if size_usd is None:
                    raise ValueError("Limit fallback requires size_usd or quantity")
                order_quantity = (Decimal(str(size_usd)) / limit_price).copy_abs()
            
            order_quantity = exchange_client.round_to_step(order_quantity)
            if order_quantity <= Decimal("0"):
                raise ValueError("Order quantity rounded to zero")
            
            # Execute limit order with short timeout (5-10 seconds)
            # Since we're pricing at touch, it should fill quickly
            timeout_seconds = 10.0
            
            self.logger.info(
                f"[{exchange_name.upper()}] Placing aggressive limit {side} {symbol}: "
                f"{order_quantity} @ ${limit_price} (at touch for immediate fill)"
            )
            
            result = await self.limit_executor.execute(
                exchange_client=exchange_client,
                symbol=symbol,
                side=side,
                size_usd=None,  # Use quantity instead
                quantity=order_quantity,
                timeout_seconds=timeout_seconds,
                price_offset_pct=Decimal("0"),  # No offset - at touch
                cancel_event=None,
                reduce_only=reduce_only
            )
            
            # Update execution mode to indicate this was a slippage fallback
            if result.filled:
                result.execution_mode_used = "limit_slippage_fallback"
                self.logger.info(
                    f"[{exchange_name.upper()}] Limit order fallback succeeded for {symbol}: "
                    f"{result.filled_quantity} @ ${result.fill_price}"
                )
            else:
                result.execution_mode_used = "limit_slippage_fallback_failed"
                result.error_message = (
                    f"Market order failed ({original_cancel_reason}) and limit fallback also failed: "
                    f"{result.error_message or 'unknown error'}"
                )
                self.logger.error(
                    f"[{exchange_name.upper()}] Limit order fallback failed for {symbol}: "
                    f"{result.error_message}"
                )
            
            return result
            
        except Exception as e:
            exchange_name = exchange_client.get_exchange_name()
            self.logger.error(
                f"[{exchange_name.upper()}] Limit order fallback error for {symbol}: {e}",
                exc_info=True
            )
            return ExecutionResult(
                success=False,
                filled=False,
                error_message=(
                    f"Market order failed ({original_cancel_reason}) and limit fallback error: {str(e)}"
                ),
                execution_mode_used="limit_slippage_fallback_error"
            )

