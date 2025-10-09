"""
PATTERN 2: Position Aggregation (PositionHold)
==============================================

Extracted from: Hummingbot ExecutorOrchestrator
Source: docs/hummingbot_reference/position_executor/NOTES.md

Purpose:
--------
Track multi-DEX positions as a single logical unit with aggregated metrics.

Critical for funding arbitrage:
- Long on DEX A + Short on DEX B = One logical position
- Aggregate PnL from both sides
- Calculate net exposure (should be ~0 for delta-neutral)
- Track cumulative funding payments

Why This Pattern?
-----------------
✅ Simplifies multi-DEX position tracking
✅ Single source of truth for position state
✅ Easy to calculate net PnL
✅ Matches your mental model: "one funding arb opportunity = one position"

Key Concepts:
-------------
1. PositionHold represents a logical position across multiple DEXes
2. TrackedOrders track individual orders on each DEX
3. get_position_summary() aggregates metrics from all sides

"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import List, Dict, Optional
from uuid import UUID
from datetime import datetime


# ============================================================================
# CORE PATTERN: Position Aggregation
# ============================================================================

@dataclass
class TrackedOrder:
    """
    Represents a single order (long or short on one DEX).
    
    Pattern: Minimal order tracking for position aggregation.
    """
    order_id: str
    dex_name: str
    symbol: str
    side: str  # "BUY" or "SELL"
    amount: Decimal
    filled_amount: Decimal
    average_price: Decimal
    status: str  # "open", "filled", "cancelled"
    timestamp: datetime = field(default_factory=datetime.now)
    
    @property
    def is_filled(self) -> bool:
        return self.status == "filled"
    
    @property
    def is_buy(self) -> bool:
        return self.side == "BUY"


@dataclass
class PositionSummary:
    """
    Aggregated metrics for a multi-DEX position.
    
    This is what PositionHold.get_position_summary() returns.
    """
    position_id: UUID
    symbol: str
    
    # Position composition
    long_dex: str
    short_dex: str
    net_amount: Decimal  # Should be ~0 for delta-neutral
    
    # Entry data
    entry_price: Decimal  # Average entry price
    position_size_usd: Decimal
    
    # PnL breakdown
    unrealized_pnl: Decimal  # From price movement (should be small)
    realized_pnl: Decimal  # From closed portions
    cumulative_funding: Decimal  # Funding payments collected
    total_fees_paid: Decimal
    net_pnl: Decimal  # realized_pnl + cumulative_funding - fees
    
    # Metrics
    net_pnl_pct: Decimal  # net_pnl / position_size_usd
    current_leverage: Decimal = Decimal("1.0")


class PositionHold:
    """
    Multi-DEX position tracker - the core pattern from Hummingbot.
    
    Represents a logical position that spans multiple DEXes.
    
    For funding arbitrage:
    - Long side: Orders on DEX with low funding rate
    - Short side: Orders on DEX with high funding rate
    - Net exposure: Should be ~0 (delta-neutral)
    
    Pattern from Hummingbot:
    - Aggregate orders from multiple executors
    - Calculate net position
    - Compute combined PnL
    """
    
    def __init__(
        self,
        position_id: UUID,
        symbol: str,
        long_dex: str,
        short_dex: str
    ):
        self.position_id = position_id
        self.symbol = symbol
        self.long_dex = long_dex
        self.short_dex = short_dex
        
        # Track orders on each side
        self.long_orders: List[TrackedOrder] = []
        self.short_orders: List[TrackedOrder] = []
        
        # Funding tracking
        self.cumulative_funding = Decimal("0")
        self.total_fees_paid = Decimal("0")
        
        # Metadata
        self.opened_at = datetime.now()
        self.status = "open"
    
    # ========================================================================
    # Order Management
    # ========================================================================
    
    def add_long_order(self, order: TrackedOrder):
        """Add order on the long side"""
        assert order.dex_name == self.long_dex, "Order DEX mismatch"
        assert order.is_buy, "Long side must be BUY"
        self.long_orders.append(order)
    
    def add_short_order(self, order: TrackedOrder):
        """Add order on the short side"""
        assert order.dex_name == self.short_dex, "Order DEX mismatch"
        assert not order.is_buy, "Short side must be SELL"
        self.short_orders.append(order)
    
    # ========================================================================
    # Position Aggregation (KEY PATTERN)
    # ========================================================================
    
    def get_position_summary(
        self,
        current_prices: Optional[Dict[str, Decimal]] = None
    ) -> PositionSummary:
        """
        ⭐ CORE PATTERN from Hummingbot ⭐
        
        Aggregate metrics from both sides into single summary.
        
        Args:
            current_prices: {dex_name: current_price} for unrealized PnL calc
        
        Returns:
            PositionSummary with aggregated metrics
        """
        # Calculate long side metrics
        long_amount = sum(o.filled_amount for o in self.long_orders if o.is_filled)
        long_value = sum(
            o.filled_amount * o.average_price 
            for o in self.long_orders if o.is_filled
        )
        long_avg_price = long_value / long_amount if long_amount > 0 else Decimal("0")
        
        # Calculate short side metrics
        short_amount = sum(o.filled_amount for o in self.short_orders if o.is_filled)
        short_value = sum(
            o.filled_amount * o.average_price 
            for o in self.short_orders if o.is_filled
        )
        short_avg_price = short_value / short_amount if short_amount > 0 else Decimal("0")
        
        # Net amount (should be ~0 for delta-neutral)
        net_amount = long_amount - short_amount
        
        # Average entry price (weighted average)
        total_value = long_value + short_value
        total_amount = long_amount + short_amount
        avg_entry_price = total_value / total_amount if total_amount > 0 else Decimal("0")
        
        # Position size in USD
        position_size_usd = long_value  # Or short_value, should be similar
        
        # Unrealized PnL from price movement
        unrealized_pnl = self._calculate_unrealized_pnl(current_prices) if current_prices else Decimal("0")
        
        # Realized PnL (from closed portions)
        realized_pnl = Decimal("0")  # TODO: Track from closed orders
        
        # Net PnL = realized + funding - fees
        net_pnl = realized_pnl + self.cumulative_funding - self.total_fees_paid
        
        # Net PnL percentage
        net_pnl_pct = net_pnl / position_size_usd if position_size_usd > 0 else Decimal("0")
        
        return PositionSummary(
            position_id=self.position_id,
            symbol=self.symbol,
            long_dex=self.long_dex,
            short_dex=self.short_dex,
            net_amount=net_amount,
            entry_price=avg_entry_price,
            position_size_usd=position_size_usd,
            unrealized_pnl=unrealized_pnl,
            realized_pnl=realized_pnl,
            cumulative_funding=self.cumulative_funding,
            total_fees_paid=self.total_fees_paid,
            net_pnl=net_pnl,
            net_pnl_pct=net_pnl_pct
        )
    
    def _calculate_unrealized_pnl(
        self,
        current_prices: Dict[str, Decimal]
    ) -> Decimal:
        """
        Calculate unrealized PnL from price movement.
        
        For delta-neutral, this should be small (ideally 0).
        """
        if not current_prices:
            return Decimal("0")
        
        long_pnl = Decimal("0")
        short_pnl = Decimal("0")
        
        # Long side PnL
        if self.long_dex in current_prices:
            current_price = current_prices[self.long_dex]
            for order in self.long_orders:
                if order.is_filled:
                    pnl = (current_price - order.average_price) * order.filled_amount
                    long_pnl += pnl
        
        # Short side PnL
        if self.short_dex in current_prices:
            current_price = current_prices[self.short_dex]
            for order in self.short_orders:
                if order.is_filled:
                    pnl = (order.average_price - current_price) * order.filled_amount
                    short_pnl += pnl
        
        # Total unrealized PnL
        return long_pnl + short_pnl
    
    # ========================================================================
    # Funding Payment Tracking
    # ========================================================================
    
    def record_funding_payment(self, amount: Decimal):
        """
        Record a funding payment.
        
        Called when funding payment event received.
        
        Amount can be positive (received) or negative (paid).
        """
        self.cumulative_funding += amount
    
    def record_fee(self, fee_amount: Decimal):
        """Record trading fee"""
        self.total_fees_paid += fee_amount


# ============================================================================
# USAGE EXAMPLE
# ============================================================================

def example_funding_arb_position():
    """
    Example: BTC funding arb between Lighter and GRVT
    """
    # Create position
    position = PositionHold(
        position_id=UUID('12345678-1234-5678-1234-567812345678'),
        symbol='BTC',
        long_dex='lighter',   # Pay funding (low rate)
        short_dex='grvt'      # Receive funding (high rate)
    )
    
    # Add long order on Lighter
    long_order = TrackedOrder(
        order_id='lighter_123',
        dex_name='lighter',
        symbol='BTC',
        side='BUY',
        amount=Decimal('1.0'),
        filled_amount=Decimal('1.0'),
        average_price=Decimal('50000'),
        status='filled'
    )
    position.add_long_order(long_order)
    position.record_fee(Decimal('10'))  # $10 fee
    
    # Add short order on GRVT
    short_order = TrackedOrder(
        order_id='grvt_456',
        dex_name='grvt',
        symbol='BTC',
        side='SELL',
        amount=Decimal('1.0'),
        filled_amount=Decimal('1.0'),
        average_price=Decimal('50010'),  # Slightly higher
        status='filled'
    )
    position.add_short_order(short_order)
    position.record_fee(Decimal('10'))  # $10 fee
    
    # Simulate funding payments over time
    position.record_funding_payment(Decimal('5'))   # Hour 1: +$5
    position.record_funding_payment(Decimal('5'))   # Hour 2: +$5
    position.record_funding_payment(Decimal('5'))   # Hour 3: +$5
    
    # Get aggregated summary
    current_prices = {
        'lighter': Decimal('50100'),
        'grvt': Decimal('50100')
    }
    summary = position.get_position_summary(current_prices)
    
    print(f"Position Summary:")
    print(f"  Symbol: {summary.symbol}")
    print(f"  Long: {summary.long_dex}, Short: {summary.short_dex}")
    print(f"  Net Amount: {summary.net_amount} (delta-neutral: ~0)")
    print(f"  Entry Price: ${summary.entry_price}")
    print(f"  Position Size: ${summary.position_size_usd}")
    print(f"  Unrealized PnL: ${summary.unrealized_pnl}")
    print(f"  Cumulative Funding: ${summary.cumulative_funding}")
    print(f"  Total Fees: ${summary.total_fees_paid}")
    print(f"  Net PnL: ${summary.net_pnl} ({summary.net_pnl_pct * 100:.2f}%)")


# ============================================================================
# HOW TO INTEGRATE INTO YOUR CODE
# ============================================================================

"""
Integration with your position_manager.py:
------------------------------------------

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

@dataclass
class FundingArbPosition:
    '''Your existing position model'''
    id: UUID
    symbol: str
    long_dex: str
    short_dex: str
    size_usd: Decimal
    
    # ADD THESE for tracking
    long_orders: List[TrackedOrder] = field(default_factory=list)
    short_orders: List[TrackedOrder] = field(default_factory=list)
    cumulative_funding: Decimal = Decimal("0")
    total_fees_paid: Decimal = Decimal("0")
    
    def get_position_summary(self, current_prices: Dict[str, Decimal]):
        '''Use the PositionHold aggregation pattern'''
        # Aggregate long side
        long_amount = sum(o.filled_amount for o in self.long_orders)
        
        # Aggregate short side
        short_amount = sum(o.filled_amount for o in self.short_orders)
        
        # Calculate net PnL
        net_pnl = self.cumulative_funding - self.total_fees_paid
        
        return {
            'net_amount': long_amount - short_amount,
            'cumulative_funding': self.cumulative_funding,
            'net_pnl': net_pnl,
            'net_pnl_pct': net_pnl / self.size_usd
        }

"""

# ============================================================================
# KEY TAKEAWAYS
# ============================================================================

"""
1. ✅ Track long + short as single logical position
2. ✅ Aggregate metrics from both sides
3. ✅ Calculate net exposure (should be ~0 for funding arb)
4. ✅ Track cumulative funding separately from price PnL
5. ✅ Single source of truth for position state

Extract for your code:
----------------------
- PositionSummary structure → Use for API responses
- get_position_summary() logic → Add to your FundingArbPosition
- Funding payment tracking → Critical for profitability
- Fee tracking → Separate from funding payments

For your PostgreSQL implementation:
-----------------------------------
- Store position metadata in strategy_positions table
- Store individual orders in separate orders table
- Join for aggregation (or cache in-memory like Hummingbot)
- Update cumulative_funding on each payment event
"""

