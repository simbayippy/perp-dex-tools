"""
Telegram Bot Service

A comprehensive Telegram bot for managing trading strategies.

Package Structure:
- core/ - Core bot functionality (bot, config, main entry point)
- managers/ - Process and resource management (processes, ports, health, safety)
- verification/ - Verification modules (credentials, proxies, configs)
- utils/ - Utility modules (API client, auth, formatters, audit logging)
"""

# Export main components for convenience
from telegram_bot_service.core import StrategyControlBot, TelegramBotConfig

__all__ = ['StrategyControlBot', 'TelegramBotConfig']


