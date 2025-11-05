"""
WebSocket handlers module for Lighter client.

Handles WebSocket callback processing for orders, positions, liquidations, and user stats.
"""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

from exchange_clients.base_models import LiquidationEvent, OrderInfo


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
        orders_cache: Dict[str, Dict[str, Any]],
        latest_orders: Dict[str, OrderInfo],
        client_to_server_order_index: Dict[str, str],
        current_order_client_id_ref: Any,
        current_order_ref: Any,
        order_fill_callback: Optional[Any] = None,
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
            orders_cache: Orders cache dictionary (client.orders_cache)
            latest_orders: Latest orders dictionary (client._latest_orders)
            client_to_server_order_index: Mapping from client to server order IDs
            current_order_client_id_ref: Reference to client.current_order_client_id
            current_order_ref: Reference to client.current_order
            order_fill_callback: Optional callback for order fills
            order_manager: Optional order manager (for notifications)
            position_manager: Optional position manager (for position updates)
            account_manager: Optional account manager (for user stats)
            emit_liquidation_event_fn: Function to emit liquidation events
            get_exchange_name_fn: Function to get exchange name
            normalize_symbol_fn: Function to normalize symbols
        """
        self.config = config
        self.logger = logger
        self.orders_cache = orders_cache
        self.latest_orders = latest_orders
        self.client_to_server_order_index = client_to_server_order_index
        self.current_order_client_id_ref = current_order_client_id_ref
        self.current_order_ref = current_order_ref
        self.order_fill_callback = order_fill_callback
        self.order_manager = order_manager
        self.position_manager = position_manager
        self.account_manager = account_manager
        self.emit_liquidation_event = emit_liquidation_event_fn
        self.get_exchange_name = get_exchange_name_fn or (lambda: "lighter")
        self.normalize_symbol = normalize_symbol_fn or (lambda s: s.upper())
    
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
            status = str(order_data.get('status', '')).upper()
            filled_size = Decimal(str(order_data.get('filled_base_amount', '0')))
            size = Decimal(str(order_data.get('initial_base_amount', '0')))
            price = Decimal(str(order_data.get('price', '0')))
            remaining_size = Decimal(str(order_data.get('remaining_base_amount', '0')))

            if order_id in self.orders_cache.keys():
                if (self.orders_cache[order_id]['status'] == 'OPEN' and
                        status == 'OPEN' and
                        filled_size == self.orders_cache[order_id]['filled_size']):
                    continue
                elif status in ['FILLED', 'CANCELED']:
                    del self.orders_cache[order_id]
                    self.client_to_server_order_index.pop(order_id, None)
                else:
                    self.orders_cache[order_id]['status'] = status
                    self.orders_cache[order_id]['filled_size'] = filled_size
            elif status == 'OPEN':
                self.orders_cache[order_id] = {'status': status, 'filled_size': filled_size}

            if status == 'OPEN' and filled_size > 0:
                status = 'PARTIALLY_FILLED'

            # log websocket order update
            if status == 'OPEN':
                self.logger.info(
                    f"[WEBSOCKET] [LIGHTER] {status} "
                    f"{size} @ {price}"
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
                    cancel_reason=''
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
                            cancel_reason='unknown'
                        )
                self.latest_orders[order_id] = current_order
                if self.order_manager:
                    self.order_manager.notify_order_update(order_id)
                if server_order_index is not None:
                    server_key = str(server_order_index)
                    self.latest_orders[server_key] = current_order
                    if self.order_manager:
                        self.order_manager.notify_order_update(server_key)

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
    
    async def handle_liquidation_notification(self, notifications: List[Dict[str, Any]]) -> None:
        """Normalize liquidation notifications from the Lighter stream."""
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
                    except (ValueError, OSError, OverflowError):
                        timestamp = datetime.now(timezone.utc)
                else:
                    timestamp = datetime.now(timezone.utc)

                metadata = {
                    "notification_id": notification.get("id"),
                    "usdc_amount": content.get("usdc_amount"),
                    "market_index": content.get("market_index"),
                    "acknowledged": notification.get("ack"),
                    "raw": notification,
                }

                event = LiquidationEvent(
                    exchange=self.get_exchange_name(),
                    symbol=getattr(self.config, "ticker", ""),
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
        """Process account positions update received from websocket stream."""
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

