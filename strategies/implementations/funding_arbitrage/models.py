"""
Data Models for Funding Arbitrage Strategy

Inspired by Hummingbot's PositionHold pattern.
Tracks delta-neutral positions across multiple DEXes.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from datetime import datetime
from uuid import UUID, uuid4
from typing import Optional, Dict, Any, List


# ============================================================================
# Funding Arbitrage Position
# ============================================================================

@dataclass
class FundingArbPosition:
    """
    Delta-neutral position pair across two DEXes.
    
    Pattern inspired by Hummingbot's PositionHold.
    
    Represents:
    - Long position on one DEX (pay funding)
    - Short position on another DEX (receive funding)
    - Net exposure should be ~0 (delta-neutral)
    """
    id: UUID
    symbol: str
    
    # Position composition
    long_dex: str
    short_dex: str
    size_usd: Decimal
    
    # Entry data
    entry_long_rate: Decimal
    entry_short_rate: Decimal
    entry_divergence: Decimal  # short_rate - long_rate
    opened_at: datetime
    
    # Current data (updated during monitoring)
    current_divergence: Optional[Decimal] = None
    last_check: Optional[datetime] = None
    
    # Funding tracking (critical for profitability)
    cumulative_funding: Decimal = Decimal("0")
    total_fees_paid: Decimal = Decimal("0")
    
    # Status
    status: str = "open"  # 'open', 'pending_close', 'closed'
    exit_reason: Optional[str] = None
    closed_at: Optional[datetime] = None
    
    # Metadata
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        if not isinstance(self.id, UUID):
            self.id = uuid4()
    
    # ========================================================================
    # Utility Methods
    # ========================================================================
    
    def get_age_hours(self) -> float:
        """
        Get position age in hours.
        
        Returns:
            Hours since position opened
        """
        return (datetime.now() - self.opened_at).total_seconds() / 3600
    
    def get_profit_erosion(self) -> Decimal:
        """
        Calculate how much profit has eroded.
        
        Returns:
            Ratio of current to entry divergence (1.0 = no erosion, 0.5 = 50% erosion)
        """
        if not self.current_divergence or self.entry_divergence == 0:
            return Decimal("1.0")
        
        return self.current_divergence / self.entry_divergence
    
    def get_net_pnl(self) -> Decimal:
        """
        Calculate net PnL.
        
        For funding arb:
        - Price PnL should be ~0 (delta-neutral)
        - Real profit comes from funding payments
        - Must subtract fees
        
        Returns:
            Net PnL in USD
        """
        return self.cumulative_funding - self.total_fees_paid
    
    def get_net_pnl_pct(self) -> Decimal:
        """
        Calculate net PnL as percentage.
        
        Returns:
            PnL percentage (e.g., 0.01 = 1%)
        """
        if self.size_usd == 0:
            return Decimal("0")
        
        return self.get_net_pnl() / self.size_usd
    
    def is_profitable(self) -> bool:
        """
        Check if position is currently profitable.
        
        Returns:
            True if net PnL > 0
        """
        return self.get_net_pnl() > 0
    
    def record_funding_payment(self, amount: Decimal):
        """
        Record a funding payment.
        
        Called when funding payment event received.
        Amount can be positive (received) or negative (paid).
        
        Args:
            amount: Funding payment amount in USD
        """
        self.cumulative_funding += amount
    
    def record_fee(self, fee_amount: Decimal):
        """
        Record trading fee.
        
        Args:
            fee_amount: Fee amount in USD
        """
        self.total_fees_paid += fee_amount
    
    def to_dict(self) -> Dict[str, Any]:
        """
        Convert to dictionary for serialization.
        
        Returns:
            Dict representation
        """
        return {
            'id': str(self.id),
            'symbol': self.symbol,
            'long_dex': self.long_dex,
            'short_dex': self.short_dex,
            'size_usd': float(self.size_usd),
            'entry_long_rate': float(self.entry_long_rate),
            'entry_short_rate': float(self.entry_short_rate),
            'entry_divergence': float(self.entry_divergence),
            'opened_at': self.opened_at.isoformat(),
            'current_divergence': float(self.current_divergence) if self.current_divergence else None,
            'cumulative_funding': float(self.cumulative_funding),
            'total_fees_paid': float(self.total_fees_paid),
            'net_pnl': float(self.get_net_pnl()),
            'net_pnl_pct': float(self.get_net_pnl_pct()),
            'status': self.status,
            'exit_reason': self.exit_reason,
            'age_hours': self.get_age_hours(),
            'metadata': self.metadata
        }


# ============================================================================
# Transfer Operation (for fund rebalancing)
# ============================================================================

@dataclass
class TransferOperation:
    """
    Cross-DEX fund transfer operation.
    
    Tracks multi-step transfer process:
    1. Withdraw from source DEX
    2. Bridge between chains (if needed)
    3. Deposit to destination DEX
    """
    id: UUID
    position_id: Optional[UUID]
    from_dex: str
    to_dex: str
    amount_usd: Decimal
    reason: str  # 'rebalance', 'initial_capital', etc.
    
    # Status tracking
    status: str  # 'pending', 'withdrawing', 'bridging', 'depositing', 'completed', 'failed'
    withdrawal_tx: Optional[str] = None
    bridge_tx: Optional[str] = None
    deposit_tx: Optional[str] = None
    
    # Error handling
    retry_count: int = 0
    error_message: Optional[str] = None
    
    # Timestamps
    created_at: datetime = field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None
    
    def __post_init__(self):
        if not isinstance(self.id, UUID):
            self.id = uuid4()


# ============================================================================
# Opportunity Data (from funding service)
# ============================================================================

@dataclass
class OpportunityData:
    """
    Funding arbitrage opportunity from funding rate service.
    
    Represents a profitable funding rate difference between two DEXes.
    """
    symbol: str
    long_dex: str  # DEX with low rate (pay funding)
    short_dex: str  # DEX with high rate (receive funding)
    
    # Rates
    divergence: Decimal  # Rate difference
    long_rate: Decimal
    short_rate: Decimal
    
    # Profitability
    net_profit_apy: Decimal  # After fees
    
    # Open Interest (for filtering)
    long_oi_usd: Optional[Decimal] = None
    short_oi_usd: Optional[Decimal] = None
    
    # Recommended size
    recommended_size_usd: Optional[Decimal] = None
    
    # Metadata
    timestamp: datetime = field(default_factory=datetime.now)
    
    def is_valid(self, min_profit: Decimal, max_oi: Optional[Decimal] = None) -> bool:
        """
        Check if opportunity meets criteria.
        
        Args:
            min_profit: Minimum profit threshold
            max_oi: Maximum OI filter (for point farming)
            
        Returns:
            True if opportunity is valid
        """
        # Check profit threshold
        if self.net_profit_apy < min_profit:
            return False
        
        # Check OI limits (for point farming on low-OI DEXes)
        if max_oi is not None:
            if self.long_oi_usd and self.long_oi_usd > max_oi:
                return False
            if self.short_oi_usd and self.short_oi_usd > max_oi:
                return False
        
        return True

