"""
Realtime BBO stream helper.

Bridges BaseWebSocketManager best-bid/ask updates to consumer coroutines with
graceful REST fallbacks when streaming data is unavailable.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Tuple

from exchange_clients import BaseExchangeClient
from exchange_clients.base_websocket import BBOData


class PriceStreamError(RuntimeError):
    """Raised when a price stream cannot deliver fresh data."""


@dataclass
class StreamedBBO:
    symbol: str
    bid: Decimal
    ask: Decimal
    timestamp: float
    sequence: Optional[int] = None


class PriceStream:
    """
    Lightweight interface for consuming websocket BBO updates with fallback support.
    """

    def __init__(
        self,
        exchange_client: BaseExchangeClient,
        stream_symbol: str,
        fetch_symbol: Optional[str] = None,
        max_staleness: float = 1.0,
    ) -> None:
        self._exchange = exchange_client
        self._stream_symbol = stream_symbol
        self._fetch_symbol = fetch_symbol or stream_symbol
        self._max_staleness = max_staleness
        self._condition = asyncio.Condition()
        self._latest: Optional[StreamedBBO] = None

        manager = getattr(exchange_client, "ws_manager", None)
        self._has_stream = manager is not None
        if manager is not None:
            manager.register_bbo_listener(self._on_bbo_update)
            cached = manager.get_latest_bbo()
            if cached:
                self._latest = _convert_bbo(cached)

    async def _on_bbo_update(self, bbo: BBOData) -> None:
        if bbo.symbol and bbo.symbol != self._stream_symbol:
            return
        streamed = _convert_bbo(bbo)
        async with self._condition:
            self._latest = streamed
            self._condition.notify_all()

    async def latest(self) -> StreamedBBO:
        """
        Return the most recent BBO, waiting briefly for websocket data before falling back.
        """
        if await self._wait_for_ws_update():
            return self._latest  # type: ignore[return-value]

        bid, ask = await self._exchange.fetch_bbo_prices(self._fetch_symbol)
        streamed = StreamedBBO(
            symbol=self._fetch_symbol,
            bid=bid if isinstance(bid, Decimal) else Decimal(str(bid)),
            ask=ask if isinstance(ask, Decimal) else Decimal(str(ask)),
            timestamp=time.time(),
        )
        async with self._condition:
            self._latest = streamed
        return streamed

    async def wait_for_update(self, timeout: float) -> StreamedBBO:
        """
        Block until a fresh websocket update arrives, respecting the provided timeout.
        """
        if await self._wait_for_ws_update(timeout):
            return self._latest  # type: ignore[return-value]
        raise PriceStreamError(f"No BBO update within {timeout}s for {self._stream_symbol}")

    async def _wait_for_ws_update(self, timeout: Optional[float] = None) -> bool:
        if not self._has_stream:
            return False
        if timeout is None:
            timeout = self._max_staleness

        end_time = time.time() + timeout
        async with self._condition:
            while True:
                if self._latest and (time.time() - self._latest.timestamp) <= self._max_staleness:
                    return True
                remaining = end_time - time.time()
                if remaining <= 0:
                    return False
                try:
                    await asyncio.wait_for(self._condition.wait(), timeout=remaining)
                except asyncio.TimeoutError:
                    return False

    def latest_nowait(self) -> Optional[StreamedBBO]:
        """
        Return the latest BBO without blocking; result may be stale.
        """
        return self._latest


def _convert_bbo(bbo: BBOData) -> StreamedBBO:
    bid = bbo.bid if isinstance(bbo.bid, Decimal) else Decimal(str(bbo.bid))
    ask = bbo.ask if isinstance(bbo.ask, Decimal) else Decimal(str(bbo.ask))
    timestamp = bbo.timestamp or time.time()
    return StreamedBBO(
        symbol=bbo.symbol,
        bid=bid,
        ask=ask,
        timestamp=timestamp,
        sequence=bbo.sequence,
    )
