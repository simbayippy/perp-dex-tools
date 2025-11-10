"""
Core Telegram Bot Service Components

Contains the main bot class, configuration, and entry point.
"""

from telegram_bot_service.core.bot import StrategyControlBot
from telegram_bot_service.core.config import TelegramBotConfig

__all__ = ['StrategyControlBot', 'TelegramBotConfig']

