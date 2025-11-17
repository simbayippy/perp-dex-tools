"""
Message formatters for Telegram bot responses
"""

from typing import Dict, Any, List, Optional
from decimal import Decimal
from datetime import datetime, timedelta, timezone

# Exchange emoji mapping
EXCHANGE_EMOJIS = {
    "lighter": "⚡",
    "aster": "✨",
    "backpack": "🎒",
    "paradex": "🎪",
    "grvt": "🔷",
    "edgex": "🔹",
}


class TelegramFormatter:
    """Format API responses into readable Telegram messages"""
    
    MAX_MESSAGE_LENGTH = 4096
    
    @staticmethod
    def format_status(data: Dict[str, Any]) -> str:
        """Format status response."""
        lines = [
            "📊 <b>Strategy Status</b>",
            "",
            f"User: {data.get('user', 'N/A')}",
            f"Strategy: {data.get('strategy', 'N/A')}",
            f"Status: {data.get('status', 'N/A')}",
        ]
        
        accounts = data.get('accessible_accounts', [])
        if accounts:
            lines.append("")
            lines.append("<b>Accessible Accounts:</b>")
            for acc in accounts:
                status_icon = "✅" if acc.get('is_active') else "❌"
                lines.append(f"  {status_icon} {acc.get('account_name', 'N/A')}")
        
        return "\n".join(lines)
    
    @staticmethod
    def format_positions(data: Dict[str, Any]) -> str:
        """Format positions response in mobile-friendly layout."""
        accounts = data.get('accounts', [])
        if not accounts:
            return "📊 <b>No active positions</b>"
        
        messages = []
        for account in accounts:
            account_name = account.get('account_name', 'N/A')
            positions = account.get('positions', [])
            
            if not positions:
                messages.append(f"📊 <b>{account_name}</b>\nNo active positions")
                continue
            
            for pos in positions:
                pos_message = TelegramFormatter._format_single_position(pos, account_name)
                messages.append(pos_message)
        
        return "\n\n".join(messages)
    
    @staticmethod
    def _format_single_position(pos: Dict[str, Any], account_name: str) -> str:
        """Format a single position optimized for mobile viewing."""
        symbol = pos.get('symbol', 'N/A')
        long_dex = pos.get('long_dex', 'N/A').upper()
        short_dex = pos.get('short_dex', 'N/A').upper()
        age_hours = pos.get('age_hours', 0)
        
        # Format age
        age_str = TelegramFormatter._format_hours(age_hours)
        
        # Header with emoji and formatting
        lines = [
            f"📊 <b>{symbol}</b> ({long_dex}/{short_dex})",
            f"<i>Account: {account_name}</i>",
            ""
        ]
        
        # Yield summary with better formatting
        entry_apy = pos.get('entry_divergence_apy')
        current_apy = pos.get('current_divergence_apy')
        erosion_ratio = pos.get('profit_erosion_ratio', 1.0)
        min_erosion_threshold = pos.get('min_erosion_threshold')
        
        if entry_apy is not None and current_apy is not None:
            erosion_pct = (1.0 - erosion_ratio) * 100 if erosion_ratio <= 1.0 else 0.0
            threshold_pct = min_erosion_threshold * 100 if min_erosion_threshold else None
            
            lines.append("<b>📈 Yield (Annualized)</b>")
            lines.append(f"  • Entry: <code>{entry_apy:.2f}%</code>")
            lines.append(f"  • Current: <code>{current_apy:.2f}%</code>")
            
            erosion_emoji = "🔴" if erosion_pct > 50 else "🟡" if erosion_pct > 30 else "🟢"
            if threshold_pct:
                lines.append(f"  • Erosion: {erosion_emoji} <code>{erosion_pct:.1f}%</code> (limit {threshold_pct:.0f}%)")
            else:
                lines.append(f"  • Erosion: {erosion_emoji} <code>{erosion_pct:.1f}%</code>")
            lines.append("")
        
        # Simplified hold status
        lines.append(f"<b>⏱ Position Age:</b> {age_str}")
        
        min_hold_summary = TelegramFormatter._format_min_hold_simple(pos)
        if min_hold_summary:
            lines.append(min_hold_summary)
        
        max_hold_summary = TelegramFormatter._format_max_hold_simple(pos)
        if max_hold_summary:
            lines.append(max_hold_summary)
        
        lines.append("")
        
        # Per-leg details (mobile-friendly vertical layout)
        legs = pos.get('legs', [])
        total_unrealized_pnl = 0.0
        total_funding = 0.0
        
        for i, leg in enumerate(legs):
            dex = leg.get('dex', 'N/A').upper()
            side = leg.get('side', 'n/a')
            quantity = leg.get('quantity', 0)
            entry_price = leg.get('entry_price')
            mark_price = leg.get('mark_price')
            unrealized_pnl = leg.get('unrealized_pnl')
            funding_accrued = leg.get('funding_accrued', 0)
            funding_apy = leg.get('funding_apy')
            
            # Accumulate totals
            if unrealized_pnl is not None:
                total_unrealized_pnl += unrealized_pnl
            if funding_accrued is not None:
                total_funding += funding_accrued
            
            # Side emoji
            side_emoji = "🟢" if side == "long" else "🔴" if side == "short" else "⚪"
            
            # Get leverage if available
            leverage = leg.get('leverage')
            if leverage is not None:
                leverage_str = f" {int(leverage)}x" if leverage >= 1 else f" {leverage:.1f}x"
                lines.append(f"<b>{side_emoji} {dex}</b> ({side.upper()}{leverage_str})")
            else:
                lines.append(f"<b>{side_emoji} {dex}</b> ({side.upper()})")
            
            # Price info
            if entry_price:
                lines.append(f"  Entry: <code>${entry_price:.6f}</code>")
            if mark_price:
                lines.append(f"  Mark:  <code>${mark_price:.6f}</code>")
            
            # Quantity
            if quantity:
                lines.append(f"  Qty:   <code>{quantity:,.4f}</code>")
            
            # PnL with emoji
            if unrealized_pnl is not None:
                pnl_emoji = "📈" if unrealized_pnl > 0 else "📉" if unrealized_pnl < 0 else "➖"
                lines.append(f"  uPnL:  {pnl_emoji} <code>${unrealized_pnl:+.2f}</code>")
            
            # Funding
            if funding_accrued is not None:
                funding_emoji = "💰" if funding_accrued > 0 else "💸" if funding_accrued < 0 else "➖"
                lines.append(f"  Funding: {funding_emoji} <code>${funding_accrued:+.2f}</code>")
            
            # Funding APY
            if funding_apy is not None:
                apy_emoji = "📈" if funding_apy > 0 else "📉" if funding_apy < 0 else "➖"
                lines.append(f"  APY: {apy_emoji} <code>{funding_apy:.2f}%</code>")
            
            if i < len(legs) - 1:
                lines.append("")
        
        lines.append("")
        lines.append("─" * 18)
        
        # Summary section
        lines.append("<b>💼 Summary</b>")
        
        # Net PnL (uPnL + Funding)
        net_pnl = total_unrealized_pnl + total_funding
        net_pnl_emoji = "📈" if net_pnl > 0 else "📉" if net_pnl < 0 else "➖"
        
        # Calculate net PnL percentage (if size is available)
        size_usd = pos.get('size_usd', 0)
        if size_usd and size_usd > 0:
            net_pnl_pct = (net_pnl / size_usd) * 100
            lines.append(f"  • Net PnL: {net_pnl_emoji} <code>${net_pnl:+.2f}</code> ({net_pnl_pct:+.2f}%)")
        else:
            lines.append(f"  • Net PnL: {net_pnl_emoji} <code>${net_pnl:+.2f}</code>")
        
        # Total unrealized PnL
        upnl_emoji = "📈" if total_unrealized_pnl > 0 else "📉" if total_unrealized_pnl < 0 else "➖"
        lines.append(f"  • Total uPnL: {upnl_emoji} <code>${total_unrealized_pnl:+.2f}</code>")
        
        # Total funding
        funding_emoji = "💰" if total_funding > 0 else "💸" if total_funding < 0 else "➖"
        lines.append(f"  • Total Funding: {funding_emoji} <code>${total_funding:+.2f}</code>")
        
        # Position size
        if size_usd:
            lines.append(f"  • Position Size: <code>${size_usd:,.2f}</code>")

        # uncomment to add position ID to the message
        # lines.append("")
        # lines.append("─" * 18)
        
        # # Position ID for closing
        # pos_id = pos.get('id', '')
        # lines.append(f"<b>Position ID:</b>")
        # lines.append(f"<code>{pos_id}</code>")
        
        message = "\n".join(lines)
        
        # Split if too long
        if len(message) > TelegramFormatter.MAX_MESSAGE_LENGTH:
            parts = TelegramFormatter._split_message(message)
            return parts[0] + "\n\n<i>(Truncated)</i>"
        
        return message
    
    @staticmethod
    def _format_min_hold_simple(pos: Dict[str, Any]) -> str:
        """Format min hold status (simplified for mobile)."""
        min_hold_hours = pos.get('min_hold_hours')
        age_hours = pos.get('age_hours', 0)
        
        if min_hold_hours is None:
            return None
        
        min_hold_hours_val = float(min_hold_hours) if min_hold_hours else 0
        if min_hold_hours_val <= 0:
            return None
        
        remaining = max(0.0, min_hold_hours_val - age_hours)
        
        if remaining <= 0:
            return "  • Min hold: ✅ <i>satisfied</i>"
        
        # Format remaining time
        remaining_minutes = max(0, int(round(remaining * 60)))
        hours_left, minutes_left = divmod(remaining_minutes, 60)
        parts = []
        if hours_left:
            parts.append(f"{hours_left}h")
        if minutes_left or not parts:
            parts.append(f"{minutes_left}m")
        remaining_fmt = " ".join(parts)
        
        return f"  • Min hold: ⏳ <code>{remaining_fmt}</code> left"
    
    @staticmethod
    def _format_max_hold_simple(pos: Dict[str, Any]) -> str:
        """Format max hold status (simplified for mobile)."""
        max_age_hours = pos.get('max_position_age_hours')
        age_hours = pos.get('age_hours', 0)
        
        if max_age_hours is None:
            return None
        
        max_age_hours_val = float(max_age_hours) if max_age_hours else 0
        if max_age_hours_val <= 0:
            return None
        
        remaining = max(0.0, max_age_hours_val - age_hours)
        
        if remaining <= 0:
            return "  • Max hold: 🚨 <b>EXCEEDED</b>"
        
        # Format remaining time
        remaining_minutes = max(0, int(round(remaining * 60)))
        hours_left, minutes_left = divmod(remaining_minutes, 60)
        parts = []
        if hours_left:
            parts.append(f"{hours_left}h")
        if minutes_left or not parts:
            parts.append(f"{minutes_left}m")
        remaining_fmt = " ".join(parts)
        
        return f"  • Max hold: ⏰ <code>{remaining_fmt}</code> left"
    
    @staticmethod
    def format_positions_for_selection(data: Dict[str, Any]) -> str:
        """Format positions as a simple list for selection."""
        accounts = data.get('accounts', [])
        if not accounts:
            return "📊 <b>No active positions</b>\n\nNothing to close."
        
        lines = [
            "📊 <b>Select a position to close:</b>",
            ""
        ]
        
        position_index = 0
        for account in accounts:
            account_name = account.get('account_name', 'N/A')
            positions = account.get('positions', [])
            
            if not positions:
                continue
            
            for pos in positions:
                position_index += 1
                symbol = pos.get('symbol', 'N/A')
                long_dex = pos.get('long_dex', 'N/A').upper()
                short_dex = pos.get('short_dex', 'N/A').upper()
                
                # Calculate net PnL
                legs = pos.get('legs', [])
                total_unrealized_pnl = sum(
                    leg.get('unrealized_pnl', 0) or 0 
                    for leg in legs
                )
                total_funding = sum(
                    leg.get('funding_accrued', 0) or 0 
                    for leg in legs
                )
                net_pnl = total_unrealized_pnl + total_funding
                
                # PnL emoji
                pnl_emoji = "📈" if net_pnl > 0 else "📉" if net_pnl < 0 else "➖"
                
                # Age
                age_hours = pos.get('age_hours', 0)
                age_str = TelegramFormatter._format_hours(age_hours)
                
                lines.append(
                    f"<b>{position_index}.</b> {symbol} ({long_dex}/{short_dex})\n"
                    f"   Account: {account_name}\n"
                    f"   Net PnL: {pnl_emoji} <code>${net_pnl:+.2f}</code>\n"
                    f"   Age: {age_str}"
                )
                lines.append("")
        
        return "\n".join(lines)
    
    @staticmethod
    def format_close_result(data: Dict[str, Any]) -> str:
        """Format close position result."""
        if data.get('success'):
            return (
                "✅ <b>Position Closed</b>\n\n"
                f"Position ID: <code>{data.get('position_id', 'N/A')}</code>\n"
                f"Account: {data.get('account_name', 'N/A')}\n"
                f"Order Type: {data.get('order_type', 'N/A')}\n"
                f"Message: {data.get('message', 'Success')}"
            )
        else:
            return (
                "❌ <b>Close Failed</b>\n\n"
                f"Error: {data.get('error', 'Unknown error')}"
            )
    
    @staticmethod
    def format_balances(data: Dict[str, Any]) -> str:
        """Format balances response in mobile-friendly layout."""
        accounts = data.get('accounts', [])
        if not accounts:
            return "💰 <b>No accounts found</b>"
        
        # Define consistent exchange order (alphabetical)
        EXCHANGE_ORDER = ['ASTER', 'BACKPACK', 'EDGEX', 'GRVT', 'LIGHTER', 'PARADEX']
        
        messages = []
        for account in accounts:
            account_name = account.get('account_name', 'N/A')
            balances = account.get('balances', [])
            
            if not balances:
                messages.append(f"💰 <b>{account_name}</b>\nNo exchange balances configured")
                continue
            
            # Build account balance message
            lines = [
                f"💰 <b>{account_name}</b>",
                ""
            ]
            
            # Sort balances by exchange name (consistent order)
            def get_exchange_sort_key(balance_info: Dict[str, Any]) -> int:
                """Get sort key for exchange ordering."""
                exchange = balance_info.get('exchange', 'N/A').upper()
                try:
                    return EXCHANGE_ORDER.index(exchange)
                except ValueError:
                    # Exchange not in predefined list, put at end
                    return len(EXCHANGE_ORDER) + ord(exchange[0]) if exchange else 999
            
            sorted_balances = sorted(balances, key=get_exchange_sort_key)
            
            # Track totals
            total_balance = Decimal("0")
            successful_balances = 0
            
            for balance_info in sorted_balances:
                exchange = balance_info.get('exchange', 'N/A').upper()
                balance_str = balance_info.get('balance')
                error = balance_info.get('error')
                
                if balance_str is not None:
                    try:
                        balance = Decimal(balance_str)
                        total_balance += balance
                        successful_balances += 1
                        
                        # Format balance with appropriate precision
                        if balance >= 1000:
                            balance_display = f"${balance:,.2f}"
                        elif balance >= 1:
                            balance_display = f"${balance:.2f}"
                        else:
                            balance_display = f"${balance:.4f}"
                        
                        lines.append(f"  • {exchange}: <code>{balance_display}</code>")
                    except (ValueError, TypeError):
                        # Invalid balance format
                        lines.append(f"  • {exchange}: ❌ <i>Invalid balance</i>")
                elif error:
                    # Show error message
                    error_msg = error[:50] + "..." if len(error) > 50 else error
                    lines.append(f"  • {exchange}: ❌ <i>{error_msg}</i>")
                else:
                    # Unknown state
                    lines.append(f"  • {exchange}: ❓ <i>Unknown</i>")
            
            # Add total if we have successful balances
            if successful_balances > 0:
                lines.append("")
                if total_balance >= 1000:
                    total_display = f"${total_balance:,.2f}"
                elif total_balance >= 1:
                    total_display = f"${total_balance:.2f}"
                else:
                    total_display = f"${total_balance:.4f}"
                lines.append(f"<b>Total:</b> <code>{total_display}</code>")
            
            messages.append("\n".join(lines))
        
        return "\n\n".join(messages)
    
    @staticmethod
    def format_trades_summary(account_name: str, summary: Dict[str, Any]) -> str:
        """Format trades summary statistics."""
        total_trades = summary.get("total_trades", 0)
        entry_trades = summary.get("entry_trades", 0)
        exit_trades = summary.get("exit_trades", 0)
        open_positions = summary.get("open_positions", 0)
        closed_positions = summary.get("closed_positions", 0)
        total_fees = summary.get("total_fees", Decimal("0"))
        total_pnl = summary.get("total_pnl", Decimal("0"))
        total_funding = summary.get("total_funding", Decimal("0"))
        net_pnl = summary.get("net_pnl", Decimal("0"))
        
        lines = [
            f"📊 <b>{account_name} - Trading Summary</b>",
            "",
            "<b>💼 Positions:</b>",
            f"  Open: <code>{open_positions}</code>",
            f"  Closed: <code>{closed_positions}</code>",
            "",
            "<b>💰 Financials:</b>",
            f"  Total Fees: <code>${total_fees:.4f}</code>",
            f"  Price PnL: <code>${total_pnl:.2f}</code>",
            f"  Funding: <code>${total_funding:.2f}</code>",
        ]
        
        # Net PnL with color indicator
        net_pnl_emoji = "🟢" if net_pnl >= 0 else "🔴"
        lines.append(f"  Net PnL: {net_pnl_emoji} <code>${net_pnl:.2f}</code>")
        
        return "\n".join(lines)
    
    @staticmethod
    def _get_exchange_emoji(dex_name: str) -> str:
        """Get emoji for exchange name."""
        return EXCHANGE_EMOJIS.get(dex_name.lower(), "")
    
    @staticmethod
    def format_position_pnl(account_name: str, positions: List[Dict[str, Any]]) -> str:
        """Format position-level PnL with per-leg breakdown and trade details."""
        lines = [
            f"<b>{account_name} - Position PnL</b>",
            "",
        ]
        
        for idx, position in enumerate(positions[:3], 1):  # Limit to 3 most recent
            symbol = position.get("symbol_name", "N/A")
            long_dex = position.get("long_dex", "N/A").upper()
            short_dex = position.get("short_dex", "N/A").upper()
            size_usd = position.get("size_usd", Decimal("0"))
            is_closed = position.get("closed_at") is not None
            status_emoji = "🔒" if is_closed else "⏳"
            status_text = f"{status_emoji} CLOSED" if is_closed else f"{status_emoji} OPEN"
            
            # Get exchange emojis for title
            long_emoji = TelegramFormatter._get_exchange_emoji(long_dex)
            short_emoji = TelegramFormatter._get_exchange_emoji(short_dex)
            long_display = f"{long_emoji} {long_dex}" if long_emoji else long_dex
            short_display = f"{short_emoji} {short_dex}" if short_emoji else short_dex
            
            # Entry/exit trade counts
            entry_count = len(position.get("entry_trades", []))
            exit_count = len(position.get("exit_trades", []))
            
            # Title: symbol on one line, exchanges on next line
            lines.append(f"<u><b>{idx}. {symbol} {status_text}</b></u>")
            lines.append(f"  {long_display}/{short_display}")
            lines.append(f"  Size: <code>${size_usd:.2f}</code> • {entry_count} entry, {exit_count} exit")
            
            # Entry trades summary - long on one line, short on next indented
            entry_trades = position.get("entry_trades", [])
            if entry_trades:
                long_entry_price = position.get("long_entry_price", Decimal("0"))
                short_entry_price = position.get("short_entry_price", Decimal("0"))
                long_entry_value = position.get("long_entry_value", Decimal("0"))
                short_entry_value = position.get("short_entry_value", Decimal("0"))
                
                if long_entry_price > 0:
                    lines.append(f"  Entry: 📈 <b>{long_dex}</b> <code>${long_entry_price:.4f}</code> <i>(${long_entry_value:.2f})</i>")
                if short_entry_price > 0:
                    lines.append(f"          📉 <b>{short_dex}</b> <code>${short_entry_price:.4f}</code> <i>(${short_entry_value:.2f})</i>")
            
            # Exit trades summary - same format
            exit_trades = position.get("exit_trades", [])
            if exit_trades:
                long_exit_price = position.get("long_exit_price", Decimal("0"))
                short_exit_price = position.get("short_exit_price", Decimal("0"))
                long_exit_value = position.get("long_exit_value", Decimal("0"))
                short_exit_value = position.get("short_exit_value", Decimal("0"))
                
                if long_exit_price > 0:
                    lines.append(f"  Exit:  📈 <b>{long_dex}</b> <code>${long_exit_price:.4f}</code> <i>(${long_exit_value:.2f})</i>")
                if short_exit_price > 0:
                    lines.append(f"          📉 <b>{short_dex}</b> <code>${short_exit_price:.4f}</code> <i>(${short_exit_value:.2f})</i>")
            
            # Per-leg PnL breakdown - simplified
            long_leg_pnl = position.get("long_leg_pnl", Decimal("0"))
            short_leg_pnl = position.get("short_leg_pnl", Decimal("0"))
            
            if long_leg_pnl != 0 or short_leg_pnl != 0:
                long_sign = "+" if long_leg_pnl >= 0 else ""
                short_sign = "+" if short_leg_pnl >= 0 else ""
                lines.append(f"  Leg PnL: <b>{long_dex}</b> <code>{long_sign}${long_leg_pnl:.2f}</code> • <b>{short_dex}</b> <code>{short_sign}${short_leg_pnl:.2f}</code>")
            
            # Fees breakdown - simplified
            entry_fees = position.get("entry_fees", Decimal("0"))
            exit_fees = position.get("exit_fees", Decimal("0"))
            total_fees = position.get("total_fees", Decimal("0"))
            lines.append(f"  Fees: Entry <code>${entry_fees:.4f}</code> + Exit <code>${exit_fees:.4f}</code> = <code>${total_fees:.4f}</code>")
            
            # PnL breakdown - simplified
            price_pnl = position.get("price_pnl", Decimal("0"))
            total_funding = position.get("total_funding", Decimal("0"))
            net_pnl = position.get("net_pnl", Decimal("0"))
            
            price_sign = "+" if price_pnl >= 0 else ""
            funding_sign = "+" if total_funding >= 0 else ""
            lines.append(f"  Price PnL: <code>{price_sign}${price_pnl:.2f}</code>")
            lines.append(f"  Funding: <code>{funding_sign}${total_funding:.2f}</code>")
            
            # Net PnL - simplified
            net_sign = "+" if net_pnl >= 0 else ""
            pnl_pct = (net_pnl / size_usd * 100) if size_usd > 0 else Decimal("0")
            pct_sign = "+" if pnl_pct >= 0 else ""
            lines.append(f"  Net PnL: <code>{net_sign}${net_pnl:.2f}</code> <i>({pct_sign}{pnl_pct:.2f}%)</i>")
            
            # Position age - simplified
            opened_at = position.get("opened_at")
            if opened_at:
                if isinstance(opened_at, str):
                    opened_dt = datetime.fromisoformat(opened_at.replace("Z", "+00:00"))
                else:
                    opened_dt = opened_at
                
                if is_closed:
                    closed_at = position.get("closed_at")
                    if closed_at:
                        if isinstance(closed_at, str):
                            closed_dt = datetime.fromisoformat(closed_at.replace("Z", "+00:00"))
                        else:
                            closed_dt = closed_at
                        age = closed_dt - opened_dt.replace(tzinfo=None) if opened_dt.tzinfo else closed_dt - opened_dt
                        age_str = f"{age.total_seconds() / 3600:.1f}h"
                    else:
                        age_str = "N/A"
                else:
                    age = datetime.now(opened_dt.tzinfo) - opened_dt if opened_dt.tzinfo else datetime.now() - opened_dt
                    age_str = f"{age.total_seconds() / 3600:.1f}h"
                
                lines.append(f"  Age: <code>{age_str}</code>")
            
            lines.append("")
        
        if len(positions) > 3:
            lines.append(f"<i>Showing 3 of {len(positions)} positions</i>")
        
        return "\n".join(lines)
    
    @staticmethod
    def format_help() -> str:
        """Format help message."""
        return """🤖 <b>Strategy Control Bot</b>

<b>🔐 Authentication:</b>
/start - Start bot and show instructions
/auth - Authenticate with API key
/logout - Unlink Telegram account
/help - Show this help message

<b>📊 Monitoring (Existing Strategies):</b>
/positions [account] - List active positions (optional account filter)
/balances [account] - List available margin balances across exchanges (optional account filter)
/trades - View trading history and PnL
/close - Close a position

<b>💎 Opportunities:</b>
/opportunities - View top funding arbitrage opportunities across exchanges 

<b>👤 Account Management:</b>
/create_account - Create new account 
/list_accounts - List your accounts 
/add_exchange - Add exchange credentials 
/add_proxy - Add proxy to account 

<b>⚙️ Config Management:</b>
/create_config - Create strategy config 
/list_configs - List your configs 

<b>🚀 Strategy Execution:</b>
/run_strategy - Start new strategy
/list_strategies - List running strategies
/stop_strategy - Stop running strategy
/resume_strategy - Resume stopped strategy
/logs - View strategy logs 
/limits - Check usage limits and quotas
"""
    
    @staticmethod
    def format_error(message: str) -> str:
        """Format error message."""
        return f"❌ <b>Error</b>\n\n{message}"
    
    @staticmethod
    def format_not_authenticated() -> str:
        """Format not authenticated message."""
        return (
            "🔒 <b>Not Authenticated</b>\n\n"
            "Please authenticate first using:\n"
            "<code>/auth &lt;your_api_key&gt;</code>\n\n"
            "Get your API key @yipsinhang:\n"
        )
    
    @staticmethod
    def _format_hours(hours: float) -> str:
        """Format hours into readable string."""
        if hours < 1:
            minutes = int(hours * 60)
            return f"{minutes}m"
        elif hours < 24:
            h = int(hours)
            m = int((hours - h) * 60)
            if m > 0:
                return f"{h}h {m}m"
            return f"{h}h"
        else:
            days = int(hours / 24)
            h = int(hours % 24)
            if h > 0:
                return f"{days}d {h}h"
            return f"{days}d"
    
    @staticmethod
    def _split_message(message: str) -> List[str]:
        """Split long message into multiple messages."""
        parts = []
        lines = message.split('\n')
        current_part = []
        current_length = 0
        
        for line in lines:
            line_length = len(line) + 1  # +1 for newline
            if current_length + line_length > TelegramFormatter.MAX_MESSAGE_LENGTH:
                if current_part:
                    parts.append('\n'.join(current_part))
                current_part = [line]
                current_length = line_length
            else:
                current_part.append(line)
                current_length += line_length
        
        if current_part:
            parts.append('\n'.join(current_part))
        
        return parts
