"""
Aster WebSocket package.

This package contains the modular Aster WebSocket manager implementation:
- manager: Main AsterWebSocketManager class
- connection: Connection lifecycle and listen key management
- message_handler: Message parsing and routing
- order_book: Order book state management
- market_switcher: Market switching and subscription management
"""

from .manager import AsterWebSocketManager

__all__ = ["AsterWebSocketManager"]

