"""
Combined Risk Management Strategy.

Multi-rule risk management with priority ordering.

Combines multiple risk checks in a waterfall approach:
1. Critical: Divergence flip (immediate exit)
2. High: Severe profit erosion (exit ASAP)
3. Medium: Normal profit erosion (gradual exit)
4. Low: Time-based exit (max holding period)

Use Case:
- Production-ready multi-layered risk management
- Handles all edge cases with priority system
- Configurable thresholds for each layer
"""

from typing import Tuple, List, Dict, Any
from decimal import Decimal
from datetime import datetime
from .base import BaseRiskManager
from .profit_erosion import ProfitErosionRiskManager
from .divergence_flip import DivergenceFlipRiskManager
from ..models import FundingArbPosition, RebalanceAction


class CombinedRiskManager(BaseRiskManager):
    """
    Multi-rule risk management with priority-based exit logic.
    
    ⭐ Recommended for production use ⭐
    
    Priority Levels:
    
    1. **CRITICAL** - Divergence Flip
       - Immediate exit when divergence goes negative
       - Prevents active losses
    
    2. **HIGH** - Severe Erosion
       - Exit when 80%+ of edge is lost
       - Preserve remaining profits
    
    3. **MEDIUM** - Normal Erosion
       - Exit based on configurable threshold
       - Balance profit preservation vs patience
    
    4. **LOW** - Time Limit
       - Max holding period (default: 168 hours = 1 week)
       - Prevents stuck positions
    
    Configuration:
        min_erosion_ratio: float (0-1)
            - Normal erosion threshold
            - Default: 0.5 (exit at 50% erosion)
        
        severe_erosion_ratio: float (0-1)
            - Severe erosion threshold
            - Default: 0.2 (exit at 80% erosion)
        
        max_position_age_hours: float
            - Max time to hold position
            - Default: 168 (1 week)
        
        flip_margin: Decimal
            - Buffer for divergence flip detection
            - Default: 0
    
    Example:
        config = {
            'min_erosion_ratio': 0.5,        # Exit at 50% erosion
            'severe_erosion_ratio': 0.2,     # Exit at 80% erosion
            'max_position_age_hours': 168,   # 1 week max
            'flip_margin': 0                 # No margin on flip
        }
    """
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        
        # Initialize sub-strategies
        self.flip_checker = DivergenceFlipRiskManager(config)
        self.erosion_checker = ProfitErosionRiskManager(config)
        
        # Thresholds
        self.severe_erosion_ratio = config.get('severe_erosion_ratio', 0.2)
        self.max_position_age_hours = config.get('max_position_age_hours', 168)  # 1 week
        
        # Validate
        if not 0 < self.severe_erosion_ratio < 1:
            raise ValueError(
                f"severe_erosion_ratio must be between 0 and 1, got {self.severe_erosion_ratio}"
            )
    
    def should_exit(
        self,
        position: FundingArbPosition,
        current_rates: Dict[str, Decimal]
    ) -> Tuple[bool, str]:
        """
        Check all risk rules in priority order.
        
        Returns on first triggered rule (waterfall logic).
        
        Args:
            position: Current position
            current_rates: Latest funding rates
        
        Returns:
            Tuple of (should_exit, reason_code)
            
            Reason codes (in priority order):
            - "DIVERGENCE_FLIPPED" - Critical
            - "SEVERE_EROSION" - High priority
            - "PROFIT_EROSION" - Medium priority
            - "TIME_LIMIT" - Low priority
        """
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # Priority 1: CRITICAL - Divergence Flip
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        should_exit, reason = self.flip_checker.should_exit(position, current_rates)
        if should_exit:
            return True, "DIVERGENCE_FLIPPED"
        
        # Calculate erosion ratio (used in multiple checks)
        if position.entry_divergence > 0:
            erosion_ratio = float(
                current_rates['divergence'] / position.entry_divergence
            )
        else:
            erosion_ratio = 0.0
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # Priority 2: HIGH - Severe Erosion
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # Lost 80%+ of edge → exit immediately
        if erosion_ratio < self.severe_erosion_ratio:
            return True, "SEVERE_EROSION"
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # Priority 3: MEDIUM - Normal Erosion
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        should_exit, reason = self.erosion_checker.should_exit(position, current_rates)
        if should_exit:
            return True, "PROFIT_EROSION"
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # Priority 4: LOW - Time Limit
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # Fallback: Close after max holding period
        age_hours = (datetime.now() - position.opened_at).total_seconds() / 3600
        if age_hours >= self.max_position_age_hours:
            return True, "TIME_LIMIT"
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # No exit conditions triggered
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        return False, None
    
    def generate_actions(
        self,
        position: FundingArbPosition,
        reason: str
    ) -> List[RebalanceAction]:
        """
        Generate actions based on exit reason.
        
        Different reasons may have different urgency levels.
        
        Args:
            position: Position to close
            reason: Exit reason from should_exit()
        
        Returns:
            List of actions with appropriate urgency flags
        """
        # Determine urgency
        urgent_reasons = ['DIVERGENCE_FLIPPED', 'SEVERE_EROSION']
        is_urgent = reason in urgent_reasons
        
        # Get position metrics for logging
        metrics = self.get_position_metrics(
            position,
            {'divergence': position.current_divergence or position.entry_divergence}
        )
        
        return [
            RebalanceAction(
                action_type="close_position",
                position_id=position.id,
                reason=reason,
                details={
                    'urgent': is_urgent,
                    'erosion_ratio': metrics['profit_erosion'],
                    'age_hours': metrics['age_hours'],
                    'estimated_pnl_usd': metrics['estimated_pnl_usd']
                }
            )
        ]

