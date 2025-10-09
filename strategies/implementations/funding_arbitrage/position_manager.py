"""
Funding Arbitrage Position Manager.

Extends base position manager with funding-specific logic:
- Track funding payments over time
- Calculate cumulative PnL from funding
- Aggregate long/short sides into single logical position (PositionHold pattern)
- Database persistence for crash recovery

⭐ Inspired by Hummingbot's PositionHold pattern ⭐
⭐ Uses PostgreSQL via funding_rate_service database ⭐
"""

from typing import Dict, List, Optional, Any
from decimal import Decimal
from datetime import datetime
from uuid import UUID
import logging
import asyncio

from strategies.components.base_components import BasePositionManager, Position
from .models import FundingArbPosition

# Import database connection from funding_rate_service (optional for testing)
try:
    from funding_rate_service.database.connection import database
    from funding_rate_service.core.mappers import dex_mapper, symbol_mapper
    DATABASE_AVAILABLE = True
except ImportError:
    # For testing - database not available
    database = None
    dex_mapper = None
    symbol_mapper = None
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
    - Positions stored in PostgreSQL (funding_rate_service DB)
    - In-memory cache for fast access
    - Automatic recovery on restart
    
    Enhancements over base manager:
    - Funding payment tracking (persisted)
    - Divergence monitoring
    - Rebalance state management
    """
    
    def __init__(self):
        """Initialize funding arbitrage position manager."""
        super().__init__()
        
        # In-memory cache for fast access (loaded from DB on startup)
        self._positions: Dict[UUID, FundingArbPosition] = {}  # {position_id: position}
        self._funding_payments: Dict[UUID, List[Dict]] = {}  # {position_id: [payment_records]}
        self._cumulative_funding: Dict[UUID, Decimal] = {}   # {position_id: total_funding}
        
        # 🔒 CRITICAL FIX: Position locks to prevent simultaneous operations
        self._position_locks: Dict[UUID, asyncio.Lock] = {}  # {position_id: lock}
        self._master_lock = asyncio.Lock()  # For managing the locks dict itself
        
        self.logger = logging.getLogger(__name__)
        self._initialized = False
    
    def _check_database_available(self) -> bool:
        """Check if database is available for operations."""
        if not DATABASE_AVAILABLE:
            self.logger.warning("Database operation skipped - running in test mode")
            return False
        return True
    
    async def _get_position_lock(self, position_id: UUID) -> asyncio.Lock:
        """
        Get or create a lock for a position.
        
        🔒 CRITICAL FIX: Prevents race conditions on position operations
        
        Args:
            position_id: Position ID
        
        Returns:
            asyncio.Lock for the position
        """
        async with self._master_lock:
            if position_id not in self._position_locks:
                self._position_locks[position_id] = asyncio.Lock()
            return self._position_locks[position_id]
    
    async def initialize(self):
        """
        Initialize manager and load positions from database.
        
        Called once on strategy startup to restore state.
        """
        if self._initialized:
            return
        
        # Connect to database if not already connected
        if not database.is_connected:
            await database.connect()
            self.logger.info("Database connection established")
        
        # Load open positions from database
        await self._load_positions_from_db()
        
        self._initialized = True
        self.logger.info(
            f"Position manager initialized with {len(await self.get_open_positions())} open positions"
        )
    
    async def _load_positions_from_db(self):
        """Load all open positions from database into memory cache."""
        if not self._check_database_available():
            return
        
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
        
        for row in rows:
            # Convert DB row to FundingArbPosition
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
            
            # Add to parent's position cache
            self._positions[position.id] = position
            
            # Initialize funding tracking
            self._cumulative_funding[position.id] = row['cumulative_funding_usd'] or Decimal("0")
            
            # Load funding payments for this position
            await self._load_funding_payments(position.id)
    
    async def _load_funding_payments(self, position_id: UUID):
        """Load funding payment history for a position."""
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
        
        self._funding_payments[position_id] = payments
    
    async def _check_position_exists_in_db(self, position_id: UUID) -> bool:
        """
        Check if position exists in database.
        
        🔒 CRITICAL FIX: Prevents double-adding positions
        
        Args:
            position_id: Position ID to check
        
        Returns:
            True if position exists in database
        """
        if not self._check_database_available():
            return False
        
        query = "SELECT COUNT(*) as count FROM strategy_positions WHERE id = :position_id"
        result = await database.fetch_one(query, values={"position_id": position_id})
        return result['count'] > 0 if result else False
    
    async def _load_position_from_db(self, position_id: UUID) -> Optional[FundingArbPosition]:
        """
        Load a single position from database.
        
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
        
        # Add to memory cache
        self._positions[position.id] = position
        
        # Initialize funding tracking
        self._cumulative_funding[position.id] = row['cumulative_funding_usd'] or Decimal("0")
        
        # Load funding payments
        await self._load_funding_payments(position.id)
        
        return position
    
    async def create_position(
        self,
        position: FundingArbPosition
    ) -> UUID:
        """
        Create new funding arbitrage position.
        
        Stores in both database and memory cache.
        
        🔒 CRITICAL FIX: Added duplicate detection to prevent double-adding
        
        Args:
            position: FundingArbPosition to track
        
        Returns:
            Position ID
        
        Raises:
            ValueError: If position already exists
        """
        # Check if position already exists in memory
        if position.id in self._positions:
            self.logger.error(
                f"❌ Position {position.id} already exists in memory! "
                f"Refusing to create duplicate."
            )
            raise ValueError(f"Position {position.id} already exists in memory")
        
        # Check if position exists in database (defense in depth)
        exists_in_db = await self._check_position_exists_in_db(position.id)
        if exists_in_db:
            self.logger.error(
                f"❌ Position {position.id} already exists in database! "
                f"Loading from DB instead of creating new."
            )
            # Load existing position instead of creating duplicate
            loaded = await self._load_position_from_db(position.id)
            if loaded:
                self.logger.warning(
                    f"⚠️ Loaded existing position {position.id} from database"
                )
                return loaded.id
            else:
                raise ValueError(f"Position {position.id} exists in DB but failed to load")
        
        # Get IDs for foreign keys
        symbol_id = symbol_mapper.get_id(position.symbol)
        long_dex_id = dex_mapper.get_id(position.long_dex)
        short_dex_id = dex_mapper.get_id(position.short_dex)
        
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
        
        # Store in memory cache
        self._positions[position.id] = position
        
        # Initialize funding tracking
        self._funding_payments[position.id] = []
        self._cumulative_funding[position.id] = Decimal("0")
        
        self.logger.info(
            f"✅ Created position {position.id}: {position.symbol} "
            f"({position.long_dex} / {position.short_dex}) "
            f"${position.size_usd} @ {position.entry_divergence*100:.3f}% APY"
        )
        
        return position.id
    
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
        
        Persists to database and updates in-memory cache.
        
        Args:
            position_id: Position receiving payment
            long_payment: Funding received/paid on long side (negative = paid)
            short_payment: Funding received/paid on short side (positive = received)
            timestamp: When payment occurred
            long_rate: Funding rate on long side (optional)
            short_rate: Funding rate on short side (optional)
            divergence: Rate divergence at payment time (optional)
        """
        if position_id not in self._funding_payments:
            self.logger.warning(f"Unknown position {position_id}, skipping funding payment")
            return
        
        # Calculate net payment (long pays, short receives in arb)
        # For funding arb: short_payment (positive) - long_payment (should be negative)
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
        new_cumulative = self._cumulative_funding.get(position_id, Decimal("0")) + net_payment
        
        update_query = """
            UPDATE strategy_positions
            SET cumulative_funding_usd = :cumulative_funding,
                funding_payments_count = funding_payments_count + 1
            WHERE id = :position_id
        """
        
        await database.execute(update_query, values={
            "cumulative_funding": new_cumulative,
            "position_id": position_id
        })
        
        # Record payment in memory
        payment_record = {
            'timestamp': timestamp,
            'long_payment': long_payment,
            'short_payment': short_payment,
            'net_payment': net_payment,
            'long_rate': long_rate,
            'short_rate': short_rate,
            'divergence': divergence
        }
        self._funding_payments[position_id].append(payment_record)
        
        # Update cumulative in memory
        self._cumulative_funding[position_id] = new_cumulative
        
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
        
        Persists to database and updates in-memory cache.
        
        Args:
            position_id: Position to update
            current_divergence: Current funding rate spread
            current_long_rate: Current long side funding rate (optional)
            current_short_rate: Current short side funding rate (optional)
        """
        position = await self.get_position(position_id)
        if not isinstance(position, FundingArbPosition):
            self.logger.warning(f"Position {position_id} is not a FundingArbPosition")
            return
        
        # Update in database
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
        
        # Update in memory
        position.current_divergence = current_divergence
        position.last_check = datetime.now()
        
        await self.update_position(position)
    
    async def flag_for_rebalance(
        self,
        position_id: UUID,
        reason: str
    ):
        """
        Flag position for rebalancing.
        
        Persists to database and updates in-memory cache.
        
        Args:
            position_id: Position to flag
            reason: Reason code (e.g. 'PROFIT_EROSION', 'DIVERGENCE_FLIPPED')
        """
        position = await self.get_position(position_id)
        if not isinstance(position, FundingArbPosition):
            return
        
        # Update in database
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
        
        # Update in memory
        position.rebalance_pending = True
        position.rebalance_reason = reason
        
        await self.update_position(position)
        
        self.logger.info(
            f"Flagged position {position_id} for rebalance: {reason}"
        )
    
    async def get_pending_rebalance_positions(self) -> List[FundingArbPosition]:
        """
        Get all positions flagged for rebalancing.
        
        Returns:
            List of positions pending rebalance
        """
        all_positions = await self.get_open_positions()
        return [
            p for p in all_positions
            if isinstance(p, FundingArbPosition) and p.rebalance_pending
        ]
    
    async def close_position(
        self,
        position_id: UUID,
        exit_reason: str,
        final_pnl_usd: Optional[Decimal] = None
    ):
        """
        Close funding arbitrage position.
        
        Persists to database and updates in-memory cache.
        
        🔒 CRITICAL FIX: Uses locking to prevent simultaneous closes
        
        Args:
            position_id: Position to close
            exit_reason: Reason for exit
            final_pnl_usd: Final realized PnL (if known)
        """
        # Acquire position lock to prevent simultaneous closes
        lock = await self._get_position_lock(position_id)
        
        async with lock:
            # Re-fetch position inside lock to ensure latest state
            position = await self.get_funding_position(position_id)
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
            if final_pnl_usd is None:
                # Use cumulative funding as baseline
                final_pnl_usd = self._cumulative_funding.get(position_id, Decimal("0"))
            
            # Update in database
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
            
            result = await database.execute(query, values={
                "exit_reason": exit_reason,
                "closed_at": closed_at,
                "pnl_usd": final_pnl_usd,
                "position_id": position_id
            })
            
            # Update in memory
            position.status = "closed"
            position.exit_reason = exit_reason
            position.closed_at = closed_at
            position.pnl_usd = final_pnl_usd
            position.rebalance_pending = False
            
            await self.update_funding_position(position)
            
            # Log closure
            self.logger.info(
                f"✅ Closed position {position_id}: {position.symbol} "
                f"Reason: {exit_reason}, PnL: ${final_pnl_usd:.2f}, "
                f"Duration: {(closed_at - position.opened_at).total_seconds() / 3600:.1f}h"
            )
            
            # Keep tracking data in database for analysis
            # Memory can be cleaned up if needed
            # del self._funding_payments[position_id]
            # del self._cumulative_funding[position_id]
            # del self._position_locks[position_id]  # Cleanup lock
    
    def get_cumulative_funding(self, position_id: UUID) -> Decimal:
        """
        Get cumulative funding for position.
        
        Args:
            position_id: Position ID
        
        Returns:
            Cumulative funding received (net)
        """
        return self._cumulative_funding.get(position_id, Decimal("0"))
    
    def get_funding_payments(self, position_id: UUID) -> List[Dict]:
        """
        Get all funding payment records for position.
        
        Args:
            position_id: Position ID
        
        Returns:
            List of payment records
        """
        return self._funding_payments.get(position_id, [])
    
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
            Dict with metrics:
                - current_pnl: Current unrealized PnL
                - cumulative_funding: Total funding received
                - age_hours: Position age
                - profit_erosion: How much edge has eroded
                - funding_payments_count: Number of funding payments
        """
        position = await self.get_position(position_id)
        if not isinstance(position, FundingArbPosition):
            return {}
        
        # Calculate metrics
        age_hours = (datetime.now() - position.opened_at).total_seconds() / 3600
        
        cumulative_funding = self.get_cumulative_funding(position_id)
        funding_payments = self.get_funding_payments(position_id)
        
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
        
        total_exposure = sum(
            p.size_usd for p in open_positions
            if isinstance(p, FundingArbPosition)
        )
        
        total_cumulative_pnl = sum(
            self.get_cumulative_funding(p.id)
            for p in open_positions
            if isinstance(p, FundingArbPosition)
        )
        
        return {
            'total_positions': len(open_positions),
            'total_exposure_usd': float(total_exposure),
            'total_cumulative_pnl_usd': float(total_cumulative_pnl),
            'positions_pending_rebalance': len(await self.get_pending_rebalance_positions())
        }
    
    async def get_funding_position(self, position_id: UUID) -> Optional[FundingArbPosition]:
        """
        Get funding arbitrage position by ID (returns as FundingArbPosition).
        
        Args:
            position_id: Position ID
        
        Returns:
            FundingArbPosition if found, None otherwise
        """
        return self._positions.get(position_id)
    
    async def update_funding_position(self, position: FundingArbPosition) -> None:
        """
        Update funding arbitrage position in memory.
        
        Args:
            position: Updated position
        """
        if position.id in self._positions:
            self._positions[position.id] = position
        else:
            self.logger.warning(f"Position {position.id} not found for update")
    
    # ========================================================================
    # BasePositionManager Interface Implementation
    # ========================================================================
    
    async def add_position(self, position: Position) -> None:
        """
        Add a new position (converts to FundingArbPosition if needed).
        
        🔒 CRITICAL FIX: Added duplicate detection
        
        This method is called by create_position(), so we need to avoid
        infinite recursion by directly adding to memory cache instead of
        calling create_position() again.
        """
        # Check if position already exists in memory
        if position.id in self._positions:
            self.logger.warning(
                f"⚠️ Position {position.id} already exists in memory, skipping add"
            )
            return
        
        if isinstance(position, FundingArbPosition):
            # Directly add to memory cache (don't call create_position to avoid recursion)
            self._positions[position.id] = position
            
            # Initialize funding tracking if not already present
            if position.id not in self._funding_payments:
                self._funding_payments[position.id] = []
            if position.id not in self._cumulative_funding:
                self._cumulative_funding[position.id] = Decimal("0")
        else:
            # Convert generic Position to FundingArbPosition
            funding_position = FundingArbPosition(
                id=position.id,
                symbol=position.symbol,
                long_dex=position.long_dex or "unknown",
                short_dex=position.short_dex or "unknown", 
                size_usd=position.size_usd,
                entry_long_rate=position.entry_long_rate or Decimal('0'),
                entry_short_rate=position.entry_short_rate or Decimal('0'),
                entry_divergence=abs((position.entry_short_rate or Decimal('0')) - (position.entry_long_rate or Decimal('0'))),
                opened_at=position.opened_at or datetime.now(),
                status=position.status
            )
            
            # Add to memory cache
            self._positions[funding_position.id] = funding_position
            
            # Initialize funding tracking
            if funding_position.id not in self._funding_payments:
                self._funding_payments[funding_position.id] = []
            if funding_position.id not in self._cumulative_funding:
                self._cumulative_funding[funding_position.id] = Decimal("0")
    
    async def get_position(self, position_id: UUID) -> Optional[Position]:
        """Get position by ID (returns as generic Position)."""
        funding_position = await self.get_funding_position(position_id)
        if not funding_position:
            return None
        
        # Convert FundingArbPosition to generic Position
        return Position(
            id=funding_position.id,
            symbol=funding_position.symbol,
            size_usd=funding_position.size_usd,
            entry_price=None,  # Not used in funding arb
            long_dex=funding_position.long_dex,
            short_dex=funding_position.short_dex,
            entry_long_rate=funding_position.entry_long_rate,
            entry_short_rate=funding_position.entry_short_rate,
            opened_at=funding_position.opened_at,
            status=funding_position.status
        )
    
    async def get_open_positions(self) -> List[Position]:
        """Get all open positions (returns as generic Positions)."""
        # Get open positions from memory (loaded from DB during initialization)
        open_funding_positions = [p for p in self._positions.values() if p.status == "open"]
        
        # Convert to generic Position objects
        return [
            Position(
                id=p.id,
                symbol=p.symbol,
                size_usd=p.size_usd,
                entry_price=None,
                long_dex=p.long_dex,
                short_dex=p.short_dex,
                entry_long_rate=p.entry_long_rate,
                entry_short_rate=p.entry_short_rate,
                opened_at=p.opened_at,
                status=p.status
            )
            for p in open_funding_positions
        ]
    
    async def update_position(self, position: Position) -> None:
        """Update existing position."""
        # Convert to FundingArbPosition and update
        if isinstance(position, FundingArbPosition):
            await self.update_funding_position(position)
        else:
            # Get existing funding position and update it
            existing = await self.get_funding_position(position.id)
            if existing:
                existing.status = position.status
                existing.size_usd = position.size_usd
                # Update other fields as needed
                await self.update_funding_position(existing)
    
    async def get_position_summary(
        self,
        position_id: UUID,
        current_market_data: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Get position summary with current P&L."""
        funding_position = await self.get_funding_position(position_id)
        if not funding_position:
            return {}
        
        return {
            'position_id': str(position_id),
            'symbol': funding_position.symbol,
            'size_usd': float(funding_position.size_usd),
            'status': funding_position.status,
            'net_pnl_usd': float(funding_position.get_net_pnl()),
            'net_pnl_pct': float(funding_position.get_net_pnl_pct()),
            'cumulative_funding': float(funding_position.cumulative_funding),
            'age_hours': funding_position.get_age_hours(),
            'long_dex': funding_position.long_dex,
            'short_dex': funding_position.short_dex
        }
    
    async def close(self):
        """Close database connection and cleanup resources."""
        if database.is_connected:
            await database.disconnect()
            self.logger.info("Database connection closed")

