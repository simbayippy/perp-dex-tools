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

from typing import Dict, List, Optional
from decimal import Decimal
from datetime import datetime
from uuid import UUID
import logging

from strategies.components.base_components import BasePositionManager, Position
from .models import FundingArbPosition

# Import database connection from funding_rate_service
from funding_rate_service.database.connection import database
from funding_rate_service.core.mappers import dex_mapper, symbol_mapper


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
        self._funding_payments: Dict[UUID, List[Dict]] = {}  # {position_id: [payment_records]}
        self._cumulative_funding: Dict[UUID, Decimal] = {}   # {position_id: total_funding}
        
        self.logger = logging.getLogger(__name__)
        self._initialized = False
    
    async def initialize(self):
        """
        Initialize manager and load positions from database.
        
        Called once on strategy startup to restore state.
        """
        if self._initialized:
            return
        
        # Load open positions from database
        await self._load_positions_from_db()
        
        self._initialized = True
        self.logger.info(
            f"Position manager initialized with {len(await self.get_open_positions())} open positions"
        )
    
    async def _load_positions_from_db(self):
        """Load all open positions from database into memory cache."""
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
    
    async def create_position(
        self,
        position: FundingArbPosition
    ) -> UUID:
        """
        Create new funding arbitrage position.
        
        Stores in both database and memory cache.
        
        Args:
            position: FundingArbPosition to track
        
        Returns:
            Position ID
        """
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
        
        # Store in memory cache (base manager)
        await self.add_position(position)
        
        # Initialize funding tracking
        self._funding_payments[position.id] = []
        self._cumulative_funding[position.id] = Decimal("0")
        
        self.logger.info(
            f"Created position {position.id}: {position.symbol} "
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
        
        Args:
            position_id: Position to close
            exit_reason: Reason for exit
            final_pnl_usd: Final realized PnL (if known)
        """
        position = await self.get_position(position_id)
        if not isinstance(position, FundingArbPosition):
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
            WHERE id = :position_id
        """
        
        await database.execute(query, values={
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
        
        await self.update_position(position)
        
        # Log closure
        self.logger.info(
            f"Closed position {position_id}: {position.symbol} "
            f"Reason: {exit_reason}, PnL: ${final_pnl_usd:.2f}, "
            f"Duration: {(closed_at - position.opened_at).total_seconds() / 3600:.1f}h"
        )
        
        # Keep tracking data in database for analysis
        # Memory can be cleaned up if needed
        # del self._funding_payments[position_id]
        # del self._cumulative_funding[position_id]
    
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

