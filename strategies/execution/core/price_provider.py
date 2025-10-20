"""
Price Provider - Unified best bid/offer retrieval without caching.

Provides a lightweight abstraction over exchange market data sources:
 - WebSocket snapshots when available (zero-latency)
 - REST/API fallbacks for guaranteed freshness

This module intentionally avoids local caching so every call reflects the
latest data exposed by the venue. Exchanges are responsible for providing
their own throttling or rate limiting.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Callable, Dict, Optional, Tuple

from helpers.unified_logger import get_core_logger

logger = get_core_logger("price_provider")


class PriceProvider:
    """
    Unified price provider that always fetches fresh market data.

    Example:
        provider = PriceProvider()
        bid, ask = await provider.get_bbo_prices(exchange_client, "BTC")
    """

    def __init__(self, prefer_websocket: bool = False) -> None:
        """
        Args:
            prefer_websocket: If True, try WebSocket snapshots before REST fallbacks.
        """
        self.prefer_websocket = prefer_websocket
        self.logger = get_core_logger("price_provider")

    async def get_bbo_prices(
        self,
        exchange_client: Any,
        symbol: str,
    ) -> Tuple[Decimal, Decimal]:
        """
        Fetch the best bid/offer for a symbol from the freshest available source.

        Preference order:
            1. WebSocket snapshot (if prefer_websocket=True)
            2. REST/API request
            3. WebSocket snapshot (fallback when REST fails and prefer_websocket=False)

        Raises:
            ValueError: If neither source can deliver a valid book.
        """
        exchange_name = exchange_client.get_exchange_name()

        # Optional early WebSocket read.
        if self.prefer_websocket:
            ws_prices = self._try_websocket_snapshot(exchange_client, symbol, exchange_name)
            if ws_prices:
                return ws_prices

        rest_error: Optional[Exception] = None
        try:
            return await self._fetch_rest_bbo(exchange_client, symbol, exchange_name)
        except Exception as exc:  # pragma: no cover - defensive logging
            rest_error = exc
            self.logger.warning(
                f"⚠️ [PRICE] REST BBO fetch failed for {exchange_name}:{symbol}: {exc}"
            )

        # Fallback to WebSocket when REST requests fail or when realtime data is preferred later.
        ws_prices = self._try_websocket_snapshot(exchange_client, symbol, exchange_name)
        if ws_prices:
            return ws_prices

        error_message = (
            f"Unable to fetch BBO prices for {exchange_name}:{symbol}"
            f"{' - REST error: ' + str(rest_error) if rest_error else ''}"
        )
        raise ValueError(error_message)

    async def _fetch_rest_bbo(
        self,
        exchange_client: Any,
        symbol: str,
        exchange_name: str,
    ) -> Tuple[Decimal, Decimal]:
        """
        Request fresh best bid/offer via the exchange's REST interface.
        """
        self.logger.info(
            f"📞 [{exchange_name.upper()}] Fetching fresh BBO for {symbol} via REST/API"
        )

        if hasattr(exchange_client, "fetch_bbo_prices"):
            bid, ask = await exchange_client.fetch_bbo_prices(symbol)
            bid_dec = Decimal(str(bid))
            ask_dec = Decimal(str(ask))
            self.logger.info(
                f"✅ [{exchange_name.upper()}] REST BBO: bid={bid_dec}, ask={ask_dec}"
            )
            return bid_dec, ask_dec

        if hasattr(exchange_client, "get_order_book_depth"):
            order_book = await exchange_client.get_order_book_depth(symbol)
        else:
            raise ValueError(
                f"{exchange_name} client does not expose fetch_bbo_prices or get_order_book_depth"
            )

        bids = order_book.get("bids") if isinstance(order_book, Dict) else None
        asks = order_book.get("asks") if isinstance(order_book, Dict) else None
        if not bids or not asks:
            raise ValueError("Empty order book")

        try:
            best_bid = Decimal(str(bids[0]["price"]))
            best_ask = Decimal(str(asks[0]["price"]))
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ValueError(f"Malformed order book payload: {exc}") from exc

        self.logger.info(
            f"✅ [{exchange_name.upper()}] REST BBO: bid={best_bid}, ask={best_ask}"
        )
        return best_bid, best_ask

    def _try_websocket_snapshot(
        self,
        exchange_client: Any,
        symbol: str,
        exchange_name: str,
    ) -> Optional[Tuple[Decimal, Decimal]]:
        """
        Attempt to read best bid/offer from an attached WebSocket manager.
        """
        getter: Optional[Callable[[], Dict[str, Any]]] = getattr(
            exchange_client, "_get_order_book_from_websocket", None
        )
        if not callable(getter):
            return None

        try:
            snapshot = getter()
        except Exception as exc:  # pragma: no cover - defensive logging
            self.logger.debug(
                f"⚠️ [PRICE] WebSocket snapshot unavailable for {exchange_name}:{symbol}: {exc}"
            )
            return None

        if not snapshot or not snapshot.get("bids") or not snapshot.get("asks"):
            return None

        try:
            best_bid = Decimal(str(snapshot["bids"][0]["price"]))
            best_ask = Decimal(str(snapshot["asks"][0]["price"]))
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            self.logger.debug(
                f"⚠️ [PRICE] Invalid WebSocket snapshot for {exchange_name}:{symbol}: {exc}"
            )
            return None

        self.logger.info(
            f"📡 [PRICE] Using WebSocket BBO for {exchange_name}:{symbol} "
            f"(bid={best_bid}, ask={best_ask})"
        )
        return best_bid, best_ask
