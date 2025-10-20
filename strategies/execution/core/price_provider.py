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
from typing import Dict, Optional, Tuple

from exchange_clients import BaseExchangeClient
from helpers.unified_logger import get_core_logger

logger = get_core_logger("price_provider")


class PriceProvider:
    """
    Unified price provider that always fetches fresh market data.

    Example:
        provider = PriceProvider()
        bid, ask = await provider.get_bbo_prices(exchange_client, "BTC")
    """

    def __init__(self) -> None:
        """
        """
        self.logger = get_core_logger("price_provider")

    async def get_bbo_prices(self, exchange_client: BaseExchangeClient, symbol: str) -> Tuple[Decimal, Decimal]:
        """
        Fetch the best bid/offer for a symbol from the freshest available source.

        Raises:
            ValueError: If neither source can deliver a valid book.
        """
        exchange_name = exchange_client.get_exchange_name()

        rest_error: Optional[Exception] = None
        try:
            return await self._fetch_exchange_bbo(exchange_client, symbol, exchange_name)
        except Exception as exc:  
            rest_error = exc
            self.logger.warning(
                f"⚠️ [PRICE] REST BBO fetch failed for {exchange_name}:{symbol}: {exc}"
            )

        error_message = (
            f"Unable to fetch BBO prices for {exchange_name}:{symbol}"
            f"  - check that fetch_bbo_prices() is implemented in the exchange client"
        )
        raise ValueError(error_message)

    async def _fetch_exchange_bbo(
        self,
        exchange_client: BaseExchangeClient,
        symbol: str,
        exchange_name: str,
    ) -> Tuple[Decimal, Decimal]:
        """
        Request fresh best bid/offer via the exchange's interface.
        """

        bid, ask = await exchange_client.fetch_bbo_prices(symbol)

        bid_dec = Decimal(str(bid))
        ask_dec = Decimal(str(ask))
        self.logger.info(
            f"✅ [{exchange_name.upper()}] BBO: bid={bid_dec}, ask={ask_dec}"
        )
        return bid_dec, ask_dec
