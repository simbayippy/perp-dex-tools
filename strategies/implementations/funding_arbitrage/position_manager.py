"""
Funding Arbitrage Position Manager.

Extends base position manager with funding-specific logic:
- Track funding payments over time
- Calculate cumulative PnL from funding
- Aggregate long/short sides into single logical position (PositionHold pattern)
- Database persistence for crash recovery

⭐ Inspired by Hummingbot's PositionHold pattern ⭐
⭐ Uses PostgreSQL via funding_rate_service database ⭐
⭐ Single source of truth: Database (no in-memory cache) ⭐
"""

from typing import Dict, List, Optional, Any
from decimal import Decimal
from datetime import datetime, date
from uuid import UUID
import json
from helpers.unified_logger import get_core_logger

from strategies.components.base_components import BasePositionManager, Position
from .models import FundingArbPosition

# Import database connection from funding_rate_service (optional for testing)
try:
    from database.connection import database
    from funding_rate_service.core.mappers import dex_mapper, symbol_mapper
    from database.repositories.symbol_repository import SymbolRepository
    from database.repositories.dex_repository import DEXRepository
    DATABASE_AVAILABLE = True
except ImportError:
    # For testing - database not available
    database = None
    dex_mapper = None
    symbol_mapper = None
    SymbolRepository = None  # type: ignore
    DEXRepository = None  # type: ignore
    DATABASE_AVAILABLE = False


class FundingArbPositionManager(BasePositionManager):
    """
    Position manager for funding arbitrage strategy.
    
    ⭐ Key Features from Hummingbot ⭐:
    - Aggregate long/short into single logical position
    - Track cumulative funding payments
    - Calculate net PnL including fees
    - Support for rebalancing workflow
    
    ⭐ Database Persistence ⭐:
    - All positions stored in PostgreSQL (funding_rate_service DB)
    - No in-memory cache - DB is single source of truth
    - Simple, predictable behavior
    
    Enhancements over base manager:
    - Funding payment tracking (persisted)
    - Divergence monitoring
    - Rebalance state management
    """
    
    def __init__(self):
        """Initialize funding arbitrage position manager."""
        super().__init__()
        self.logger = get_core_logger("funding_arb_position_manager")
        self._initialized = False

    def _prepare_metadata_for_storage(self, metadata: Optional[Dict[str, Any]]) -> Optional[str]:
        """Convert metadata dict into JSON-serializable string."""
        if not metadata:
            return None

        def _sanitize(value: Any) -> Any:
            if isinstance(value, dict):
                return {key: _sanitize(val) for key, val in value.items()}
            if isinstance(value, (list, tuple, set)):
                return [_sanitize(item) for item in value]
            if isinstance(value, Decimal):
                return float(value) if value.is_finite() else str(value)
            if isinstance(value, (datetime, date)):
                return value.isoformat()
            if isinstance(value, UUID):
                return str(value)
            if isinstance(value, (str, int, float, bool)) or value is None:
                return value
            return str(value)

        sanitized_metadata = _sanitize(metadata)
        return json.dumps(sanitized_metadata)
    
    def _check_database_available(self) -> bool:
        """Check if database is available for operations."""
        if not DATABASE_AVAILABLE:
            self.logger.warning("Database operation skipped - running in test mode")
            return False
        return True
    
    async def initialize(self):
        """
        Initialize manager and ensure database connection.
        
        Called once on strategy startup.
        """
        if self._initialized:
            return
        
        if not self._check_database_available():
            self.logger.warning("Database not available - running in test mode")
            self._initialized = True
            return
        
        # Connect to database if not already connected
        if not database.is_connected:
            await database.connect()
            self.logger.info("Database connection established")

        await self._ensure_mappers_loaded()
        
        # Count open positions for logging
        open_positions = await self.get_open_positions()
        
        self._initialized = True
        self.logger.info(
            f"Position manager initialized with {len(open_positions)} open positions"
        )

    async def _ensure_mappers_loaded(self) -> None:
        """Ensure DEX and symbol mappers are populated from the database."""
        if not DATABASE_AVAILABLE:
            return

        try:
            if (not dex_mapper.is_loaded() or not symbol_mapper.is_loaded()) and not database.is_connected:
                await database.connect()

            if not dex_mapper.is_loaded():
                await dex_mapper.load_from_db(database)
            if not symbol_mapper.is_loaded():
                await symbol_mapper.load_from_db(database)
        except Exception as exc:  # pragma: no cover - defensive logging
            self.logger.error(f"Failed to load mapper data: {exc}")
            raise

    # ========================================================================
    # Core CRUD Operations (BasePositionManager Interface)
    # ========================================================================

    async def create(self, position: FundingArbPosition) -> UUID:
        """
        Create new funding arbitrage position and persist to database.
        
        Args:
            position: FundingArbPosition to track
        
        Returns:
            Position ID
        
        Raises:
            ValueError: If position already exists or invalid data
        """
        if not self._check_database_available():
            self.logger.warning("Database unavailable - position not persisted")
            return position.id
        
        # Get IDs for foreign keys
        symbol_id = symbol_mapper.get_id(position.symbol)
        long_dex_id = dex_mapper.get_id(position.long_dex)
        short_dex_id = dex_mapper.get_id(position.short_dex)

        # Fetch missing mappings from database if needed
        if symbol_id is None:
            symbol_repo = SymbolRepository(database)
            symbol_id = await symbol_repo.get_or_create(position.symbol)
            symbol_mapper.add(symbol_id, position.symbol)

        if long_dex_id is None or short_dex_id is None:
            dex_repo = DEXRepository(database)

            if long_dex_id is None:
                long_row = await dex_repo.get_by_name(position.long_dex.lower())
                if not long_row:
                    raise ValueError(f"Unknown DEX '{position.long_dex}'")
                long_dex_id = long_row["id"]
                dex_mapper.add(long_dex_id, long_row["name"])

            if short_dex_id is None:
                short_row = await dex_repo.get_by_name(position.short_dex.lower())
                if not short_row:
                    raise ValueError(f"Unknown DEX '{position.short_dex}'")
                short_dex_id = short_row["id"]
                dex_mapper.add(short_dex_id, short_row["name"])

        if symbol_id is None or long_dex_id is None or short_dex_id is None:
            raise ValueError(
                f"Missing mapping for position {position.id}: "
                f"symbol_id={symbol_id}, long_dex_id={long_dex_id}, short_dex_id={short_dex_id}"
            )
        
        # Insert into database
        query = """
            INSERT INTO strategy_positions (
                id, strategy_name, symbol_id, long_dex_id, short_dex_id,
                size_usd, entry_long_rate, entry_short_rate, entry_divergence,
                opened_at, status, cumulative_funding_usd, funding_payments_count
            ) VALUES (
                :id, :strategy_name, :symbol_id, :long_dex_id, :short_dex_id,
                :size_usd, :entry_long_rate, :entry_short_rate, :entry_divergence,
                :opened_at, :status, :cumulative_funding, :payment_count
            )
        """
        
        await database.execute(query, values={
            "id": position.id,
            "strategy_name": "funding_arbitrage",
            "symbol_id": symbol_id,
            "long_dex_id": long_dex_id,
            "short_dex_id": short_dex_id,
            "size_usd": position.size_usd,
            "entry_long_rate": position.entry_long_rate,
            "entry_short_rate": position.entry_short_rate,
            "entry_divergence": position.entry_divergence,
            "opened_at": position.opened_at,
            "status": position.status,
            "cumulative_funding": Decimal("0"),
            "payment_count": 0
        })
        
        self.logger.info(
            f"✅ Created position {position.id}: {position.symbol} "
            f"({position.long_dex} / {position.short_dex}) "
            f"${position.size_usd} @ {position.entry_divergence*100:.3f}% APY"
        )
        
        return position.id

    async def get(self, position_id: UUID) -> Optional[FundingArbPosition]:
        """
        Get position by ID from database.
        
        Args:
            position_id: Position ID to load
        
        Returns:
            FundingArbPosition if found, None otherwise
        """
        if not self._check_database_available():
            return None
        
        query = """
            SELECT 
                p.id,
                s.symbol,
                p.long_dex_id,
                p.short_dex_id,
                p.size_usd,
                p.entry_long_rate,
                p.entry_short_rate,
                p.entry_divergence,
                p.opened_at,
                p.current_divergence,
                p.last_check,
                p.status,
                p.rebalance_pending,
                p.rebalance_reason,
                p.exit_reason,
                p.closed_at,
                p.pnl_usd,
                p.cumulative_funding_usd,
                p.metadata
            FROM strategy_positions p
            JOIN symbols s ON p.symbol_id = s.id
            WHERE p.id = :position_id
        """
        
        row = await database.fetch_one(query, values={"position_id": position_id})
        
        if not row:
            return None
        
        # Convert DB row to FundingArbPosition
        return FundingArbPosition(
            id=row['id'],
            symbol=row['symbol'],
            long_dex=dex_mapper.get_name(row['long_dex_id']),
            short_dex=dex_mapper.get_name(row['short_dex_id']),
            size_usd=row['size_usd'],
            entry_long_rate=row['entry_long_rate'],
            entry_short_rate=row['entry_short_rate'],
            entry_divergence=row['entry_divergence'],
            opened_at=row['opened_at'],
            current_divergence=row['current_divergence'],
            last_check=row['last_check'],
            status=row['status'],
            rebalance_pending=row['rebalance_pending'],
            rebalance_reason=row['rebalance_reason'],
            exit_reason=row['exit_reason'],
            closed_at=row['closed_at'],
            pnl_usd=row['pnl_usd']
        )
    
    async def get_open_positions(self) -> List[FundingArbPosition]:
        """
        Get all open positions from database.
        
        Returns:
            List of open FundingArbPosition instances
        """
        if not self._check_database_available():
            return []
        
        query = """
            SELECT 
                p.id,
                s.symbol,
                p.long_dex_id,
                p.short_dex_id,
                p.size_usd,
                p.entry_long_rate,
                p.entry_short_rate,
                p.entry_divergence,
                p.opened_at,
                p.current_divergence,
                p.last_check,
                p.status,
                p.rebalance_pending,
                p.rebalance_reason,
                p.exit_reason,
                p.closed_at,
                p.pnl_usd,
                p.cumulative_funding_usd,
                p.metadata
            FROM strategy_positions p
            JOIN symbols s ON p.symbol_id = s.id
            WHERE p.status = 'open'
        """
        
        rows = await database.fetch_all(query)
        
        positions = []
        for row in rows:
            position = FundingArbPosition(
                id=row['id'],
                symbol=row['symbol'],
                long_dex=dex_mapper.get_name(row['long_dex_id']),
                short_dex=dex_mapper.get_name(row['short_dex_id']),
                size_usd=row['size_usd'],
                entry_long_rate=row['entry_long_rate'],
                entry_short_rate=row['entry_short_rate'],
                entry_divergence=row['entry_divergence'],
                opened_at=row['opened_at'],
                current_divergence=row['current_divergence'],
                last_check=row['last_check'],
                status=row['status'],
                rebalance_pending=row['rebalance_pending'],
                rebalance_reason=row['rebalance_reason'],
                exit_reason=row['exit_reason'],
                closed_at=row['closed_at'],
                pnl_usd=row['pnl_usd']
            )
            positions.append(position)
        
        return positions

    async def find_open_position(
        self,
        symbol: str,
        long_dex: str,
        short_dex: str,
    ) -> Optional[FundingArbPosition]:
        """
        Find an open position matching the provided symbol/DEX tuple.

        Args:
            symbol: Trading symbol (e.g., "BTC")
            long_dex: Long-side DEX name
            short_dex: Short-side DEX name

        Returns:
            Matching FundingArbPosition or None if not found
        """
        if not self._check_database_available():
            return None

        await self._ensure_mappers_loaded()

        symbol_id = symbol_mapper.get_id(symbol)
        long_dex_id = dex_mapper.get_id(long_dex)
        short_dex_id = dex_mapper.get_id(short_dex)

        if symbol_id is None and SymbolRepository:
            symbol_repo = SymbolRepository(database)
            symbol_row = await symbol_repo.get_by_name(symbol)
            if symbol_row:
                symbol_id = symbol_row["id"]
                symbol_mapper.add(symbol_id, symbol_row["symbol"])

        if (long_dex_id is None or short_dex_id is None) and DEXRepository:
            dex_repo = DEXRepository(database)
            if long_dex_id is None:
                long_row = await dex_repo.get_by_name(long_dex.lower())
                if long_row:
                    long_dex_id = long_row["id"]
                    dex_mapper.add(long_dex_id, long_row["name"])
            if short_dex_id is None:
                short_row = await dex_repo.get_by_name(short_dex.lower())
                if short_row:
                    short_dex_id = short_row["id"]
                    dex_mapper.add(short_dex_id, short_row["name"])

        if symbol_id is None or long_dex_id is None or short_dex_id is None:
            return None

        query = """
            SELECT id
            FROM strategy_positions
            WHERE status = 'open'
              AND strategy_name = 'funding_arbitrage'
              AND symbol_id = :symbol_id
              AND long_dex_id = :long_dex_id
              AND short_dex_id = :short_dex_id
            LIMIT 1
        """

        row = await database.fetch_one(
            query,
            values={
                "symbol_id": symbol_id,
                "long_dex_id": long_dex_id,
                "short_dex_id": short_dex_id,
            },
        )

        if not row:
            return None

        return await self.get(row["id"])
    
    async def update(self, position: FundingArbPosition) -> None:
        """
        Update existing position in database.
        
        Args:
            position: Updated position
        """
        if not self._check_database_available():
            return
        
        # Get IDs for foreign keys
        symbol_id = symbol_mapper.get_id(position.symbol)
        long_dex_id = dex_mapper.get_id(position.long_dex)
        short_dex_id = dex_mapper.get_id(position.short_dex)
        
        # Convert metadata dict to JSON string for PostgreSQL
        metadata_json = self._prepare_metadata_for_storage(position.metadata)
        
        query = """
            UPDATE strategy_positions
            SET symbol_id = :symbol_id,
                long_dex_id = :long_dex_id,
                short_dex_id = :short_dex_id,
                size_usd = :size_usd,
                entry_long_rate = :entry_long_rate,
                entry_short_rate = :entry_short_rate,
                entry_divergence = :entry_divergence,
                current_divergence = :current_divergence,
                last_check = :last_check,
                status = :status,
                rebalance_pending = :rebalance_pending,
                rebalance_reason = :rebalance_reason,
                exit_reason = :exit_reason,
                closed_at = :closed_at,
                pnl_usd = :pnl_usd,
                metadata = CAST(:metadata AS jsonb)
            WHERE id = :position_id
        """
        
        await database.execute(query, values={
            "symbol_id": symbol_id,
            "long_dex_id": long_dex_id,
            "short_dex_id": short_dex_id,
            "size_usd": position.size_usd,
            "entry_long_rate": position.entry_long_rate,
            "entry_short_rate": position.entry_short_rate,
            "entry_divergence": position.entry_divergence,
            "current_divergence": position.current_divergence,
            "last_check": position.last_check or datetime.now(),
            "status": position.status,
            "rebalance_pending": position.rebalance_pending,
            "rebalance_reason": position.rebalance_reason,
            "exit_reason": position.exit_reason,
            "closed_at": position.closed_at,
            "pnl_usd": position.pnl_usd,
            "metadata": metadata_json,
            "position_id": position.id
        })
    
    async def close(
        self,
        position_id: UUID,
        exit_reason: str,
        pnl_usd: Optional[Decimal] = None
    ):
        """
        Close funding arbitrage position in database.
        
        Args:
            position_id: Position to close
            exit_reason: Reason for exit
            pnl_usd: Final realized PnL (if known)
        """
        if not self._check_database_available():
            return
        
        # Get position to check if already closed
        position = await self.get(position_id)
        if not position:
            self.logger.warning(f"Position {position_id} not found, cannot close")
            return
        
        # Check if already closed
        if position.status == "closed":
            self.logger.warning(
                f"Position {position_id} already closed (reason: {position.exit_reason}), "
                f"skipping duplicate close request"
            )
            return
        
        # Calculate PnL if not provided
        if pnl_usd is None:
            # Get cumulative funding from DB
            cumulative_funding = await self.get_cumulative_funding(position_id)
            pnl_usd = cumulative_funding
        
        closed_at = datetime.now()

        query = """
            UPDATE strategy_positions
            SET status = 'closed',
                exit_reason = :exit_reason,
                closed_at = :closed_at,
                pnl_usd = :pnl_usd,
                rebalance_pending = FALSE
            WHERE id = :position_id AND status = 'open'
        """
        
        try:
            await database.execute(query, values={
                "exit_reason": exit_reason,
                "closed_at": closed_at,
                "pnl_usd": pnl_usd,
                "position_id": position_id
            })
            
            # Log closure
            self.logger.info(
                f"✅ Closed position {position_id}: {position.symbol} "
                f"Reason: {exit_reason}, PnL: ${pnl_usd:.2f}, "
                f"Duration: {(closed_at - position.opened_at).total_seconds() / 3600:.1f}h"
            )
        except Exception as exc:  # pragma: no cover - defensive logging
            self.logger.error(f"Failed to close position {position_id}: {exc}")
            raise
    
    async def get_position_summary(
        self,
        position_id: UUID,
        current_market_data: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Get position summary with current P&L.
        
        Args:
            position_id: Position ID
            current_market_data: Current market data (optional)
        
        Returns:
            Dict with position summary
        """
        position = await self.get(position_id)
        if not position:
            return {}
        
        cumulative_funding = await self.get_cumulative_funding(position_id)
        
        return {
            'position_id': str(position_id),
            'symbol': position.symbol,
            'size_usd': float(position.size_usd),
            'status': position.status,
            'net_pnl_usd': float(position.get_net_pnl()),
            'net_pnl_pct': float(position.get_net_pnl_pct()),
            'cumulative_funding': float(cumulative_funding),
            'age_hours': position.get_age_hours(),
            'long_dex': position.long_dex,
            'short_dex': position.short_dex
        }

    # ========================================================================
    # Funding-Specific Operations
    # ========================================================================

    async def record_funding_payment(
        self,
        position_id: UUID,
        long_payment: Decimal,
        short_payment: Decimal,
        timestamp: datetime,
        long_rate: Optional[Decimal] = None,
        short_rate: Optional[Decimal] = None,
        divergence: Optional[Decimal] = None
    ):
        """
        Record funding payment for position.
        
        ⭐ Pattern from Hummingbot's did_complete_funding_payment() ⭐
        
        Persists to database only.
        
        Args:
            position_id: Position receiving payment
            long_payment: Funding received/paid on long side (negative = paid)
            short_payment: Funding received/paid on short side (positive = received)
            timestamp: When payment occurred
            long_rate: Funding rate on long side (optional)
            short_rate: Funding rate on short side (optional)
            divergence: Rate divergence at payment time (optional)
        """
        if not self._check_database_available():
            return
        
        # Calculate net payment (long pays, short receives in arb)
        net_payment = short_payment - long_payment
        
        # Insert into database
        query = """
            INSERT INTO funding_payments (
                position_id, payment_time, long_payment, short_payment, net_payment,
                long_rate, short_rate, divergence
            ) VALUES (
                :position_id, :payment_time, :long_payment, :short_payment, :net_payment,
                :long_rate, :short_rate, :divergence
            )
        """
        
        await database.execute(query, values={
            "position_id": position_id,
            "payment_time": timestamp,
            "long_payment": long_payment,
            "short_payment": short_payment,
            "net_payment": net_payment,
            "long_rate": long_rate,
            "short_rate": short_rate,
            "divergence": divergence
        })
        
        # Update cumulative funding in positions table
        update_query = """
            UPDATE strategy_positions
            SET cumulative_funding_usd = cumulative_funding_usd + :net_payment,
                funding_payments_count = funding_payments_count + 1
            WHERE id = :position_id
        """
        
        await database.execute(update_query, values={
            "net_payment": net_payment,
            "position_id": position_id
        })
        
        # Get new cumulative for logging
        new_cumulative = await self.get_cumulative_funding(position_id)
        
        self.logger.debug(
            f"Funding payment for {position_id}: "
            f"net=${net_payment:.4f}, cumulative=${new_cumulative:.4f}"
        )
    
    async def update_position_state(
        self,
        position_id: UUID,
        current_divergence: Decimal,
        current_long_rate: Optional[Decimal] = None,
        current_short_rate: Optional[Decimal] = None
    ):
        """
        Update position with current market state.
        
        Args:
            position_id: Position to update
            current_divergence: Current funding rate spread
            current_long_rate: Current long side funding rate (optional)
            current_short_rate: Current short side funding rate (optional)
        """
        if not self._check_database_available():
            return
        
        query = """
            UPDATE strategy_positions
            SET current_divergence = :current_divergence,
                last_check = :last_check
            WHERE id = :position_id
        """
        
        await database.execute(query, values={
            "current_divergence": current_divergence,
            "last_check": datetime.now(),
            "position_id": position_id
        })
    
    async def flag_for_rebalance(
        self,
        position_id: UUID,
        reason: str
    ):
        """
        Flag position for rebalancing.
        
        Args:
            position_id: Position to flag
            reason: Reason code (e.g. 'PROFIT_EROSION', 'DIVERGENCE_FLIPPED')
        """
        if not self._check_database_available():
            return
        
        query = """
            UPDATE strategy_positions
            SET rebalance_pending = TRUE,
                rebalance_reason = :reason
            WHERE id = :position_id
        """
        
        await database.execute(query, values={
            "reason": reason,
            "position_id": position_id
        })
        
        self.logger.info(
            f"Flagged position {position_id} for rebalance: {reason}"
        )
    
    async def get_pending_rebalance_positions(self) -> List[FundingArbPosition]:
        """
        Get all positions flagged for rebalancing.
        
        Returns:
            List of positions pending rebalance
        """
        if not self._check_database_available():
            return []
        
        query = """
            SELECT 
                p.id,
                s.symbol,
                p.long_dex_id,
                p.short_dex_id,
                p.size_usd,
                p.entry_long_rate,
                p.entry_short_rate,
                p.entry_divergence,
                p.opened_at,
                p.current_divergence,
                p.last_check,
                p.status,
                p.rebalance_pending,
                p.rebalance_reason,
                p.exit_reason,
                p.closed_at,
                p.pnl_usd
            FROM strategy_positions p
            JOIN symbols s ON p.symbol_id = s.id
            WHERE p.status = 'open' AND p.rebalance_pending = TRUE
        """
        
        rows = await database.fetch_all(query)
        
        positions = []
        for row in rows:
            position = FundingArbPosition(
                id=row['id'],
                symbol=row['symbol'],
                long_dex=dex_mapper.get_name(row['long_dex_id']),
                short_dex=dex_mapper.get_name(row['short_dex_id']),
                size_usd=row['size_usd'],
                entry_long_rate=row['entry_long_rate'],
                entry_short_rate=row['entry_short_rate'],
                entry_divergence=row['entry_divergence'],
                opened_at=row['opened_at'],
                current_divergence=row['current_divergence'],
                last_check=row['last_check'],
                status=row['status'],
                rebalance_pending=row['rebalance_pending'],
                rebalance_reason=row['rebalance_reason'],
                exit_reason=row['exit_reason'],
                closed_at=row['closed_at'],
                pnl_usd=row['pnl_usd']
            )
            positions.append(position)
        
        return positions
    
    async def get_cumulative_funding(self, position_id: UUID) -> Decimal:
        """
        Get cumulative funding for position from database.
        
        Args:
            position_id: Position ID
        
        Returns:
            Cumulative funding received (net)
        """
        if not self._check_database_available():
            return Decimal("0")
        
        query = """
            SELECT cumulative_funding_usd
            FROM strategy_positions
            WHERE id = :position_id
        """
        
        row = await database.fetch_one(query, values={"position_id": position_id})
        
        if not row:
            return Decimal("0")
        
        return row['cumulative_funding_usd'] or Decimal("0")
    
    async def get_funding_payments(self, position_id: UUID) -> List[Dict]:
        """
        Get all funding payment records for position from database.
        
        Args:
            position_id: Position ID
        
        Returns:
            List of payment records
        """
        if not self._check_database_available():
            return []
        
        query = """
            SELECT payment_time, long_payment, short_payment, net_payment,
                   long_rate, short_rate, divergence
            FROM funding_payments
            WHERE position_id = :position_id
            ORDER BY payment_time ASC
        """
        
        rows = await database.fetch_all(query, values={"position_id": position_id})
        
        payments = []
        for row in rows:
            payments.append({
                'timestamp': row['payment_time'],
                'long_payment': row['long_payment'],
                'short_payment': row['short_payment'],
                'net_payment': row['net_payment'],
                'long_rate': row['long_rate'],
                'short_rate': row['short_rate'],
                'divergence': row['divergence']
            })
        
        return payments
    
    async def get_position_metrics(
        self,
        position_id: UUID
    ) -> Dict:
        """
        Get comprehensive metrics for position.
        
        ⭐ Similar to Hummingbot's executor.info() ⭐
        
        Args:
            position_id: Position ID
        
        Returns:
            Dict with metrics
        """
        position = await self.get(position_id)
        if not position:
            return {}
        
        # Calculate metrics
        age_hours = (datetime.now() - position.opened_at).total_seconds() / 3600
        
        cumulative_funding = await self.get_cumulative_funding(position_id)
        funding_payments = await self.get_funding_payments(position_id)
        
        # Profit erosion
        if position.entry_divergence > 0 and position.current_divergence:
            erosion = float(position.current_divergence / position.entry_divergence)
        else:
            erosion = 1.0
        
        return {
            'position_id': str(position_id),
            'symbol': position.symbol,
            'long_dex': position.long_dex,
            'short_dex': position.short_dex,
            'size_usd': float(position.size_usd),
            'age_hours': age_hours,
            'entry_divergence_pct': float(position.entry_divergence * 100),
            'current_divergence_pct': float((position.current_divergence or position.entry_divergence) * 100),
            'profit_erosion': erosion,
            'cumulative_funding_usd': float(cumulative_funding),
            'funding_payments_count': len(funding_payments),
            'status': position.status,
            'rebalance_pending': position.rebalance_pending,
            'rebalance_reason': position.rebalance_reason
        }
    
    async def get_portfolio_summary(self) -> Dict:
        """
        Get summary of entire position portfolio.
        
        Returns:
            Dict with portfolio-level metrics
        """
        open_positions = await self.get_open_positions()
        
        total_exposure = sum(p.size_usd for p in open_positions)
        
        # Get cumulative funding for all positions
        total_cumulative_pnl = Decimal("0")
        for p in open_positions:
            cumulative = await self.get_cumulative_funding(p.id)
            total_cumulative_pnl += cumulative
        
        pending_rebalance = await self.get_pending_rebalance_positions()
        
        return {
            'total_positions': len(open_positions),
            'total_exposure_usd': float(total_exposure),
            'total_cumulative_pnl_usd': float(total_cumulative_pnl),
            'positions_pending_rebalance': len(pending_rebalance)
        }
    
    async def shutdown(self):
        """Close database connection and cleanup resources."""
        if DATABASE_AVAILABLE and database.is_connected:
            await database.disconnect()
            self.logger.info("Database connection closed")
