"""
Websocket Manager - manages websocket callback registration and routing.

This module extracts the websocket callback management logic, including:
- Registering/unregistering order contexts
- Creating callback routers
- Storing/restoring original callbacks
- Cleanup on completion
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Optional

from strategies.execution.core.utils import coerce_decimal

from ..contexts import OrderContext


class WebsocketManager:
    """Manages websocket callback registration and routing."""
    
    def __init__(self, logger):
        self.logger = logger
        self._order_context_registry: Dict[str, OrderContext] = {}
        self._pending_websocket_callbacks: Dict[str, List[Dict[str, Any]]] = {}
        self._original_callbacks: Dict[Any, Any] = {}
    
    def register_callbacks(self, exchange_clients: List[Any]) -> None:
        """
        Register websocket callback routers for exchange clients.
        
        Registers both fill callbacks (for incremental fills) and status callbacks
        (for FILLED/CANCELED status changes). Status callbacks are critical for
        properly tracking cancellations and preventing false fills.
        
        Args:
            exchange_clients: List of exchange clients to register callbacks for
        """
        # Store original callbacks and set our routers
        self._original_callbacks.clear()
        fill_router = self.get_callback_router()
        status_router = self._create_websocket_status_callback_router()
        
        for exchange_client in exchange_clients:
            # Register fill callback router
            if hasattr(exchange_client, 'order_fill_callback'):
                self._original_callbacks[exchange_client] = exchange_client.order_fill_callback
                exchange_client.order_fill_callback = fill_router
                self.logger.debug(
                    f"Set websocket fill callback router for {exchange_client.get_exchange_name()}"
                )
            
            # Register status callback router (for FILLED/CANCELED status changes)
            if hasattr(exchange_client, 'order_status_callback'):
                # Store original status callback if it exists
                if exchange_client not in self._original_callbacks:
                    self._original_callbacks[exchange_client] = {}
                if not isinstance(self._original_callbacks[exchange_client], dict):
                    # Convert to dict format if it was just fill callback
                    self._original_callbacks[exchange_client] = {
                        'fill': self._original_callbacks[exchange_client],
                        'status': getattr(exchange_client, 'order_status_callback', None)
                    }
                else:
                    self._original_callbacks[exchange_client]['status'] = exchange_client.order_status_callback
                
                exchange_client.order_status_callback = status_router
                self.logger.debug(
                    f"Set websocket status callback router for {exchange_client.get_exchange_name()}"
                )
    
    def get_callback_router(self) -> Any:
        """Get the websocket callback router function."""
        return self._create_websocket_callback_router()
    
    def register_order_context(self, ctx: OrderContext, order_id: str) -> None:
        """
        Register OrderContext for websocket callbacks.
        
        Handles timing edge case: if callbacks arrived before registration,
        processes them now. Otherwise, future callbacks will route directly.
        
        Also registers server_order_id for exchanges that use both (e.g., Lighter).
        Checks websocket cache for cancellation status if order is already cancelled.
        
        Args:
            ctx: OrderContext to register
            order_id: Order identifier (client_order_id)
        """
        if not order_id:
            return
        
        # Check if order is already cancelled in websocket cache
        # This handles case where cancellation happened before registration
        exchange_client = ctx.spec.exchange_client
        if hasattr(exchange_client, 'order_manager'):
            order_manager = exchange_client.order_manager
            if hasattr(order_manager, 'latest_orders'):
                cached_order = order_manager.latest_orders.get(order_id)
                if cached_order:
                    status = getattr(cached_order, "status", "").upper()
                    if status == "CANCELED" or status == "CANCELLED":
                        filled_size = getattr(cached_order, "filled_size", None)
                        filled_size_decimal = coerce_decimal(filled_size) if filled_size is not None else Decimal("0")
                        ctx.on_websocket_cancel(filled_size_decimal)
        
        # Register client order ID
        self._order_context_registry[order_id] = ctx
        
        # For exchanges like Lighter, also register server_order_id if available
        # Check if exchange client has client_to_server_order_index mapping
        if hasattr(exchange_client, 'order_manager'):
            order_manager = exchange_client.order_manager
            if hasattr(order_manager, 'client_to_server_order_index'):
                server_order_id = order_manager.client_to_server_order_index.get(order_id)
                if server_order_id:
                    self._order_context_registry[str(server_order_id)] = ctx
                    # Also check server order ID in cache
                    if hasattr(order_manager, 'latest_orders'):
                        cached_server_order = order_manager.latest_orders.get(str(server_order_id))
                        if cached_server_order:
                            status = getattr(cached_server_order, "status", "").upper()
                            if status == "CANCELED" or status == "CANCELLED":
                                filled_size = getattr(cached_server_order, "filled_size", None)
                                filled_size_decimal = coerce_decimal(filled_size) if filled_size is not None else Decimal("0")
                                ctx.on_websocket_cancel(filled_size_decimal)
        
        # Process any pending callbacks that arrived before registration
        pending = self._pending_websocket_callbacks.pop(order_id, [])
        for callback_data in pending:
            try:
                callback_type = callback_data.get("type")
                if callback_type == "fill":
                    ctx.on_websocket_fill(
                        callback_data["quantity"],
                        callback_data["price"]
                    )
                elif callback_type == "cancel":
                    ctx.on_websocket_cancel(callback_data.get("filled_size", Decimal("0")))
                elif callback_type == "status":
                    status = callback_data.get("status", "").upper()
                    filled_size = callback_data.get("filled_size", Decimal("0"))
                    price = callback_data.get("price")
                    if status == "CANCELED" or status == "CANCELLED":
                        ctx.on_websocket_cancel(filled_size)
                    elif status == "FILLED":
                        # Ensure fills are recorded
                        if filled_size > ctx.filled_quantity:
                            additional = filled_size - ctx.filled_quantity
                            ctx.on_websocket_fill(additional, price or Decimal("0"))
            except Exception as exc:
                self.logger.warning(
                    f"Error processing pending websocket callback for {order_id}: {exc}"
                )
    
    def _create_websocket_callback_router(self) -> Any:
        """
        Create callback function that routes websocket callbacks to registered contexts.
        
        This router handles fill callbacks from websocket handlers. It also checks
        latest_orders cache for cancellation status when fills are reported.
        If context isn't registered yet, queues the callback for later processing.
        
        Returns:
            Callback function compatible with OrderFillCallback signature
        """
        async def router(order_id: str, price: Decimal, filled_size: Decimal, sequence: Optional[int] = None) -> None:
            """Route websocket callback to correct OrderContext."""
            try:
                ctx = self._order_context_registry.get(order_id)
                
                if ctx is None:
                    # Context not registered yet - queue callback for later
                    if order_id not in self._pending_websocket_callbacks:
                        self._pending_websocket_callbacks[order_id] = []
                    self._pending_websocket_callbacks[order_id].append({
                        "type": "fill",
                        "quantity": filled_size,
                        "price": price,
                        "sequence": sequence,
                    })
                    self.logger.debug(
                        f"Queued websocket fill callback for {order_id} (context not registered yet)"
                    )
                    return
                
                # Context registered - route fill directly
                ctx.on_websocket_fill(filled_size, price)
                
                # Also check if order was cancelled (websocket handlers update latest_orders)
                # This handles case where cancellation happens after registration
                exchange_client = ctx.spec.exchange_client
                if hasattr(exchange_client, 'order_manager'):
                    order_manager = exchange_client.order_manager
                    if hasattr(order_manager, 'latest_orders'):
                        cached_order = order_manager.latest_orders.get(order_id)
                        if cached_order:
                            status = getattr(cached_order, "status", "").upper()
                            if status == "CANCELED" or status == "CANCELLED":
                                # Order was cancelled - mark it
                                cached_filled_size = getattr(cached_order, "filled_size", None)
                                cached_filled_decimal = coerce_decimal(cached_filled_size) if cached_filled_size is not None else Decimal("0")
                                if not ctx.websocket_cancelled:
                                    ctx.on_websocket_cancel(cached_filled_decimal)
                
            except Exception as exc:
                # Don't crash executor - websocket callbacks are optimization
                self.logger.warning(
                    f"Error in websocket callback router for {order_id}: {exc}"
                )
        
        return router
    
    def _create_websocket_status_callback_router(self) -> Any:
        """
        Create callback function for handling status changes (FILLED/CANCELED) from websocket.
        
        This routes order_status_callback events from exchange clients to OrderContext.
        Status callbacks are critical for properly tracking cancellations and preventing false fills.
        
        Signature matches OrderStatusCallback: (order_id, status, filled_size, price)
        
        Returns:
            Callback function for status events
        """
        async def status_router(order_id: str, status: str, filled_size: Decimal, price: Optional[Decimal] = None) -> None:
            """Route websocket status callback (FILLED/CANCELED) to correct OrderContext."""
            try:
                status_upper = status.upper()
                
                # Try to find context by order_id (client or server order ID)
                ctx = self._order_context_registry.get(order_id)
                
                # If not found, also try server order ID for exchanges like Lighter
                if ctx is None:
                    # Check if this might be a server order ID - look through all contexts
                    for registered_id, registered_ctx in self._order_context_registry.items():
                        exchange_client = registered_ctx.spec.exchange_client
                        if hasattr(exchange_client, 'order_manager'):
                            order_manager = exchange_client.order_manager
                            if hasattr(order_manager, 'client_to_server_order_index'):
                                # Check if registered_id maps to this order_id
                                server_id = order_manager.client_to_server_order_index.get(registered_id)
                                if server_id and str(server_id) == order_id:
                                    ctx = registered_ctx
                                    break
                
                if ctx is None:
                    # Context not registered yet - queue callback for later
                    if order_id not in self._pending_websocket_callbacks:
                        self._pending_websocket_callbacks[order_id] = []
                    self._pending_websocket_callbacks[order_id].append({
                        "type": "status",
                        "status": status_upper,
                        "filled_size": filled_size,
                        "price": price,
                    })
                    self.logger.debug(
                        f"Queued websocket status callback for {order_id} (context not registered yet): {status_upper}"
                    )
                    return
                
                # Context registered - route status change
                if status_upper == "CANCELED" or status_upper == "CANCELLED":
                    # Route cancellation to OrderContext
                    ctx.on_websocket_cancel(filled_size)
                    self.logger.debug(
                        f"🔔 Routed websocket CANCELED status to context {order_id}: filled_size={filled_size}"
                    )
                elif status_upper == "FILLED":
                    # For FILLED status, ensure fills are recorded
                    # filled_size here is the total filled size, not incremental
                    if filled_size > ctx.filled_quantity:
                        additional = filled_size - ctx.filled_quantity
                        ctx.on_websocket_fill(additional, price or Decimal("0"))
                        self.logger.debug(
                            f"🔔 Routed websocket FILLED status to context {order_id}: "
                            f"additional={additional}, total={filled_size}"
                        )
                
            except Exception as exc:
                # Don't crash executor - websocket callbacks are optimization
                self.logger.warning(
                    f"Error in websocket status callback router for {order_id}: {exc}"
                )
        
        return status_router
    
    def cleanup(self) -> None:
        """
        Cleanup websocket callback infrastructure after execution completes.
        
        Restores original callbacks on exchange clients and clears registries.
        This ensures no memory leaks and proper cleanup between executions.
        """
        # Restore original callbacks on exchange clients
        for exchange_client, original_callback in self._original_callbacks.items():
            try:
                if isinstance(original_callback, dict):
                    # Both fill and status callbacks were stored
                    if hasattr(exchange_client, 'order_fill_callback'):
                        exchange_client.order_fill_callback = original_callback.get('fill')
                    if hasattr(exchange_client, 'order_status_callback'):
                        exchange_client.order_status_callback = original_callback.get('status')
                else:
                    # Only fill callback was stored (backward compatibility)
                    if hasattr(exchange_client, 'order_fill_callback'):
                        exchange_client.order_fill_callback = original_callback
            except Exception as exc:
                self.logger.warning(
                    f"Error restoring callback for {exchange_client.get_exchange_name()}: {exc}"
                )
        
        # Clear registries
        self._order_context_registry.clear()
        self._pending_websocket_callbacks.clear()
        self._original_callbacks.clear()

