"""
Event-Based Reconciler - uses websocket events instead of polling.

This module replaces polling-based order tracking with event-driven websocket callbacks
for instant response and faster order cycling.
"""

from __future__ import annotations

import asyncio
import time
from decimal import Decimal
from typing import Any, Dict, Optional

from strategies.execution.core.utils import coerce_decimal

from .order_tracker import OrderTracker
from .reconciler import ReconciliationResult


class EventBasedReconciler:
    """Reconciler that uses websocket events instead of polling."""
    
    def __init__(self, logger):
        self.logger = logger
        self._order_registry: Dict[str, OrderTracker] = {}
        self._pending_callbacks: Dict[str, list] = {}
        self._callback_router = None
    
    def get_callback_router(self):
        """Get the websocket callback router function."""
        if self._callback_router is None:
            self._callback_router = self._create_callback_router()
        return self._callback_router
    
    def _create_callback_router(self):
        """Create websocket callback that routes to order trackers."""
        async def router(order_id: str, price: Decimal, filled_size: Decimal, sequence: Optional[int] = None) -> None:
            """Route websocket fill callback to correct OrderTracker."""
            try:
                tracker = self._order_registry.get(order_id)
                
                if tracker is None:
                    # Tracker not registered yet - queue callback for later
                    if order_id not in self._pending_callbacks:
                        self._pending_callbacks[order_id] = []
                    self._pending_callbacks[order_id].append({
                        "type": "fill",
                        "quantity": filled_size,
                        "price": price,
                        "sequence": sequence,
                    })
                    self.logger.debug(
                        f"Queued websocket fill callback for {order_id} (tracker not registered yet)"
                    )
                    return
                
                # Tracker registered - route fill directly
                self.logger.info(
                    f"🔔 Routing websocket fill to tracker {order_id}: "
                    f"filled_size={filled_size}, price={price}"
                )
                tracker.on_fill(filled_size, price)
                
            except Exception as exc:
                # Don't crash executor - websocket callbacks are optimization
                self.logger.warning(
                    f"Error in websocket callback router for {order_id}: {exc}"
                )
        
        return router
    
    def _check_cancellation_status(
        self,
        exchange_client: Any,
        order_id: str,
        tracker: OrderTracker
    ) -> bool:
        """
        Check if order was cancelled by examining websocket cache.
        
        Returns:
            True if order is cancelled, False otherwise
        """
        try:
            if hasattr(exchange_client, 'order_manager'):
                order_manager = exchange_client.order_manager
                if hasattr(order_manager, 'latest_orders'):
                    cached_order = order_manager.latest_orders.get(order_id)
                    if cached_order:
                        status = getattr(cached_order, "status", "").upper()
                        if status in {"CANCELED", "CANCELLED"}:
                            # Order was cancelled - update tracker
                            cached_filled_size = getattr(cached_order, "filled_size", None)
                            filled_size_decimal = coerce_decimal(cached_filled_size) if cached_filled_size is not None else Decimal("0")
                            tracker.on_cancel(filled_size_decimal)
                            return True
        except Exception as exc:
            self.logger.debug(f"Error checking cancellation status for {order_id}: {exc}")
        
        return False
    
    async def wait_for_order_event(
        self,
        exchange_client: Any,
        order_id: str,
        order_quantity: Decimal,
        limit_price: Decimal,
        target_quantity: Decimal,
        accumulated_filled_qty: Decimal,
        current_order_filled_qty: Decimal,
        attempt_timeout: float,
        pricing_strategy: str,
        retry_count: int,
        retry_backoff_ms: int,
        logger: Any,
        exchange_name: str,
        symbol: str,
    ) -> ReconciliationResult:
        """
        Wait for websocket event instead of polling.
        Much faster and more efficient!
        
        Args:
            exchange_client: Exchange client instance
            order_id: Order ID to track
            order_quantity: Original order quantity
            limit_price: Limit price used for the order
            target_quantity: Target quantity to fill
            accumulated_filled_qty: Accumulated fills across all retry attempts
            current_order_filled_qty: Fills for current order (to detect new fills)
            attempt_timeout: Timeout for this attempt
            pricing_strategy: Pricing strategy used (for logging)
            retry_count: Current retry count (for logging)
            retry_backoff_ms: Backoff delay in milliseconds
            logger: Logger instance
            exchange_name: Exchange name for logging
            symbol: Trading symbol
            
        Returns:
            ReconciliationResult with fill status and details
        """
        # Create tracker for this order
        tracker = OrderTracker(
            order_id=order_id,
            quantity=order_quantity,
            limit_price=limit_price,
        )
        
        # Process any pending callbacks that arrived before registration
        pending = self._pending_callbacks.pop(order_id, [])
        for callback_data in pending:
            try:
                if callback_data.get("type") == "fill":
                    tracker.on_fill(
                        callback_data["quantity"],
                        callback_data["price"]
                    )
            except Exception as exc:
                logger.debug(f"Error processing pending callback for {order_id}: {exc}")
        
        # Register tracker for websocket callbacks
        self._order_registry[order_id] = tracker
        
        try:
            # Check initial cancellation status (order might be cancelled before we register)
            initial_cancelled = self._check_cancellation_status(exchange_client, order_id, tracker)
            
            if initial_cancelled:
                # Order already cancelled
                new_fills = tracker.filled_quantity - current_order_filled_qty
                if new_fills > Decimal("0"):
                    accumulated_filled_qty += new_fills
                    current_order_filled_qty = tracker.filled_quantity
                
                return ReconciliationResult(
                    filled=tracker.filled_quantity > Decimal("0"),
                    filled_qty=tracker.filled_quantity,
                    fill_price=tracker.fill_price or limit_price,
                    accumulated_filled_qty=accumulated_filled_qty,
                    current_order_filled_qty=current_order_filled_qty,
                    partial_fill_detected=tracker.filled_quantity > Decimal("0"),
                    error=None,
                )
            
            # Wait for fill/cancel event or timeout
            # Also periodically check cancellation status (since we don't have cancel callback)
            start_time = time.time()
            status = None
            
            # Create task for waiting on events
            event_task = asyncio.create_task(tracker.wait_for_event(attempt_timeout))
            
            # Check cancellation status periodically while waiting
            check_interval = 0.1  # Check every 100ms
            while not event_task.done():
                elapsed = time.time() - start_time
                if elapsed >= attempt_timeout:
                    event_task.cancel()
                    status = "TIMEOUT"
                    break
                
                # Check cancellation status
                if self._check_cancellation_status(exchange_client, order_id, tracker):
                    event_task.cancel()
                    status = "CANCELED"
                    break
                
                # Wait a bit before next check
                await asyncio.sleep(check_interval)
            
            # Get result from event task if it completed
            if status is None:
                try:
                    status = await event_task
                except asyncio.CancelledError:
                    status = "TIMEOUT"
            
            # Process result
            new_fills_from_order = tracker.filled_quantity - current_order_filled_qty
            if new_fills_from_order > Decimal("0"):
                accumulated_filled_qty += new_fills_from_order
                current_order_filled_qty = tracker.filled_quantity
            
            filled = False
            filled_qty = Decimal("0")
            fill_price: Optional[Decimal] = None
            partial_fill_detected = False
            error: Optional[str] = None
            
            if status == "FILLED":
                filled = True
                filled_qty = tracker.filled_quantity
                fill_price = tracker.fill_price or limit_price
                
                logger.info(
                    f"✅ [{exchange_name}] Order {order_id} filled via websocket: "
                    f"{filled_qty} @ ${fill_price} for {symbol} "
                    f"(tracker status: {tracker.status})"
                )
            elif status == "CANCELED":
                # Check for partial fills
                if tracker.filled_quantity > Decimal("0"):
                    partial_fill_detected = True
                    filled_qty = tracker.filled_quantity
                    fill_price = tracker.fill_price or limit_price
                    
                    logger.info(
                        f"📊 [{exchange_name}] Partial fill before cancellation for {symbol}: "
                        f"+{new_fills_from_order} (total: {accumulated_filled_qty}/{target_quantity}) @ ${fill_price}"
                    )
                else:
                    # Try to get cancellation reason for better logging
                    cancellation_reason = None
                    try:
                        order_status_check = await exchange_client.get_order_info(order_id)
                        if order_status_check:
                            cancellation_reason = getattr(order_status_check, 'cancel_reason', None)
                    except Exception:
                        pass
                    
                    error = "Order cancelled without fills"
                    if cancellation_reason:
                        logger.info(
                            f"🔄 [{exchange_name}] Order {order_id} cancelled without fills for {symbol}. "
                            f"Reason: {cancellation_reason}. Will retry with adaptive pricing."
                        )
                    else:
                        logger.info(
                            f"🔄 [{exchange_name}] Order {order_id} cancelled without fills for {symbol}. "
                            f"Will retry with adaptive pricing."
                        )
            else:  # TIMEOUT
                # Proactively cancel the order since it timed out
                # This prevents orders from staying open unnecessarily
                try:
                    order_status_check = await exchange_client.get_order_info(order_id)
                    if order_status_check and order_status_check.status not in {"CANCELED", "CANCELLED", "FILLED"}:
                        logger.debug(
                            f"⏱️ [{exchange_name}] Order {order_id} timed out after {attempt_timeout}s. "
                            f"Proactively cancelling for {symbol}"
                        )
                        try:
                            await exchange_client.cancel_order(order_id)
                        except Exception as cancel_exc:
                            logger.debug(f"⚠️ [{exchange_name}] Failed to cancel timed-out order {order_id}: {cancel_exc}")
                except Exception as status_exc:
                    logger.debug(f"⚠️ [{exchange_name}] Failed to check order status for {order_id}: {status_exc}")
                
                # Check if we have partial fills
                if tracker.filled_quantity > Decimal("0"):
                    partial_fill_detected = True
                    filled_qty = tracker.filled_quantity
                    fill_price = tracker.fill_price or limit_price
                    
                    logger.debug(
                        f"📊 [{exchange_name}] Partial fill detected on timeout for {symbol}: "
                        f"{filled_qty} @ ${fill_price}"
                    )
                else:
                    logger.debug(
                        f"⏱️ [{exchange_name}] Order {order_id} timeout for {symbol} "
                        f"(no fills detected)"
                    )
            
            return ReconciliationResult(
                filled=filled,
                filled_qty=filled_qty,
                fill_price=fill_price,
                accumulated_filled_qty=accumulated_filled_qty,
                current_order_filled_qty=current_order_filled_qty,
                partial_fill_detected=partial_fill_detected,
                error=error,
            )
            
        finally:
            # Cleanup - remove tracker from registry
            self._order_registry.pop(order_id, None)
    
    def cleanup(self) -> None:
        """Cleanup all registries (for testing/debugging)."""
        self._order_registry.clear()
        self._pending_callbacks.clear()

