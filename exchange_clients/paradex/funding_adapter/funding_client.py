"""
API client management for Paradex funding adapter.

Handles SDK client initialization, lifecycle, and cleanup.
"""

import asyncio
from typing import Optional

# Import Paradex SDK
try:
    from paradex_py import Paradex
    from paradex_py.environment import PROD, TESTNET
    PARADEX_SDK_AVAILABLE = True
except ImportError:
    PARADEX_SDK_AVAILABLE = False


class ParadexFundingClient:
    """Manages Paradex SDK API client lifecycle for funding data."""

    def __init__(self, api_base_url: str, environment: str = "prod"):
        """
        Initialize funding client manager.
        
        Args:
            api_base_url: Paradex API base URL (determined by environment)
            environment: "prod" or "testnet"
        """
        if not PARADEX_SDK_AVAILABLE:
            raise ImportError(
                "Paradex SDK is required. Install with: pip install paradex-py"
            )
        
        self.api_base_url = api_base_url
        self.environment = environment
        self.paradex: Optional[Paradex] = None

    async def ensure_client(self) -> None:
        """Ensure API client is initialized."""
        if self.paradex is None:
            # Convert environment string to proper enum
            env = PROD if self.environment.lower() == 'prod' else TESTNET
            # Initialize Paradex client (read-only, no credentials needed)
            # Use run_in_executor since SDK is synchronous
            self.paradex = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: Paradex(env=env, logger=None)
            )

    async def close(self) -> None:
        """Close the API client."""
        # Paradex SDK doesn't require explicit cleanup
        self.paradex = None

