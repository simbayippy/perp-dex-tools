"""
Strategy execution handlers for Telegram bot
"""

from datetime import datetime
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler

from telegram_bot_service.handlers.base import BaseHandler


class StrategyHandler(BaseHandler):
    """Handler for strategy execution commands and callbacks"""
    
    async def list_strategies_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /list_strategies or /status command."""
        user, _ = await self.require_auth(update, context)
        if not user:
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
        user, _ = await self.require_auth(update, context)
        if not user:
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
        user, _ = await self.require_auth(update, context)
        if not user:
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
        user, _ = await self.require_auth(update, context)
        if not user:
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
        user, _ = await self.require_auth(update, context)
        if not user:
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
    
    def register_handlers(self, application):
        """Register strategy execution command and callback handlers"""
        # Commands
        application.add_handler(CommandHandler("run", self.run_strategy_command))
        application.add_handler(CommandHandler("run_strategy", self.run_strategy_command))
        application.add_handler(CommandHandler("list_strategies", self.list_strategies_command))
        application.add_handler(CommandHandler("stop", self.stop_strategy_command))
        application.add_handler(CommandHandler("stop_strategy", self.stop_strategy_command))
        application.add_handler(CommandHandler("logs", self.logs_command))
        application.add_handler(CommandHandler("limits", self.limits_command))
        
        # Callbacks
        application.add_handler(CallbackQueryHandler(
            self.run_account_callback,
            pattern="^run_account:"
        ))
        application.add_handler(CallbackQueryHandler(
            self.run_config_callback,
            pattern="^run_config:"
        ))

