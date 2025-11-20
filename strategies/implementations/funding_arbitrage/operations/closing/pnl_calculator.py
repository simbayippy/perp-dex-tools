"""PnL calculation for position closing."""

from decimal import Decimal
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from exchange_clients.base_models import TradeData

from ..core.trade_aggregator import aggregate_trades_by_order
from ..core.decimal_utils import to_decimal

# Database imports (optional - handle gracefully if not available)
try:
    from database.connection import database
    from database.repositories.trade_fill_repository import TradeFillRepository
    from funding_rate_service.core.mappers import dex_mapper, symbol_mapper
    DATABASE_AVAILABLE = True
except ImportError:
    database = None
    TradeFillRepository = None  # type: ignore
    dex_mapper = None  # type: ignore
    symbol_mapper = None  # type: ignore
    DATABASE_AVAILABLE = False

if TYPE_CHECKING:
    from ...models import FundingArbPosition
    from ...strategy import FundingArbitrageStrategy


class PnLCalculator:
    """Handles PnL calculation from trade history."""
    
    def __init__(self, strategy: "FundingArbitrageStrategy"):
        self._strategy = strategy
    
    def calculate_closing_fees(
        self,
        close_result: Dict[str, Any],
        order_type: Optional[str] = None
    ) -> Decimal:
        """
        Calculate closing fees from filled orders.
        
        Args:
            close_result: Close execution result with filled_orders
            order_type: "market" (taker) or "limit" (maker), defaults to taker if None
            
        Returns:
            Total closing fees in USD
        """
        strategy = self._strategy
        total_closing_fees = Decimal("0")
        is_maker = order_type == "limit" if order_type else False
        
        filled_orders = close_result.get("filled_orders", [])
        for fill_info in filled_orders:
            dex = fill_info.get("dex")
            fill_price = fill_info.get("fill_price")
            filled_qty = fill_info.get("filled_quantity")
            
            if not dex or not fill_price or not filled_qty:
                continue
            
            fee_structure = strategy.fee_calculator.get_fee_structure(dex)
            fee_rate = fee_structure.maker_fee if is_maker else fee_structure.taker_fee
            
            fill_price_decimal = to_decimal(fill_price)
            filled_qty_decimal = to_decimal(filled_qty)
            fee_rate_decimal = to_decimal(fee_rate)
            
            order_value_usd = fill_price_decimal * filled_qty_decimal
            order_fee = order_value_usd * fee_rate_decimal
            total_closing_fees += order_fee
            
            strategy.logger.debug(
                f"[{dex}] Closing fee: ${order_fee:.4f} "
                f"(value=${order_value_usd:.2f}, rate={fee_rate_decimal*10000:.2f}bps, "
                f"type={'maker' if is_maker else 'taker'})"
            )
        
        return total_closing_fees
    
    async def calculate_pnl_from_trade_history(
        self,
        position: "FundingArbPosition",
        close_result: Optional[Dict[str, Any]],
        start_time: float,
        end_time: float,
        store_trades_fn: Any,
    ) -> Optional[Tuple[Decimal, str]]:
        """
        Calculate PnL from exchange trade history APIs.
        
        Fetches both entry and exit trades for accurate PnL calculation.
        Supports both automated closes (with close_result) and manual closes (without close_result).
        
        Args:
            position: The closed position
            close_result: Optional close execution result with filled_orders and order_ids.
                         None if position was closed manually (e.g., via exchange UI).
            start_time: Start timestamp (Unix seconds) - position opened_at
            end_time: End timestamp (Unix seconds) - current time after close
            store_trades_fn: Function to store trades in database
            
        Returns:
            Tuple of (pnl, method_name) if successful, None if trade history unavailable
        """
        strategy = self._strategy
        
        legs_metadata = position.metadata.get("legs", {})
        entry_order_ids_by_dex: Dict[str, Optional[str]] = {}
        for dex in [position.long_dex, position.short_dex]:
            if dex:
                leg_meta = legs_metadata.get(dex, {})
                entry_order_ids_by_dex[dex] = leg_meta.get("order_id")
        
        exit_order_ids_by_dex: Dict[str, Optional[str]] = {}
        closing_order_ids: set = set()
        if close_result:
            filled_orders = close_result.get("filled_orders", [])
            for fill_info in filled_orders:
                dex = fill_info.get("dex")
                order_id = fill_info.get("order_id")
                if dex and order_id:
                    exit_order_ids_by_dex[dex] = order_id
                    closing_order_ids.add(order_id)
        
        all_trades_by_dex: Dict[str, List[TradeData]] = {}
        entry_trades_by_dex: Dict[str, List[TradeData]] = {}
        exit_trades_by_dex: Dict[str, List[TradeData]] = {}
        
        # Handle timezone-aware and naive datetimes
        opened_at = position.opened_at
        if opened_at.tzinfo is None:
            opened_at_timestamp = opened_at.timestamp()
        else:
            opened_at_timestamp = opened_at.timestamp()
        # Widen time window to 30 minutes to account for API indexing delays
        entry_window_start = opened_at_timestamp - 1800  # 30 minutes before
        entry_window_end = opened_at_timestamp + 1800   # 30 minutes after
        exit_window_start = end_time - 300
        exit_window_end = end_time + 300
        
        order_ids_available = False
        
        for dex in [position.long_dex, position.short_dex]:
            if not dex:
                continue
            
            client = strategy.exchange_clients.get(dex)
            if not client:
                strategy.logger.debug(f"[{dex}] Exchange client not available for trade history")
                continue
            
            entry_order_id = entry_order_ids_by_dex.get(dex)
            exit_order_id = exit_order_ids_by_dex.get(dex)
            
            entry_trades: List[TradeData] = []
            if entry_order_id:
                try:
                    trades = await client.get_user_trade_history(
                        symbol=position.symbol,
                        start_time=entry_window_start,
                        end_time=entry_window_end,
                        order_id=entry_order_id,
                    )
                    if trades:
                        entry_trades.extend(trades)
                        order_ids_available = True
                        strategy.logger.debug(
                            f"[{dex}] Found {len(trades)} entry trades for order_id {entry_order_id}"
                        )
                except Exception as e:
                    strategy.logger.debug(
                        f"[{dex}] Failed to get entry trades with order_id {entry_order_id}: {e}"
                    )
            
            exit_trades: List[TradeData] = []
            if exit_order_id:
                try:
                    trades = await client.get_user_trade_history(
                        symbol=position.symbol,
                        start_time=exit_window_start,
                        end_time=exit_window_end,
                        order_id=exit_order_id,
                    )
                    if trades:
                        exit_trades.extend(trades)
                        order_ids_available = True
                        strategy.logger.debug(
                            f"[{dex}] Found {len(trades)} exit trades for order_id {exit_order_id}"
                        )
                except Exception as e:
                    strategy.logger.debug(
                        f"[{dex}] Failed to get exit trades with order_id {exit_order_id}: {e}"
                    )
            
            if not entry_trades or not exit_trades:
                try:
                    all_trades = await client.get_user_trade_history(
                        symbol=position.symbol,
                        start_time=start_time,
                        end_time=end_time,
                        order_id=None,
                    )
                    if all_trades:
                        for trade in all_trades:
                            if entry_order_id and trade.order_id == entry_order_id:
                                if trade not in entry_trades:
                                    entry_trades.append(trade)
                            elif exit_order_id and trade.order_id == exit_order_id:
                                if trade not in exit_trades:
                                    exit_trades.append(trade)
                            elif trade.order_id in closing_order_ids:
                                if trade not in exit_trades:
                                    exit_trades.append(trade)
                            else:
                                trade_ts = trade.timestamp
                                if entry_window_start <= trade_ts <= entry_window_end:
                                    if trade not in entry_trades:
                                        entry_trades.append(trade)
                                elif exit_window_start <= trade_ts <= exit_window_end:
                                    if trade not in exit_trades:
                                        exit_trades.append(trade)
                        
                        strategy.logger.debug(
                            f"[{dex}] Found {len(entry_trades)} entry and {len(exit_trades)} exit trades "
                            f"(timestamp-filtered from {len(all_trades)} total)"
                        )
                except Exception as e:
                    strategy.logger.debug(
                        f"[{dex}] Failed to get trade history: {e}"
                    )
            
            if entry_trades:
                entry_trades_by_dex[dex] = entry_trades
            if exit_trades:
                exit_trades_by_dex[dex] = exit_trades
            if entry_trades or exit_trades:
                all_trades_by_dex[dex] = entry_trades + exit_trades
        
        total_trades = sum(len(trades) for trades in all_trades_by_dex.values())
        if total_trades == 0:
            return None
        
        total_price_pnl = Decimal("0")
        entry_fees = Decimal("0")
        closing_fees = Decimal("0")
        total_realized_funding = Decimal("0")
        
        # Calculate entry prices from entry trades (weighted average) for each DEX
        # This is used as fallback when metadata entry_price is missing or zero
        entry_prices_from_trades: Dict[str, Decimal] = {}
        for dex, trades in entry_trades_by_dex.items():
            if trades:
                total_value = Decimal("0")
                total_qty = Decimal("0")
                for trade in trades:
                    entry_fees += trade.fee
                    if trade.realized_funding is not None:
                        total_realized_funding += trade.realized_funding
                    # Calculate weighted average entry price
                    total_value += trade.price * trade.quantity
                    total_qty += trade.quantity
                if total_qty > 0:
                    entry_prices_from_trades[dex] = total_value / total_qty
                    strategy.logger.debug(
                        f"[{dex}] Calculated entry price from trades: ${entry_prices_from_trades[dex]:.6f} "
                        f"(from {len(trades)} entry trades)"
                    )
            else:
                # No entry trades found, but still process fees if any
                for trade in trades:
                    entry_fees += trade.fee
                    if trade.realized_funding is not None:
                        total_realized_funding += trade.realized_funding
        
        for dex, trades in exit_trades_by_dex.items():
            leg_meta = legs_metadata.get(dex, {})
            entry_price = leg_meta.get("entry_price")
            side = leg_meta.get("side", "long")
            
            # If entry_price from metadata is missing or zero, use calculated entry price from trades
            if not entry_price or entry_price == Decimal("0"):
                entry_price = entry_prices_from_trades.get(dex)
                if entry_price:
                    strategy.logger.debug(
                        f"[{dex}] Using entry price from trades (${entry_price:.6f}) "
                        f"since metadata entry_price was missing or zero"
                    )
            
            for trade in trades:
                closing_fees += trade.fee
                if trade.realized_funding is not None:
                    total_realized_funding += trade.realized_funding
                
                if trade.realized_pnl is not None:
                    total_price_pnl += trade.realized_pnl
                    strategy.logger.debug(
                        f"[{dex}] Price PnL from realized_pnl: ${trade.realized_pnl:.2f} "
                        f"(exit=${trade.price:.6f}, qty={trade.quantity})"
                    )
                elif entry_price and entry_price > Decimal("0"):
                    entry_price_decimal = to_decimal(entry_price)
                    if side == "long":
                        leg_pnl = (trade.price - entry_price_decimal) * trade.quantity
                    else:
                        leg_pnl = (entry_price_decimal - trade.price) * trade.quantity
                    total_price_pnl += leg_pnl
                    strategy.logger.debug(
                        f"[{dex}] Price PnL from trade history: ${leg_pnl:.2f} "
                        f"(entry=${entry_price_decimal:.6f}, exit=${trade.price:.6f}, qty={trade.quantity})"
                    )
                else:
                    strategy.logger.warning(
                        f"[{dex}] Cannot calculate PnL for exit trade: "
                        f"entry_price missing/zero and no realized_pnl "
                        f"(exit=${trade.price:.6f}, qty={trade.quantity})"
                    )
        
        if total_realized_funding != 0:
            funding_to_add = total_realized_funding
            funding_source = "trade_history"
        else:
            cumulative_funding = await strategy.position_manager.get_cumulative_funding(position.id)
            funding_to_add = to_decimal(cumulative_funding)
            funding_source = "database"
        
        total_fees_decimal = entry_fees + closing_fees
        pnl = total_price_pnl + funding_to_add - total_fees_decimal
        
        method_name = f"trade_history_{'with_order_id' if order_ids_available else 'timestamp_filtered'}"
        if close_result is None:
            method_name += "_manual_close"
        
        # Store trades in database (non-blocking)
        await store_trades_fn(
            position=position,
            entry_trades_by_dex=entry_trades_by_dex,
            exit_trades_by_dex=exit_trades_by_dex,
        )
        
        strategy.logger.info(
            f"PnL calculation ({method_name}): "
            f"price_pnl=${total_price_pnl:.2f}, "
            f"funding=${funding_to_add:.2f} (from {funding_source}), "
            f"entry_fees=${entry_fees:.2f}, "
            f"closing_fees=${closing_fees:.2f}, "
            f"total_fees=${total_fees_decimal:.2f}, "
            f"net_pnl=${pnl:.2f}"
        )
        
        return (pnl, method_name)
    
    async def store_trades_in_database(
        self,
        position: "FundingArbPosition",
        entry_trades_by_dex: Dict[str, List[TradeData]],
        exit_trades_by_dex: Dict[str, List[TradeData]],
    ) -> None:
        """
        Store entry and exit trades in database.
        
        Non-blocking: logs warnings but doesn't fail if storage fails.
        """
        if not DATABASE_AVAILABLE or not database or not TradeFillRepository:
            return
        
        strategy = self._strategy
        
        try:
            account_id = strategy.position_manager.account_id
            if not account_id:
                strategy.logger.debug(
                    f"[{position.symbol}] Cannot store trades: account_id not available"
                )
                return
            
            if not dex_mapper.is_loaded() or not symbol_mapper.is_loaded():
                if not database.is_connected:
                    await database.connect()
                if not dex_mapper.is_loaded():
                    await dex_mapper.load_from_db(database)
                if not symbol_mapper.is_loaded():
                    await symbol_mapper.load_from_db(database)
            
            symbol_id = symbol_mapper.get_id(position.symbol)
            if symbol_id is None:
                strategy.logger.warning(
                    f"[{position.symbol}] Cannot store trades: symbol_id not found"
                )
                return
            
            repository = TradeFillRepository(database)
            
            for dex_name, exit_trades in exit_trades_by_dex.items():
                if not exit_trades:
                    continue
                
                dex_id = dex_mapper.get_id(dex_name)
                if dex_id is None:
                    strategy.logger.warning(
                        f"[{position.symbol}] Cannot store exit trades for {dex_name}: dex_id not found"
                    )
                    continue
                
                aggregated = aggregate_trades_by_order(exit_trades)
                
                for order_id, agg in aggregated.items():
                    try:
                        # Ensure timestamp is timezone-aware UTC
                        timestamp_seconds = agg['timestamp']
                        if isinstance(timestamp_seconds, datetime):
                            if timestamp_seconds.tzinfo is None:
                                timestamp_dt = timestamp_seconds.replace(tzinfo=timezone.utc)
                            else:
                                timestamp_dt = timestamp_seconds.astimezone(timezone.utc)
                        else:
                            timestamp_dt = datetime.fromtimestamp(timestamp_seconds, tz=timezone.utc)
                        fill_id = await repository.insert_trade_fill(
                            position_id=position.id,
                            account_id=account_id,
                            trade_type='exit',
                            dex_id=dex_id,
                            symbol_id=symbol_id,
                            order_id=order_id,
                            trade_id=agg['trade_id'],
                            timestamp=timestamp_dt,
                            side=agg['side'],
                            total_quantity=agg['total_quantity'],
                            weighted_avg_price=agg['weighted_avg_price'],
                            total_fee=agg['total_fee'],
                            fee_currency=agg['fee_currency'],
                            realized_pnl=agg['realized_pnl'],
                            realized_funding=agg['realized_funding'],
                            fill_count=agg['fill_count'],
                        )
                        if fill_id:
                            strategy.logger.debug(
                                f"[{position.symbol}] Stored exit trade for {dex_name} "
                                f"(order_id={order_id}, fills={agg['fill_count']})"
                            )
                    except Exception as e:
                        strategy.logger.warning(
                            f"[{position.symbol}] Failed to store exit trade for {dex_name} "
                            f"(order_id={order_id}): {e}"
                        )
            
            for dex_name, entry_trades in entry_trades_by_dex.items():
                if not entry_trades:
                    continue
                
                dex_id = dex_mapper.get_id(dex_name)
                if dex_id is None:
                    strategy.logger.warning(
                        f"[{position.symbol}] Cannot store entry trades for {dex_name}: dex_id not found"
                    )
                    continue
                
                aggregated = aggregate_trades_by_order(entry_trades)
                
                for order_id, agg in aggregated.items():
                    try:
                        existing = await repository.get_trades_by_order_id(order_id)
                        if existing and existing.get('position_id') == str(position.id):
                            strategy.logger.debug(
                                f"[{position.symbol}] Entry trade already stored for {dex_name} "
                                f"(order_id={order_id})"
                            )
                            continue
                        
                        # Ensure timestamp is timezone-aware UTC, then repository will convert to naive
                        timestamp_seconds = agg['timestamp']
                        if isinstance(timestamp_seconds, datetime):
                            if timestamp_seconds.tzinfo is None:
                                timestamp_dt = timestamp_seconds.replace(tzinfo=timezone.utc)
                            else:
                                timestamp_dt = timestamp_seconds.astimezone(timezone.utc)
                        else:
                            timestamp_dt = datetime.fromtimestamp(float(timestamp_seconds), tz=timezone.utc)
                        fill_id = await repository.insert_trade_fill(
                            position_id=position.id,
                            account_id=account_id,
                            trade_type='entry',
                            dex_id=dex_id,
                            symbol_id=symbol_id,
                            order_id=order_id,
                            trade_id=agg['trade_id'],
                            timestamp=timestamp_dt,
                            side=agg['side'],
                            total_quantity=agg['total_quantity'],
                            weighted_avg_price=agg['weighted_avg_price'],
                            total_fee=agg['total_fee'],
                            fee_currency=agg['fee_currency'],
                            realized_pnl=agg['realized_pnl'],
                            realized_funding=agg['realized_funding'],
                            fill_count=agg['fill_count'],
                        )
                        if fill_id:
                            strategy.logger.debug(
                                f"[{position.symbol}] Stored entry trade for {dex_name} "
                                f"(order_id={order_id}, fills={agg['fill_count']})"
                            )
                    except Exception as e:
                        strategy.logger.warning(
                            f"[{position.symbol}] Failed to store entry trade for {dex_name} "
                            f"(order_id={order_id}): {e}"
                        )
        except Exception as e:
            strategy.logger.warning(
                f"[{position.symbol}] Failed to store trades in database: {e}"
            )

