"""
Configuration management handlers for Telegram bot
"""

import json
import yaml
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler

from telegram_bot_service.handlers.base import BaseHandler


class ConfigHandler(BaseHandler):
    """Handler for configuration management commands, callbacks, and wizards"""
    
    async def list_configs_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /list_configs or /my_configs command."""
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
        """Handle /create_config or /new_config command."""
        user, _ = await self.require_auth(update, context)
        if not user:
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
    
    def register_handlers(self, application):
        """Register config management command and callback handlers"""
        # Commands
        application.add_handler(CommandHandler("list_configs", self.list_configs_command))
        application.add_handler(CommandHandler("my_configs", self.list_configs_command))
        application.add_handler(CommandHandler("create_config", self.create_config_command))
        application.add_handler(CommandHandler("new_config", self.create_config_command))
        
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

