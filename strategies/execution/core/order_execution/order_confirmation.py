"""
Order Confirmation Waiter - Waits for order confirmation via WebSocket or REST.

Handles waiting for order fills, with WebSocket-first strategy and REST fallback.
"""

import asyncio
import time
from decimal import Decimal
from typing import Optional

from exchange_clients import BaseExchangeClient
from exchange_clients.base_models import OrderInfo
from helpers.unified_logger import get_core_logger


class OrderConfirmationWaiter:
    """
    Waits for order confirmation via WebSocket (preferred) or REST polling (fallback).
    
    Strategy:
    - Wait for initial WebSocket update (OPEN status)
    - Poll cache/REST until FILLED status or timeout
    - Fallback to REST polling if WebSocket unavailable
    """
    
    def __init__(self):
        """Initialize order confirmation waiter."""
        self.logger = get_core_logger("order_confirmation")
    
    async def wait_for_confirmation(
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
                initial_info = await exchange_client.await_order_update(
                    order_id, timeout=min(timeout_seconds, 2.0)
                )
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

