"""
Backpack WebSocket package.

This package contains the modular Backpack WebSocket manager implementation:
- manager: Main BackpackWebSocketManager class
- connection: Connection lifecycle for account and depth streams
- message_handler: Message parsing and routing
- order_book: Order book state management
- market_switcher: Market switching logic
"""

from .manager import BackpackWebSocketManager

__all__ = ["BackpackWebSocketManager"]

