"""
API routes
"""

from funding_rate_service.api.routes import funding_rates, opportunities, dexes, health, tasks

__all__ = [
    "funding_rates",
    "opportunities",
    "dexes",
    "health",
    "tasks",
]

