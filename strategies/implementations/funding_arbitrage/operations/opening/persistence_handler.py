"""Persistence handling for position opening."""

from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from exchange_clients.base_models import TradeData

from ..core.trade_aggregator import aggregate_trades_by_order
from ..models.execution_models import PersistenceOutcome

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


class PersistenceHandler:
    """Handles position persistence and trade storage."""
    
    def __init__(self, strategy: "FundingArbitrageStrategy"):
        self._strategy = strategy
    
    async def persist_position(
        self,
        *,
        position: "FundingArbPosition",
        timestamp_iso: str,
        total_cost: Decimal,
        entry_fees: Decimal,
        total_slippage: Decimal,
        position_builder: Any,
    ) -> Optional[PersistenceOutcome]:
        """
        Persist the opened position. Returns outcome describing whether
        we merged or created the record.
        
        Args:
            position: Position to persist
            timestamp_iso: ISO timestamp string
            total_cost: Total cost
            entry_fees: Entry fees
            total_slippage: Total slippage
            position_builder: Position builder instance
            
        Returns:
            PersistenceOutcome if successful, None otherwise
        """
        strategy = self._strategy
        position_manager = strategy.position_manager
        existing_position = await position_manager.find_open_position(
            position.symbol,
            position.long_dex,
            position.short_dex,
        )

        if existing_position:
            merge_result = position_builder.merge_existing_position(
                existing_position=existing_position,
                new_position=position,
                total_cost=total_cost,
                entry_fees=entry_fees,
                total_slippage=total_slippage,
                timestamp_iso=timestamp_iso,
            )

            if merge_result is None:
                strategy.logger.warning(
                    f"⚠️ Skipping position update for {position.symbol}: resulting size would be non-positive"
                )
                return None

            merged_position, updated_size, additional_size = merge_result
            await position_manager.update(merged_position)
            strategy.position_opened_this_session = True

            return PersistenceOutcome(
                type="merged",
                position=merged_position,
                updated_size=updated_size,
                additional_size=additional_size,
            )

        await position_manager.create(position)
        strategy.position_opened_this_session = True

        return PersistenceOutcome(type="created", position=position)
    
    async def store_entry_trades(
        self,
        position: "FundingArbPosition",
        long_fill: Dict[str, Any],
        short_fill: Dict[str, Any],
    ) -> None:
        """
        Store entry trades in database.
        
        Non-blocking: logs warnings but doesn't fail if storage fails.
        
        Args:
            position: The position that was just opened
            long_fill: Long leg fill data
            short_fill: Short leg fill data
        """
        if not DATABASE_AVAILABLE or not database or not TradeFillRepository:
            return
        
        strategy = self._strategy
        
        try:
            account_id = strategy.position_manager.account_id
            if not account_id:
                strategy.logger.debug(
                    f"[{position.symbol}] Cannot store entry trades: account_id not available"
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
                    f"[{position.symbol}] Cannot store entry trades: symbol_id not found"
                )
                return
            
            repository = TradeFillRepository(database)
            
            long_order_id = long_fill.get("order_id")
            short_order_id = short_fill.get("order_id")
            
            entry_trades_by_dex: Dict[str, List[TradeData]] = {}
            # Handle timezone-aware and naive datetimes
            opened_at = position.opened_at
            if opened_at.tzinfo is None:
                # Naive datetime - assume UTC
                opened_at_timestamp = opened_at.timestamp()
            else:
                # Timezone-aware datetime - convert to UTC timestamp
                opened_at_timestamp = opened_at.timestamp()
            # Widen time window to 30 minutes (instead of 10) to account for API indexing delays
            start_time = opened_at_timestamp - 1800  # 30 minutes before
            end_time = opened_at_timestamp + 1800   # 30 minutes after
            
            for dex_name in [position.long_dex, position.short_dex]:
                client = strategy.exchange_clients.get(dex_name)
                if not client:
                    continue
                
                order_id = long_order_id if dex_name == position.long_dex else short_order_id
                if not order_id:
                    continue
                
                try:
                    trades = await client.get_user_trade_history(
                        symbol=position.symbol,
                        start_time=start_time,
                        end_time=end_time,
                        order_id=order_id,
                    )
                    if trades:
                        entry_trades_by_dex[dex_name] = trades
                except Exception as e:
                    strategy.logger.debug(
                        f"[{position.symbol}] Failed to fetch entry trades for {dex_name}: {e}"
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
                        # Ensure timestamp is timezone-aware UTC
                        timestamp_seconds = agg['timestamp']
                        if isinstance(timestamp_seconds, datetime):
                            # If it's already a datetime, convert to UTC-aware
                            if timestamp_seconds.tzinfo is None:
                                timestamp_dt = timestamp_seconds.replace(tzinfo=timezone.utc)
                            else:
                                timestamp_dt = timestamp_seconds.astimezone(timezone.utc)
                        else:
                            # Convert from Unix timestamp to UTC-aware datetime
                            # Use time.time() as reference to ensure timezone-aware conversion
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
                f"[{position.symbol}] Failed to store entry trades in database: {e}"
            )

