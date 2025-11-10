"""
Authentication handlers for Telegram bot
"""

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler

from telegram_bot_service.handlers.base import BaseHandler


class AuthHandler(BaseHandler):
    """Handler for authentication-related commands"""
    
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
    
    def register_handlers(self, application):
        """Register authentication command handlers"""
        application.add_handler(CommandHandler("start", self.start_command))
        application.add_handler(CommandHandler("help", self.help_command))
        application.add_handler(CommandHandler("auth", self.auth_command))
        application.add_handler(CommandHandler("logout", self.logout_command))

