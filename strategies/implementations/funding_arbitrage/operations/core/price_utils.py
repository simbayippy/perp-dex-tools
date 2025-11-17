"""Price extraction and fetching utilities."""

from decimal import Decimal
from typing import TYPE_CHECKING, Optional, Any

if TYPE_CHECKING:
    from exchange_clients.base_models import ExchangePositionSnapshot
    from exchange_clients.base_client import BaseExchangeClient


def extract_snapshot_price(snapshot: "ExchangePositionSnapshot") -> Optional[Decimal]:
    """
    Extract price from position snapshot.
    
    Tries multiple attributes in order: mark_price, entry_price, or calculated from exposure/quantity.
    
    Args:
        snapshot: Position snapshot
        
    Returns:
        Price as Decimal, or None if unable to determine
    """
    for attr in ("mark_price", "entry_price"):
        value = getattr(snapshot, attr, None)
        if value is not None and value > 0:
            return value

    exposure = getattr(snapshot, "exposure_usd", None)
    quantity = getattr(snapshot, "quantity", None)
    if exposure is not None and quantity:
        try:
            return (exposure / quantity.copy_abs()).copy_abs()
        except Exception:
            return None
    return None


async def fetch_mid_price(
    client: "BaseExchangeClient",
    symbol: str,
    logger: Any,  # type: ignore
) -> Optional[Decimal]:
    """
    Fetch mid price (average of best bid and ask) from exchange.
    
    Args:
        client: Exchange client
        symbol: Symbol to fetch price for
        logger: Logger instance
        
    Returns:
        Mid price as Decimal, or None if unable to fetch
    """
    try:
        best_bid, best_ask = await client.fetch_bbo_prices(symbol)
    except Exception as exc:
        logger.warning(
            f"[{client.get_exchange_name()}] Failed to fetch BBO for {symbol}: {exc}"
        )
        return None

    try:
        bid = Decimal(str(best_bid))
        ask = Decimal(str(best_ask))
    except Exception:
        return None

    if bid <= 0 or ask <= 0:
        return None

    return (bid + ask) / 2

