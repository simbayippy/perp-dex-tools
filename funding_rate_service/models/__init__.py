"""
Data models for Funding Rate Service
"""

from models.dex import (
    DEXFeeStructure,
    DEXMetadata,
    DEXHealth,
    FeeType,
)
from models.symbol import (
    Symbol,
    DEXSymbol,
)
from models.funding_rate import (
    FundingRate,
    FundingRateResponse,
    LatestFundingRates,
    AllLatestFundingRates,
)
from models.opportunity import (
    ArbitrageOpportunity,
    OpportunityResponse,
)
from models.filters import (
    OpportunityFilter,
)
from models.system import (
    ServiceHealth,
    CollectionLog,
    CollectionStatus,
)
from models.history import (
    FundingRateHistory,
    FundingRateStats,
)

__all__ = [
    # DEX models
    "DEXFeeStructure",
    "DEXMetadata",
    "DEXHealth",
    "FeeType",
    # Symbol models
    "Symbol",
    "DEXSymbol",
    # Funding rate models
    "FundingRate",
    "FundingRateResponse",
    "LatestFundingRates",
    "AllLatestFundingRates",
    # Opportunity models
    "ArbitrageOpportunity",
    "OpportunityResponse",
    # Filter models
    "OpportunityFilter",
    # System models
    "ServiceHealth",
    "CollectionLog",
    "CollectionStatus",
    # History models
    "FundingRateHistory",
    "FundingRateStats",
]

