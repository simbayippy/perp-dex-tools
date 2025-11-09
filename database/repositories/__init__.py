"""
Database repositories
"""

from database.repositories.dashboard_repository import DashboardRepository
from database.repositories.dex_repository import DEXRepository
from database.repositories.funding_rate_repository import FundingRateRepository
from database.repositories.opportunity_repository import OpportunityRepository
from database.repositories.symbol_repository import SymbolRepository
from database.repositories.user_repository import UserRepository
from database.repositories.api_key_repository import APIKeyRepository

__all__ = [
    "DashboardRepository",
    "DEXRepository",
    "SymbolRepository",
    "FundingRateRepository",
    "OpportunityRepository",
    "UserRepository",
    "APIKeyRepository",
]
