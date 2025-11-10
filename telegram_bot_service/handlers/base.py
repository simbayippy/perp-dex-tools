"""
Base handler class with shared dependencies and common helper methods
"""

import logging
from typing import Optional, Dict, Any, Tuple
from telegram import Update
from telegram.ext import ContextTypes
from databases import Database

from telegram_bot_service.utils.auth import TelegramAuth
from telegram_bot_service.utils.formatters import TelegramFormatter
from telegram_bot_service.managers.process_manager import StrategyProcessManager
from telegram_bot_service.verification.credential_verifier import CredentialVerifier
from telegram_bot_service.verification.proxy_verifier import ProxyVerifier
from telegram_bot_service.utils.audit_logger import AuditLogger
from telegram_bot_service.managers.safety_manager import SafetyManager
from telegram_bot_service.managers.health_monitor import HealthMonitor
from telegram_bot_service.verification.config_validator import ConfigValidator
from telegram_bot_service.core.config import TelegramBotConfig


class BaseHandler:
    """Base class for all telegram bot handlers with shared dependencies"""
    
    def __init__(
        self,
        config: TelegramBotConfig,
        database: Database,
        auth: TelegramAuth,
        formatter: TelegramFormatter,
        process_manager: StrategyProcessManager,
        credential_verifier: CredentialVerifier,
        proxy_verifier: ProxyVerifier,
        audit_logger: AuditLogger,
        safety_manager: SafetyManager,
        health_monitor: HealthMonitor,
        config_validator: ConfigValidator,
        encryptor: Optional[Any] = None,
    ):
        self.config = config
        self.database = database
        self.auth = auth
        self.formatter = formatter
        self.process_manager = process_manager
        self.credential_verifier = credential_verifier
        self.proxy_verifier = proxy_verifier
        self.audit_logger = audit_logger
        self.safety_manager = safety_manager
        self.health_monitor = health_monitor
        self.config_validator = config_validator
        self.encryptor = encryptor
        self.logger = logging.getLogger(self.__class__.__name__)
    
    async def get_user_and_api_key(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """
        Helper method to get user and API key from update.
        Returns (user, api_key) or (None, None) if not authenticated.
        """
        telegram_user_id = update.effective_user.id
        user = await self.auth.get_user_by_telegram_id(telegram_user_id)
        
        if not user:
            return None, None
        
        api_key = await self.auth.get_api_key_for_user(user)
        return user, api_key
    
    async def require_auth(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """
        Helper method to require authentication.
        Returns (user, api_key) or sends error message and returns (None, None).
        """
        user, api_key = await self.get_user_and_api_key(update, context)
        
        if not user:
            if update.message:
                await update.message.reply_text(
                    self.formatter.format_not_authenticated(),
                    parse_mode='HTML'
                )
            elif update.callback_query:
                await update.callback_query.edit_message_text(
                    self.formatter.format_not_authenticated(),
                    parse_mode='HTML'
                )
            return None, None
        
        if not api_key:
            if update.message:
                await update.message.reply_text(
                    "❌ API key not found. Please authenticate again with /auth",
                    parse_mode='HTML'
                )
            elif update.callback_query:
                await update.callback_query.edit_message_text(
                    "❌ API key not found. Please authenticate again with /auth",
                    parse_mode='HTML'
                )
            return None, None
        
        return user, api_key

