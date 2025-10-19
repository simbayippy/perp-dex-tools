"""
Backpack exchange client implementation.
"""

import os
import asyncio
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Dict, List, Optional, Tuple

from bpx.public import Public
from bpx.account import Account
from bpx.constants.enums import OrderTypeEnum, TimeInForceEnum

from exchange_clients.base import (
    BaseExchangeClient,
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
        self._ws_task: Optional[asyncio.Task] = None
        self._order_update_handler: Optional[Callable[[Dict[str, Any]], None]] = None

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
        symbol = getattr(self.config, "contract_id", None)

        if not self.ws_manager:
            self.ws_manager = BackpackWebSocketManager(
                public_key=self.public_key,
                secret_key=self.secret_key,
                symbol=symbol,
                order_update_callback=self._handle_websocket_order_update,
            )
            self.ws_manager.set_logger(self.logger)
        else:
            self.ws_manager.update_symbol(symbol)

        if self._ws_task and not self._ws_task.done():
            return

        self._ws_task = asyncio.create_task(self.ws_manager.connect())

        # Give the WS manager a brief moment to connect (best-effort).
        await self.ws_manager.wait_until_ready(timeout=2.0)

    async def disconnect(self) -> None:
        """Disconnect from Backpack WebSocket and cleanup."""
        if self.ws_manager:
            await self.ws_manager.disconnect()

        if self._ws_task:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
            self._ws_task = None

    def get_exchange_name(self) -> str:
        """Return exchange identifier."""
        return "backpack"

    def setup_order_update_handler(self, handler: Callable[[Dict[str, Any]], None]) -> None:
        """Register strategy-level order update handler."""
        self._order_update_handler = handler

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

    def normalize_symbol(self, symbol: str) -> str:
        """
        Convert normalized symbol (e.g., 'BTC') to Backpack format.
        """
        return get_backpack_symbol_format(symbol)

    # --------------------------------------------------------------------- #
    # WebSocket callbacks
    # --------------------------------------------------------------------- #

    async def _handle_websocket_order_update(self, order_data: Dict[str, Any]) -> None:
        """Normalize and forward order update events to the registered handler."""
        if not self._order_update_handler:
            return

        try:
            symbol = order_data.get("s") or order_data.get("symbol")
            if symbol and getattr(self.config, "contract_id", None):
                # Skip updates for other symbols when a specific contract is configured
                expected_symbol = getattr(self.config, "contract_id")
                if expected_symbol and symbol != expected_symbol:
                    return

            side_raw = (order_data.get("S") or order_data.get("side") or "").upper()
            if side_raw == "BID":
                side = "buy"
            elif side_raw == "ASK":
                side = "sell"
            else:
                side = side_raw.lower() or None

            event = (order_data.get("e") or order_data.get("event") or "").lower()
            status = None
            if event == "orderfill" and (
                self._to_decimal(order_data.get("q")) == self._to_decimal(order_data.get("z"))
            ):
                status = "FILLED"
            elif event == "orderfill":
                status = "PARTIALLY_FILLED"
            elif event in {"orderaccepted", "new"}:
                status = "OPEN"
            elif event in {"ordercancelled", "orderexpired", "canceled"}:
                status = "CANCELED"

            payload = {
                "order_id": str(order_data.get("i") or order_data.get("orderId") or ""),
                "side": side,
                "order_type": order_data.get("o") or order_data.get("type") or "UNKNOWN",
                "status": status or order_data.get("X") or order_data.get("status"),
                "size": self._to_decimal(order_data.get("q")),
                "price": self._to_decimal(order_data.get("p")),
                "contract_id": symbol,
                "filled_size": self._to_decimal(order_data.get("z")),
                "raw_event": order_data,
            }

            self._order_update_handler(payload)
        except Exception as exc:
            self.logger.error(f"Error handling Backpack order update: {exc}")

    # --------------------------------------------------------------------- #
    # Market data
    # --------------------------------------------------------------------- #

    @query_retry(default_return=(Decimal("0"), Decimal("0")))
    async def fetch_bbo_prices(self, contract_id: str) -> Tuple[Decimal, Decimal]:
        """Fetch best bid/offer from Backpack public depth."""
        try:
            order_book = self.public_client.get_depth(contract_id)
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

    def get_order_book_from_websocket(self) -> Optional[Dict[str, List[Dict[str, Decimal]]]]:
        """Backpack WebSocket currently does not maintain a local order book."""
        return None

    async def get_order_book_depth(
        self,
        contract_id: str,
        levels: int = 10,
    ) -> Dict[str, List[Dict[str, Decimal]]]:
        """Fetch order book depth via REST fallback."""
        try:
            order_book = self.public_client.get_depth(contract_id)
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
        rounded_price = self.round_to_tick(price)

        try:
            result = self.account_client.execute_order(
                symbol=contract_id,
                side=backpack_side,
                order_type=OrderTypeEnum.LIMIT,
                quantity=str(quantity),
                price=str(rounded_price),
                post_only=True,
                time_in_force=TimeInForceEnum.GTC,
            )
        except Exception as exc:
            self.logger.error(f"[BACKPACK] Failed to place limit order: {exc}")
            return OrderResult(success=False, error_message=str(exc))

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
            size=quantity,
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

        try:
            result = self.account_client.execute_order(
                symbol=contract_id,
                side=backpack_side,
                order_type=OrderTypeEnum.MARKET,
                quantity=str(quantity),
            )
        except Exception as exc:
            self.logger.error(f"[BACKPACK] Failed to place market order: {exc}")
            return OrderResult(success=False, error_message=str(exc))

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
            size=executed_qty or quantity,
            price=avg_price,
            status=status,
            filled_size=executed_qty,
            error_message=None if success else f"Market order status: {status}",
        )

    async def place_close_order(
        self,
        contract_id: str,
        quantity: Decimal,
        price: Decimal,
        side: str,
        max_retries: int = 15,
    ) -> OrderResult:
        """Retry-friendly close order helper using post-only limit orders."""
        retries = 0
        side_lower = side.lower()

        while retries < max_retries:
            retries += 1
            best_bid, best_ask = await self.fetch_bbo_prices(contract_id)
            if best_bid <= 0 or best_ask <= 0:
                return OrderResult(success=False, error_message="No bid/ask data available")

            adjusted_price = price
            tick = getattr(self.config, "tick_size", Decimal("0.0001"))

            if side_lower == "sell":
                if price <= best_bid:
                    adjusted_price = best_bid + tick
                backpack_side = "Ask"
            else:
                if price >= best_ask:
                    adjusted_price = best_ask - tick
                backpack_side = "Bid"

            adjusted_price = self.round_to_tick(adjusted_price)

            try:
                result = self.account_client.execute_order(
                    symbol=contract_id,
                    side=backpack_side,
                    order_type=OrderTypeEnum.LIMIT,
                    quantity=str(quantity),
                    price=str(adjusted_price),
                    post_only=True,
                    time_in_force=TimeInForceEnum.GTC,
                )
            except Exception as exc:
                self.logger.error(f"[BACKPACK] Close order attempt failed: {exc}")
                await asyncio.sleep(0.25)
                continue

            if not result or "id" not in result:
                await asyncio.sleep(0.25)
                continue

            return OrderResult(
                success=True,
                order_id=str(result["id"]),
                side=side_lower,
                size=quantity,
                price=adjusted_price,
                status="NEW",
            )

        return OrderResult(success=False, error_message="Max retries exceeded for close order")

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
        try:
            order = self.account_client.get_open_order(symbol=self.config.contract_id, order_id=order_id)
        except Exception as exc:
            self.logger.error(f"[BACKPACK] Failed to fetch order info: {exc}")
            return None

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

        return OrderInfo(
            order_id=str(order.get("id", order_id)),
            side=side or "",
            size=size or Decimal("0"),
            price=price or Decimal("0"),
            status=order.get("status", ""),
            filled_size=filled or Decimal("0"),
            remaining_size=remaining or Decimal("0"),
        )

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

        Backpack SDK support is pending; return None for now.
        """
        self.logger.debug("[BACKPACK] get_account_balance not implemented")
        return None

    async def get_leverage_info(self, symbol: str) -> Dict[str, Any]:
        """
        Fetch leverage limits for symbol.

        Backpack has not published leverage APIs yet; return conservative defaults.
        """
        self.logger.debug(f"[BACKPACK] Using placeholder leverage info for {symbol}")
        return {
            "max_leverage": Decimal("10"),
            "max_notional": None,
            "margin_requirement": Decimal("0.10"),
            "brackets": None,
            "error": None,
        }

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
                tick_size = self._to_decimal(price_filter.get("tickSize"), Decimal("0.0001"))
                break

        if not target_symbol:
            raise ValueError(f"Failed to find Backpack contract for ticker {ticker}")

        self.config.contract_id = target_symbol
        self.config.tick_size = tick_size or Decimal("0.0001")

        if getattr(self.config, "quantity", Decimal("0")) < (min_quantity or Decimal("0")):
            raise ValueError(
                f"Order quantity {self.config.quantity} below Backpack minimum {min_quantity}"
            )

        return self.config.contract_id, self.config.tick_size

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
