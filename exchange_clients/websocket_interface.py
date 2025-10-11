"""
WebSocket Interface for Exchange Clients

This module provides a lightweight protocol/interface for exchange WebSocket managers.
It's NOT a required base class - exchanges can implement WebSockets however they want.

Purpose:
- Document the expected interface for WebSocket managers
- Provide type hints for IDE support
- Serve as a reference for implementing new exchanges

Implementation Notes:
- This is a PROTOCOL (duck typing), not a strict base class
- Exchanges using SDK WebSockets don't need to follow this
- Only custom WebSocket implementations (Aster, Lighter) should consider this pattern
- Feel free to add exchange-specific methods as needed
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, Callable
import asyncio


class WebSocketManagerInterface(ABC):
    """
    Optional interface for exchange WebSocket managers.
    
    This is a REFERENCE IMPLEMENTATION - not all exchanges need to follow this.
    Use this as a guide when implementing custom WebSocket logic.
    
    Exchanges using SDK-provided WebSockets (EdgeX, GRVT, Paradex) can ignore this.
    """
    
    def __init__(
        self, 
        config: Dict[str, Any],
        order_update_callback: Optional[Callable] = None,
        **kwargs
    ):
        """
        Initialize WebSocket manager.
        
        Args:
            config: Exchange configuration
            order_update_callback: Callback for order updates
            **kwargs: Exchange-specific parameters
        """
        self.config = config
        self.order_update_callback = order_update_callback
        self.websocket = None
        self.running = False
        self.logger = None
    
    @abstractmethod
    async def connect(self) -> None:
        """
        Connect to exchange WebSocket.
        
        Should:
        - Establish WebSocket connection
        - Authenticate if required
        - Subscribe to necessary channels
        - Start message processing loop
        """
        pass
    
    @abstractmethod
    async def disconnect(self) -> None:
        """
        Disconnect from WebSocket and cleanup resources.
        
        Should:
        - Set running flag to False
        - Cancel background tasks
        - Close WebSocket connection
        - Cleanup any resources
        """
        pass
    
    def set_logger(self, logger) -> None:
        """
        Set logger instance for WebSocket manager.
        
        Args:
            logger: Logger instance (typically from unified_logger)
        """
        self.logger = logger
    
    # Optional: Add exchange-specific methods as needed
    # Examples:
    # - async def subscribe_to_channel(self, channel: str) -> None
    # - async def keepalive(self) -> None
    # - def get_connection_status(self) -> bool


# Example implementation pattern:
"""
class AsterWebSocketManager(WebSocketManagerInterface):
    def __init__(self, config, api_key, secret_key, order_update_callback):
        super().__init__(config, order_update_callback)
        self.api_key = api_key
        self.secret_key = secret_key
        self.listen_key = None
        # ... exchange-specific initialization
    
    async def connect(self) -> None:
        # Aster-specific connection logic
        self.listen_key = await self._get_listen_key()
        # ... connect to WebSocket
    
    async def disconnect(self) -> None:
        # Aster-specific disconnection logic
        pass
    
    # Add Aster-specific methods
    async def _get_listen_key(self) -> str:
        pass
    
    async def _keepalive_listen_key(self) -> bool:
        pass
"""

