"""
Helper functions for Backpack client.

Decimal conversion, precision inference, symbol formatting, and price/quantity helpers.
"""

from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_UP
from typing import Any, Optional, Callable

from exchange_clients.backpack.common import (
    get_backpack_symbol_format,
    normalize_symbol as normalize_backpack_symbol,
)


def to_decimal(value: Any, default: Optional[Decimal] = None) -> Optional[Decimal]:
    """Convert various numeric inputs to Decimal safely."""
    if value in (None, "", "null"):
        return default

    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return default


def get_decimal_places(price: Decimal) -> int:
    """
    Infer the number of decimal places from a price.
    
    Examples:
        0.7794 -> 4
        0.001 -> 3
        100.50 -> 2
        1000 -> 0
    """
    if not isinstance(price, Decimal):
        price = Decimal(str(price))
    
    # Get the exponent (negative for decimals)
    sign, digits, exponent = price.as_tuple()
    
    if isinstance(exponent, int):
        # Negative exponent means decimal places
        return abs(exponent) if exponent < 0 else 0
    
    # Fallback: count digits after decimal point in string representation
    price_str = str(price)
    if '.' in price_str:
        return len(price_str.split('.')[1].rstrip('0'))
    return 0


def infer_precision_from_prices(
    symbol: str,
    bid: Decimal,
    ask: Decimal,
    precision_cache: dict,
    logger: Optional[Any] = None,
) -> int:
    """
    Infer the decimal precision for a symbol from observed BBO prices.
    Caches the result for future use.
    
    Args:
        symbol: Symbol to infer precision for
        bid: Best bid price
        ask: Best ask price
        precision_cache: Dictionary to cache precision results
        logger: Optional logger instance
        
    Returns:
        Inferred precision (number of decimal places)
    """
    # Get max precision from both bid and ask
    bid_precision = get_decimal_places(bid)
    ask_precision = get_decimal_places(ask)
    precision = max(bid_precision, ask_precision)
    
    # Cache it
    precision_cache[symbol] = precision
    
    if logger:
        logger.debug(
            f"[BACKPACK] Inferred precision for {symbol}: {precision}dp "
            f"(bid={bid} [{bid_precision}dp], ask={ask} [{ask_precision}dp])"
        )
    
    return precision


def get_symbol_precision(
    symbol: Optional[str],
    precision_cache: dict,
    max_price_decimals: int = 3,
) -> int:
    """
    Get the cached decimal precision for a symbol.
    Falls back to max_price_decimals if not yet inferred.
    
    Args:
        symbol: Symbol to look up
        precision_cache: Dictionary caching precision results
        max_price_decimals: Default max decimal places
        
    Returns:
        Precision (number of decimal places)
    """
    if symbol:
        return precision_cache.get(symbol, max_price_decimals)
    return max_price_decimals


def to_internal_symbol(stream_symbol: Optional[str]) -> str:
    """Convert Backpack stream symbol to internal normalized format."""
    if not stream_symbol:
        return ""
    return normalize_backpack_symbol(stream_symbol).upper()


def ensure_exchange_symbol(
    identifier: Optional[str],
    market_symbol_map: dict,
    ws_manager: Optional[Any] = None,
) -> Optional[str]:
    """
    Normalize symbol/contract inputs to Backpack's expected wire format.
    
    Args:
        identifier: Symbol identifier to normalize
        market_symbol_map: Dictionary mapping normalized symbols to exchange symbols
        ws_manager: Optional WebSocket manager to update symbol
        
    Returns:
        Exchange-formatted symbol or original identifier
    """
    if not identifier:
        return None

    normalized = identifier.upper()
    symbol: Optional[str] = None

    mapped = market_symbol_map.get(normalized)
    if mapped:
        symbol = mapped
    elif "_" in normalized:
        # Already in exchange format (e.g., BTC_USDC_PERP)
        symbol = normalized
    elif normalized not in {"MULTI_SYMBOL", "MULTI"}:
        symbol = get_backpack_symbol_format(normalized)

    if symbol and ws_manager and ws_manager.symbol != symbol:
        ws_manager.update_symbol(symbol)

    return symbol or identifier


def quantize_quantity(
    quantity: Any,
    step_size: Optional[Decimal],
    max_decimals: Optional[int] = None,
) -> Decimal:
    """
    Quantize quantity to appropriate decimal places.
    
    Args:
        quantity: The quantity to quantize
        step_size: Optional step size to quantize to
        max_decimals: Optional maximum decimal places to enforce
        
    Returns:
        Quantized quantity
    """
    if not isinstance(quantity, Decimal):
        quantity = Decimal(str(quantity))

    if not step_size or step_size <= 0:
        # No step_size configured - default to 8 decimal places for crypto
        default_decimals = max_decimals if max_decimals is not None else 8
        quantizer = Decimal(10) ** -default_decimals
        return quantity.quantize(quantizer, rounding=ROUND_DOWN)
    
    try:
        if not isinstance(step_size, Decimal):
            step_size = Decimal(str(step_size))
        quantized = quantity.quantize(step_size, rounding=ROUND_DOWN)
        
        # Enforce max_decimals if provided
        if max_decimals is not None:
            quantizer = Decimal(10) ** -max_decimals
            quantized = quantized.quantize(quantizer, rounding=ROUND_DOWN)
        
        return quantized
    except (InvalidOperation, ValueError):
        decimals = max(0, -Decimal(str(step_size)).normalize().as_tuple().exponent)
        if max_decimals is not None:
            decimals = min(decimals, max_decimals)
        return Decimal(f"{quantity:.{decimals}f}")


def format_decimal(
    value: Any,
    step: Optional[Decimal] = None,
    max_decimals: int = 8,
) -> str:
    """
    Format a decimal value as a string with appropriate precision.
    
    Args:
        value: The value to format
        step: Optional step size to quantize to
        max_decimals: Maximum decimal places (default: 8 for crypto)
        
    Returns:
        Formatted string representation
    """
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    
    if step and step > 0:
        try:
            if not isinstance(step, Decimal):
                step = Decimal(str(step))
            return str(value.quantize(step))
        except (InvalidOperation, ValueError):
            pass
        decimals = max(0, -step.normalize().as_tuple().exponent)
        # Cap at max_decimals
        decimals = min(decimals, max_decimals)
        return f"{value:.{decimals}f}"
    
    # No step provided - default to max_decimals
    return f"{value:.{max_decimals}f}".rstrip('0').rstrip('.')


def enforce_max_decimals(
    price: Decimal,
    symbol: Optional[str],
    get_symbol_precision_fn: Callable[[Optional[str]], int],
    max_price_decimals: int = 3,
) -> Decimal:
    """
    Enforce decimal precision limits for prices.
    
    Uses inferred precision from market data if available for the symbol,
    otherwise falls back to max_price_decimals.
    
    Args:
        price: Price to enforce precision on
        symbol: Optional symbol to use for precision lookup
        get_symbol_precision_fn: Function to get symbol precision
        max_price_decimals: Default max decimal places
        
    Returns:
        Price with appropriate decimal places
    """
    if not isinstance(price, Decimal):
        price = Decimal(str(price))
    
    # Determine precision: use inferred if available, else default
    max_decimals = get_symbol_precision_fn(symbol) if symbol else max_price_decimals
    
    # Get the current number of decimal places
    exponent = price.as_tuple().exponent
    current_decimals = abs(exponent) if isinstance(exponent, int) else 0
    
    # If already within limits, return as is
    if current_decimals <= max_decimals:
        return price
    
    # Round to max_decimals
    tick = Decimal(10) ** -max_decimals
    try:
        return price.quantize(tick, rounding=ROUND_DOWN)
    except (InvalidOperation, TypeError, ValueError):
        # Fallback: use string formatting
        return Decimal(f"{price:.{max_decimals}f}")


def quantize_to_tick(
    price: Decimal,
    rounding_mode,
    tick_size: Optional[Decimal],
    symbol: Optional[str],
    get_symbol_precision_fn: Callable[[Optional[str]], int],
    enforce_max_decimals_fn: Callable,
    max_price_decimals: int = 3,
) -> Decimal:
    """
    Quantize price to tick size with precision enforcement.
    
    Args:
        price: Price to quantize
        rounding_mode: Rounding mode (ROUND_DOWN, ROUND_UP, etc.)
        tick_size: Optional tick size
        symbol: Optional symbol for precision lookup
        get_symbol_precision_fn: Function to get symbol precision
        enforce_max_decimals_fn: Function to enforce max decimals
        max_price_decimals: Default max decimal places
        
    Returns:
        Quantized price
    """
    if tick_size and tick_size > 0:
        try:
            tick = tick_size if isinstance(tick_size, Decimal) else Decimal(str(tick_size))
        except (InvalidOperation, TypeError, ValueError):
            tick = None
        if tick and tick > 0:
            try:
                quantized_price = price.quantize(tick, rounding=rounding_mode)
                # Ensure the result doesn't exceed symbol's decimal precision
                return enforce_max_decimals_fn(quantized_price, symbol)
            except (InvalidOperation, TypeError, ValueError):
                decimals = max(0, -tick.normalize().as_tuple().exponent)
                # Enforce symbol's max decimal precision
                max_decimals = get_symbol_precision_fn(symbol) if symbol else max_price_decimals
                decimals = min(decimals, max_decimals)
                return Decimal(f"{price:.{decimals}f}")
    
    # Enforce symbol's decimal precision
    return enforce_max_decimals_fn(price, symbol)


async def compute_post_only_price(
    contract_id: str,
    raw_price: Decimal,
    side: str,
    tick_size: Optional[Decimal],
    fetch_bbo_fn: Callable,
    quantize_to_tick_fn: Callable,
    logger: Optional[Any] = None,
) -> Decimal:
    """
    Quantize price toward the maker side and avoid matching the top of book.
    
    Args:
        contract_id: Contract identifier
        raw_price: Raw price to adjust
        side: Order side ('buy' or 'sell')
        tick_size: Optional tick size
        fetch_bbo_fn: Function to fetch BBO prices
        quantize_to_tick_fn: Function to quantize to tick
        logger: Optional logger instance
        
    Returns:
        Adjusted post-only price
    """
    price = raw_price if isinstance(raw_price, Decimal) else Decimal(str(raw_price))
    original_price = price
    tick: Optional[Decimal] = None

    rounding_mode = ROUND_DOWN if side.lower() == "buy" else ROUND_UP
    price = quantize_to_tick_fn(price, rounding_mode, contract_id)

    if tick_size and tick_size > 0:
        try:
            tick = tick_size if isinstance(tick_size, Decimal) else Decimal(str(tick_size))
        except (InvalidOperation, TypeError, ValueError):
            tick = None

    best_bid = best_ask = Decimal("0")
    try:
        best_bid, best_ask = await fetch_bbo_fn(contract_id)
    except Exception as exc:
        if logger:
            logger.debug(f"[BACKPACK] Failed to refresh BBO for price adjustment: {exc}")

    if tick and tick > 0:
        if side.lower() == "buy" and best_ask > 0:
            while price >= best_ask and price - tick > 0:
                price -= tick
        elif side.lower() == "sell" and best_bid > 0:
            while price <= best_bid:
                price += tick

    if price <= 0 and tick and tick > 0:
        price = tick

    price = quantize_to_tick_fn(price, ROUND_DOWN if side.lower() == "buy" else ROUND_UP, contract_id)

    if logger and price != original_price:
        logger.debug(
            f"[BACKPACK] Post-only price adjusted: raw={original_price} -> adjusted={price} "
            f"(best_bid={best_bid or '0'}, best_ask={best_ask or '0'}, tick={tick or 'n/a'})"
        )

    return price

