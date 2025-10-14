"""
Dashboard event bus for broadcasting live updates to subscribers.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Set


class DashboardEventBus:
    """Simple pub/sub bus; each subscriber receives JSON-serializable dicts."""

    def __init__(self) -> None:
        self._subscribers: Set[asyncio.Queue] = set()
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        async with self._lock:
            self._subscribers.add(queue)
        return queue

    async def unsubscribe(self, queue: asyncio.Queue) -> None:
        async with self._lock:
            self._subscribers.discard(queue)

    async def broadcast(self, message: Dict[str, Any]) -> None:
        async with self._lock:
            for queue in list(self._subscribers):
                try:
                    queue.put_nowait(message)
                except asyncio.QueueFull:
                    try:
                        queue.get_nowait()
                        queue.put_nowait(message)
                    except asyncio.QueueEmpty:
                        pass


event_bus = DashboardEventBus()
