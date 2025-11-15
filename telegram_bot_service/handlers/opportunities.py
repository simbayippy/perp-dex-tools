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
    
    async def opportunity_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /opportunity command - shows unfiltered opportunities."""
        user, _ = await self.require_auth(update, context)
        if not user:
            return
        
        try:
            # Show loading message
            loading_msg = await update.message.reply_text(
                "🔍 <b>Finding opportunities...</b>\n\n"
                "Scanning funding rates across all exchanges...",
                parse_mode='HTML'
            )
            
            # Find opportunities with minimal filters (unfiltered)
            filters = OpportunityFilter(
                limit=3,
                min_profit_percent=Decimal("0"),  # No minimum profit filter
                sort_by="net_profit_percent",
                sort_desc=True
            )
            
            opportunities = await self.opportunity_finder.find_opportunities(filters)
            
            # Format message
            header = "📊 <b>Top Funding Arbitrage Opportunities</b>"
            message = self._format_opportunities_list(opportunities, header, max_opportunities=3)
            
            # Create keyboard with refresh and config button
            keyboard = [
                [InlineKeyboardButton("🔄 Refresh", callback_data="opportunity_refresh")],
                [InlineKeyboardButton("⚙️ View My Configs", callback_data="opportunity_configs")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            # Replace loading message
            await loading_msg.edit_text(
                message,
                parse_mode='HTML',
                reply_markup=reply_markup
            )
            
        except Exception as e:
            self.logger.error(f"Opportunity command error: {e}", exc_info=True)
            await update.message.reply_text(
                f"❌ Failed to fetch opportunities: {str(e)}",
                parse_mode='HTML'
            )
    
    async def opportunity_configs_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle config selection callback - shows user's configs."""
        query = update.callback_query
        await query.answer()
        
        user, _ = await self.require_auth(update, context)
        if not user:
            return
        
        try:
            user_id = str(user["id"])
            
            # Get user's own configs (funding_arbitrage only)
            user_configs_query = """
                SELECT id, config_name, strategy_type, is_active, created_at
                FROM strategy_configs
                WHERE user_id = :user_id 
                  AND is_template = FALSE
                  AND strategy_type = 'funding_arbitrage'
                ORDER BY created_at DESC
            """
            user_configs = await self.database.fetch_all(user_configs_query, {"user_id": user_id})
            
            # Get public templates (funding_arbitrage only)
            templates_query = """
                SELECT id, config_name, strategy_type, created_at
                FROM strategy_configs
                WHERE is_template = TRUE
                  AND strategy_type = 'funding_arbitrage'
                ORDER BY config_name
            """
            templates = await self.database.fetch_all(templates_query)
            
            # Build message
            lines = ["📋 <b>Select Configuration</b>\n"]
            keyboard = []
            
            # User's own configs
            if user_configs:
                lines.append("<b>Your Configs:</b>")
                for cfg in user_configs:
                    cfg_dict = dict(cfg) if not isinstance(cfg, dict) else cfg
                    config_id = str(cfg_dict["id"])
                    config_name = cfg_dict["config_name"]
                    status = "🟢" if cfg_dict.get("is_active", False) else "⚫"
                    lines.append(f"{status} <b>{config_name}</b>")
                    
                    keyboard.append([
                        InlineKeyboardButton(
                            f"📋 {config_name}",
                            callback_data=f"opportunity_config:{config_id}"
                        )
                    ])
                lines.append("")
            
            # Public templates
            if templates:
                lines.append("<b>Public Templates:</b>")
                for tpl in templates[:10]:  # Limit to 10 templates
                    tpl_dict = dict(tpl) if not isinstance(tpl, dict) else tpl
                    config_id = str(tpl_dict['id'])
                    config_name = tpl_dict['config_name']
                    lines.append(f"⭐ {config_name}")
                    
                    keyboard.append([
                        InlineKeyboardButton(
                            f"⭐ {config_name}",
                            callback_data=f"opportunity_config:{config_id}"
                        )
                    ])
                if len(templates) > 10:
                    lines.append(f"... and {len(templates) - 10} more")
            
            if not user_configs and not templates:
                lines.append("No funding arbitrage configs found.")
                lines.append("Create one with /create_config")
            
            # Add back button
            keyboard.append([
                InlineKeyboardButton("🔙 Back to Opportunities", callback_data="opportunity_back")
            ])
            
            message = "\n".join(lines)
            reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
            
            await query.edit_message_text(
                message,
                parse_mode='HTML',
                reply_markup=reply_markup
            )
            
        except Exception as e:
            self.logger.error(f"Opportunity configs callback error: {e}", exc_info=True)
            await query.edit_message_text(
                f"❌ Failed to load configs: {str(e)}",
                parse_mode='HTML'
            )
    
    async def opportunity_config_view_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle config-specific opportunity view callback."""
        query = update.callback_query
        await query.answer()
        
        user, _ = await self.require_auth(update, context)
        if not user:
            return
        
        # Parse config_id from callback data
        callback_data = query.data
        if not callback_data.startswith("opportunity_config:"):
            await query.edit_message_text(
                "❌ Invalid selection. Please try again.",
                parse_mode='HTML'
            )
            return
        
        config_id = callback_data.split(":", 1)[1]
        
        try:
            # Show loading message
            await query.edit_message_text(
                "🔍 <b>Finding opportunities...</b>\n\n"
                "Applying config filters...",
                parse_mode='HTML'
            )
            
            # Get config from database
            config_row = await self.database.fetch_one(
                """
                SELECT config_name, config_data, strategy_type, is_template, user_id
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
            
            config_dict = dict(config_row) if not isinstance(config_row, dict) else config_row
            config_name = config_dict['config_name']
            config_data_raw = config_dict['config_data']
            
            # Parse config_data (JSONB might be string or dict)
            import json
            if isinstance(config_data_raw, str):
                try:
                    config_data = json.loads(config_data_raw)
                except json.JSONDecodeError:
                    config_data = {}
            else:
                config_data = config_data_raw or {}
            
            # Convert config params to OpportunityFilter
            filters = self._config_to_opportunity_filter(config_data)
            filters.limit = 3
            filters.sort_by = "net_profit_percent"
            filters.sort_desc = True
            
            # Find opportunities with config filters
            opportunities = await self.opportunity_finder.find_opportunities(filters)
            
            # Format message with config-specific header
            header = f"📊 <b>Opportunities for: {config_name}</b>"
            message = self._format_opportunities_list(opportunities, header, max_opportunities=3)
            
            # Create keyboard with refresh and back button
            keyboard = [
                [InlineKeyboardButton("🔄 Refresh", callback_data=f"opportunity_config_refresh:{config_id}")],
                [InlineKeyboardButton("🔙 Back to Configs", callback_data="opportunity_configs")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                message,
                parse_mode='HTML',
                reply_markup=reply_markup
            )
            
        except Exception as e:
            self.logger.error(f"Opportunity config view error: {e}", exc_info=True)
            await query.edit_message_text(
                f"❌ Failed to fetch opportunities: {str(e)}",
                parse_mode='HTML'
            )
    
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
    
    async def opportunity_refresh_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle refresh button - refresh default unfiltered view."""
        query = update.callback_query
        await query.answer("🔄 Refreshing opportunities...")
        
        user, _ = await self.require_auth(update, context)
        if not user:
            return
        
        try:
            # Show loading message
            await query.edit_message_text(
                "🔍 <b>Finding opportunities...</b>\n\n"
                "Scanning funding rates across all exchanges...",
                parse_mode='HTML'
            )
            
            # Find opportunities with minimal filters (unfiltered)
            filters = OpportunityFilter(
                limit=3,
                min_profit_percent=Decimal("0"),  # No minimum profit filter
                sort_by="net_profit_percent",
                sort_desc=True
            )
            
            opportunities = await self.opportunity_finder.find_opportunities(filters)
            
            # Format message
            header = "📊 <b>Top Funding Arbitrage Opportunities</b>"
            message = self._format_opportunities_list(opportunities, header, max_opportunities=3)
            
            # Create keyboard with config button and refresh button
            keyboard = [
                [InlineKeyboardButton("🔄 Refresh", callback_data="opportunity_refresh")],
                [InlineKeyboardButton("⚙️ View My Configs", callback_data="opportunity_configs")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                message,
                parse_mode='HTML',
                reply_markup=reply_markup
            )
            
        except Exception as e:
            self.logger.error(f"Opportunity refresh callback error: {e}", exc_info=True)
            await query.edit_message_text(
                f"❌ Failed to refresh opportunities: {str(e)}",
                parse_mode='HTML'
            )
    
    async def opportunity_config_refresh_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle refresh button for config-filtered view."""
        query = update.callback_query
        await query.answer("🔄 Refreshing opportunities...")
        
        user, _ = await self.require_auth(update, context)
        if not user:
            return
        
        # Parse config_id from callback data
        callback_data = query.data
        if not callback_data.startswith("opportunity_config_refresh:"):
            await query.edit_message_text(
                "❌ Invalid refresh request. Please try again.",
                parse_mode='HTML'
            )
            return
        
        config_id = callback_data.split(":", 1)[1]
        
        try:
            # Show loading message
            await query.edit_message_text(
                "🔍 <b>Finding opportunities...</b>\n\n"
                "Applying config filters...",
                parse_mode='HTML'
            )
            
            # Get config from database
            config_row = await self.database.fetch_one(
                """
                SELECT config_name, config_data, strategy_type, is_template, user_id
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
            
            config_dict = dict(config_row) if not isinstance(config_row, dict) else config_row
            config_name = config_dict['config_name']
            config_data_raw = config_dict['config_data']
            
            # Parse config_data (JSONB might be string or dict)
            import json
            if isinstance(config_data_raw, str):
                try:
                    config_data = json.loads(config_data_raw)
                except json.JSONDecodeError:
                    config_data = {}
            else:
                config_data = config_data_raw or {}
            
            # Convert config params to OpportunityFilter
            filters = self._config_to_opportunity_filter(config_data)
            filters.limit = 3
            filters.sort_by = "net_profit_percent"
            filters.sort_desc = True
            
            # Find opportunities with config filters
            opportunities = await self.opportunity_finder.find_opportunities(filters)
            
            # Format message with config-specific header
            header = f"📊 <b>Opportunities for: {config_name}</b>"
            message = self._format_opportunities_list(opportunities, header, max_opportunities=3)
            
            # Create keyboard with refresh and back button
            keyboard = [
                [InlineKeyboardButton("🔄 Refresh", callback_data=f"opportunity_config_refresh:{config_id}")],
                [InlineKeyboardButton("🔙 Back to Configs", callback_data="opportunity_configs")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                message,
                parse_mode='HTML',
                reply_markup=reply_markup
            )
            
        except Exception as e:
            self.logger.error(f"Opportunity config refresh error: {e}", exc_info=True)
            await query.edit_message_text(
                f"❌ Failed to refresh opportunities: {str(e)}",
                parse_mode='HTML'
            )
    
    async def opportunity_back_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle back button - return to default unfiltered view."""
        query = update.callback_query
        await query.answer()
        
        user, _ = await self.require_auth(update, context)
        if not user:
            return
        
        try:
            # Show loading message
            await query.edit_message_text(
                "🔍 <b>Finding opportunities...</b>\n\n"
                "Scanning funding rates across all exchanges...",
                parse_mode='HTML'
            )
            
            # Find opportunities with minimal filters (unfiltered)
            filters = OpportunityFilter(
                limit=3,
                min_profit_percent=Decimal("0"),  # No minimum profit filter
                sort_by="net_profit_percent",
                sort_desc=True
            )
            
            opportunities = await self.opportunity_finder.find_opportunities(filters)
            
            # Format message
            header = "📊 <b>Top Funding Arbitrage Opportunities</b>"
            message = self._format_opportunities_list(opportunities, header, max_opportunities=3)
            
            # Create keyboard with config button and refresh button
            keyboard = [
                [InlineKeyboardButton("🔄 Refresh", callback_data="opportunity_refresh")],
                [InlineKeyboardButton("⚙️ View My Configs", callback_data="opportunity_configs")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                message,
                parse_mode='HTML',
                reply_markup=reply_markup
            )
            
        except Exception as e:
            self.logger.error(f"Opportunity back callback error: {e}", exc_info=True)
            await query.edit_message_text(
                f"❌ Failed to fetch opportunities: {str(e)}",
                parse_mode='HTML'
            )
    
    def register_handlers(self, application):
        """Register opportunity command and callback handlers"""
        # Command handler
        application.add_handler(CommandHandler("opportunities", self.opportunity_command))
        
        # Callback query handlers
        application.add_handler(CallbackQueryHandler(
            self.opportunity_refresh_callback,
            pattern="^opportunity_refresh$"
        ))
        application.add_handler(CallbackQueryHandler(
            self.opportunity_config_refresh_callback,
            pattern="^opportunity_config_refresh:"
        ))
        application.add_handler(CallbackQueryHandler(
            self.opportunity_configs_callback,
            pattern="^opportunity_configs$"
        ))
        application.add_handler(CallbackQueryHandler(
            self.opportunity_config_view_callback,
            pattern="^opportunity_config:"
        ))
        application.add_handler(CallbackQueryHandler(
            self.opportunity_back_callback,
            pattern="^opportunity_back$"
        ))

