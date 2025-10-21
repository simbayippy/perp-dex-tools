"""
Backpack exchange client implementation.
"""

import os
import asyncio
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_UP
from typing import Any, Callable, Dict, List, Optional, Tuple

from bpx.public import Public
from bpx.account import Account
from bpx.constants.enums import OrderTypeEnum, TimeInForceEnum

from exchange_clients.base_client import BaseExchangeClient
from exchange_clients.base_models import (
    ExchangePositionSnapshot,
    MissingCredentialsError,
    OrderInfo,
    OrderResult,
    query_retry,
    validate_credentials,
)
from exchange_clients.backpack.common import (
    get_backpack_symbol_format,
    normalize_symbol as normalize_backpack_symbol,
)
from exchange_clients.backpack.websocket_manager import BackpackWebSocketManager
from helpers.unified_logger import get_exchange_logger


class BackpackClient(BaseExchangeClient):
    """Backpack exchange client implementation."""

    def __init__(self, config: Dict[str, Any]):
        """Initialize Backpack client."""
        super().__init__(config)

        self.logger = get_exchange_logger("backpack", getattr(self.config, "ticker", "UNKNOWN"))

        self.public_key = os.getenv("BACKPACK_PUBLIC_KEY")
        self.secret_key = os.getenv("BACKPACK_SECRET_KEY")

        self.ws_manager: Optional[BackpackWebSocketManager] = None
        self._order_update_handler: Optional[Callable[[Dict[str, Any]], None]] = None
        self._market_symbol_map: Dict[str, str] = {}
        self._latest_orders: Dict[str, OrderInfo] = {}

        try:
            self.public_client = Public()
            self.account_client = Account(public_key=self.public_key, secret_key=self.secret_key)
        except Exception as exc:
            message = str(exc).lower()
            if "base64" in message or "invalid" in message:
                raise MissingCredentialsError(f"Invalid Backpack credentials format: {exc}") from exc
            raise

    # --------------------------------------------------------------------- #
    # Configuration & connection management
    # --------------------------------------------------------------------- #

    def _validate_config(self) -> None:
        """Validate Backpack configuration."""
        validate_credentials("BACKPACK_PUBLIC_KEY", os.getenv("BACKPACK_PUBLIC_KEY"))
        validate_credentials("BACKPACK_SECRET_KEY", os.getenv("BACKPACK_SECRET_KEY"))

    async def connect(self) -> None:
        """Connect to Backpack WebSocket for order updates."""
        raw_symbol = getattr(self.config, "contract_id", None)
        ws_symbol: Optional[str] = None
        if raw_symbol and raw_symbol.upper() not in {"MULTI_SYMBOL", "MULTI"}:
            ws_symbol = self._ensure_exchange_symbol(raw_symbol)

        if not self.ws_manager:
            self.ws_manager = BackpackWebSocketManager(
                public_key=self.public_key,
                secret_key=self.secret_key,
                symbol=ws_symbol,
                order_update_callback=self._handle_websocket_order_update,
                liquidation_callback=self.handle_liquidation_notification,
                depth_fetcher=self._fetch_depth_snapshot,
                symbol_formatter=self._ensure_exchange_symbol,
            )
            self.ws_manager.set_logger(self.logger)
        else:
            self.ws_manager.update_symbol(ws_symbol)

        await self.ws_manager.connect()

        if ws_symbol:
            ready = await self.ws_manager.wait_until_ready(timeout=5.0)
            if not ready and self.logger:
                self.logger.warning("[BACKPACK] Timed out waiting for account stream readiness")
            await self.ws_manager.wait_for_order_book(timeout=5.0)
        else:
            if self.logger:
                self.logger.debug("[BACKPACK] No contract symbol provided; WebSocket subscriptions deferred")

    async def disconnect(self) -> None:
        """Disconnect from Backpack WebSocket and cleanup."""
        if self.ws_manager:
            await self.ws_manager.disconnect()

    def get_exchange_name(self) -> str:
        """Return exchange identifier."""
        return "backpack"

    def supports_liquidation_stream(self) -> bool:
        """Backpack exposes liquidation-origin events on the order update stream."""
        return True

    # --------------------------------------------------------------------- #
    # Utility helpers
    # --------------------------------------------------------------------- #

    @staticmethod
    def _to_decimal(value: Any, default: Optional[Decimal] = None) -> Optional[Decimal]:
        """Convert various numeric inputs to Decimal safely."""
        if value in (None, "", "null"):
            return default

        try:
            return Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            return default

    @staticmethod
    def _to_internal_symbol(stream_symbol: Optional[str]) -> str:
        if not stream_symbol:
            return ""
        return normalize_backpack_symbol(stream_symbol).upper()

    def _ensure_exchange_symbol(self, identifier: Optional[str]) -> Optional[str]:
        """Normalize symbol/contract inputs to Backpack's expected wire format."""
        if not identifier:
            return None

        normalized = identifier.upper()
        symbol: Optional[str] = None

        mapped = self._market_symbol_map.get(normalized)
        if mapped:
            symbol = mapped
        elif "_" in normalized:
            # Already in exchange format (e.g., BTC_USDC_PERP)
            symbol = normalized
        elif normalized not in {"MULTI_SYMBOL", "MULTI"}:
            symbol = get_backpack_symbol_format(normalized)

        if symbol and self.ws_manager and self.ws_manager.symbol != symbol:
            self.ws_manager.update_symbol(symbol)

        return symbol or identifier

    def _fetch_depth_snapshot(self, symbol: str) -> Dict[str, Any]:
        """Blocking depth snapshot fetch used by the WebSocket manager."""
        exchange_symbol = self._ensure_exchange_symbol(symbol)
        return self.public_client.get_depth(exchange_symbol)

    def normalize_symbol(self, symbol: str) -> str:
        """
        Convert normalized symbol (e.g., 'BTC') to Backpack format.
        """
        return self._ensure_exchange_symbol(symbol) or symbol

    def _quantize_quantity(self, quantity: Any) -> Decimal:
        if not isinstance(quantity, Decimal):
            quantity = Decimal(str(quantity))

        step_size = getattr(self.config, "step_size", None)
        if not step_size or step_size <= 0:
            return quantity
        try:
            if not isinstance(step_size, Decimal):
                step_size = Decimal(str(step_size))
            return quantity.quantize(step_size, rounding=ROUND_DOWN)
        except (InvalidOperation, ValueError):
            decimals = max(0, -Decimal(str(step_size)).normalize().as_tuple().exponent)
            return Decimal(f"{quantity:.{decimals}f}")

    def _format_decimal(self, value: Any, step: Optional[Decimal] = None) -> str:
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
            return f"{value:.{decimals}f}"
        return format(value, "f")

    def _quantize_to_tick(self, price: Decimal, rounding_mode) -> Decimal:
        tick_size = getattr(self.config, "tick_size", None)
        if tick_size and tick_size > 0:
            try:
                tick = tick_size if isinstance(tick_size, Decimal) else Decimal(str(tick_size))
            except (InvalidOperation, TypeError, ValueError):
                tick = None
            if tick and tick > 0:
                try:
                    return price.quantize(tick, rounding=rounding_mode)
                except (InvalidOperation, TypeError, ValueError):
                    decimals = max(0, -tick.normalize().as_tuple().exponent)
                    return Decimal(f"{price:.{decimals}f}")
        # Fallback: Backpack enforces 4 decimal places on price inputs.
        default_tick = Decimal("0.0001")
        try:
            return price.quantize(default_tick, rounding=rounding_mode)
        except (InvalidOperation, TypeError, ValueError):
            return price

    async def _compute_post_only_price(self, contract_id: str, raw_price: Decimal, side: str) -> Decimal:
        """
        Quantize price toward the maker side and avoid matching the top of book.
        """
        price = raw_price if isinstance(raw_price, Decimal) else Decimal(str(raw_price))
        original_price = price
        tick_size = getattr(self.config, "tick_size", None)
        tick: Optional[Decimal] = None

        rounding_mode = ROUND_DOWN if side.lower() == "buy" else ROUND_UP
        price = self._quantize_to_tick(price, rounding_mode)

        if tick_size and tick_size > 0:
            try:
                tick = tick_size if isinstance(tick_size, Decimal) else Decimal(str(tick_size))
            except (InvalidOperation, TypeError, ValueError):
                tick = None

        best_bid = best_ask = Decimal("0")
        try:
            best_bid, best_ask = await self.fetch_bbo_prices(contract_id)
        except Exception as exc:  # pragma: no cover - defensive logging
            if self.logger:
                self.logger.debug(f"[BACKPACK] Failed to refresh BBO for price adjustment: {exc}")

        if tick and tick > 0:
            if side.lower() == "buy" and best_ask > 0:
                while price >= best_ask and price - tick > 0:
                    price -= tick
            elif side.lower() == "sell" and best_bid > 0:
                while price <= best_bid:
                    price += tick

        if price <= 0 and tick and tick > 0:
            price = tick

        price = self._quantize_to_tick(price, ROUND_DOWN if side.lower() == "buy" else ROUND_UP)

        if self.logger and price != original_price:
            self.logger.debug(f"[BACKPACK] Post-only price adjusted: raw={original_price} -> adjusted={price} (best_bid={best_bid or '0'}, best_ask={best_ask or '0'}, tick={tick or 'n/a'})")

        return price

    # --------------------------------------------------------------------- #
    # WebSocket callbacks
    # --------------------------------------------------------------------- #

    async def _handle_websocket_order_update(self, order_data: Dict[str, Any]) -> None:
        """Normalize and forward order update events to the registered handler."""
        try:
            symbol = order_data.get("s") or order_data.get("symbol")
            if symbol and getattr(self.config, "contract_id", None):
                expected_symbol = getattr(self.config, "contract_id")
                if expected_symbol and symbol != expected_symbol:
                    return

            order_id = str(order_data.get("i") or order_data.get("orderId") or "")
            if not order_id:
                return

            side_raw = (order_data.get("S") or order_data.get("side") or "").upper()
            if side_raw == "BID":
                side = "buy"
            elif side_raw == "ASK":
                side = "sell"
            else:
                side = side_raw.lower() or None

            quantity = self._to_decimal(order_data.get("q"), Decimal("0"))
            filled = self._to_decimal(order_data.get("z"), Decimal("0"))
            price = self._to_decimal(order_data.get("p"), Decimal("0"))
            remaining = None
            if quantity is not None and filled is not None:
                remaining = quantity - filled

            event = (order_data.get("e") or order_data.get("event") or "").lower()
            status = None
            if event == "orderfill":
                if quantity is not None and quantity > 0 and filled >= quantity:
                    status = "FILLED"
                elif filled and filled > 0:
                    status = "PARTIALLY_FILLED"
                else:
                    status = "OPEN"
            elif event in {"orderaccepted", "new"}:
                status = "OPEN"
            elif event in {"ordercancelled", "ordercanceled", "orderexpired", "canceled"}:
                status = "CANCELED"
            else:
                status = order_data.get("X") or order_data.get("status")
            status = (status or "").upper()

            info = OrderInfo(
                order_id=order_id,
                side=side or "",
                size=quantity or Decimal("0"),
                price=price or Decimal("0"),
                status=status,
                filled_size=filled or Decimal("0"),
                remaining_size=remaining or Decimal("0"),
            )
            self._latest_orders[order_id] = info

            if status == "FILLED":
                self.logger.info(
                    f"[WEBSOCKET] [BACKPACK] {status} "
                    f"{filled or quantity} @ {price or 'n/a'}"
                )
            else:
                self.logger.info(
                    f"[WEBSOCKET] [BACKPACK] {status} "
                    f"{filled or quantity} @ {price or 'n/a'}"
                )

            if self._order_update_handler:
                payload = {
                    "order_id": order_id,
                    "side": side,
                    "order_type": order_data.get("o") or order_data.get("type") or "UNKNOWN",
                    "status": status,
                    "size": quantity,
                    "price": price,
                    "contract_id": symbol,
                    "filled_size": filled,
                    "raw_event": order_data,
                }
                self._order_update_handler(payload)
        except Exception as exc:
            self.logger.error(f"Error handling Backpack order update: {exc}")

    async def handle_liquidation_notification(self, payload: Dict[str, Any]) -> None:
        """
        Convert Backpack order updates triggered by liquidations into LiquidationEvent objects.
        """
        try:
            origin = (payload.get("O") or "").upper()
            if origin not in {
                "LIQUIDATION_AUTOCLOSE",
                "ADL_AUTOCLOSE",
                "BACKSTOP_LIQUIDITY_PROVIDER",
            }:
                return

            event_type = (payload.get("e") or "").lower()
            if event_type != "orderfill":
                return

            symbol_raw = payload.get("s")
            if not symbol_raw:
                return

            internal_symbol = self._to_internal_symbol(symbol_raw) or (self.config.ticker or symbol_raw)

            side_raw = (payload.get("S") or "").lower()
            side = "buy" if side_raw in {"bid", "buy"} else "sell" if side_raw in {"ask", "sell"} else side_raw or "buy"

            fill_qty = self._to_decimal(payload.get("l"), Decimal("0")) or Decimal("0")
            if fill_qty <= 0:
                fill_qty = self._to_decimal(payload.get("z"), Decimal("0")) or Decimal("0")
            if fill_qty <= 0:
                return
            fill_qty = fill_qty.copy_abs()

            price = self._to_decimal(payload.get("L"), Decimal("0")) or Decimal("0")

            timestamp_us = payload.get("E")
            timestamp = datetime.now(timezone.utc)
            if timestamp_us is not None:
                try:
                    timestamp = datetime.fromtimestamp(int(timestamp_us) / 1_000_000, tz=timezone.utc)
                except (ValueError, TypeError, OSError):
                    timestamp = datetime.now(timezone.utc)

            metadata: Dict[str, Any] = {
                "order_id": payload.get("i"),
                "trade_id": payload.get("t"),
                "origin": origin,
                "maker": payload.get("m"),
                "raw": payload,
            }

            event = LiquidationEvent(
                exchange=self.get_exchange_name(),
                symbol=internal_symbol,
                side=side,
                quantity=fill_qty,
                price=price,
                timestamp=timestamp,
                metadata=metadata,
            )
            await self.emit_liquidation_event(event)
        except Exception as exc:  # pragma: no cover - defensive logging
            self.logger.error(f"Error handling Backpack liquidation notification: {exc}")

    # --------------------------------------------------------------------- #
    # Market data
    # --------------------------------------------------------------------- #

    @query_retry(default_return=(Decimal("0"), Decimal("0")))
    async def fetch_bbo_prices(self, contract_id: str) -> Tuple[Decimal, Decimal]:
        """Fetch best bid/offer, preferring WebSocket data when available."""
        if self.ws_manager and self.ws_manager.best_bid is not None and self.ws_manager.best_ask is not None:
            self.logger.info(f"📡 [BACKPACK] Using real-time BBO from WebSocket")
            return self.ws_manager.best_bid, self.ws_manager.best_ask

        self.logger.info(f"📞 [REST][BACKPACK] Using REST depth snapshot")
        # Fall back to REST depth snapshot
        try:
            symbol = self._ensure_exchange_symbol(contract_id)
            order_book = self.public_client.get_depth(symbol)
        except Exception as exc:
            self.logger.error(f"[BACKPACK] Failed to fetch depth for {contract_id}: {exc}")
            raise

        bids = order_book.get("bids", []) if isinstance(order_book, dict) else []
        asks = order_book.get("asks", []) if isinstance(order_book, dict) else []

        try:
            best_bid = max((self._to_decimal(level[0], Decimal("0")) for level in bids), default=Decimal("0"))
        except Exception:
            best_bid = Decimal("0")

        try:
            best_ask = min((self._to_decimal(level[0], Decimal("0")) for level in asks if level), default=Decimal("0"))
        except Exception:
            best_ask = Decimal("0")

        return best_bid, best_ask

    def _get_order_book_from_websocket(self) -> Optional[Dict[str, List[Dict[str, Decimal]]]]:
        """Return the latest order book maintained by the WebSocket manager."""
        if not self.ws_manager:
            self.logger.warning(f"📞 [BACKPACK] No WebSocket manager available")
            return None
        book = self.ws_manager.get_order_book()
        self.logger.info(
            f"📡 [BACKPACK] Using real-time order book from WebSocket "
            f"({len(book['bids'])} bids, {len(book['asks'])} asks)"
        )
        return book

    async def get_order_book_depth(
        self,
        contract_id: str,
        levels: int = 10,
    ) -> Dict[str, List[Dict[str, Decimal]]]:
        """Fetch order book depth, preferring WebSocket data when available."""
        if self.ws_manager:
            ws_book = self.ws_manager.get_order_book(levels=levels)
            if ws_book:
                return ws_book

        # REST fallback
        try:
            symbol = self._ensure_exchange_symbol(contract_id)
            order_book = self.public_client.get_depth(symbol)
        except Exception as exc:
            self.logger.error(f"[BACKPACK] Failed to fetch order book depth: {exc}")
            return {"bids": [], "asks": []}

        if not isinstance(order_book, dict):
            return {"bids": [], "asks": []}

        bids_raw = order_book.get("bids", []) or []
        asks_raw = order_book.get("asks", []) or []

        bids_sorted = sorted(bids_raw, key=lambda x: self._to_decimal(x[0], Decimal("0")), reverse=True)[:levels]
        asks_sorted = sorted(asks_raw, key=lambda x: self._to_decimal(x[0], Decimal("0")))[:levels]

        bids = [
            {"price": self._to_decimal(price, Decimal("0")), "size": self._to_decimal(size, Decimal("0"))}
            for price, size in bids_sorted
        ]
        asks = [
            {"price": self._to_decimal(price, Decimal("0")), "size": self._to_decimal(size, Decimal("0"))}
            for price, size in asks_sorted
        ]

        return {"bids": bids, "asks": asks}

    # --------------------------------------------------------------------- #
    # Order placement & management
    # --------------------------------------------------------------------- #

    async def get_order_price(self, direction: str) -> Decimal:
        """Determine a maker-friendly order price."""
        best_bid, best_ask = await self.fetch_bbo_prices(self.config.contract_id)
        if best_bid <= 0 or best_ask <= 0:
            raise ValueError("Invalid bid/ask prices")

        if direction.lower() == "buy":
            price = best_ask - getattr(self.config, "tick_size", Decimal("0.01"))
        else:
            price = best_bid + getattr(self.config, "tick_size", Decimal("0.01"))

        return self.round_to_tick(price)

    async def place_limit_order(
        self,
        contract_id: str,
        quantity: Decimal,
        price: Decimal,
        side: str,
    ) -> OrderResult:
        """Place a post-only limit order on Backpack."""
        backpack_side = "Bid" if side.lower() == "buy" else "Ask"

        # round price as backpack always seems to instant fill
        rounded_price = await self._compute_post_only_price(contract_id, price, side)
        quantized_quantity = self._quantize_quantity(quantity)
        min_quantity = getattr(self.config, "min_quantity", None)
        if min_quantity and quantized_quantity < min_quantity:
            message = (
                f"Quantity {quantized_quantity} below minimum {min_quantity} for {contract_id}"
            )
            self.logger.error(f"[BACKPACK] {message}")
            return OrderResult(success=False, error_message=message)

        quantity_str = self._format_decimal(quantized_quantity, getattr(self.config, "step_size", None))
        payload_preview = {
            "symbol": contract_id,
            "side": backpack_side,
            "orderType": OrderTypeEnum.LIMIT,
            "quantity": quantity_str,
            "price": str(rounded_price),
            "post_only": True,
            "time_in_force": TimeInForceEnum.GTC,
        }
        self.logger.debug(f"[BACKPACK] Executing limit order payload: {payload_preview}")

        try:
            result = self.account_client.execute_order(
                symbol=contract_id,
                side=backpack_side,
                order_type=OrderTypeEnum.LIMIT,
                quantity=quantity_str,
                price=str(rounded_price),
                post_only=True,
                time_in_force=TimeInForceEnum.GTC,
            )
        except Exception as exc:
            self.logger.error(f"[BACKPACK] Failed to place limit order: {exc}")
            return OrderResult(success=False, error_message=str(exc))

        if isinstance(result, dict) and result.get("code"):
            self.logger.error(f"[BACKPACK] Limit order rejected: {result}")
            return OrderResult(success=False, error_message=result.get("message", "Order rejected"))

        if not result or "id" not in result:
            return OrderResult(success=False, error_message="Limit order response missing order id")

        order_id = str(result["id"])

        await asyncio.sleep(0.05)
        info = await self.get_order_info(order_id)

        if info:
            return OrderResult(
                success=info.status not in {"Rejected", "Cancelled"},
                order_id=info.order_id,
                side=info.side,
                size=info.size,
                price=info.price,
                status=info.status,
                filled_size=info.filled_size,
            )

        return OrderResult(
            success=True,
            order_id=order_id,
            side=side.lower(),
            size=quantized_quantity,
            price=rounded_price,
            status="OPEN",
        )

    async def place_market_order(
        self,
        contract_id: str,
        quantity: Decimal,
        side: str,
    ) -> OrderResult:
        """Place a market order for immediate execution."""
        backpack_side = "Bid" if side.lower() == "buy" else "Ask"
        quantized_quantity = self._quantize_quantity(quantity)
        min_quantity = getattr(self.config, "min_quantity", None)
        if min_quantity and quantized_quantity < min_quantity:
            message = (
                f"Quantity {quantized_quantity} below minimum {min_quantity} for {contract_id}"
            )
            self.logger.error(f"[BACKPACK] {message}")
            return OrderResult(success=False, error_message=message)
        quantity_str = self._format_decimal(quantized_quantity, getattr(self.config, "step_size", None))
        payload_preview = {
            "symbol": contract_id,
            "side": backpack_side,
            "orderType": OrderTypeEnum.MARKET,
            "quantity": quantity_str,
        }
        self.logger.debug(f"[BACKPACK] Executing market order payload: {payload_preview}")

        try:
            result = self.account_client.execute_order(
                symbol=contract_id,
                side=backpack_side,
                order_type=OrderTypeEnum.MARKET,
                quantity=quantity_str,
            )
        except Exception as exc:
            self.logger.error(f"[BACKPACK] Failed to place market order: {exc}")
            return OrderResult(success=False, error_message=str(exc))

        self.logger.debug(f"[BACKPACK] Market order response: {result}")
        if isinstance(result, dict) and result.get("code"):
            self.logger.error(f"[BACKPACK] Market order rejected: {result}")
            return OrderResult(success=False, error_message=result.get("message", "Order rejected"))

        if not result or "id" not in result:
            return OrderResult(success=False, error_message="Market order response missing order id")

        status = (result.get("status") or "").upper()
        executed_qty = self._to_decimal(result.get("executedQuantity"), Decimal("0"))
        executed_quote_qty = self._to_decimal(result.get("executedQuoteQuantity"), Decimal("0"))
        avg_price = Decimal("0")
        if executed_qty and executed_qty > 0:
            avg_price = (executed_quote_qty or Decimal("0")) / executed_qty

        success = status == "FILLED"

        return OrderResult(
            success=success,
            order_id=str(result.get("id")),
            side=side.lower(),
            size=executed_qty or quantized_quantity,
            price=avg_price,
            status=status,
            filled_size=executed_qty,
            error_message=None if success else f"Market order status: {status}",
        )

    async def cancel_order(self, order_id: str) -> OrderResult:
        """Cancel an existing order."""
        try:
            result = self.account_client.cancel_order(symbol=self.config.contract_id, order_id=order_id)
        except Exception as exc:
            return OrderResult(success=False, error_message=str(exc))

        if not result:
            return OrderResult(success=False, error_message="Cancel order returned empty response")

        filled_size = self._to_decimal(result.get("executedQuantity"), Decimal("0"))
        status = result.get("status") or "CANCELLED"

        return OrderResult(success=True, order_id=str(order_id), status=status, filled_size=filled_size)

    @query_retry()
    async def get_order_info(self, order_id: str) -> Optional[OrderInfo]:
        """Fetch detailed order information."""
        order_id_str = str(order_id)
        cached = self._latest_orders.get(order_id_str)
        if cached:
            status_upper = (cached.status or "").upper()
            if status_upper in {"FILLED", "CANCELED"}:
                return cached
        try:
            order = self.account_client.get_open_order(symbol=self.config.contract_id, order_id=order_id)
        except Exception as exc:
            self.logger.error(f"[BACKPACK] Failed to fetch order info: {exc}")
            return cached

        if not order:
            return None

        side_raw = (order.get("side") or "").lower()
        if side_raw == "bid":
            side = "buy"
        elif side_raw == "ask":
            side = "sell"
        else:
            side = side_raw or None

        size = self._to_decimal(order.get("quantity"), Decimal("0"))
        price = self._to_decimal(order.get("price"), Decimal("0"))
        filled = self._to_decimal(order.get("executedQuantity"), Decimal("0"))

        remaining = None
        if size is not None and filled is not None:
            remaining = size - filled

        info = OrderInfo(
            order_id=str(order.get("id", order_id)),
            side=side or "",
            size=size or Decimal("0"),
            price=price or Decimal("0"),
            status=order.get("status", ""),
            filled_size=filled or Decimal("0"),
            remaining_size=remaining or Decimal("0"),
        )
        self._latest_orders[order_id_str] = info
        return info

    @query_retry(default_return=[])
    async def get_active_orders(self, contract_id: str) -> List[OrderInfo]:
        """Return currently active orders."""
        try:
            response = self.account_client.get_open_orders(symbol=contract_id)
        except Exception as exc:
            self.logger.error(f"[BACKPACK] Failed to fetch open orders: {exc}")
            return []

        if not response:
            return []

        orders_raw = response if isinstance(response, list) else response.get("orders", [])
        orders: List[OrderInfo] = []
        for order in orders_raw:
            side_raw = (order.get("side") or "").lower()
            side = "buy" if side_raw == "bid" else "sell" if side_raw == "ask" else side_raw
            size = self._to_decimal(order.get("quantity"), Decimal("0"))
            price = self._to_decimal(order.get("price"), Decimal("0"))
            filled = self._to_decimal(order.get("executedQuantity"), Decimal("0"))
            remaining = None
            if size is not None and filled is not None:
                remaining = size - filled

            orders.append(
                OrderInfo(
                    order_id=str(order.get("id", "")),
                    side=side or "",
                    size=size or Decimal("0"),
                    price=price or Decimal("0"),
                    status=order.get("status", ""),
                    filled_size=filled or Decimal("0"),
                    remaining_size=remaining or Decimal("0"),
                )
            )
            order_id = str(order.get("id", ""))
            if order_id:
                self._latest_orders[order_id] = orders[-1]

        return orders

    @query_retry(default_return=Decimal("0"))
    async def get_account_positions(self) -> Decimal:
        """Return absolute position size for configured contract."""
        try:
            positions = self.account_client.get_open_positions()
        except Exception as exc:
            self.logger.error(f"[BACKPACK] Failed to fetch open positions: {exc}")
            return Decimal("0")

        contract_id = getattr(self.config, "contract_id", None)
        if not positions or not contract_id:
            return Decimal("0")

        for position in positions:
            if (position.get("symbol") or "").upper() == contract_id.upper():
                quantity = self._to_decimal(position.get("netQuantity"), Decimal("0"))
                return quantity.copy_abs() if isinstance(quantity, Decimal) else Decimal("0")

        return Decimal("0")

    async def get_account_balance(self) -> Optional[Decimal]:
        """
        Fetch available account balance.

        Returns the available USDC balance if present, otherwise None.
        """
        try:
            balances = await asyncio.to_thread(self.account_client.get_balances)
        except Exception as exc:
            self.logger.warning(f"[BACKPACK] Failed to fetch balances: {exc}")
            return None

        balance = self._extract_available_balance(balances)
        if balance is None:
            self.logger.warning("[BACKPACK] Unable to determine available USDC balance")
        return balance

    async def get_leverage_info(self, symbol: str) -> Dict[str, Any]:
        """
        Fetch leverage limits for symbol.

        Backpack publishes initial margin settings in the market metadata. We derive
        leverage as floor(1 / initial_margin) and surface the account-level cap as well.
        """
        exchange_symbol = self._ensure_exchange_symbol(symbol) or symbol
        result: Dict[str, Any] = {
            "max_leverage": None,
            "max_notional": None,
            "margin_requirement": None,
            "brackets": None,
            "account_leverage": None,
            "error": None,
        }

        def _normalize_fraction(raw: Any) -> Optional[Decimal]:
            fraction = self._to_decimal(raw, None)
            if fraction is None:
                return None
            if fraction > 1:
                if fraction >= 1000:
                    fraction = fraction / Decimal("10000")
                else:
                    fraction = fraction / Decimal("100")
            return fraction

        market_payload: Optional[Dict[str, Any]] = None
        try:
            market_payload = await asyncio.to_thread(
                self.public_client.http_client.get,
                self.public_client.get_market_url(exchange_symbol),
            )
        except Exception as exc:
            if self.logger:
                self.logger.debug(f"[BACKPACK] Direct market lookup failed for {exchange_symbol}: {exc}")

        if not isinstance(market_payload, dict):
            try:
                markets = await asyncio.to_thread(self.public_client.get_markets)
            except Exception as exc:
                message = f"Failed to fetch markets list: {exc}"
                if self.logger:
                    self.logger.warning(f"[BACKPACK] {message}")
                result["error"] = message
                return result

            if isinstance(markets, list):
                for market in markets:
                    if not isinstance(market, dict):
                        continue
                    if market.get("symbol") == exchange_symbol:
                        market_payload = market
                        break
                if market_payload is None:
                    base_symbol = symbol.upper()
                    market_payload = next(
                        (
                            market
                            for market in markets
                            if isinstance(market, dict)
                            and (market.get("baseSymbol") == base_symbol or market.get("baseAsset") == base_symbol)
                            and (market.get("marketType") or "").upper() in {"PERP", "PERPETUAL"}
                        ),
                        None,
                    )

        if not isinstance(market_payload, dict):
            result["error"] = f"No market data available for {exchange_symbol}"
            return result

        perp_info = market_payload.get("perpInfo") or market_payload.get("perp_info") or market_payload

        imf_candidate = (
            perp_info.get("imfFunction")
            or perp_info.get("imf_function")
            or perp_info.get("initialMarginFunction")
            or perp_info.get("initial_margin_function")
            if isinstance(perp_info, dict)
            else None
        )

        imf_base = self._to_decimal(imf_candidate.get("base"), None) if isinstance(imf_candidate, dict) else None

        initial_margin_fraction = _normalize_fraction(perp_info.get("initialMarginFraction")) if isinstance(perp_info, dict) else None
        if initial_margin_fraction is None and isinstance(perp_info, dict):
            initial_margin_fraction = _normalize_fraction(
                perp_info.get("initialMargin") or perp_info.get("initial_margin") or perp_info.get("imf") or imf_base
            )

        max_leverage = None
        if initial_margin_fraction and initial_margin_fraction > 0:
            max_leverage = Decimal("1") / initial_margin_fraction
        elif imf_base and imf_base > 0:
            max_leverage = Decimal("1") / imf_base

        if max_leverage is not None:
            max_leverage = max_leverage.to_integral_value(rounding=ROUND_DOWN)

        max_notional = None
        if isinstance(perp_info, dict):
            max_notional = self._to_decimal(
                perp_info.get("openInterestLimit")
                or perp_info.get("riskLimitNotional")
                or perp_info.get("open_interest_limit"),
                None,
            )

        maintenance_margin_fraction = _normalize_fraction(
            perp_info.get("maintenanceMarginFraction")
            or perp_info.get("maintenanceMargin")
            or perp_info.get("maintenance_margin")
            or perp_info.get("mmf")
        ) if isinstance(perp_info, dict) else None

        brackets = [
            {
                "notional_cap": max_notional,
                "initial_margin": initial_margin_fraction or imf_base,
                "maintenance_margin": maintenance_margin_fraction,
                "max_leverage": max_leverage,
            }
        ] if max_leverage is not None else []

        if result["margin_requirement"] is None:
            result["margin_requirement"] = initial_margin_fraction or imf_base

        result["max_leverage"] = max_leverage
        result["max_notional"] = max_notional
        result["brackets"] = brackets or None
        result["maintenance_margin"] = maintenance_margin_fraction

        # Account-level leverage cap (optional)
        try:
            account_info = await asyncio.to_thread(self.account_client.get_account)
        except Exception as exc:
            if self.logger:
                self.logger.debug(f"[BACKPACK] Unable to fetch account leverage cap: {exc}")
            account_info = None

        if isinstance(account_info, dict):
            leverage_limit = account_info.get("leverageLimit") or account_info.get("leverage_limit")
            account_leverage = self._to_decimal(leverage_limit, None)
            if account_leverage and account_leverage > 0:
                result["account_leverage"] = account_leverage

        if result["max_leverage"] is None:
            result["error"] = f"Unable to determine leverage limits for {exchange_symbol}"

        # Log leverage info summary
        if self.logger and result["max_leverage"] is not None:
            margin_req = result["margin_requirement"]
            margin_pct = (margin_req * 100) if margin_req else None
            
            self.logger.info(
                f"📊 [BACKPACK] Leverage info for {symbol}:\n"
                f"  - Symbol max leverage: {result['max_leverage']:.1f}x\n"
                f"  - Account leverage: {result.get('account_leverage', 'N/A')}x\n"
                f"  - Max notional: {result['max_notional'] or 'None'}\n"
                f"  - Margin requirement: {margin_req} ({margin_pct:.1f}%)" if margin_pct else f"  - Margin requirement: {margin_req}"
            )

        return result

    async def get_contract_attributes(self) -> Tuple[str, Decimal]:
        """Populate contract_id and tick_size for current ticker."""
        ticker = getattr(self.config, "ticker", "")
        if not ticker:
            raise ValueError("Ticker is empty")

        min_quantity = Decimal("0")
        tick_size = Decimal("0")

        try:
            markets = self.public_client.get_markets()
        except Exception as exc:
            self.logger.error(f"[BACKPACK] Failed to fetch markets: {exc}")
            raise

        target_symbol = ""

        for market in markets or []:
            if (
                market.get("marketType") == "PERP"
                and market.get("baseSymbol") == ticker
                and market.get("quoteSymbol") == "USDC"
            ):
                target_symbol = market.get("symbol", "")
                quantity_filter = (market.get("filters", {}) or {}).get("quantity", {}) or {}
                price_filter = (market.get("filters", {}) or {}).get("price", {}) or {}
                min_quantity = self._to_decimal(quantity_filter.get("minQuantity"), Decimal("0"))
                step_size = self._to_decimal(quantity_filter.get("stepSize"), Decimal("0.0001"))
                tick_size = self._to_decimal(price_filter.get("tickSize"), Decimal("0.0001"))
                self._market_symbol_map[market.get("baseSymbol", "").upper()] = target_symbol
                setattr(self.config, "min_quantity", min_quantity or Decimal("0"))
                setattr(self.config, "step_size", step_size or Decimal("0.0001"))
                break

        if not target_symbol:
            raise ValueError(f"Failed to find Backpack contract for ticker {ticker}")

        self.config.contract_id = target_symbol
        self.config.tick_size = tick_size or Decimal("0.0001")
        if not getattr(self.config, "step_size", None):
            setattr(self.config, "step_size", Decimal("0.0001"))
        if not getattr(self.config, "min_quantity", None):
            setattr(self.config, "min_quantity", Decimal("0"))

        if getattr(self.config, "quantity", Decimal("0")) < (min_quantity or Decimal("0")):
            raise ValueError(
                f"Order quantity {self.config.quantity} below Backpack minimum {min_quantity}"
            )

        return self.config.contract_id, self.config.tick_size

    # --------------------------------------------------------------------- #
    # Balance helpers
    # --------------------------------------------------------------------- #

    def _extract_available_balance(self, payload: Any) -> Optional[Decimal]:
        """
        Attempt to extract the available USDC balance from Backpack's capital response.
        """
        if payload is None:
            return None

        entries: List[Dict[str, Any]] = []

        if isinstance(payload, dict):
            for key in ("balances", "capital", "data", "items"):
                value = payload.get(key)
                if isinstance(value, list):
                    entries = value
                    break
            else:
                if all(isinstance(v, dict) for v in payload.values()):
                    entries = [dict(symbol=k, **v) for k, v in payload.items()]
        elif isinstance(payload, list):
            entries = payload

        if not entries:
            return None

        total_available = Decimal("0")
        for entry in entries:
            if not isinstance(entry, dict):
                continue

            asset = (
                entry.get("symbol")
                or entry.get("asset")
                or entry.get("currency")
                or entry.get("token")
            )

            asset_code = str(asset).upper()
            if asset_code not in {"USDC", "USD", "USDT"}:
                continue

            available_value: Optional[Decimal] = None
            for key in ("available", "availableBalance", "free", "freeBalance", "balanceAvailable"):
                if key not in entry or entry[key] is None:
                    continue
                try:
                    available_value = Decimal(str(entry[key]))
                except (InvalidOperation, TypeError):
                    available_value = None
                else:
                    break

            if available_value is None:
                fallback = entry.get("total") or entry.get("quantity")
                if fallback is not None:
                    try:
                        available_value = Decimal(str(fallback))
                    except (InvalidOperation, TypeError):
                        available_value = None

            if available_value is not None:
                total_available += available_value

        return total_available if total_available > 0 else None

    # --------------------------------------------------------------------- #
    # Position inspection
    # --------------------------------------------------------------------- #

    async def get_position_snapshot(self, symbol: str) -> Optional[ExchangePositionSnapshot]:
        """
        Return a normalized position snapshot for a given symbol.
        """
        normalized_symbol = symbol.upper()
        target_symbol = self.normalize_symbol(normalized_symbol)

        try:
            positions = self.account_client.get_open_positions()
        except Exception as exc:
            self.logger.warning(f"[BACKPACK] Failed to fetch positions for snapshot: {exc}")
            return None

        if not positions:
            return None

        for position in positions:
            raw_symbol = (position.get("symbol") or "").upper()
            if raw_symbol != target_symbol:
                # As a fallback, normalize Backpack symbol (handles legacy formats)
                if normalize_backpack_symbol(raw_symbol) != normalized_symbol:
                    continue

            quantity = self._to_decimal(
                position.get("netQuantity")
                or position.get("quantity")
                or position.get("position")
                or position.get("contracts"),
                Decimal("0"),
            )

            entry_price = self._to_decimal(
                position.get("averageEntryPrice")
                or position.get("avgEntryPrice")
                or position.get("entryPrice"),
            )

            mark_price = self._to_decimal(
                position.get("markPrice")
                or position.get("marketPrice")
                or position.get("indexPrice")
                or position.get("oraclePrice"),
            )

            notional = self._to_decimal(
                position.get("notional")
                or position.get("positionValue")
                or position.get("grossPositionValue"),
            )

            exposure = notional.copy_abs() if isinstance(notional, Decimal) else None
            if exposure is None and mark_price is not None and quantity:
                exposure = mark_price * quantity.copy_abs()

            unrealized = self._to_decimal(
                position.get("unrealizedPnl")
                or position.get("unrealizedPnlUsd")
                or position.get("unrealizedPnL")
                or position.get("pnl"),
            )

            realized = self._to_decimal(position.get("realizedPnl") or position.get("realizedPnlUsd"))
            funding_accrued = self._to_decimal(
                position.get("fundingFees") or position.get("fundingAccrued")
            )
            margin_reserved = self._to_decimal(
                position.get("initialMargin")
                or position.get("marginUsed")
                or position.get("allocatedMargin")
            )
            leverage = self._to_decimal(position.get("leverage"))
            liquidation_price = self._to_decimal(position.get("liquidationPrice"))

            side = None
            if isinstance(quantity, Decimal):
                if quantity > 0:
                    side = "long"
                elif quantity < 0:
                    side = "short"

            metadata: Dict[str, Any] = {
                "backpack_symbol": raw_symbol,
                "position_id": position.get("id") or position.get("positionId"),
                "updated_at": position.get("updatedAt"),
            }
            if notional is not None:
                metadata["notional"] = notional

            return ExchangePositionSnapshot(
                symbol=normalized_symbol,
                quantity=quantity or Decimal("0"),
                side=side,
                entry_price=entry_price,
                mark_price=mark_price,
                exposure_usd=exposure,
                unrealized_pnl=unrealized,
                realized_pnl=realized,
                funding_accrued=funding_accrued,
                margin_reserved=margin_reserved,
                leverage=leverage,
                liquidation_price=liquidation_price,
                timestamp=datetime.now(timezone.utc),
                metadata={k: v for k, v in metadata.items() if v is not None},
            )

        return None
