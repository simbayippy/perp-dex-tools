"""
Aster funding adapter package.

This package contains the modular Aster funding adapter implementation:
- adapter: Main AsterFundingAdapter class
- funding_client: API client management
- fetchers: Data fetching logic
"""

from .adapter import AsterFundingAdapter

__all__ = ["AsterFundingAdapter"]

