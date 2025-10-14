"""
Shared event primitives for exchange clients.

Provides plumbing for streaming events (e.g., liquidations) that can be
consumed by multiple strategy components concurrently.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Iterable, Optional, Set


@dataclass(frozen=True)
class LiquidationEvent:
    """
    Normalised liquidation notification emitted by an exchange client.

    Attributes:
        exchange: Canonical exchange name (e.g., "lighter", "aster")
        symbol: Trading pair or contract symbol (e.g., "BTCUSDT", "BTC-PERP")
        side: Side that was liquidated ("buy" or "sell" context dependent)
        quantity: Absolute size liquidated
        price: Execution/average price of the liquidation
        timestamp: Event timestamp (UTC)
        metadata: Additional raw fields for downstream consumers
    """

    exchange: str
    symbol: str
    side: str
    quantity: Decimal
    price: Decimal
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = field(default_factory=dict)


class LiquidationEventDispatcher:
    """
    Broadcast helper for liquidation events.

    Consumers register queues; emit() fan-outs events to all listeners.
    """

    def __init__(self) -> None:
        self._queues: Set[asyncio.Queue[LiquidationEvent]] = set()

    def register(self, queue: Optional[asyncio.Queue[LiquidationEvent]] = None) -> asyncio.Queue[LiquidationEvent]:
        """
        Register a queue to receive liquidation events.

        Args:
            queue: Optional pre-created asyncio.Queue. If omitted, a new queue is created.
        """
        target_queue: asyncio.Queue[LiquidationEvent]
        if queue is None:
            target_queue = asyncio.Queue()
        else:
            target_queue = queue

        self._queues.add(target_queue)
        return target_queue

    def unregister(self, queue: asyncio.Queue[LiquidationEvent]) -> None:
        """Remove a previously registered queue."""
        self._queues.discard(queue)

    async def emit(self, event: LiquidationEvent) -> None:
        """
        Fan out event to all registered queues.

        Emits asynchronously to avoid blocking the producer.
        """
        if not self._queues:
            return

        await asyncio.gather(
            *[self._safe_put(queue, event) for queue in list(self._queues)],
            return_exceptions=True,
        )

    async def _safe_put(self, queue: asyncio.Queue[LiquidationEvent], event: LiquidationEvent) -> None:
        try:
            await queue.put(event)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Drop queue on unexpected error to avoid spamming failures.
            self._queues.discard(queue)

    def listeners(self) -> Iterable[asyncio.Queue[LiquidationEvent]]:
        """Expose current listeners (read-only)."""
        return tuple(self._queues)

