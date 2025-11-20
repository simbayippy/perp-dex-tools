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
        
        Args:
            exchange_clients: List of exchange clients to register callbacks for
        """
        # Store original callbacks and set our router
        self._original_callbacks.clear()
        router_callback = self.get_callback_router()
        
        for exchange_client in exchange_clients:
            # Store original callback to restore later
            if hasattr(exchange_client, 'order_fill_callback'):
                self._original_callbacks[exchange_client] = exchange_client.order_fill_callback
                # Set our router callback
                exchange_client.order_fill_callback = router_callback
                self.logger.debug(
                    f"Set websocket callback router for {exchange_client.get_exchange_name()}"
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
    
    def _create_websocket_cancel_callback_router(self) -> Any:
        """
        Create callback function for handling cancellation events from websocket.
        
        This is separate from fill callbacks because cancellation events have different
        data structure (status + filled_size, not incremental fills).
        
        Returns:
            Callback function for cancellation events
        """
        async def cancel_router(order_id: str, filled_size: Decimal) -> None:
            """Route websocket cancellation callback to correct OrderContext."""
            try:
                ctx = self._order_context_registry.get(order_id)
                
                if ctx is None:
                    # Context not registered yet - queue callback for later
                    if order_id not in self._pending_websocket_callbacks:
                        self._pending_websocket_callbacks[order_id] = []
                    self._pending_websocket_callbacks[order_id].append({
                        "type": "cancel",
                        "filled_size": filled_size,
                    })
                    self.logger.debug(
                        f"Queued websocket cancel callback for {order_id} (context not registered yet)"
                    )
                    return
                
                # Context registered - route directly
                ctx.on_websocket_cancel(filled_size)
                
            except Exception as exc:
                # Don't crash executor - websocket callbacks are optimization
                self.logger.warning(
                    f"Error in websocket cancel callback router for {order_id}: {exc}"
                )
        
        return cancel_router
    
    def cleanup(self) -> None:
        """
        Cleanup websocket callback infrastructure after execution completes.
        
        Restores original callbacks on exchange clients and clears registries.
        This ensures no memory leaks and proper cleanup between executions.
        """
        # Restore original callbacks on exchange clients
        for exchange_client, original_callback in self._original_callbacks.items():
            try:
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

