"""
Telegram bot with command handlers for strategy control
"""

import asyncio
import logging
from typing import Optional, Dict, Any
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from databases import Database

from telegram_bot_service.config import TelegramBotConfig
from telegram_bot_service.api_client import ControlAPIClient
from telegram_bot_service.auth import TelegramAuth
from telegram_bot_service.formatters import TelegramFormatter


class StrategyControlBot:
    """Telegram bot for strategy control"""
    
    def __init__(self, config: TelegramBotConfig):
        self.config = config
        self.database = Database(config.database_url)
        self.auth = TelegramAuth(self.database)
        self.formatter = TelegramFormatter()
        self.application = None
        self.logger = logging.getLogger(__name__)
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command."""
        welcome = (
            "👋 <b>Welcome to Strategy Control Bot!</b>\n\n"
            "This bot allows you to monitor and control your trading strategies.\n\n"
            "To get started, authenticate with your API key:\n"
            "<code>/auth &lt;your_api_key&gt;</code>\n\n"
            "Use /help to see all available commands."
        )
        await update.message.reply_text(welcome, parse_mode='HTML')
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command."""
        help_text = self.formatter.format_help()
        await update.message.reply_text(help_text, parse_mode='HTML')
    
    async def auth_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /auth command."""
        telegram_user_id = update.effective_user.id
        
        if not context.args or len(context.args) == 0:
            await update.message.reply_text(
                "❌ Please provide your API key:\n"
                "<code>/auth &lt;your_api_key&gt;</code>",
                parse_mode='HTML'
            )
            return
        
        api_key = context.args[0]
        
        try:
            result = await self.auth.authenticate_with_api_key(api_key, telegram_user_id)
            
            if result['success']:
                await update.message.reply_text(
                    f"✅ {result['message']}\n\n"
                    "You can now use commands like /positions, /status, etc.",
                    parse_mode='HTML'
                )
            else:
                await update.message.reply_text(
                    f"❌ {result['message']}",
                    parse_mode='HTML'
                )
        except Exception as e:
            self.logger.error(f"Auth error: {e}")
            await update.message.reply_text(
                f"❌ Authentication failed: {str(e)}",
                parse_mode='HTML'
            )
    
    async def logout_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /logout command."""
        telegram_user_id = update.effective_user.id
        
        try:
            success = await self.auth.logout(telegram_user_id)
            if success:
                await update.message.reply_text(
                    "✅ Successfully logged out. Your Telegram account has been unlinked.",
                    parse_mode='HTML'
                )
            else:
                await update.message.reply_text(
                    "ℹ️ You are not currently authenticated.",
                    parse_mode='HTML'
                )
        except Exception as e:
            self.logger.error(f"Logout error: {e}")
            await update.message.reply_text(
                f"❌ Logout failed: {str(e)}",
                parse_mode='HTML'
            )
    
    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /status command."""
        telegram_user_id = update.effective_user.id
        
        # Get user and API key
        user = await self.auth.get_user_by_telegram_id(telegram_user_id)
        if not user:
            await update.message.reply_text(
                self.formatter.format_not_authenticated(),
                parse_mode='HTML'
            )
            return
        
        api_key = await self.auth.get_api_key_for_user(user)
        if not api_key:
            await update.message.reply_text(
                "❌ API key not found. Please authenticate again with /auth",
                parse_mode='HTML'
            )
            return
        
        try:
            client = ControlAPIClient(self.config.control_api_base_url, api_key)
            data = await client.get_status()
            message = self.formatter.format_status(data)
            await update.message.reply_text(message, parse_mode='HTML')
        except Exception as e:
            self.logger.error(f"Status error: {e}")
            await update.message.reply_text(
                self.formatter.format_error(f"Failed to get status: {str(e)}"),
                parse_mode='HTML'
            )
    
    async def positions_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /positions command."""
        telegram_user_id = update.effective_user.id
        
        # Get user and API key
        user = await self.auth.get_user_by_telegram_id(telegram_user_id)
        if not user:
            await update.message.reply_text(
                self.formatter.format_not_authenticated(),
                parse_mode='HTML'
            )
            return
        
        api_key = await self.auth.get_api_key_for_user(user)
        if not api_key:
            await update.message.reply_text(
                "❌ API key not found. Please authenticate again with /auth",
                parse_mode='HTML'
            )
            return
        
        # Optional account filter
        account_name = context.args[0] if context.args and len(context.args) > 0 else None
        
        try:
            client = ControlAPIClient(self.config.control_api_base_url, api_key)
            data = await client.get_positions(account_name=account_name)
            
            # Format and send (may need to split if too long)
            message = self.formatter.format_positions(data)
            
            # Split if needed (TelegramFormatter handles this)
            messages = self.formatter._split_message(message) if len(message) > self.formatter.MAX_MESSAGE_LENGTH else [message]
            
            for msg in messages:
                await update.message.reply_text(msg, parse_mode='HTML')
                
        except Exception as e:
            self.logger.error(f"Positions error: {e}")
            await update.message.reply_text(
                self.formatter.format_error(f"Failed to get positions: {str(e)}"),
                parse_mode='HTML'
            )
    
    async def close_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /close command."""
        telegram_user_id = update.effective_user.id
        
        # Get user and API key
        user = await self.auth.get_user_by_telegram_id(telegram_user_id)
        if not user:
            await update.message.reply_text(
                self.formatter.format_not_authenticated(),
                parse_mode='HTML'
            )
            return
        
        api_key = await self.auth.get_api_key_for_user(user)
        if not api_key:
            await update.message.reply_text(
                "❌ API key not found. Please authenticate again with /auth",
                parse_mode='HTML'
            )
            return
        
        # Parse arguments
        if not context.args or len(context.args) == 0:
            await update.message.reply_text(
                "❌ Please provide position ID:\n"
                "<code>/close &lt;position_id&gt; [market|limit]</code>",
                parse_mode='HTML'
            )
            return
        
        position_id = context.args[0]
        order_type = context.args[1] if len(context.args) > 1 else "market"
        
        if order_type not in ("market", "limit"):
            await update.message.reply_text(
                "❌ Order type must be 'market' or 'limit'",
                parse_mode='HTML'
            )
            return
        
        try:
            client = ControlAPIClient(self.config.control_api_base_url, api_key)
            data = await client.close_position(
                position_id=position_id,
                order_type=order_type,
                reason="telegram_manual_close"
            )
            message = self.formatter.format_close_result(data)
            await update.message.reply_text(message, parse_mode='HTML')
        except Exception as e:
            self.logger.error(f"Close error: {e}")
            await update.message.reply_text(
                self.formatter.format_error(f"Failed to close position: {str(e)}"),
                parse_mode='HTML'
            )
    
    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle errors."""
        self.logger.error(f"Update {update} caused error {context.error}")
        if update and update.effective_message:
            await update.effective_message.reply_text(
                "❌ An error occurred. Please try again or use /help for assistance.",
                parse_mode='HTML'
            )
    
    def setup_handlers(self, application: Application):
        """Setup command handlers."""
        application.add_handler(CommandHandler("start", self.start_command))
        application.add_handler(CommandHandler("help", self.help_command))
        application.add_handler(CommandHandler("auth", self.auth_command))
        application.add_handler(CommandHandler("logout", self.logout_command))
        application.add_handler(CommandHandler("status", self.status_command))
        application.add_handler(CommandHandler("positions", self.positions_command))
        application.add_handler(CommandHandler("close", self.close_command))
        
        # Error handler
        application.add_error_handler(self.error_handler)
    
    async def start(self):
        """Start the bot."""
        # Connect to database
        await self.database.connect()
        self.logger.info("Database connected")
        
        # Create application
        self.application = Application.builder().token(self.config.telegram_bot_token).build()
        
        # Setup handlers
        self.setup_handlers(self.application)
        
        # Start polling
        self.logger.info("Starting Telegram bot...")
        await self.application.initialize()
        await self.application.start()
        await self.application.updater.start_polling()
        self.logger.info("Telegram bot started and polling")
    
    async def stop(self):
        """Stop the bot."""
        if self.application:
            await self.application.updater.stop()
            await self.application.stop()
            await self.application.shutdown()
        await self.database.disconnect()
        self.logger.info("Telegram bot stopped")

