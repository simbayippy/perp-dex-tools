"""
Trade Fill Repository - handles trade fill database operations
"""

from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID
from databases import Database

from helpers.unified_logger import get_core_logger

logger = get_core_logger("trade_fill_repository")


class TradeFillRepository:
    """Repository for Trade Fill data access"""
    
    def __init__(self, db: Database):
        self.db = db
    
    async def insert_trade_fill(
        self,
        position_id: UUID,
        account_id: UUID,
        trade_type: str,
        dex_id: int,
        symbol_id: int,
        order_id: str,
        trade_id: Optional[str],
        timestamp: datetime,
        side: str,
        total_quantity: Decimal,
        weighted_avg_price: Decimal,
        total_fee: Decimal,
        fee_currency: str,
        realized_pnl: Optional[Decimal] = None,
        realized_funding: Optional[Decimal] = None,
        fill_count: int = 1,
    ) -> Optional[UUID]:
        """
        Insert a new trade fill record.
        
        Args:
            position_id: Position UUID
            account_id: Account UUID
            trade_type: 'entry' or 'exit'
            dex_id: DEX ID
            symbol_id: Symbol ID
            order_id: Order ID (required, unique per position)
            trade_id: Exchange-specific trade ID (optional)
            timestamp: First fill timestamp
            side: 'buy' or 'sell'
            total_quantity: Sum of all quantities
            weighted_avg_price: Weighted average price
            total_fee: Sum of all fees
            fee_currency: Fee currency (e.g., 'USDC', 'USDT')
            realized_pnl: Realized PnL if available
            realized_funding: Realized funding if available
            fill_count: Number of fills aggregated
            
        Returns:
            Trade fill ID if successful, None if duplicate or error
        """
        query = """
            INSERT INTO trade_fills (
                position_id, account_id, trade_type,
                dex_id, symbol_id, order_id, trade_id,
                timestamp, side, total_quantity, weighted_avg_price,
                total_fee, fee_currency, realized_pnl, realized_funding,
                fill_count
            )
            VALUES (
                :position_id, :account_id, :trade_type,
                :dex_id, :symbol_id, :order_id, :trade_id,
                :timestamp, :side, :total_quantity, :weighted_avg_price,
                :total_fee, :fee_currency, :realized_pnl, :realized_funding,
                :fill_count
            )
            ON CONFLICT (position_id, order_id) DO NOTHING
            RETURNING id
        """
        
        try:
            # Convert timezone-aware datetime to naive UTC for PostgreSQL TIMESTAMP column
            # PostgreSQL TIMESTAMP columns expect naive datetimes (without timezone)
            timestamp_naive = self._to_naive_utc(timestamp)
            
            result = await self.db.fetch_val(
                query,
                {
                    "position_id": position_id,
                    "account_id": account_id,
                    "trade_type": trade_type,
                    "dex_id": dex_id,
                    "symbol_id": symbol_id,
                    "order_id": order_id,
                    "trade_id": trade_id,
                    "timestamp": timestamp_naive,
                    "side": side,
                    "total_quantity": total_quantity,
                    "weighted_avg_price": weighted_avg_price,
                    "total_fee": total_fee,
                    "fee_currency": fee_currency,
                    "realized_pnl": realized_pnl,
                    "realized_funding": realized_funding,
                    "fill_count": fill_count,
                }
            )
            return result
        except Exception as e:
            logger.error(f"Failed to insert trade fill: {e}")
            return None
    
    @staticmethod
    def _to_naive_utc(dt: datetime) -> datetime:
        """
        Convert timezone-aware datetime to naive UTC datetime.
        
        PostgreSQL TIMESTAMP columns expect naive datetimes. This helper ensures
        we always pass naive UTC datetimes to avoid timezone mismatch errors.
        
        Args:
            dt: Datetime (timezone-aware or naive)
            
        Returns:
            Naive UTC datetime
        """
        if dt is None:
            return None
        if dt.tzinfo is None:
            # Already naive, return as-is
            return dt
        # Convert to UTC and remove timezone info
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    
    async def get_trades_by_position(
        self,
        position_id: UUID,
        trade_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get all trades for a position.
        
        Args:
            position_id: Position UUID
            trade_type: Optional filter ('entry' or 'exit')
            
        Returns:
            List of trade fill records
        """
        query = """
            SELECT tf.*, d.name as dex_name, s.symbol
            FROM trade_fills tf
            JOIN dexes d ON tf.dex_id = d.id
            JOIN symbols s ON tf.symbol_id = s.id
            WHERE tf.position_id = :position_id
        """
        values = {"position_id": position_id}
        
        if trade_type:
            query += " AND tf.trade_type = :trade_type"
            values["trade_type"] = trade_type
        
        query += " ORDER BY tf.timestamp ASC"
        
        try:
            rows = await self.db.fetch_all(query, values)
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Failed to get trades by position: {e}")
            return []
    
    async def get_trades_by_order_id(self, order_id: str) -> Optional[Dict[str, Any]]:
        """
        Get trade by order_id.
        
        Args:
            order_id: Order ID
            
        Returns:
            Trade fill record or None
        """
        query = """
            SELECT tf.*, d.name as dex_name, s.symbol
            FROM trade_fills tf
            JOIN dexes d ON tf.dex_id = d.id
            JOIN symbols s ON tf.symbol_id = s.id
            WHERE tf.order_id = :order_id
            LIMIT 1
        """
        
        try:
            row = await self.db.fetch_one(query, {"order_id": order_id})
            return dict(row) if row else None
        except Exception as e:
            logger.error(f"Failed to get trade by order_id: {e}")
            return None
    
    async def get_trades_by_account(
        self,
        account_id: UUID,
        symbol: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        trade_type: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Get trades for an account with optional filters.
        
        Args:
            account_id: Account UUID
            symbol: Optional symbol filter
            start_time: Optional start time filter
            end_time: Optional end time filter
            trade_type: Optional trade type filter ('entry' or 'exit')
            limit: Maximum number of results
            
        Returns:
            List of trade fill records
        """
        query = """
            SELECT tf.*, d.name as dex_name, s.symbol
            FROM trade_fills tf
            JOIN dexes d ON tf.dex_id = d.id
            JOIN symbols s ON tf.symbol_id = s.id
            WHERE tf.account_id = :account_id
        """
        values = {"account_id": account_id}
        
        if symbol:
            query += " AND s.symbol = :symbol"
            values["symbol"] = symbol
        
        if start_time:
            query += " AND tf.timestamp >= :start_time"
            values["start_time"] = start_time
        
        if end_time:
            query += " AND tf.timestamp <= :end_time"
            values["end_time"] = end_time
        
        if trade_type:
            query += " AND tf.trade_type = :trade_type"
            values["trade_type"] = trade_type
        
        query += " ORDER BY tf.timestamp DESC LIMIT :limit"
        values["limit"] = limit
        
        try:
            rows = await self.db.fetch_all(query, values)
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Failed to get trades by account: {e}")
            return []
    
    async def get_trades_by_symbol_and_dex(
        self,
        symbol_id: int,
        dex_id: int,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Get trades filtered by symbol and DEX.
        
        Args:
            symbol_id: Symbol ID
            dex_id: DEX ID
            start_time: Optional start time filter
            end_time: Optional end time filter
            limit: Maximum number of results
            
        Returns:
            List of trade fill records
        """
        query = """
            SELECT tf.*, d.name as dex_name, s.symbol
            FROM trade_fills tf
            JOIN dexes d ON tf.dex_id = d.id
            JOIN symbols s ON tf.symbol_id = s.id
            WHERE tf.symbol_id = :symbol_id AND tf.dex_id = :dex_id
        """
        values = {"symbol_id": symbol_id, "dex_id": dex_id}
        
        if start_time:
            query += " AND tf.timestamp >= :start_time"
            values["start_time"] = start_time
        
        if end_time:
            query += " AND tf.timestamp <= :end_time"
            values["end_time"] = end_time
        
        query += " ORDER BY tf.timestamp DESC LIMIT :limit"
        values["limit"] = limit
        
        try:
            rows = await self.db.fetch_all(query, values)
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Failed to get trades by symbol and dex: {e}")
            return []

