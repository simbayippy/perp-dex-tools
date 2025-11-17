"""
Simple Telegram bot utility for sending text messages.

This is a lightweight wrapper around the Telegram Bot API for sending
simple text notifications. For more complex bot functionality, see
telegram_bot_service.
"""

from __future__ import annotations

import requests
from typing import Optional


class TelegramBot:
    """
    Simple Telegram bot for sending text messages.
    
    This is a minimal implementation for sending notifications.
    For full bot functionality, use telegram_bot_service.StrategyControlBot.
    """

    def __init__(self, token: str, chat_id: str) -> None:
        """
        Initialize Telegram bot.
        
        Args:
            token: Telegram bot token
            chat_id: Telegram chat ID to send messages to
        """
        self.token = token
        self.chat_id = chat_id
        self.api_url = f"https://api.telegram.org/bot{token}"

    def send_text(self, text: str) -> None:
        """
        Send a text message to the configured chat.
        
        Args:
            text: Message text to send
            
        Raises:
            Exception: If the message fails to send
        """
        url = f"{self.api_url}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",  # Allow basic HTML formatting
        }
        
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()

