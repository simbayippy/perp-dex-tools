"""
Message parsing and routing for Backpack WebSocket.

Handles incoming WebSocket message parsing, type detection, and routing to appropriate handlers.
"""

import json
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Dict, Optional, Awaitable


class BackpackMessageHandler:
    """Handles WebSocket message parsing and routing."""

    def __init__(
        self,
        order_update_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
        liquidation_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
        logger: Optional[Any] = None,
    ):
        """
        Initialize message handler.
        
        Args:
            order_update_callback: Callback for order updates
            liquidation_callback: Callback for liquidations
            logger: Logger instance
        """
        self.order_update_callback = order_update_callback
        self.liquidation_callback = liquidation_callback
        self.logger = logger

    def set_logger(self, logger):
        """Set the logger instance."""
        self.logger = logger

    def _log(self, message: str, level: str = "INFO"):
        """Log message using the logger if available."""
        if self.logger:
            if hasattr(self.logger, 'log'):
                self.logger.log(message, level)
            elif level == "ERROR" and hasattr(self.logger, 'error'):
                self.logger.error(message)
            elif level == "WARNING" and hasattr(self.logger, 'warning'):
                self.logger.warning(message)
            elif level == "DEBUG" and hasattr(self.logger, 'debug'):
                self.logger.debug(message)
            elif hasattr(self.logger, 'info'):
                self.logger.info(message)

    async def process_account_message(self, message: str) -> None:
        """
        Process account stream message.
        
        Args:
            message: Raw message string
        """
        try:
            data = json.loads(message)
        except json.JSONDecodeError as exc:
            self._log(f"[BACKPACK] Failed to decode account message: {exc}", "ERROR")
            return

        stream = data.get("stream", "")
        payload = data.get("data", {})

        if "orderUpdate" in stream:
            await self._handle_order_update(payload)
        else:
            self._log(f"[BACKPACK] Ignoring account stream message: {data}", "DEBUG")

    async def _handle_order_update(self, payload: Dict[str, Any]) -> None:
        """
        Handle order update from account stream.
        
        Args:
            payload: Order update payload
        """
        if not self.order_update_callback:
            return
        try:
            await self.order_update_callback(payload)
        except Exception as exc:
            self._log(f"[BACKPACK] Order update callback failed: {exc}", "ERROR")

        # Check for liquidation events
        await self._maybe_dispatch_liquidation(payload)

    async def _maybe_dispatch_liquidation(self, payload: Dict[str, Any]) -> None:
        """
        Check if order update is a liquidation event and dispatch if so.
        
        Args:
            payload: Order update payload
        """
        if not self.liquidation_callback:
            return

        origin = (payload.get("O") or "").upper()
        event_type = (payload.get("e") or "").lower()
        if origin not in {
            "LIQUIDATION_AUTOCLOSE",
            "ADL_AUTOCLOSE",
            "BACKSTOP_LIQUIDITY_PROVIDER",
        }:
            return
        if event_type != "orderfill":
            return

        last_fill = payload.get("l")
        executed = payload.get("z")
        try:
            last_qty = Decimal(str(last_fill)) if last_fill is not None else Decimal("0")
        except (InvalidOperation, TypeError):
            last_qty = Decimal("0")
        if last_qty <= 0:
            try:
                exec_qty = Decimal(str(executed)) if executed is not None else Decimal("0")
            except (InvalidOperation, TypeError):
                exec_qty = Decimal("0")
            if exec_qty <= 0:
                return
        try:
            await self.liquidation_callback(payload)
        except Exception as exc:
            self._log(f"[BACKPACK] Liquidation callback failed: {exc}", "ERROR")

    def process_depth_message(self, message: str) -> Dict[str, Any]:
        """
        Process depth stream message.
        
        Args:
            message: Raw message string
            
        Returns:
            Dictionary with message type and payload
        """
        try:
            data = json.loads(message)
        except json.JSONDecodeError as exc:
            self._log(f"[BACKPACK] Failed to decode depth message: {exc}", "ERROR")
            return {"type": None, "payload": None}

        stream = data.get("stream", "")
        payload = data.get("data", {})

        if stream.startswith("depth"):
            return {"type": "depth", "payload": payload}
        elif stream.startswith("bookTicker"):
            return {"type": "book_ticker", "payload": payload}
        else:
            self._log(f"[BACKPACK] Ignoring depth stream message: {data}", "DEBUG")
            return {"type": None, "payload": None}

