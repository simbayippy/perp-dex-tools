"""
Monitoring handlers for Telegram bot (status, positions, close)
"""

import httpx
from typing import Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler

from telegram_bot_service.handlers.base import BaseHandler
from telegram_bot_service.utils.api_client import ControlAPIClient


class MonitoringHandler(BaseHandler):
    """Handler for monitoring-related commands (positions, close)"""
    
    async def _get_control_api_port_for_position(self, position_id: str) -> Optional[int]:
        """
        Get the control API port for the strategy that owns this position.
        
        Args:
            position_id: Position UUID
            
        Returns:
            Control API port number, or None if not found
        """
        try:
            # Get account_id from position
            position_row = await self.database.fetch_one("""
                SELECT account_id::text as account_id
                FROM strategy_positions
                WHERE id = :position_id
            """, {"position_id": position_id})
            
            if not position_row:
                self.logger.warning(f"Position {position_id} not found in database")
                return None
            
            account_id = position_row['account_id']
            
            # Find running strategy for this account
            strategy_row = await self.database.fetch_one("""
                SELECT control_api_port
                FROM strategy_runs
                WHERE account_id::text = :account_id
                  AND status IN ('starting', 'running', 'paused')
                ORDER BY started_at DESC
                LIMIT 1
            """, {"account_id": account_id})
            
            if strategy_row:
                port = strategy_row['control_api_port']
                self.logger.info(f"Found control API port {port} for position {position_id} (account {account_id})")
                return port
            else:
                self.logger.warning(
                    f"No running strategy found for account {account_id} (position {position_id}). "
                    f"Falling back to default port {self.config.control_api_port}"
                )
                return None
                
        except Exception as e:
            self.logger.error(f"Error getting control API port for position {position_id}: {e}")
            return None
    
    async def positions_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /positions command - shows summary with interactive buttons."""
        user, api_key = await self.require_auth(update, context)
        if not user or not api_key:
            return
        
        # Optional account filter
        account_name = context.args[0] if context.args and len(context.args) > 0 else None
        
        try:
            client = ControlAPIClient(self.config.control_api_base_url, api_key)
            data = await client.get_positions(account_name=account_name)
            
            accounts = data.get('accounts', [])
            
            # Collect all positions for button creation
            all_positions = []
            accounts_with_positions = []
            for account in accounts:
                positions = account.get('positions', [])
                if positions:
                    accounts_with_positions.append(account)
                    for pos in positions:
                        all_positions.append({
                            'position': pos,
                            'account_name': account.get('account_name', 'N/A')
                        })
            
            # Format and send summary (may need to split if too long)
            message = self.formatter.format_positions(data)
            
            # Split if needed (TelegramFormatter handles this)
            messages = self.formatter._split_message(message) if len(message) > self.formatter.MAX_MESSAGE_LENGTH else [message]
            
            # Send all summary messages except the last one
            for msg in messages[:-1]:
                await update.message.reply_text(msg, parse_mode='HTML')
            
            # For the last message, add interactive buttons if there are positions
            last_message = messages[-1] if messages else ""
            
            if all_positions:
                # Create keyboard with position buttons
                keyboard = []
                
                # If multiple accounts, show account selection buttons first
                if not account_name and len(accounts_with_positions) > 1:
                    for account in accounts_with_positions:
                        account_name_btn = account.get('account_name', 'N/A')
                        position_count = len(account.get('positions', []))
                        button_label = f"📊 {account_name_btn} ({position_count} position{'s' if position_count != 1 else ''})"
                        callback_data = f"positions_account:{account_name_btn}"
                        keyboard.append([InlineKeyboardButton(button_label, callback_data=callback_data)])
                
                # Add refresh button (before close buttons)
                refresh_callback = f"positions_refresh:{account_name}" if account_name else "positions_refresh:"
                keyboard.append([InlineKeyboardButton("🔄 Refresh", callback_data=refresh_callback)])
                
                # Add direct position selection buttons
                position_index = 0
                for account in accounts_with_positions:
                    positions = account.get('positions', [])
                    for pos in positions:
                        position_index += 1
                        symbol = pos.get('symbol', 'N/A')
                        long_dex = pos.get('long_dex', 'N/A').upper()
                        short_dex = pos.get('short_dex', 'N/A').upper()
                        position_id = pos.get('id', '')
                        
                        # Button label: "❌ Close: 1. PROVE (PARADEX/LIGHTER)" - clearly indicates closing action
                        button_label = f"❌ Close: {position_index}. {symbol} ({long_dex}/{short_dex})"
                        # Store position_id in callback data
                        callback_data = f"close_pos:{position_id}"
                        
                        keyboard.append([InlineKeyboardButton(button_label, callback_data=callback_data)])
                
                reply_markup = InlineKeyboardMarkup(keyboard)
                await update.message.reply_text(
                    last_message,
                    parse_mode='HTML',
                    reply_markup=reply_markup
                )
            else:
                # No positions, but still add refresh button
                keyboard = [[InlineKeyboardButton("🔄 Refresh", callback_data="positions_refresh:")]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await update.message.reply_text(
                    last_message,
                    parse_mode='HTML',
                    reply_markup=reply_markup
                )
                
        except Exception as e:
            self.logger.error(f"Positions error: {e}")
            
            # Handle connection errors with helpful message
            error_str = str(e).lower()
            is_connection_error = (
                isinstance(e, (httpx.ConnectError, httpx.ConnectTimeout, httpx.NetworkError)) or
                "connection" in error_str or
                "all connection attempts failed" in error_str
            )
            
            if is_connection_error:
                await update.message.reply_text(
                    "📊 <b>Control API Not Available</b>\n\n"
                    "Cannot connect to the control API server.\n\n"
                    "The control API server can run independently of strategies.\n"
                    "Start it with: <code>python scripts/start_control_api.py</code>",
                    parse_mode='HTML'
                )
            else:
                # Real error - show it
                await update.message.reply_text(
                    self.formatter.format_error(f"Failed to get positions: {str(e)}"),
                    parse_mode='HTML'
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
            
            # Format positions summary for this account
            message = self.formatter.format_positions(data)
            
            # Get positions for button creation
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
                await query.edit_message_text(
                    f"📊 <b>{account_name}</b>\n\nNo active positions.",
                    parse_mode='HTML'
                )
                return
            
            # Create keyboard with position buttons
            keyboard = []
            
            # Add refresh button (before close buttons)
            keyboard.append([InlineKeyboardButton("🔄 Refresh", callback_data=f"positions_refresh:{account_name}")])
            
            position_index = 0
            for account in accounts:
                positions = account.get('positions', [])
                for pos in positions:
                    position_index += 1
                    symbol = pos.get('symbol', 'N/A')
                    long_dex = pos.get('long_dex', 'N/A').upper()
                    short_dex = pos.get('short_dex', 'N/A').upper()
                    position_id = pos.get('id', '')
                    
                    # Button label: "❌ Close: 1. PROVE (PARADEX/LIGHTER)" - clearly indicates closing action
                    button_label = f"❌ Close: {position_index}. {symbol} ({long_dex}/{short_dex})"
                    callback_data = f"close_pos:{position_id}"
                    
                    keyboard.append([InlineKeyboardButton(button_label, callback_data=callback_data)])
            
            # Add back button
            keyboard.append([InlineKeyboardButton("🔙 Back to All Positions", callback_data="positions_back_to_all")])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            # Split message if needed
            messages = self.formatter._split_message(message) if len(message) > self.formatter.MAX_MESSAGE_LENGTH else [message]
            
            # Edit with first message, then send rest as new messages
            await query.edit_message_text(
                messages[0],
                parse_mode='HTML',
                reply_markup=reply_markup
            )
            
            # Send remaining messages if any
            for msg in messages[1:]:
                await query.message.reply_text(msg, parse_mode='HTML')
            
        except Exception as e:
            self.logger.error(f"Positions account callback error: {e}")
            
            # Handle edge case: no running strategies + connection error = no positions
            error_str = str(e).lower()
            is_connection_error = (
                isinstance(e, (httpx.ConnectError, httpx.ConnectTimeout, httpx.NetworkError)) or
                "connection" in error_str or
                "all connection attempts failed" in error_str
            )
            
            if is_connection_error:
                await query.edit_message_text(
                    "📊 <b>Control API Not Available</b>\n\n"
                    "Cannot connect to the control API server.\n\n"
                    "The control API server can run independently of strategies.\n"
                    "Start it with: <code>python scripts/start_control_api.py</code>",
                    parse_mode='HTML'
                )
            else:
                # Real error - show it
                await query.edit_message_text(
                    self.formatter.format_error(f"Failed to get positions: {str(e)}"),
                    parse_mode='HTML'
                )
    
    async def positions_refresh_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle refresh callback - refreshes positions data in place."""
        query = update.callback_query
        await query.answer()
        
        # Parse callback data: "positions_refresh:{account_name}" or "positions_refresh:"
        callback_data = query.data
        if not callback_data.startswith("positions_refresh:"):
            await query.edit_message_text(
                "❌ Invalid selection. Please use /positions again.",
                parse_mode='HTML'
            )
            return
        
        parts = callback_data.split(":", 1)
        account_name = parts[1] if len(parts) > 1 and parts[1] else None
        
        self.logger.info(f"Refreshing positions data (account: {account_name or 'all'})")
        
        # Get user and API key
        user, api_key = await self.require_auth(update, context)
        if not user or not api_key:
            return
        
        try:
            self.logger.debug(f"Fetching fresh positions data from API (account: {account_name or 'all'})")
            client = ControlAPIClient(self.config.control_api_base_url, api_key)
            data = await client.get_positions(account_name=account_name)
            
            self.logger.debug(f"Received positions data: {len(data.get('accounts', []))} accounts")
            accounts = data.get('accounts', [])
            
            # Collect all positions for button creation
            all_positions = []
            accounts_with_positions = []
            for account in accounts:
                positions = account.get('positions', [])
                if positions:
                    accounts_with_positions.append(account)
                    for pos in positions:
                        all_positions.append({
                            'position': pos,
                            'account_name': account.get('account_name', 'N/A')
                        })
            
            self.logger.debug(f"Formatting positions: {len(all_positions)} total positions")
            # Format positions summary
            message = self.formatter.format_positions(data)
            
            # Split if needed
            messages = self.formatter._split_message(message) if len(message) > self.formatter.MAX_MESSAGE_LENGTH else [message]
            
            # Create keyboard
            keyboard = []
            if not account_name and len(accounts_with_positions) > 1:
                for account in accounts_with_positions:
                    account_name_btn = account.get('account_name', 'N/A')
                    position_count = len(account.get('positions', []))
                    button_label = f"📊 {account_name_btn} ({position_count} position{'s' if position_count != 1 else ''})"
                    callback_data = f"positions_account:{account_name_btn}"
                    keyboard.append([InlineKeyboardButton(button_label, callback_data=callback_data)])
            
            # Add refresh button (before close buttons)
            refresh_callback = f"positions_refresh:{account_name}" if account_name else "positions_refresh:"
            keyboard.append([InlineKeyboardButton("🔄 Refresh", callback_data=refresh_callback)])
            
            # Add direct position selection buttons
            position_index = 0
            for account in accounts_with_positions:
                positions = account.get('positions', [])
                for pos in positions:
                    position_index += 1
                    symbol = pos.get('symbol', 'N/A')
                    long_dex = pos.get('long_dex', 'N/A').upper()
                    short_dex = pos.get('short_dex', 'N/A').upper()
                    position_id = pos.get('id', '')
                    
                    button_label = f"❌ Close: {position_index}. {symbol} ({long_dex}/{short_dex})"
                    callback_data = f"close_pos:{position_id}"
                    keyboard.append([InlineKeyboardButton(button_label, callback_data=callback_data)])
            
            # Add back button if viewing specific account
            if account_name:
                keyboard.append([InlineKeyboardButton("🔙 Back to All Positions", callback_data="positions_back_to_all")])
            
            reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
            
            # Handle "Message is not modified" error gracefully
            from telegram.error import BadRequest
            try:
                await query.edit_message_text(
                    messages[0],
                    parse_mode='HTML',
                    reply_markup=reply_markup
                )
            except BadRequest as e:
                if "Message is not modified" in str(e):
                    await query.answer("✅ Positions are up to date", show_alert=False)
                    return
                else:
                    raise
            
            # Send remaining messages if any
            for msg in messages[1:]:
                await query.message.reply_text(msg, parse_mode='HTML')
            
        except Exception as e:
            self.logger.error(f"Positions refresh error: {e}")
            
            # Handle edge case: no running strategies + connection error = no positions
            error_str = str(e).lower()
            is_connection_error = (
                isinstance(e, (httpx.ConnectError, httpx.ConnectTimeout, httpx.NetworkError)) or
                "connection" in error_str or
                "all connection attempts failed" in error_str
            )
            
            if not has_running_strategies and is_connection_error:
                await query.edit_message_text(
                    "📊 <b>No Active Positions</b>\n\n"
                    "No positions found. All strategies are currently paused.",
                    parse_mode='HTML'
                )
            else:
                await query.edit_message_text(
                    self.formatter.format_error(f"Failed to refresh positions: {str(e)}"),
                    parse_mode='HTML'
                )
    
    async def positions_back_to_all_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle back button - return to all positions view."""
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
            
            # Collect all positions for button creation
            all_positions = []
            accounts_with_positions = []
            for account in accounts:
                positions = account.get('positions', [])
                if positions:
                    accounts_with_positions.append(account)
                    for pos in positions:
                        all_positions.append({
                            'position': pos,
                            'account_name': account.get('account_name', 'N/A')
                        })
            
            # Format positions summary
            message = self.formatter.format_positions(data)
            
            # Split if needed
            messages = self.formatter._split_message(message) if len(message) > self.formatter.MAX_MESSAGE_LENGTH else [message]
            
            # Create keyboard
            keyboard = []
            if len(accounts_with_positions) > 1:
                for account in accounts_with_positions:
                    account_name_btn = account.get('account_name', 'N/A')
                    position_count = len(account.get('positions', []))
                    button_label = f"📊 {account_name_btn} ({position_count} position{'s' if position_count != 1 else ''})"
                    callback_data = f"positions_account:{account_name_btn}"
                    keyboard.append([InlineKeyboardButton(button_label, callback_data=callback_data)])
            
            # Add refresh button (before close buttons)
            keyboard.append([InlineKeyboardButton("🔄 Refresh", callback_data="positions_refresh:")])
            
            # Add direct position selection buttons
            position_index = 0
            for account in accounts_with_positions:
                positions = account.get('positions', [])
                for pos in positions:
                    position_index += 1
                    symbol = pos.get('symbol', 'N/A')
                    long_dex = pos.get('long_dex', 'N/A').upper()
                    short_dex = pos.get('short_dex', 'N/A').upper()
                    position_id = pos.get('id', '')
                    
                    # Button label: "❌ Close: 1. PROVE (PARADEX/LIGHTER)" - clearly indicates closing action
                    button_label = f"❌ Close: {position_index}. {symbol} ({long_dex}/{short_dex})"
                    callback_data = f"close_pos:{position_id}"
                    
                    keyboard.append([InlineKeyboardButton(button_label, callback_data=callback_data)])
            
            reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
            
            # Edit with first message
            await query.edit_message_text(
                messages[0],
                parse_mode='HTML',
                reply_markup=reply_markup
            )
            
            # Send remaining messages if any
            for msg in messages[1:]:
                await query.message.reply_text(msg, parse_mode='HTML')
            
        except Exception as e:
            self.logger.error(f"Back to all positions error: {e}")
            
            # Handle edge case: no running strategies + connection error = no positions
            error_str = str(e).lower()
            is_connection_error = (
                isinstance(e, (httpx.ConnectError, httpx.ConnectTimeout, httpx.NetworkError)) or
                "connection" in error_str or
                "all connection attempts failed" in error_str
            )
            
            if not has_running_strategies and is_connection_error:
                # No running strategies and connection failed - treat as no positions
                await query.edit_message_text(
                    "📊 <b>No Active Positions</b>\n\n"
                    "No positions found. All strategies are currently paused.",
                    parse_mode='HTML'
                )
            else:
                # Real error - show it
                await query.edit_message_text(
                    self.formatter.format_error(f"Failed to get positions: {str(e)}"),
                    parse_mode='HTML'
                )
    
    async def balances_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /balances command - shows available margin balances across exchanges."""
        user, api_key = await self.require_auth(update, context)
        if not user or not api_key:
            return
        
        # Optional account filter
        account_name = context.args[0] if context.args and len(context.args) > 0 else None
        
        # Send initial loading message
        loading_message = await update.message.reply_text(
            "💰 <b>Fetching balances...</b>\n\n"
            "This may take a few seconds as we connect to each exchange.",
            parse_mode='HTML'
        )
        
        try:
            client = ControlAPIClient(self.config.control_api_base_url, api_key)
            data = await client.get_balances(account_name=account_name)
            
            # Format balances
            message = self.formatter.format_balances(data)
            
            # Split if needed (TelegramFormatter handles this)
            messages = self.formatter._split_message(message) if len(message) > self.formatter.MAX_MESSAGE_LENGTH else [message]
            
            # Replace loading message with first result message
            await loading_message.edit_text(messages[0], parse_mode='HTML')
            
            # Send remaining messages if any
            for msg in messages[1:]:
                await update.message.reply_text(msg, parse_mode='HTML')
                
        except Exception as e:
            self.logger.error(f"Balances command error: {e}")
            
            # Handle connection errors with helpful message
            error_str = str(e).lower()
            is_connection_error = (
                isinstance(e, (httpx.ConnectError, httpx.ConnectTimeout, httpx.NetworkError)) or
                "connection" in error_str or
                "all connection attempts failed" in error_str
            )
            
            if is_connection_error:
                await loading_message.edit_text(
                    "💰 <b>Control API Not Available</b>\n\n"
                    "Cannot connect to the control API server.\n\n"
                    "The control API server can run independently of strategies.\n"
                    "Start it with: <code>python scripts/start_control_api.py</code>",
                    parse_mode='HTML'
                )
            else:
                # Real error - show it
                await loading_message.edit_text(
                    self.formatter.format_error(f"Failed to get balances: {str(e)}"),
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
                    
                    # Button label: "❌ Close: 1. PROVE (PARADEX/LIGHTER)" - clearly indicates closing action
                    button_label = f"❌ Close: {position_index}. {symbol} ({long_dex}/{short_dex})"
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
                "❌ Invalid selection. Please use /positions again.",
                parse_mode='HTML'
            )
            return
        
        position_id = callback_data.split(":", 1)[1]
        
        # Store position_id in user_data for the next step
        context.user_data['close_position_id'] = position_id
        # Store that we came from positions view (for back button)
        context.user_data['close_from_positions'] = True
        
        # Show order type selection with back button
        keyboard = [
            [
                InlineKeyboardButton("🟢 Market", callback_data=f"close_type:{position_id}:market"),
                InlineKeyboardButton("🟡 Limit", callback_data=f"close_type:{position_id}:limit")
            ],
            [InlineKeyboardButton("🔙 Back to Positions", callback_data="close_back_to_positions")],
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
    
    async def close_back_to_positions_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle back button from order type selection - return to positions view."""
        query = update.callback_query
        await query.answer()
        
        # Clear close-related user_data
        context.user_data.pop('close_position_id', None)
        context.user_data.pop('close_from_positions', None)
        
        # Get user and API key
        user, api_key = await self.require_auth(update, context)
        if not user or not api_key:
            return
        
        try:
            client = ControlAPIClient(self.config.control_api_base_url, api_key)
            data = await client.get_positions(account_name=None)
            
            accounts = data.get('accounts', [])
            
            # Collect all positions for button creation
            all_positions = []
            accounts_with_positions = []
            for account in accounts:
                positions = account.get('positions', [])
                if positions:
                    accounts_with_positions.append(account)
                    for pos in positions:
                        all_positions.append({
                            'position': pos,
                            'account_name': account.get('account_name', 'N/A')
                        })
            
            # Format positions summary
            message = self.formatter.format_positions(data)
            
            # Split if needed
            messages = self.formatter._split_message(message) if len(message) > self.formatter.MAX_MESSAGE_LENGTH else [message]
            
            # Create keyboard
            keyboard = []
            if len(accounts_with_positions) > 1:
                for account in accounts_with_positions:
                    account_name_btn = account.get('account_name', 'N/A')
                    position_count = len(account.get('positions', []))
                    button_label = f"📊 {account_name_btn} ({position_count} position{'s' if position_count != 1 else ''})"
                    callback_data = f"positions_account:{account_name_btn}"
                    keyboard.append([InlineKeyboardButton(button_label, callback_data=callback_data)])
            
            # Add direct position selection buttons
            position_index = 0
            for account in accounts_with_positions:
                positions = account.get('positions', [])
                for pos in positions:
                    position_index += 1
                    symbol = pos.get('symbol', 'N/A')
                    long_dex = pos.get('long_dex', 'N/A').upper()
                    short_dex = pos.get('short_dex', 'N/A').upper()
                    position_id = pos.get('id', '')
                    
                    # Button label: "❌ Close: 1. PROVE (PARADEX/LIGHTER)" - clearly indicates closing action
                    button_label = f"❌ Close: {position_index}. {symbol} ({long_dex}/{short_dex})"
                    callback_data = f"close_pos:{position_id}"
                    
                    keyboard.append([InlineKeyboardButton(button_label, callback_data=callback_data)])
            
            reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
            
            # Edit with first message
            await query.edit_message_text(
                messages[0],
                parse_mode='HTML',
                reply_markup=reply_markup
            )
            
            # Send remaining messages if any
            for msg in messages[1:]:
                await query.message.reply_text(msg, parse_mode='HTML')
            
        except Exception as e:
            self.logger.error(f"Back to positions error: {e}")
            await query.edit_message_text(
                self.formatter.format_error(f"Failed to get positions: {str(e)}"),
                parse_mode='HTML'
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
            context.user_data.pop('close_from_positions', None)
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
            # Get the correct control API port for this position
            port = await self._get_control_api_port_for_position(position_id)
            
            # Construct API URL with correct port
            if port:
                api_url = f"http://{self.config.control_api_host}:{port}"
                self.logger.info(f"Using strategy-specific control API: {api_url}")
            else:
                # No running strategy found - cannot close position via standalone API
                # The standalone control API (port 8766) is read-only and cannot close positions
                await query.edit_message_text(
                    "❌ <b>Cannot Close Position</b>\n\n"
                    "No running strategy found for this position's account.\n\n"
                    "Positions can only be closed when a strategy is running.\n\n"
                    "To close this position:\n"
                    "1. Start a strategy for this account with /run_strategy\n"
                    "2. Or manually close via exchange interface",
                    parse_mode='HTML'
                )
                # Clear user_data
                context.user_data.pop('close_position_id', None)
                return
            
            client = ControlAPIClient(api_url, api_key)
            data = await client.close_position(
                position_id=position_id,
                order_type=order_type,
                reason="telegram_manual_close"
            )
            
            message = self.formatter.format_close_result(data)
            await query.edit_message_text(message, parse_mode='HTML')
            
            # Clear user_data
            context.user_data.pop('close_position_id', None)
            
        except httpx.HTTPStatusError as e:
            # Handle HTTP errors (e.g., 403, 500 from control API)
            error_msg = str(e)
            if e.response.status_code == 403:
                error_msg = "Permission denied. The position may not belong to your account."
            elif e.response.status_code == 500:
                # Try to get more details from response
                try:
                    error_data = e.response.json()
                    error_detail = error_data.get('detail', error_msg)
                    if 'read-only mode' in error_detail.lower() or 'strategy controller' in error_detail.lower():
                        error_msg = (
                            "Cannot close position: No running strategy found.\n\n"
                            "Start a strategy for this account with /run_strategy to enable position closing."
                        )
                    else:
                        error_msg = f"Server error: {error_detail}"
                except Exception:
                    pass
            
            self.logger.error(f"Close error: {e}")
            await query.edit_message_text(
                self.formatter.format_error(f"Failed to close position: {error_msg}"),
                parse_mode='HTML'
            )
            # Clear user_data
            context.user_data.pop('close_position_id', None)
            
        except Exception as e:
            self.logger.error(f"Close error: {e}")
            await query.edit_message_text(
                self.formatter.format_error(f"Failed to close position: {str(e)}"),
                parse_mode='HTML'
            )
            # Clear user_data
            context.user_data.pop('close_position_id', None)
    
    def register_handlers(self, application):
        """Register monitoring command and callback handlers"""
        # Note: /status removed - use /list_strategies instead for strategy status
        application.add_handler(CommandHandler("positions", self.positions_command))
        application.add_handler(CommandHandler("balances", self.balances_command))
        application.add_handler(CommandHandler("close", self.close_command))
        
        # Callback query handlers for interactive flows
        # Order matters: more specific patterns first
        application.add_handler(CallbackQueryHandler(
            self.positions_account_callback,
            pattern="^positions_account:"
        ))
        application.add_handler(CallbackQueryHandler(
            self.positions_refresh_callback,
            pattern="^positions_refresh:"
        ))
        application.add_handler(CallbackQueryHandler(
            self.positions_back_to_all_callback,
            pattern="^positions_back_to_all$"
        ))
        application.add_handler(CallbackQueryHandler(
            self.close_back_to_positions_callback,
            pattern="^close_back_to_positions$"
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

