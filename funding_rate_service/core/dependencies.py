"""
Dependency Injection for FastAPI

Manages service instances and provides them to route handlers.
"""

from typing import Optional
from fastapi import HTTPException
from core.opportunity_finder import OpportunityFinder
from core.historical_analyzer import HistoricalAnalyzer


class ServiceContainer:
    """Container for all service instances"""
    
    def __init__(self):
        self.opportunity_finder: Optional[OpportunityFinder] = None
        self.historical_analyzer: Optional[HistoricalAnalyzer] = None
    
    def set_opportunity_finder(self, finder: OpportunityFinder):
        """Set the opportunity finder instance"""
        self.opportunity_finder = finder
    
    def set_historical_analyzer(self, analyzer: HistoricalAnalyzer):
        """Set the historical analyzer instance"""
        self.historical_analyzer = analyzer
    
    def get_opportunity_finder(self) -> OpportunityFinder:
        """Get the opportunity finder instance"""
        if self.opportunity_finder is None:
            raise HTTPException(
                status_code=503,
                detail="Opportunity finder not initialized. Service may still be starting up."
            )
        return self.opportunity_finder
    
    def get_historical_analyzer(self) -> HistoricalAnalyzer:
        """Get the historical analyzer instance"""
        if self.historical_analyzer is None:
            raise HTTPException(
                status_code=503,
                detail="Historical analyzer not initialized. Service may still be starting up."
            )
        return self.historical_analyzer


# Global container instance
services = ServiceContainer()


# Dependency functions for FastAPI
def get_opportunity_finder() -> OpportunityFinder:
    """FastAPI dependency to get opportunity finder"""
    return services.get_opportunity_finder()


def get_historical_analyzer() -> HistoricalAnalyzer:
    """FastAPI dependency to get historical analyzer"""
    return services.get_historical_analyzer()