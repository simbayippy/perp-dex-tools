"""
Telegram bot with command handlers for strategy control
"""

import asyncio
import logging
import os
from typing import Optional
from telegram import Update
from telegram.ext import Application, ContextTypes
from databases import Database
from cryptography.fernet import Fernet

from telegram_bot_service.core.config import TelegramBotConfig
from telegram_bot_service.handlers.auth import AuthHandler
from telegram_bot_service.handlers.monitoring import MonitoringHandler
from telegram_bot_service.handlers.accounts import AccountHandler
from telegram_bot_service.handlers.configs import ConfigHandler
from telegram_bot_service.handlers.strategies import StrategyHandler
from telegram_bot_service.handlers.wizards import WizardRouter
from telegram_bot_service.utils.auth import TelegramAuth
from telegram_bot_service.utils.formatters import TelegramFormatter
from telegram_bot_service.managers.process_manager import StrategyProcessManager
from telegram_bot_service.verification.credential_verifier import CredentialVerifier
from telegram_bot_service.verification.proxy_verifier import ProxyVerifier
from telegram_bot_service.utils.audit_logger import AuditLogger
from telegram_bot_service.managers.safety_manager import SafetyManager
from telegram_bot_service.managers.health_monitor import HealthMonitor
from telegram_bot_service.verification.config_validator import ConfigValidator


class StrategyControlBot:
    """Telegram bot for strategy control"""
    
    def __init__(self, config: TelegramBotConfig):
        self.config = config
        self.database = Database(config.database_url)
        self.auth = TelegramAuth(self.database)
        self.formatter = TelegramFormatter()
        self.application = None
        self.logger = logging.getLogger(__name__)
        
        # Initialize managers
        self.process_manager = StrategyProcessManager(self.database)
        self.credential_verifier = CredentialVerifier()
        self.proxy_verifier = ProxyVerifier()
        self.audit_logger = AuditLogger(self.database)
        self.safety_manager = SafetyManager(self.database)
        self.health_monitor = HealthMonitor(self.database)
        self.config_validator = ConfigValidator(self.database)
        
        # Credential encryptor
        encryption_key = os.getenv('CREDENTIAL_ENCRYPTION_KEY')
        if encryption_key:
            self.encryptor = Fernet(encryption_key.encode() if isinstance(encryption_key, str) else encryption_key)
        else:
            self.encryptor = None
    
        # Initialize handlers
        handler_kwargs = {
            'config': self.config,
            'database': self.database,
            'auth': self.auth,
            'formatter': self.formatter,
            'process_manager': self.process_manager,
            'credential_verifier': self.credential_verifier,
            'proxy_verifier': self.proxy_verifier,
            'audit_logger': self.audit_logger,
            'safety_manager': self.safety_manager,
            'health_monitor': self.health_monitor,
            'config_validator': self.config_validator,
            'encryptor': self.encryptor,
        }
        
        self.auth_handler = AuthHandler(**handler_kwargs)
        self.monitoring_handler = MonitoringHandler(**handler_kwargs)
        self.account_handler = AccountHandler(**handler_kwargs)
        self.config_handler = ConfigHandler(**handler_kwargs)
        self.strategy_handler = StrategyHandler(**handler_kwargs)
        self.wizard_router = WizardRouter(
            account_handler=self.account_handler,
            config_handler=self.config_handler,
            **handler_kwargs
            )
    
    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle errors."""
        error = context.error
        self.logger.error(f"Update {update} caused error {error}", exc_info=error)
        
        # Try to provide more helpful error messages
        if update and update.callback_query:
            query = update.callback_query
            try:
                await query.answer("❌ An error occurred. Please try again.")
                await query.edit_message_text(
                    "❌ An error occurred. Please try again or use /help for assistance.\n\n"
                    f"Error: {str(error)}",
                    parse_mode='HTML'
                )
            except:
                try:
                    await query.message.reply_text(
                        "❌ An error occurred. Please try again or use /help for assistance.",
                        parse_mode='HTML'
                    )
                except:
                    pass
        elif update and update.effective_message:
            await update.effective_message.reply_text(
                "❌ An error occurred. Please try again or use /help for assistance.",
                parse_mode='HTML'
            )
    
    def setup_handlers(self, application: Application):
        """Setup command handlers."""
        # Register handlers from each handler class
        self.auth_handler.register_handlers(application)
        self.monitoring_handler.register_handlers(application)
        self.account_handler.register_handlers(application)
        self.config_handler.register_handlers(application)
        self.strategy_handler.register_handlers(application)
        self.wizard_router.register_handlers(application)
        
        # Error handler
        application.add_error_handler(self.error_handler)
    
    async def start(self):
        """Start the bot."""
        # Connect to database
        await self.database.connect()
        self.logger.info("Database connected")
        
        # Recover processes (sync DB with Supervisor)
        try:
            self.logger.info("Recovering processes...")
            stats = await self.process_manager.recover_processes()
            self.logger.info(f"Process recovery: {stats}")
        except Exception as e:
            self.logger.error(f"Process recovery error: {e}")
        
        # Create application
        self.application = Application.builder().token(self.config.telegram_bot_token).build()
        
        # Setup handlers
        self.setup_handlers(self.application)
        
        # Start health monitoring background task
        self._health_monitor_task = asyncio.create_task(self._health_monitor_loop())
        
        # Start polling
        self.logger.info("Starting Telegram bot...")
        await self.application.initialize()
        await self.application.start()
        await self.application.updater.start_polling()
        self.logger.info("Telegram bot started and polling")
    
    async def _health_monitor_loop(self):
        """Background task to monitor strategy health."""
        while True:
            try:
                await asyncio.sleep(60)  # Run every 60 seconds
                stats = await self.health_monitor.check_all_strategies()
                if stats.get("unhealthy", 0) > 0 or stats.get("degraded", 0) > 0:
                    self.logger.warning(f"Health check found issues: {stats}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Health monitor error: {e}")
    
    async def stop(self):
        """Stop the bot."""
        # Cancel health monitor task
        if hasattr(self, '_health_monitor_task'):
            self._health_monitor_task.cancel()
            try:
                await self._health_monitor_task
            except asyncio.CancelledError:
                pass
        
        if self.application:
            try:
                if self.application.updater.running:
                    await self.application.updater.stop()
            except (RuntimeError, AttributeError):
                # Updater not running or already stopped
                pass
            
            try:
                await self.application.stop()
            except Exception as e:
                self.logger.warning(f"Error stopping application: {e}")
            
            try:
                await self.application.shutdown()
            except Exception as e:
                self.logger.warning(f"Error shutting down application: {e}")
        
        try:
            await self.database.disconnect()
        except Exception as e:
            self.logger.warning(f"Error disconnecting database: {e}")
        
        self.logger.info("Telegram bot stopped")
