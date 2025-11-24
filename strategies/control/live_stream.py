"""
Live position streaming helpers for the Control API.

Bridges exchange WebSocket BBO streams to FastAPI WebSocket clients so
terminal viewers can receive mark/price updates without repeatedly
polling REST endpoints.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from fastapi import WebSocket

try:
    from exchange_clients.base_websocket import BBOData
except ImportError:  # pragma: no cover - optional dependency during docs builds
    BBOData = Any  # type: ignore


def _to_float(value: Any) -> Optional[float]:
    """Best-effort conversion to float."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass
class StreamEvent:
    """Serialized payload sent to websocket clients."""

    type: str
    exchange: str
    symbol: Optional[str]
    bid: Optional[float]
    ask: Optional[float]
    timestamp: float
    sequence: Optional[int]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "exchange": self.exchange,
            "symbol": self.symbol,
            "bid": self.bid,
            "ask": self.ask,
            "timestamp": self.timestamp,
            "sequence": self.sequence,
        }


class LiveStreamConnection:
    """Represents a websocket subscriber with a send queue."""

    def __init__(
        self,
        websocket: WebSocket,
        user_info: Dict[str, Any],
        disconnect_cb: Callable[["LiveStreamConnection"], Awaitable[None]],
        queue_size: int = 256,
    ) -> None:
        self.websocket = websocket
        self.user_info = user_info
        self._disconnect_cb = disconnect_cb
        self._queue: asyncio.Queue[Optional[Dict[str, Any]]] = asyncio.Queue(maxsize=queue_size)
        self._send_task = asyncio.create_task(self._sender())
        self._active = True

    async def _sender(self) -> None:
        """Background task that flushes the queue to the websocket."""
        try:
            while True:
                payload = await self._queue.get()
                if payload is None:
                    break
                await self.websocket.send_json(payload)
        except Exception:
            # Connection dropped or websocket closed by client
            pass
        finally:
            if self._active:
                await self._disconnect_cb(self)

    def enqueue(self, payload: Dict[str, Any]) -> None:
        """Queue a payload for delivery, dropping oldest on overflow."""
        if not self._active:
            return
        try:
            self._queue.put_nowait(payload)
        except asyncio.QueueFull:
            # Drop the oldest payload to make room for the newest update
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                self._queue.put_nowait(payload)
            except asyncio.QueueFull:
                # Queue still full (unlikely) - drop the new payload
                pass

    async def close(self) -> None:
        """Terminate sender task and close websocket."""
        if not self._active:
            return
        self._active = False
        try:
            self._queue.put_nowait(None)
        except asyncio.QueueFull:
            # Queue is full - clear one slot and insert sentinel
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                self._queue.put_nowait(None)
            except asyncio.QueueFull:
                pass
        self._send_task.cancel()
        try:
            await self.websocket.close()
        except Exception:
            pass


class LivePositionStreamManager:
    """
    Coordinates websocket subscribers and relays BBO events.

    Strategy instances call `attach_strategy` so we can subscribe to their
    exchange websocket managers. FastAPI websocket handlers then register
    clients through `register_connection`.
    """

    def __init__(self) -> None:
        self._strategy: Optional[Any] = None
        self._connections: List[LiveStreamConnection] = []
        self._listener_handles: Dict[str, Tuple[Any, Callable[[BBOData], Awaitable[None]]]] = {}
        self._lock = asyncio.Lock()

    async def register_connection(
        self,
        websocket: WebSocket,
        user_info: Dict[str, Any],
    ) -> LiveStreamConnection:
        """Register a websocket client and return the connection handle."""
        connection = LiveStreamConnection(websocket, user_info, self._on_connection_lost)
        async with self._lock:
            self._connections.append(connection)
        connection.enqueue(
            {
                "type": "hello",
                "message": "live position stream connected",
                "username": user_info.get("username"),
            }
        )
        return connection

    async def unregister_connection(self, connection: LiveStreamConnection) -> None:
        """Explicitly remove a websocket client (e.g., on disconnect)."""
        async with self._lock:
            if connection in self._connections:
                self._connections.remove(connection)
        await connection.close()

    async def _on_connection_lost(self, connection: LiveStreamConnection) -> None:
        """Cleanup callback invoked by LiveStreamConnection on errors."""
        await self.unregister_connection(connection)

    def attach_strategy(self, strategy: Optional[Any]) -> None:
        """
        Attach to an active strategy so we can subscribe to its exchanges.

        Args:
            strategy: FundingArbitrageStrategy instance (may be None)
        """
        self._strategy = strategy
        self._register_exchange_listeners()

    def _register_exchange_listeners(self) -> None:
        """Register BBO listeners on each exchange client exactly once."""
        if not self._strategy:
            return

        exchange_clients = getattr(self._strategy, "exchange_clients", {}) or {}
        for name, client in exchange_clients.items():
            if name in self._listener_handles:
                continue

            ws_manager = getattr(client, "ws_manager", None)
            if not ws_manager or not hasattr(ws_manager, "register_bbo_listener"):
                continue

            async def listener(bbo: BBOData, exchange_name: str = name) -> None:
                await self._handle_bbo_update(exchange_name, bbo)

            try:
                ws_manager.register_bbo_listener(listener)
                self._listener_handles[name] = (ws_manager, listener)
            except Exception:
                # If registration fails, skip this exchange but don't crash server
                continue

    async def _handle_bbo_update(self, exchange_name: str, bbo: BBOData) -> None:
        """Convert BBOData to a JSON-friendly payload and broadcast."""
        payload = StreamEvent(
            type="bbo",
            exchange=exchange_name.upper(),
            symbol=str(bbo.symbol).upper() if getattr(bbo, "symbol", None) else None,
            bid=_to_float(getattr(bbo, "bid", None)),
            ask=_to_float(getattr(bbo, "ask", None)),
            timestamp=float(getattr(bbo, "timestamp", None) or time.time()),
            sequence=getattr(bbo, "sequence", None),
        ).to_dict()

        await self._broadcast(payload)

    async def _broadcast(self, payload: Dict[str, Any]) -> None:
        """Fan out payload to all active subscribers."""
        if not self._connections:
            return

        async with self._lock:
            connections_snapshot = list(self._connections)

        stale: List[LiveStreamConnection] = []
        for connection in connections_snapshot:
            try:
                connection.enqueue(payload)
            except Exception:
                stale.append(connection)

        for connection in stale:
            await self.unregister_connection(connection)

