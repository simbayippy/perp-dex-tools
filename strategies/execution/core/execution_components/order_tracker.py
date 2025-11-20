"""
Order Tracker - lightweight state container for tracking order fills via websocket events.

This module provides event-driven order tracking instead of polling.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional


@dataclass
class OrderTracker:
    """Tracks a single order's state via websocket events."""
    
    order_id: str
    quantity: Decimal
    limit_price: Decimal
    filled_quantity: Decimal = Decimal("0")
    fill_price: Optional[Decimal] = None
    status: str = "PENDING"  # PENDING, OPEN, FILLED, CANCELED
    fill_event: asyncio.Event = field(default_factory=asyncio.Event)
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    
    def on_fill(self, quantity: Decimal, price: Decimal) -> None:
        """
        Handle fill callback from websocket.
        
        Args:
            quantity: Incremental fill quantity (not cumulative)
            price: Fill price
        """
        if quantity is None or quantity <= Decimal("0"):
            return
        
        # Update filled quantity (thread-safe for websocket callbacks)
        self.filled_quantity += quantity
        self.fill_price = price
        
        # Check if fully filled (99% threshold to handle rounding)
        if self.filled_quantity >= self.quantity * Decimal("0.99"):
            self.status = "FILLED"
            self.fill_event.set()
    
    def on_cancel(self, filled_size: Decimal) -> None:
        """
        Handle cancel callback from websocket.
        
        Args:
            filled_size: Final filled size reported by websocket (may be 0 if no fills)
        """
        self.status = "CANCELED"
        
        # Update filled quantity if websocket reports more than we have
        if filled_size is not None and filled_size > self.filled_quantity:
            self.filled_quantity = filled_size
        
        self.cancel_event.set()
    
    async def wait_for_event(self, timeout: float) -> str:
        """
        Wait for fill or cancel event.
        
        Args:
            timeout: Maximum time to wait in seconds
            
        Returns:
            Final status: "FILLED", "CANCELED", or "TIMEOUT"
        """
        # Create tasks for both events
        fill_task = asyncio.create_task(self.fill_event.wait())
        cancel_task = asyncio.create_task(self.cancel_event.wait())
        
        try:
            done, pending = await asyncio.wait(
                {fill_task, cancel_task},
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED
            )
            
            # Cancel pending tasks
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            
            if not done:
                return "TIMEOUT"
            
            # Return current status (will be FILLED or CANCELED)
            return self.status
            
        except Exception:
            # Cleanup on error
            fill_task.cancel()
            cancel_task.cancel()
            return "TIMEOUT"

