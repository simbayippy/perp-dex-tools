"""
Account management handlers for Telegram bot
"""

import json
import uuid
from typing import Dict, Any
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler

from telegram_bot_service.handlers.base import BaseHandler
from database.scripts.proxy_utils import upsert_proxy, assign_proxy


class AccountHandler(BaseHandler):
    """Handler for account management commands, callbacks, and wizards"""
    
    async def list_accounts_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /list_accounts command."""
        user, _ = await self.require_auth(update, context)
        if not user:
            return
        
        try:
            query = """
                SELECT a.id, a.account_name, a.is_active, a.created_at,
                       COUNT(DISTINCT aec.exchange_id) as exchange_count,
                       COUNT(DISTINCT apa.proxy_id) as proxy_count
                FROM accounts a
                LEFT JOIN account_exchange_credentials aec ON a.id = aec.account_id AND aec.is_active = TRUE
                LEFT JOIN account_proxy_assignments apa ON a.id = apa.account_id AND apa.status = 'active'
                WHERE a.user_id = :user_id
                GROUP BY a.id, a.account_name, a.is_active, a.created_at
                ORDER BY a.account_name
            """
            rows = await self.database.fetch_all(query, {"user_id": user["id"]})
            
            if not rows:
                await update.message.reply_text(
                    "📊 <b>No accounts found</b>\n\n"
                    "Create your first account with /create_account",
                    parse_mode='HTML'
                )
                return
            
            message = "📊 <b>Your Accounts</b>\n\n"
            keyboard = []
            
            for row in rows:
                status_emoji = "🟢" if row["is_active"] else "⚫"
                account_id = str(row["id"])
                account_name = row["account_name"]
                message += (
                    f"{status_emoji} <b>{account_name}</b>\n"
                    f"   Exchanges: {row['exchange_count']}\n"
                    f"   Proxies: {row['proxy_count']}\n\n"
                )
                
                # Add edit and delete buttons for each account
                keyboard.append([
                    InlineKeyboardButton(
                        f"✏️ {account_name}",
                        callback_data=f"edit_account_btn:{account_id}"
                    ),
                    InlineKeyboardButton(
                        f"🗑️ {account_name}",
                        callback_data=f"delete_account_btn:{account_id}"
                    )
                ])
            
            reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
            
            await update.message.reply_text(
                message,
                parse_mode='HTML',
                reply_markup=reply_markup
            )
            
        except Exception as e:
            self.logger.error(f"List accounts error: {e}")
            await update.message.reply_text(
                f"❌ Failed to list accounts: {str(e)}",
                parse_mode='HTML'
            )
    
    async def create_account_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /create_account command - starts wizard."""
        user, _ = await self.require_auth(update, context)
        if not user:
            return
        
        # Start wizard
        context.user_data['wizard'] = {
            'type': 'create_account',
            'step': 1,
            'data': {}
        }
        
        await update.message.reply_text(
            "📝 <b>Create New Account</b>\n\n"
            "Step 1/4: Enter account name:\n"
            "(e.g., 'my_trading_account')",
            parse_mode='HTML'
        )
    
    async def add_exchange_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /add_exchange command - interactive account selection."""
        user, _ = await self.require_auth(update, context)
        if not user:
            return
        
        # Get user's accounts
        query = """
            SELECT id, account_name
            FROM accounts
            WHERE user_id = :user_id AND is_active = TRUE
            ORDER BY account_name
        """
        accounts = await self.database.fetch_all(query, {"user_id": str(user["id"])})
        
        if not accounts:
            await update.message.reply_text(
                "❌ No accounts found. Create an account first with /create_account",
                parse_mode='HTML'
            )
            return
        
        # Create account selection keyboard
        keyboard = []
        for acc in accounts:
            keyboard.append([InlineKeyboardButton(
                acc['account_name'],
                callback_data=f"add_exchange_account:{acc['id']}"
            )])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "🔐 <b>Add Exchange Credentials</b>\n\n"
            "Step 1/2: Select account:",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
    
    async def add_proxy_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /add_proxy command - interactive account selection."""
        user, _ = await self.require_auth(update, context)
        if not user:
            return
        
        # Get user's accounts
        query = """
            SELECT id, account_name
            FROM accounts
            WHERE user_id = :user_id AND is_active = TRUE
            ORDER BY account_name
        """
        accounts = await self.database.fetch_all(query, {"user_id": str(user["id"])})
        
        if not accounts:
            await update.message.reply_text(
                "❌ No accounts found. Create an account first with /create_account",
                parse_mode='HTML'
            )
            return
        
        # Create account selection keyboard
        keyboard = []
        for acc in accounts:
            keyboard.append([InlineKeyboardButton(
                acc['account_name'],
                callback_data=f"add_proxy_account:{acc['id']}"
            )])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "🌐 <b>Add Proxy</b>\n\n"
            "Select account:",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
    
    # ========================================================================
    # Account Callbacks
    # ========================================================================
    
    async def edit_account_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle edit account button click."""
        query = update.callback_query
        await query.answer()
        
        try:
            callback_data = query.data
            account_id = callback_data.split(":", 1)[1]
            
            # Get account details
            account_row = await self.database.fetch_one(
                """
                SELECT account_name, description, is_active
                FROM accounts
                WHERE id = :id
                """,
                {"id": account_id}
            )
            
            if not account_row:
                await query.edit_message_text(
                    "❌ Account not found.",
                    parse_mode='HTML'
                )
                return
            
            account_name = account_row['account_name']
            
            # Show edit options with inline buttons
            keyboard = [
                [InlineKeyboardButton("✏️ Edit Name", callback_data=f"edit_acc_name:{account_id}")],
                [InlineKeyboardButton("📝 Edit Description", callback_data=f"edit_acc_desc:{account_id}")],
                [InlineKeyboardButton("🔐 Add Exchange", callback_data=f"edit_acc_exchange:{account_id}")],
                [InlineKeyboardButton("🌐 Add Proxy", callback_data=f"edit_acc_proxy:{account_id}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                f"✏️ <b>Edit Account: {account_name}</b>\n\n"
                "What would you like to edit?",
                parse_mode='HTML',
                reply_markup=reply_markup
            )
        except Exception as e:
            self.logger.error(f"Error in edit_account_callback: {e}", exc_info=True)
            try:
                await query.edit_message_text(
                    f"❌ Error: {str(e)}\n\nPlease try again.",
                    parse_mode='HTML'
                )
            except:
                await query.message.reply_text(
                    f"❌ Error: {str(e)}\n\nPlease try again.",
                    parse_mode='HTML'
                )
    
    async def delete_account_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle delete account button click - show confirmation."""
        query = update.callback_query
        await query.answer()
        
        try:
            callback_data = query.data
            account_id = callback_data.split(":", 1)[1]
            
            # Get account name
            account_row = await self.database.fetch_one(
                "SELECT account_name FROM accounts WHERE id = :id",
                {"id": account_id}
            )
            
            if not account_row:
                await query.edit_message_text(
                    "❌ Account not found.",
                    parse_mode='HTML'
                )
                return
            
            account_name = account_row['account_name']
            
            # Check if account has running strategies
            running_strategies = await self.database.fetch_all(
                """
                SELECT COUNT(*) as count
                FROM strategy_runs
                WHERE account_id = :account_id AND status IN ('running', 'starting', 'paused')
                """,
                {"account_id": account_id}
            )
            
            has_running = running_strategies[0]['count'] > 0 if running_strategies else False
            
            if has_running:
                await query.edit_message_text(
                    f"⚠️ <b>Cannot Delete Account</b>\n\n"
                    f"Account <b>{account_name}</b> has running strategies.\n"
                    f"Please stop all strategies first before deleting.",
                    parse_mode='HTML'
                )
                return
            
            # Show confirmation
            keyboard = [
                [
                    InlineKeyboardButton("✅ Yes, Delete", callback_data=f"delete_account_confirm:{account_id}"),
                    InlineKeyboardButton("❌ Cancel", callback_data=f"delete_account_cancel:{account_id}")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                f"⚠️ <b>Delete Account?</b>\n\n"
                f"Account: <b>{account_name}</b>\n\n"
                f"This will permanently delete:\n"
                f"• Account settings\n"
                f"• Exchange credentials\n"
                f"• Proxy assignments\n\n"
                f"<b>This action cannot be undone!</b>\n\n"
                f"Are you sure you want to delete this account?",
                parse_mode='HTML',
                reply_markup=reply_markup
            )
        except Exception as e:
            self.logger.error(f"Error in delete_account_callback: {e}", exc_info=True)
            try:
                await query.edit_message_text(
                    f"❌ Error: {str(e)}\n\nPlease try again.",
                    parse_mode='HTML'
                )
            except:
                await query.message.reply_text(
                    f"❌ Error: {str(e)}\n\nPlease try again.",
                    parse_mode='HTML'
                )
    
    async def delete_account_confirm_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle account deletion confirmation."""
        query = update.callback_query
        await query.answer()
        
        try:
            callback_data = query.data
            account_id = callback_data.split(":", 1)[1]
            
            telegram_user_id = query.from_user.id
            user = await self.auth.get_user_by_telegram_id(telegram_user_id)
            
            # Get account name before deletion
            account_row = await self.database.fetch_one(
                "SELECT account_name FROM accounts WHERE id = :id AND user_id = :user_id",
                {"id": account_id, "user_id": str(user["id"])}
            )
            
            if not account_row:
                await query.edit_message_text(
                    "❌ Account not found or you don't have permission to delete it.",
                    parse_mode='HTML'
                )
                return
            
            account_name = account_row['account_name']
            
            # Delete account (CASCADE will handle related records)
            await self.database.execute(
                "DELETE FROM accounts WHERE id = :id AND user_id = :user_id",
                {"id": account_id, "user_id": str(user["id"])}
            )
            
            await self.audit_logger.log_action(
                str(user["id"]),
                "delete_account",
                {"account_id": account_id, "account_name": account_name}
            )
            
            await query.edit_message_text(
                f"✅ Account <b>{account_name}</b> deleted successfully.",
                parse_mode='HTML'
            )
        except Exception as e:
            self.logger.error(f"Error in delete_account_confirm_callback: {e}", exc_info=True)
            await query.edit_message_text(
                f"❌ Failed to delete account: {str(e)}",
                parse_mode='HTML'
            )
    
    async def delete_account_cancel_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle account deletion cancellation."""
        query = update.callback_query
        await query.answer("Deletion cancelled.")
        
        await query.edit_message_text(
            "❌ Account deletion cancelled.",
            parse_mode='HTML'
        )
    
    async def edit_account_name_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle edit account name button click."""
        query = update.callback_query
        await query.answer()
        
        try:
            callback_data = query.data
            account_id = callback_data.split(":", 1)[1]
            
            # Get account name
            account_row = await self.database.fetch_one(
                "SELECT account_name FROM accounts WHERE id = :id",
                {"id": account_id}
            )
            
            if not account_row:
                await query.edit_message_text(
                    "❌ Account not found.",
                    parse_mode='HTML'
                )
                return
            
            # Start wizard for name edit
            context.user_data['wizard'] = {
                'type': 'edit_account',
                'step': 2,
                'data': {
                    'account_id': account_id,
                    'account_name': account_row['account_name'],
                    'edit_type': 'name'
                }
            }
            
            await query.edit_message_text(
                f"✏️ <b>Edit Account Name</b>\n\n"
                f"Current name: <b>{account_row['account_name']}</b>\n\n"
                "Enter new account name:",
                parse_mode='HTML'
            )
        except Exception as e:
            self.logger.error(f"Error in edit_account_name_callback: {e}", exc_info=True)
            try:
                await query.edit_message_text(
                    f"❌ Error: {str(e)}\n\nPlease try again.",
                    parse_mode='HTML'
                )
            except:
                await query.message.reply_text(
                    f"❌ Error: {str(e)}\n\nPlease try again.",
                    parse_mode='HTML'
                )
    
    async def edit_account_description_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle edit account description button click."""
        query = update.callback_query
        await query.answer()
        
        try:
            callback_data = query.data
            account_id = callback_data.split(":", 1)[1]
            
            # Get account details
            account_row = await self.database.fetch_one(
                "SELECT account_name, description FROM accounts WHERE id = :id",
                {"id": account_id}
            )
            
            if not account_row:
                await query.edit_message_text(
                    "❌ Account not found.",
                    parse_mode='HTML'
                )
                return
            
            # Start wizard for description edit
            context.user_data['wizard'] = {
                'type': 'edit_account',
                'step': 2,
                'data': {
                    'account_id': account_id,
                    'account_name': account_row['account_name'],
                    'edit_type': 'description'
                }
            }
            
            current_desc = account_row['description'] or "None"
            
            await query.edit_message_text(
                f"📝 <b>Edit Account Description</b>\n\n"
                f"Account: <b>{account_row['account_name']}</b>\n"
                f"Current description: {current_desc}\n\n"
                "Enter new description (or 'none' to remove):",
                parse_mode='HTML'
            )
        except Exception as e:
            self.logger.error(f"Error in edit_account_description_callback: {e}", exc_info=True)
            try:
                await query.edit_message_text(
                    f"❌ Error: {str(e)}\n\nPlease try again.",
                    parse_mode='HTML'
                )
            except:
                await query.message.reply_text(
                    f"❌ Error: {str(e)}\n\nPlease try again.",
                    parse_mode='HTML'
                )
    
    async def edit_account_add_exchange_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle add exchange from edit account menu."""
        query = update.callback_query
        await query.answer()
        
        try:
            callback_data = query.data
            account_id = callback_data.split(":", 1)[1]
            
            # Store account_id for add_exchange flow
            context.user_data['add_exchange_account_id'] = account_id
            
            # Get account name
            account_row = await self.database.fetch_one(
                "SELECT account_name FROM accounts WHERE id = :id",
                {"id": account_id}
            )
            
            if not account_row:
                await query.edit_message_text(
                    "❌ Account not found.",
                    parse_mode='HTML'
                )
                return
            
            # Show exchange selection
            keyboard = [
                [InlineKeyboardButton("🔵 Lighter", callback_data="add_exc_ex:lighter")],
                [InlineKeyboardButton("🟢 Aster", callback_data="add_exc_ex:aster")],
                [InlineKeyboardButton("🟣 Backpack", callback_data="add_exc_ex:backpack")],
                [InlineKeyboardButton("🟠 Paradex", callback_data="add_exc_ex:paradex")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                f"🔐 <b>Add Exchange Credentials</b>\n\n"
                f"Account: <b>{account_row['account_name']}</b>\n\n"
                "Select exchange:",
                parse_mode='HTML',
                reply_markup=reply_markup
            )
        except Exception as e:
            self.logger.error(f"Error in edit_account_add_exchange_callback: {e}", exc_info=True)
            try:
                await query.edit_message_text(
                    f"❌ Error: {str(e)}\n\nPlease try again.",
                    parse_mode='HTML'
                )
            except:
                await query.message.reply_text(
                    f"❌ Error: {str(e)}\n\nPlease try again.",
                    parse_mode='HTML'
                )
    
    async def edit_account_add_proxy_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle add proxy from edit account menu."""
        query = update.callback_query
        await query.answer()
        
        try:
            callback_data = query.data
            account_id = callback_data.split(":", 1)[1]
            
            # Get account name
            account_row = await self.database.fetch_one(
                "SELECT account_name FROM accounts WHERE id = :id",
                {"id": account_id}
            )
            
            if not account_row:
                await query.edit_message_text(
                    "❌ Account not found.",
                    parse_mode='HTML'
                )
                return
            
            account_name = account_row['account_name']
            
            # Start proxy wizard
            context.user_data['wizard'] = {
                'type': 'add_proxy',
                'step': 1,
                'data': {
                    'account_id': account_id,
                    'account_name': account_name
                }
            }
            
            await query.edit_message_text(
                f"🌐 <b>Add Proxy</b>\n\n"
                f"Account: <b>{account_name}</b>\n\n"
                "Step 1/2: Enter proxy URL:\n"
                "Format: <code>socks5://host:port</code>\n"
                "Example: <code>socks5://123.45.67.89:1080</code>",
                parse_mode='HTML'
            )
        except Exception as e:
            self.logger.error(f"Error in edit_account_add_proxy_callback: {e}", exc_info=True)
            try:
                await query.edit_message_text(
                    f"❌ Error: {str(e)}\n\nPlease try again.",
                    parse_mode='HTML'
                )
            except:
                await query.message.reply_text(
                    f"❌ Error: {str(e)}\n\nPlease try again.",
                    parse_mode='HTML'
                )
    
    async def add_exchange_account_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle account selection for add_exchange."""
        query = update.callback_query
        await query.answer()
        
        try:
            callback_data = query.data
            account_id = callback_data.split(":", 1)[1]
            
            # Get account name
            account_row = await self.database.fetch_one(
                "SELECT account_name FROM accounts WHERE id = :id",
                {"id": account_id}
            )
            
            if not account_row:
                await query.edit_message_text(
                    "❌ Account not found. Please try again.",
                    parse_mode='HTML'
                )
                return
            
            account_name = account_row['account_name']
            
            # Store account_id in context for next step (to avoid long callback_data)
            context.user_data['add_exchange_account_id'] = account_id
            
            # Show exchange selection with shorter callback_data (no account_id in callback)
            keyboard = [
                [InlineKeyboardButton("⚡ Lighter", callback_data=f"add_exc_ex:lighter")],
                [InlineKeyboardButton("🌟 Aster", callback_data=f"add_exc_ex:aster")],
                [InlineKeyboardButton("🎒 Backpack", callback_data=f"add_exc_ex:backpack")],
                [InlineKeyboardButton("🎪 Paradex", callback_data=f"add_exc_ex:paradex")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                f"🔐 <b>Add Exchange Credentials</b>\n\n"
                f"Account: <b>{account_name}</b>\n\n"
                "Step 2/2: Select exchange:",
                parse_mode='HTML',
                reply_markup=reply_markup
            )
        except Exception as e:
            self.logger.error(f"Error in add_exchange_account_callback: {e}", exc_info=True)
            try:
                await query.edit_message_text(
                    f"❌ Error: {str(e)}\n\nPlease try again with /add_exchange",
                    parse_mode='HTML'
                )
            except:
                await query.message.reply_text(
                    f"❌ Error: {str(e)}\n\nPlease try again with /add_exchange",
                    parse_mode='HTML'
                )
    
    async def add_exchange_exchange_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle exchange selection for add_exchange."""
        query = update.callback_query
        await query.answer()
        
        try:
            # Get account_id from context (stored in previous step)
            account_id = context.user_data.get('add_exchange_account_id')
            if not account_id:
                await query.edit_message_text(
                    "❌ Session expired. Please start over with /add_exchange",
                    parse_mode='HTML'
                )
                return
            
            callback_data = query.data
            # Format: add_exc_ex:{exchange}
            parts = callback_data.split(":", 1)
            if len(parts) != 2:
                await query.edit_message_text(
                    "❌ Invalid callback data. Please try again with /add_exchange",
                    parse_mode='HTML'
                )
                return
            
            exchange = parts[1]
            
            if exchange not in ['lighter', 'aster', 'backpack', 'paradex']:
                await query.edit_message_text(
                    f"❌ Invalid exchange: {exchange}",
                    parse_mode='HTML'
                )
                return
            
            # Get account name
            account_row = await self.database.fetch_one(
                "SELECT account_name FROM accounts WHERE id = :id",
                {"id": account_id}
            )
            
            if not account_row:
                await query.edit_message_text(
                    "❌ Account not found. Please try again.",
                    parse_mode='HTML'
                )
                return
            
            account_name = account_row['account_name']
            
            # Clear the temporary account_id from context
            context.user_data.pop('add_exchange_account_id', None)
            
            # Start wizard for credentials
            context.user_data['wizard'] = {
                'type': 'add_exchange',
                'step': 1,
                'data': {
                    'account_id': account_id,
                    'account_name': account_name,
                    'exchange': exchange
                }
            }
            
            # Show input mode selection
            keyboard = [
                [InlineKeyboardButton("⚡ Quick Input (JSON)", callback_data=f"add_exc_mode:{exchange}:json")],
                [InlineKeyboardButton("📝 Step-by-Step", callback_data=f"add_exc_mode:{exchange}:interactive")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            # Get required fields for this exchange
            field_descriptions = {
                'lighter': {
                    'fields': ['private_key', 'account_index', 'api_key_index'],
                    'example': '{\n  "private_key": "0x...",\n  "account_index": "0",\n  "api_key_index": "0"\n}'
                },
                'aster': {
                    'fields': ['api_key', 'secret_key'],
                    'example': '{\n  "api_key": "your_api_key",\n  "secret_key": "your_secret_key"\n}'
                },
                'backpack': {
                    'fields': ['public_key', 'secret_key'],
                    'example': '{\n  "public_key": "your_public_key",\n  "secret_key": "your_secret_key"\n}'
                },
                'paradex': {
                    'fields': ['l1_address', 'l2_private_key_hex'],
                    'optional': ['l2_address', 'environment'],
                    'example': '{\n  "l1_address": "0x...",\n  "l2_private_key_hex": "0x...",\n  "l2_address": "0x..." (optional),\n  "environment": "prod" (optional)\n}'
                }
            }
            
            fields_info = field_descriptions.get(exchange, {})
            required_fields = ', '.join(fields_info.get('fields', []))
            optional_fields = fields_info.get('optional', [])
            
            message = (
                f"🔐 <b>Add {exchange.upper()} Credentials</b>\n\n"
                f"Account: <b>{account_name}</b>\n\n"
                f"<b>Required fields:</b> {required_fields}\n"
            )
            
            if optional_fields:
                message += f"<b>Optional:</b> {', '.join(optional_fields)}\n"
            
            message += "\nChoose input method:"
            
            await query.edit_message_text(
                message,
                parse_mode='HTML',
                reply_markup=reply_markup
            )
        except Exception as e:
            self.logger.error(f"Error in add_exchange_exchange_callback: {e}", exc_info=True)
            try:
                await query.edit_message_text(
                    f"❌ Error: {str(e)}\n\nPlease try again with /add_exchange",
                    parse_mode='HTML'
                )
            except:
                await query.message.reply_text(
                    f"❌ Error: {str(e)}\n\nPlease try again with /add_exchange",
                    parse_mode='HTML'
                )
    
    async def add_exchange_mode_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle input mode selection (JSON vs interactive)."""
        query = update.callback_query
        await query.answer()
        
        try:
            callback_data = query.data
            # Format: add_exc_mode:{exchange}:{mode}
            parts = callback_data.split(":", 2)
            if len(parts) != 3:
                await query.edit_message_text(
                    "❌ Invalid callback data. Please try again with /add_exchange",
                    parse_mode='HTML'
                )
                return
            
            exchange = parts[1]
            mode = parts[2]
            
            # Get account_id from context
            account_id = context.user_data.get('add_exchange_account_id')
            if not account_id:
                await query.edit_message_text(
                    "❌ Session expired. Please start over with /add_exchange",
                    parse_mode='HTML'
                )
                return
            
            # Get account name
            account_row = await self.database.fetch_one(
                "SELECT account_name FROM accounts WHERE id = :id",
                {"id": account_id}
            )
            
            if not account_row:
                await query.edit_message_text(
                    "❌ Account not found. Please try again.",
                    parse_mode='HTML'
                )
                return
            
            account_name = account_row['account_name']
            
            # Clear the temporary account_id from context
            context.user_data.pop('add_exchange_account_id', None)
            
            # Start wizard for credentials
            context.user_data['wizard'] = {
                'type': 'add_exchange',
                'step': 1,
                'mode': mode,  # 'json' or 'interactive'
                'data': {
                    'account_id': account_id,
                    'account_name': account_name,
                    'exchange': exchange,
                    'credentials': {}
                }
            }
            
            if mode == 'json':
                # JSON mode - show example and prompt
                field_descriptions = {
                    'lighter': {
                        'example': '{\n  "private_key": "0x...",\n  "account_index": "0",\n  "api_key_index": "0"\n}'
                    },
                    'aster': {
                        'example': '{\n  "api_key": "your_api_key",\n  "secret_key": "your_secret_key"\n}'
                    },
                    'backpack': {
                        'example': '{\n  "public_key": "your_public_key",\n  "secret_key": "your_secret_key"\n}'
                    },
                    'paradex': {
                        'example': '{\n  "l1_address": "0x...",\n  "l2_private_key_hex": "0x...",\n  "l2_address": "0x..." (optional),\n  "environment": "prod" (optional)\n}'
                    }
                }
                
                example = field_descriptions.get(exchange, {}).get('example', '{}')
                
                await query.edit_message_text(
                    f"🔐 <b>Quick Input: {exchange.upper()}</b>\n\n"
                    f"Account: <b>{account_name}</b>\n\n"
                    "Send your credentials as JSON:\n\n"
                    f"<code>{example}</code>\n\n"
                    "You can send it as a single message or multiple lines.\n"
                    "<i>Send 'cancel' to abort.</i>",
                    parse_mode='HTML'
                )
            else:
                # Interactive mode - start with first field
                field_descriptions = {
                    'lighter': {
                        'fields': [
                            ('private_key', 'Enter your private key (0x...):'),
                            ('account_index', 'Enter account index (usually 0):'),
                            ('api_key_index', 'Enter API key index (usually 0):')
                        ]
                    },
                    'aster': {
                        'fields': [
                            ('api_key', 'Enter your API key:'),
                            ('secret_key', 'Enter your secret key:')
                        ]
                    },
                    'backpack': {
                        'fields': [
                            ('public_key', 'Enter your public key:'),
                            ('secret_key', 'Enter your secret key:')
                        ]
                    },
                    'paradex': {
                        'fields': [
                            ('l1_address', 'Enter L1 address (0x...):'),
                            ('l2_private_key_hex', 'Enter L2 private key hex (0x...):'),
                            ('l2_address', 'Enter L2 address (0x..., optional, or send "skip"):'),
                            ('environment', 'Enter environment (prod/staging, default: prod, or send "skip"):')
                        ]
                    }
                }
                
                fields = field_descriptions.get(exchange, {}).get('fields', [])
                if fields:
                    first_field_name, first_field_prompt = fields[0]
                    context.user_data['wizard']['data']['current_field_index'] = 0
                    context.user_data['wizard']['data']['fields'] = fields
                    
                    await query.edit_message_text(
                        f"📝 <b>Step-by-Step: {exchange.upper()}</b>\n\n"
                        f"Account: <b>{account_name}</b>\n\n"
                        f"Step 1/{len(fields)}: {first_field_prompt}\n\n"
                        "<i>Send 'cancel' at any time to abort.</i>",
                        parse_mode='HTML'
                    )
        except Exception as e:
            self.logger.error(f"Error in add_exchange_mode_callback: {e}", exc_info=True)
            try:
                await query.edit_message_text(
                    f"❌ Error: {str(e)}\n\nPlease try again with /add_exchange",
                    parse_mode='HTML'
                )
            except:
                await query.message.reply_text(
                    f"❌ Error: {str(e)}\n\nPlease try again with /add_exchange",
                    parse_mode='HTML'
                )
    
    async def add_proxy_account_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle account selection for add_proxy."""
        query = update.callback_query
        await query.answer()
        
        try:
            callback_data = query.data
            account_id = callback_data.split(":", 1)[1]
            
            # Get account name
            account_row = await self.database.fetch_one(
                "SELECT account_name FROM accounts WHERE id = :id",
                {"id": account_id}
            )
            
            if not account_row:
                await query.edit_message_text(
                    "❌ Account not found. Please try again.",
                    parse_mode='HTML'
                )
                return
            
            account_name = account_row['account_name']
            
            # Start proxy wizard
            context.user_data['wizard'] = {
                'type': 'add_proxy',
                'step': 1,
                'data': {
                    'account_id': account_id,
                    'account_name': account_name
                }
            }
            
            await query.edit_message_text(
                f"🌐 <b>Add Proxy</b>\n\n"
                f"Account: <b>{account_name}</b>\n\n"
                "Step 1/2: Enter proxy URL:\n"
                "Format: <code>socks5://host:port</code>\n"
                "Example: <code>socks5://123.45.67.89:1080</code>",
                parse_mode='HTML'
            )
        except Exception as e:
            self.logger.error(f"Error in add_proxy_account_callback: {e}", exc_info=True)
            try:
                await query.edit_message_text(
                    f"❌ Error: {str(e)}\n\nPlease try again with /add_proxy",
                    parse_mode='HTML'
                )
            except:
                await query.message.reply_text(
                    f"❌ Error: {str(e)}\n\nPlease try again with /add_proxy",
                    parse_mode='HTML'
                )
    
    # ========================================================================
    # Account Wizards
    # ========================================================================
    
    async def handle_create_account_wizard(self, update, context, wizard, text):
        """Handle create account wizard steps."""
        step = wizard['step']
        data = wizard['data']
        
        if step == 1:
            # Account name
            data['account_name'] = text
            wizard['step'] = 2
            # Create account immediately and send single combined message
            await self._create_account_finalize(update, context, data)
        # Additional steps can be added here
    
    async def _create_account_finalize(self, update, context, data):
        """Finalize account creation."""
        telegram_user_id = update.effective_user.id
        user = await self.auth.get_user_by_telegram_id(telegram_user_id)
        
        try:
            account_id = str(uuid.uuid4())
            query = """
                INSERT INTO accounts (id, account_name, user_id, description)
                VALUES (:id, :name, :user_id, :description)
            """
            await self.database.execute(query, {
                "id": account_id,
                "name": data['account_name'],
                "user_id": str(user["id"]),  # Ensure string conversion
                "description": f"Account created via Telegram: {data['account_name']}"
            })
            
            await self.audit_logger.log_account_creation(str(user["id"]), account_id, data['account_name'])
            await self.safety_manager.initialize_user_limits(str(user["id"]))
            
            context.user_data.pop('wizard', None)
            
            # Single combined message
            await update.message.reply_text(
                f"✅ Account created: <b>{data['account_name']}</b>\n\n"
                "Next steps:\n"
                "1. Add exchange credentials: /add_exchange\n"
                "2. Add proxy: /add_proxy {account_name}",
                parse_mode='HTML'
            )
        except Exception as e:
            self.logger.error(f"Create account error: {e}")
            await update.message.reply_text(
                f"❌ Failed to create account: {str(e)}",
                parse_mode='HTML'
            )
    
    async def handle_add_exchange_wizard(self, update, context, wizard, text):
        """Handle add exchange wizard steps."""
        data = wizard['data']
        exchange = data['exchange']
        account_id = data['account_id']
        mode = wizard.get('mode', 'json')  # Default to JSON for backward compatibility
        
        try:
            if mode == 'interactive':
                # Step-by-step interactive mode
                fields = data.get('fields', [])
                current_index = data.get('current_field_index', 0)
                credentials = data.get('credentials', {})
                
                if current_index >= len(fields):
                    await update.message.reply_text(
                        "❌ Invalid step. Please start over with /add_exchange",
                        parse_mode='HTML'
                    )
                    return
                
                field_name, field_prompt = fields[current_index]
                
                # Allow cancel at any step
                if text.lower() in ['cancel', '/cancel']:
                    context.user_data.pop('wizard', None)
                    await update.message.reply_text(
                        "❌ Credential addition cancelled.",
                        parse_mode='HTML'
                    )
                    return
                
                # Handle optional fields
                if text.lower() == 'skip' and field_name in ['l2_address', 'environment']:
                    if field_name == 'environment':
                        credentials[field_name] = 'prod'  # Default value
                    else:
                        credentials[field_name] = None
                else:
                    credentials[field_name] = text.strip()
                
                # Move to next field
                current_index += 1
                data['current_field_index'] = current_index
                data['credentials'] = credentials
                
                if current_index < len(fields):
                    # More fields to collect
                    next_field_name, next_field_prompt = fields[current_index]
                    await update.message.reply_text(
                        f"✅ {field_name} saved\n\n"
                        f"Step {current_index + 1}/{len(fields)}: {next_field_prompt}",
                        parse_mode='HTML'
                    )
                    return
                else:
                    # All fields collected, proceed to verification
                    pass  # Fall through to verification
            else:
                # JSON mode - parse JSON input
                import json
                
                # Allow cancel
                if text.lower() in ['cancel', '/cancel']:
                    context.user_data.pop('wizard', None)
                    await update.message.reply_text(
                        "❌ Credential addition cancelled.",
                        parse_mode='HTML'
                    )
                    return
                
                try:
                    # Try to parse as JSON (handle both single-line and multi-line)
                    json_text = text.strip()
                    credentials_dict = json.loads(json_text)
                    
                    # Map to expected credential format based on exchange
                    if exchange == 'lighter':
                        credentials = {
                            'private_key': credentials_dict.get('private_key', ''),
                            'account_index': str(credentials_dict.get('account_index', '0')),
                            'api_key_index': str(credentials_dict.get('api_key_index', '0'))
                        }
                        if not credentials['private_key']:
                            raise ValueError("Missing required field: private_key")
                    elif exchange == 'aster':
                        credentials = {
                            'api_key': credentials_dict.get('api_key', ''),
                            'secret_key': credentials_dict.get('secret_key', '')
                        }
                        if not credentials['api_key'] or not credentials['secret_key']:
                            raise ValueError("Missing required fields: api_key, secret_key")
                    elif exchange == 'backpack':
                        credentials = {
                            'public_key': credentials_dict.get('public_key', ''),
                            'secret_key': credentials_dict.get('secret_key', '')
                        }
                        if not credentials['public_key'] or not credentials['secret_key']:
                            raise ValueError("Missing required fields: public_key, secret_key")
                    elif exchange == 'paradex':
                        credentials = {
                            'l1_address': credentials_dict.get('l1_address', ''),
                            'l2_private_key_hex': credentials_dict.get('l2_private_key_hex', ''),
                            'l2_address': credentials_dict.get('l2_address'),
                            'environment': credentials_dict.get('environment', 'prod')
                        }
                        if not credentials['l1_address'] or not credentials['l2_private_key_hex']:
                            raise ValueError("Missing required fields: l1_address, l2_private_key_hex")
                    else:
                        raise ValueError(f"Unknown exchange: {exchange}")
                    
                except json.JSONDecodeError as e:
                    await update.message.reply_text(
                        f"❌ Invalid JSON format: {str(e)}\n\n"
                        "Please send valid JSON. Example:\n"
                        f"<code>{{\"api_key\": \"value\", \"secret_key\": \"value\"}}</code>",
                        parse_mode='HTML'
                    )
                    return
                except ValueError as e:
                    await update.message.reply_text(
                        f"❌ {str(e)}\n\nPlease check your JSON and try again.",
                        parse_mode='HTML'
                    )
                    return
            
            # Verify credentials
            await update.message.reply_text("⏳ Verifying credentials...", parse_mode='HTML')
            success, error = await self.credential_verifier.verify_exchange_credentials(exchange, credentials)
            
            if not success:
                await update.message.reply_text(
                    f"❌ Credential verification failed: {error}",
                    parse_mode='HTML'
                )
                return
            
            # Store credentials
            telegram_user_id = update.effective_user.id
            user = await self.auth.get_user_by_telegram_id(telegram_user_id)
            await self._store_exchange_credentials(account_id, exchange, credentials)
            
            await self.audit_logger.log_credential_update(str(user["id"]), account_id, exchange)
            
            context.user_data.pop('wizard', None)
            
            await update.message.reply_text(
                f"✅ {exchange.upper()} credentials added successfully to <b>{data['account_name']}</b>!",
                parse_mode='HTML'
            )
            
        except Exception as e:
            self.logger.error(f"Add exchange error: {e}")
            await update.message.reply_text(
                f"❌ Error adding exchange: {str(e)}",
                parse_mode='HTML'
            )
    
    async def handle_add_proxy_wizard(self, update, context, wizard, text):
        """Handle add proxy wizard steps."""
        data = wizard['data']
        step = wizard['step']
        
        if step == 1:
            # Proxy URL
            # Validate format
            valid, error = self.proxy_verifier.validate_proxy_format(text)
            if not valid:
                await update.message.reply_text(
                    f"❌ Invalid proxy URL: {error}",
                    parse_mode='HTML'
                )
                return
            
            data['proxy_url'] = text
            wizard['step'] = 2
            
            await update.message.reply_text(
                "✅ Proxy URL saved\n\n"
                "Step 2/2: Enter proxy auth (optional):\n"
                "Format: username:password\n"
                "Or send 'skip' to continue without auth",
                parse_mode='HTML'
            )
        
        elif step == 2:
            # Proxy auth
            username = None
            password = None
            
            if text.lower() != 'skip':
                if ':' in text:
                    parts = text.split(':', 1)
                    username = parts[0]
                    password = parts[1] if len(parts) > 1 else None
                else:
                    username = text
            
            data['proxy_username'] = username
            data['proxy_password'] = password
            
            # Verify proxy
            await update.message.reply_text("⏳ Verifying proxy...", parse_mode='HTML')
            success, error = await self.proxy_verifier.verify_proxy(
                data['proxy_url'],
                username=username,
                password=password
            )
            
            if not success:
                await update.message.reply_text(
                    f"❌ Proxy verification failed: {error}",
                    parse_mode='HTML'
                )
                return
            
            # Get account_id from wizard data (already stored from callback)
            account_id = data.get('account_id')
            if not account_id:
                # Fallback: try to get from account_name (for backward compatibility)
                telegram_user_id = update.effective_user.id
                user = await self.auth.get_user_by_telegram_id(telegram_user_id)
                query = """
                    SELECT id FROM accounts
                    WHERE account_name = :name AND user_id = :user_id
                """
                row = await self.database.fetch_one(query, {
                    "name": data['account_name'],
                    "user_id": str(user["id"])
                })
                if not row:
                    await update.message.reply_text(
                        f"❌ Account not found: {data['account_name']}",
                        parse_mode='HTML'
                    )
                    return
                account_id = row["id"]
            
            telegram_user_id = update.effective_user.id
            user = await self.auth.get_user_by_telegram_id(telegram_user_id)
            
            # Store proxy
            await self._store_proxy(account_id, data)
            
            await self.audit_logger.log_proxy_assignment(str(user["id"]), account_id, "proxy_id")
            
            context.user_data.pop('wizard', None)
            
            await update.message.reply_text(
                f"✅ Proxy added successfully to <b>{data['account_name']}</b>!",
                parse_mode='HTML'
            )
    
    async def handle_edit_account_wizard(self, update, context, wizard, text):
        """Handle edit account wizard steps (now only handles step 2 - value input)."""
        data = wizard['data']
        step = wizard['step']
        account_id = data['account_id']
        
        if text.lower() == 'cancel':
            context.user_data.pop('wizard', None)
            await update.message.reply_text(
                "❌ Account edit cancelled.",
                parse_mode='HTML'
            )
            return
        
        if step == 2:
            # User provided new value (step 1 is now handled by buttons)
            edit_type = data.get('edit_type')
            telegram_user_id = update.effective_user.id
            user = await self.auth.get_user_by_telegram_id(telegram_user_id)
            
            try:
                if edit_type == 'name':
                    # Update account name
                    await self.database.execute(
                        """
                        UPDATE accounts
                        SET account_name = :name, updated_at = NOW()
                        WHERE id = :id AND user_id = :user_id
                        """,
                        {"id": account_id, "name": text, "user_id": str(user["id"])}
                    )
                    # Wrap log_action in try-except to prevent it from causing formatting errors
                    try:
                        await self.audit_logger.log_action(
                            str(user["id"]),
                            "edit_account",
                            {"account_id": account_id, "field": "name", "new_value": text}
                        )
                    except Exception as log_error:
                        # Silently log audit failures to avoid cascading errors
                        self.logger.error("Failed to log audit action: " + str(log_error))
                    
                    # Use string concatenation to avoid f-string formatting issues with braces
                    await update.message.reply_text(
                        "✅ Account name updated to <b>" + text + "</b>",
                        parse_mode='HTML'
                    )
                
                elif edit_type == 'description':
                    # Update description
                    description = None if text.lower() == 'none' else text
                    await self.database.execute(
                        """
                        UPDATE accounts
                        SET description = :desc, updated_at = NOW()
                        WHERE id = :id AND user_id = :user_id
                        """,
                        {"id": account_id, "desc": description, "user_id": str(user["id"])}
                    )
                    # Wrap log_action in try-except to prevent it from causing formatting errors
                    try:
                        await self.audit_logger.log_action(
                            str(user["id"]),
                            "edit_account",
                            {"account_id": account_id, "field": "description", "new_value": description}
                        )
                    except Exception as log_error:
                        # Silently log audit failures to avoid cascading errors
                        self.logger.error("Failed to log audit action: " + str(log_error))
                    
                    await update.message.reply_text(
                        "✅ Account description updated.",
                        parse_mode='HTML'
                    )
                
                context.user_data.pop('wizard', None)
            except Exception as e:
                error_msg = "Error updating account: " + str(e)
                self.logger.error(error_msg)
                await update.message.reply_text(
                    "❌ Failed to update account: " + str(e),
                    parse_mode='HTML'
                )
    
    # ========================================================================
    # Helper Methods
    # ========================================================================
    
    async def _store_exchange_credentials(self, account_id: str, exchange_name: str, credentials: Dict[str, str]):
        """Store exchange credentials in database."""
        # Get exchange_id
        exchange = await self.database.fetch_one(
            "SELECT id FROM dexes WHERE name = :name",
            {"name": exchange_name.lower()}
        )
        
        if not exchange:
            raise ValueError(f"Exchange '{exchange_name}' not found")
        
        exchange_id = exchange['id']
        
        # Encrypt credentials
        api_key_encrypted = None
        secret_key_encrypted = None
        additional_creds = {}
        
        if 'api_key' in credentials and credentials['api_key']:
            api_key_encrypted = self.encryptor.encrypt(credentials['api_key'].encode()).decode()
        
        if 'secret_key' in credentials and credentials['secret_key']:
            secret_key_encrypted = self.encryptor.encrypt(credentials['secret_key'].encode()).decode()
        
        # Store additional credentials
        for key, value in credentials.items():
            if key not in ['api_key', 'secret_key'] and value:
                additional_creds[key] = self.encryptor.encrypt(str(value).encode()).decode()
        
        # Check if exists
        existing = await self.database.fetch_one("""
            SELECT id FROM account_exchange_credentials 
            WHERE account_id = :account_id 
              AND exchange_id = :exchange_id 
              AND subaccount_index = 0
        """, {
            "account_id": account_id,
            "exchange_id": exchange_id
        })
        
        additional_json = json.dumps(additional_creds) if additional_creds else None
        
        if existing:
            # Update
            await self.database.execute("""
                UPDATE account_exchange_credentials
                SET api_key_encrypted = :api_key,
                    secret_key_encrypted = :secret_key,
                    additional_credentials_encrypted = CAST(:additional AS jsonb),
                    updated_at = NOW()
                WHERE id = :id
            """, {
                "id": existing['id'],
                "api_key": api_key_encrypted,
                "secret_key": secret_key_encrypted,
                "additional": additional_json
            })
        else:
            # Insert
            await self.database.execute("""
                INSERT INTO account_exchange_credentials (
                    account_id, exchange_id, subaccount_index,
                    api_key_encrypted, secret_key_encrypted, 
                    additional_credentials_encrypted
                )
                VALUES (
                    :account_id, :exchange_id, 0,
                    :api_key, :secret_key, CAST(:additional AS jsonb)
                )
            """, {
                "account_id": account_id,
                "exchange_id": exchange_id,
                "api_key": api_key_encrypted,
                "secret_key": secret_key_encrypted,
                "additional": additional_json
            })
    
    async def _store_proxy(self, account_id: str, data: Dict[str, Any]):
        """Store proxy in database."""
        proxy_url = data['proxy_url']
        username = data.get('proxy_username')
        password = data.get('proxy_password')
        
        # Create proxy label
        label = f"{data['account_name']}_proxy_{uuid.uuid4().hex[:8]}"
        
        # Encrypt credentials if provided
        encrypted_creds = None
        if username or password:
            creds_payload = {
                "username": username,
                "password": password
            }
            if self.encryptor:
                encrypted_creds = json.dumps({
                    "username": self.encryptor.encrypt(username.encode()).decode() if username else None,
                    "password": self.encryptor.encrypt(password.encode()).decode() if password else None
                })
            else:
                encrypted_creds = json.dumps(creds_payload)
        
        auth_type = "basic" if username else "none"
        
        # Upsert proxy
        proxy_id = await upsert_proxy(
            self.database,
            label=label,
            endpoint_url=proxy_url,
            auth_type=auth_type,
            encrypted_credentials=encrypted_creds
        )
        
        # Assign to account
        await assign_proxy(
            self.database,
            account_name=data['account_name'],
            proxy_id=proxy_id,
            priority=0,
            status="active"
        )
    
    def register_handlers(self, application):
        """Register account management command and callback handlers"""
        # Commands
        application.add_handler(CommandHandler("list_accounts", self.list_accounts_command))
        application.add_handler(CommandHandler("create_account", self.create_account_command))
        application.add_handler(CommandHandler("quick_start", self.create_account_command))
        application.add_handler(CommandHandler("add_exchange", self.add_exchange_command))
        application.add_handler(CommandHandler("add_proxy", self.add_proxy_command))
        
        # Callbacks
        application.add_handler(CallbackQueryHandler(
            self.edit_account_callback,
            pattern="^edit_account_btn:"
        ))
        application.add_handler(CallbackQueryHandler(
            self.delete_account_callback,
            pattern="^delete_account_btn:"
        ))
        application.add_handler(CallbackQueryHandler(
            self.delete_account_confirm_callback,
            pattern="^delete_account_confirm:"
        ))
        application.add_handler(CallbackQueryHandler(
            self.delete_account_cancel_callback,
            pattern="^delete_account_cancel:"
        ))
        application.add_handler(CallbackQueryHandler(
            self.edit_account_name_callback,
            pattern="^edit_acc_name:"
        ))
        application.add_handler(CallbackQueryHandler(
            self.edit_account_description_callback,
            pattern="^edit_acc_desc:"
        ))
        application.add_handler(CallbackQueryHandler(
            self.edit_account_add_exchange_callback,
            pattern="^edit_acc_exchange:"
        ))
        application.add_handler(CallbackQueryHandler(
            self.edit_account_add_proxy_callback,
            pattern="^edit_acc_proxy:"
        ))
        application.add_handler(CallbackQueryHandler(
            self.add_exchange_account_callback,
            pattern="^add_exchange_account:"
        ))
        application.add_handler(CallbackQueryHandler(
            self.add_exchange_exchange_callback,
            pattern="^add_exc_ex:"
        ))
        application.add_handler(CallbackQueryHandler(
            self.add_exchange_mode_callback,
            pattern="^add_exc_mode:"
        ))
        application.add_handler(CallbackQueryHandler(
            self.add_proxy_account_callback,
            pattern="^add_proxy_account:"
        ))

