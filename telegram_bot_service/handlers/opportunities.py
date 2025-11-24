"""
Opportunity handlers for Telegram bot - Display funding arbitrage opportunities
"""

from decimal import Decimal
from typing import List, Optional, Dict, Any
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler

from telegram_bot_service.handlers.base import BaseHandler
from funding_rate_service.core.opportunity_finder import OpportunityFinder
from funding_rate_service.core.fee_calculator import FundingArbFeeCalculator
from funding_rate_service.core.mappers import dex_mapper, symbol_mapper
from funding_rate_service.models.filters import OpportunityFilter
from funding_rate_service.models.opportunity import ArbitrageOpportunity

# Exchange emoji mapping (reuse from strategy notification service)
EXCHANGE_EMOJIS = {
    "lighter": "⚡",
    "aster": "✨",
    "backpack": "🎒",
    "paradex": "🎪",
    "grvt": "🔷",
    "edgex": "🔹",
}


class OpportunitiesHandler(BaseHandler):
    """Handler for opportunity-related commands"""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # Initialize OpportunityFinder (use self.database which is connected)
        self.fee_calculator = FundingArbFeeCalculator()
        self.opportunity_finder = OpportunityFinder(
            database=self.database,
            fee_calculator=self.fee_calculator,
            dex_mapper=dex_mapper,
            symbol_mapper=symbol_mapper
        )
    
    @staticmethod
    def _get_exchange_emoji(dex_name: str) -> str:
        """Get emoji for exchange name."""
        dex_lower = dex_name.lower()
        return EXCHANGE_EMOJIS.get(dex_lower, "")
    
    def _format_opportunity(self, opp: ArbitrageOpportunity, index: int) -> str:
        """Format a single opportunity for display."""
        long_emoji = self._get_exchange_emoji(opp.long_dex)
        short_emoji = self._get_exchange_emoji(opp.short_dex)
        
        long_display = f"{long_emoji} {opp.long_dex.upper()}" if long_emoji else opp.long_dex.upper()
        short_display = f"{short_emoji} {opp.short_dex.upper()}" if short_emoji else opp.short_dex.upper()
        
        lines = [
            f"<b><u>{index}. {opp.symbol}</u></b>",
            f"  Long: {long_display} | Short: {short_display}",
            ""
        ]
        
        # Funding rates
        long_rate_pct = opp.long_rate * Decimal("100")
        short_rate_pct = opp.short_rate * Decimal("100")
        divergence_pct = opp.divergence * Decimal("100")
        
        lines.append("<b>📊 Funding Rates:</b>")
        lines.append(f"  • Long: <code>{long_rate_pct:.4f}%</code> ({long_display})")
        lines.append(f"  • Short: <code>{short_rate_pct:.4f}%</code> ({short_display})")
        lines.append(f"  • Divergence: <code>{divergence_pct:.4f}%</code>")
        lines.append("")
        
        # Profitability
        net_profit_pct = opp.net_profit_percent * Decimal("100")
        
        # Calculate APY correctly: net_profit_percent is per 8-hour period (as decimal, e.g., 0.006108)
        # The annualized_apy from fee_calculator is already calculated as:
        # net_rate * payments_per_year * 100 = net_rate * 1095 * 100
        # So it's already in percentage form (e.g., 668.8116 means 668.8116%)
        if opp.annualized_apy:
            # annualized_apy is already a percentage, use it directly
            apy_pct = opp.annualized_apy
        else:
            # Fallback calculation if annualized_apy is not set
            # net_profit_percent is decimal (0.006108), convert to percentage then annualize
            apy_pct = opp.net_profit_percent * Decimal("1095") * Decimal("100")
        
        lines.append("<b>💰 Profitability:</b>")
        lines.append(f"  • Net Profit: <code>{net_profit_pct:.4f}%</code>")
        if apy_pct:
            lines.append(f"  • APY: <code>{apy_pct:.2f}%</code>")
        lines.append("")
        
        # Volume
        lines.append("<b>📈 Volume (24h):</b>")
        if opp.long_dex_volume_24h:
            vol_long = self._format_currency(opp.long_dex_volume_24h)
            lines.append(f"  • {long_display}: <code>{vol_long}</code>")
        else:
            lines.append(f"  • {long_display}: <code>N/A</code>")
        
        if opp.short_dex_volume_24h:
            vol_short = self._format_currency(opp.short_dex_volume_24h)
            lines.append(f"  • {short_display}: <code>{vol_short}</code>")
        else:
            lines.append(f"  • {short_display}: <code>N/A</code>")
        lines.append("")
        
        # Open Interest
        lines.append("<b>💎 Open Interest:</b>")
        if opp.long_dex_oi_usd:
            oi_long = self._format_currency(opp.long_dex_oi_usd)
            lines.append(f"  • {long_display}: <code>{oi_long}</code>")
        else:
            lines.append(f"  • {long_display}: <code>N/A</code>")
        
        if opp.short_dex_oi_usd:
            oi_short = self._format_currency(opp.short_dex_oi_usd)
            lines.append(f"  • {short_display}: <code>{oi_short}</code>")
        else:
            lines.append(f"  • {short_display}: <code>N/A</code>")
        
        return "\n".join(lines)
    
    @staticmethod
    def _format_currency(value: Decimal) -> str:
        """Format currency value with appropriate units."""
        if value >= Decimal("1000000"):
            return f"${float(value) / 1000000:.2f}M"
        elif value >= Decimal("1000"):
            return f"${float(value) / 1000:.2f}K"
        else:
            return f"${float(value):.2f}"
    
    def _format_opportunities_list(
        self, 
        opportunities: List[ArbitrageOpportunity], 
        header: str,
        max_opportunities: int = 3
    ) -> str:
        """Format list of opportunities with header."""
        lines = [header, ""]
        
        if not opportunities:
            lines.append("No opportunities found.")
            return "\n".join(lines)
        
        # Limit to max_opportunities
        display_opps = opportunities[:max_opportunities]
        
        for idx, opp in enumerate(display_opps, 1):
            opp_text = self._format_opportunity(opp, idx)
            lines.append(opp_text)
            if idx < len(display_opps):
                lines.append("")  # Separator between opportunities
        
        return "\n".join(lines)

    async def _get_active_configs(self, user_id: str) -> List[Dict[str, Any]]:
        """Get active funding arbitrage configs for a user."""
        query = """
            SELECT sc.id, sc.config_name
            FROM strategy_runs sr
            JOIN strategy_configs sc ON sr.config_id = sc.id
            WHERE sr.user_id = :user_id
              AND sr.status IN ('running', 'starting', 'paused')
              AND sc.strategy_type = 'funding_arbitrage'
            GROUP BY sc.id, sc.config_name
            ORDER BY sc.config_name;
        """
        rows = await self.database.fetch_all(query, {"user_id": user_id})
        return [dict(row) for row in rows]

    async def opportunity_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /opportunities command by showing active or available configs first."""
        user, _ = await self.require_auth(update, context)
        if not user:
            return
    
        message_to_edit = None
        try:
            user_id = str(user["id"])
            
            # Determine if this is a command or callback
            if update.callback_query:
                await update.callback_query.answer()
                message_to_edit = update.callback_query.message
            else:
                message_to_edit = await update.message.reply_text(
                    "🔍 Finding your configurations...", parse_mode='HTML'
                )

            active_configs = await self._get_active_configs(user_id)

            if len(active_configs) == 1:
                config_id = str(active_configs[0]["id"])
                await self._show_filtered_opportunities(message_to_edit, context, config_id, from_command=True)
                return

            if len(active_configs) > 1:
                lines = ["📊 <b>Select Active Configuration</b>\n\n"
                         "You have multiple active strategies. Choose a config to see its opportunities:"]
                keyboard = []
                for cfg in active_configs:
                    keyboard.append([
                        InlineKeyboardButton(
                            f"🟢 {cfg['config_name']}",
                            callback_data=f"opportunity_config:{cfg['id']}"
                        )
                    ])
                keyboard.append([InlineKeyboardButton("🌐 View All (Unfiltered)", callback_data="opportunity_all")])
                reply_markup = InlineKeyboardMarkup(keyboard)
                await message_to_edit.edit_text("\n".join(lines), parse_mode='HTML', reply_markup=reply_markup)
                return
            
            # No active configs, show all available configs
            await self._show_all_configs(message_to_edit, context, user_id)

        except Exception as e:
            self.logger.error(f"Opportunity command error: {e}", exc_info=True)
            error_message = f"❌ Failed to fetch opportunities: {str(e)}"
            if message_to_edit:
                await message_to_edit.edit_text(error_message, parse_mode='HTML')
            elif update.message:
                await update.message.reply_text(error_message, parse_mode='HTML')
    
    async def _show_all_configs(self, message_to_edit: Update.message, context: ContextTypes.DEFAULT_TYPE, user_id: str):
        """Show all available funding arbitrage configs for a user."""
        try:
            # Get user's own configs
            user_configs_query = """
                SELECT id, config_name, is_active
                FROM strategy_configs
                WHERE user_id = :user_id 
                  AND is_template = FALSE
                  AND strategy_type = 'funding_arbitrage'
                ORDER BY created_at DESC
            """
            user_configs = await self.database.fetch_all(user_configs_query, {"user_id": user_id})
            
            # Get public templates
            templates_query = """
                SELECT id, config_name
                FROM strategy_configs
                WHERE is_template = TRUE
                  AND strategy_type = 'funding_arbitrage'
                ORDER BY config_name
            """
            templates = await self.database.fetch_all(templates_query)
            
            lines = ["📋 <b>Select Configuration</b>\n\n"
                     "No active strategies found. Choose a config to see its potential opportunities:"]
            keyboard = []
            
            if user_configs:
                lines.append("\n<b>Your Configs:</b>")
                for cfg in user_configs:
                    cfg_dict = dict(cfg)
                    status = "🟢" if cfg_dict.get("is_active", False) else "⚫"
                    lines.append(f"{status} {cfg_dict['config_name']}")
                    keyboard.append([
                        InlineKeyboardButton(
                            f"📋 {cfg_dict['config_name']}",
                            callback_data=f"opportunity_config:{cfg_dict['id']}"
                        )
                    ])
            
            if templates:
                lines.append("\n<b>Public Templates:</b>")
                for tpl in templates[:5]:
                    tpl_dict = dict(tpl)
                    lines.append(f"⭐ {tpl_dict['config_name']}")
                    keyboard.append([
                        InlineKeyboardButton(
                            f"⭐ {tpl_dict['config_name']}",
                            callback_data=f"opportunity_config:{tpl_dict['id']}"
                        )
                    ])
            
            if not user_configs and not templates:
                lines.append("\nNo funding arbitrage configs found. Create one with /create_config")

            keyboard.append([InlineKeyboardButton("🌐 View All (Unfiltered)", callback_data="opportunity_all")])
            
            message_text = "\n".join(lines)
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await message_to_edit.edit_text(
                message_text,
                parse_mode='HTML',
                reply_markup=reply_markup
            )
            
        except Exception as e:
            self.logger.error(f"Show all configs error: {e}", exc_info=True)
            await message_to_edit.edit_text(f"❌ Failed to load configs: {str(e)}", parse_mode='HTML')

    async def _show_filtered_opportunities(self, message_to_edit: Update.message, context: ContextTypes.DEFAULT_TYPE, config_id: str, from_command: bool = False):
        """Show opportunities filtered by a specific config."""
        try:
            await message_to_edit.edit_text(
                "🔍 Finding opportunities with your config filters...", parse_mode='HTML'
            )
            
            config_row = await self.database.fetch_one(
                "SELECT config_name, config_data FROM strategy_configs WHERE id = :id", {"id": config_id}
            )
            
            if not config_row:
                await message_to_edit.edit_text("❌ Config not found.", parse_mode='HTML')
                return
            
            config_dict = dict(config_row)
            config_name = config_dict['config_name']
            config_data_raw = config_dict['config_data']
            
            import json
            config_data = json.loads(config_data_raw) if isinstance(config_data_raw, str) else (config_data_raw or {})
            
            filters = self._config_to_opportunity_filter(config_data)
            filters.limit = 3
            filters.sort_by = "net_profit_percent"
            filters.sort_desc = True
            
            opportunities = await self.opportunity_finder.find_opportunities(filters)
            
            header = f"📊 <b>Opportunities for: {config_name}</b>"
            message = self._format_opportunities_list(opportunities, header, max_opportunities=3)
            
            keyboard = [
                [InlineKeyboardButton("🔄 Refresh", callback_data=f"opportunity_config_refresh:{config_id}")]
            ]
            
            if from_command:
                # This is the default view for a user with one active config.
                # Provide a way to see the unfiltered list.
                keyboard.append([InlineKeyboardButton("🌐 View All (Unfiltered)", callback_data="opportunity_all")])
            else:
                # This view came from a selection list, so provide a "Back" button and all view.
                keyboard.append([InlineKeyboardButton("🔙 Back to Configs", callback_data="opportunity_back_to_configs_command")])
                keyboard.append([InlineKeyboardButton("🌐 View All (Unfiltered)", callback_data="opportunity_all")])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await message_to_edit.edit_text(
                message,
                parse_mode='HTML',
                reply_markup=reply_markup
            )
            
        except Exception as e:
            self.logger.error(f"Filtered opportunities error: {e}", exc_info=True)
            await message_to_edit.edit_text(f"❌ Failed to fetch opportunities: {str(e)}", parse_mode='HTML')

    async def _show_unfiltered_opportunities(self, message_to_edit: Update.message, context: ContextTypes.DEFAULT_TYPE):
        """Show unfiltered opportunities."""
        try:
            await message_to_edit.edit_text(
                "🔍 Finding opportunities (unfiltered)...", parse_mode='HTML'
            )
            
            filters = OpportunityFilter(limit=3, min_profit_percent=Decimal("0"), sort_by="net_profit_percent", sort_desc=True)
            opportunities = await self.opportunity_finder.find_opportunities(filters)
            
            header = "📊 <b>Top Funding Arbitrage Opportunities (Unfiltered)</b>"
            message = self._format_opportunities_list(opportunities, header, max_opportunities=3)
            
            keyboard = [
                [InlineKeyboardButton("🔄 Refresh", callback_data="opportunity_all_refresh")],
                [InlineKeyboardButton("🔙 Back to My Configs", callback_data="opportunity_back_to_configs_command")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await message_to_edit.edit_text(
                message,
                parse_mode='HTML',
                reply_markup=reply_markup
            )
            
        except Exception as e:
            self.logger.error(f"Unfiltered opportunities error: {e}", exc_info=True)
            await message_to_edit.edit_text(f"❌ Failed to fetch opportunities: {str(e)}", parse_mode='HTML')

    async def opportunity_configs_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle "View My Configs" button, showing all available configs."""
        query = update.callback_query
        await query.answer()
        user, _ = await self.require_auth(update, context)
        if not user:
            return
        await self._show_all_configs(query.message, context, str(user["id"]))

    async def opportunity_config_view_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle a specific config selection to view its opportunities."""
        query = update.callback_query
        await query.answer()
        user, _ = await self.require_auth(update, context)
        if not user:
            return

        config_id = query.data.split(":", 1)[1]
        await self._show_filtered_opportunities(query.message, context, config_id)

    def _config_to_opportunity_filter(self, config_data: Dict[str, Any]) -> OpportunityFilter:
        """Convert config data to OpportunityFilter."""
        # Extract config parameters
        min_profit_rate = config_data.get('min_profit_rate')
        min_volume_24h = config_data.get('min_volume_24h')
        min_oi_usd = config_data.get('min_oi_usd')
        max_oi_usd = config_data.get('max_oi_usd')
        scan_exchanges = config_data.get('scan_exchanges') or []
        mandatory_exchange = config_data.get('mandatory_exchange')
        
        # Convert to OpportunityFilter
        filters = OpportunityFilter()
        
        if min_profit_rate is not None:
            filters.min_profit_percent = Decimal(str(min_profit_rate))
        
        if min_volume_24h is not None:
            filters.min_volume_24h = Decimal(str(min_volume_24h))
        
        if min_oi_usd is not None:
            filters.min_oi_usd = Decimal(str(min_oi_usd))
        
        if max_oi_usd is not None:
            filters.max_oi_usd = Decimal(str(max_oi_usd))
        
        if scan_exchanges:
            # Convert to lowercase list
            if isinstance(scan_exchanges, str):
                filters.whitelist_dexes = [ex.strip().lower() for ex in scan_exchanges.split(',') if ex.strip()]
            elif isinstance(scan_exchanges, list):
                filters.whitelist_dexes = [str(ex).strip().lower() for ex in scan_exchanges if str(ex).strip()]
        
        if mandatory_exchange:
            if isinstance(mandatory_exchange, str):
                filters.required_dex = mandatory_exchange.strip().lower()
        
        return filters
    
    async def opportunity_all_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle 'View All' button."""
        query = update.callback_query
        await query.answer()
        await self._show_unfiltered_opportunities(query.message, context)

    async def opportunity_all_refresh_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle refresh for the unfiltered view."""
        query = update.callback_query
        await query.answer("🔄 Refreshing...")
        await self._show_unfiltered_opportunities(query.message, context)

    async def opportunity_config_refresh_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle refresh button for config-filtered view."""
        query = update.callback_query
        await query.answer("🔄 Refreshing...")
        config_id = query.data.split(":", 1)[1]
        await self._show_filtered_opportunities(query.message, context, config_id)

    async def opportunity_back_to_configs_command_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle 'Back' button to return to the initial /opportunities command view."""
        # This effectively re-runs the /opportunities command logic
        from unittest.mock import Mock
        mock_message = Mock()
        mock_message.reply_text = update.callback_query.message.edit_text
        mock_update = Mock()
        mock_update.message = mock_message
        mock_update.effective_user = update.effective_user
        mock_update.callback_query = None
        await self.opportunity_command(mock_update, context)

    def register_handlers(self, application):
        """Register opportunity command and callback handlers"""
        application.add_handler(CommandHandler("opportunities", self.opportunity_command))
        application.add_handler(CallbackQueryHandler(self.opportunity_command, pattern="^opportunity_back_to_configs_command$"))
        application.add_handler(CallbackQueryHandler(self.opportunity_configs_callback, pattern="^opportunity_configs$"))
        application.add_handler(CallbackQueryHandler(self.opportunity_config_view_callback, pattern="^opportunity_config:"))
        application.add_handler(CallbackQueryHandler(self.opportunity_all_callback, pattern="^opportunity_all$"))
        application.add_handler(CallbackQueryHandler(self.opportunity_all_refresh_callback, pattern="^opportunity_all_refresh$"))
        application.add_handler(CallbackQueryHandler(self.opportunity_config_refresh_callback, pattern="^opportunity_config_refresh:"))
        application.add_handler(CallbackQueryHandler(self.opportunity_back_callback, pattern="^opportunity_back_to_configs$"))

    async def opportunity_back_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle 'Back to Configs' button, which shows the list of all available configs."""
        query = update.callback_query
        await query.answer()
        user, _ = await self.require_auth(update, context)
        if not user:
            return
        await self._show_all_configs(query.message, context, str(user["id"]))
