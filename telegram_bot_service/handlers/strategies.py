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
        """Handle /list_strategies command - shows filter options."""
        user, _ = await self.require_auth(update, context)
        if not user:
            return
        
        try:
            # Show filter selection buttons
            keyboard = [
                [InlineKeyboardButton("🟢 Running", callback_data="filter_strategies:running")],
                [InlineKeyboardButton("⚫ Stopped", callback_data="filter_strategies:stopped")],
                [InlineKeyboardButton("📋 All", callback_data="filter_strategies:all")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            is_admin = user.get("is_admin", False)
            title = "📊 <b>All Strategies</b>" if is_admin else "📊 <b>Your Strategies</b>"
            
            await update.message.reply_text(
                f"{title}\n\n"
                "Select a filter to view strategies:",
                parse_mode='HTML',
                reply_markup=reply_markup
            )
            
        except Exception as e:
            self.logger.error(f"List strategies error: {e}")
            await update.message.reply_text(
                f"❌ Failed to list strategies: {str(e)}",
                parse_mode='HTML'
            )
    
    async def back_to_strategies_filters_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle back button - return to filter selection for list_strategies."""
        query = update.callback_query
        await query.answer()
        
        user, _ = await self.require_auth(update, context)
        if not user:
            return
        
        try:
            # Show filter selection buttons (same as initial command)
            keyboard = [
                [InlineKeyboardButton("🟢 Running", callback_data="filter_strategies:running")],
                [InlineKeyboardButton("⚫ Stopped", callback_data="filter_strategies:stopped")],
                [InlineKeyboardButton("📋 All", callback_data="filter_strategies:all")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            is_admin = user.get("is_admin", False)
            title = "📊 <b>All Strategies</b>" if is_admin else "📊 <b>Your Strategies</b>"
            
            await query.edit_message_text(
                f"{title}\n\n"
                "Select a filter to view strategies:",
                parse_mode='HTML',
                reply_markup=reply_markup
            )
        except Exception as e:
            self.logger.error(f"Back to strategies filters error: {e}")
            await query.edit_message_text(
                f"❌ Error: {str(e)}",
                parse_mode='HTML'
            )
    
    async def back_to_logs_filters_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle back button - return to filter selection for logs."""
        query = update.callback_query
        await query.answer()
        
        user, _ = await self.require_auth(update, context)
        if not user:
            return
        
        try:
            # Show filter selection buttons (same as initial command)
            keyboard = [
                [InlineKeyboardButton("🟢 Running", callback_data="filter_logs:running")],
                [InlineKeyboardButton("⚫ Stopped", callback_data="filter_logs:stopped")],
                [InlineKeyboardButton("📋 All", callback_data="filter_logs:all")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            title = "📄 <b>View Logs</b>"
            
            await query.edit_message_text(
                f"{title}\n\n"
                "Select a filter to view strategy logs:",
                parse_mode='HTML',
                reply_markup=reply_markup
            )
        except Exception as e:
            self.logger.error(f"Back to logs filters error: {e}")
            await query.edit_message_text(
                f"❌ Error: {str(e)}",
                parse_mode='HTML'
            )
    
    async def filter_strategies_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle filter selection for list_strategies."""
        query = update.callback_query
        await query.answer()
        
        user, _ = await self.require_auth(update, context)
        if not user:
            return
        
        # Parse filter type from callback data: "filter_strategies:{filter_type}"
        callback_data = query.data
        filter_type = callback_data.split(":", 1)[1]
        
        try:
            # Get strategies with filter applied
            is_admin = user.get("is_admin", False)
            user_id = None if is_admin else user["id"]
            strategies = await self._get_strategies_with_filter(user_id, filter_type)
            
            title = "📊 <b>All Strategies</b>" if is_admin else "📊 <b>Your Strategies</b>"
            filter_label = {
                'running': '🟢 Running',
                'stopped': '⚫ Stopped',
                'all': '📋 All'
            }.get(filter_type, filter_type.title())
            
            if not strategies:
                # Show back button instead of all filters
                keyboard = [
                    [InlineKeyboardButton("⬅️ Back", callback_data="back_to_strategies_filters")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await query.edit_message_text(
                    f"{title} - {filter_label}\n\n"
                    "No strategies found matching this filter.\n"
                    "Start a strategy with /run",
                    parse_mode='HTML',
                    reply_markup=reply_markup
                )
                return
            
            message = f"{title} - {filter_label}\n\n"
            keyboard = []
            
            for strat in strategies:
                status_emoji = {
                    'running': '🟢',
                    'starting': '🟡',
                    'stopped': '⚫',
                    'error': '🔴',
                    'paused': '⏸'
                }.get(strat['status'], '⚪')
                
                run_id = str(strat['id'])
                run_id_short = run_id[:8]
                status = strat['status']
                
                message += (
                    f"{status_emoji} <b>{run_id_short}</b>\n"
                    f"   Status: {status}\n"
                )
                
                # Show uptime for running strategies, or "Stopped X ago" for stopped strategies
                if status in ('running', 'starting', 'paused'):
                    if strat.get('started_at'):
                        started = strat['started_at']
                        if isinstance(started, str):
                            started = datetime.fromisoformat(started.replace('Z', '+00:00'))
                        uptime = datetime.now() - started.replace(tzinfo=None)
                        hours = int(uptime.total_seconds() / 3600)
                        minutes = int((uptime.total_seconds() % 3600) / 60)
                        message += f"   Uptime: {hours}h {minutes}m\n"
                elif status in ('stopped', 'error'):
                    if strat.get('stopped_at'):
                        stopped = strat['stopped_at']
                        if isinstance(stopped, str):
                            stopped = datetime.fromisoformat(stopped.replace('Z', '+00:00'))
                        stopped_delta = datetime.now() - stopped.replace(tzinfo=None)
                        hours = int(stopped_delta.total_seconds() / 3600)
                        minutes = int((stopped_delta.total_seconds() % 3600) / 60)
                        message += f"   Stopped: {hours}h {minutes}m ago\n"
                    elif strat.get('started_at'):
                        # Fallback: if stopped_at is not available, show age since started
                        started = strat['started_at']
                        if isinstance(started, str):
                            started = datetime.fromisoformat(started.replace('Z', '+00:00'))
                        age = datetime.now() - started.replace(tzinfo=None)
                        hours = int(age.total_seconds() / 3600)
                        minutes = int((age.total_seconds() % 3600) / 60)
                        message += f"   Age: {hours}h {minutes}m\n"
                
                message += "\n"
                
                # Add action button based on status
                if status in ('running', 'starting'):
                    # Running strategies can be stopped
                    keyboard.append([
                        InlineKeyboardButton(
                            f"🛑 Stop {run_id_short}",
                            callback_data=f"stop_strategy:{run_id}"
                        )
                    ])
                elif status in ('stopped', 'error'):
                    # Stopped strategies can be resumed
                    keyboard.append([
                        InlineKeyboardButton(
                            f"🟢 Resume {run_id_short}",
                            callback_data=f"resume_strategy:{run_id}"
                        )
                    ])
                elif status == 'paused':
                    # Paused strategies can be resumed
                    keyboard.append([
                        InlineKeyboardButton(
                            f"🟢 Resume {run_id_short}",
                            callback_data=f"resume_strategy:{run_id}"
                        )
                    ])
            
            # Add back button at the bottom
            keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="back_to_strategies_filters")])
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(message, parse_mode='HTML', reply_markup=reply_markup)
            
        except Exception as e:
            self.logger.error(f"Filter strategies error: {e}")
            await query.edit_message_text(
                f"❌ Failed to filter strategies: {str(e)}",
                parse_mode='HTML'
            )
    
    async def _get_strategies_with_filter(self, user_id, filter_type: str):
        """Get strategies with filter applied."""
        # Build query based on filter
        if filter_type == 'running':
            status_list = ['starting', 'running', 'paused']
        elif filter_type == 'stopped':
            status_list = ['stopped', 'error']
        else:  # 'all'
            status_list = None
        
        if user_id:
            if status_list:
                # Use parameterized query with tuple unpacking
                placeholders = ','.join([':status' + str(i) for i in range(len(status_list))])
                params = {"user_id": user_id}
                for i, status in enumerate(status_list):
                    params[f"status{i}"] = status
                query = f"""
                    SELECT id, status, started_at, stopped_at, supervisor_program_name
                    FROM strategy_runs
                    WHERE user_id = :user_id AND status IN ({placeholders})
                    ORDER BY started_at DESC
                """
                rows = await self.database.fetch_all(query, params)
            else:
                query = """
                    SELECT id, status, started_at, stopped_at, supervisor_program_name
                    FROM strategy_runs
                    WHERE user_id = :user_id
                    ORDER BY started_at DESC
                """
                rows = await self.database.fetch_all(query, {"user_id": user_id})
        else:
            # Admin - see all
            if status_list:
                placeholders = ','.join([':status' + str(i) for i in range(len(status_list))])
                params = {}
                for i, status in enumerate(status_list):
                    params[f"status{i}"] = status
                query = f"""
                    SELECT id, status, started_at, stopped_at, supervisor_program_name
                    FROM strategy_runs
                    WHERE status IN ({placeholders})
                    ORDER BY started_at DESC
                """
                rows = await self.database.fetch_all(query, params)
            else:
                query = """
                    SELECT id, status, started_at, stopped_at, supervisor_program_name
                    FROM strategy_runs
                    ORDER BY started_at DESC
                """
                rows = await self.database.fetch_all(query)
        
        return [dict(row) for row in rows]
    
    async def run_strategy_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /run command."""
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
        """Handle /stop_strategy command - shows interactive list of running strategies."""
        user, _ = await self.require_auth(update, context)
        if not user:
            return
        
        try:
            # Get user's running strategies (admins see all)
            is_admin = user.get("is_admin", False)
            user_id = None if is_admin else user["id"]
            
            # Query strategies with config info
            if user_id:
                query = """
                    SELECT 
                        sr.id, sr.user_id, sr.account_id, sr.config_id,
                        sr.supervisor_program_name, sr.status, sr.control_api_port,
                        sr.log_file_path, sr.started_at, sr.last_heartbeat, sr.health_status,
                        sc.config_name, sc.strategy_type
                    FROM strategy_runs sr
                    JOIN strategy_configs sc ON sr.config_id = sc.id
                    WHERE sr.user_id = :user_id 
                    AND sr.status IN ('starting', 'running', 'paused')
                    ORDER BY sr.started_at DESC
                """
                strategies = await self.database.fetch_all(query, {"user_id": user_id})
            else:
                query = """
                    SELECT 
                        sr.id, sr.user_id, sr.account_id, sr.config_id,
                        sr.supervisor_program_name, sr.status, sr.control_api_port,
                        sr.log_file_path, sr.started_at, sr.last_heartbeat, sr.health_status,
                        sc.config_name, sc.strategy_type
                    FROM strategy_runs sr
                    JOIN strategy_configs sc ON sr.config_id = sc.id
                    WHERE sr.status IN ('starting', 'running', 'paused')
                    ORDER BY sr.started_at DESC
                """
                strategies = await self.database.fetch_all(query)
            
            strategies = [dict(row) for row in strategies]
            
            if not strategies:
                await update.message.reply_text(
                    "🛑 <b>No Running Strategies</b>\n\n"
                    "No strategies are currently running.\n"
                    "Start a strategy with /run",
                    parse_mode='HTML'
                )
                return
            
            # Create strategy selection keyboard
            keyboard = []
            for strat in strategies:
                run_id = str(strat['id'])
                run_id_short = run_id[:8]
                config_name = strat.get('config_name', 'Unknown')
                strategy_type = strat.get('strategy_type', 'unknown')
                
                # Format strategy type for display
                strategy_type_display = {
                    'funding_arbitrage': 'Funding Arb',
                    'grid': 'Grid'
                }.get(strategy_type, strategy_type.title())
                
                status_emoji = {
                    'running': '🟢',
                    'starting': '🟡',
                    'paused': '⏸'
                }.get(strat['status'], '⚪')
                
                # Button label: "🟢 e9680e47 - Funding Arb"
                button_label = f"{status_emoji} {run_id_short} - {strategy_type_display}"
                keyboard.append([InlineKeyboardButton(
                    button_label,
                    callback_data=f"stop_strategy:{run_id}"
                )])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            title = "🛑 <b>Stop Strategy</b>"
            await update.message.reply_text(
                f"{title}\n\n"
                "Select a strategy to stop:",
                parse_mode='HTML',
                reply_markup=reply_markup
            )
            
        except Exception as e:
            self.logger.error(f"Stop strategy command error: {e}")
            await update.message.reply_text(
                f"❌ Failed to load strategies: {str(e)}",
                parse_mode='HTML'
            )
    
    async def stop_strategy_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle callback for stopping a strategy."""
        query = update.callback_query
        await query.answer()
        
        user, _ = await self.require_auth(update, context)
        if not user:
            return
        
        # Parse callback data: "stop_strategy:{run_id}"
        callback_data = query.data
        if not callback_data.startswith("stop_strategy:"):
            await query.edit_message_text(
                "❌ Invalid selection. Please use /stop_strategy again.",
                parse_mode='HTML'
            )
            return
        
        run_id = callback_data.split(":", 1)[1]
        
        try:
            # Verify ownership (security check)
            is_admin = user.get("is_admin", False)
            if is_admin:
                # Admins can stop any strategy
                verify_query = """
                    SELECT id, status FROM strategy_runs
                    WHERE id = :run_id
                """
                row = await self.database.fetch_one(
                    verify_query,
                    {"run_id": run_id}
                )
            else:
                # Regular users can only stop their own strategies
                verify_query = """
                    SELECT id, status FROM strategy_runs
                    WHERE id = :run_id AND user_id = :user_id
                """
                row = await self.database.fetch_one(
                    verify_query,
                    {"run_id": run_id, "user_id": user["id"]}
                )
            
            if not row:
                await query.edit_message_text(
                    "❌ Strategy not found or you don't have permission to stop it",
                    parse_mode='HTML'
                )
                return
            
            current_status = row['status']
            if current_status in ('stopped', 'error'):
                await query.edit_message_text(
                    f"ℹ️ Strategy is already stopped (status: {current_status})",
                    parse_mode='HTML'
                )
                return
            
            # Show pause options
            run_id_short = run_id[:8]
            keyboard = [
                [
                    InlineKeyboardButton(
                        "⏸️ Pause (Keep Positions)",
                        callback_data=f"stop_strategy_pause:{run_id}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "⏸️ Pause & Close Positions",
                        callback_data=f"stop_strategy_close:{run_id}"
                    )
                ],
                [
                    InlineKeyboardButton("❌ Cancel", callback_data="stop_strategy_cancel")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                f"⏸️ <b>Choose Pause Option</b>\n\n"
                f"Run ID: <code>{run_id_short}</code>\n\n"
                f"<b>⏸️ Pause (Keep Positions):</b>\n"
                f"• Terminates the strategy process\n"
                f"• Positions remain open on exchanges\n"
                f"• You can close them manually or resume later\n\n"
                f"<b>⏸️ Pause & Close Positions:</b>\n"
                f"• Closes all open positions first\n"
                f"• Then terminates the strategy process",
                parse_mode='HTML',
                reply_markup=reply_markup
            )
                
        except Exception as e:
            self.logger.error(f"Stop strategy callback error: {e}", exc_info=True)
            await query.edit_message_text(
                f"❌ Error stopping strategy: {str(e)}",
                parse_mode='HTML'
            )
    
    async def stop_strategy_pause_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle pause option - just stop the strategy without closing positions."""
        query = update.callback_query
        await query.answer()
        
        user, _ = await self.require_auth(update, context)
        if not user:
            return
        
        # Parse callback data: "stop_strategy_pause:{run_id}"
        callback_data = query.data
        if callback_data == "stop_strategy_cancel":
            await query.edit_message_text(
                "❌ Pause cancelled.",
                parse_mode='HTML'
            )
            return
        
        if not callback_data.startswith("stop_strategy_pause:"):
            await query.edit_message_text(
                "❌ Invalid selection. Please use /stop_strategy again.",
                parse_mode='HTML'
            )
            return
        
        run_id = callback_data.split(":", 1)[1]
        run_id_short = run_id[:8]
        
        try:
            # Verify ownership
            is_admin = user.get("is_admin", False)
            if is_admin:
                verify_query = """
                    SELECT id, status FROM strategy_runs
                    WHERE id = :run_id
                """
                row = await self.database.fetch_one(
                    verify_query,
                    {"run_id": run_id}
                )
            else:
                verify_query = """
                    SELECT id, status FROM strategy_runs
                    WHERE id = :run_id AND user_id = :user_id
                """
                row = await self.database.fetch_one(
                    verify_query,
                    {"run_id": run_id, "user_id": user["id"]}
                )
            
            if not row:
                await query.edit_message_text(
                    "❌ Strategy not found or you don't have permission to stop it",
                    parse_mode='HTML'
                )
                return
            
            current_status = row['status']
            if current_status in ('stopped', 'error'):
                await query.edit_message_text(
                    f"ℹ️ Strategy is already stopped (status: {current_status})",
                    parse_mode='HTML'
                )
                return
            
            # Show loading message
            await query.edit_message_text(
                f"⏳ <b>Pausing strategy...</b>\n\n"
                f"Run ID: <code>{run_id_short}</code>\n\n"
                f"Positions will remain open.",
                parse_mode='HTML'
            )
            
            # Stop strategy
            success = await self.process_manager.stop_strategy(run_id)
            
            # Immediately sync status to ensure DB is accurate
            if success:
                await self.process_manager.sync_status_with_supervisor()
            
            if success:
                await self.audit_logger.log_strategy_stop(str(user["id"]), run_id)
                await query.edit_message_text(
                    f"✅ <b>Strategy Paused</b>\n\n"
                    f"Run ID: <code>{run_id_short}</code>\n"
                    f"Status: stopped\n\n"
                    f"ℹ️ <b>Note:</b> Positions remain open. "
                    f"You can close them manually with /close or resume the strategy later.",
                    parse_mode='HTML'
                )
            else:
                await query.edit_message_text(
                    f"❌ <b>Failed to Pause Strategy</b>\n\n"
                    f"Run ID: <code>{run_id_short}</code>\n"
                    f"Please try again or check logs.",
                    parse_mode='HTML'
                )
                
        except Exception as e:
            self.logger.error(f"Stop strategy pause callback error: {e}", exc_info=True)
            await query.edit_message_text(
                f"❌ Error pausing strategy: {str(e)}",
                parse_mode='HTML'
            )
    
    async def stop_strategy_close_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle pause with close option - close positions first, then stop strategy."""
        query = update.callback_query
        await query.answer()
        
        user, api_key = await self.require_auth(update, context)
        if not user or not api_key:
            return
        
        # Parse callback data: "stop_strategy_close:{run_id}"
        callback_data = query.data
        if not callback_data.startswith("stop_strategy_close:"):
            await query.edit_message_text(
                "❌ Invalid selection. Please use /stop_strategy again.",
                parse_mode='HTML'
            )
            return
        
        run_id = callback_data.split(":", 1)[1]
        run_id_short = run_id[:8]
        
        try:
            # Verify ownership and get account info
            is_admin = user.get("is_admin", False)
            if is_admin:
                verify_query = """
                    SELECT sr.id, sr.status, sr.account_id, a.account_name
                    FROM strategy_runs sr
                    LEFT JOIN accounts a ON sr.account_id = a.id
                    WHERE sr.id = :run_id
                """
                row = await self.database.fetch_one(
                    verify_query,
                    {"run_id": run_id}
                )
            else:
                verify_query = """
                    SELECT sr.id, sr.status, sr.account_id, a.account_name
                    FROM strategy_runs sr
                    LEFT JOIN accounts a ON sr.account_id = a.id
                    WHERE sr.id = :run_id AND sr.user_id = :user_id
                """
                row = await self.database.fetch_one(
                    verify_query,
                    {"run_id": run_id, "user_id": user["id"]}
                )
            
            if not row:
                await query.edit_message_text(
                    "❌ Strategy not found or you don't have permission to stop it",
                    parse_mode='HTML'
                )
                return
            
            current_status = row['status']
            if current_status in ('stopped', 'error'):
                await query.edit_message_text(
                    f"ℹ️ Strategy is already stopped (status: {current_status})",
                    parse_mode='HTML'
                )
                return
            
            # Get account_name safely
            try:
                account_name = row['account_name']
            except (KeyError, TypeError):
                account_name = None
            
            # Initialize counters
            closed_count = 0
            failed_count = 0
            position_ids = []
            
            # Show loading message
            await query.edit_message_text(
                f"⏳ <b>Closing positions...</b>\n\n"
                f"Run ID: <code>{run_id_short}</code>\n"
                f"Account: {account_name or 'N/A'}\n\n"
                f"Please wait...",
                parse_mode='HTML'
            )
            
            # Get positions for this account
            from telegram_bot_service.utils.api_client import ControlAPIClient
            client = ControlAPIClient(self.config.control_api_base_url, api_key)
            
            try:
                positions_data = await client.get_positions(account_name=account_name)
                accounts = positions_data.get('accounts', [])
                
                # Collect all position IDs
                for account in accounts:
                    positions = account.get('positions', [])
                    for pos in positions:
                        pos_id = pos.get('id')
                        if pos_id:
                            position_ids.append(pos_id)
                
                # Close each position
                for pos_id in position_ids:
                    try:
                        await client.close_position(
                            position_id=pos_id,
                            order_type="market",
                            reason="telegram_stop_with_close"
                        )
                        closed_count += 1
                    except Exception as e:
                        failed_count += 1
                        self.logger.error(f"Failed to close position {pos_id}: {e}")
                
                # Update message with close results
                if closed_count > 0:
                    close_message = f"✅ Closed {closed_count} position(s)"
                    if failed_count > 0:
                        close_message += f"\n⚠️ Failed to close {failed_count} position(s)"
                else:
                    close_message = "ℹ️ No positions to close"
                
                await query.edit_message_text(
                    f"{close_message}\n\n"
                    f"⏳ <b>Pausing strategy...</b>\n\n"
                    f"Run ID: <code>{run_id_short}</code>",
                    parse_mode='HTML'
                )
                
            except Exception as e:
                self.logger.error(f"Error getting/closing positions: {e}")
                # Continue to stop strategy even if position closing failed
                await query.edit_message_text(
                    f"⚠️ <b>Warning:</b> Could not close positions: {str(e)}\n\n"
                    f"⏳ <b>Pausing strategy anyway...</b>\n\n"
                    f"Run ID: <code>{run_id_short}</code>",
                    parse_mode='HTML'
                )
            
            # Stop strategy
            success = await self.process_manager.stop_strategy(run_id)
            
            # Immediately sync status to ensure DB is accurate
            if success:
                await self.process_manager.sync_status_with_supervisor()
            
            if success:
                await self.audit_logger.log_strategy_stop(str(user["id"]), run_id)
                final_message = (
                    f"✅ <b>Strategy Paused</b>\n\n"
                    f"Run ID: <code>{run_id_short}</code>\n"
                    f"Status: stopped\n\n"
                )
                if closed_count > 0:
                    final_message += f"✅ Closed {closed_count} position(s)\n"
                if failed_count > 0:
                    final_message += f"⚠️ Failed to close {failed_count} position(s)\n"
                if not position_ids:
                    final_message += "ℹ️ No positions were open\n"
                
                await query.edit_message_text(final_message, parse_mode='HTML')
            else:
                await query.edit_message_text(
                    f"❌ <b>Failed to Pause Strategy</b>\n\n"
                    f"Run ID: <code>{run_id_short}</code>\n"
                    f"Please try again or check logs.",
                    parse_mode='HTML'
                )
                
        except Exception as e:
            self.logger.error(f"Stop strategy close callback error: {e}", exc_info=True)
            await query.edit_message_text(
                f"❌ Error pausing strategy: {str(e)}",
                parse_mode='HTML'
            )
    
    async def resume_strategy_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /resume_strategy command - shows interactive list of stopped strategies."""
        user, _ = await self.require_auth(update, context)
        if not user:
            return
        
        try:
            # Get user's stopped strategies (admins see all)
            is_admin = user.get("is_admin", False)
            user_id = None if is_admin else user["id"]
            
            # Query strategies with config info - only stopped/error status
            if user_id:
                query = """
                    SELECT 
                        sr.id, sr.user_id, sr.account_id, sr.config_id,
                        sr.supervisor_program_name, sr.status, sr.control_api_port,
                        sr.log_file_path, sr.started_at, sr.stopped_at,
                        sc.config_name, sc.strategy_type
                    FROM strategy_runs sr
                    JOIN strategy_configs sc ON sr.config_id = sc.id
                    WHERE sr.user_id = :user_id 
                    AND sr.status IN ('stopped', 'error')
                    ORDER BY sr.stopped_at DESC NULLS LAST, sr.started_at DESC
                """
                strategies = await self.database.fetch_all(query, {"user_id": user_id})
            else:
                query = """
                    SELECT 
                        sr.id, sr.user_id, sr.account_id, sr.config_id,
                        sr.supervisor_program_name, sr.status, sr.control_api_port,
                        sr.log_file_path, sr.started_at, sr.stopped_at,
                        sc.config_name, sc.strategy_type
                    FROM strategy_runs sr
                    JOIN strategy_configs sc ON sr.config_id = sc.id
                    WHERE sr.status IN ('stopped', 'error')
                    ORDER BY sr.stopped_at DESC NULLS LAST, sr.started_at DESC
                """
                strategies = await self.database.fetch_all(query)
            
            strategies = [dict(row) for row in strategies]
            
            if not strategies:
                await update.message.reply_text(
                    "▶️ <b>No Stopped Strategies</b>\n\n"
                    "No stopped strategies available to resume.\n"
                    "Start a strategy with /run",
                    parse_mode='HTML'
                )
                return
            
            # Create strategy selection keyboard
            keyboard = []
            for strat in strategies:
                run_id = str(strat['id'])
                run_id_short = run_id[:8]
                config_name = strat.get('config_name', 'Unknown')
                strategy_type = strat.get('strategy_type', 'unknown')
                
                # Format strategy type for display
                strategy_type_display = {
                    'funding_arbitrage': 'Funding Arb',
                    'grid': 'Grid'
                }.get(strategy_type, strategy_type.title())
                
                status_emoji = {
                    'stopped': '⚫',
                    'error': '🔴'
                }.get(strat['status'], '⚪')
                
                # Button label: "⚫ e9680e47 - Funding Arb"
                button_label = f"{status_emoji} {run_id_short} - {strategy_type_display}"
                keyboard.append([InlineKeyboardButton(
                    button_label,
                    callback_data=f"resume_strategy:{run_id}"
                )])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            title = "▶️ <b>Resume Strategy</b>"
            await update.message.reply_text(
                f"{title}\n\n"
                "Select a stopped strategy to resume:",
                parse_mode='HTML',
                reply_markup=reply_markup
            )
            
        except Exception as e:
            self.logger.error(f"Resume strategy command error: {e}")
            await update.message.reply_text(
                f"❌ Failed to load strategies: {str(e)}",
                parse_mode='HTML'
            )
    
    async def resume_strategy_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle callback for resuming a strategy."""
        query = update.callback_query
        await query.answer()
        
        user, _ = await self.require_auth(update, context)
        if not user:
            return
        
        # Parse callback data: "resume_strategy:{run_id}"
        callback_data = query.data
        if not callback_data.startswith("resume_strategy:"):
            await query.edit_message_text(
                "❌ Invalid selection. Please use /resume_strategy again.",
                parse_mode='HTML'
            )
            return
        
        run_id = callback_data.split(":", 1)[1]
        
        try:
            # Verify ownership (security check)
            is_admin = user.get("is_admin", False)
            if is_admin:
                # Admins can resume any strategy
                verify_query = """
                    SELECT id, status FROM strategy_runs
                    WHERE id = :run_id
                """
                row = await self.database.fetch_one(
                    verify_query,
                    {"run_id": run_id}
                )
            else:
                # Regular users can only resume their own strategies
                verify_query = """
                    SELECT id, status FROM strategy_runs
                    WHERE id = :run_id AND user_id = :user_id
                """
                row = await self.database.fetch_one(
                    verify_query,
                    {"run_id": run_id, "user_id": user["id"]}
                )
            
            if not row:
                await query.edit_message_text(
                    "❌ Strategy not found or you don't have permission to resume it",
                    parse_mode='HTML'
                )
                return
            
            current_status = row['status']
            if current_status not in ('stopped', 'error', 'paused'):
                await query.edit_message_text(
                    f"ℹ️ Strategy is not stopped or paused (status: {current_status}).\n"
                    f"Only stopped/paused strategies can be resumed.",
                    parse_mode='HTML'
                )
                return
            
            # Show loading message
            run_id_short = run_id[:8]
            await query.edit_message_text(
                f"⏳ <b>Resuming strategy...</b>\n\n"
                f"Run ID: <code>{run_id_short}</code>",
                parse_mode='HTML'
            )
            
            # Resume strategy
            success = await self.process_manager.resume_strategy(run_id)
            
            # Immediately sync status to ensure DB is accurate
            if success:
                await self.process_manager.sync_status_with_supervisor()
                
                # Get updated status from DB
                status_row = await self.database.fetch_one(
                    "SELECT status FROM strategy_runs WHERE id = :run_id",
                    {"run_id": run_id}
                )
                actual_status = status_row['status'] if status_row else 'starting'
            else:
                actual_status = None
            
            if success:
                await self.audit_logger.log_action(
                    user_id=str(user["id"]),
                    action="resume_strategy",
                    details={"run_id": run_id}
                )
                await query.edit_message_text(
                    f"✅ <b>Strategy Resumed</b>\n\n"
                    f"Run ID: <code>{run_id_short}</code>\n"
                    f"Status: {actual_status}",
                    parse_mode='HTML'
                )
            else:
                await query.edit_message_text(
                    f"❌ <b>Failed to Resume Strategy</b>\n\n"
                    f"Run ID: <code>{run_id_short}</code>\n"
                    f"Please check logs or try again.",
                    parse_mode='HTML'
                )
                
        except Exception as e:
            self.logger.error(f"Resume strategy callback error: {e}", exc_info=True)
            await query.edit_message_text(
                f"❌ Error resuming strategy: {str(e)}",
                parse_mode='HTML'
            )
    
    async def logs_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /logs command - shows filter options."""
        user, _ = await self.require_auth(update, context)
        if not user:
            return
        
        try:
            # Show filter selection buttons
            keyboard = [
                [InlineKeyboardButton("🟢 Running", callback_data="filter_logs:running")],
                [InlineKeyboardButton("⚫ Stopped", callback_data="filter_logs:stopped")],
                [InlineKeyboardButton("📋 All", callback_data="filter_logs:all")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            title = "📄 <b>View Logs</b>"
            
            await update.message.reply_text(
                f"{title}\n\n"
                "Select a filter to view strategy logs:",
                parse_mode='HTML',
                reply_markup=reply_markup
            )
            
        except Exception as e:
            self.logger.error(f"Logs command error: {e}", exc_info=True)
            await update.message.reply_text(
                f"❌ Error: {str(e)}",
                parse_mode='HTML'
            )
    
    async def filter_logs_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle filter selection for logs."""
        query = update.callback_query
        await query.answer()
        
        user, _ = await self.require_auth(update, context)
        if not user:
            return
        
        # Parse filter type from callback data: "filter_logs:{filter_type}"
        callback_data = query.data
        filter_type = callback_data.split(":", 1)[1]
        
        try:
            # Get strategies with filter applied
            is_admin = user.get("is_admin", False)
            user_id = None if is_admin else user["id"]
            strategies = await self._get_logs_strategies_with_filter(user_id, filter_type)
            
            filter_label = {
                'running': '🟢 Running',
                'stopped': '⚫ Stopped',
                'all': '📋 All'
            }.get(filter_type, filter_type.title())
            
            if not strategies:
                # Show back button instead of all filters
                keyboard = [
                    [InlineKeyboardButton("⬅️ Back", callback_data="back_to_logs_filters")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await query.edit_message_text(
                    f"📄 <b>View Logs</b> - {filter_label}\n\n"
                    "No strategies found matching this filter.\n"
                    "Start a strategy with /run",
                    parse_mode='HTML',
                    reply_markup=reply_markup
                )
                return
            
            # Create strategy selection keyboard
            keyboard = []
            for strat in strategies:
                run_id = str(strat['id'])
                run_id_short = run_id[:8]
                config_name = strat.get('config_name', 'Unknown')
                strategy_type = strat.get('strategy_type', 'unknown')
                
                # Format strategy type for display
                strategy_type_display = {
                    'funding_arbitrage': 'Funding Arb',
                    'grid': 'Grid'
                }.get(strategy_type, strategy_type.title())
                
                status_emoji = {
                    'running': '🟢',
                    'starting': '🟡',
                    'stopped': '⚫',
                    'error': '🔴',
                    'paused': '⏸'
                }.get(strat['status'], '⚪')
                
                # Button label: "🟢 e9680e47 - Funding Arb"
                button_label = f"{status_emoji} {run_id_short} - {strategy_type_display}"
                keyboard.append([InlineKeyboardButton(
                    button_label,
                    callback_data=f"view_logs:{run_id}"
                )])
            
            # Show back button instead of all filter buttons
            keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="back_to_logs_filters")])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                f"📄 <b>View Logs</b> - {filter_label}\n\n"
                "Select a strategy to view logs:",
                parse_mode='HTML',
                reply_markup=reply_markup
            )
            
        except Exception as e:
            self.logger.error(f"Filter logs error: {e}")
            await query.edit_message_text(
                f"❌ Failed to filter logs: {str(e)}",
                parse_mode='HTML'
            )
    
    async def _get_logs_strategies_with_filter(self, user_id, filter_type: str):
        """Get strategies with filter applied for logs view."""
        # Build query based on filter
        if filter_type == 'running':
            status_list = ['starting', 'running', 'paused']
        elif filter_type == 'stopped':
            status_list = ['stopped', 'error']
        else:  # 'all'
            status_list = None
        
        if user_id:
            if status_list:
                placeholders = ','.join([':status' + str(i) for i in range(len(status_list))])
                params = {"user_id": user_id}
                for i, status in enumerate(status_list):
                    params[f"status{i}"] = status
                query = f"""
                    SELECT 
                        sr.id, sr.user_id, sr.account_id, sr.config_id,
                        sr.supervisor_program_name, sr.status, sr.control_api_port,
                        sr.log_file_path, sr.started_at, sr.last_heartbeat, sr.health_status,
                        sc.config_name, sc.strategy_type
                    FROM strategy_runs sr
                    JOIN strategy_configs sc ON sr.config_id = sc.id
                    WHERE sr.user_id = :user_id AND sr.status IN ({placeholders})
                    ORDER BY sr.started_at DESC
                """
                rows = await self.database.fetch_all(query, params)
            else:
                query = """
                    SELECT 
                        sr.id, sr.user_id, sr.account_id, sr.config_id,
                        sr.supervisor_program_name, sr.status, sr.control_api_port,
                        sr.log_file_path, sr.started_at, sr.last_heartbeat, sr.health_status,
                        sc.config_name, sc.strategy_type
                    FROM strategy_runs sr
                    JOIN strategy_configs sc ON sr.config_id = sc.id
                    WHERE sr.user_id = :user_id
                    ORDER BY sr.started_at DESC
                """
                rows = await self.database.fetch_all(query, {"user_id": user_id})
        else:
            # Admin - see all
            if status_list:
                placeholders = ','.join([':status' + str(i) for i in range(len(status_list))])
                params = {}
                for i, status in enumerate(status_list):
                    params[f"status{i}"] = status
                query = f"""
                    SELECT 
                        sr.id, sr.user_id, sr.account_id, sr.config_id,
                        sr.supervisor_program_name, sr.status, sr.control_api_port,
                        sr.log_file_path, sr.started_at, sr.last_heartbeat, sr.health_status,
                        sc.config_name, sc.strategy_type
                    FROM strategy_runs sr
                    JOIN strategy_configs sc ON sr.config_id = sc.id
                    WHERE sr.status IN ({placeholders})
                    ORDER BY sr.started_at DESC
                """
                rows = await self.database.fetch_all(query, params)
            else:
                query = """
                    SELECT 
                        sr.id, sr.user_id, sr.account_id, sr.config_id,
                        sr.supervisor_program_name, sr.status, sr.control_api_port,
                        sr.log_file_path, sr.started_at, sr.last_heartbeat, sr.health_status,
                        sc.config_name, sc.strategy_type
                    FROM strategy_runs sr
                    JOIN strategy_configs sc ON sr.config_id = sc.id
                    ORDER BY sr.started_at DESC
                """
                rows = await self.database.fetch_all(query)
        
        return [dict(row) for row in rows]
    
    async def view_logs_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle callback for viewing logs."""
        query = update.callback_query
        await query.answer()
        
        user, _ = await self.require_auth(update, context)
        if not user:
            return
        
        # Parse callback data: "view_logs:{run_id}"
        callback_data = query.data
        if not callback_data.startswith("view_logs:"):
            await query.edit_message_text(
                "❌ Invalid selection. Please use /logs again.",
                parse_mode='HTML'
            )
            return
        
        run_id = callback_data.split(":", 1)[1]
        
        try:
            # Verify ownership (security check)
            is_admin = user.get("is_admin", False)
            if is_admin:
                # Admins can view any strategy
                verify_query = """
                    SELECT id, supervisor_program_name, log_file_path, config_id
                    FROM strategy_runs
                    WHERE id = :run_id
                """
                row = await self.database.fetch_one(
                    verify_query,
                    {"run_id": run_id}
                )
            else:
                # Regular users can only view their own strategies
                verify_query = """
                    SELECT id, supervisor_program_name, log_file_path, config_id
                    FROM strategy_runs
                    WHERE id = :run_id AND user_id = :user_id
                """
                row = await self.database.fetch_one(
                    verify_query,
                    {"run_id": run_id, "user_id": user["id"]}
                )
            
            if not row:
                await query.edit_message_text(
                    "❌ Strategy not found or you don't have permission to view logs",
                    parse_mode='HTML'
                )
                return
            
            run_id_full = str(row["id"])
            run_id_short = run_id_full[:8]
            
            # Get config name for display
            config_id = str(row["config_id"])
            config_row = await self.database.fetch_one(
                "SELECT config_name, strategy_type FROM strategy_configs WHERE id = :id",
                {"id": config_id}
            )
            if config_row:
                config_name = config_row['config_name']
                strategy_type = config_row['strategy_type']
            else:
                config_name = 'Unknown'
                strategy_type = 'unknown'
            
            strategy_type_display = {
                'funding_arbitrage': 'Funding Arbitrage',
                'grid': 'Grid'
            }.get(strategy_type, strategy_type.title())
            
            # Show loading message
            await query.edit_message_text(
                f"⏳ <b>Loading logs...</b>\n\n"
                f"Strategy: {strategy_type_display}\n"
                f"Config: {config_name}\n"
                f"Run ID: <code>{run_id_short}</code>",
                parse_mode='HTML'
            )
            
            # Try to get log file from database first
            try:
                log_file = row["log_file_path"]
            except (KeyError, TypeError):
                log_file = None
            
            # If not in DB or file doesn't exist, try to find it by matching UUID prefix
            if not log_file or not Path(log_file).exists():
                logs_dir = self.process_manager.project_root / "logs"
                if logs_dir.exists():
                    # Find log file matching the UUID (full or partial)
                    matching_logs = list(logs_dir.glob("strategy_*.out.log"))
                    for log_path in matching_logs:
                        # Extract UUID from filename: strategy_<uuid>.out.log
                        log_uuid = log_path.stem.replace('strategy_', '').replace('.out', '')
                        # Match by first 8 characters or full UUID
                        if log_uuid.startswith(run_id_short) or run_id_full.startswith(log_uuid[:8]):
                            log_file = str(log_path)
                            break
            
            # If still no log file found, inform user
            if not log_file or not Path(log_file).exists():
                await query.edit_message_text(
                    f"📄 <b>Log File Not Found</b>\n\n"
                    f"Strategy: {strategy_type_display}\n"
                    f"Config: {config_name}\n"
                    f"Run ID: <code>{run_id_short}</code>\n\n"
                    f"⚠️ The log file may have been cleaned up or deleted.\n"
                    f"This is expected if logs were manually removed.",
                    parse_mode='HTML'
                )
                return
            
            if log_file and Path(log_file).exists():
                # Send log file as document
                with open(log_file, 'rb') as f:
                    await query.message.reply_document(
                        document=f,
                        filename=f"strategy_{run_id_short}_{strategy_type_display.lower().replace(' ', '_')}.log",
                        caption=f"📄 <b>Log file</b>\n\n"
                                f"Strategy: {strategy_type_display}\n"
                                f"Config: {config_name}\n"
                                f"Run ID: <code>{run_id_short}</code>",
                        parse_mode='HTML'
                    )
                
                # Update the message to show success
                await query.edit_message_text(
                    f"✅ <b>Log file sent!</b>\n\n"
                    f"Strategy: {strategy_type_display}\n"
                    f"Config: {config_name}\n"
                    f"Run ID: <code>{run_id_short}</code>\n\n"
                    f"📄 Check the document below.",
                    parse_mode='HTML'
                )
                
        except Exception as e:
            self.logger.error(f"View logs error: {e}", exc_info=True)
            await query.edit_message_text(
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
        
        config_data_raw = config_row['config_data']
        config_name = config_row['config_name']
        strategy_type = config_row['strategy_type']
        
        # Log config details for debugging
        self.logger.info(f"Loading config: name={config_name}, type={strategy_type}, id={config_id}")
        self.logger.debug(f"Config data raw type: {type(config_data_raw)}, value preview: {str(config_data_raw)[:200]}")
        
        # Parse config_data - JSONB might come back as string or dict
        import json
        if isinstance(config_data_raw, str):
            try:
                config_data = json.loads(config_data_raw)
            except json.JSONDecodeError as e:
                self.logger.error(f"Failed to parse config_data JSON: {e}, raw: {config_data_raw[:100]}")
                await query.edit_message_text(
                    f"❌ Invalid config data format: Failed to parse JSON",
                    parse_mode='HTML'
                )
                return
        elif isinstance(config_data_raw, dict):
            config_data = config_data_raw
        else:
            self.logger.error(f"Config data is not dict or string: {type(config_data_raw)}, value: {config_data_raw}")
            await query.edit_message_text(
                f"❌ Invalid config data format: Expected dict or JSON string, got {type(config_data_raw).__name__}",
                parse_mode='HTML'
            )
            return
        
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
                self.logger.info(f"Wrapped config_data with strategy wrapper. Final config keys: {list(config_data.keys())}")
            else:
                self.logger.info(f"Config already has strategy/config structure. Keys: {list(config_data.keys())}")
        else:
            self.logger.error(f"Config data is not a dict after parsing: {type(config_data)}, value: {config_data}")
            await query.edit_message_text(
                "❌ Invalid config data format: Config must be a dictionary",
                parse_mode='HTML'
            )
            return
        
        # Log final config structure for debugging
        config_dict = config_data.get('config', {}) if isinstance(config_data, dict) else {}
        self.logger.info(
            f"Final config_data structure: "
            f"strategy={config_data.get('strategy')}, "
            f"config_keys={list(config_dict.keys())}, "
            f"config_preview={str(config_dict)[:500]}"
        )
        
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
            self.logger.error(f"Start strategy error: {e}", exc_info=True)
            # Escape HTML special characters in error message to avoid parsing errors
            error_msg = str(e).replace('<', '&lt;').replace('>', '&gt;')
            await query.edit_message_text(
                f"❌ Failed to start strategy: {error_msg}",
                parse_mode='HTML'
            )
    
    def register_handlers(self, application):
        """Register strategy execution command and callback handlers"""
        # Commands
        application.add_handler(CommandHandler("run", self.run_strategy_command))
        application.add_handler(CommandHandler("list_strategies", self.list_strategies_command))
        application.add_handler(CommandHandler("stop_strategy", self.stop_strategy_command))
        application.add_handler(CommandHandler("resume_strategy", self.resume_strategy_command))
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
        application.add_handler(CallbackQueryHandler(
            self.view_logs_callback,
            pattern="^view_logs:"
        ))
        application.add_handler(CallbackQueryHandler(
            self.filter_strategies_callback,
            pattern="^filter_strategies:"
        ))
        application.add_handler(CallbackQueryHandler(
            self.filter_logs_callback,
            pattern="^filter_logs:"
        ))
        application.add_handler(CallbackQueryHandler(
            self.back_to_strategies_filters_callback,
            pattern="^back_to_strategies_filters$"
        ))
        application.add_handler(CallbackQueryHandler(
            self.back_to_logs_filters_callback,
            pattern="^back_to_logs_filters$"
        ))
        application.add_handler(CallbackQueryHandler(
            self.stop_strategy_callback,
            pattern="^stop_strategy:"
        ))
        # Stop strategy pause callback
        application.add_handler(CallbackQueryHandler(
            self.stop_strategy_pause_callback,
            pattern="^stop_strategy_pause:|^stop_strategy_cancel$"
        ))
        # Stop strategy close callback
        application.add_handler(CallbackQueryHandler(
            self.stop_strategy_close_callback,
            pattern="^stop_strategy_close:"
        ))
        application.add_handler(CallbackQueryHandler(
            self.resume_strategy_callback,
            pattern="^resume_strategy:"
        ))

