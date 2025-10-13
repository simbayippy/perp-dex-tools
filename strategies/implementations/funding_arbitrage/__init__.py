"""
Funding Arbitrage Strategy Implementation

Delta-neutral funding rate arbitrage across multiple DEXes.

Pattern: Stateful strategy with multi-DEX support

⭐ Uses direct internal calls to funding_rate_service (no HTTP) ⭐

Components:
- Core strategy orchestrator
- Funding rate analyzer
- Risk management system (pluggable)
- Position models
"""

from .config import FundingArbConfig, RiskManagementConfig
from .models import FundingArbPosition, OpportunityData, TransferOperation
from .position_manager import FundingArbPositionManager
from .state_manager import FundingArbStateManager
from .strategy import FundingArbitrageStrategy

# Risk management (factory pattern)
from .risk_management import (
    get_risk_manager,
    BaseRiskManager,
    ProfitErosionRiskManager,
    DivergenceFlipRiskManager,
    CombinedRiskManager
)

__all__ = [
    # Main strategy
    'FundingArbitrageStrategy',
    
    # Configuration
    'FundingArbConfig',
    'RiskManagementConfig',
    
    # Models
    'FundingArbPosition',
    'OpportunityData',
    'TransferOperation',
    
    # Core components
    'FundingArbPositionManager',
    'FundingArbStateManager',
    
    # Risk management
    'get_risk_manager',
    'BaseRiskManager',
    'ProfitErosionRiskManager',
    'DivergenceFlipRiskManager',
    'CombinedRiskManager',
]

