"""
Configuration management for Telegram bot service
"""

import os
from typing import Optional
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


class TelegramBotConfig:
    """Configuration for Telegram bot service"""
    
    def __init__(self):
        # Telegram bot configuration
        self.telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
        if not self.telegram_bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN environment variable is required")
        
        # Control API configuration
        self.control_api_host: str = os.getenv("CONTROL_API_HOST", "localhost")
        self.control_api_port: int = int(os.getenv("CONTROL_API_PORT", "8766"))
        self.control_api_base_url: str = f"http://{self.control_api_host}:{self.control_api_port}"
        
        # Database configuration
        self.database_url: str = os.getenv("DATABASE_URL", "")
        if not self.database_url:
            raise ValueError("DATABASE_URL environment variable is required")
        
        # Logging configuration
        self.log_level: str = os.getenv("TELEGRAM_BOT_LOG_LEVEL", "INFO")
    
    def validate(self) -> bool:
        """Validate configuration"""
        if not self.telegram_bot_token:
            return False
        if not self.database_url:
            return False
        return True

