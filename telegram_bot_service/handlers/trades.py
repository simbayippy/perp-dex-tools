"""
Trades and PnL handlers for Telegram bot
"""

from typing import Optional, List, Dict, Any, Tuple
from decimal import Decimal
from datetime import datetime, timedelta, timezone
from uuid import UUID
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler

from telegram_bot_service.handlers.base import BaseHandler
from database.repositories.trade_fill_repository import TradeFillRepository


class TradesHandler(BaseHandler):
    """Handler for trades and PnL viewing commands"""
    
    async def trades_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /trades command - shows account selection or summary."""
        user, _ = await self.require_auth(update, context)
        if not user:
            return
        
        try:
            # Get user's accounts
            accounts = await self._get_user_accounts(user["id"])
            
            if not accounts:
                await update.message.reply_text(
                    "❌ <b>No Accounts Found</b>\n\n"
                    "You don't have any accounts set up.\n\n"
                    "Create an account with: <code>/create_account</code>",
                    parse_mode='HTML'
                )
                return
            
            # If single account, show summary directly
            if len(accounts) == 1:
                account_id = accounts[0]["id"]
                account_name = accounts[0]["account_name"]
                await self._show_summary(update.message, account_id, account_name)
            else:
                # Multiple accounts - show selection
                await self._show_account_selection(update.message, accounts)
                
        except Exception as e:
            self.logger.error(f"Trades command error: {e}")
            await update.message.reply_text(
                self.formatter.format_error(f"Failed to load trades: {str(e)}"),
                parse_mode='HTML'
            )
    
    async def _get_user_accounts(self, user_id: str) -> List[Dict[str, Any]]:
        """Get accounts for authenticated user."""
        query = """
            SELECT id, account_name
            FROM accounts
            WHERE user_id = :user_id
            ORDER BY account_name ASC
        """
        rows = await self.database.fetch_all(query, {"user_id": user_id})
        return [dict(row) for row in rows]
    
    async def _show_account_selection(self, message, accounts: List[Dict[str, Any]]):
        """Show account selection buttons."""
        keyboard = []
        
        # Get trade counts for each account
        for account in accounts:
            account_id = account["id"]
            trade_count = await self._get_trade_count(account_id)
            button_label = f"📊 {account['account_name']} ({trade_count} trades)"
            callback_data = f"trades_account:{account_id}"
            keyboard.append([InlineKeyboardButton(button_label, callback_data=callback_data)])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await message.reply_text(
            "📊 <b>Select Account</b>\n\n"
            "Choose an account to view trades and PnL:",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
    
    async def _get_trade_count(self, account_id: UUID) -> int:
        """Get total trade count for an account."""
        query = """
            SELECT COUNT(*) as count
            FROM trade_fills
            WHERE account_id = :account_id
        """
        row = await self.database.fetch_one(query, {"account_id": account_id})
        return row["count"] if row else 0
    
    async def _show_summary(self, message, account_id: UUID, account_name: str):
        """Show summary view for an account."""
        try:
            # Get summary data
            summary = await self._get_trades_summary(account_id)
            
            # Format summary message
            summary_msg = self.formatter.format_trades_summary(account_name, summary)
            
            # Create keyboard
            keyboard = [
                [InlineKeyboardButton("💰 View Position PnL", callback_data=f"trades_pnl:{account_id}")],
                [InlineKeyboardButton("🔄 Refresh", callback_data=f"trades_summary:{account_id}")],
            ]
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            # Split message if needed
            messages = self.formatter._split_message(summary_msg) if len(summary_msg) > self.formatter.MAX_MESSAGE_LENGTH else [summary_msg]
            
            # Send first message with buttons
            await message.reply_text(
                messages[0],
                parse_mode='HTML',
                reply_markup=reply_markup
            )
            
            # Send remaining messages if any
            for msg in messages[1:]:
                await message.reply_text(msg, parse_mode='HTML')
                
        except Exception as e:
            self.logger.error(f"Show summary error: {e}")
            await message.reply_text(
                self.formatter.format_error(f"Failed to load summary: {str(e)}"),
                parse_mode='HTML'
            )
    
    async def _get_trades_summary(self, account_id: UUID) -> Dict[str, Any]:
        """Get summary statistics for an account."""
        repository = TradeFillRepository(self.database)
        
        # Get all trades
        all_trades = await repository.get_trades_by_account(account_id, limit=10000)
        
        # Get positions with accurate PnL calculation (same method as position PnL view)
        positions_with_pnl = await self._get_positions_with_pnl(account_id)
        
        # Calculate stats
        total_trades = len(all_trades)
        entry_trades = [t for t in all_trades if t.get("trade_type") == "entry"]
        exit_trades = [t for t in all_trades if t.get("trade_type") == "exit"]
        
        open_positions = [p for p in positions_with_pnl if not p.get("closed_at")]
        closed_positions = [p for p in positions_with_pnl if p.get("closed_at")]
        
        # Calculate fees from trades
        total_entry_fees = sum(Decimal(str(t.get("total_fee", 0))) for t in entry_trades)
        total_exit_fees = sum(Decimal(str(t.get("total_fee", 0))) for t in exit_trades)
        total_fees = total_entry_fees + total_exit_fees
        
        # Calculate PnL from positions (using the accurate calculation from _get_positions_with_pnl)
        total_price_pnl = Decimal("0")
        total_funding = Decimal("0")
        total_net_pnl = Decimal("0")
        
        for position in closed_positions:
            # Use the calculated values from _get_positions_with_pnl
            price_pnl = position.get("price_pnl", Decimal("0"))
            funding = position.get("total_funding", Decimal("0"))
            net_pnl = position.get("net_pnl", Decimal("0"))
            
            total_price_pnl += price_pnl
            total_funding += funding
            total_net_pnl += net_pnl
        
        return {
            "total_trades": total_trades,
            "entry_trades": len(entry_trades),
            "exit_trades": len(exit_trades),
            "open_positions": len(open_positions),
            "closed_positions": len(closed_positions),
            "total_fees": total_fees,
            "total_pnl": total_price_pnl,
            "total_funding": total_funding,
            "net_pnl": total_net_pnl,
        }
    
    def _get_trades_cutoff_time(self) -> Optional[datetime]:
        """Get cutoff time from TRADES_CUTOFF_TIMESTAMP environment variable."""
        cutoff_str = os.getenv("TRADES_CUTOFF_TIMESTAMP")
        if not cutoff_str:
            return None
        
        try:
            # Try parsing as ISO 8601 timestamp
            if "T" in cutoff_str or "Z" in cutoff_str:
                cutoff_str = cutoff_str.replace("Z", "+00:00")
                return datetime.fromisoformat(cutoff_str)
            
            # Try parsing as Unix timestamp
            try:
                timestamp = float(cutoff_str)
                return datetime.fromtimestamp(timestamp, tz=timezone.utc)
            except ValueError:
                pass
            
            # Try parsing as "Xh" format (hours ago)
            if cutoff_str.endswith("h"):
                hours = float(cutoff_str[:-1])
                return datetime.now(timezone.utc) - timedelta(hours=hours)
            
            return None
        except Exception as e:
            self.logger.warning(f"Failed to parse TRADES_CUTOFF_TIMESTAMP: {e}")
            return None
    
    async def _get_positions(self, account_id: UUID, cutoff_time: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """Get positions for an account, optionally filtered by cutoff_time."""
        query = """
            SELECT 
                sp.id,
                sp.size_usd,
                sp.opened_at,
                sp.closed_at,
                sp.pnl_usd,
                sp.exit_reason,
                sp.entry_long_rate,
                sp.entry_short_rate,
                sp.entry_divergence,
                sp.cumulative_funding_usd,
                s.symbol as symbol_name,
                d1.name as long_dex,
                d2.name as short_dex
            FROM strategy_positions sp
            JOIN symbols s ON sp.symbol_id = s.id
            JOIN dexes d1 ON sp.long_dex_id = d1.id
            JOIN dexes d2 ON sp.short_dex_id = d2.id
            WHERE sp.account_id = :account_id
        """
        values = {"account_id": account_id}
        
        if cutoff_time:
            query += " AND sp.opened_at >= :cutoff_time"
            values["cutoff_time"] = cutoff_time.replace(tzinfo=None) if cutoff_time.tzinfo else cutoff_time
        
        query += " ORDER BY sp.opened_at DESC"
        
        rows = await self.database.fetch_all(query, values)
        return [dict(row) for row in rows]
    
    async def trades_account_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle account selection callback."""
        query = update.callback_query
        await query.answer()
        
        user, _ = await self.require_auth(update, context)
        if not user:
            return
        
        try:
            account_id_str = query.data.split(":")[1]
            account_id = UUID(account_id_str)
            
            # Get account name
            account_row = await self.database.fetch_one(
                "SELECT account_name FROM accounts WHERE id = :id",
                {"id": account_id}
            )
            account_name = account_row["account_name"] if account_row else "Unknown"
            
            await self._show_summary(query.message, account_id, account_name)
            
        except Exception as e:
            self.logger.error(f"Trades account callback error: {e}")
            await query.edit_message_text(
                self.formatter.format_error(f"Failed to load account: {str(e)}"),
                parse_mode='HTML'
            )
    
    async def trades_summary_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle summary refresh callback."""
        query = update.callback_query
        await query.answer()
        
        user, _ = await self.require_auth(update, context)
        if not user:
            return
        
        try:
            account_id_str = query.data.split(":")[1]
            account_id = UUID(account_id_str)
            
            # Get account name
            account_row = await self.database.fetch_one(
                "SELECT account_name FROM accounts WHERE id = :id",
                {"id": account_id}
            )
            account_name = account_row["account_name"] if account_row else "Unknown"
            
            # Get summary data
            summary = await self._get_trades_summary(account_id)
            summary_msg = self.formatter.format_trades_summary(account_name, summary)
            
            # Create keyboard
            keyboard = [
                [InlineKeyboardButton("💰 View Position PnL", callback_data=f"trades_pnl:{account_id}")],
                [InlineKeyboardButton("🔄 Refresh", callback_data=f"trades_summary:{account_id}")],
            ]
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            # Split message if needed
            messages = self.formatter._split_message(summary_msg) if len(summary_msg) > self.formatter.MAX_MESSAGE_LENGTH else [summary_msg]
            
            # Handle "Message is not modified" error gracefully
            try:
                await query.edit_message_text(
                    messages[0],
                    parse_mode='HTML',
                    reply_markup=reply_markup
                )
            except BadRequest as e:
                if "Message is not modified" in str(e):
                    await query.answer("✅ Trades summary is up to date", show_alert=False)
                    return
                else:
                    raise
            
            # Send remaining messages if any
            for msg in messages[1:]:
                await query.message.reply_text(msg, parse_mode='HTML')
                
        except Exception as e:
            self.logger.error(f"Trades summary callback error: {e}")
            await query.edit_message_text(
                self.formatter.format_error(f"Failed to refresh: {str(e)}"),
                parse_mode='HTML'
            )
    
    async def trades_pnl_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle position PnL callback."""
        query = update.callback_query
        await query.answer()
        
        user, _ = await self.require_auth(update, context)
        if not user:
            return
        
        try:
            account_id_str = query.data.split(":")[1]
            account_id = UUID(account_id_str)
            
            # Get account name
            account_row = await self.database.fetch_one(
                "SELECT account_name FROM accounts WHERE id = :id",
                {"id": account_id}
            )
            account_name = account_row["account_name"] if account_row else "Unknown"
            
            # Get positions with PnL
            positions = await self._get_positions_with_pnl(account_id)
            
            if not positions:
                keyboard = [[InlineKeyboardButton("⬅️ Back to Summary", callback_data=f"trades_summary:{account_id}")]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await query.edit_message_text(
                    f"💰 <b>{account_name} - Position PnL</b>\n\n"
                    "No positions found.",
                    parse_mode='HTML',
                    reply_markup=reply_markup
                )
                return
            
            # Format position PnL
            pnl_msg = self.formatter.format_position_pnl(account_name, positions)
            
            # Create keyboard
            keyboard = [
                [InlineKeyboardButton("⬅️ Back to Summary", callback_data=f"trades_summary:{account_id}")],
                [InlineKeyboardButton("🔄 Refresh", callback_data=f"trades_pnl:{account_id}")],
            ]
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            # Split message if needed
            messages = self.formatter._split_message(pnl_msg) if len(pnl_msg) > self.formatter.MAX_MESSAGE_LENGTH else [pnl_msg]
            
            # Handle "Message is not modified" error gracefully
            try:
                await query.edit_message_text(
                    messages[0],
                    parse_mode='HTML',
                    reply_markup=reply_markup
                )
            except BadRequest as e:
                if "Message is not modified" in str(e):
                    await query.answer("✅ Position PnL is up to date", show_alert=False)
                    return
                else:
                    raise
            
            # Send remaining messages if any
            for msg in messages[1:]:
                await query.message.reply_text(msg, parse_mode='HTML')
                
        except Exception as e:
            self.logger.error(f"Trades PnL callback error: {e}")
            await query.edit_message_text(
                self.formatter.format_error(f"Failed to load PnL: {str(e)}"),
                parse_mode='HTML'
            )
    
    async def _get_positions_with_pnl(self, account_id: UUID) -> List[Dict[str, Any]]:
        """Get positions with calculated PnL including per-leg breakdown."""
        # Get cutoff time for filtering
        cutoff_time = self._get_trades_cutoff_time()
        
        positions = await self._get_positions(account_id, cutoff_time=cutoff_time)
        repository = TradeFillRepository(self.database)
        
        result = []
        for position in positions:
            position_id = position["id"]
            long_dex = position.get("long_dex", "").lower()
            short_dex = position.get("short_dex", "").lower()
            
            # Get trades for this position
            trades = await repository.get_trades_by_position(position_id)
            entry_trades = [t for t in trades if t.get("trade_type") == "entry"]
            exit_trades = [t for t in trades if t.get("trade_type") == "exit"]
            
            # Separate trades by DEX
            long_entry_trades = [t for t in entry_trades if t.get("dex_name", "").lower() == long_dex]
            short_entry_trades = [t for t in entry_trades if t.get("dex_name", "").lower() == short_dex]
            long_exit_trades = [t for t in exit_trades if t.get("dex_name", "").lower() == long_dex]
            short_exit_trades = [t for t in exit_trades if t.get("dex_name", "").lower() == short_dex]
            
            # Calculate entry/exit values and prices
            long_entry_value = sum(Decimal(str(t.get("total_quantity", 0))) * Decimal(str(t.get("weighted_avg_price", 0))) for t in long_entry_trades)
            short_entry_value = sum(Decimal(str(t.get("total_quantity", 0))) * Decimal(str(t.get("weighted_avg_price", 0))) for t in short_entry_trades)
            long_exit_value = sum(Decimal(str(t.get("total_quantity", 0))) * Decimal(str(t.get("weighted_avg_price", 0))) for t in long_exit_trades)
            short_exit_value = sum(Decimal(str(t.get("total_quantity", 0))) * Decimal(str(t.get("weighted_avg_price", 0))) for t in short_exit_trades)
            
            # Calculate weighted average entry/exit prices
            long_entry_qty = sum(Decimal(str(t.get("total_quantity", 0))) for t in long_entry_trades)
            short_entry_qty = sum(Decimal(str(t.get("total_quantity", 0))) for t in short_entry_trades)
            long_exit_qty = sum(Decimal(str(t.get("total_quantity", 0))) for t in long_exit_trades)
            short_exit_qty = sum(Decimal(str(t.get("total_quantity", 0))) for t in short_exit_trades)
            
            long_entry_price = long_entry_value / long_entry_qty if long_entry_qty > 0 else Decimal("0")
            short_entry_price = short_entry_value / short_entry_qty if short_entry_qty > 0 else Decimal("0")
            long_exit_price = long_exit_value / long_exit_qty if long_exit_qty > 0 else Decimal("0")
            short_exit_price = short_exit_value / short_exit_qty if short_exit_qty > 0 else Decimal("0")
            
            # Calculate per-leg PnL
            # Long leg: profit when exit price > entry price (we bought low, sold high)
            long_leg_pnl = Decimal("0")
            if long_exit_qty > 0 and long_entry_qty > 0:
                # For long: profit = (exit_price - entry_price) * quantity
                long_leg_pnl = (long_exit_price - long_entry_price) * long_exit_qty
            elif long_exit_trades:
                # Use realized_pnl if available
                for trade in long_exit_trades:
                    realized_pnl = trade.get("realized_pnl")
                    if realized_pnl:
                        long_leg_pnl += Decimal(str(realized_pnl))
            
            # Short leg: profit when entry price > exit price (we sold high, bought low)
            short_leg_pnl = Decimal("0")
            if short_exit_qty > 0 and short_entry_qty > 0:
                # For short: profit = (entry_price - exit_price) * quantity
                short_leg_pnl = (short_entry_price - short_exit_price) * short_exit_qty
            elif short_exit_trades:
                # Use realized_pnl if available
                for trade in short_exit_trades:
                    realized_pnl = trade.get("realized_pnl")
                    if realized_pnl:
                        short_leg_pnl += Decimal(str(realized_pnl))
            
            # Calculate fees
            entry_fees = sum(Decimal(str(t.get("total_fee", 0))) for t in entry_trades)
            exit_fees = sum(Decimal(str(t.get("total_fee", 0))) for t in exit_trades)
            total_fees = entry_fees + exit_fees
            
            # Get price PnL (sum of both legs)
            price_pnl = long_leg_pnl + short_leg_pnl
            
            # If price_pnl is still 0, try from position record
            if price_pnl == 0 and position.get("pnl_usd"):
                price_pnl = Decimal(str(position["pnl_usd"]))
            
            # Get funding
            total_funding = Decimal("0")
            for trade in entry_trades + exit_trades:
                realized_funding = trade.get("realized_funding")
                if realized_funding:
                    total_funding += Decimal(str(realized_funding))
            
            if total_funding == 0 and position.get("cumulative_funding_usd"):
                total_funding = Decimal(str(position["cumulative_funding_usd"]))
            
            net_pnl = price_pnl + total_funding - total_fees
            
            # Store enhanced data
            position["entry_trades"] = entry_trades
            position["exit_trades"] = exit_trades
            position["entry_fees"] = entry_fees
            position["exit_fees"] = exit_fees
            position["total_fees"] = total_fees
            position["price_pnl"] = price_pnl
            position["total_funding"] = total_funding
            position["net_pnl"] = net_pnl
            
            # Per-leg breakdown
            position["long_entry_price"] = long_entry_price
            position["long_exit_price"] = long_exit_price
            position["long_entry_value"] = long_entry_value
            position["long_exit_value"] = long_exit_value
            position["long_leg_pnl"] = long_leg_pnl
            
            position["short_entry_price"] = short_entry_price
            position["short_exit_price"] = short_exit_price
            position["short_entry_value"] = short_entry_value
            position["short_exit_value"] = short_exit_value
            position["short_leg_pnl"] = short_leg_pnl
            
            result.append(position)
        
        return result
    
    def register_handlers(self, application):
        """Register command and callback handlers."""
        # Command
        application.add_handler(CommandHandler("trades", self.trades_command))
        
        # Callbacks
        application.add_handler(CallbackQueryHandler(
            self.trades_account_callback,
            pattern="^trades_account:"
        ))
        application.add_handler(CallbackQueryHandler(
            self.trades_summary_callback,
            pattern="^trades_summary:"
        ))
        application.add_handler(CallbackQueryHandler(
            self.trades_pnl_callback,
            pattern="^trades_pnl:"
        ))

