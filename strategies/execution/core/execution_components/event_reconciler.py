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
        self._status_callback_router = None
        self._original_fill_callback = None
        self._original_status_callback = None
    
    def set_original_callbacks(self, fill_callback=None, status_callback=None):
        """Set original callbacks for chaining (called by execution strategy)."""
        self._original_fill_callback = fill_callback
        self._original_status_callback = status_callback
    
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
                
                # Also call original callback if it exists (for strategy notifications)
                if self._original_fill_callback:
                    try:
                        if asyncio.iscoroutinefunction(self._original_fill_callback):
                            asyncio.create_task(
                                self._original_fill_callback(order_id, price, filled_size, sequence)
                            )
                        else:
                            self._original_fill_callback(order_id, price, filled_size, sequence)
                    except Exception as exc:
                        self.logger.debug(f"Error calling original fill callback: {exc}")
                
            except Exception as exc:
                # Don't crash executor - websocket callbacks are optimization
                self.logger.warning(
                    f"Error in websocket callback router for {order_id}: {exc}"
                )
        
        return router
    
    def get_status_callback_router(self):
        """Get the websocket status callback router function."""
        if self._status_callback_router is None:
            self._status_callback_router = self._create_status_callback_router()
        return self._status_callback_router
    
    def _create_status_callback_router(self):
        """Create websocket status callback that routes to order trackers."""
        async def router(order_id: str, status: str, filled_size: Decimal, price: Optional[Decimal] = None) -> None:
            """Route websocket status callback (FILLED/CANCELED) to correct OrderTracker."""
            try:
                tracker = self._order_registry.get(order_id)
                
                if tracker is None:
                    # Tracker not registered yet - queue callback for later
                    if order_id not in self._pending_callbacks:
                        self._pending_callbacks[order_id] = []
                    self._pending_callbacks[order_id].append({
                        "type": "status",
                        "status": status,
                        "filled_size": filled_size,
                        "price": price,
                    })
                    self.logger.debug(
                        f"Queued websocket status callback for {order_id} (tracker not registered yet)"
                    )
                    return
                
                # Tracker registered - route status directly
                status_upper = status.upper()
                if status_upper == "FILLED":
                    self.logger.info(
                        f"🔔 Routing websocket FILLED status to tracker {order_id}: "
                        f"filled_size={filled_size}, price={price}"
                    )
                    # Update tracker with final fill
                    if filled_size > tracker.filled_quantity:
                        tracker.on_fill(filled_size - tracker.filled_quantity, price or tracker.limit_price)
                    # Always update filled_quantity to match websocket (handles instant fills)
                    if filled_size > tracker.filled_quantity:
                        tracker.filled_quantity = filled_size
                    if price is not None:
                        tracker.fill_price = price
                    tracker.status = "FILLED"
                    # CRITICAL: Set fill_event to wake up wait_for_event() task
                    tracker.fill_event.set()
                elif status_upper in {"CANCELED", "CANCELLED"}:
                    self.logger.info(
                        f"🔔 Routing websocket CANCELED status to tracker {order_id}: "
                        f"filled_size={filled_size}"
                    )
                    tracker.on_cancel(filled_size)
                
                # Note: Original status callback not chained since trading_bot doesn't use it
                # (strategies only need fill notifications, not status notifications)
                
            except Exception as exc:
                # Don't crash executor - websocket callbacks are optimization
                self.logger.warning(
                    f"Error in websocket status callback router for {order_id}: {exc}"
                )
        
        return router
    
    def _check_order_status_in_cache(
        self,
        exchange_client: Any,
        order_id: str,
        tracker: OrderTracker
    ) -> Optional[str]:
        """
        Check order status in websocket cache (latest_orders).
        Updates tracker if status is FILLED or CANCELED.
        
        Returns:
            "FILLED", "CANCELED", or None if not found/not final state
        """
        try:
            if hasattr(exchange_client, 'order_manager'):
                order_manager = exchange_client.order_manager
                if hasattr(order_manager, 'latest_orders'):
                    cached_order = order_manager.latest_orders.get(order_id)
                    if cached_order:
                        status = getattr(cached_order, "status", "").upper()
                        
                        if status == "FILLED":
                            # Order filled - update tracker
                            cached_filled_size = getattr(cached_order, "filled_size", None)
                            cached_price = getattr(cached_order, "price", None)
                            
                            filled_size_decimal = coerce_decimal(cached_filled_size) if cached_filled_size is not None else Decimal("0")
                            price_decimal = coerce_decimal(cached_price) if cached_price is not None else tracker.limit_price
                            
                            # Only update if we have new fills
                            if filled_size_decimal > tracker.filled_quantity:
                                tracker.on_fill(filled_size_decimal - tracker.filled_quantity, price_decimal)
                            # Always update filled_quantity to match cache (handles instant fills)
                            if filled_size_decimal > tracker.filled_quantity:
                                tracker.filled_quantity = filled_size_decimal
                            if cached_price is not None:
                                tracker.fill_price = price_decimal
                            
                            # Ensure status is set to FILLED
                            tracker.status = "FILLED"
                            # CRITICAL: Set fill_event to wake up wait_for_event() task
                            tracker.fill_event.set()
                            return "FILLED"
                            
                        elif status in {"CANCELED", "CANCELLED"}:
                            # Order was cancelled - update tracker
                            cached_filled_size = getattr(cached_order, "filled_size", None)
                            filled_size_decimal = coerce_decimal(cached_filled_size) if cached_filled_size is not None else Decimal("0")
                            tracker.on_cancel(filled_size_decimal)
                            return "CANCELED"
        except Exception as exc:
            self.logger.debug(f"Error checking order status in cache for {order_id}: {exc}")
        
        return None
    
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
        status = self._check_order_status_in_cache(exchange_client, order_id, tracker)
        return status == "CANCELED"
    
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
                elif callback_data.get("type") == "status":
                    status = callback_data.get("status", "").upper()
                    filled_size = callback_data.get("filled_size", Decimal("0"))
                    price = callback_data.get("price")
                    if status == "FILLED":
                        if filled_size > tracker.filled_quantity:
                            tracker.on_fill(filled_size - tracker.filled_quantity, price or tracker.limit_price)
                        # Always update filled_quantity to match websocket (handles instant fills)
                        if filled_size > tracker.filled_quantity:
                            tracker.filled_quantity = filled_size
                        if price is not None:
                            tracker.fill_price = price
                        tracker.status = "FILLED"
                        # CRITICAL: Set fill_event to wake up wait_for_event() task
                        tracker.fill_event.set()
                    elif status in {"CANCELED", "CANCELLED"}:
                        tracker.on_cancel(filled_size)
            except Exception as exc:
                logger.debug(f"Error processing pending callback for {order_id}: {exc}")
        
        # Register tracker for websocket callbacks
        self._order_registry[order_id] = tracker
        
        try:
            # Check initial status in cache (order might be filled/cancelled before we register)
            initial_status = self._check_order_status_in_cache(exchange_client, order_id, tracker)
            
            if initial_status == "FILLED":
                # Order already filled
                new_fills = tracker.filled_quantity - current_order_filled_qty
                if new_fills > Decimal("0"):
                    accumulated_filled_qty += new_fills
                    current_order_filled_qty = tracker.filled_quantity
                
                return ReconciliationResult(
                    filled=True,
                    filled_qty=tracker.filled_quantity,
                    fill_price=tracker.fill_price or limit_price,
                    accumulated_filled_qty=accumulated_filled_qty,
                    current_order_filled_qty=current_order_filled_qty,
                    partial_fill_detected=False,
                    error=None,
                )
            elif initial_status == "CANCELED":
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
                    error="Order cancelled without fills" if tracker.filled_quantity == Decimal("0") else None,
                )
            
            # Wait for fill/cancel event or timeout
            # Status callbacks should fire instantly, but we check cache as a safety net
            start_time = time.time()
            status = None
            
            # Create task for waiting on events
            event_task = asyncio.create_task(tracker.wait_for_event(attempt_timeout))
            
            # Check websocket cache periodically as safety net (status callbacks should handle this instantly)
            # Reduced frequency since status callbacks are primary mechanism
            check_interval = 0.5  # Check every 500ms (reduced from 100ms since status callbacks handle it)
            while not event_task.done():
                elapsed = time.time() - start_time
                if elapsed >= attempt_timeout:
                    event_task.cancel()
                    status = "TIMEOUT"
                    break
                
                # Safety net: Check websocket cache for status changes (in case callback missed)
                cached_status = self._check_order_status_in_cache(exchange_client, order_id, tracker)
                if cached_status == "FILLED":
                    # CRITICAL: _check_order_status_in_cache already updated tracker.filled_quantity
                    # from the cache, so tracker now has the correct final filled amount
                    event_task.cancel()
                    status = "FILLED"
                    logger.debug(
                        f"✅ [{exchange_name}] Order {order_id} FILLED detected via websocket cache (safety net) for {symbol} "
                        f"(tracker.filled_quantity={tracker.filled_quantity})"
                    )
                    break
                elif cached_status == "CANCELED":
                    event_task.cancel()
                    status = "CANCELED"
                    logger.debug(
                        f"✅ [{exchange_name}] Order {order_id} CANCELED detected via websocket cache (safety net) for {symbol}"
                    )
                    break
                
                # Wait before next check (status callbacks should fire before this)
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
                # ACID-like approach: Ensure order is in final state before returning
                # Use websocket events instead of polling (much faster and more efficient!)
                logger.info(
                    f"⏱️ [{exchange_name}] Order {order_id} timed out after {attempt_timeout}s. "
                    f"Sending cancel and waiting for final state via websocket for {symbol}"
                )
                
                # Step 1: Try to cancel ONCE
                try:
                    # Send cancel request (may fail if already filled/cancelled - that's ok)
                    logger.info(f"🔄 [{exchange_name}] Attempting to cancel order {order_id}")
                    try:
                        await exchange_client.cancel_order(order_id)
                    except Exception as cancel_exc:
                        logger.debug(
                            f"Cancel exception for {order_id}: {cancel_exc}. "
                            f"Order may have filled/cancelled already (will be caught by websocket)."
                        )
                except Exception as status_exc:
                    logger.warning(f"⚠️ [{exchange_name}] Failed to send cancel: {status_exc}")
                
                # Step 2: Wait for websocket event (fill or cancel)
                # The tracker is still registered and receiving websocket callbacks!
                # Also check websocket cache directly since cancel events don't route through fill callback
                extended_wait = 3.0  # Wait up to 3s for websocket event
                wait_start = time.time()
                
                logger.debug(
                    f"⏳ [{exchange_name}] Waiting up to {extended_wait}s for websocket event "
                    f"(fill or cancel) for {order_id}"
                )
                
                while time.time() - wait_start < extended_wait:
                    # Check if tracker received websocket event (for incremental fills)
                    if tracker.status in {"FILLED", "CANCELED"}:
                        if tracker.status == "FILLED":
                            logger.info(
                                f"✅ [{exchange_name}] Order {order_id} filled (via websocket callback): "
                                f"{tracker.filled_quantity} @ ${tracker.fill_price or limit_price} for {symbol}"
                            )
                        else:
                            logger.info(
                                f"✅ [{exchange_name}] Order {order_id} cancelled (via websocket callback) for {symbol}"
                            )
                        break
                    
                    # Check websocket cache for status changes (FILLED or CANCELED)
                    # This catches status changes that don't trigger incremental fill callbacks
                    cached_status = self._check_order_status_in_cache(exchange_client, order_id, tracker)
                    if cached_status == "FILLED":
                        logger.info(
                            f"✅ [{exchange_name}] Order {order_id} filled (via websocket cache): "
                            f"{tracker.filled_quantity} @ ${tracker.fill_price or limit_price} for {symbol}"
                        )
                        break
                    elif cached_status == "CANCELED":
                        logger.info(
                            f"✅ [{exchange_name}] Order {order_id} cancelled (via websocket cache) for {symbol}"
                        )
                        break
                    
                    # Wait a bit and check again
                    await asyncio.sleep(0.1)
                
                # After extended wait, check if still pending
                if tracker.status not in {"FILLED", "CANCELED"}:
                    # Check websocket cache one more time (cancellation might have arrived)
                    if self._check_cancellation_status(exchange_client, order_id, tracker):
                        logger.info(
                            f"✅ [{exchange_name}] Order {order_id} cancelled (found in websocket cache) for {symbol}"
                        )
                    else:
                        # Last resort: poll once to check final state
                        try:
                            final_check = await exchange_client.get_order_info(order_id)
                            if final_check:
                                final_status = final_check.status.upper()
                                if final_status == "FILLED":
                                    filled_size = getattr(final_check, 'filled_size', Decimal("0"))
                                    price = getattr(final_check, 'price', limit_price)
                                    if filled_size > tracker.filled_quantity:
                                        tracker.on_fill(filled_size - tracker.filled_quantity, price)
                                    # Always update filled_quantity to match polling result (handles instant fills)
                                    if filled_size > tracker.filled_quantity:
                                        tracker.filled_quantity = filled_size
                                    if price is not None:
                                        tracker.fill_price = price
                                    tracker.status = "FILLED"
                                    # Set fill_event for consistency (even though wait_for_event already timed out)
                                    tracker.fill_event.set()
                                    logger.warning(
                                        f"⚠️ [{exchange_name}] Order {order_id} filled but websocket event missed. "
                                        f"Captured via polling: {filled_size} @ ${price}"
                                    )
                                elif final_status in {"CANCELED", "CANCELLED"}:
                                    # Update tracker with cancel status
                                    filled_size = getattr(final_check, 'filled_size', Decimal("0"))
                                    tracker.on_cancel(coerce_decimal(filled_size) if filled_size else Decimal("0"))
                                    logger.info(
                                        f"✅ [{exchange_name}] Order {order_id} cancelled (via polling) for {symbol}"
                                    )
                                else:
                                    logger.error(
                                        f"❌ [{exchange_name}] Order {order_id} still OPEN after cancel + {extended_wait}s wait! "
                                        f"This may cause double-ordering. Status: {final_status}"
                                    )
                        except Exception as final_exc:
                            logger.error(
                                f"❌ [{exchange_name}] Failed to verify final state for {order_id}: {final_exc}"
                            )
                
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

