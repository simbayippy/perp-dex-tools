"""
Profit Erosion Risk Management Strategy.

Exit when divergence drops below a threshold percentage of entry divergence.

Use Case:
- Preserve profits before they fully evaporate
- Exit when funding rate spread narrows significantly
- Configurable erosion threshold (e.g. exit at 50% erosion)

Example:
    Entry divergence: 20% APY
    Current divergence: 8% APY
    Erosion: 8/20 = 0.4 (60% eroded)
    → If threshold is 0.5, EXIT
"""

from typing import Tuple, List, Dict, Any
from decimal import Decimal
from .base import BaseRiskManager
from ..models import FundingArbPosition, RebalanceAction


class ProfitErosionRiskManager(BaseRiskManager):
    """
    Exit before all profit disappears.
    
    Configuration:
        min_erosion_ratio: float (0-1)
            - Exit when current_divergence/entry_divergence < this value
            - Default: 0.5 (exit when 50% of edge is lost)
            - Lower = more aggressive exit (preserve more profit)
            - Higher = more patient (tolerate more erosion)
    
    Example:
        config = {
            'min_erosion_ratio': 0.5  # Exit when edge drops below 50%
        }
        
        Entry: 20% APY divergence
        Current: 9% APY divergence
        Erosion ratio: 9/20 = 0.45 < 0.5 → EXIT
    """
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        
        # Default: Exit when 50% of edge is lost
        self.min_erosion_ratio = config.get('min_erosion_ratio', 0.5)
        
        # Validate config
        if not 0 < self.min_erosion_ratio <= 1:
            raise ValueError(
                f"min_erosion_ratio must be between 0 and 1, got {self.min_erosion_ratio}"
            )
    
    def should_exit(
        self,
        position: FundingArbPosition,
        current_rates: Dict[str, Decimal]
    ) -> Tuple[bool, str]:
        """
        Check if profit has eroded below threshold.
        
        Formula:
            erosion_ratio = current_divergence / entry_divergence
            should_exit = erosion_ratio < min_erosion_ratio
        
        Args:
            position: Current position
            current_rates: Latest funding rates
        
        Returns:
            (True, "PROFIT_EROSION") if should exit, else (False, None)
        """
        # Avoid division by zero
        if position.entry_divergence == 0:
            return False, None
        
        # Calculate erosion ratio
        current_divergence = current_rates['divergence']
        erosion_ratio = float(current_divergence / position.entry_divergence)
        
        # Check threshold
        if erosion_ratio < self.min_erosion_ratio:
            return True, "PROFIT_EROSION"
        
        return False, None
    
    def generate_actions(
        self,
        position: FundingArbPosition,
        reason: str
    ) -> List[RebalanceAction]:
        """
        Generate close position action.
        
        Args:
            position: Position to close
            reason: Should be "PROFIT_EROSION"
        
        Returns:
            List with single close_position action
        """
        return [
            RebalanceAction(
                action_type="close_position",
                position_id=position.id,
                reason=reason,
                details={
                    'erosion_ratio': float(
                        position.current_divergence / position.entry_divergence
                        if position.entry_divergence else 0
                    ),
                    'threshold': self.min_erosion_ratio
                }
            )
        ]

