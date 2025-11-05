"""
Backpack funding adapter package.

This package contains the modular Backpack funding adapter implementation:
- adapter: Main BackpackFundingAdapter class
- funding_client: HTTP session management
- fetchers: Data fetching logic
"""

from .adapter import BackpackFundingAdapter

__all__ = ["BackpackFundingAdapter"]

