"""
WebSocket handlers module for Lighter client.

Handles WebSocket callback processing for orders, positions, liquidations, and user stats.

Contains the business logic for the WebSocket handlers, what to do with Events received from the WebSocket.
"""

import asyncio
import time
from collections import deque
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

from exchange_clients.base_models import CancelReason, OrderInfo
from exchange_clients.events import LiquidationEvent


class LighterWebSocketHandlers:
    """
    WebSocket handlers for Lighter exchange.
    
    Handles:
    - Order update callbacks
    - Position stream updates
    - Liquidation notifications
    - User stats updates (delegated to account_manager)
    """
    
    def __init__(
        self,
        config: Any,
        logger: Any,
        latest_orders: Dict[str, OrderInfo],
        client_to_server_order_index: Dict[str, str],
        current_order_client_id_ref: Any,
        current_order_ref: Any,
        order_fill_callback: Optional[Any] = None,
        order_status_callback: Optional[Any] = None,
        order_manager: Optional[Any] = None,
        position_manager: Optional[Any] = None,
        account_manager: Optional[Any] = None,
        emit_liquidation_event_fn: Optional[Any] = None,
        get_exchange_name_fn: Optional[Any] = None,
        normalize_symbol_fn: Optional[Any] = None,
    ):
        """
        Initialize WebSocket handlers.
        
        Args:
            config: Trading configuration object
            logger: Logger instance
            latest_orders: Latest orders dictionary (client._latest_orders) - stores OrderInfo objects
            client_to_server_order_index: Mapping from client to server order IDs
            current_order_client_id_ref: Reference to client.current_order_client_id
            current_order_ref: Reference to client.current_order
            order_fill_callback: Optional callback for incremental order fills
            order_status_callback: Optional callback for order status changes (FILLED/CANCELED)
            order_manager: Optional order manager (for notifications)
            position_manager: Optional position manager (for position updates)
            account_manager: Optional account manager (for user stats)
            emit_liquidation_event_fn: Function to emit liquidation events
            get_exchange_name_fn: Function to get exchange name
            normalize_symbol_fn: Function to normalize symbols
        """
        self.config = config
        self.logger = logger
        self.latest_orders = latest_orders
        self.client_to_server_order_index = client_to_server_order_index
        self.current_order_client_id_ref = current_order_client_id_ref
        self.current_order_ref = current_order_ref
        self.order_fill_callback = order_fill_callback
        self.order_status_callback = order_status_callback
        self.order_manager = order_manager
        self.position_manager = position_manager
        self.account_manager = account_manager
        self.emit_liquidation_event = emit_liquidation_event_fn
        self.get_exchange_name = get_exchange_name_fn or (lambda: "lighter")
        self.normalize_symbol = normalize_symbol_fn or (lambda s: s.upper())
        
        # Track recent liquidations to correlate with order fills
        # Format: deque of (market_id, quantity, price, timestamp, symbol)
        # Keep for 30 seconds - liquidations and order fills should arrive nearly simultaneously
        # (within seconds, not minutes)
        self._recent_liquidations: deque[Tuple[int, Decimal, Decimal, float, str]] = deque(maxlen=100)
    
    def handle_websocket_order_update(self, order_data_list: List[Dict[str, Any]]) -> None:
        """Handle order updates from WebSocket."""
        for order_data in order_data_list:
            market_index = order_data.get('market_index')
            client_order_index = order_data.get('client_order_index')
            server_order_index = order_data.get('order_index')

            if market_index is None or client_order_index is None:
                continue

            if server_order_index is not None:
                self.client_to_server_order_index[str(client_order_index)] = str(server_order_index)

            if str(market_index) != str(self.config.contract_id):
                self.logger.info(
                    f"[LIGHTER] Ignoring order update for market {market_index} "
                    f"(expected {self.config.contract_id})"
                )
                continue

            side = 'sell' if order_data['is_ask'] else 'buy'
            # Let strategy determine order type - exchange client just reports the order
            order_type = "ORDER"

            order_id = str(client_order_index)
            linked_order_index = str(server_order_index) if server_order_index is not None else "?"
            status_raw = str(order_data.get('status', '')).upper()
            filled_size = Decimal(str(order_data.get('filled_base_amount', '0')))
            size = Decimal(str(order_data.get('initial_base_amount', '0')))
            price = Decimal(str(order_data.get('price', '0')))
            remaining_size = Decimal(str(order_data.get('remaining_base_amount', '0')))

            # Parse cancellation reason from status
            # Lighter sends "CANCELED-POST-ONLY" for post-only violations
            cancel_reason = ""
            status = status_raw
            if status_raw.startswith("CANCELED"):
                if status_raw == "CANCELED-POST-ONLY":
                    cancel_reason = CancelReason.POST_ONLY_VIOLATION
                    # Normalize status to CANCELED for consistency
                    status = "CANCELED"
                else:
                    # Other cancellation types - default to unknown
                    cancel_reason = CancelReason.UNKNOWN

            # Determine final status before deduplication check
            final_status = status
            if status == 'OPEN' and filled_size > 0:
                final_status = 'PARTIALLY_FILLED'

            # Deduplication: Skip duplicate OPEN and PARTIALLY_FILLED updates with same filled_size
            existing_order = self.latest_orders.get(order_id)
            should_skip_log = False
            if existing_order:
                # Skip logging if status and filled_size haven't changed
                if ((existing_order.status == 'OPEN' and final_status == 'OPEN') or
                    (existing_order.status == 'PARTIALLY_FILLED' and final_status == 'PARTIALLY_FILLED')):
                    if filled_size == existing_order.filled_size:
                        should_skip_log = True
                elif final_status in ['FILLED', 'CANCELED']:
                    # Clean up filled/canceled orders (but keep in latest_orders for querying)
                    self.client_to_server_order_index.pop(order_id, None)

            # Use final_status for rest of processing
            status = final_status

            # Check if this fill is from a liquidation
            is_liquidation_fill = False
            if status == 'FILLED' and filled_size > 0:
                is_liquidation_fill = self._is_liquidation_fill(
                    market_id=int(market_index),
                    filled_size=filled_size,
                    price=price
                )
                
                if is_liquidation_fill:
                    symbol = getattr(self.config, "ticker", "UNKNOWN")
                    self.logger.error(
                        f"🚨 [LIGHTER] LIQUIDATION FILL DETECTED: Order {order_id} | "
                        f"Symbol: {symbol} | Side: {side.upper()} | "
                        f"Qty: {filled_size} @ {price} | Market ID: {market_index}"
                    )

            # log websocket order update (skip if duplicate)
            if not should_skip_log:
                if status == 'OPEN':
                    self.logger.info(
                        f"[WEBSOCKET] [LIGHTER] {status} "
                        f"{size} @ {price}"
                    )
                elif is_liquidation_fill:
                    # Already logged above with ERROR level, just log transaction
                    self.logger.info(
                        f"[WEBSOCKET] [LIGHTER] {status} (LIQUIDATION) "
                        f"{filled_size} @ {price}"
                    )
                else:
                    self.logger.info(
                        f"[WEBSOCKET] [LIGHTER] {status} "
                        f"{filled_size} @ {price}"
                    )

            current_order = None
            current_order_client_id = getattr(self.current_order_client_id_ref, 'current_order_client_id', None) if self.current_order_client_id_ref else None
            if order_data.get('client_order_index') == current_order_client_id or order_type == 'OPEN':
                current_order = OrderInfo(
                    order_id=order_id,
                    side=side,
                    size=size,
                    price=price,
                    status=status,
                    filled_size=filled_size,
                    remaining_size=remaining_size,
                    cancel_reason=cancel_reason
                )
                if self.current_order_ref is not None:
                    setattr(self.current_order_ref, 'current_order', current_order)
                self.latest_orders[order_id] = current_order
                if self.order_manager:
                    self.order_manager.notify_order_update(order_id)
                if server_order_index is not None:
                    server_key = str(server_order_index)
                    self.latest_orders[server_key] = current_order
                    if self.order_manager:
                        self.order_manager.notify_order_update(server_key)

            if status in ['FILLED', 'CANCELED']:
                self.logger.log_transaction(order_id, side, filled_size, price, status)
                if current_order is None:
                    current_order = self.latest_orders.get(order_id)
                    if current_order is None:
                        current_order = OrderInfo(
                            order_id=order_id,
                            side=side,
                            size=size,
                            price=price,
                            status=status,
                            filled_size=filled_size,
                            remaining_size=remaining_size,
                            cancel_reason=cancel_reason or CancelReason.UNKNOWN
                        )
                self.latest_orders[order_id] = current_order
                if self.order_manager:
                    self.order_manager.notify_order_update(order_id)
                if server_order_index is not None:
                    server_key = str(server_order_index)
                    self.latest_orders[server_key] = current_order
                    if self.order_manager:
                        self.order_manager.notify_order_update(server_key)

                # Call incremental fill callback (for FILLED orders)
                if status == 'FILLED' and self.order_fill_callback:
                    try:
                        sequence = getattr(order_data, 'offset', None)
                    except Exception:
                        sequence = None
                    asyncio.get_running_loop().create_task(
                        self.order_fill_callback(
                            order_id,
                            price,
                            filled_size,
                            sequence,
                        )
                    )
                
                # Call status callback for final states (FILLED/CANCELED)
                if self.order_status_callback and status in ['FILLED', 'CANCELED']:
                    try:
                        asyncio.get_running_loop().create_task(
                            self.order_status_callback(
                                order_id,
                                status,
                                filled_size,
                                price,
                            )
                        )
                    except RuntimeError:
                        # No running loop - log warning but continue
                        self.logger.warning(
                            f"Cannot call order_status_callback for {order_id}: no running event loop"
                        )
    
    def _is_liquidation_fill(
        self, 
        market_id: int, 
        filled_size: Decimal, 
        price: Decimal,
        timestamp: Optional[float] = None
    ) -> bool:
        """
        Check if an order fill matches a recent liquidation event.
        
        Args:
            market_id: Market ID of the order
            filled_size: Filled quantity
            price: Fill price
            timestamp: Optional timestamp of the fill (defaults to now)
            
        Returns:
            True if this fill matches a recent liquidation
        """
        if timestamp is None:
            timestamp = time.time()
        
        # Check against recent liquidations (within 30 seconds)
        # Liquidations and order fills should arrive nearly simultaneously (within seconds)
        for liq_market_id, liq_quantity, liq_price, liq_timestamp, _ in self._recent_liquidations:
            # Check if market matches
            if liq_market_id != market_id:
                continue
            
            # Check if timestamp is within 30 seconds
            # Liquidations happen instantly, so notification and order fill should arrive
            # within seconds of each other (accounting for network/processing delays)
            if abs(timestamp - liq_timestamp) > 30:  # 30 seconds
                continue
            
            # Check if quantity matches (within 1% tolerance for rounding)
            quantity_diff = abs(filled_size - liq_quantity)
            if quantity_diff / max(filled_size, liq_quantity, Decimal("1")) > Decimal("0.01"):
                continue
            
            # Check if price is reasonably close (within 5% - liquidations can have different prices)
            price_diff = abs(price - liq_price)
            if price_diff / max(price, liq_price, Decimal("0.0001")) > Decimal("0.05"):
                continue
            
            return True
        
        return False

    async def handle_liquidation_notification(self, notifications: List[Dict[str, Any]]) -> None:
        """
        Normalize liquidation notifications from the Lighter stream.
        
        Also tracks recent liquidations to correlate with order fills.
        """
        for notification in notifications:
            try:
                if notification.get("kind") != "liquidation":
                    continue

                content = notification.get("content", {})
                if not content:
                    continue

                quantity = Decimal(str(content.get("size") or "0")).copy_abs()
                if quantity <= 0:
                    continue

                price_source = content.get("avg_price") or content.get("price")
                price = Decimal(str(price_source or "0"))
                side = "sell" if content.get("is_ask") else "buy"

                raw_timestamp = content.get("timestamp")
                if raw_timestamp is not None:
                    try:
                        timestamp = datetime.fromtimestamp(int(raw_timestamp), tz=timezone.utc)
                        timestamp_seconds = int(raw_timestamp)
                    except (ValueError, OSError, OverflowError):
                        timestamp = datetime.now(timezone.utc)
                        timestamp_seconds = time.time()
                else:
                    timestamp = datetime.now(timezone.utc)
                    timestamp_seconds = time.time()

                market_index = content.get("market_index")
                symbol = getattr(self.config, "ticker", "")
                
                # Track this liquidation for correlation with order fills
                if market_index is not None:
                    self._recent_liquidations.append((
                        int(market_index),
                        quantity,
                        price,
                        timestamp_seconds,
                        symbol
                    ))
                    
                    # Log liquidation prominently
                    self.logger.error(
                        f"🚨 [LIGHTER] LIQUIDATION DETECTED: {symbol} | "
                        f"Side: {side.upper()} | Qty: {quantity} | Price: {price} | "
                        f"Market ID: {market_index} | Timestamp: {timestamp.isoformat()}"
                    )

                metadata = {
                    "notification_id": notification.get("id"),
                    "usdc_amount": content.get("usdc_amount"),
                    "market_index": market_index,
                    "acknowledged": notification.get("ack"),
                    "raw": notification,
                }

                event = LiquidationEvent(
                    exchange=self.get_exchange_name(),
                    symbol=symbol,
                    side=side,
                    quantity=quantity,
                    price=price,
                    timestamp=timestamp,
                    metadata=metadata,
                )
                if self.emit_liquidation_event:
                    await self.emit_liquidation_event(event)
            except (InvalidOperation, TypeError) as exc:
                self.logger.warning(
                    f"Failed to parse liquidation notification: {notification} ({exc})"
                )
    
    async def handle_positions_stream_update(self, payload: Dict[str, Any]) -> None:
        """
        Process account positions update received from websocket stream.
        
        Detects when positions suddenly go to zero, which may indicate liquidation.
        """
        positions_map = payload.get("positions") or {}
        if not isinstance(positions_map, dict):
            return

        updates: Dict[str, Dict[str, Any]] = {}

        for market_idx, raw_position in positions_map.items():
            if raw_position is None:
                continue

            position_dict = dict(raw_position)
            market_id = position_dict.get("market_id")
            if market_id is None:
                try:
                    market_id = int(market_idx)
                    position_dict["market_id"] = market_id
                except (TypeError, ValueError):
                    position_dict["market_id"] = market_idx

            symbol_raw = position_dict.get("symbol")
            if not symbol_raw:
                if market_id == getattr(self.config, "contract_id", None):
                    symbol_raw = getattr(self.config, "ticker", None)
            if not symbol_raw:
                symbol_raw = str(market_idx)

            position_dict["symbol"] = symbol_raw
            
            # Detect if position suddenly went to zero (potential liquidation)
            if self.position_manager:
                async with self.position_manager.positions_lock:
                    normalized_symbol = self.normalize_symbol(str(symbol_raw)).upper()
                    previous_position = self.position_manager.raw_positions.get(normalized_symbol)
                    
                    if previous_position:
                        prev_qty = Decimal(str(previous_position.get("position", "0")))
                        current_qty = Decimal(str(position_dict.get("position", "0")))
                        
                        # If we had a position and now it's zero, this might be a liquidation
                        if prev_qty != 0 and current_qty == 0:
                            # Check if this matches a recent liquidation
                            is_known_liquidation = False
                            if market_id is not None:
                                is_known_liquidation = self._is_liquidation_fill(
                                    market_id=int(market_id),
                                    filled_size=prev_qty,
                                    price=Decimal(str(position_dict.get("avg_entry_price", "0")))
                                )
                            
                            if not is_known_liquidation:
                                # Position went to zero but we didn't see liquidation notification
                                # This could be:
                                # - A liquidation we missed
                                # - A manual close
                                # - An expected rollback operation (emergency position closure)
                                # - A normal position close via market order
                                self.logger.warning(
                                    f"⚠️ [LIGHTER] Position suddenly zeroed: {normalized_symbol} | "
                                    f"Previous qty: {prev_qty} | Market ID: {market_id} | "
                                    f"Possible causes: liquidation (no notification), rollback, or manual close"
                                )
                            else:
                                self.logger.info(
                                    f"[LIGHTER] Position zeroed (matches known liquidation): {normalized_symbol} | "
                                    f"Previous qty: {prev_qty} | Market ID: {market_id}"
                                )
            
            # WebSocket provides "total_funding_paid_out", but snapshot_from_cache() expects "funding_accrued"
            # Note: total_funding_paid_out is optional (omitted when empty/None)
            if "total_funding_paid_out" in position_dict and position_dict["total_funding_paid_out"] is not None:
                position_dict["funding_accrued"] = position_dict["total_funding_paid_out"]
            
            normalized_symbol = self.normalize_symbol(str(symbol_raw)).upper()
            updates[normalized_symbol] = position_dict

        # Update position manager's raw_positions if available
        if self.position_manager:
            async with self.position_manager.positions_lock:
                self.position_manager.raw_positions.update(updates)
                self.position_manager.positions_ready.set()
        else:
            # Fallback: update directly if position_manager not available
            # This shouldn't happen in normal operation, but handle gracefully
            self.logger.warning("[LIGHTER] Position manager not available for WebSocket update")

