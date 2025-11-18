"""WebSocket feed management utilities."""

from typing import TYPE_CHECKING, Dict, Any

if TYPE_CHECKING:
    from exchange_clients.base_client import BaseExchangeClient


class WebSocketManager:
    """Manages WebSocket feed preparation for exchange clients."""
    
    def __init__(self):
        self._ws_prepared: Dict[str, str] = {}
    
    async def prepare_websocket_feeds(
        self,
        exchange_client: "BaseExchangeClient",
        symbol: str,
        logger: Any,
    ) -> None:
        """
        Ensure exchange WebSocket streams are aligned with the symbol we intend to trade.
        
        Args:
            exchange_client: Exchange client
            symbol: Symbol to prepare feed for
            logger: Logger instance
        """
        try:
            await exchange_client.ensure_market_feed(symbol)
            # Note: ensure_market_feed now waits internally for WebSocket data to be ready
        except Exception as exc:
            logger.debug(
                f"⚠️ [{exchange_client.get_exchange_name().upper()}] WebSocket prep error: {exc}"
            )
    
    async def ensure_market_feed_once(
        self,
        client: "BaseExchangeClient",
        symbol: str,
        logger: Any,
    ) -> None:
        """
        Prepare the client's websocket feed for the target symbol once per session run.
        
        Args:
            client: Exchange client
            symbol: Symbol to prepare
            logger: Logger instance
        """
        exchange_name = client.get_exchange_name().upper()
        symbol_key = symbol.upper()
        previous_symbol = self._ws_prepared.get(exchange_name)
        should_prepare = previous_symbol != symbol_key

        ws_manager = getattr(client, "ws_manager", None)
        if not should_prepare and ws_manager is not None:
            ws_symbol = getattr(ws_manager, "symbol", None)
            if isinstance(ws_symbol, str):
                should_prepare = ws_symbol.upper() != symbol_key

        try:
            if should_prepare:
                await client.ensure_market_feed(symbol)
                # Note: ensure_market_feed now waits for book ticker to be ready
        except Exception as exc:
            logger.debug(
                f"⚠️ [{exchange_name}] WebSocket prep error during close: {exc}"
            )
        else:
            self._ws_prepared[exchange_name] = symbol_key

