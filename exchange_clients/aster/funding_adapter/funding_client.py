"""
API client management for Aster funding adapter.

Handles SDK client initialization, lifecycle, and cleanup.
"""

from typing import Optional

# Import Aster SDK
try:
    from aster.rest_api import Client as AsterClient
    ASTER_SDK_AVAILABLE = True
except ImportError:
    ASTER_SDK_AVAILABLE = False


class AsterFundingClient:
    """Manages Aster SDK API client lifecycle for funding data."""

    def __init__(self, api_base_url: str, timeout: int):
        """
        Initialize funding client manager.
        
        Args:
            api_base_url: Aster API base URL
            timeout: Request timeout in seconds
        """
        if not ASTER_SDK_AVAILABLE:
            raise ImportError(
                "Aster SDK is required. Install with: pip install aster-connector-python"
            )
        
        self.api_base_url = api_base_url
        self.timeout = timeout
        self.aster_client: Optional[AsterClient] = None

    def ensure_client(self) -> AsterClient:
        """
        Ensure API client is initialized.
        
        Returns:
            AsterClient instance
        """
        if self.aster_client is None:
            self.aster_client = AsterClient(base_url=self.api_base_url, timeout=self.timeout)
        return self.aster_client

    async def close(self) -> None:
        """Close the API client."""
        # Aster SDK doesn't require explicit cleanup
        self.aster_client = None

