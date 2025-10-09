"""
Execution Tracker - Records and analyzes execution quality.

⭐ Inspired by Hummingbot's ExecutorInfo and performance tracking ⭐

Tracks all order executions for:
- Quality analysis (slippage, fill rate, etc.)
- Performance optimization
- Debugging and incident review
- Strategy backtesting data

Key features:
- Execution recording with full context
- Aggregated quality metrics
- Time-series analysis
- Export to database for persistence
"""

from typing import Dict, List, Optional
from decimal import Decimal
from datetime import datetime
from dataclasses import dataclass, asdict
from uuid import UUID, uuid4
import logging

logger = logging.getLogger(__name__)


@dataclass
class ExecutionRecord:
    """
    Complete record of an order execution.
    
    ⭐ Similar to Hummingbot's ExecutorInfo ⭐
    
    Contains all details needed for quality analysis and debugging.
    """
    execution_id: UUID
    strategy_name: str
    
    # Order details
    symbol: str
    side: str
    size_usd: Decimal
    execution_mode: str
    
    # Timing
    started_at: datetime
    completed_at: Optional[datetime]
    execution_time_ms: int
    
    # Results
    success: bool
    filled: bool
    fill_price: Optional[Decimal] = None
    filled_quantity: Optional[Decimal] = None
    
    # Quality metrics
    expected_price: Optional[Decimal] = None
    slippage_usd: Decimal = Decimal('0')
    slippage_pct: Decimal = Decimal('0')
    fees_usd: Optional[Decimal] = None
    
    # Events/errors
    events: List[str] = None
    error_message: Optional[str] = None
    
    def __post_init__(self):
        """Initialize events list if None."""
        if self.events is None:
            self.events = []


class ExecutionTracker:
    """
    Tracks all order executions for analytics.
    
    ⭐ Inspired by Hummingbot's performance tracking ⭐
    
    Example:
        tracker = ExecutionTracker()
        
        # Record execution
        await tracker.record_execution(ExecutionRecord(
            execution_id=uuid4(),
            strategy_name="funding_arb",
            symbol="BTC-PERP",
            side="buy",
            size_usd=Decimal("1000"),
            ...
        ))
        
        # Get stats
        stats = tracker.get_execution_stats("funding_arb", time_window_hours=24)
        print(f"Success rate: {stats['success_rate']*100:.1f}%")
        print(f"Avg slippage: {stats['avg_slippage_pct']*100:.3f}%")
    """
    
    def __init__(self):
        """Initialize execution tracker."""
        self.executions: Dict[UUID, ExecutionRecord] = {}
        self.logger = logging.getLogger(__name__)
    
    async def record_execution(self, record: ExecutionRecord):
        """
        Save execution record.
        
        Args:
            record: ExecutionRecord to save
        """
        self.executions[record.execution_id] = record
        
        self.logger.info(
            f"Recorded execution {record.execution_id}: "
            f"{record.side} {record.symbol} {'✅' if record.filled else '❌'}"
        )
        
        # TODO: Persist to database
        # await database.save_execution_record(record)
    
    def get_execution_stats(
        self,
        strategy_name: Optional[str] = None,
        time_window_hours: int = 24
    ) -> Dict:
        """
        Get execution quality metrics.
        
        Args:
            strategy_name: Filter by strategy (None = all strategies)
            time_window_hours: Time window for analysis (hours)
        
        Returns:
            {
                'total_executions': int,
                'successful_executions': int,
                'filled_executions': int,
                'success_rate': float,
                'fill_rate': float,
                'avg_slippage_pct': Decimal,
                'avg_slippage_usd': Decimal,
                'avg_execution_time_ms': int,
                'total_fees_usd': Decimal
            }
        """
        # Filter executions
        cutoff_time = datetime.now().timestamp() - (time_window_hours * 3600)
        
        filtered = [
            record for record in self.executions.values()
            if (not strategy_name or record.strategy_name == strategy_name)
            and record.started_at.timestamp() >= cutoff_time
        ]
        
        if not filtered:
            return {
                'total_executions': 0,
                'successful_executions': 0,
                'filled_executions': 0,
                'success_rate': 0.0,
                'fill_rate': 0.0,
                'avg_slippage_pct': Decimal('0'),
                'avg_slippage_usd': Decimal('0'),
                'avg_execution_time_ms': 0,
                'total_fees_usd': Decimal('0')
            }
        
        # Calculate metrics
        total = len(filtered)
        successful = sum(1 for r in filtered if r.success)
        filled = sum(1 for r in filtered if r.filled)
        
        success_rate = successful / total
        fill_rate = filled / total
        
        # Slippage stats (only for filled orders)
        filled_records = [r for r in filtered if r.filled]
        if filled_records:
            avg_slippage_pct = sum(r.slippage_pct for r in filled_records) / len(filled_records)
            avg_slippage_usd = sum(r.slippage_usd for r in filled_records) / len(filled_records)
        else:
            avg_slippage_pct = Decimal('0')
            avg_slippage_usd = Decimal('0')
        
        # Execution time
        avg_execution_time_ms = sum(r.execution_time_ms for r in filtered) // total
        
        # Total fees
        total_fees_usd = sum(
            r.fees_usd for r in filtered if r.fees_usd is not None
        )
        
        return {
            'total_executions': total,
            'successful_executions': successful,
            'filled_executions': filled,
            'success_rate': success_rate,
            'fill_rate': fill_rate,
            'avg_slippage_pct': avg_slippage_pct,
            'avg_slippage_usd': avg_slippage_usd,
            'avg_execution_time_ms': avg_execution_time_ms,
            'total_fees_usd': total_fees_usd
        }
    
    def get_recent_executions(
        self,
        limit: int = 10,
        strategy_name: Optional[str] = None
    ) -> List[ExecutionRecord]:
        """
        Get most recent executions.
        
        Args:
            limit: Maximum number to return
            strategy_name: Filter by strategy (None = all)
        
        Returns:
            List of ExecutionRecord, sorted by timestamp (newest first)
        """
        filtered = [
            record for record in self.executions.values()
            if not strategy_name or record.strategy_name == strategy_name
        ]
        
        # Sort by started_at descending
        sorted_records = sorted(
            filtered,
            key=lambda r: r.started_at,
            reverse=True
        )
        
        return sorted_records[:limit]
    
    def clear_old_records(self, hours: int = 168):  # 7 days default
        """
        Clear records older than specified hours.
        
        Args:
            hours: Keep records newer than this (default 168 = 7 days)
        """
        cutoff_time = datetime.now().timestamp() - (hours * 3600)
        
        before_count = len(self.executions)
        
        self.executions = {
            exec_id: record
            for exec_id, record in self.executions.items()
            if record.started_at.timestamp() >= cutoff_time
        }
        
        removed = before_count - len(self.executions)
        
        if removed > 0:
            self.logger.info(f"Cleared {removed} old execution records")

