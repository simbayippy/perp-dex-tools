"""
Strategy execution handlers for Telegram bot
"""

from datetime import datetime
from pathlib import Path
import xmlrpc.client
import json
import yaml
import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler

from telegram_bot_service.handlers.base import BaseHandler


class StrategyHandler(BaseHandler):
    """Handler for strategy execution commands and callbacks"""
    
    def _clean_log_line(self, line: str) -> str:
        """
        Clean a log line by removing ANSI escape codes, timestamps, log levels, and module info.
        Returns only the core message content.
        
        Example:
            Input:  "[32m2025-11-12 21:03:57[0m | [1mINFO    [0m | [36munified_logger:_emit:618[0m | [1m=======================================================[0m"
            Output: "======================================================="
        """
        # Remove ANSI escape codes (both \x1b[ and [ formats)
        # Pattern matches: [32m, [0m, [1m, [36m, etc.
        line = re.sub(r'\x1b\[[0-9;]*m', '', line)  # \x1b[ format
        line = re.sub(r'\[[0-9;]+m', '', line)  # [ format
        
        # Split by pipe separator and take the last part (the actual message)
        # Format: timestamp | level | module:line | message
        parts = line.split('|')
        if len(parts) > 1:
            # Take the last part (the message)
            message = parts[-1].strip()
        else:
            # If no pipe separator, use the whole line
            message = line.strip()
        
        # Clean up any remaining extra whitespace
        message = re.sub(r'\s+', ' ', message).strip()
        
        return message
    
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
        
        # Store filter in context for later use
        context.user_data['strategy_filter'] = filter_type
        
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
                    "Start a strategy with /run_strategy",
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
                config_name = strat.get('config_name', 'Unknown')
                account_name = strat.get('account_name', 'Unknown')
                
                message += (
                    f"{status_emoji} <b>{run_id_short}</b>\n"
                    f"   Status: {status}\n"
                    f"   Config: {config_name}\n"
                    f"   Account: {account_name}\n"
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
                
                # Add action buttons: View Config + Stop/Resume
                row_buttons = []
                
                # View Config button (always available)
                row_buttons.append(
                    InlineKeyboardButton(
                        f"📋 Config",
                        callback_data=f"view_strategy_config:{run_id}"
                    )
                )
                
                # Stop/Resume button
                if status in ('running', 'starting'):
                    row_buttons.append(
                        InlineKeyboardButton(
                            f"🛑 Stop",
                            callback_data=f"stop_strategy:{run_id}"
                        )
                    )
                elif status in ('stopped', 'error', 'paused'):
                    row_buttons.append(
                        InlineKeyboardButton(
                            f"🟢 Resume",
                            callback_data=f"resume_strategy:{run_id}"
                        )
                    )
                
                keyboard.append(row_buttons)
            
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
                    SELECT sr.id, sr.status, sr.started_at, sr.stopped_at, sr.supervisor_program_name,
                           sc.config_name, a.account_name
                    FROM strategy_runs sr
                    LEFT JOIN strategy_configs sc ON sr.config_id = sc.id
                    LEFT JOIN accounts a ON sr.account_id = a.id
                    WHERE sr.user_id = :user_id AND sr.status IN ({placeholders})
                    ORDER BY sr.started_at DESC
                """
                rows = await self.database.fetch_all(query, params)
            else:
                query = """
                    SELECT sr.id, sr.status, sr.started_at, sr.stopped_at, sr.supervisor_program_name,
                           sc.config_name, a.account_name
                    FROM strategy_runs sr
                    LEFT JOIN strategy_configs sc ON sr.config_id = sc.id
                    LEFT JOIN accounts a ON sr.account_id = a.id
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
                    SELECT sr.id, sr.status, sr.started_at, sr.stopped_at, sr.supervisor_program_name,
                           sc.config_name, a.account_name
                    FROM strategy_runs sr
                    LEFT JOIN strategy_configs sc ON sr.config_id = sc.id
                    LEFT JOIN accounts a ON sr.account_id = a.id
                    WHERE sr.status IN ({placeholders})
                    ORDER BY sr.started_at DESC
                """
                rows = await self.database.fetch_all(query, params)
            else:
                query = """
                    SELECT sr.id, sr.status, sr.started_at, sr.stopped_at, sr.supervisor_program_name,
                           sc.config_name, a.account_name
                    FROM strategy_runs sr
                    LEFT JOIN strategy_configs sc ON sr.config_id = sc.id
                    LEFT JOIN accounts a ON sr.account_id = a.id
                    ORDER BY sr.started_at DESC
                """
                rows = await self.database.fetch_all(query)
        
        return [dict(row) for row in rows]
    
    async def run_strategy_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /run_strategy command."""
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
                    "Start a strategy with /run_strategy",
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
    
    async def pause_strategy_direct_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle direct pause from detail view - pause immediately without dialog."""
        query = update.callback_query
        await query.answer()
        
        user, _ = await self.require_auth(update, context)
        if not user:
            return
        
        callback_data = query.data
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
                    "❌ Strategy not found or you don't have permission to pause it",
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
                # Add back button to return to detail view
                keyboard = [
                    [InlineKeyboardButton("⬅️ Back to Details", callback_data=f"view_strategy_config:{run_id}")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await query.edit_message_text(
                    f"✅ <b>Strategy Paused</b>\n\n"
                    f"Run ID: <code>{run_id_short}</code>\n"
                    f"Status: stopped\n\n"
                    f"ℹ️ <b>Note:</b> Positions remain open. "
                    f"You can close them manually with /close or resume the strategy later.",
                    parse_mode='HTML',
                    reply_markup=reply_markup
                )
            else:
                await query.edit_message_text(
                    f"❌ <b>Failed to Pause Strategy</b>\n\n"
                    f"Run ID: <code>{run_id_short}</code>\n"
                    f"Please try again or check logs.",
                    parse_mode='HTML'
                )
                
        except Exception as e:
            self.logger.error(f"Pause strategy direct callback error: {e}", exc_info=True)
            await query.edit_message_text(
                f"❌ Error pausing strategy: {str(e)}",
                parse_mode='HTML'
            )
    
    async def resume_strategy_direct_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle direct resume from detail view - resume immediately."""
        query = update.callback_query
        await query.answer()
        
        user, _ = await self.require_auth(update, context)
        if not user:
            return
        
        callback_data = query.data
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
                # Add back button to return to detail view
                keyboard = [
                    [InlineKeyboardButton("⬅️ Back to Details", callback_data=f"view_strategy_config:{run_id}")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await query.edit_message_text(
                    f"✅ <b>Strategy Resumed</b>\n\n"
                    f"Run ID: <code>{run_id_short}</code>\n"
                    f"Status: {actual_status}",
                    parse_mode='HTML',
                    reply_markup=reply_markup
                )
            else:
                await query.edit_message_text(
                    f"❌ <b>Failed to Resume Strategy</b>\n\n"
                    f"Run ID: <code>{run_id_short}</code>\n"
                    f"Please check logs or try again.",
                    parse_mode='HTML'
                )
                
        except Exception as e:
            self.logger.error(f"Resume strategy direct callback error: {e}", exc_info=True)
            await query.edit_message_text(
                f"❌ Error resuming strategy: {str(e)}",
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
                # Add back button to return to detail view
                keyboard = [
                    [InlineKeyboardButton("⬅️ Back to Details", callback_data=f"view_strategy_config:{run_id}")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await query.edit_message_text(
                    f"✅ <b>Strategy Paused</b>\n\n"
                    f"Run ID: <code>{run_id_short}</code>\n"
                    f"Status: stopped\n\n"
                    f"ℹ️ <b>Note:</b> Positions remain open. "
                    f"You can close them manually with /close or resume the strategy later.",
                    parse_mode='HTML',
                    reply_markup=reply_markup
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
                    "Start a strategy with /run_strategy",
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
                # Add back button to return to detail view
                keyboard = [
                    [InlineKeyboardButton("⬅️ Back to Details", callback_data=f"view_strategy_config:{run_id}")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await query.edit_message_text(
                    f"✅ <b>Strategy Resumed</b>\n\n"
                    f"Run ID: <code>{run_id_short}</code>\n"
                    f"Status: {actual_status}",
                    parse_mode='HTML',
                    reply_markup=reply_markup
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
    
    async def view_strategy_config_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle view strategy config callback."""
        query = update.callback_query
        await query.answer()
        
        user, _ = await self.require_auth(update, context)
        if not user:
            return
        
        # Parse callback data: "view_strategy_config:{run_id}"
        callback_data = query.data
        if not callback_data.startswith("view_strategy_config:"):
            await query.edit_message_text(
                "❌ Invalid selection. Please use /list_strategies again.",
                parse_mode='HTML'
            )
            return
        
        run_id = callback_data.split(":", 1)[1]
        
        try:
            # Get strategy run details
            strategy_row = await self.database.fetch_one(
                """
                SELECT sr.id, sr.status, sr.config_id, sr.supervisor_program_name,
                       sc.config_name, sc.config_data, sc.strategy_type, sc.user_id as config_user_id,
                       sc.is_template, a.account_name
                FROM strategy_runs sr
                LEFT JOIN strategy_configs sc ON sr.config_id = sc.id
                LEFT JOIN accounts a ON sr.account_id = a.id
                WHERE sr.id = :run_id
                """,
                {"run_id": run_id}
            )
            
            if not strategy_row:
                await query.edit_message_text(
                    "❌ Strategy not found",
                    parse_mode='HTML'
                )
                return
            
            # Convert Row to dict for safe access
            strategy_dict = dict(strategy_row)
            
            # Check permissions (user can only view their own strategies unless admin)
            is_admin = user.get("is_admin", False)
            if not is_admin:
                user_strategy = await self.database.fetch_one(
                    "SELECT user_id FROM strategy_runs WHERE id = :run_id",
                    {"run_id": run_id}
                )
                if not user_strategy or str(user_strategy["user_id"]) != str(user["id"]):
                    await query.edit_message_text(
                        "❌ You don't have permission to view this strategy",
                        parse_mode='HTML'
                    )
                    return
            
            config_data_raw = strategy_dict.get('config_data')
            config_name = strategy_dict.get('config_name') or 'Unknown'
            strategy_type = strategy_dict.get('strategy_type') or 'Unknown'
            account_name = strategy_dict.get('account_name') or 'Unknown'
            status = strategy_dict.get('status')
            supervisor_name = strategy_dict.get('supervisor_program_name', '')
            config_user_id = strategy_dict.get('config_user_id')
            is_template = strategy_dict.get('is_template', False)
            run_id_short = run_id[:8]
            
            # Determine config ownership/type
            config_type_info = ""
            if is_template or not config_user_id:
                config_type_info = "📄 <b>Template Config</b> (Public)\n"
            elif str(config_user_id) == str(user["id"]):
                config_type_info = "👤 <b>Your Config</b>\n"
            else:
                config_type_info = "👥 <b>Other User's Config</b>\n"
            
            # Get config file path (where supervisor reads it from)
            import tempfile
            config_file_path = Path(tempfile.gettempdir()) / f"strategy_{run_id}.yml"
            
            # Parse config data
            import json
            import yaml
            if isinstance(config_data_raw, str):
                config_dict = json.loads(config_data_raw)
            else:
                config_dict = config_data_raw
            
            # Extract actual config (might be nested)
            actual_config = config_dict.get('config', config_dict)
            
            # Format config for display (show key parameters)
            message = (
                f"📋 <b>Strategy Config</b>\n\n"
                f"{config_type_info}"
                f"<b>Run ID:</b> {run_id_short}\n"
                f"<b>Status:</b> {status}\n"
                f"<b>Config:</b> {config_name}\n"
                f"<b>Account:</b> {account_name}\n"
                f"<b>Strategy:</b> {strategy_type.replace('_', ' ').title()}\n"
            )
            
            # Show config file path if available
            if supervisor_name:
                message += f"<b>Supervisor:</b> {supervisor_name}\n"
            message += f"<b>Config File:</b> <code>{config_file_path}</code>\n\n"
            
            message += f"<b>Configuration:</b>\n"
            
            # Format key config parameters
            if strategy_type == 'funding_arbitrage':
                target_margin = actual_config.get('target_margin')
                target_exposure = actual_config.get('target_exposure')
                scan_exchanges = actual_config.get('scan_exchanges', [])
                mandatory_exchange = actual_config.get('mandatory_exchange')
                max_positions = actual_config.get('max_positions', 1)
                min_profit_rate = actual_config.get('min_profit_rate')
                
                if target_margin:
                    message += f"💰 Target Margin: ${target_margin:.2f}\n"
                elif target_exposure:
                    message += f"💰 Target Exposure: ${target_exposure:.2f} (deprecated)\n"
                
                if scan_exchanges:
                    exchanges_str = ', '.join([ex.upper() for ex in scan_exchanges])
                    message += f"🏦 Exchanges: {exchanges_str}\n"
                
                if mandatory_exchange:
                    message += f"⭐ Mandatory: {mandatory_exchange.upper()}\n"
                
                message += f"📊 Max Positions: {max_positions}\n"
                
                if min_profit_rate:
                    min_profit_pct = float(min_profit_rate) * 100
                    message += f"📈 Min Profit Rate: {min_profit_pct:.4f}%\n"
            else:
                # For other strategy types, show a summary
                message += "<code>"
                config_yaml = yaml.dump(actual_config, default_flow_style=False, indent=2, sort_keys=False)
                # Truncate if too long
                if len(config_yaml) > 1000:
                    message += config_yaml[:1000] + "\n... (truncated)"
                else:
                    message += config_yaml
                message += "</code>"
            
            # Add action buttons
            keyboard = []
            
            # Add Pause/Resume button based on status
            # Use direct pause/resume callbacks (no dialog) when called from detail view
            if status in ('running', 'starting'):
                keyboard.append([
                    InlineKeyboardButton(
                        "⏸️ Pause",
                        callback_data=f"pause_strategy_direct:{run_id}"
                    )
                ])
            elif status in ('stopped', 'error', 'paused'):
                keyboard.append([
                    InlineKeyboardButton(
                        "▶️ Resume",
                        callback_data=f"resume_strategy_direct:{run_id}"
                    )
                ])
            
            # Add Edit Config button if user owns the config OR if it's a template
            # (templates will auto-create a copy when editing)
            if (config_user_id and str(config_user_id) == str(user["id"])) or is_template or not config_user_id:
                keyboard.append([
                    InlineKeyboardButton(
                        "✏️ Edit Config",
                        callback_data=f"edit_strategy_config:{run_id}"
                    )
                ])
            
            # Add Delete Strategy button
            keyboard.append([
                InlineKeyboardButton(
                    "🗑️ Delete Strategy",
                    callback_data=f"delete_strategy:{run_id}"
                )
            ])
            
            # Store current filter in context for later use
            # Try to get filter from context, default to 'all'
            current_filter = context.user_data.get('strategy_filter', 'all')
            
            # Add back button - use filter if available
            keyboard.append([
                InlineKeyboardButton("⬅️ Back to List", callback_data=f"filter_strategies:{current_filter}")
            ])
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                message,
                parse_mode='HTML',
                reply_markup=reply_markup
            )
            
        except Exception as e:
            self.logger.error(f"View strategy config error: {e}", exc_info=True)
            error_msg = str(e).replace('<', '&lt;').replace('>', '&gt;')
            await query.edit_message_text(
                f"❌ Failed to load config: {error_msg}",
                parse_mode='HTML'
            )
    
    async def edit_strategy_config_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle edit config from strategy view."""
        query = update.callback_query
        await query.answer()
        
        user, _ = await self.require_auth(update, context)
        if not user:
            return
        
        callback_data = query.data
        if not callback_data.startswith("edit_strategy_config:"):
            await query.edit_message_text(
                "❌ Invalid selection.",
                parse_mode='HTML'
            )
            return
        
        run_id = callback_data.split(":", 1)[1]
        
        try:
            # Get strategy config_id and config details
            strategy_row = await self.database.fetch_one(
                """
                SELECT sr.config_id, sc.user_id as config_user_id, sc.config_name, 
                       sc.strategy_type, sc.config_data, sc.is_template
                FROM strategy_runs sr
                LEFT JOIN strategy_configs sc ON sr.config_id = sc.id
                WHERE sr.id = :run_id
                """,
                {"run_id": run_id}
            )
            
            if not strategy_row:
                await query.edit_message_text(
                    "❌ Strategy not found",
                    parse_mode='HTML'
                )
                return
            
            # Convert Row to dict
            strategy_dict = dict(strategy_row)
            
            config_id = strategy_dict.get('config_id')
            config_user_id = strategy_dict.get('config_user_id')
            config_name = strategy_dict.get('config_name')
            strategy_type = strategy_dict.get('strategy_type')
            config_data = strategy_dict.get('config_data')
            is_template = strategy_dict.get('is_template', False)
            
            # Track if we created a copy
            copy_created = False
            
            # Check if config is a template (public template)
            if is_template or not config_user_id:
                copy_created = True
                # Automatically create a copy of the template
                template_name = config_name or "Template"
                copy_name = f"{template_name} (Copy)"
                
                # Check if copy name already exists, add number if needed
                existing_count_row = await self.database.fetch_one(
                    """
                    SELECT COUNT(*) as count
                    FROM strategy_configs
                    WHERE user_id = :user_id AND config_name LIKE :pattern
                    """,
                    {"user_id": str(user["id"]), "pattern": f"{copy_name}%"}
                )
                
                existing_count = 0
                if existing_count_row:
                    existing_count = dict(existing_count_row).get('count', 0)
                
                if existing_count > 0:
                    copy_name = f"{template_name} (Copy {existing_count + 1})"
                
                # Parse config_data
                if isinstance(config_data, str):
                    try:
                        config_data_dict = json.loads(config_data)
                    except json.JSONDecodeError:
                        try:
                            config_data_dict = yaml.safe_load(config_data)
                        except Exception:
                            config_data_dict = {}
                else:
                    config_data_dict = config_data or {}
                
                # Create copy
                new_config_result = await self.database.fetch_one(
                    """
                    INSERT INTO strategy_configs (
                        user_id, config_name, strategy_type, config_data,
                        is_template, is_active, created_at, updated_at
                    )
                    VALUES (
                        :user_id, :config_name, :strategy_type, CAST(:config_data AS jsonb),
                        FALSE, TRUE, NOW(), NOW()
                    )
                    RETURNING id
                    """,
                    {
                        "user_id": str(user["id"]),
                        "config_name": copy_name,
                        "strategy_type": strategy_type,
                        "config_data": json.dumps(config_data_dict)
                    }
                )
                
                new_config_id = str(dict(new_config_result)['id'])
                
                # Update strategy to use the new copy
                await self.database.execute(
                    """
                    UPDATE strategy_runs
                    SET config_id = :new_config_id
                    WHERE id = :run_id
                    """,
                    {"new_config_id": new_config_id, "run_id": run_id}
                )
                
                # If strategy is running, regenerate its temp config file
                strategy_status_check = await self.database.fetch_one(
                    "SELECT status FROM strategy_runs WHERE id = :run_id",
                    {"run_id": run_id}
                )
                if strategy_status_check:
                    status = dict(strategy_status_check).get('status')
                    if status in ('running', 'starting', 'paused'):
                        # Regenerate temp config file
                        import tempfile
                        from pathlib import Path
                        from datetime import datetime
                        from decimal import Decimal
                        
                        temp_dir = Path(tempfile.gettempdir())
                        config_file = temp_dir / f"strategy_{run_id}.yml"
                        
                        try:
                            full_config = {
                                "strategy": strategy_type,
                                "created_at": datetime.now().isoformat(),
                                "version": "1.0",
                                "config": config_data_dict
                            }
                            
                            # Register Decimal representer for YAML
                            def decimal_representer(dumper, data):
                                return dumper.represent_scalar('tag:yaml.org,2002:float', str(data))
                            yaml.add_representer(Decimal, decimal_representer)
                            
                            with open(config_file, 'w') as f:
                                yaml.dump(
                                    full_config,
                                    f,
                                    default_flow_style=False,
                                    sort_keys=False,
                                    allow_unicode=True,
                                    indent=2
                                )
                            self.logger.info(f"Regenerated temp config file for strategy {run_id[:8]}: {config_file}")
                        except Exception as e:
                            self.logger.error(f"Failed to regenerate temp config for strategy {run_id[:8]}: {e}", exc_info=True)
                
                await self.audit_logger.log_action(
                    str(user["id"]),
                    "copy_template_for_strategy",
                    {
                        "run_id": run_id,
                        "template_config_id": config_id,
                        "new_config_id": new_config_id,
                        "new_config_name": copy_name
                    }
                )
                
                # Update variables to use the new config
                config_id = new_config_id
                config_name = copy_name
                config_user_id = str(user["id"])  # Update to user's ID since it's now their copy
                # Use the config_data_dict we already parsed for the copy
                config_data = config_data_dict
                self.logger.info(f"Created copy of template config {config_id} -> {new_config_id} for strategy {run_id[:8]}")
            
            # Check permissions (now that we've created a copy if needed)
            if not config_user_id or str(config_user_id) != str(user["id"]):
                await query.edit_message_text(
                    "❌ You don't have permission to edit this config.",
                    parse_mode='HTML'
                )
                return
            
            if not config_id:
                await query.edit_message_text(
                    "❌ Config not found for this strategy.",
                    parse_mode='HTML'
                )
                return
            
            # Parse config_data for display (only if we didn't already parse it during copy creation)
            if isinstance(config_data, str):
                try:
                    config_data = json.loads(config_data)
                except json.JSONDecodeError:
                    try:
                        config_data = yaml.safe_load(config_data)
                    except Exception:
                        pass
            config_data = config_data or {}
            
            # Get current filter from context (stored when filtering strategies)
            current_filter = context.user_data.get('strategy_filter', 'all')
            
            # Start edit wizard
            context.user_data['wizard'] = {
                'type': 'edit_config',
                'step': 1,
                'data': {
                    'config_id': config_id,
                    'config_name': config_name or "config",
                    'strategy_type': strategy_type,
                    'run_id': run_id,  # Store run_id to show which strategy is affected
                    'strategy_filter': current_filter  # Store filter to go back to correct list
                }
            }
            
            # Build config display
            structured_config = {
                "strategy": strategy_type,
                "config": config_data
            }
            config_yaml = yaml.dump(structured_config, default_flow_style=False, indent=2, sort_keys=False)
            
            # Telegram message limit is 4096 characters
            # Reserve space for header text and buttons (~500 chars)
            max_config_length = 3500
            
            # Check if strategy is running
            strategy_status_row = await self.database.fetch_one(
                "SELECT status FROM strategy_runs WHERE id = :run_id",
                {"run_id": run_id}
            )
            strategy_status = None
            if strategy_status_row:
                strategy_status = dict(strategy_status_row).get('status')
            
            status_note = ""
            if strategy_status in ('running', 'starting'):
                status_note = f"\n\nℹ️ <b>Note:</b> This strategy is currently <b>{strategy_status}</b>. Config changes will be reloaded instantly."
            elif strategy_status == 'paused':
                status_note = f"\n\n⚠️ <b>Note:</b> This strategy is currently <b>{strategy_status}</b>. Changes will apply when resumed."
            
            # Check if we created a copy (template was used)
            copy_note = ""
            if copy_created:
                copy_note = f"\n\n📋 <b>Note:</b> A copy of the template config was created: <b>{config_name}</b>\n"
                copy_note += "The strategy now uses this copy, which you can edit freely."
            
            # Build header message
            header_message = (
                f"✏️ <b>Edit Config: {config_name}</b>\n\n"
                f"Strategy Type: <b>{strategy_type}</b>\n"
                f"Run ID: <code>{run_id[:8]}</code>{copy_note}{status_note}\n\n"
                f"Current config (YAML):\n"
            )
            
            # If config is too long, send it in a separate message
            if len(config_yaml) > max_config_length:
                # Send header first
                await query.edit_message_text(
                    header_message +
                    f"<code>{config_yaml[:max_config_length]}...</code>\n\n"
                    f"⚠️ Config is too long to display fully. Sending full config in next message...",
                    parse_mode='HTML',
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("⬅️ Back to Strategy", callback_data=f"view_strategy_config:{run_id}")]
                    ])
                )
                
                # Send full config in a separate message
                await query.message.reply_text(
                    f"📋 <b>Full Config (YAML):</b>\n\n"
                    f"<code>{config_yaml}</code>\n\n"
                    f"Send updated config as JSON/YAML, or 'cancel' to cancel:",
                    parse_mode='HTML'
                )
            else:
                # Send everything in one message
                await query.edit_message_text(
                    header_message +
                    f"<code>{config_yaml}</code>\n\n"
                    f"Send updated config as JSON/YAML, or 'cancel' to cancel:",
                    parse_mode='HTML',
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("⬅️ Back to Strategy", callback_data=f"view_strategy_config:{run_id}")]
                    ])
                )
            
        except Exception as e:
            self.logger.error(f"Edit strategy config error: {e}", exc_info=True)
            await query.edit_message_text(
                f"❌ Failed to edit config: {str(e)}",
                parse_mode='HTML'
            )
    
    async def delete_strategy_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle delete strategy button click - show confirmation."""
        query = update.callback_query
        await query.answer()
        
        user, _ = await self.require_auth(update, context)
        if not user:
            return
        
        callback_data = query.data
        if not callback_data.startswith("delete_strategy:"):
            await query.edit_message_text(
                "❌ Invalid selection.",
                parse_mode='HTML'
            )
            return
        
        run_id = callback_data.split(":", 1)[1]
        
        try:
            # Get strategy details
            strategy_row = await self.database.fetch_one(
                """
                SELECT sr.id, sr.status, sr.supervisor_program_name,
                       sc.config_name, a.account_name
                FROM strategy_runs sr
                LEFT JOIN strategy_configs sc ON sr.config_id = sc.id
                LEFT JOIN accounts a ON sr.account_id = a.id
                WHERE sr.id = :run_id
                """,
                {"run_id": run_id}
            )
            
            if not strategy_row:
                await query.edit_message_text(
                    "❌ Strategy not found",
                    parse_mode='HTML'
                )
                return
            
            # Convert Row to dict
            strategy_dict = dict(strategy_row)
            
            # Check permissions (user can only delete their own strategies unless admin)
            is_admin = user.get("is_admin", False)
            if not is_admin:
                user_strategy = await self.database.fetch_one(
                    "SELECT user_id FROM strategy_runs WHERE id = :run_id",
                    {"run_id": run_id}
                )
                if not user_strategy or str(user_strategy["user_id"]) != str(user["id"]):
                    await query.edit_message_text(
                        "❌ You don't have permission to delete this strategy.",
                        parse_mode='HTML'
                    )
                    return
            
            status = strategy_dict.get('status')
            config_name = strategy_dict.get('config_name') or 'Unknown'
            account_name = strategy_dict.get('account_name') or 'Unknown'
            supervisor_name = strategy_dict.get('supervisor_program_name')
            run_id_short = run_id[:8]
            
            # Check if strategy is running
            if status in ('running', 'starting', 'paused'):
                await query.edit_message_text(
                    f"⚠️ <b>Cannot Delete Running Strategy</b>\n\n"
                    f"Strategy <b>{run_id_short}</b> is currently {status}.\n"
                    f"Please stop the strategy first before deleting.",
                    parse_mode='HTML'
                )
                return
            
            # Show confirmation
            keyboard = [
                [
                    InlineKeyboardButton(
                        "✅ Yes, Delete",
                        callback_data=f"delete_strategy_confirm:{run_id}"
                    ),
                    InlineKeyboardButton(
                        "❌ Cancel",
                        callback_data=f"view_strategy_config:{run_id}"
                    )
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            message = (
                f"⚠️ <b>Delete Strategy?</b>\n\n"
                f"<b>Run ID:</b> {run_id_short}\n"
                f"<b>Status:</b> {status}\n"
                f"<b>Config:</b> {config_name}\n"
                f"<b>Account:</b> {account_name}\n\n"
                f"This will permanently delete this strategy run.\n\n"
                f"<b>This action cannot be undone!</b>\n\n"
                f"Are you sure you want to delete this strategy?"
            )
            
            await query.edit_message_text(
                message,
                parse_mode='HTML',
                reply_markup=reply_markup
            )
            
        except Exception as e:
            self.logger.error(f"Delete strategy callback error: {e}", exc_info=True)
            await query.edit_message_text(
                f"❌ Error: {str(e)}",
                parse_mode='HTML'
            )
    
    async def delete_strategy_confirm_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle strategy deletion confirmation."""
        query = update.callback_query
        await query.answer()
        
        user, _ = await self.require_auth(update, context)
        if not user:
            return
        
        callback_data = query.data
        if not callback_data.startswith("delete_strategy_confirm:"):
            await query.edit_message_text(
                "❌ Invalid selection.",
                parse_mode='HTML'
            )
            return
        
        run_id = callback_data.split(":", 1)[1]
        
        try:
            # Check permissions again
            is_admin = user.get("is_admin", False)
            if not is_admin:
                user_strategy = await self.database.fetch_one(
                    "SELECT user_id FROM strategy_runs WHERE id = :run_id",
                    {"run_id": run_id}
                )
                if not user_strategy or str(user_strategy["user_id"]) != str(user["id"]):
                    await query.edit_message_text(
                        "❌ You don't have permission to delete this strategy.",
                        parse_mode='HTML'
                    )
                    return
            
            # Get strategy details before deletion
            strategy_row = await self.database.fetch_one(
                """
                SELECT supervisor_program_name, status
                FROM strategy_runs
                WHERE id = :run_id
                """,
                {"run_id": run_id}
            )
            
            if not strategy_row:
                await query.edit_message_text(
                    "❌ Strategy not found",
                    parse_mode='HTML'
                )
                return
            
            strategy_dict = dict(strategy_row)
            supervisor_name = strategy_dict.get('supervisor_program_name')
            status = strategy_dict.get('status')
            
            # Ensure strategy is stopped and remove from supervisor
            if supervisor_name:
                try:
                    supervisor = self.process_manager._get_supervisor_client()
                    
                    # Stop the process if running
                    if status in ('running', 'starting', 'paused'):
                        try:
                            supervisor.supervisor.stopProcess(supervisor_name)
                            self.logger.info(f"Stopped supervisor process: {supervisor_name}")
                        except Exception as e:
                            self.logger.warning(f"Failed to stop supervisor process: {e}")
                    
                    # Remove process group from supervisor
                    try:
                        # First check if process group exists
                        try:
                            process_info = supervisor.supervisor.getProcessInfo(supervisor_name)
                            # Process exists, remove it
                            try:
                                supervisor.supervisor.removeProcessGroup(supervisor_name)
                                self.logger.info(f"Removed process group from supervisor: {supervisor_name}")
                            except xmlrpc.client.Fault as fault:
                                # If already removed or doesn't exist, that's OK
                                if 'NOT_RUNNING' in str(fault) or 'BAD_NAME' in str(fault):
                                    self.logger.info(f"Process group already removed or doesn't exist: {supervisor_name}")
                                else:
                                    raise
                        except xmlrpc.client.Fault as fault:
                            # Process doesn't exist, that's OK
                            if 'BAD_NAME' in str(fault) or 'NOT_RUNNING' in str(fault):
                                self.logger.info(f"Process group doesn't exist in supervisor: {supervisor_name}")
                            else:
                                raise
                    except Exception as e:
                        self.logger.warning(f"Failed to remove process group from supervisor: {e}")
                        
                except Exception as e:
                    self.logger.warning(f"Failed to interact with supervisor: {e}")
            
            # Delete config file if it exists
            import tempfile
            from pathlib import Path
            config_file = Path(tempfile.gettempdir()) / f"strategy_{run_id}.yml"
            if config_file.exists():
                try:
                    config_file.unlink()
                    self.logger.info(f"Deleted config file: {config_file}")
                except Exception as e:
                    self.logger.warning(f"Failed to delete config file: {e}")
            
            # Delete supervisor config file if it exists
            if supervisor_name:
                supervisor_config_file = Path("/etc/supervisor/conf.d") / f"{supervisor_name}.conf"
                if supervisor_config_file.exists():
                    try:
                        import subprocess
                        subprocess.run(["sudo", "rm", str(supervisor_config_file)], check=False)
                        self.logger.info(f"Deleted supervisor config: {supervisor_config_file}")
                        
                        # Reload supervisor config
                        try:
                            supervisor = self.process_manager._get_supervisor_client()
                            supervisor.supervisor.reloadConfig()
                            self.logger.info("Reloaded supervisor config")
                        except Exception as e:
                            self.logger.warning(f"Failed to reload supervisor config: {e}")
                    except Exception as e:
                        self.logger.warning(f"Failed to delete supervisor config: {e}")
            
            # Delete from database
            await self.database.execute(
                "DELETE FROM strategy_runs WHERE id = :run_id",
                {"run_id": run_id}
            )
            
            await self.audit_logger.log_action(
                str(user["id"]),
                "delete_strategy",
                {"run_id": run_id, "supervisor_name": supervisor_name}
            )
            
            await query.edit_message_text(
                f"✅ <b>Strategy Deleted</b>\n\n"
                f"Run ID: <code>{run_id[:8]}</code>\n\n"
                f"The strategy has been permanently deleted.",
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ Back to Strategies", callback_data="back_to_strategies_filters")]
                ])
            )
            
        except Exception as e:
            self.logger.error(f"Delete strategy confirm error: {e}", exc_info=True)
            await query.edit_message_text(
                f"❌ Failed to delete strategy: {str(e)}",
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
                    "Start a strategy with /run_strategy",
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
    
    async def _get_log_file_info(self, run_id: str, user: dict) -> tuple:
        """
        Helper method to get log file information for a strategy run.
        Returns (log_file_path, run_id_full, run_id_short, config_name, strategy_type_display) or None if not found.
        """
        is_admin = user.get("is_admin", False)
        if is_admin:
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
            return None
        
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
        
        return (log_file, run_id_full, run_id_short, config_name, strategy_type_display)
    
    async def view_logs_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle callback for viewing logs - shows choice menu."""
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
            # Get log file info
            log_info = await self._get_log_file_info(run_id, user)
            if not log_info:
                await query.edit_message_text(
                    "❌ Strategy not found or you don't have permission to view logs",
                    parse_mode='HTML'
                )
                return
            
            log_file, run_id_full, run_id_short, config_name, strategy_type_display = log_info
            
            # If log file not found, inform user
            if not log_file or not Path(log_file).exists():
                await query.edit_message_text(
                    f"📄 <b>Log File Not Found</b>\n\n"
                    f"Strategy: {strategy_type_display}\n"
                    f"Config: {config_name}\n"
                    f"Run ID: <code>{run_id_short}</code>\n\n"
                    f"⚠️ The log file may have been cleaned up or deleted.\n"
                    f"This is expected if logs were manually removed.",
                    parse_mode='HTML',
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("⬅️ Back", callback_data="back_to_logs_filters")]
                    ])
                )
                return
            
            # Show choice menu
            keyboard = [
                [InlineKeyboardButton("⚡ Quick View (Last 15)", callback_data=f"view_logs_quick:{run_id}")],
                [InlineKeyboardButton("📄 Full Log File", callback_data=f"view_logs_full:{run_id}")],
                [InlineKeyboardButton("⬅️ Back", callback_data="back_to_logs_filters")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                f"📄 <b>View Logs</b>\n\n"
                f"Strategy: {strategy_type_display}\n"
                f"Config: {config_name}\n"
                f"Run ID: <code>{run_id_short}</code>\n\n"
                f"Choose how you want to view the logs:",
                parse_mode='HTML',
                reply_markup=reply_markup
            )
                
        except Exception as e:
            self.logger.error(f"View logs error: {e}", exc_info=True)
            await query.edit_message_text(
                f"❌ Error getting logs: {str(e)}",
                parse_mode='HTML'
            )
    
    async def view_logs_quick_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle quick view callback - shows last 15 log lines formatted in HTML."""
        query = update.callback_query
        await query.answer()
        
        user, _ = await self.require_auth(update, context)
        if not user:
            return
        
        # Parse callback data: "view_logs_quick:{run_id}"
        callback_data = query.data
        if not callback_data.startswith("view_logs_quick:"):
            await query.edit_message_text(
                "❌ Invalid selection. Please use /logs again.",
                parse_mode='HTML'
            )
            return
        
        run_id = callback_data.split(":", 1)[1]
        
        try:
            # Get log file info
            log_info = await self._get_log_file_info(run_id, user)
            if not log_info:
                await query.edit_message_text(
                    "❌ Strategy not found or you don't have permission to view logs",
                    parse_mode='HTML'
                )
                return
            
            log_file, run_id_full, run_id_short, config_name, strategy_type_display = log_info
            
            # If log file not found, inform user
            if not log_file or not Path(log_file).exists():
                await query.edit_message_text(
                    f"📄 <b>Log File Not Found</b>\n\n"
                    f"Strategy: {strategy_type_display}\n"
                    f"Config: {config_name}\n"
                    f"Run ID: <code>{run_id_short}</code>\n\n"
                    f"⚠️ The log file may have been cleaned up or deleted.",
                    parse_mode='HTML',
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("⬅️ Back", callback_data="back_to_logs_filters")]
                    ])
                )
                return
            
            # Read last 15 lines from log file
            try:
                with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                    lines = f.readlines()
                    last_15_lines = lines[-15:] if len(lines) > 15 else lines
            except Exception as e:
                self.logger.error(f"Error reading log file: {e}")
                await query.edit_message_text(
                    f"❌ Error reading log file: {str(e)}",
                    parse_mode='HTML'
                )
                return
            
            # Clean and format log lines
            # Strip ANSI codes, timestamps, log levels, and module info - keep only core messages
            formatted_lines = []
            line_number = 1
            for line in last_15_lines:
                # Remove trailing newline
                line = line.rstrip('\n\r')
                # Clean the log line to extract only the core message
                cleaned_line = self._clean_log_line(line)
                # Skip empty lines
                if not cleaned_line:
                    continue
                # Escape HTML special characters
                cleaned_line = cleaned_line.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                # Add line number and separator for better readability
                formatted_line = f"{line_number}. {cleaned_line}"
                formatted_lines.append(formatted_line)
                line_number += 1
            
            # Join with double newline for better visual separation
            log_content = '\n\n'.join(formatted_lines)
            
            # Telegram message limit is 4096 characters
            # Reserve space for header and HTML tags
            max_content_length = 3500
            if len(log_content) > max_content_length:
                # Truncate if too long
                log_content = log_content[:max_content_length] + "\n... (truncated)"
            
            # Wrap entire content in preformatted code block for better display
            log_content = f"<pre><code>{log_content}</code></pre>"
            
            keyboard = [
                [InlineKeyboardButton("📄 View Full Log File", callback_data=f"view_logs_full:{run_id}")],
                [InlineKeyboardButton("⬅️ Back", callback_data="back_to_logs_filters")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                f"⚡ <b>Quick View - Last 15 Logs</b>\n\n"
                f"Strategy: {strategy_type_display}\n"
                f"Config: {config_name}\n"
                f"Run ID: <code>{run_id_short}</code>\n\n"
                f"{log_content}",
                parse_mode='HTML',
                reply_markup=reply_markup
            )
                
        except Exception as e:
            self.logger.error(f"View logs quick error: {e}", exc_info=True)
            await query.edit_message_text(
                f"❌ Error getting logs: {str(e)}",
                parse_mode='HTML'
            )
    
    async def view_logs_full_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle full logs callback - sends entire log file as document."""
        query = update.callback_query
        await query.answer()
        
        user, _ = await self.require_auth(update, context)
        if not user:
            return
        
        # Parse callback data: "view_logs_full:{run_id}"
        callback_data = query.data
        if not callback_data.startswith("view_logs_full:"):
            await query.edit_message_text(
                "❌ Invalid selection. Please use /logs again.",
                parse_mode='HTML'
            )
            return
        
        run_id = callback_data.split(":", 1)[1]
        
        try:
            # Get log file info
            log_info = await self._get_log_file_info(run_id, user)
            if not log_info:
                await query.edit_message_text(
                    "❌ Strategy not found or you don't have permission to view logs",
                    parse_mode='HTML'
                )
                return
            
            log_file, run_id_full, run_id_short, config_name, strategy_type_display = log_info
            
            # If log file not found, inform user
            if not log_file or not Path(log_file).exists():
                await query.edit_message_text(
                    f"📄 <b>Log File Not Found</b>\n\n"
                    f"Strategy: {strategy_type_display}\n"
                    f"Config: {config_name}\n"
                    f"Run ID: <code>{run_id_short}</code>\n\n"
                    f"⚠️ The log file may have been cleaned up or deleted.\n"
                    f"This is expected if logs were manually removed.",
                    parse_mode='HTML',
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("⬅️ Back", callback_data="back_to_logs_filters")]
                    ])
                )
                return
            
            # Show loading message
            await query.edit_message_text(
                f"⏳ <b>Loading log file...</b>\n\n"
                f"Strategy: {strategy_type_display}\n"
                f"Config: {config_name}\n"
                f"Run ID: <code>{run_id_short}</code>",
                parse_mode='HTML'
            )
            
            # Send log file as document
            with open(log_file, 'rb') as f:
                await query.message.reply_document(
                    document=f,
                    filename=f"strategy_{run_id_short}_{strategy_type_display.lower().replace(' ', '_')}.log",
                    parse_mode='HTML'
                )
            
            # Update the message to show success
            keyboard = [
                [InlineKeyboardButton("⚡ Quick View (Last 15)", callback_data=f"view_logs_quick:{run_id}")],
                [InlineKeyboardButton("⬅️ Back", callback_data="back_to_logs_filters")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                f"✅ <b>Log file sent!</b>\n\n"
                f"Strategy: {strategy_type_display}\n"
                f"Config: {config_name}\n"
                f"Run ID: <code>{run_id_short}</code>\n\n"
                f"📄 Check the document below.",
                parse_mode='HTML',
                reply_markup=reply_markup
            )
                
        except Exception as e:
            self.logger.error(f"View logs full error: {e}", exc_info=True)
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
    
    async def back_to_account_selection_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle back button from config selection - return to account selection."""
        query = update.callback_query
        await query.answer()
        
        user, _ = await self.require_auth(update, context)
        if not user:
            return
        
        # Reset wizard to step 1 (account selection)
        wizard = context.user_data.get('wizard', {})
        wizard['step'] = 1
        # Clear account_id but keep config_id if it was pre-selected (from run_from_list)
        if 'config_id' in wizard.get('data', {}):
            # Keep config_id but clear account_id
            wizard['data'] = {'config_id': wizard['data'].get('config_id'), 'config_name': wizard['data'].get('config_name')}
        else:
            wizard['data'] = {}
        context.user_data['wizard'] = wizard
        
        # Get user's accounts
        accounts_query = """
            SELECT a.id, a.account_name
            FROM accounts a
            WHERE a.user_id = :user_id AND a.is_active = TRUE
            ORDER BY a.account_name
        """
        accounts = await self.database.fetch_all(accounts_query, {"user_id": user["id"]})
        
        if not accounts:
            await query.edit_message_text(
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
        
        # Check if config was pre-selected (from run_from_list)
        config_name = wizard.get('data', {}).get('config_name')
        if config_name:
            await query.edit_message_text(
                f"🚀 <b>Run Strategy</b>\n\n"
                f"Config: <b>{config_name}</b>\n\n"
                "Step 1/2: Select account:",
                parse_mode='HTML',
                reply_markup=reply_markup
            )
        else:
            await query.edit_message_text(
                "🚀 <b>Start Strategy</b>\n\n"
                "Step 1/2: Select account:",
                parse_mode='HTML',
                reply_markup=reply_markup
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
        
        # Check if config_id is already pre-selected (from run_from_list)
        pre_selected_config_id = wizard['data'].get('config_id')
        if pre_selected_config_id:
            # Config already selected, run directly
            await query.edit_message_text(
                f"⏳ <b>Starting strategy...</b>\n\n"
                f"Account: {account_name}\n"
                f"Config: {wizard['data'].get('config_name', 'N/A')}\n"
                f"{'🔓 Admin mode: Running on VPS IP' if user.get('is_admin') else '🔒 Proxy required'}",
                parse_mode='HTML'
            )
            
            # Get config data and run strategy
            config_row = await self.database.fetch_one(
                "SELECT config_data, config_name, strategy_type FROM strategy_configs WHERE id = :id",
                {"id": pre_selected_config_id}
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
            
            # Parse config data
            import json
            if isinstance(config_data_raw, str):
                try:
                    config_data = json.loads(config_data_raw)
                except json.JSONDecodeError as e:
                    self.logger.error(f"Failed to parse config_data JSON: {e}")
                    await query.edit_message_text(
                        f"❌ Invalid config data format: Failed to parse JSON",
                        parse_mode='HTML'
                    )
                    return
            elif isinstance(config_data_raw, dict):
                config_data = config_data_raw
            else:
                await query.edit_message_text(
                    f"❌ Invalid config data format: Expected dict or JSON string",
                    parse_mode='HTML'
                )
                return
            
            # Ensure config_data has the correct structure
            if isinstance(config_data, dict):
                if 'strategy' not in config_data or 'config' not in config_data:
                    config_data = {
                        "strategy": strategy_type,
                        "created_at": datetime.now().isoformat(),
                        "version": "1.0",
                        "config": config_data
                    }
            
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
            
            try:
                # Spawn strategy
                result = await self.process_manager.spawn_strategy(
                    user_id=user["id"],
                    account_id=account_id,
                    account_name=account_name,
                    config_id=pre_selected_config_id,
                    config_data=config_data,
                    is_admin=user.get("is_admin", False)
                )
                
                await self.audit_logger.log_strategy_start(
                    str(user["id"]),
                    result['run_id'],
                    account_id,
                    pre_selected_config_id,
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
                error_msg = str(e).replace('<', '&lt;').replace('>', '&gt;')
                await query.edit_message_text(
                    f"❌ Failed to start strategy: {error_msg}",
                    parse_mode='HTML'
                )
            return
        
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
        
        # Create config selection keyboard with improved UI
        keyboard = []
        message_parts = []
        
        if user_configs:
            # User configs section with distinct styling
            message_parts.append("━━━━━━━━━━━━━━━━━━━━")
            message_parts.append("💼 <b>Your Configs</b>")
            message_parts.append("━━━━━━━━━━━━━━━━━━━━")
            
            for cfg in user_configs:
                cfg_dict = dict(cfg) if not isinstance(cfg, dict) else cfg
                config_name = cfg_dict['config_name']
                strategy_type = cfg_dict['strategy_type']
                
                # Format strategy type for display
                strategy_display = {
                    'funding_arbitrage': 'Funding Arb',
                    'grid': 'Grid'
                }.get(strategy_type, strategy_type.title())
                
                # Use distinct emoji and color for user configs
                button_text = f"💼 {config_name} ({strategy_display})"
                keyboard.append([InlineKeyboardButton(
                    button_text,
                    callback_data=f"run_config:{cfg_dict['id']}"
                )])
        
        if templates:
            # Templates section with distinct styling
            if user_configs:
                message_parts.append("")  # Add blank line separator
            message_parts.append("━━━━━━━━━━━━━━━━━━━━")
            message_parts.append("⭐ <b>Templates</b>")
            message_parts.append("━━━━━━━━━━━━━━━━━━━━\n")
            
            for tpl in templates:
                tpl_dict = dict(tpl) if not isinstance(tpl, dict) else tpl
                config_name = tpl_dict['config_name']
                strategy_type = tpl_dict['strategy_type']
                
                # Format strategy type for display
                strategy_display = {
                    'funding_arbitrage': 'Funding Arb',
                    'grid': 'Grid'
                }.get(strategy_type, strategy_type.title())
                
                # Use distinct emoji for templates
                button_text = f"⭐ {config_name} (template)"
                keyboard.append([InlineKeyboardButton(
                    button_text,
                    callback_data=f"run_config:{tpl_dict['id']}"
                )])
        
        reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
        
        # Build message with sections
        message = f"✅ Account selected: <b>{account_name}</b>\n\n"
        message += "Step 2/2: Select configuration:\n\n"
        if message_parts:
            message += "\n".join(message_parts)
        
        # Add back button to return to account selection
        if keyboard:
            keyboard.append([InlineKeyboardButton("⬅️ Back to Account Selection", callback_data="back_to_account_selection")])
            reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            message,
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
                "❌ Error: Account not selected. Please start over with /run_strategy",
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
        application.add_handler(CommandHandler("run_strategy", self.run_strategy_command))
        application.add_handler(CommandHandler("list_strategies", self.list_strategies_command))
        application.add_handler(CommandHandler("stop_strategy", self.stop_strategy_command))
        application.add_handler(CommandHandler("resume_strategy", self.resume_strategy_command))
        application.add_handler(CommandHandler("logs", self.logs_command))
        application.add_handler(CommandHandler("limits", self.limits_command))
        
        # Callbacks
        application.add_handler(CallbackQueryHandler(
            self.back_to_account_selection_callback,
            pattern="^back_to_account_selection$"
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
            self.view_logs_callback,
            pattern="^view_logs:"
        ))
        application.add_handler(CallbackQueryHandler(
            self.view_logs_quick_callback,
            pattern="^view_logs_quick:"
        ))
        application.add_handler(CallbackQueryHandler(
            self.view_logs_full_callback,
            pattern="^view_logs_full:"
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
        # Direct pause/resume from detail view
        application.add_handler(CallbackQueryHandler(
            self.pause_strategy_direct_callback,
            pattern="^pause_strategy_direct:"
        ))
        application.add_handler(CallbackQueryHandler(
            self.resume_strategy_direct_callback,
            pattern="^resume_strategy_direct:"
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
        application.add_handler(CallbackQueryHandler(
            self.view_strategy_config_callback,
            pattern="^view_strategy_config:"
        ))
        application.add_handler(CallbackQueryHandler(
            self.edit_strategy_config_callback,
            pattern="^edit_strategy_config:"
        ))
        application.add_handler(CallbackQueryHandler(
            self.delete_strategy_callback,
            pattern="^delete_strategy:"
        ))
        application.add_handler(CallbackQueryHandler(
            self.delete_strategy_confirm_callback,
            pattern="^delete_strategy_confirm:"
        ))

