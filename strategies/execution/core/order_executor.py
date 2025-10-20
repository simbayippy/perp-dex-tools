"""
Order Executor - Smart order placement with tiered execution.

Provides intelligent order execution with multiple modes:
- limit_only: Place limit order, wait for fill
- limit_with_fallback: Try limit first, fallback to market if timeout
- market_only: Immediate market order
- adaptive: Choose mode based on liquidity analysis

Key features:
- Automatic fallback from limit to market
- Timeout handling
- Slippage tracking
- Execution quality metrics
"""

from typing import Dict, Optional
from decimal import Decimal
from enum import Enum
from dataclasses import dataclass
import time
import asyncio
from helpers.unified_logger import get_core_logger
from exchange_clients import BaseExchangeClient

logger = get_core_logger("order_executor")


class ExecutionMode(Enum):
    """
    Execution modes for order placement.
    
    """
    LIMIT_ONLY = "limit_only"
    LIMIT_WITH_FALLBACK = "limit_with_fallback"
    MARKET_ONLY = "market_only"
    ADAPTIVE = "adaptive"


@dataclass
class ExecutionResult:
    """
    Result of order execution.
    
    Contains all metrics needed for quality analysis.
    """
    success: bool
    filled: bool
    
    # Price & quantity
    fill_price: Optional[Decimal] = None
    filled_quantity: Optional[Decimal] = None
    
    # Quality metrics
    expected_price: Optional[Decimal] = None
    slippage_usd: Decimal = Decimal('0')
    slippage_pct: Decimal = Decimal('0')
    
    # Execution details
    execution_mode_used: str = ""
    execution_time_ms: int = 0
    
    # Error handling
    error_message: Optional[str] = None
    order_id: Optional[str] = None


class OrderExecutor:
    """
    Intelligent order executor with tiered execution strategy.
    
    ⭐ Inspired by Hummingbot's PositionExecutor ⭐
    
    Key Patterns:
    1. Limit orders for better pricing (maker orders)
    2. Market fallback if limit times out
    3. Configurable timeout per order
    4. Automatic price selection (mid-market with buffer)
    
    Example:
        executor = OrderExecutor()
        
        # Try limit, fallback to market after 30s
        result = await executor.execute_order(
            exchange_client=client,
            symbol="BTC-PERP",
            side="buy",
            size_usd=Decimal("1000"),
            mode=ExecutionMode.LIMIT_WITH_FALLBACK,
                    timeout_seconds=40.0
        )
        
        if result.filled:
            print(f"Filled at ${result.fill_price}, slippage: {result.slippage_pct}%")
    """
    
    DEFAULT_LIMIT_PRICE_OFFSET_PCT = Decimal("0.0001")  # 1 basis point

    def __init__(
        self,
        default_timeout: float = 40.0,
        price_provider = None,  # Optional PriceProvider for shared BBO lookups
        default_limit_price_offset_pct: Decimal = DEFAULT_LIMIT_PRICE_OFFSET_PCT
    ):
        """
        Initialize order executor.
        
        Args:
            default_timeout: Default timeout for limit orders (seconds)
            price_provider: Optional PriceProvider for retrieving shared BBO data
            default_limit_price_offset_pct: Default maker improvement for limit orders
        """
        self.default_timeout = default_timeout
        self.price_provider = price_provider
        self.default_limit_price_offset_pct = default_limit_price_offset_pct
        self.logger = get_core_logger("order_executor")
    
    async def execute_order(
        self,
        exchange_client: BaseExchangeClient,
        symbol: str,
        side: str,
        size_usd: Optional[Decimal] = None,
        quantity: Optional[Decimal] = None,
        mode: ExecutionMode = ExecutionMode.LIMIT_WITH_FALLBACK,
        timeout_seconds: Optional[float] = None,
        limit_price_offset_pct: Optional[Decimal] = None,
        cancel_event: Optional[asyncio.Event] = None
    ) -> ExecutionResult:
        """
        Execute order with intelligent mode selection.
        
        Args:
            exchange_client: Exchange client instance
            symbol: Trading pair (e.g., "BTC-PERP")
            side: "buy" or "sell"
            size_usd: Order size in USD
            mode: Execution mode
            timeout_seconds: Timeout for limit orders (uses default if None)
            limit_price_offset_pct: Price improvement for limit orders (None = executor default)
            cancel_event: Optional asyncio.Event to request cancellation (only respected for limit orders)
        
        Returns:
            ExecutionResult with all execution details
        """
        if size_usd is None and quantity is None:
            raise ValueError("OrderExecutor.execute_order requires size_usd or quantity")

        start_time = time.time()
        timeout = timeout_seconds or self.default_timeout
        
        # Get exchange name for better logging
        try:
            exchange_name = exchange_client.get_exchange_name()
        except Exception:
            exchange_name = "unknown"
        
        # Choose emoji based on side
        emoji = "🟢" if side == "buy" else "🔴"
        
        size_components = []
        if size_usd is not None:
            size_components.append(f"${size_usd}")
        if quantity is not None:
            size_components.append(f"qty={quantity}")
        size_descriptor = " ".join(size_components)

        self.logger.info(
            f"{emoji} [{exchange_name.upper()}] Executing {side} {symbol} ({size_descriptor}) in mode {mode.value}"
        )
        
        try:
            offset_pct = (
                limit_price_offset_pct
                if limit_price_offset_pct is not None
                else self.default_limit_price_offset_pct
            )
            if not isinstance(offset_pct, Decimal):
                offset_pct = Decimal(str(offset_pct))

            if mode == ExecutionMode.MARKET_ONLY:
                result = await self._execute_market(
                    exchange_client, symbol, side, size_usd, quantity
                )
            
            elif mode == ExecutionMode.LIMIT_ONLY:
                result = await self._execute_limit(
                    exchange_client,
                    symbol,
                    side,
                    size_usd,
                    quantity,
                    timeout,
                    offset_pct,
                    cancel_event,
                )
            
            elif mode == ExecutionMode.LIMIT_WITH_FALLBACK:
                # Try limit first
                result = await self._execute_limit(
                    exchange_client,
                    symbol,
                    side,
                    size_usd,
                    quantity,
                    timeout,
                    offset_pct,
                    cancel_event,
                )
                
                if not result.filled:
                    # Fallback to market
                    self.logger.info(
                        f"Limit order timeout for {symbol}, falling back to market"
                    )
                    result = await self._execute_market(
                        exchange_client, symbol, side, size_usd, quantity
                    )
                    result.execution_mode_used = "market_fallback"
            
            elif mode == ExecutionMode.ADAPTIVE:
                # Use liquidity analyzer to decide (will implement later)
                # For now, default to limit_with_fallback
                result = await self.execute_order(
                    exchange_client=exchange_client,
                    symbol=symbol,
                    side=side,
                    size_usd=size_usd,
                    quantity=quantity,
                    mode=ExecutionMode.LIMIT_WITH_FALLBACK,
                    timeout_seconds=timeout,
                    limit_price_offset_pct=offset_pct,
                    cancel_event=cancel_event,
                )
            
            else:
                raise ValueError(f"Unknown execution mode: {mode}")
            
            # Add execution time
            result.execution_time_ms = int((time.time() - start_time) * 1000)
            
            return result
        
        except Exception as e:
            self.logger.error(f"Order execution failed: {e}", exc_info=True)
            return ExecutionResult(
                success=False,
                filled=False,
                error_message=str(e),
                execution_time_ms=int((time.time() - start_time) * 1000)
            )
    
    async def _execute_limit(
        self,
        exchange_client: BaseExchangeClient,
        symbol: str,
        side: str,
        size_usd: Optional[Decimal],
        quantity: Optional[Decimal],
        timeout_seconds: float,
        price_offset_pct: Decimal,
        cancel_event: Optional[asyncio.Event] = None
    ) -> ExecutionResult:
        """
        Place limit order at favorable price, wait for fill.
        
        Price selection (maker order):
        - Buy: best_ask - offset (better than market for us)
        - Sell: best_bid + offset (better than market for us)
        
        """
        try:
            best_bid, best_ask = await self._fetch_bbo_prices(exchange_client, symbol)
            mid_price = (best_bid + best_ask) / 2
            
            # Calculate limit price (maker order with small improvement)
            if side == "buy":
                # Buy at ask - offset (better than market taker)
                limit_price = best_ask * (Decimal('1') - price_offset_pct)
            else:
                # Sell at bid + offset (better than market taker)
                limit_price = best_bid * (Decimal('1') + price_offset_pct)
            
            order_quantity: Decimal
            if quantity is not None:
                order_quantity = Decimal(str(quantity)).copy_abs()
            else:
                if size_usd is None:
                    raise ValueError("Limit execution requires size_usd or quantity")
                order_quantity = (Decimal(str(size_usd)) / limit_price).copy_abs()

            order_quantity = exchange_client.round_to_step(order_quantity)
            if order_quantity <= Decimal("0"):
                raise ValueError("Order quantity rounded to zero")
            
            # Get the exchange-specific contract ID (normalized symbol)
            contract_id = exchange_client.resolve_contract_id(symbol)
            
            exchange_name = exchange_client.get_exchange_name()
            self.logger.info(
                f"[{exchange_name.upper()}] Placing limit {side} {symbol} (contract_id={contract_id}): "
                f"{order_quantity} @ ${limit_price} (mid: ${mid_price}, offset: {price_offset_pct}%)"
            )
            
            # Place limit order using the normalized contract_id
            order_result = await exchange_client.place_limit_order(
                contract_id=contract_id,
                quantity=float(order_quantity),
                price=float(limit_price),
                side=side
            )
            
            if not order_result.success:
                return ExecutionResult(
                    success=False,
                    filled=False,
                    error_message=f"Limit order placement failed: {order_result.error_message}",
                    execution_mode_used="limit_failed"
                )
            
            order_id = order_result.order_id
            
            # Wait for fill (with timeout)
            start_wait = time.time()
            
            while time.time() - start_wait < timeout_seconds:
                if cancel_event and cancel_event.is_set():
                    self.logger.info(
                        f"[{exchange_name.upper()}] Cancellation requested for limit order {order_id}"
                    )
                    try:
                        await exchange_client.cancel_order(order_id)
                    except Exception as e:
                        self.logger.error(f"Failed to cancel order {order_id}: {e}")
                    return ExecutionResult(
                        success=False,
                        filled=False,
                        error_message="Limit order cancelled by executor",
                        execution_mode_used="limit_cancelled",
                        order_id=order_id
                    )
                # Check order status
                order_info = await exchange_client.get_order_info(order_id)
                
                if order_info and order_info.status == "FILLED":
                    fill_price = Decimal(str(order_info.price))
                    filled_qty = Decimal(str(order_info.filled_size))
                    
                    exchange_name = exchange_client.get_exchange_name()
                    self.logger.info(
                        f"[{exchange_name.upper()}] Limit order filled: {filled_qty} @ ${fill_price}"
                    )
                    
                    # Calculate slippage (should be near zero for maker orders)
                    slippage_usd = abs(fill_price - limit_price) * filled_qty
                    slippage_pct = abs(fill_price - limit_price) / limit_price if limit_price > 0 else Decimal('0')
                    
                    return ExecutionResult(
                        success=True,
                        filled=True,
                        fill_price=fill_price,
                        filled_quantity=filled_qty,
                        expected_price=limit_price,
                        slippage_usd=slippage_usd,
                        slippage_pct=slippage_pct,
                        execution_mode_used="limit",
                        order_id=order_id
                    )
                
                # Check more frequently near the end
                wait_interval = 0.5 if (timeout_seconds - (time.time() - start_wait)) > 5 else 0.2
                await asyncio.sleep(wait_interval)
            
            # Timeout - cancel order
            exchange_name = exchange_client.get_exchange_name()
            self.logger.warning(
                f"[{exchange_name.upper()}] Limit order timeout after {timeout_seconds}s, canceling {order_id}"
            )
            
            try:
                await exchange_client.cancel_order(order_id)
            except Exception as e:
                self.logger.error(f"Failed to cancel order {order_id}: {e}")
            
            return ExecutionResult(
                success=False,
                filled=False,
                error_message=f"Limit order timeout after {timeout_seconds}s",
                execution_mode_used="limit_timeout",
                order_id=order_id
            )
        
        except Exception as e:
            # Extract exchange name for better error messages
            try:
                exchange_name = exchange_client.get_exchange_name()
            except Exception:
                exchange_name = "unknown"
            
            self.logger.error(
                f"[{exchange_name.upper()}] Limit order execution failed for {symbol}: {e}",
                exc_info=True
            )
            return ExecutionResult(
                success=False,
                filled=False,
                error_message=f"[{exchange_name}] Limit execution error: {str(e)}",
                execution_mode_used="limit_error"
            )
    
    async def _execute_market(
        self,
        exchange_client: BaseExchangeClient,
        symbol: str,
        side: str,
        size_usd: Optional[Decimal],
        quantity: Optional[Decimal]
    ) -> ExecutionResult:
        """
        Execute market order immediately.
        """
        try:
            # Get current price for quantity calculation & slippage tracking
            best_bid, best_ask = await self._fetch_bbo_prices(exchange_client, symbol)
            mid_price = (best_bid + best_ask) / 2
            expected_price = best_ask if side == "buy" else best_bid
            
            if quantity is not None:
                order_quantity = Decimal(str(quantity)).copy_abs()
            else:
                if size_usd is None:
                    raise ValueError("Market execution requires size_usd or quantity")
                order_quantity = (Decimal(str(size_usd)) / expected_price).copy_abs()

            order_quantity = exchange_client.round_to_step(order_quantity)
            if order_quantity <= Decimal("0"):
                raise ValueError("Order quantity rounded to zero")
            
            # Get the exchange-specific contract ID (normalized symbol)
            contract_id = exchange_client.resolve_contract_id(symbol)
            
            exchange_name = exchange_client.get_exchange_name()
            self.logger.info(
                f"[{exchange_name.upper()}] Placing market {side} {symbol} (contract_id={contract_id}): "
                f"{order_quantity} @ ~${expected_price}"
            )
            
            # Place market order using the normalized contract_id
            result = await exchange_client.place_market_order(
                contract_id=contract_id,
                quantity=float(order_quantity),
                side=side
            )
            
            if not result.success:
                return ExecutionResult(
                    success=False,
                    filled=False,
                    error_message=f"Market order failed: {result.error_message}",
                    execution_mode_used="market_failed"
                )
            
            # Get actual fill price
            fill_price = Decimal(str(result.price)) if result.price else expected_price
            filled_qty = order_quantity  # Assume full fill for market orders
            
            # Calculate slippage
            slippage_usd = abs(fill_price - expected_price) * filled_qty
            slippage_pct = abs(fill_price - expected_price) / expected_price if expected_price > 0 else Decimal('0')
            
            exchange_name = exchange_client.get_exchange_name()
            self.logger.info(
                f"[{exchange_name.upper()}] Market order filled: {filled_qty} @ ${fill_price} "
                f"(slippage: ${slippage_usd:.2f} / {slippage_pct*100:.3f}%)"
            )
            
            return ExecutionResult(
                success=True,
                filled=True,
                fill_price=fill_price,
                filled_quantity=filled_qty,
                expected_price=expected_price,
                slippage_usd=slippage_usd,
                slippage_pct=slippage_pct,
                execution_mode_used="market",
                order_id=result.order_id if hasattr(result, 'order_id') else None
            )
        
        except Exception as e:
            # Extract exchange name for better error messages
            try:
                exchange_name = exchange_client.get_exchange_name()
            except Exception:
                exchange_name = "unknown"
            
            self.logger.error(
                f"[{exchange_name.upper()}] Market order execution failed for {symbol}: {e}",
                exc_info=True
            )
            return ExecutionResult(
                success=False,
                filled=False,
                error_message=f"[{exchange_name}] Market execution error: {str(e)}",
                execution_mode_used="market_error"
            )
    
    async def _fetch_bbo_prices(
        self,
        exchange_client: BaseExchangeClient,
        symbol: str
    ) -> tuple[Decimal, Decimal]:
        """
        Fetch best bid/offer prices using the configured price provider or exchange client.
        
        Returns:
            (best_bid, best_ask) as Decimals
        """
        try:
            if self.price_provider:
                bid, ask = await self.price_provider.get_bbo_prices(
                    exchange_client=exchange_client,
                    symbol=symbol
                )
                return bid, ask

            # fallback to exchange client's fetch_bbo_prices()
            bid, ask = await exchange_client.fetch_bbo_prices(symbol)
            bid_dec = Decimal(str(bid))
            ask_dec = Decimal(str(ask))
            return bid_dec, ask_dec
        
        except Exception as e:
            self.logger.error(f"Failed to fetch BBO prices: {e}")
            raise
