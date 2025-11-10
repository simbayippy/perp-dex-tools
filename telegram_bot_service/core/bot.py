"""
Telegram bot with command handlers for strategy control
"""

import asyncio
import logging
import os
import json
import uuid
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)
from databases import Database
from cryptography.fernet import Fernet

from telegram_bot_service.core.config import TelegramBotConfig
from telegram_bot_service.utils.api_client import ControlAPIClient
from telegram_bot_service.utils.auth import TelegramAuth
from telegram_bot_service.utils.formatters import TelegramFormatter
from telegram_bot_service.managers.process_manager import StrategyProcessManager
from telegram_bot_service.verification.credential_verifier import CredentialVerifier
from telegram_bot_service.verification.proxy_verifier import ProxyVerifier
from telegram_bot_service.utils.audit_logger import AuditLogger
from telegram_bot_service.managers.safety_manager import SafetyManager
from telegram_bot_service.managers.health_monitor import HealthMonitor
from telegram_bot_service.verification.config_validator import ConfigValidator
from database.scripts.proxy_utils import upsert_proxy, assign_proxy, parse_proxy_line


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
        """Handle /close command - shows interactive position selection."""
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
        telegram_user_id = query.from_user.id
        user = await self.auth.get_user_by_telegram_id(telegram_user_id)
        if not user:
            await query.edit_message_text(
                self.formatter.format_not_authenticated(),
                parse_mode='HTML'
            )
            return
        
        api_key = await self.auth.get_api_key_for_user(user)
        if not api_key:
            await query.edit_message_text(
                "❌ API key not found. Please authenticate again with /auth",
                parse_mode='HTML'
            )
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
    
    # ========================================================================
    # Account Management Commands
    # ========================================================================
    
    async def list_accounts_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /list_accounts command."""
        telegram_user_id = update.effective_user.id
        user = await self.auth.get_user_by_telegram_id(telegram_user_id)
        if not user:
            await update.message.reply_text(
                self.formatter.format_not_authenticated(),
                parse_mode='HTML'
            )
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
        telegram_user_id = update.effective_user.id
        user = await self.auth.get_user_by_telegram_id(telegram_user_id)
        if not user:
            await update.message.reply_text(
                self.formatter.format_not_authenticated(),
                parse_mode='HTML'
            )
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
        telegram_user_id = update.effective_user.id
        user = await self.auth.get_user_by_telegram_id(telegram_user_id)
        if not user:
            await update.message.reply_text(
                self.formatter.format_not_authenticated(),
                parse_mode='HTML'
            )
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
        """Handle /add_proxy command."""
        telegram_user_id = update.effective_user.id
        user = await self.auth.get_user_by_telegram_id(telegram_user_id)
        if not user:
            await update.message.reply_text(
                self.formatter.format_not_authenticated(),
                parse_mode='HTML'
            )
            return
        
        if not context.args or len(context.args) < 1:
            await update.message.reply_text(
                "❌ Usage: /add_proxy &lt;account_name&gt;\n\n"
                "Example: /add_proxy my_account",
                parse_mode='HTML'
            )
            return
        
        account_name = context.args[0]
        
        # Start proxy wizard
        context.user_data['wizard'] = {
            'type': 'add_proxy',
            'step': 1,
            'data': {
                'account_name': account_name
            }
        }
        
        await update.message.reply_text(
            "🌐 <b>Add Proxy</b>\n\n"
            "Step 1/2: Enter proxy URL:\n"
            "Format: socks5://host:port\n"
            "Example: socks5://123.45.67.89:1080",
            parse_mode='HTML'
        )
    
    # ========================================================================
    # Config Management Commands
    # ========================================================================
    
    async def list_configs_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /list_configs or /my_configs command."""
        telegram_user_id = update.effective_user.id
        user = await self.auth.get_user_by_telegram_id(telegram_user_id)
        if not user:
            await update.message.reply_text(
                self.formatter.format_not_authenticated(),
                parse_mode='HTML'
            )
            return
        
        try:
            # Get user configs
            query = """
                SELECT id, config_name, strategy_type, is_active, created_at
                FROM strategy_configs
                WHERE user_id = :user_id AND is_template = FALSE
                ORDER BY created_at DESC
            """
            user_configs = await self.database.fetch_all(query, {"user_id": user["id"]})
            
            # Get public templates
            query_templates = """
                SELECT id, config_name, strategy_type, created_at
                FROM strategy_configs
                WHERE is_template = TRUE
                ORDER BY config_name
            """
            templates = await self.database.fetch_all(query_templates)
            
            message = "📋 <b>Your Configurations</b>\n\n"
            keyboard = []
            
            if user_configs:
                message += "<b>Your Configs:</b>\n"
                for cfg in user_configs:
                    status = "🟢" if cfg["is_active"] else "⚫"
                    config_id = str(cfg["id"])
                    config_name = cfg["config_name"]
                    message += f"{status} <b>{config_name}</b> ({cfg['strategy_type']})\n"
                    
                    # Add edit and delete buttons for each config
                    keyboard.append([
                        InlineKeyboardButton(
                            f"✏️ {config_name}",
                            callback_data=f"edit_config_btn:{config_id}"
                        ),
                        InlineKeyboardButton(
                            f"🗑️ {config_name}",
                            callback_data=f"delete_config_btn:{config_id}"
                        )
                    ])
                message += "\n"
            else:
                message += "No configs yet. Create one with /create_config\n\n"
            
            if templates:
                message += "<b>Public Templates:</b>\n"
                for tpl in templates[:10]:  # Limit to 10 templates
                    message += f"📄 {tpl['config_name']} ({tpl['strategy_type']})\n"
                if len(templates) > 10:
                    message += f"... and {len(templates) - 10} more\n"
            
            reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
            
            await update.message.reply_text(
                message,
                parse_mode='HTML',
                reply_markup=reply_markup
            )
            
        except Exception as e:
            self.logger.error(f"List configs error: {e}")
            await update.message.reply_text(
                f"❌ Failed to list configs: {str(e)}",
                parse_mode='HTML'
            )
    
    async def create_config_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /create_config or /new_config command."""
        telegram_user_id = update.effective_user.id
        user = await self.auth.get_user_by_telegram_id(telegram_user_id)
        if not user:
            await update.message.reply_text(
                self.formatter.format_not_authenticated(),
                parse_mode='HTML'
            )
            return
        
        # Start config creation wizard
        context.user_data['wizard'] = {
            'type': 'create_config',
            'step': 1,
            'data': {}
        }
        
        keyboard = [
            [InlineKeyboardButton("📊 Funding Arbitrage", callback_data="config_type:funding_arbitrage")],
            [InlineKeyboardButton("📈 Grid", callback_data="config_type:grid")],
            [InlineKeyboardButton("📝 JSON Input", callback_data="config_type:json")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "📋 <b>Create New Configuration</b>\n\n"
            "Choose creation method:",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
    
    # ========================================================================
    # Strategy Execution Commands
    # ========================================================================
    
    async def list_strategies_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /list_strategies or /status command."""
        telegram_user_id = update.effective_user.id
        user = await self.auth.get_user_by_telegram_id(telegram_user_id)
        if not user:
            await update.message.reply_text(
                self.formatter.format_not_authenticated(),
                parse_mode='HTML'
            )
            return
        
        try:
            strategies = await self.process_manager.get_running_strategies(user["id"])
            
            if not strategies:
                await update.message.reply_text(
                    "📊 <b>No Running Strategies</b>\n\n"
                    "Start a strategy with /run",
                    parse_mode='HTML'
                )
                return
            
            message = "📊 <b>Your Strategies</b>\n\n"
            for strat in strategies:
                status_emoji = {
                    'running': '🟢',
                    'starting': '🟡',
                    'stopped': '⚫',
                    'error': '🔴',
                    'paused': '⏸'
                }.get(strat['status'], '⚪')
                
                run_id_short = str(strat['id'])[:8]
                message += (
                    f"{status_emoji} <b>{run_id_short}</b>\n"
                    f"   Status: {strat['status']}\n"
                )
                
                if strat.get('started_at'):
                    started = strat['started_at']
                    if isinstance(started, str):
                        started = datetime.fromisoformat(started.replace('Z', '+00:00'))
                    uptime = datetime.now() - started.replace(tzinfo=None)
                    hours = int(uptime.total_seconds() / 3600)
                    minutes = int((uptime.total_seconds() % 3600) / 60)
                    message += f"   Uptime: {hours}h {minutes}m\n"
                
                message += "\n"
            
            await update.message.reply_text(message, parse_mode='HTML')
            
        except Exception as e:
            self.logger.error(f"List strategies error: {e}")
            await update.message.reply_text(
                f"❌ Failed to list strategies: {str(e)}",
                parse_mode='HTML'
            )
    
    async def run_strategy_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /run or /run_strategy command."""
        telegram_user_id = update.effective_user.id
        user = await self.auth.get_user_by_telegram_id(telegram_user_id)
        if not user:
            await update.message.reply_text(
                self.formatter.format_not_authenticated(),
                parse_mode='HTML'
            )
            return
        
        # Check safety limits
        allowed, reason = await self.safety_manager.can_start_strategy(user["id"])
        if not allowed:
            await update.message.reply_text(
                f"❌ Cannot start strategy: {reason}",
                parse_mode='HTML'
            )
            return
        
        # Check resource availability
        resource_ok, resource_msg = await self.health_monitor.before_spawn_check()
        if not resource_ok:
            await update.message.reply_text(
                f"❌ Resource check failed: {resource_msg}",
                parse_mode='HTML'
            )
            return
        
        # Check user limit
        user_limit_ok, user_limit_msg = await self.health_monitor.check_user_running_count(user["id"])
        if not user_limit_ok:
            await update.message.reply_text(
                f"❌ {user_limit_msg}",
                parse_mode='HTML'
            )
            return
        
        # Start wizard to select account and config
        context.user_data['wizard'] = {
            'type': 'run_strategy',
            'step': 1,
            'data': {}
        }
        
        # Get user's accounts
        query = """
            SELECT a.id, a.account_name
            FROM accounts a
            WHERE a.user_id = :user_id AND a.is_active = TRUE
            ORDER BY a.account_name
        """
        accounts = await self.database.fetch_all(query, {"user_id": user["id"]})
        
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
                callback_data=f"run_account:{acc['id']}"
            )])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "🚀 <b>Start Strategy</b>\n\n"
            "Step 1/2: Select account:",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
    
    async def stop_strategy_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /stop or /stop_strategy command."""
        telegram_user_id = update.effective_user.id
        user = await self.auth.get_user_by_telegram_id(telegram_user_id)
        if not user:
            await update.message.reply_text(
                self.formatter.format_not_authenticated(),
                parse_mode='HTML'
            )
            return
        
        if not context.args or len(context.args) < 1:
            await update.message.reply_text(
                "❌ Usage: /stop &lt;run_id&gt;\n\n"
                "Get run_id from /list_strategies",
                parse_mode='HTML'
            )
            return
        
        run_id = context.args[0]
        
        try:
            # Verify ownership
            query = """
                SELECT id FROM strategy_runs
                WHERE id = :run_id AND user_id = :user_id
            """
            row = await self.database.fetch_one(query, {"run_id": run_id, "user_id": user["id"]})
            
            if not row:
                await update.message.reply_text(
                    "❌ Strategy not found or you don't have permission",
                    parse_mode='HTML'
                )
                return
            
            # Stop strategy
            success = await self.process_manager.stop_strategy(run_id)
            
            if success:
                await self.audit_logger.log_strategy_stop(str(user["id"]), run_id)
                await update.message.reply_text(
                    f"✅ Strategy stopped: {run_id[:8]}",
                    parse_mode='HTML'
                )
            else:
                await update.message.reply_text(
                    f"❌ Failed to stop strategy: {run_id[:8]}",
                    parse_mode='HTML'
                )
                
        except Exception as e:
            self.logger.error(f"Stop strategy error: {e}")
            await update.message.reply_text(
                f"❌ Error stopping strategy: {str(e)}",
                parse_mode='HTML'
            )
    
    async def logs_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /logs command."""
        telegram_user_id = update.effective_user.id
        user = await self.auth.get_user_by_telegram_id(telegram_user_id)
        if not user:
            await update.message.reply_text(
                self.formatter.format_not_authenticated(),
                parse_mode='HTML'
            )
            return
        
        if not context.args or len(context.args) < 1:
            await update.message.reply_text(
                "❌ Usage: /logs &lt;run_id&gt;\n\n"
                "Get run_id from /list_strategies",
                parse_mode='HTML'
            )
            return
        
        run_id = context.args[0]
        
        try:
            # Verify ownership
            query = """
                SELECT id FROM strategy_runs
                WHERE id = :run_id AND user_id = :user_id
            """
            row = await self.database.fetch_one(query, {"run_id": run_id, "user_id": user["id"]})
            
            if not row:
                await update.message.reply_text(
                    "❌ Strategy not found or you don't have permission",
                    parse_mode='HTML'
                )
                return
            
            # Get log file
            log_file = await self.process_manager.get_log_file(run_id)
            
            if log_file and Path(log_file).exists():
                # Send log file as document
                with open(log_file, 'rb') as f:
                    await update.message.reply_document(
                        document=f,
                        filename=f"strategy_{run_id[:8]}.log",
                        caption=f"📄 Log file for strategy {run_id[:8]}"
                    )
            else:
                await update.message.reply_text(
                    "❌ Log file not available",
                    parse_mode='HTML'
                )
                
        except Exception as e:
            self.logger.error(f"Logs error: {e}")
            await update.message.reply_text(
                f"❌ Error getting logs: {str(e)}",
                parse_mode='HTML'
            )
    
    async def limits_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /limits command."""
        telegram_user_id = update.effective_user.id
        user = await self.auth.get_user_by_telegram_id(telegram_user_id)
        if not user:
            await update.message.reply_text(
                self.formatter.format_not_authenticated(),
                parse_mode='HTML'
            )
            return
        
        try:
            # Get running count
            query = """
                SELECT COUNT(*) as count
                FROM strategy_runs
                WHERE user_id = :user_id AND status IN ('starting', 'running', 'paused')
            """
            row = await self.database.fetch_one(query, {"user_id": user["id"]})
            running_count = row["count"] if row else 0
            
            # Get limits
            daily_limit = await self.safety_manager.get_daily_limit(user["id"])
            starts_today = await self.safety_manager.count_starts_today(user["id"])
            max_error_rate = await self.safety_manager.get_max_error_rate(user["id"])
            error_rate = await self.safety_manager.calculate_error_rate(user["id"])
            cooldown_ok, cooldown_msg = await self.safety_manager.check_cooldown(user["id"])
            
            message = (
                f"📊 <b>Your Limits</b>\n\n"
                f"🟢 Running: {running_count}/3\n"
                f"📅 Daily starts: {starts_today}/{daily_limit}\n"
                f"⚠️ Error rate: {error_rate:.1%} (max: {max_error_rate:.1%})\n"
                f"⏱ Cooldown: {'✅ Ready' if cooldown_ok else cooldown_msg}\n"
            )
            
            await update.message.reply_text(message, parse_mode='HTML')
            
        except Exception as e:
            self.logger.error(f"Limits error: {e}")
            await update.message.reply_text(
                f"❌ Error getting limits: {str(e)}",
                parse_mode='HTML'
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
        # Basic commands
        application.add_handler(CommandHandler("start", self.start_command))
        application.add_handler(CommandHandler("help", self.help_command))
        application.add_handler(CommandHandler("auth", self.auth_command))
        application.add_handler(CommandHandler("logout", self.logout_command))
        
        # Monitoring commands
        application.add_handler(CommandHandler("status", self.status_command))
        application.add_handler(CommandHandler("positions", self.positions_command))
        application.add_handler(CommandHandler("close", self.close_command))
        
        # Account management commands
        application.add_handler(CommandHandler("list_accounts", self.list_accounts_command))
        application.add_handler(CommandHandler("create_account", self.create_account_command))
        application.add_handler(CommandHandler("quick_start", self.create_account_command))
        application.add_handler(CommandHandler("add_exchange", self.add_exchange_command))
        application.add_handler(CommandHandler("add_proxy", self.add_proxy_command))
        
        # Config management commands
        application.add_handler(CommandHandler("list_configs", self.list_configs_command))
        application.add_handler(CommandHandler("my_configs", self.list_configs_command))
        application.add_handler(CommandHandler("create_config", self.create_config_command))
        application.add_handler(CommandHandler("new_config", self.create_config_command))
        
        # Strategy execution commands
        application.add_handler(CommandHandler("run", self.run_strategy_command))
        application.add_handler(CommandHandler("run_strategy", self.run_strategy_command))
        application.add_handler(CommandHandler("list_strategies", self.list_strategies_command))
        application.add_handler(CommandHandler("stop", self.stop_strategy_command))
        application.add_handler(CommandHandler("stop_strategy", self.stop_strategy_command))
        application.add_handler(CommandHandler("logs", self.logs_command))
        application.add_handler(CommandHandler("limits", self.limits_command))
        
        # Callback query handlers for interactive flows
        # Order matters: more specific patterns first
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
        application.add_handler(CallbackQueryHandler(
            self.run_account_callback,
            pattern="^run_account:"
        ))
        application.add_handler(CallbackQueryHandler(
            self.run_config_callback,
            pattern="^run_config:"
        ))
        application.add_handler(CallbackQueryHandler(
            self.config_type_callback,
            pattern="^config_type:"
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
            self.edit_config_callback,
            pattern="^edit_config_btn:"
        ))
        application.add_handler(CallbackQueryHandler(
            self.delete_config_callback,
            pattern="^delete_config_btn:"
        ))
        application.add_handler(CallbackQueryHandler(
            self.delete_config_confirm_callback,
            pattern="^delete_config_confirm:"
        ))
        application.add_handler(CallbackQueryHandler(
            self.delete_config_cancel_callback,
            pattern="^delete_config_cancel:"
        ))
        
        # Wizard message handler (for multi-step wizards)
        application.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            self.handle_wizard_message
        ))
        
        # Error handler
        application.add_error_handler(self.error_handler)
    
    async def handle_wizard_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle wizard step messages."""
        wizard = context.user_data.get('wizard')
        if not wizard:
            return  # Not in a wizard
        
        text = update.message.text
        
        if wizard['type'] == 'create_account':
            await self._handle_create_account_wizard(update, context, wizard, text)
        elif wizard['type'] == 'add_exchange':
            await self._handle_add_exchange_wizard(update, context, wizard, text)
        elif wizard['type'] == 'add_proxy':
            await self._handle_add_proxy_wizard(update, context, wizard, text)
        elif wizard['type'] == 'edit_account':
            await self._handle_edit_account_wizard(update, context, wizard, text)
        elif wizard['type'] == 'edit_config':
            await self._handle_edit_config_wizard(update, context, wizard, text)
    
    async def _handle_create_account_wizard(self, update, context, wizard, text):
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
            
            # Prompt for exchange-specific credentials
            prompts = {
                'lighter': "Enter Lighter credentials:\nFormat: <code>private_key,account_index,api_key_index</code>",
                'aster': "Enter Aster credentials:\nFormat: <code>api_key,secret_key</code>",
                'backpack': "Enter Backpack credentials:\nFormat: <code>public_key,secret_key</code>",
                'paradex': "Enter Paradex credentials:\nFormat: <code>l1_address,l2_private_key_hex[,l2_address,environment]</code>"
            }
            
            await query.edit_message_text(
                f"🔐 <b>Add {exchange.upper()} Credentials</b>\n\n"
                f"Account: <b>{account_name}</b>\n\n"
                f"{prompts[exchange]}\n\n"
                "Send credentials separated by commas:",
                parse_mode='HTML'
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
    
    async def _handle_add_exchange_wizard(self, update, context, wizard, text):
        """Handle add exchange wizard steps."""
        data = wizard['data']
        exchange = data['exchange']
        account_id = data['account_id']
        
        try:
            # Parse credentials based on exchange
            parts = [p.strip() for p in text.split(',')]
            
            if exchange == 'lighter':
                if len(parts) < 3:
                    await update.message.reply_text(
                        "❌ Invalid format. Need: private_key,account_index,api_key_index",
                        parse_mode='HTML'
                    )
                    return
                credentials = {
                    'private_key': parts[0],
                    'account_index': parts[1],
                    'api_key_index': parts[2]
                }
            elif exchange == 'aster':
                if len(parts) < 2:
                    await update.message.reply_text(
                        "❌ Invalid format. Need: api_key,secret_key",
                        parse_mode='HTML'
                    )
                    return
                credentials = {
                    'api_key': parts[0],
                    'secret_key': parts[1]
                }
            elif exchange == 'backpack':
                if len(parts) < 2:
                    await update.message.reply_text(
                        "❌ Invalid format. Need: public_key,secret_key",
                        parse_mode='HTML'
                    )
                    return
                credentials = {
                    'public_key': parts[0],
                    'secret_key': parts[1]
                }
            elif exchange == 'paradex':
                if len(parts) < 2:
                    await update.message.reply_text(
                        "❌ Invalid format. Need: l1_address,l2_private_key_hex[,l2_address,environment]",
                        parse_mode='HTML'
                    )
                    return
                credentials = {
                    'l1_address': parts[0],
                    'l2_private_key_hex': parts[1],
                    'l2_address': parts[2] if len(parts) > 2 else None,
                    'environment': parts[3] if len(parts) > 3 else 'prod'
                }
            
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
    
    async def _handle_add_proxy_wizard(self, update, context, wizard, text):
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
            
            # Get account ID
            query = """
                SELECT id FROM accounts
                WHERE account_name = :name AND user_id = :user_id
            """
            telegram_user_id = update.effective_user.id
            user = await self.auth.get_user_by_telegram_id(telegram_user_id)
            row = await self.database.fetch_one(query, {
                "name": data['account_name'],
                "user_id": user["id"]
            })
            
            if not row:
                await update.message.reply_text(
                    f"❌ Account not found: {data['account_name']}",
                    parse_mode='HTML'
                )
                return
            
            account_id = row["id"]
            
            # Store proxy
            await self._store_proxy(account_id, data)
            
            await self.audit_logger.log_proxy_assignment(str(user["id"]), account_id, "proxy_id")
            
            context.user_data.pop('wizard', None)
            
            await update.message.reply_text(
                f"✅ Proxy added successfully!",
                parse_mode='HTML'
            )
    
    async def _handle_edit_account_wizard(self, update, context, wizard, text):
        """Handle edit account wizard steps."""
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
        
        if step == 1:
            # User selected what to edit (1-4)
            choice = text.strip()
            
            if choice == '1':
                # Edit account name
                wizard['step'] = 2
                wizard['data']['edit_type'] = 'name'
                await update.message.reply_text(
                    "Enter new account name:",
                    parse_mode='HTML'
                )
            elif choice == '2':
                # Edit description
                wizard['step'] = 2
                wizard['data']['edit_type'] = 'description'
                await update.message.reply_text(
                    "Enter new description (or 'none' to remove):",
                    parse_mode='HTML'
                )
            elif choice == '3':
                # Add exchange credentials (redirect to add_exchange flow)
                context.user_data.pop('wizard', None)
                await update.message.reply_text(
                    f"Use /add_exchange to add exchange credentials to this account.",
                    parse_mode='HTML'
                )
            elif choice == '4':
                # Add proxy (redirect to add_proxy flow)
                context.user_data.pop('wizard', None)
                account_name = data['account_name']
                await update.message.reply_text(
                    f"Use /add_proxy {account_name} to add a proxy to this account.",
                    parse_mode='HTML'
                )
            else:
                await update.message.reply_text(
                    "❌ Invalid choice. Please send 1, 2, 3, 4, or 'cancel'.",
                    parse_mode='HTML'
                )
        
        elif step == 2:
            # User provided new value
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
                    await self.audit_logger.log_action(
                        str(user["id"]),
                        "edit_account",
                        {"account_id": account_id, "field": "name", "new_value": text}
                    )
                    await update.message.reply_text(
                        f"✅ Account name updated to <b>{text}</b>",
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
                    await self.audit_logger.log_action(
                        str(user["id"]),
                        "edit_account",
                        {"account_id": account_id, "field": "description", "new_value": description}
                    )
                    await update.message.reply_text(
                        f"✅ Account description updated.",
                        parse_mode='HTML'
                    )
                
                context.user_data.pop('wizard', None)
            except Exception as e:
                self.logger.error(f"Error updating account: {e}")
                await update.message.reply_text(
                    f"❌ Failed to update account: {str(e)}",
                    parse_mode='HTML'
                )
    
    async def _handle_edit_config_wizard(self, update, context, wizard, text):
        """Handle edit config wizard steps."""
        data = wizard['data']
        config_id = data['config_id']
        
        if text.lower() == 'cancel':
            context.user_data.pop('wizard', None)
            await update.message.reply_text(
                "❌ Config edit cancelled.",
                parse_mode='HTML'
            )
            return
        
        telegram_user_id = update.effective_user.id
        user = await self.auth.get_user_by_telegram_id(telegram_user_id)
        
        try:
            import yaml
            import json
            
            # Try to parse as YAML first, then JSON
            try:
                config_dict = yaml.safe_load(text)
            except:
                try:
                    config_dict = json.loads(text)
                except:
                    await update.message.reply_text(
                        "❌ Invalid format. Please provide valid YAML or JSON.",
                        parse_mode='HTML'
                    )
                    return
            
            # Validate config structure
            if not isinstance(config_dict, dict):
                await update.message.reply_text(
                    "❌ Config must be a dictionary/object.",
                    parse_mode='HTML'
                )
                return
            
            # Update config in database
            await self.database.execute(
                """
                UPDATE strategy_configs
                SET config_data = CAST(:config_data AS jsonb),
                    updated_at = NOW()
                WHERE id = :id AND user_id = :user_id
                """,
                {
                    "id": config_id,
                    "config_data": json.dumps(config_dict),
                    "user_id": str(user["id"])
                }
            )
            
            await self.audit_logger.log_action(
                str(user["id"]),
                "edit_config",
                {"config_id": config_id, "config_name": data['config_name']}
            )
            
            context.user_data.pop('wizard', None)
            
            await update.message.reply_text(
                f"✅ Config <b>{data['config_name']}</b> updated successfully!",
                parse_mode='HTML'
            )
        except Exception as e:
            self.logger.error(f"Error updating config: {e}")
            await update.message.reply_text(
                f"❌ Failed to update config: {str(e)}",
                parse_mode='HTML'
            )
    
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
    
    # ========================================================================
    # Account Management Callbacks
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
            
            # Start edit wizard
            context.user_data['wizard'] = {
                'type': 'edit_account',
                'step': 1,
                'data': {
                    'account_id': account_id,
                    'account_name': account_name
                }
            }
            
            await query.edit_message_text(
                f"✏️ <b>Edit Account: {account_name}</b>\n\n"
                "What would you like to edit?\n\n"
                "1. Account name\n"
                "2. Description\n"
                "3. Add exchange credentials\n"
                "4. Add proxy\n\n"
                "Send the number (1-4) or 'cancel' to cancel:",
                parse_mode='HTML'
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
    
    # ========================================================================
    # Config Management Callbacks
    # ========================================================================
    
    async def edit_config_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle edit config button click."""
        query = update.callback_query
        await query.answer()
        
        try:
            callback_data = query.data
            config_id = callback_data.split(":", 1)[1]
            
            # Get config details
            config_row = await self.database.fetch_one(
                """
                SELECT config_name, strategy_type, config_data, is_active
                FROM strategy_configs
                WHERE id = :id
                """,
                {"id": config_id}
            )
            
            if not config_row:
                await query.edit_message_text(
                    "❌ Config not found.",
                    parse_mode='HTML'
                )
                return
            
            config_name = config_row['config_name']
            
            # Start edit wizard (for now, just show current config and allow JSON edit)
            context.user_data['wizard'] = {
                'type': 'edit_config',
                'step': 1,
                'data': {
                    'config_id': config_id,
                    'config_name': config_name,
                    'strategy_type': config_row['strategy_type']
                }
            }
            
            import yaml
            config_yaml = yaml.dump(config_row['config_data'], default_flow_style=False, indent=2)
            
            await query.edit_message_text(
                f"✏️ <b>Edit Config: {config_name}</b>\n\n"
                f"Strategy Type: <b>{config_row['strategy_type']}</b>\n\n"
                f"Current config (YAML):\n"
                f"<code>{config_yaml[:500]}{'...' if len(config_yaml) > 500 else ''}</code>\n\n"
                f"Send updated config as JSON/YAML, or 'cancel' to cancel:",
                parse_mode='HTML'
            )
        except Exception as e:
            self.logger.error(f"Error in edit_config_callback: {e}", exc_info=True)
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
    
    async def delete_config_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle delete config button click - show confirmation."""
        query = update.callback_query
        await query.answer()
        
        try:
            callback_data = query.data
            config_id = callback_data.split(":", 1)[1]
            
            # Get config name
            config_row = await self.database.fetch_one(
                "SELECT config_name FROM strategy_configs WHERE id = :id",
                {"id": config_id}
            )
            
            if not config_row:
                await query.edit_message_text(
                    "❌ Config not found.",
                    parse_mode='HTML'
                )
                return
            
            config_name = config_row['config_name']
            
            # Check if config is in use
            running_strategies = await self.database.fetch_all(
                """
                SELECT COUNT(*) as count
                FROM strategy_runs
                WHERE config_id = :config_id AND status IN ('running', 'starting', 'paused')
                """,
                {"config_id": config_id}
            )
            
            has_running = running_strategies[0]['count'] > 0 if running_strategies else False
            
            if has_running:
                await query.edit_message_text(
                    f"⚠️ <b>Cannot Delete Config</b>\n\n"
                    f"Config <b>{config_name}</b> is being used by running strategies.\n"
                    f"Please stop all strategies using this config first.",
                    parse_mode='HTML'
                )
                return
            
            # Show confirmation
            keyboard = [
                [
                    InlineKeyboardButton("✅ Yes, Delete", callback_data=f"delete_config_confirm:{config_id}"),
                    InlineKeyboardButton("❌ Cancel", callback_data=f"delete_config_cancel:{config_id}")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                f"⚠️ <b>Delete Config?</b>\n\n"
                f"Config: <b>{config_name}</b>\n\n"
                f"This will permanently delete this configuration.\n\n"
                f"<b>This action cannot be undone!</b>\n\n"
                f"Are you sure you want to delete this config?",
                parse_mode='HTML',
                reply_markup=reply_markup
            )
        except Exception as e:
            self.logger.error(f"Error in delete_config_callback: {e}", exc_info=True)
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
    
    async def delete_config_confirm_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle config deletion confirmation."""
        query = update.callback_query
        await query.answer()
        
        try:
            callback_data = query.data
            config_id = callback_data.split(":", 1)[1]
            
            telegram_user_id = query.from_user.id
            user = await self.auth.get_user_by_telegram_id(telegram_user_id)
            
            # Get config name before deletion
            config_row = await self.database.fetch_one(
                "SELECT config_name FROM strategy_configs WHERE id = :id AND user_id = :user_id",
                {"id": config_id, "user_id": str(user["id"])}
            )
            
            if not config_row:
                await query.edit_message_text(
                    "❌ Config not found or you don't have permission to delete it.",
                    parse_mode='HTML'
                )
                return
            
            config_name = config_row['config_name']
            
            # Delete config
            await self.database.execute(
                "DELETE FROM strategy_configs WHERE id = :id AND user_id = :user_id",
                {"id": config_id, "user_id": str(user["id"])}
            )
            
            await self.audit_logger.log_action(
                str(user["id"]),
                "delete_config",
                {"config_id": config_id, "config_name": config_name}
            )
            
            await query.edit_message_text(
                f"✅ Config <b>{config_name}</b> deleted successfully.",
                parse_mode='HTML'
            )
        except Exception as e:
            self.logger.error(f"Error in delete_config_confirm_callback: {e}", exc_info=True)
            await query.edit_message_text(
                f"❌ Failed to delete config: {str(e)}",
                parse_mode='HTML'
            )
    
    async def delete_config_cancel_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle config deletion cancellation."""
        query = update.callback_query
        await query.answer("Deletion cancelled.")
        
        await query.edit_message_text(
            "❌ Config deletion cancelled.",
            parse_mode='HTML'
        )
    
    async def run_account_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle account selection for run_strategy wizard."""
        query = update.callback_query
        await query.answer()
        
        callback_data = query.data
        account_id = callback_data.split(":", 1)[1]
        
        telegram_user_id = query.from_user.id
        user = await self.auth.get_user_by_telegram_id(telegram_user_id)
        
        # Store account_id in wizard data
        wizard = context.user_data.get('wizard', {})
        wizard['data']['account_id'] = account_id
        wizard['step'] = 2
        context.user_data['wizard'] = wizard
        
        # Get account name
        account_row = await self.database.fetch_one(
            "SELECT account_name FROM accounts WHERE id = :id",
            {"id": account_id}
        )
        account_name = account_row['account_name'] if account_row else account_id
        
        # Get user's configs
        config_query = """
            SELECT id, config_name, strategy_type
            FROM strategy_configs
            WHERE user_id = :user_id AND is_template = FALSE AND is_active = TRUE
            ORDER BY config_name
        """
        user_configs = await self.database.fetch_all(config_query, {"user_id": user["id"]})
        
        # Get templates
        template_query = """
            SELECT id, config_name, strategy_type
            FROM strategy_configs
            WHERE is_template = TRUE
            ORDER BY config_name
            LIMIT 10
        """
        templates = await self.database.fetch_all(template_query)
        
        # Create config selection keyboard
        keyboard = []
        if user_configs:
            keyboard.append([InlineKeyboardButton("📋 Your Configs", callback_data="config_header")])
            for cfg in user_configs:
                keyboard.append([InlineKeyboardButton(
                    f"{cfg['config_name']} ({cfg['strategy_type']})",
                    callback_data=f"run_config:{cfg['id']}"
                )])
        
        if templates:
            keyboard.append([InlineKeyboardButton("📄 Templates", callback_data="config_header")])
            for tpl in templates:
                keyboard.append([InlineKeyboardButton(
                    f"📄 {tpl['config_name']} ({tpl['strategy_type']})",
                    callback_data=f"run_config:{tpl['id']}"
                )])
        
        reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
        
        await query.edit_message_text(
            f"✅ Account selected: <b>{account_name}</b>\n\n"
            "Step 2/2: Select configuration:",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
    
    async def run_config_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle config selection and start strategy."""
        query = update.callback_query
        await query.answer()
        
        callback_data = query.data
        config_id = callback_data.split(":", 1)[1]
        
        telegram_user_id = query.from_user.id
        user = await self.auth.get_user_by_telegram_id(telegram_user_id)
        
        wizard = context.user_data.get('wizard', {})
        account_id = wizard['data'].get('account_id')
        
        if not account_id:
            await query.edit_message_text(
                "❌ Error: Account not selected. Please start over with /run",
                parse_mode='HTML'
            )
            return
        
        # Get account name
        account_row = await self.database.fetch_one(
            "SELECT account_name FROM accounts WHERE id = :id",
            {"id": account_id}
        )
        account_name = account_row['account_name'] if account_row else account_id
        
        # Get config data
        config_row = await self.database.fetch_one(
            "SELECT config_data, config_name, strategy_type FROM strategy_configs WHERE id = :id",
            {"id": config_id}
        )
        
        if not config_row:
            await query.edit_message_text(
                "❌ Config not found",
                parse_mode='HTML'
            )
            return
        
        config_data = config_row['config_data']
        config_name = config_row['config_name']
        strategy_type = config_row['strategy_type']
        
        # Ensure config_data has the correct structure for runbot.py
        # It should have 'strategy' and 'config' keys
        if isinstance(config_data, dict):
            if 'strategy' not in config_data or 'config' not in config_data:
                # Wrap the config_data with strategy and config keys
                config_data = {
                    "strategy": strategy_type,
                    "created_at": datetime.now().isoformat(),
                    "version": "1.0",
                    "config": config_data
                }
        else:
            await query.edit_message_text(
                "❌ Invalid config data format",
                parse_mode='HTML'
            )
            return
        
        # Validate config before running
        valid, errors = await self.config_validator.validate_before_run(
            config_data, account_id, is_admin=user.get("is_admin", False)
        )
        if not valid:
            await query.edit_message_text(
                f"❌ Config validation failed:\n" + "\n".join(errors),
                parse_mode='HTML'
            )
            return
        
        # Show starting message
        await query.edit_message_text(
            f"⏳ <b>Starting strategy...</b>\n\n"
            f"Account: {account_name}\n"
            f"Config: {config_name}\n"
            f"{'🔓 Admin mode: Running on VPS IP' if user.get('is_admin') else '🔒 Proxy required'}",
            parse_mode='HTML'
        )
        
        try:
            # Spawn strategy
            result = await self.process_manager.spawn_strategy(
                user_id=user["id"],
                account_id=account_id,
                account_name=account_name,
                config_id=config_id,
                config_data=config_data,
                is_admin=user.get("is_admin", False)
            )
            
            await self.audit_logger.log_strategy_start(
                str(user["id"]),
                result['run_id'],
                account_id,
                config_id,
                is_admin=user.get("is_admin", False)
            )
            
            await query.edit_message_text(
                f"✅ <b>Strategy Started!</b>\n\n"
                f"Run ID: <code>{result['run_id'][:8]}</code>\n"
                f"Status: {result['status']}\n"
                f"Port: {result['port']}\n\n"
                f"View status: /list_strategies",
                parse_mode='HTML'
            )
            
            context.user_data.pop('wizard', None)
            
        except Exception as e:
            self.logger.error(f"Start strategy error: {e}")
            await query.edit_message_text(
                f"❌ Failed to start strategy: {str(e)}",
                parse_mode='HTML'
            )
    
    async def config_type_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle config type selection."""
        query = update.callback_query
        await query.answer()
        
        callback_data = query.data
        config_type = callback_data.split(":", 1)[1]
        
        if config_type == "json":
            await query.edit_message_text(
                "📝 <b>JSON Config Input</b>\n\n"
                "Send your config as JSON or YAML:\n"
                "(This feature will be implemented)",
                parse_mode='HTML'
            )
        else:
            await query.edit_message_text(
                f"📋 <b>Config Wizard: {config_type}</b>\n\n"
                "(Wizard implementation coming soon)\n"
                "For now, use JSON input or create configs manually.",
                parse_mode='HTML'
            )
    
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

