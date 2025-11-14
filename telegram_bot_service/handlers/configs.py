"""
Configuration management handlers for Telegram bot
"""

import json
import yaml
from decimal import Decimal
from datetime import datetime
from typing import Dict, Any, Optional, List
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler

from telegram_bot_service.handlers.base import BaseHandler
from strategies.base_schema import ParameterType
from strategies.implementations.funding_arbitrage.config_builder.schema import get_funding_arb_schema
from strategies.implementations.grid.config_builder.schema import get_grid_schema


class ConfigHandler(BaseHandler):
    """Handler for configuration management commands, callbacks, and wizards"""
    
    async def list_configs_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /list_configs command."""
        user, _ = await self.require_auth(update, context)
        if not user:
            return
        
        try:
            message, reply_markup = await self._build_config_list(str(user["id"]))
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

    async def _build_config_list(self, user_id: str):
        """Build config list message and keyboard."""
        # Get user configs
        query = """
            SELECT id, config_name, strategy_type, is_active, created_at
            FROM strategy_configs
            WHERE user_id = :user_id AND is_template = FALSE
            ORDER BY created_at DESC
        """
        user_configs = await self.database.fetch_all(query, {"user_id": user_id})
        
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
                # Convert Row to dict for safe access
                cfg_dict = dict(cfg) if not isinstance(cfg, dict) else cfg
                status = "🟢" if cfg_dict["is_active"] else "⚫"
                config_id = str(cfg_dict["id"])
                config_name = cfg_dict["config_name"]
                message += f"{status} <b>{config_name}</b> ({cfg_dict['strategy_type']})\n"
                
                # Add run, edit, and delete buttons for each config
                keyboard.append([
                    InlineKeyboardButton(
                        f"🚀 Run",
                        callback_data=f"run_from_list:{config_id}"
                    ),
                    InlineKeyboardButton(
                        f"✏️ Edit",
                        callback_data=f"edit_config_btn:{config_id}"
                    ),
                    InlineKeyboardButton(
                        f"🗑️ Delete",
                        callback_data=f"delete_config_btn:{config_id}"
                    )
                ])
            message += "\n"
        else:
            message += "No configs yet. Create one with /create_config\n\n"
        
        if templates:
            message += "<b>Public Templates:</b>\n"
            for tpl in templates[:10]:  # Limit to 10 templates
                # Convert Row to dict for safe access
                tpl_dict = dict(tpl) if not isinstance(tpl, dict) else tpl
                config_id = str(tpl_dict['id'])
                config_name = tpl_dict['config_name']
                message += f"⭐ {config_name}\n"
                # message += f"⭐ {config_name} ({tpl_dict['strategy_type']})\n"
                
                # Add copy button for each template
                keyboard.append([
                    InlineKeyboardButton(
                        f"📋 Copy {config_name}",
                        callback_data=f"copy_template_config:{config_id}"
                    )
                ])
            if len(templates) > 10:
                message += f"... and {len(templates) - 10} more\n"
        
        reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
        return message, reply_markup
    
    async def create_config_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /create_config command."""
        user, _ = await self.require_auth(update, context)
        if not user:
            return
        
        # Start config creation - first select strategy type
        keyboard = [
            [InlineKeyboardButton("📊 Funding Arbitrage", callback_data="config_strategy:funding_arbitrage")],
            [InlineKeyboardButton("📈 Grid", callback_data="config_strategy:grid")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "📋 <b>Create New Configuration</b>\n\n"
            "Choose strategy type:",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
    
    async def edit_config_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle edit config button click."""
        query = update.callback_query
        await query.answer()
        
        try:
            callback_data = query.data
            config_id = callback_data.split(":", 1)[1]
            
            telegram_user_id = query.from_user.id
            user = await self.auth.get_user_by_telegram_id(telegram_user_id)
            
            # Get config details
            config_row = await self.database.fetch_one(
                """
                SELECT config_name, strategy_type, config_data, is_active, is_template, user_id
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
            
            # Convert Row to dict for safe access
            config_dict = dict(config_row)
            
            config_name = config_dict['config_name']
            is_template = config_dict.get('is_template', False)
            config_user_id = config_dict.get('user_id')
            
            # Check if user is trying to edit a template
            if is_template:
                # Offer to create a copy instead
                keyboard = [
                    [
                        InlineKeyboardButton(
                            "✅ Yes, Create Copy",
                            callback_data=f"copy_template_config:{config_id}"
                        ),
                        InlineKeyboardButton(
                            "❌ Cancel",
                            callback_data="list_configs_back"
                        )
                    ]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await query.edit_message_text(
                    f"⚠️ <b>Templates Cannot Be Edited</b>\n\n"
                    f"Config <b>{config_name}</b> is a public template.\n\n"
                    f"To customize it, we'll create a copy for you that you can edit.\n\n"
                    f"Would you like to create a copy?",
                    parse_mode='HTML',
                    reply_markup=reply_markup
                )
                return
            
            # Check if user owns this config
            if config_user_id and str(config_user_id) != str(user["id"]):
                await query.edit_message_text(
                    "❌ You don't have permission to edit this config.",
                    parse_mode='HTML'
                )
                return
            
            # Start edit wizard (for now, just show current config and allow JSON edit)
            context.user_data['wizard'] = {
                'type': 'edit_config',
                'step': 1,
                'data': {
                    'config_id': config_id,
                    'config_name': config_name,
                    'strategy_type': config_dict['strategy_type']
                }
            }
            config_data = config_dict['config_data']
            if isinstance(config_data, str):
                try:
                    config_data = json.loads(config_data)
                except json.JSONDecodeError:
                    try:
                        config_data = yaml.safe_load(config_data)
                    except Exception:
                        pass
            config_data = config_data or {}
            structured_config = {
                "strategy": config_dict['strategy_type'],
                "config": config_data
            }
            config_yaml = yaml.dump(structured_config, default_flow_style=False, indent=2, sort_keys=False)
            
            # Telegram message limit is 4096 characters
            # Reserve space for header text and buttons (~500 chars)
            max_config_length = 3500
            
            # Build header message
            header_message = (
                f"✏️ <b>Edit Config: {config_name}</b>\n\n"
                f"Strategy Type: <b>{config_dict['strategy_type']}</b>\n\n"
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
                        [InlineKeyboardButton("⬅️ Back", callback_data="list_configs_back")]
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
                        [InlineKeyboardButton("⬅️ Back", callback_data="list_configs_back")]
                    ])
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
            
            # Convert Row to dict
            config_dict = dict(config_row)
            config_name = config_dict['config_name']
            
            # Check if config is in use (check for ALL strategies, not just running ones)
            all_strategies = await self.database.fetch_all(
                """
                SELECT id, status
                FROM strategy_runs
                WHERE config_id = :config_id
                ORDER BY started_at DESC
                LIMIT 10
                """,
                {"config_id": config_id}
            )
            
            if all_strategies:
                # Build message showing strategies using this config
                message = (
                    f"⚠️ <b>Cannot Delete Config</b>\n\n"
                    f"Config <b>{config_name}</b> is still being used by one or more strategies.\n\n"
                    f"You must delete all strategies using this config before you can delete it.\n\n"
                )
                
                # Count by status
                running_count = sum(1 for s in all_strategies if dict(s).get('status') in ('running', 'starting', 'paused'))
                stopped_count = len(all_strategies) - running_count
                
                message += f"<b>Strategies using this config:</b>\n"
                if running_count > 0:
                    message += f"• {running_count} running/active\n"
                if stopped_count > 0:
                    message += f"• {stopped_count} stopped/completed\n"
                
                # Count total if we got 10 (might be more)
                if len(all_strategies) >= 10:
                    total_count = await self.database.fetch_one(
                        """
                        SELECT COUNT(*) as count
                        FROM strategy_runs
                        WHERE config_id = :config_id
                        """,
                        {"config_id": config_id}
                    )
                    total = dict(total_count).get('count', len(all_strategies)) if total_count else len(all_strategies)
                    if total > 10:
                        message += f"• Total: {total} strategies\n"
                
                message += "\nUse /list_strategies to view and delete strategies."
                
                await query.edit_message_text(
                    message,
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
            
            # Convert Row to dict
            config_dict = dict(config_row)
            config_name = config_dict['config_name']
            
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
            
            # Add back button to return to config list
            keyboard = [
                [InlineKeyboardButton("⬅️ Back to Configs", callback_data="list_configs_back")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                f"✅ Config <b>{config_name}</b> deleted successfully.",
                parse_mode='HTML',
                reply_markup=reply_markup
            )
        except Exception as e:
            self.logger.error(f"Error in delete_config_confirm_callback: {e}", exc_info=True)
            
            # Check if this is a foreign key constraint violation
            error_str = str(e).lower()
            if "violates foreign key constraint" in error_str or "strategy_runs_config_id_fkey" in error_str:
                # Config is still referenced by strategy runs
                # Query for strategies using this config
                strategies = await self.database.fetch_all(
                    """
                    SELECT id, status, supervisor_program_name, started_at
                    FROM strategy_runs
                    WHERE config_id = :config_id
                    ORDER BY started_at DESC
                    LIMIT 10
                    """,
                    {"config_id": config_id}
                )
                
                # Build error message
                message = (
                    f"⚠️ <b>Cannot Delete Config</b>\n\n"
                    f"Config <b>{config_name}</b> is still being used by one or more strategies.\n\n"
                    f"You must delete all strategies using this config before you can delete it.\n\n"
                )
                
                if strategies:
                    message += f"<b>Strategies using this config ({len(strategies)}):</b>\n"
                    for strat in strategies:
                        strat_dict = dict(strat) if not isinstance(strat, dict) else strat
                        run_id_short = str(strat_dict['id'])[:8]
                        status = strat_dict.get('status', 'unknown')
                        message += f"• <code>{run_id_short}</code> ({status})\n"
                    
                    if len(strategies) >= 10:
                        # Count total strategies
                        total_count = await self.database.fetch_one(
                            """
                            SELECT COUNT(*) as count
                            FROM strategy_runs
                            WHERE config_id = :config_id
                            """,
                            {"config_id": config_id}
                        )
                        total = dict(total_count).get('count', len(strategies)) if total_count else len(strategies)
                        if total > 10:
                            message += f"\n... and {total - 10} more\n"
                
                message += "\nUse /list_strategies to view and delete strategies."
                
                # Add back button
                keyboard = [
                    [InlineKeyboardButton("⬅️ Back to Configs", callback_data="list_configs_back")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await query.edit_message_text(
                    message,
                    parse_mode='HTML',
                    reply_markup=reply_markup
                )
            else:
                # Other error - show generic message
                await query.edit_message_text(
                    f"❌ Failed to delete config: {str(e)}",
                    parse_mode='HTML'
                )
    
    async def delete_config_cancel_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle config deletion cancellation."""
        query = update.callback_query
        await query.answer("Deletion cancelled.")
        
        # Add back button to return to config list
        keyboard = [
            [InlineKeyboardButton("⬅️ Back to Configs", callback_data="list_configs_back")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "❌ Config deletion cancelled.",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
    
    async def config_strategy_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle strategy type selection - show creation method options."""
        query = update.callback_query
        await query.answer()
        
        callback_data = query.data
        strategy_type = callback_data.split(":", 1)[1]
        
        user, _ = await self.require_auth(update, context)
        if not user:
            return
        
        # Store strategy type and show creation method options
        schema_map = {
            'funding_arbitrage': get_funding_arb_schema(),
            'grid': get_grid_schema()
        }
        
        if strategy_type not in schema_map:
            await query.edit_message_text(
                f"❌ Unknown strategy type: {strategy_type}",
                parse_mode='HTML'
            )
            return
        
        schema = schema_map[strategy_type]
        
        keyboard = [
            [InlineKeyboardButton("🧙 Interactive Wizard", callback_data=f"config_method:wizard:{strategy_type}")],
            [InlineKeyboardButton("📝 JSON/YAML Input", callback_data=f"config_method:json:{strategy_type}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"📋 <b>{schema.display_name}</b>\n\n"
            f"{schema.description}\n\n"
            "Choose creation method:",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
    
    async def config_method_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle creation method selection (wizard or json)."""
        query = update.callback_query
        await query.answer()
        
        callback_data = query.data
        # Format: config_method:wizard:funding_arbitrage or config_method:json:funding_arbitrage
        parts = callback_data.split(":")
        method = parts[1]
        strategy_type = parts[2]
        
        user, _ = await self.require_auth(update, context)
        if not user:
            return
        
        if method == "json":
            # Load default config for the strategy type
            default_config = self._load_default_config(strategy_type)
            if default_config is None:
                await query.edit_message_text(
                    f"❌ Could not load default config for {strategy_type}",
                    parse_mode='HTML'
                )
                return
            
            # Start JSON/YAML input wizard with default config
            context.user_data['wizard'] = {
                'type': 'create_config_json',
                'step': 1,
                'data': {
                    'strategy_type': strategy_type,
                    'default_config': default_config
                }
            }
            
            # Format config as YAML for display
            config_yaml = yaml.dump(default_config, default_flow_style=False, indent=2)
            
            # Add back button
            keyboard = [
                [InlineKeyboardButton("⬅️ Back", callback_data=f"config_method_back:{strategy_type}")],
                [InlineKeyboardButton("❌ Cancel", callback_data="wizard_cancel")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                f"📝 <b>JSON/YAML Config Input: {strategy_type.replace('_', ' ').title()}</b>\n\n"
                f"Default configuration:\n"
                f"<code>{config_yaml[:800]}{'...' if len(config_yaml) > 800 else ''}</code>\n\n"
                "Send your config as JSON or YAML to edit, or send 'use_default' to use the default config above.\n"
                "Send 'cancel' to cancel.",
                parse_mode='HTML',
                reply_markup=reply_markup
            )
        else:
            # Start wizard for the selected strategy type
            await self._start_wizard_for_strategy(query, context, strategy_type)
    
    def _load_default_config(self, strategy_type: str) -> Optional[Dict]:
        """Load default config from file based on strategy type."""
        from pathlib import Path
        
        if strategy_type == "funding_arbitrage":
            config_file = Path("configs/config.yml")
        elif strategy_type == "grid":
            config_file = Path("configs/grid/grid_original.yml")
        else:
            return None
        
        if not config_file.exists():
            self.logger.warning(f"Default config file not found: {config_file}")
            return None
        
        try:
            with open(config_file, 'r') as f:
                config_dict = yaml.safe_load(f)
            return config_dict
        except Exception as e:
            self.logger.error(f"Error loading default config: {e}")
            return None
    
    async def _start_wizard_for_strategy(self, query, context: ContextTypes.DEFAULT_TYPE, strategy_type: str):
        """Start the interactive wizard for a strategy type."""
        schema_map = {
            'funding_arbitrage': get_funding_arb_schema(),
            'grid': get_grid_schema()
        }
        
        if strategy_type not in schema_map:
            await query.edit_message_text(
                f"❌ Unknown strategy type: {strategy_type}",
                parse_mode='HTML'
            )
            return
        
        schema = schema_map[strategy_type]
        
        # Initialize wizard state
        # Filter out max_oi_usd from initial params (handled separately)
        # Also filter out parameters that should be hardcoded
        hardcoded_params = {"max_positions", "max_new_positions_per_cycle", "check_interval_seconds", "dry_run"}
        base_params = [p for p in schema.parameters if p.key != "max_oi_usd" and p.key not in hardcoded_params]
        max_oi_param = next((p for p in schema.parameters if p.key == "max_oi_usd"), None)
        
        # Store param keys instead of full schema objects (can't pickle)
        param_keys = [p.key for p in base_params]
        max_oi_key = max_oi_param.key if max_oi_param else None
        
        context.user_data['wizard'] = {
            'type': f'create_config_{strategy_type}',
            'step': 0,  # 0 = config name, then params
            'data': {
                'strategy_type': strategy_type,
                'param_keys': param_keys,
                'max_oi_key': max_oi_key,
                'config': {},
                'current_param_index': 0
            }
        }
        
        # Start with config name
        await query.edit_message_text(
            f"📋 <b>Config Wizard: {schema.display_name}</b>\n\n"
            f"{schema.description}\n\n"
            "Step 1/1: Enter a name for this configuration:\n"
            "(e.g., 'My Funding Arb Config', 'Grid BTC Strategy')",
            parse_mode='HTML'
        )
    
    async def config_method_back_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle back button from JSON/YAML input - return to creation method selection."""
        query = update.callback_query
        await query.answer()
        
        callback_data = query.data
        strategy_type = callback_data.split(":", 1)[1]
        
        user, _ = await self.require_auth(update, context)
        if not user:
            return
        
        # Clear wizard state
        context.user_data.pop('wizard', None)
        
        # Show creation method selection again
        schema_map = {
            'funding_arbitrage': get_funding_arb_schema(),
            'grid': get_grid_schema()
        }
        
        if strategy_type not in schema_map:
            await query.edit_message_text(
                f"❌ Unknown strategy type: {strategy_type}",
                parse_mode='HTML'
            )
            return
        
        schema = schema_map[strategy_type]
        
        keyboard = [
            [InlineKeyboardButton("🧙 Interactive Wizard", callback_data=f"config_method:wizard:{strategy_type}")],
            [InlineKeyboardButton("📝 JSON/YAML Input", callback_data=f"config_method:json:{strategy_type}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"📋 <b>{schema.display_name}</b>\n\n"
            f"{schema.description}\n\n"
            "Choose creation method:",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
    
    async def list_configs_back_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle back button from config detail to list view."""
        query = update.callback_query
        await query.answer()
        
        user, _ = await self.require_auth(update, context)
        if not user:
            return
        
        context.user_data.pop('wizard', None)
        
        try:
            message, reply_markup = await self._build_config_list(str(user["id"]))
            await query.edit_message_text(
                message,
                parse_mode='HTML',
                reply_markup=reply_markup
            )
        except Exception as e:
            self.logger.error(f"Error returning to config list: {e}")
            await query.edit_message_text(
                f"❌ Failed to load configs: {str(e)}",
                parse_mode='HTML'
            )
    
    async def config_type_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle config type selection (legacy - kept for backward compatibility)."""
        # This is now handled by config_strategy_callback and config_method_callback
        # But we keep this for any existing callbacks
        await self.config_strategy_callback(update, context)
    
    def _get_schema(self, strategy_type: str):
        """Get schema for a strategy type."""
        schema_map = {
            'funding_arbitrage': get_funding_arb_schema(),
            'grid': get_grid_schema()
        }
        return schema_map.get(strategy_type)
    
    async def handle_create_config_wizard(self, update, context, wizard, text):
        """Handle create config wizard steps for funding_arbitrage and grid."""
        wizard_type = wizard['type']
        data = wizard['data']
        step = wizard['step']
        strategy_type = data['strategy_type']
        
        # Get schema
        schema = self._get_schema(strategy_type)
        if not schema:
            await update.message.reply_text(
                f"❌ Unknown strategy type: {strategy_type}",
                parse_mode='HTML'
            )
            context.user_data.pop('wizard', None)
            return
        
        # Reconstruct params from keys
        param_keys = data.get('param_keys', [])
        params = [p for p in schema.parameters if p.key in param_keys]
        max_oi_key = data.get('max_oi_key')
        max_oi_param = next((p for p in schema.parameters if p.key == max_oi_key), None) if max_oi_key else None
        
        # Handle cancellation
        if text.lower() in ['cancel', '/cancel']:
            context.user_data.pop('wizard', None)
            await update.message.reply_text(
                "❌ Config creation cancelled.",
                parse_mode='HTML'
            )
            return
        
        telegram_user_id = update.effective_user.id
        user = await self.auth.get_user_by_telegram_id(telegram_user_id)
        
        try:
            if step == 0:
                # Step 0: Get config name
                config_name = text.strip()
                if not config_name:
                    await update.message.reply_text(
                        "❌ Config name cannot be empty. Please enter a name or 'cancel' to cancel.",
                        parse_mode='HTML'
                    )
                    return
                
                # Check if name already exists for this user
                existing = await self.database.fetch_one(
                    """
                    SELECT id FROM strategy_configs
                    WHERE user_id = :user_id AND config_name = :config_name AND is_template = FALSE
                    """,
                    {"user_id": str(user["id"]), "config_name": config_name}
                )
                
                if existing:
                    await update.message.reply_text(
                        f"❌ A config named <b>{config_name}</b> already exists.\n"
                        "Please choose a different name or 'cancel' to cancel.",
                        parse_mode='HTML'
                    )
                    return
                
                data['config_name'] = config_name
                wizard['step'] = 1
                
                # Move to first parameter
                await self._prompt_next_parameter(update, context, wizard, schema, params, max_oi_param)
            
            elif step == 1:
                # Step 1+: Collecting parameters
                current_index = data['current_param_index']
                config = data['config']
                
                if current_index >= len(params):
                    # All base params collected, check if we need max_oi_usd
                    mandatory_exchange = config.get('mandatory_exchange')
                    
                    if max_oi_param and mandatory_exchange:
                        # Need to prompt for max_oi_usd
                        wizard['step'] = 2
                        data['current_param_index'] = -1  # Special flag for max_oi
                        await self._prompt_parameter(update, context, wizard, max_oi_param, schema, params, max_oi_param)
                    else:
                        # All done, finalize config
                        await self._finalize_config(update, context, wizard, user)
                else:
                    # Process current parameter
                    param = params[current_index]
                    value = await self._process_parameter_input(text, param, config, update)
                    
                    if value is None:
                        # Validation failed, error already sent
                        return
                    
                    # Special handling for certain parameters
                    if param.key == "mandatory_exchange":
                        if isinstance(value, str):
                            value_str = value.strip().lower()
                            config[param.key] = value_str if value_str and value_str != "none" else None
                        else:
                            config[param.key] = None
                    elif param.key == "min_profit_rate":
                        # Convert APY to per-interval rate (handled in _process_parameter_input)
                        config[param.key] = value
                    else:
                        config[param.key] = value
                    
                    # Move to next parameter
                    data['current_param_index'] = current_index + 1
                    await self._prompt_next_parameter(update, context, wizard, schema, params, max_oi_param)
            
            elif step == 2:
                # Step 2: Handling max_oi_usd parameter
                if max_oi_param:
                    config = data['config']
                    value = await self._process_parameter_input(text, max_oi_param, config, update)
                    if value is None:
                        return
                    config[max_oi_param.key] = value
                
                # Finalize config
                await self._finalize_config(update, context, wizard, user)
        
        except Exception as e:
            self.logger.error(f"Error in create config wizard: {e}", exc_info=True)
            await update.message.reply_text(
                f"❌ Error: {str(e)}\n\nPlease try again with /create_config",
                parse_mode='HTML'
            )
            context.user_data.pop('wizard', None)
    
    async def handle_create_config_json(self, update, context, wizard, text):
        """Handle JSON/YAML config input."""
        if text.lower() in ['cancel', '/cancel']:
            context.user_data.pop('wizard', None)
            await update.message.reply_text(
                "❌ Config creation cancelled.",
                parse_mode='HTML'
            )
            return
        
        telegram_user_id = update.effective_user.id
        user = await self.auth.get_user_by_telegram_id(telegram_user_id)
        
        data = wizard['data']
        strategy_type = data.get('strategy_type')
        default_config = data.get('default_config')
        
        try:
            # Check if user wants to use default
            if text.lower() == 'use_default':
                if not default_config:
                    await update.message.reply_text(
                        "❌ No default config available. Please provide your config.",
                        parse_mode='HTML'
                    )
                    return
                
                # Extract config data from default_config structure
                config_data = default_config.get('config', default_config)
                final_strategy_type = strategy_type or default_config.get('strategy')
                
                if not final_strategy_type:
                    await update.message.reply_text(
                        "❌ Could not determine strategy type from default config.",
                        parse_mode='HTML'
                    )
                    return
                
                # Validate config using schema
                schema_map = {
                    'funding_arbitrage': get_funding_arb_schema(),
                    'grid': get_grid_schema()
                }
                schema = schema_map[final_strategy_type]
                
                # Parse and validate
                parsed_config = schema.parse_config(config_data)
                is_valid, errors = schema.validate_config(parsed_config)
                
                if not is_valid:
                    error_msg = "\n".join(f"• {e}" for e in errors[:5])
                    if len(errors) > 5:
                        error_msg += f"\n... and {len(errors) - 5} more errors"
                    await update.message.reply_text(
                        f"❌ Default config validation failed:\n\n{error_msg}\n\n"
                        "Please send your own config or 'cancel' to cancel.",
                        parse_mode='HTML'
                    )
                    return
                
                # Ask for config name
                wizard['step'] = 2
                wizard['data'] = {
                    'strategy_type': final_strategy_type,
                    'config_data': parsed_config
                }
                await update.message.reply_text(
                    "✅ Using default configuration!\n\n"
                    "Enter a name for this configuration:\n"
                    "(e.g., 'My Funding Arb Config', 'Grid BTC Strategy')",
                    parse_mode='HTML'
                )
                return
            
            # User provided their own config - parse it
            # Try to parse as YAML first, then JSON
            try:
                config_dict = yaml.safe_load(text)
            except Exception as yaml_error:
                try:
                    config_dict = json.loads(text)
                except Exception as json_error:
                    await update.message.reply_text(
                        f"❌ Invalid format. Could not parse as YAML or JSON.\n\n"
                        f"YAML error: {str(yaml_error)}\n"
                        f"JSON error: {str(json_error)}\n\n"
                        "Please check your format and try again, or send 'cancel' to cancel.",
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
            
            # Extract strategy and config data
            extracted_strategy_type = config_dict.get('strategy')
            config_data = config_dict.get('config', config_dict)  # Allow both formats
            
            # Use strategy_type from wizard if not in config
            if not extracted_strategy_type:
                if strategy_type:
                    extracted_strategy_type = strategy_type
                else:
                    await update.message.reply_text(
                        "❌ Missing 'strategy' field. Please specify strategy type (funding_arbitrage or grid).",
                        parse_mode='HTML'
                    )
                    return
            
            if extracted_strategy_type not in ['funding_arbitrage', 'grid']:
                await update.message.reply_text(
                    f"❌ Invalid strategy type: {extracted_strategy_type}. Must be 'funding_arbitrage' or 'grid'.",
                    parse_mode='HTML'
                )
                return
            
            # Use strategy_type from wizard if provided, otherwise use from config
            final_strategy_type = strategy_type or extracted_strategy_type
            
            if not isinstance(config_data, dict):
                await update.message.reply_text(
                    "❌ Config data must be a dictionary/object.",
                    parse_mode='HTML'
                )
                return
            
            # Validate config using schema
            schema_map = {
                'funding_arbitrage': get_funding_arb_schema(),
                'grid': get_grid_schema()
            }
            schema = schema_map[final_strategy_type]
            
            # Parse and validate
            parsed_config = schema.parse_config(config_data)
            is_valid, errors = schema.validate_config(parsed_config)
            
            if not is_valid:
                error_msg = "\n".join(f"• {e}" for e in errors[:5])
                if len(errors) > 5:
                    error_msg += f"\n... and {len(errors) - 5} more errors"
                await update.message.reply_text(
                    f"❌ Config validation failed:\n\n{error_msg}\n\n"
                    "Please fix the errors and try again, or send 'cancel' to cancel.",
                    parse_mode='HTML'
                )
                return
            
            # Get config name (prompt if not provided)
            config_name = config_dict.get('config_name')
            if not config_name:
                # Ask for config name
                wizard['step'] = 2
                wizard['data'] = {
                    'strategy_type': final_strategy_type,
                    'config_data': parsed_config
                }
                await update.message.reply_text(
                    "✅ Config parsed successfully!\n\n"
                    "Enter a name for this configuration:\n"
                    "(e.g., 'My Funding Arb Config', 'Grid BTC Strategy')",
                    parse_mode='HTML'
                )
                return
            
            # Save config
            await self._save_config_to_db(user, config_name, final_strategy_type, parsed_config)
            
            context.user_data.pop('wizard', None)
            await update.message.reply_text(
                f"✅ Config <b>{config_name}</b> created successfully!",
                parse_mode='HTML'
            )
        
        except Exception as e:
            self.logger.error(f"Error creating config from JSON: {e}", exc_info=True)
            await update.message.reply_text(
                f"❌ Error creating config: {str(e)}\n\nPlease try again or send 'cancel' to cancel.",
                parse_mode='HTML'
            )
    
    async def handle_create_config_json_name(self, update, context, wizard, text):
        """Handle config name input for JSON config."""
        if text.lower() in ['cancel', '/cancel']:
            context.user_data.pop('wizard', None)
            await update.message.reply_text(
                "❌ Config creation cancelled.",
                parse_mode='HTML'
            )
            return
        
        telegram_user_id = update.effective_user.id
        user = await self.auth.get_user_by_telegram_id(telegram_user_id)
        
        config_name = text.strip()
        if not config_name:
            await update.message.reply_text(
                "❌ Config name cannot be empty. Please enter a name or 'cancel' to cancel.",
                parse_mode='HTML'
            )
            return
        
        data = wizard['data']
        strategy_type = data['strategy_type']
        config_data = data['config_data']
        
        # Check if name already exists
        existing = await self.database.fetch_one(
            """
            SELECT id FROM strategy_configs
            WHERE user_id = :user_id AND config_name = :config_name AND is_template = FALSE
            """,
            {"user_id": str(user["id"]), "config_name": config_name}
        )
        
        if existing:
            await update.message.reply_text(
                f"❌ A config named <b>{config_name}</b> already exists.\n"
                "Please choose a different name or 'cancel' to cancel.",
                parse_mode='HTML'
            )
            return
        
        # Save config
        await self._save_config_to_db(user, config_name, strategy_type, config_data)
        
        context.user_data.pop('wizard', None)
        await update.message.reply_text(
            f"✅ Config <b>{config_name}</b> created successfully!",
                parse_mode='HTML'
            )
    
    async def handle_edit_config_wizard(self, update, context, wizard, text):
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
            
            # Extract the inner config if user sent full structure with 'strategy' and 'config' keys
            # Database stores only the inner config dict, not the full structure
            # Validate nesting level - reject double-nested configs (config.config)
            if 'config' in config_dict:
                nested_config = config_dict['config']
                
                # Check for double-nesting: if nested config also has a 'config' key, reject it
                if isinstance(nested_config, dict) and 'config' in nested_config:
                    await update.message.reply_text(
                        "❌ Invalid config structure: double-nesting detected.\n\n"
                        "Found: config.config (not allowed)\n\n"
                        "Expected format:\n"
                        "```yaml\n"
                        "strategy: funding_arbitrage\n"
                        "config:\n"
                        "  scan_exchanges:\n"
                        "    - aster\n"
                        "  target_margin: 35.0\n"
                        "  ...\n"
                        "```\n\n"
                        "Or just the config dict without the outer structure.",
                        parse_mode='Markdown'
                    )
                    return
                
                # User sent full structure: {strategy: ..., config: {...}}
                # Extract just the config part
                config_dict = nested_config
            
            # Validate that the extracted config has required fields (not just empty or metadata)
            if not isinstance(config_dict, dict) or not config_dict:
                await update.message.reply_text(
                    "❌ Config is empty or invalid. Please provide a valid config dictionary.",
                    parse_mode='HTML'
                )
                return
            
            # Check if config has at least one strategy-specific field to ensure it's not just metadata
            strategy_specific_fields = ['scan_exchanges', 'target_margin', 'risk_strategy', 'min_profit_rate', 
                                       'exchanges', 'max_positions', 'mandatory_exchange']
            if not any(field in config_dict for field in strategy_specific_fields):
                await update.message.reply_text(
                    "❌ Config appears to be invalid. Missing required fields like 'scan_exchanges', 'target_margin', etc.\n\n"
                    "Please ensure your config contains the actual strategy parameters.",
                    parse_mode='HTML'
                )
                return
            
            # Check for invalid structure: has 'strategy' key but no 'config' key (and wasn't extracted above)
            if 'strategy' in config_dict and 'config' not in config_dict:
                # Check if it has actual config keys (might be valid if user sent just the config dict)
                if not any(field in config_dict for field in strategy_specific_fields):
                    await update.message.reply_text(
                        "❌ Config structure invalid. Expected format: {strategy: '...', config: {...}} or just the config dict.",
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
            
            # Regenerate config files for active strategies using this config
            # (includes running, starting, and paused - any status where the process exists)
            affected_strategies = await self._regenerate_configs_for_active_strategies(config_id)
            
            # If editing from a specific strategy view, also regenerate that strategy's config file
            # (only if it wasn't already regenerated above, i.e., if it's stopped/failed)
            run_id = data.get('run_id')
            if run_id:
                # Check if this strategy was already regenerated (i.e., it's active: running/starting/paused)
                already_regenerated = any(
                    str(strat.get('id')) == str(run_id) 
                    for strat in affected_strategies
                )
                
                if not already_regenerated:
                    # Strategy is stopped/failed (not active), regenerate its config file
                    # so it's ready when the strategy is restarted
                    await self._regenerate_strategy_config_file(run_id, config_id)
            
            # Get user's API key for hot-reload (if available)
            api_key = await self.auth.get_api_key_for_user(user) if user else None
            
            if api_key:
                self.logger.info(f"Attempting hot-reload for {len(affected_strategies)} active strategies")
            else:
                self.logger.info("No API key available for hot-reload (user may not be authenticated)")
            
            # Try to hot-reload configs for active strategies via control API
            reload_results = await self._hot_reload_running_strategies(affected_strategies, api_key=api_key)
            
            context.user_data.pop('wizard', None)
            
            # Build success message
            message = f"✅ Config <b>{data['config_name']}</b> updated successfully!"
            
            # Check if we came from a specific strategy view
            run_id = data.get('run_id')
            strategy_filter = data.get('strategy_filter', 'all')  # Get filter from wizard data
            
            # Build keyboard with back button
            keyboard = []
            if run_id:
                # Show specific strategy info
                run_id_short = str(run_id)[:8]
                run_id_str = str(run_id)
                if reload_results.get(run_id_str, False):
                    message += f"\n\n✅ Strategy <code>{run_id_short}</code> config reloaded instantly!"
                else:
                    # Check strategy status to determine message
                    strategy_row = await self.database.fetch_one(
                        "SELECT status FROM strategy_runs WHERE id = :run_id",
                        {"run_id": run_id}
                    )
                    status = dict(strategy_row).get('status') if strategy_row else None
                    if status in ('running', 'starting'):
                        message += f"\n\n⚠️ Strategy <code>{run_id_short}</code> config updated. Hot-reload unavailable - changes will apply on next cycle."
                    elif status == 'paused':
                        message += f"\n\nℹ️ Strategy <code>{run_id_short}</code> is paused. Changes will apply when resumed."
                    else:
                        message += f"\n\nℹ️ Strategy <code>{run_id_short}</code> config updated."
                # Add back button to strategy list with filter
                keyboard.append([
                    InlineKeyboardButton("⬅️ Back to Strategies", callback_data=f"filter_strategies:{strategy_filter}")
                ])
            elif affected_strategies:
                # Show all affected strategies
                reloaded_count = sum(1 for s in affected_strategies if reload_results.get(str(s['id']), False))
                message += f"\n\n🔄 Updated {len(affected_strategies)} active strateg{'y' if len(affected_strategies) == 1 else 'ies'}:"
                for strat in affected_strategies[:5]:  # Show max 5
                    run_id_short = str(strat['id'])[:8]
                    status = strat['status']
                    reloaded = reload_results.get(str(strat['id']), False)
                    icon = "✅" if reloaded else "🔄"
                    message += f"\n   {icon} {run_id_short} ({status})"
                if len(affected_strategies) > 5:
                    message += f"\n   ... and {len(affected_strategies) - 5} more"
                if reloaded_count > 0:
                    message += f"\n\n✅ {reloaded_count} strateg{'y' if reloaded_count == 1 else 'ies'} reloaded instantly."
                if reloaded_count < len(affected_strategies):
                    message += f"\n⚠️ {len(affected_strategies) - reloaded_count} strateg{'y' if len(affected_strategies) - reloaded_count == 1 else 'ies'} will apply changes on next cycle."
            
            reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
            
            await update.message.reply_text(
                message,
                parse_mode='HTML',
                reply_markup=reply_markup
            )
        except Exception as e:
            self.logger.error(f"Error updating config: {e}")
            await update.message.reply_text(
                f"❌ Failed to update config: {str(e)}",
                parse_mode='HTML'
            )
    
    async def copy_template_config_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle copy template config button click."""
        query = update.callback_query
        await query.answer()
        
        try:
            callback_data = query.data
            template_config_id = callback_data.split(":", 1)[1]
            
            telegram_user_id = query.from_user.id
            user = await self.auth.get_user_by_telegram_id(telegram_user_id)
            
            # Get template config
            template_row = await self.database.fetch_one(
                """
                SELECT config_name, strategy_type, config_data
                FROM strategy_configs
                WHERE id = :id AND is_template = TRUE
                """,
                {"id": template_config_id}
            )
            
            if not template_row:
                await query.edit_message_text(
                    "❌ Template not found.",
                    parse_mode='HTML'
                )
                return
            
            # Convert Row to dict
            template_dict = dict(template_row)
            
            template_name = template_dict['config_name']
            strategy_type = template_dict['strategy_type']
            config_data = template_dict['config_data']
            
            # Create copy name
            copy_name = f"{template_name} (Copy)"
            
            # Check if copy name already exists, add number if needed
            existing_count = await self.database.fetch_one(
                """
                SELECT COUNT(*) as count
                FROM strategy_configs
                WHERE user_id = :user_id AND config_name LIKE :pattern
                """,
                {"user_id": str(user["id"]), "pattern": f"{copy_name}%"}
            )
            
            if existing_count and existing_count['count'] > 0:
                copy_name = f"{template_name} (Copy {existing_count['count'] + 1})"
            
            # Create copy
            if isinstance(config_data, str):
                config_data_dict = json.loads(config_data)
            else:
                config_data_dict = config_data
            
            await self.database.execute(
                """
                INSERT INTO strategy_configs (
                    user_id, config_name, strategy_type, config_data,
                    is_template, is_active, created_at, updated_at
                )
                VALUES (
                    :user_id, :config_name, :strategy_type, CAST(:config_data AS jsonb),
                    FALSE, TRUE, NOW(), NOW()
                )
                """,
                {
                    "user_id": str(user["id"]),
                    "config_name": copy_name,
                    "strategy_type": strategy_type,
                    "config_data": json.dumps(config_data_dict)
                }
            )
            
            await self.audit_logger.log_action(
                str(user["id"]),
                "copy_template_config",
                {"template_id": template_config_id, "template_name": template_name, "copy_name": copy_name}
            )
            
            # Now start edit wizard for the new copy
            new_config_row = await self.database.fetch_one(
                """
                SELECT id FROM strategy_configs
                WHERE user_id = :user_id AND config_name = :config_name
                ORDER BY created_at DESC
                LIMIT 1
                """,
                {"user_id": str(user["id"]), "config_name": copy_name}
            )
            
            if new_config_row:
                # Show success and redirect to list configs
                await query.edit_message_text(
                    f"✅ Created copy: <b>{copy_name}</b>\n\n"
                    f"You can now edit it from /list_configs",
                    parse_mode='HTML'
                )
            else:
                await query.edit_message_text(
                    f"✅ Created copy: <b>{copy_name}</b>\n\n"
                    f"Use /list_configs to edit it.",
                    parse_mode='HTML'
                )
                
        except Exception as e:
            self.logger.error(f"Error copying template: {e}", exc_info=True)
            await query.edit_message_text(
                f"❌ Failed to copy template: {str(e)}",
                parse_mode='HTML'
            )
    
    async def _regenerate_configs_for_active_strategies(self, config_id: str) -> List[Dict[str, Any]]:
        """
        Regenerate config files for all active strategies using this config.
        
        Active strategies are those with status: 'running', 'starting', or 'paused'.
        These are strategies where the process exists and may be reading the config file.
        Stopped/failed strategies are excluded as they don't need immediate config updates.
        
        When a config is edited, all active strategies that use it need their temp
        config files updated so they pick up the changes.
        
        Args:
            config_id: The config UUID that was updated
            
        Returns:
            List of affected strategy runs (with id, supervisor_program_name, status)
        """
        # Find active strategies using this config
        # Active = running, starting, or paused (process exists and may read config)
        active_strategies = await self.database.fetch_all(
            """
            SELECT id, supervisor_program_name, status
            FROM strategy_runs
            WHERE config_id = :config_id
            AND status IN ('running', 'starting', 'paused')
            """,
            {"config_id": config_id}
        )
        
        self.logger.info(f"Found {len(active_strategies) if active_strategies else 0} active strategies using config_id: {config_id}")
        
        if not active_strategies:
            return []
        
        # Regenerate config file for each active strategy using the core function
        affected_strategies = []
        for strategy in active_strategies:
            # Convert Row to dict
            strategy_dict = dict(strategy) if not isinstance(strategy, dict) else strategy
            run_id = str(strategy_dict['id'])
            
            # Use the core regeneration function
            success = await self._regenerate_strategy_config_file(run_id, config_id)
            if success:
                affected_strategies.append(strategy_dict)
        
        return affected_strategies
    
    async def _regenerate_strategy_config_file(self, run_id: str, config_id: str) -> bool:
        """
        Regenerate the temp config file for a specific strategy from the database config.
        
        This is the core function for regenerating strategy config files. It fetches the config
        from the database and writes it to the temp file that the supervisor uses.
        
        Args:
            run_id: Strategy run UUID
            config_id: Config UUID
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Get config from database
            config_row = await self.database.fetch_one(
                """
                SELECT config_data, strategy_type
                FROM strategy_configs
                WHERE id = :config_id
                """,
                {"config_id": config_id}
            )
            
            if not config_row:
                self.logger.warning(f"Config not found for config_id: {config_id}")
                return False
            
            # Convert Row to dict
            config_dict_row = dict(config_row)
            config_data_raw = config_dict_row['config_data']
            strategy_type = config_dict_row['strategy_type']
            
            # Parse config data
            if isinstance(config_data_raw, str):
                config_dict = json.loads(config_data_raw)
            else:
                config_dict = config_data_raw
            
            # Write the config file
            return await self._write_strategy_config_file(run_id, config_dict, strategy_type)
            
        except Exception as e:
            self.logger.error(f"Failed to regenerate config file for strategy {run_id[:8]}: {e}", exc_info=True)
            return False
    
    async def _write_strategy_config_file(
        self, 
        run_id: str, 
        config_dict: Dict[str, Any], 
        strategy_type: str
    ) -> bool:
        """
        Write strategy config to temp file.
        
        This is the low-level function that actually writes the config file.
        It's separated to allow reuse when config data is already available.
        
        Args:
            run_id: Strategy run UUID
            config_dict: Parsed config dictionary
            strategy_type: Strategy type (e.g., 'funding_arbitrage')
            
        Returns:
            True if successful, False otherwise
        """
        try:
            import tempfile
            from pathlib import Path
            
            temp_dir = Path(tempfile.gettempdir())
            config_file = temp_dir / f"strategy_{run_id}.yml"
            
            # Build full config structure
            full_config = {
                "strategy": strategy_type,
                "created_at": datetime.now().isoformat(),
                "version": "1.0",
                "config": config_dict
            }
            
            # Register Decimal representer for YAML
            def decimal_representer(dumper, data):
                return dumper.represent_scalar('tag:yaml.org,2002:float', str(data))
            yaml.add_representer(Decimal, decimal_representer)
            
            # Write config file
            with open(config_file, 'w') as f:
                yaml.dump(
                    full_config,
                    f,
                    default_flow_style=False,
                    sort_keys=False,
                    allow_unicode=True,
                    indent=2
                )
            
            self.logger.info(f"Regenerated config file for strategy {run_id[:8]}: {config_file}")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to write config file for strategy {run_id[:8]}: {e}", exc_info=True)
            return False
    
    async def _hot_reload_running_strategies(
        self, 
        strategies: List[Dict[str, Any]], 
        api_key: Optional[str] = None
    ) -> Dict[str, bool]:
        """
        Attempt to hot-reload configs for running strategies via control API.
        
        This allows config changes to take effect immediately without restarting.
        Falls back gracefully if control API is not available.
        
        Args:
            strategies: List of strategy dicts with id and status
            api_key: Optional API key for authentication (if None, hot-reload is skipped)
        
        Returns:
            Dict mapping run_id to success status (True if reloaded, False otherwise)
        """
        reload_results = {}
        
        if not strategies:
            return reload_results
        
        # Skip hot-reload if API key is not provided
        if not api_key:
            self.logger.info("Skipping hot-reload: API key not provided (user may not be authenticated via /auth)")
            return reload_results
        
        from telegram_bot_service.utils.api_client import ControlAPIClient
        
        self.logger.info(f"Attempting hot-reload for {len(strategies)} strategies")
            
        # Try to reload config for each running strategy
        for strategy in strategies:
            run_id = str(strategy.get('id', ''))
            status = strategy.get('status', '')
            
            # Only reload if strategy is actually running
            if status not in ('running', 'starting'):
                self.logger.debug(f"Strategy {run_id[:8]} status is '{status}', skipping hot-reload (only 'running' or 'starting' can be reloaded)")
                reload_results[run_id] = False
                continue
            
            try:
                # Get control API port for this strategy
                strategy_row = await self.database.fetch_one(
                    """
                    SELECT control_api_port
                    FROM strategy_runs
                    WHERE id = :run_id
                    """,
                    {"run_id": run_id}
                )
                
                # Convert Row to dict for safe access
                strategy_dict = dict(strategy_row) if strategy_row else {}
                
                if not strategy_dict or not strategy_dict.get('control_api_port'):
                    self.logger.warning(f"Strategy {run_id[:8]} has no control_api_port in database, cannot hot-reload")
                    reload_results[run_id] = False
                    continue
                
                # Create client with strategy-specific port
                port = strategy_dict['control_api_port']
                strategy_url = f"http://127.0.0.1:{port}"
                strategy_client = ControlAPIClient(strategy_url, api_key)
                
                # Check if embedded server is actually running before attempting hot-reload
                self.logger.info(f"Checking if embedded control API is running for strategy {run_id[:8]} on port {port}")
                is_running = await strategy_client.health_check()
                
                if not is_running:
                    self.logger.warning(
                        f"⚠️  Embedded control API server not running for strategy {run_id[:8]} "
                        f"(port {port}). Hot-reload requires the embedded server to be running. "
                        f"This may happen if the embedded server failed to start or the strategy "
                        f"was started without CONTROL_API_ENABLED=true. "
                        f"Config changes will take effect after strategy restart."
                    )
                    reload_results[run_id] = False
                    continue
                
                # Attempt reload
                self.logger.info(f"Attempting hot-reload for strategy {run_id[:8]} via {strategy_url}")
                result = await strategy_client.reload_config()
                if result.get('success'):
                    self.logger.info(f"✅ Hot-reloaded config for strategy {run_id[:8]}")
                    reload_results[run_id] = True
                else:
                    self.logger.warning(f"⚠️ Hot-reload failed for strategy {run_id[:8]}: {result.get('error', 'Unknown error')}")
                    reload_results[run_id] = False
                    
            except Exception as e:
                # Log but don't fail - hot-reload is optional
                self.logger.warning(f"Could not hot-reload config for strategy {run_id[:8]}: {e}", exc_info=True)
                reload_results[run_id] = False
        
        return reload_results
    
    # Helper methods for wizard
    
    async def _prompt_next_parameter(self, update, context, wizard, schema, params, max_oi_param):
        """Prompt for the next parameter in the wizard."""
        data = wizard['data']
        current_index = data['current_param_index']
        config = data['config']
        message = None
        if getattr(update, "message", None):
            message = update.message
        elif getattr(update, "callback_query", None):
            message = update.callback_query.message
        
        if message is None:
            self.logger.error("Unable to determine message object for wizard prompt.")
            return
        
        if current_index >= len(params):
            # Check if we need max_oi_usd
            mandatory_exchange = config.get('mandatory_exchange')
            
            if max_oi_param and mandatory_exchange:
                wizard['step'] = 2
                data['current_param_index'] = -1
                await self._prompt_parameter(message, context, wizard, max_oi_param, schema, params, max_oi_param)
            else:
                await self._finalize_config(update, context, wizard, None)
            return
        
        param = params[current_index]
        await self._prompt_parameter(message, context, wizard, param, schema, params, max_oi_param)
    
    async def _prompt_parameter_message(self, message, context, wizard, param, schema, params, max_oi_param):
        """Prompt user for a parameter value using a Message object."""
        data = wizard['data']
        current_index = data['current_param_index']
        total_params = len(params) + (1 if max_oi_param and data['config'].get('mandatory_exchange') else 0)
        
        # Calculate step number (1 for name, then params)
        step_num = current_index + 2 if current_index >= 0 else total_params + 1
        
        prompt_text = f"<b>Step {step_num}/{total_params + 1}: {param.prompt}</b>\n\n"
        
        if param.help_text:
            prompt_text += f"ℹ️ {param.help_text}\n\n"
        
        if param.show_default_in_prompt and param.default is not None:
            default_str = self._format_value_display(param.default)
            prompt_text += f"Default: <code>{default_str}</code>\n\n"
        
        # Handle different parameter types
        if param.param_type == ParameterType.CHOICE:
            keyboard = []
            choices = param.choices or []
            for choice in choices:
                keyboard.append([InlineKeyboardButton(
                    choice.upper() if choice == param.default else choice,
                    callback_data=f"wizard_param:{param.key}:{choice}"
                )])
            keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="wizard_cancel")])
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await message.reply_text(
                prompt_text + "Select an option:",
                parse_mode='HTML',
                reply_markup=reply_markup
            )
        
        elif param.param_type == ParameterType.MULTI_CHOICE:
            # Use checklist (inline keyboard) for multi-choice
            keyboard = []
            choices = param.choices or []
            defaults = param.default if isinstance(param.default, list) else []
            
            # Initialize multi_selections in wizard state
            if 'multi_selections' not in data:
                data['multi_selections'] = {}
            data['multi_selections'][param.key] = defaults.copy()
            
            # Create buttons with checkmarks for selected items
            for choice in choices:
                is_selected = choice in defaults
                prefix = "✅ " if is_selected else "☐ "
                keyboard.append([InlineKeyboardButton(
                    f"{prefix}{choice.upper() if is_selected else choice}",
                    callback_data=f"wizard_multi:{param.key}:{choice}:toggle"
                )])
            
            # Add Done button
            keyboard.append([InlineKeyboardButton("👉 Done", callback_data=f"wizard_multi_done:{param.key}")])
            keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="wizard_cancel")])
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            selected_str = ", ".join(defaults) if defaults else "None"
            prompt_text += f"Selected: <code>{selected_str}</code>\n\n"
            prompt_text += "Tap exchanges to select/deselect, then tap 'Done':"
            
            await message.reply_text(
                prompt_text,
                parse_mode='HTML',
                reply_markup=reply_markup
            )
        
        elif param.param_type == ParameterType.BOOLEAN:
            keyboard = [
                [
                    InlineKeyboardButton("✅ Yes", callback_data=f"wizard_param:{param.key}:true"),
                    InlineKeyboardButton("❌ No", callback_data=f"wizard_param:{param.key}:false")
                ],
                [InlineKeyboardButton("❌ Cancel", callback_data="wizard_cancel")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await message.reply_text(
                prompt_text + "Select:",
                parse_mode='HTML',
                reply_markup=reply_markup
            )
        
        else:
            # Text, Integer, Decimal
            prompt_text += "Enter value:"
            if param.param_type == ParameterType.DECIMAL:
                prompt_text += "\n(Enter a number, e.g., 100.5)"
            elif param.param_type == ParameterType.INTEGER:
                prompt_text += "\n(Enter a whole number, e.g., 5)"
            
            await message.reply_text(
                prompt_text,
                parse_mode='HTML'
            )
    
    async def _prompt_parameter(self, message, context, wizard, param, schema, params, max_oi_param):
        """Prompt user for a parameter value."""
        await self._prompt_parameter_message(message, context, wizard, param, schema, params, max_oi_param)
    
    async def _process_parameter_input(self, text: str, param, config: Dict, update=None) -> Optional[Any]:
        """Process and validate parameter input."""
        # Handle special cases
        if param.param_type == ParameterType.MULTI_CHOICE:
            # Parse comma-separated values
            values = [v.strip() for v in text.split(',')]
            # Validate
            is_valid, error_msg = param.validate(values)
            if not is_valid:
                if update:
                    await update.message.reply_text(
                        f"❌ {error_msg}\n\nPlease try again or send 'cancel' to cancel.",
                        parse_mode='HTML'
                    )
                return None
            return param.parse_value(values)
        
        # Validate
        is_valid, error_msg = param.validate(text)
        if not is_valid:
            if update:
                await update.message.reply_text(
                    f"❌ {error_msg}\n\nPlease try again or send 'cancel' to cancel.",
                    parse_mode='HTML'
                )
            return None
        
        # Parse value
        parsed = param.parse_value(text)
        
        # Special handling for min_profit_rate (convert APY to per-interval)
        if param.key == "min_profit_rate" and isinstance(parsed, Decimal):
            from trading_config.config_builder import FUNDING_PAYMENTS_PER_YEAR
            apy = parsed
            per_interval = apy / FUNDING_PAYMENTS_PER_YEAR
            return per_interval
        
        return parsed
    
    def _format_value_display(self, value: Any) -> str:
        """Format a value for display."""
        if isinstance(value, list):
            return ", ".join(str(v) for v in value)
        elif isinstance(value, bool):
            return "Yes" if value else "No"
        elif isinstance(value, Decimal):
            return str(value)
        else:
            return str(value)
    
    async def _finalize_config(self, update, context, wizard, user):
        """Finalize and save the config."""
        if user is None:
            telegram_user_id = update.effective_user.id
            user = await self.auth.get_user_by_telegram_id(telegram_user_id)
        
        data = wizard['data']
        config_name = data['config_name']
        strategy_type = data['strategy_type']
        config = data['config']
        
        # Hardcode specific parameters for funding_arbitrage
        if strategy_type == "funding_arbitrage":
            config["max_positions"] = 1
            config["max_new_positions_per_cycle"] = 1
            config["check_interval_seconds"] = 60
            config["dry_run"] = False
        
        # Post-process config (similar to config_builder.py)
        scan_list = config.get("scan_exchanges") or []
        mandatory_exchange = config.get("mandatory_exchange")
        if mandatory_exchange:
            if mandatory_exchange not in scan_list:
                scan_list.append(mandatory_exchange)
            ordered_unique = list(dict.fromkeys(scan_list))
            config["scan_exchanges"] = [ex.lower() for ex in ordered_unique]
        elif scan_list:
            config["scan_exchanges"] = [ex.lower() for ex in scan_list]
        
        # Ensure max_oi_usd is set
        if "max_oi_usd" not in config:
            config["max_oi_usd"] = None
        
        # Save to database
        await self._save_config_to_db(user, config_name, strategy_type, config)
        
        context.user_data.pop('wizard', None)
        
        await update.message.reply_text(
            f"✅ Configuration <b>{config_name}</b> created successfully!\n\n"
            f"Strategy: <b>{strategy_type}</b>\n"
            f"You can now use this config when starting a strategy with /start_strategy",
            parse_mode='HTML'
        )
    
    async def _save_config_to_db(self, user, config_name: str, strategy_type: str, config_data: Dict):
        """Save config to database."""
        # Convert Decimal to float for JSON serialization
        def decimal_to_float(obj):
            if isinstance(obj, Decimal):
                return float(obj)
            elif isinstance(obj, dict):
                return {k: decimal_to_float(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [decimal_to_float(item) for item in obj]
            return obj
        
        config_json = json.dumps(decimal_to_float(config_data))
        
        await self.database.execute(
            """
            INSERT INTO strategy_configs (user_id, config_name, strategy_type, config_data)
            VALUES (:user_id, :config_name, :strategy_type, CAST(:config_data AS jsonb))
            """,
            {
                "user_id": str(user["id"]),
                "config_name": config_name,
                "strategy_type": strategy_type,
                "config_data": config_json
            }
        )
        
        await self.audit_logger.log_action(
            str(user["id"]),
            "create_config",
            {"config_name": config_name, "strategy_type": strategy_type}
        )
    
    async def handle_wizard_param_callback(self, update, context):
        """Handle parameter selection from inline keyboard."""
        query = update.callback_query
        await query.answer()
        
        callback_data = query.data
        if callback_data == "wizard_cancel":
            context.user_data.pop('wizard', None)
            await query.edit_message_text(
                "❌ Config creation cancelled.",
                parse_mode='HTML'
            )
            return
        
        # Parse callback: wizard_param:param_key:value
        parts = callback_data.split(":", 2)
        if len(parts) != 3:
            await query.answer("Invalid callback", show_alert=True)
            return
        
        param_key = parts[1]
        param_value = parts[2]
        
        wizard = context.user_data.get('wizard')
        if not wizard:
            await query.answer("Wizard session expired", show_alert=True)
            return
        
        wizard_type = wizard['type']
        if not wizard_type.startswith('create_config_') or wizard_type == 'create_config_json':
            await query.answer("Invalid wizard type", show_alert=True)
            return
        
        data = wizard['data']
        strategy_type = data['strategy_type']
        
        # Get schema
        schema = self._get_schema(strategy_type)
        if not schema:
            await query.answer("Invalid strategy type", show_alert=True)
            return
        
        # Reconstruct params
        param_keys = data.get('param_keys', [])
        params = [p for p in schema.parameters if p.key in param_keys]
        max_oi_key = data.get('max_oi_key')
        max_oi_param = next((p for p in schema.parameters if p.key == max_oi_key), None) if max_oi_key else None
        
        current_index = data['current_param_index']
        
        # Check if this is max_oi_param
        if current_index == -1 and max_oi_param and max_oi_param.key == param_key:
            param = max_oi_param
        elif current_index >= len(params):
            await query.answer("Invalid step", show_alert=True)
            return
        else:
            param = params[current_index]
            if param.key != param_key:
                await query.answer("Parameter mismatch", show_alert=True)
                return
        
        # Process the value
        config = data['config']
        value = param.parse_value(param_value)
        
        # Special handling
        if param.key == "mandatory_exchange":
            if isinstance(value, str):
                value_str = value.strip().lower()
                config[param.key] = value_str if value_str and value_str != "none" else None
            else:
                config[param.key] = None
        else:
            config[param.key] = value
        
        # Move to next parameter
        if current_index == -1:
            # Was max_oi_param, finalize
            await query.edit_message_text(
                f"✅ {param.prompt}: <b>{self._format_value_display(value)}</b>",
                parse_mode='HTML'
            )
            # Send finalization message
            await query.message.reply_text(
                "⏳ Finalizing configuration...",
                parse_mode='HTML'
            )
            await self._finalize_config(update, context, wizard, None)
        else:
            data['current_param_index'] = current_index + 1
            
            # Update message to show selection
            await query.edit_message_text(
                f"✅ {param.prompt}: <b>{self._format_value_display(value)}</b>",
                parse_mode='HTML'
            )
            
            # Prompt next parameter using message directly
            # We need to get the current param to prompt
            if current_index + 1 < len(params):
                next_param = params[current_index + 1]
                await self._prompt_parameter_message(query.message, context, wizard, next_param, schema, params, max_oi_param)
            else:
                # Check if we need max_oi
                config = data['config']
                mandatory_exchange = config.get('mandatory_exchange')
                if max_oi_param and mandatory_exchange:
                    wizard['step'] = 2
                    data['current_param_index'] = -1
                    await self._prompt_parameter_message(query.message, context, wizard, max_oi_param, schema, params, max_oi_param)
                else:
                    await self._finalize_config(update, context, wizard, None)
    
    async def handle_wizard_multi_callback(self, update, context):
        """Handle multi-choice checklist callbacks."""
        query = update.callback_query
        await query.answer()
        
        callback_data = query.data
        
        if callback_data.startswith("wizard_multi_done:"):
            # User clicked Done - process the selection
            param_key = callback_data.split(":", 1)[1]
            
            wizard = context.user_data.get('wizard')
            if not wizard:
                await query.answer("Wizard session expired", show_alert=True)
                return
            
            wizard_type = wizard['type']
            if not wizard_type.startswith('create_config_') or wizard_type == 'create_config_json':
                await query.answer("Invalid wizard type", show_alert=True)
                return
            
            data = wizard['data']
            strategy_type = data['strategy_type']
            
            # Get schema
            schema = self._get_schema(strategy_type)
            if not schema:
                await query.answer("Invalid strategy type", show_alert=True)
                return
            
            # Get current selections from wizard state
            multi_selections = data.get('multi_selections', {})
            selected_values = multi_selections.get(param_key, [])
            
            if not selected_values:
                await query.answer("Please select at least one option", show_alert=True)
                return
            
            # Get param
            param = next((p for p in schema.parameters if p.key == param_key), None)
            if not param:
                await query.answer("Parameter not found", show_alert=True)
                return
            
            # Validate
            is_valid, error_msg = param.validate(selected_values)
            if not is_valid:
                await query.answer(f"Invalid selection: {error_msg}", show_alert=True)
                return
            
            # Store in config
            config = data['config']
            config[param_key] = selected_values
            
            # Clear multi_selections for this param
            if 'multi_selections' in data:
                data['multi_selections'].pop(param_key, None)
            
            # Move to next parameter
            param_keys = data.get('param_keys', [])
            params = [p for p in schema.parameters if p.key in param_keys]
            current_index = data['current_param_index']
            data['current_param_index'] = current_index + 1
            
            max_oi_key = data.get('max_oi_key')
            max_oi_param = next((p for p in schema.parameters if p.key == max_oi_key), None) if max_oi_key else None
            
            # Update message
            selected_str = ", ".join(selected_values)
            await query.edit_message_text(
                f"✅ {param.prompt}: <b>{selected_str}</b>",
                parse_mode='HTML'
            )
            
            # Prompt next parameter
            await self._prompt_next_parameter(update, context, wizard, schema, params, max_oi_param)
        
        elif callback_data.startswith("wizard_multi:"):
            # User toggled an option
            parts = callback_data.split(":", 3)
            if len(parts) != 4:
                await query.answer("Invalid callback", show_alert=True)
                return
            
            param_key = parts[1]
            choice_value = parts[2]
            
            wizard = context.user_data.get('wizard')
            if not wizard:
                await query.answer("Wizard session expired", show_alert=True)
                return
            
            data = wizard['data']
            
            # Initialize multi_selections if needed
            if 'multi_selections' not in data:
                data['multi_selections'] = {}
            if param_key not in data['multi_selections']:
                # Initialize with defaults
                schema = self._get_schema(data['strategy_type'])
                if schema:
                    param = next((p for p in schema.parameters if p.key == param_key), None)
                    if param and isinstance(param.default, list):
                        data['multi_selections'][param_key] = param.default.copy()
                    else:
                        data['multi_selections'][param_key] = []
            
            # Toggle selection
            current_selections = data['multi_selections'][param_key]
            if choice_value in current_selections:
                current_selections.remove(choice_value)
            else:
                current_selections.append(choice_value)
            
            # Update the keyboard
            schema = self._get_schema(data['strategy_type'])
            if schema:
                param = next((p for p in schema.parameters if p.key == param_key), None)
                if param:
                    keyboard = []
                    choices = param.choices or []
                    selected_values = data['multi_selections'][param_key]
                    
                    for choice in choices:
                        is_selected = choice in selected_values
                        prefix = "✅ " if is_selected else "☐ "
                        keyboard.append([InlineKeyboardButton(
                            f"{prefix}{choice.upper() if is_selected else choice}",
                            callback_data=f"wizard_multi:{param_key}:{choice}:toggle"
                        )])
                    
                    keyboard.append([InlineKeyboardButton("👉 Done", callback_data=f"wizard_multi_done:{param_key}")])
                    keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="wizard_cancel")])
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    
                    # Calculate step number
                    param_keys = data.get('param_keys', [])
                    step_num = data['current_param_index'] + 2
                    total_params = len(param_keys) + (1 if data.get('max_oi_key') and data['config'].get('mandatory_exchange') else 0)
                    
                    selected_str = ", ".join(selected_values) if selected_values else "None"
                    prompt_text = f"<b>Step {step_num}/{total_params + 1}: {param.prompt}</b>\n\n"
                    if param.help_text:
                        prompt_text += f"ℹ️ {param.help_text}\n\n"
                    prompt_text += f"Selected: <code>{selected_str}</code>\n\n"
                    prompt_text += "Tap exchanges to select/deselect, then tap 'Done':"
                    
                    await query.edit_message_text(
                        prompt_text,
                        parse_mode='HTML',
                        reply_markup=reply_markup
            )
    
    async def run_from_list_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle run button click from config list - show account selection."""
        query = update.callback_query
        await query.answer()
        
        user, _ = await self.require_auth(update, context)
        if not user:
            return
        
        callback_data = query.data
        config_id = callback_data.split(":", 1)[1]
        
        # Check safety limits
        allowed, reason = await self.safety_manager.can_start_strategy(user["id"])
        if not allowed:
            await query.edit_message_text(
                f"❌ Cannot start strategy: {reason}",
                parse_mode='HTML'
            )
            return
        
        # Check resource availability
        resource_ok, resource_msg = await self.health_monitor.before_spawn_check()
        if not resource_ok:
            await query.edit_message_text(
                f"❌ Resource check failed: {resource_msg}",
                parse_mode='HTML'
            )
            return
        
        # Check user limit
        user_limit_ok, user_limit_msg = await self.health_monitor.check_user_running_count(user["id"])
        if not user_limit_ok:
            await query.edit_message_text(
                f"❌ {user_limit_msg}",
                parse_mode='HTML'
            )
            return
        
        # Get config name for display
        config_row = await self.database.fetch_one(
            "SELECT config_name FROM strategy_configs WHERE id = :id",
            {"id": config_id}
        )
        if not config_row:
            await query.edit_message_text(
                "❌ Config not found",
                parse_mode='HTML'
            )
            return
        
        config_name = config_row['config_name']
        
        # Start wizard with config_id pre-selected
        context.user_data['wizard'] = {
            'type': 'run_strategy',
            'step': 1,
            'data': {
                'config_id': config_id,
                'config_name': config_name
            }
        }
        
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
        
        await query.edit_message_text(
            f"🚀 <b>Run Strategy</b>\n\n"
            f"Config: <b>{config_name}</b>\n\n"
            "Step 1/2: Select account:",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
    
    def register_handlers(self, application):
        """Register config management command and callback handlers"""
        # Commands
        application.add_handler(CommandHandler("list_configs", self.list_configs_command))
        application.add_handler(CommandHandler("create_config", self.create_config_command))
        
        # Callbacks
        application.add_handler(CallbackQueryHandler(
            self.run_from_list_callback,
            pattern="^run_from_list:"
        ))
        application.add_handler(CallbackQueryHandler(
            self.copy_template_config_callback,
            pattern="^copy_template_config:"
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
        application.add_handler(CallbackQueryHandler(
            self.config_type_callback,
            pattern="^config_type:"
        ))
        application.add_handler(CallbackQueryHandler(
            self.config_strategy_callback,
            pattern="^config_strategy:"
        ))
        application.add_handler(CallbackQueryHandler(
            self.config_method_callback,
            pattern="^config_method:"
        ))
        application.add_handler(CallbackQueryHandler(
            self.config_method_back_callback,
            pattern="^config_method_back:"
        ))
        application.add_handler(CallbackQueryHandler(
            self.list_configs_back_callback,
            pattern="^list_configs_back$"
        ))
        application.add_handler(CallbackQueryHandler(
            self.handle_wizard_param_callback,
            pattern="^wizard_param:|^wizard_cancel"
        ))
        application.add_handler(CallbackQueryHandler(
            self.handle_wizard_multi_callback,
            pattern="^wizard_multi:|^wizard_multi_done:"
        ))

