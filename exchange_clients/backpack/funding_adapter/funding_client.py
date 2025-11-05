"""
API client management for Backpack funding adapter.

Handles HTTP session initialization, lifecycle, and cleanup.
"""

from typing import Optional

import aiohttp


class BackpackFundingClient:
    """Manages HTTP session lifecycle for funding data."""

    def __init__(self, api_base_url: str, timeout: int):
        """
        Initialize funding client manager.
        
        Args:
            api_base_url: Backpack API base URL
            timeout: Request timeout in seconds
        """
        self.api_base_url = api_base_url
        self.timeout = timeout
        self.session: Optional[aiohttp.ClientSession] = None

    async def ensure_client(self) -> aiohttp.ClientSession:
        """
        Ensure HTTP session is initialized.
        
        Returns:
            aiohttp.ClientSession instance
        """
        if self.session is None or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            self.session = aiohttp.ClientSession(timeout=timeout)
        return self.session

    async def close(self) -> None:
        """Close the HTTP session."""
        if self.session and not self.session.closed:
            await self.session.close()
        self.session = None

