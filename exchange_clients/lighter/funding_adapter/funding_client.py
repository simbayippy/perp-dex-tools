"""
API client management for Lighter funding adapter.

Handles SDK client initialization, lifecycle, and cleanup.
"""

from typing import Optional

# Import Lighter SDK
try:
    from lighter import ApiClient, Configuration, FundingApi, OrderApi
    LIGHTER_SDK_AVAILABLE = True
except ImportError:
    LIGHTER_SDK_AVAILABLE = False


class LighterFundingClient:
    """Manages Lighter SDK API client lifecycle for funding data."""

    def __init__(self, api_base_url: str):
        """
        Initialize funding client manager.
        
        Args:
            api_base_url: Lighter API base URL (mainnet or testnet)
        """
        if not LIGHTER_SDK_AVAILABLE:
            raise ImportError(
                "Lighter SDK is required. Install with: pip install lighter-python"
            )
        
        self.api_base_url = api_base_url
        self.api_client: Optional[ApiClient] = None
        self.funding_api: Optional[FundingApi] = None
        self.order_api: Optional[OrderApi] = None

    async def ensure_client(self) -> None:
        """Ensure API client is initialized."""
        if self.api_client is None or self.api_client.rest_client.pool_manager is None:
            configuration = Configuration(host=self.api_base_url)
            self.api_client = ApiClient(configuration=configuration)
            self.funding_api = FundingApi(self.api_client)
            self.order_api = OrderApi(self.api_client)

    async def close(self) -> None:
        """Close the API client."""
        if self.api_client and self.api_client.rest_client.pool_manager:
            await self.api_client.close()

