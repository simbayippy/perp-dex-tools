"""
Wizard message router for Telegram bot
"""

from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, filters

from telegram_bot_service.handlers.base import BaseHandler


class WizardRouter(BaseHandler):
    """Router for wizard message handling"""
    
    def __init__(self, account_handler, config_handler, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.account_handler = account_handler
        self.config_handler = config_handler
    
    async def handle_wizard_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle wizard step messages."""
        wizard = context.user_data.get('wizard')
        if not wizard:
            return  # Not in a wizard
        
        text = update.message.text
        
        wizard_type = wizard['type']
        
        if wizard_type == 'create_account':
            await self.account_handler.handle_create_account_wizard(update, context, wizard, text)
        elif wizard_type == 'add_exchange':
            await self.account_handler.handle_add_exchange_wizard(update, context, wizard, text)
        elif wizard_type == 'add_proxy':
            await self.account_handler.handle_add_proxy_wizard(update, context, wizard, text)
        elif wizard_type == 'edit_account':
            await self.account_handler.handle_edit_account_wizard(update, context, wizard, text)
        elif wizard_type == 'edit_config':
            await self.config_handler.handle_edit_config_wizard(update, context, wizard, text)
    
    def register_handlers(self, application):
        """Register wizard message handler"""
        # Wizard message handler (for multi-step wizards)
        application.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            self.handle_wizard_message
        ))

