"""
Data models for Funding Rate Service
"""

from funding_rate_service.models.dex import (
    DEXFeeStructure,
    DEXMetadata,
    DEXHealth,
    FeeType,
)
from funding_rate_service.models.symbol import (
    Symbol,
    DEXSymbol,
)
from funding_rate_service.models.funding_rate import (
    FundingRate,
    FundingRateResponse,
    LatestFundingRates,
    AllLatestFundingRates,
)
from funding_rate_service.models.opportunity import (
    ArbitrageOpportunity,
    OpportunityResponse,
)
from funding_rate_service.models.filters import (
    OpportunityFilter,
)
from funding_rate_service.models.system import (
    ServiceHealth,
    CollectionLog,
    CollectionStatus,
)
from funding_rate_service.models.history import (
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

