"""
Configuration management handlers for Telegram bot
"""

import json
import yaml
from decimal import Decimal
from datetime import datetime
from typing import Dict, Any, Optional
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
            
            await query.edit_message_text(
                f"📝 <b>JSON/YAML Config Input: {strategy_type.replace('_', ' ').title()}</b>\n\n"
                f"Default configuration:\n"
                f"<code>{config_yaml[:800]}{'...' if len(config_yaml) > 800 else ''}</code>\n\n"
                "Send your config as JSON or YAML to edit, or send 'use_default' to use the default config above.\n"
                "Send 'cancel' to cancel.",
                parse_mode='HTML'
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
    
    # Helper methods for wizard
    
    async def _prompt_next_parameter(self, update, context, wizard, schema, params, max_oi_param):
        """Prompt for the next parameter in the wizard."""
        data = wizard['data']
        current_index = data['current_param_index']
        config = data['config']
        
        if current_index >= len(params):
            # Check if we need max_oi_usd
            mandatory_exchange = config.get('mandatory_exchange')
            
            if max_oi_param and mandatory_exchange:
                wizard['step'] = 2
                data['current_param_index'] = -1
                await self._prompt_parameter(update, context, wizard, max_oi_param, schema, params, max_oi_param)
            else:
                await self._finalize_config(update, context, wizard, None)
            return
        
        param = params[current_index]
        await self._prompt_parameter(update, context, wizard, param, schema, params, max_oi_param)
    
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
            keyboard.append([InlineKeyboardButton("✅ Done", callback_data=f"wizard_multi_done:{param.key}")])
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
    
    async def _prompt_parameter(self, update, context, wizard, param, schema, params, max_oi_param):
        """Prompt user for a parameter value."""
        await self._prompt_parameter_message(update.message, context, wizard, param, schema, params, max_oi_param)
    
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
                    
                    keyboard.append([InlineKeyboardButton("✅ Done", callback_data=f"wizard_multi_done:{param_key}")])
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
    
    def register_handlers(self, application):
        """Register config management command and callback handlers"""
        # Commands
        application.add_handler(CommandHandler("list_configs", self.list_configs_command))
        application.add_handler(CommandHandler("create_config", self.create_config_command))
        
        # Callbacks
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
            self.handle_wizard_param_callback,
            pattern="^wizard_param:|^wizard_cancel"
        ))
        application.add_handler(CallbackQueryHandler(
            self.handle_wizard_multi_callback,
            pattern="^wizard_multi:|^wizard_multi_done:"
        ))

