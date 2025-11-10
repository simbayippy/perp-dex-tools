"""
Monitoring handlers for Telegram bot (status, positions, close)
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler

from telegram_bot_service.handlers.base import BaseHandler
from telegram_bot_service.utils.api_client import ControlAPIClient


class MonitoringHandler(BaseHandler):
    """Handler for monitoring-related commands (positions, close)"""
    
    async def positions_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /positions command - interactive account/position selection."""
        user, api_key = await self.require_auth(update, context)
        if not user or not api_key:
            return
        
        # Optional account filter
        account_name = context.args[0] if context.args and len(context.args) > 0 else None
        
        try:
            client = ControlAPIClient(self.config.control_api_base_url, api_key)
            data = await client.get_positions(account_name=account_name)
            
            accounts = data.get('accounts', [])
            
            # If account filter provided, show positions directly
            if account_name:
                await self._show_positions_for_account(update, context, data, account_name, api_key)
                return
            
            # No account filter - show account selection
            if not accounts:
                await update.message.reply_text(
                    "📊 <b>No active positions</b>\n\nNo accounts with open positions found.",
                    parse_mode='HTML'
                )
                return
            
            # Filter accounts that have positions
            accounts_with_positions = [
                acc for acc in accounts 
                if acc.get('positions') and len(acc.get('positions', [])) > 0
            ]
            
            if not accounts_with_positions:
                await update.message.reply_text(
                    "📊 <b>No active positions</b>\n\nNo open positions found.",
                    parse_mode='HTML'
                )
                return
            
            # Show account selection buttons
            keyboard = []
            for account in accounts_with_positions:
                account_name = account.get('account_name', 'N/A')
                position_count = len(account.get('positions', []))
                button_label = f"📊 {account_name} ({position_count} position{'s' if position_count != 1 else ''})"
                callback_data = f"positions_account:{account_name}"
                keyboard.append([InlineKeyboardButton(button_label, callback_data=callback_data)])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            total_positions = sum(len(acc.get('positions', [])) for acc in accounts_with_positions)
            await update.message.reply_text(
                f"📊 <b>Active Positions</b>\n\n"
                f"Found <b>{total_positions}</b> position{'s' if total_positions != 1 else ''} "
                f"across <b>{len(accounts_with_positions)}</b> account{'s' if len(accounts_with_positions) != 1 else ''}.\n\n"
                f"Select an account to view positions:",
                parse_mode='HTML',
                reply_markup=reply_markup
            )
                
        except Exception as e:
            self.logger.error(f"Positions error: {e}")
            await update.message.reply_text(
                self.formatter.format_error(f"Failed to get positions: {str(e)}"),
                parse_mode='HTML'
            )
    
    async def _show_positions_for_account(
        self, 
        update: Update, 
        context: ContextTypes.DEFAULT_TYPE,
        data: dict,
        account_name: str,
        api_key: str
    ):
        """Show positions for a specific account with close buttons."""
        accounts = data.get('accounts', [])
        account = next((acc for acc in accounts if acc.get('account_name') == account_name), None)
        
        if not account:
            message = update.message if hasattr(update, 'message') and update.message else None
            query = update.callback_query if hasattr(update, 'callback_query') and update.callback_query else None
            
            error_msg = f"❌ Account '{account_name}' not found."
            if query:
                await query.edit_message_text(error_msg, parse_mode='HTML')
            elif message:
                await message.reply_text(error_msg, parse_mode='HTML')
            return
        
        positions = account.get('positions', [])
        
        if not positions:
            message = update.message if hasattr(update, 'message') and update.message else None
            query = update.callback_query if hasattr(update, 'callback_query') and update.callback_query else None
            
            no_pos_msg = f"📊 <b>{account_name}</b>\n\nNo active positions."
            if query:
                await query.edit_message_text(no_pos_msg, parse_mode='HTML')
            elif message:
                await message.reply_text(no_pos_msg, parse_mode='HTML')
            return
        
        # Format positions summary
        message_text = f"📊 <b>{account_name}</b>\n\n"
        message_text += f"Found <b>{len(positions)}</b> position{'s' if len(positions) != 1 else ''}:\n\n"
        
        # Create inline keyboard with position buttons
        keyboard = []
        position_index = 0
        for pos in positions:
            position_index += 1
            symbol = pos.get('symbol', 'N/A')
            long_dex = pos.get('long_dex', 'N/A').upper()
            short_dex = pos.get('short_dex', 'N/A').upper()
            position_id = pos.get('id', '')
            
            # Button label: "1. BTC/USD (LIGHTER/PARADEX)"
            button_label = f"{position_index}. {symbol} ({long_dex}/{short_dex})"
            # Store position_id in callback data
            callback_data = f"close_pos:{position_id}"
            
            keyboard.append([InlineKeyboardButton(button_label, callback_data=callback_data)])
        
        # Add back button
        keyboard.append([InlineKeyboardButton("🔙 Back to Accounts", callback_data="positions_back_to_accounts")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Send or edit message
        query = update.callback_query if hasattr(update, 'callback_query') and update.callback_query else None
        message = update.message if hasattr(update, 'message') and update.message else None
        
        if query:
            await query.answer()
            await query.edit_message_text(
                message_text + "Select a position to close:",
                parse_mode='HTML',
                reply_markup=reply_markup
            )
        elif message:
            await message.reply_text(
                message_text + "Select a position to close:",
                parse_mode='HTML',
                reply_markup=reply_markup
            )
    
    async def positions_account_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle account selection callback - show positions for selected account."""
        query = update.callback_query
        await query.answer()
        
        # Parse callback data: "positions_account:{account_name}"
        callback_data = query.data
        if not callback_data.startswith("positions_account:"):
            await query.edit_message_text(
                "❌ Invalid selection. Please use /positions again.",
                parse_mode='HTML'
            )
            return
        
        account_name = callback_data.split(":", 1)[1]
        
        # Get user and API key
        user, api_key = await self.require_auth(update, context)
        if not user or not api_key:
            return
        
        try:
            client = ControlAPIClient(self.config.control_api_base_url, api_key)
            data = await client.get_positions(account_name=account_name)
            
            await self._show_positions_for_account(update, context, data, account_name, api_key)
            
        except Exception as e:
            self.logger.error(f"Positions account callback error: {e}")
            await query.edit_message_text(
                self.formatter.format_error(f"Failed to get positions: {str(e)}"),
                parse_mode='HTML'
            )
    
    async def positions_back_to_accounts_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle back button - return to account selection."""
        query = update.callback_query
        await query.answer()
        
        # Get user and API key
        user, api_key = await self.require_auth(update, context)
        if not user or not api_key:
            return
        
        try:
            client = ControlAPIClient(self.config.control_api_base_url, api_key)
            data = await client.get_positions(account_name=None)
            
            accounts = data.get('accounts', [])
            
            if not accounts:
                await query.edit_message_text(
                    "📊 <b>No active positions</b>\n\nNo accounts with open positions found.",
                    parse_mode='HTML'
                )
                return
            
            # Filter accounts that have positions
            accounts_with_positions = [
                acc for acc in accounts 
                if acc.get('positions') and len(acc.get('positions', [])) > 0
            ]
            
            if not accounts_with_positions:
                await query.edit_message_text(
                    "📊 <b>No active positions</b>\n\nNo open positions found.",
                    parse_mode='HTML'
                )
                return
            
            # Show account selection buttons
            keyboard = []
            for account in accounts_with_positions:
                account_name = account.get('account_name', 'N/A')
                position_count = len(account.get('positions', []))
                button_label = f"📊 {account_name} ({position_count} position{'s' if position_count != 1 else ''})"
                callback_data = f"positions_account:{account_name}"
                keyboard.append([InlineKeyboardButton(button_label, callback_data=callback_data)])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            total_positions = sum(len(acc.get('positions', [])) for acc in accounts_with_positions)
            await query.edit_message_text(
                f"📊 <b>Active Positions</b>\n\n"
                f"Found <b>{total_positions}</b> position{'s' if total_positions != 1 else ''} "
                f"across <b>{len(accounts_with_positions)}</b> account{'s' if len(accounts_with_positions) != 1 else ''}.\n\n"
                f"Select an account to view positions:",
                parse_mode='HTML',
                reply_markup=reply_markup
            )
            
        except Exception as e:
            self.logger.error(f"Back to accounts error: {e}")
            await query.edit_message_text(
                self.formatter.format_error(f"Failed to get positions: {str(e)}"),
                parse_mode='HTML'
            )
    
    async def close_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /close command - shows interactive position selection."""
        user, api_key = await self.require_auth(update, context)
        if not user or not api_key:
            return
        
        # Optional account filter
        account_name = context.args[0] if context.args and len(context.args) > 0 else None
        
        try:
            client = ControlAPIClient(self.config.control_api_base_url, api_key)
            data = await client.get_positions(account_name=account_name)
            
            # Check if there are any positions
            accounts = data.get('accounts', [])
            all_positions = []
            for account in accounts:
                positions = account.get('positions', [])
                for pos in positions:
                    all_positions.append({
                        'position': pos,
                        'account_name': account.get('account_name', 'N/A')
                    })
            
            if not all_positions:
                await update.message.reply_text(
                    "📊 <b>No active positions</b>\n\nNothing to close.",
                    parse_mode='HTML'
                )
                return
            
            # Format positions list
            message = self.formatter.format_positions_for_selection(data)
            
            # Create inline keyboard with position buttons
            keyboard = []
            position_index = 0
            for account in accounts:
                positions = account.get('positions', [])
                for pos in positions:
                    position_index += 1
                    symbol = pos.get('symbol', 'N/A')
                    long_dex = pos.get('long_dex', 'N/A').upper()
                    short_dex = pos.get('short_dex', 'N/A').upper()
                    position_id = pos.get('id', '')
                    
                    # Button label: "1. BTC/USD (LIGHTER/PARADEX)"
                    button_label = f"{position_index}. {symbol} ({long_dex}/{short_dex})"
                    # Store position_id in callback data
                    callback_data = f"close_pos:{position_id}"
                    
                    keyboard.append([InlineKeyboardButton(button_label, callback_data=callback_data)])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                message,
                parse_mode='HTML',
                reply_markup=reply_markup
            )
            
        except Exception as e:
            self.logger.error(f"Close command error: {e}")
            await update.message.reply_text(
                self.formatter.format_error(f"Failed to get positions: {str(e)}"),
                parse_mode='HTML'
            )
    
    async def close_position_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle position selection callback - shows order type selection."""
        query = update.callback_query
        await query.answer()
        
        # Parse callback data: "close_pos:{position_id}"
        callback_data = query.data
        if not callback_data.startswith("close_pos:"):
            await query.edit_message_text(
                "❌ Invalid selection. Please use /close again.",
                parse_mode='HTML'
            )
            return
        
        position_id = callback_data.split(":", 1)[1]
        
        # Store position_id in user_data for the next step
        context.user_data['close_position_id'] = position_id
        
        # Show order type selection
        keyboard = [
            [
                InlineKeyboardButton("🟢 Market", callback_data=f"close_type:{position_id}:market"),
                InlineKeyboardButton("🟡 Limit", callback_data=f"close_type:{position_id}:limit")
            ],
            [InlineKeyboardButton("❌ Cancel", callback_data="close_cancel")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"📊 <b>Select Order Type</b>\n\n"
            f"Position ID: <code>{position_id}</code>\n\n"
            f"Choose how to close this position:",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
    
    async def close_order_type_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle order type selection callback - executes the close."""
        query = update.callback_query
        await query.answer()
        
        # Parse callback data: "close_type:{position_id}:{order_type}"
        callback_data = query.data
        if callback_data == "close_cancel":
            await query.edit_message_text(
                "❌ Close cancelled.",
                parse_mode='HTML'
            )
            # Clear user_data
            context.user_data.pop('close_position_id', None)
            return
        
        if not callback_data.startswith("close_type:"):
            await query.edit_message_text(
                "❌ Invalid selection. Please use /close again.",
                parse_mode='HTML'
            )
            return
        
        # Parse: "close_type:{position_id}:{order_type}"
        parts = callback_data.split(":", 2)
        if len(parts) != 3:
            await query.edit_message_text(
                "❌ Invalid selection. Please use /close again.",
                parse_mode='HTML'
            )
            return
        
        position_id = parts[1]
        order_type = parts[2]
        
        if order_type not in ("market", "limit"):
            await query.edit_message_text(
                "❌ Invalid order type. Please use /close again.",
                parse_mode='HTML'
            )
            return
        
        # Get user and API key
        user, api_key = await self.require_auth(update, context)
        if not user or not api_key:
            return
        
        # Show processing message
        await query.edit_message_text(
            f"⏳ <b>Closing position...</b>\n\n"
            f"Position ID: <code>{position_id}</code>\n"
            f"Order Type: {order_type.upper()}",
            parse_mode='HTML'
        )
        
        try:
            client = ControlAPIClient(self.config.control_api_base_url, api_key)
            data = await client.close_position(
                position_id=position_id,
                order_type=order_type,
                reason="telegram_manual_close"
            )
            
            message = self.formatter.format_close_result(data)
            await query.edit_message_text(message, parse_mode='HTML')
            
            # Clear user_data
            context.user_data.pop('close_position_id', None)
            
        except Exception as e:
            self.logger.error(f"Close error: {e}")
            await query.edit_message_text(
                self.formatter.format_error(f"Failed to close position: {str(e)}"),
                parse_mode='HTML'
            )
    
    def register_handlers(self, application):
        """Register monitoring command and callback handlers"""
        # Note: /status removed - use /list_strategies instead for strategy status
        application.add_handler(CommandHandler("positions", self.positions_command))
        application.add_handler(CommandHandler("close", self.close_command))
        
        # Callback query handlers for interactive flows
        # Order matters: more specific patterns first
        application.add_handler(CallbackQueryHandler(
            self.positions_account_callback,
            pattern="^positions_account:"
        ))
        application.add_handler(CallbackQueryHandler(
            self.positions_back_to_accounts_callback,
            pattern="^positions_back_to_accounts$"
        ))
        application.add_handler(CallbackQueryHandler(
            self.close_position_callback,
            pattern="^close_pos:"
        ))
        application.add_handler(CallbackQueryHandler(
            self.close_order_type_callback,
            pattern="^close_type:"
        ))
        application.add_handler(CallbackQueryHandler(
            self.close_order_type_callback,
            pattern="^close_cancel$"
        ))

