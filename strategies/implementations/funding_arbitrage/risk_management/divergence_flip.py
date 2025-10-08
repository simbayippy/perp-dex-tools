"""
Divergence Flip Risk Management Strategy.

Mandatory exit when funding rate divergence becomes negative (losing money).

Use Case:
- Critical protection when rates flip direction
- When short side becomes profitable and long side becomes unprofitable
- Immediate exit required to prevent losses

Example:
    Entry: Long Lighter (paying -5%), Short Paradex (receiving +10%) → +15% divergence ✅
    Flip:  Long Lighter (paying -10%), Short Paradex (receiving +3%) → -7% divergence ❌
    → IMMEDIATE EXIT (now losing money on both sides)
"""

from typing import Tuple, List, Dict, Any
from decimal import Decimal
from .base import BaseRiskManager
from ..models import FundingArbPosition, RebalanceAction


class DivergenceFlipRiskManager(BaseRiskManager):
    """
    Mandatory exit when divergence flips negative.
    
    This is a CRITICAL risk control - when divergence goes negative,
    you're actively losing money instead of earning funding.
    
    Configuration:
        flip_margin: Decimal (optional)
            - Small buffer to account for fee changes
            - Default: 0 (exit on any negative divergence)
            - Example: 0.0001 (0.01% margin before triggering)
    
    Divergence Calculation:
        divergence = long_rate - short_rate
        
        Positive divergence = profitable
            - You're receiving more on short than paying on long
        
        Negative divergence = losing money
            - You're paying more on long than receiving on short
    
    Example:
        Entry state:
            Long: -0.05% (paying 5%)
            Short: +0.10% (receiving 10%)
            Divergence: +0.15% (profitable)
        
        Flipped state:
            Long: -0.10% (paying 10%)
            Short: +0.03% (receiving 3%)
            Divergence: -0.07% (LOSING MONEY)
            → IMMEDIATE EXIT
    """
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        
        # Small margin to account for fee changes
        self.flip_margin = Decimal(str(config.get('flip_margin', 0)))
    
    def should_exit(
        self,
        position: FundingArbPosition,
        current_rates: Dict[str, Decimal]
    ) -> Tuple[bool, str]:
        """
        Check if divergence has flipped negative.
        
        Formula:
            divergence = long_rate - short_rate
            should_exit = divergence < flip_margin
        
        Args:
            position: Current position
            current_rates: Latest funding rates
        
        Returns:
            (True, "DIVERGENCE_FLIPPED") if negative divergence, else (False, None)
        """
        current_divergence = current_rates['divergence']
        
        # Check if divergence is negative (with margin)
        if current_divergence < self.flip_margin:
            return True, "DIVERGENCE_FLIPPED"
        
        return False, None
    
    def generate_actions(
        self,
        position: FundingArbPosition,
        reason: str
    ) -> List[RebalanceAction]:
        """
        Generate URGENT close position action.
        
        Args:
            position: Position to close immediately
            reason: Should be "DIVERGENCE_FLIPPED"
        
        Returns:
            List with single close_position action marked as urgent
        """
        return [
            RebalanceAction(
                action_type="close_position",
                position_id=position.id,
                reason=reason,
                details={
                    'urgent': True,  # Process immediately
                    'current_divergence': float(position.current_divergence or 0),
                    'entry_divergence': float(position.entry_divergence),
                    'flip_margin': float(self.flip_margin)
                }
            )
        ]

