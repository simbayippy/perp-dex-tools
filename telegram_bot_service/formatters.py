"""
Message formatters for Telegram bot responses
"""

from typing import Dict, Any, List
from decimal import Decimal
from datetime import datetime, timedelta


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
        """Format positions response in table format similar to position_monitor."""
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
        """Format a single position in detailed table format."""
        symbol = pos.get('symbol', 'N/A')
        
        lines = [
            f"Position <b>{symbol}</b> snapshot [Account: {account_name}]"
        ]
        
        # Yield summary (entry, current, erosion)
        entry_apy = pos.get('entry_divergence_apy')
        current_apy = pos.get('current_divergence_apy')
        erosion_ratio = pos.get('profit_erosion_ratio', 1.0)
        erosion_pct = pos.get('profit_erosion_pct', 0)
        min_erosion_threshold = pos.get('min_erosion_threshold')  # May not be in API yet
        
        if entry_apy is not None and current_apy is not None:
            # Format like position_monitor: "Yield (annualised) | entry X% | current Y% | erosion Z%"
            entry_display = f"{entry_apy:.4f}%"
            current_display = f"{current_apy:.4f}%"
            erosion_display = f"{erosion_pct:.2f}%"
            
            threshold_display = f"{min_erosion_threshold * 100:.2f}%" if min_erosion_threshold else "n/a"
            
            yield_line = (
                f"Yield (annualised) | entry {entry_display} | "
                f"current {current_display} | "
                f"erosion {erosion_display} (limit {threshold_display})"
            )
            lines.append(yield_line)
            # Add explanation
            lines.append(
                f"  <i>Erosion: Current APY ({current_apy:.2f}%) is {erosion_pct:.1f}% lower than Entry APY ({entry_apy:.2f}%)</i>"
            )
        
        # Min hold status
        min_hold_status = TelegramFormatter._format_min_hold_status(pos)
        if min_hold_status:
            lines.append(min_hold_status)
        
        # Max hold status
        max_hold_status = TelegramFormatter._format_max_hold_status(pos)
        if max_hold_status:
            lines.append(max_hold_status)
        
        # Table separator
        lines.append("─" * 95)
        
        # Table header (monospace for alignment)
        header = (
            f"<code>{'Exchange':<12} {'Side':<6} {'Qty':>11} "
            f"{'Entry':>12} {'Mark':>12} {'uPnL':>12} "
            f"{'Funding':>12} {'Funding APY':>12}</code>"
        )
        lines.append(header)
        lines.append("─" * 95)
        
        # Table rows (per leg)
        legs = pos.get('legs', [])
        for leg in legs:
            dex = leg.get('dex', 'N/A')
            side = leg.get('side', 'n/a')
            quantity = leg.get('quantity', 0)
            entry_price = leg.get('entry_price')
            mark_price = leg.get('mark_price')
            unrealized_pnl = leg.get('unrealized_pnl')
            funding_accrued = leg.get('funding_accrued', 0)
            funding_apy = leg.get('funding_apy')
            
            # Format values
            qty_str = TelegramFormatter._format_number(quantity, 4)
            entry_str = TelegramFormatter._format_number(entry_price, 6) if entry_price else "n/a"
            mark_str = TelegramFormatter._format_number(mark_price, 6) if mark_price else "n/a"
            pnl_str = TelegramFormatter._format_number(unrealized_pnl, 2) if unrealized_pnl is not None else "0.00"
            funding_str = TelegramFormatter._format_number(funding_accrued, 2)
            apy_str = TelegramFormatter._format_rate(funding_apy) if funding_apy is not None else "n/a"
            
            row = (
                f"<code>{dex:<12} {side:<6} {qty_str:>11} "
                f"{entry_str:>12} {mark_str:>12} {pnl_str:>12} "
                f"{funding_str:>12} {apy_str:>12}</code>"
            )
            lines.append(row)
        
        # Position ID for closing
        pos_id = pos.get('id', '')
        lines.append("")
        lines.append(f"Position ID: <code>{pos_id}</code>")
        
        message = "\n".join(lines)
        
        # Split if too long
        if len(message) > TelegramFormatter.MAX_MESSAGE_LENGTH:
            parts = TelegramFormatter._split_message(message)
            return parts[0] + "\n\n<i>(Message truncated - use API for full details)</i>"
        
        return message
    
    @staticmethod
    def _format_min_hold_status(pos: Dict[str, Any]) -> str:
        """Format min hold status (similar to position_monitor)."""
        min_hold_hours = pos.get('min_hold_hours')
        age_hours = pos.get('age_hours', 0)
        opened_at_str = pos.get('opened_at')
        
        if min_hold_hours is None or min_hold_hours <= 0:
            return "Min hold: disabled"
        
        if not opened_at_str:
            return "Min hold: n/a"
        
        # Parse opened_at
        try:
            opened_at = datetime.fromisoformat(opened_at_str.replace('Z', '+00:00'))
            opened_at = opened_at.replace(tzinfo=None)
        except Exception:
            return "Min hold: n/a"
        
        remaining = max(0.0, float(min_hold_hours) - age_hours)
        
        if remaining <= 0:
            ready_at = opened_at + timedelta(hours=min_hold_hours)
            ready_display = ready_at.strftime("%Y-%m-%d %H:%M:%S")
            return f"Min hold: satisfied (risk checks active since {ready_display})"
        
        # Format remaining time
        remaining_minutes = max(0, int(round(remaining * 60)))
        hours_left, minutes_left = divmod(remaining_minutes, 60)
        parts = []
        if hours_left:
            parts.append(f"{hours_left}h")
        if minutes_left or not parts:
            parts.append(f"{minutes_left}m")
        remaining_fmt = " ".join(parts)
        
        ready_at = opened_at + timedelta(hours=min_hold_hours)
        ready_display = ready_at.strftime("%Y-%m-%d %H:%M:%S")
        
        return f"Min hold: ACTIVE ({remaining_fmt} remaining, risk checks resume {ready_display})"
    
    @staticmethod
    def _format_max_hold_status(pos: Dict[str, Any]) -> str:
        """Format max hold status (similar to position_monitor)."""
        max_age_hours = pos.get('max_position_age_hours')
        age_hours = pos.get('age_hours', 0)
        opened_at_str = pos.get('opened_at')
        
        if max_age_hours is None or max_age_hours <= 0:
            return "Max hold: disabled"
        
        if not opened_at_str:
            return "Max hold: n/a"
        
        # Parse opened_at
        try:
            opened_at = datetime.fromisoformat(opened_at_str.replace('Z', '+00:00'))
            opened_at = opened_at.replace(tzinfo=None)
        except Exception:
            return "Max hold: n/a"
        
        remaining = max(0.0, float(max_age_hours) - age_hours)
        force_close_at = opened_at + timedelta(hours=max_age_hours)
        force_close_display = force_close_at.strftime("%Y-%m-%d %H:%M:%S")
        
        if remaining <= 0:
            return f"Max hold: EXCEEDED (force close was due at {force_close_display})"
        
        # Format remaining time
        remaining_minutes = max(0, int(round(remaining * 60)))
        hours_left, minutes_left = divmod(remaining_minutes, 60)
        parts = []
        if hours_left:
            parts.append(f"{hours_left}h")
        if minutes_left or not parts:
            parts.append(f"{minutes_left}m")
        remaining_fmt = " ".join(parts)
        
        return f"Max hold: {remaining_fmt} remaining (force close at {force_close_display}, configured: {max_age_hours}h)"
    
    @staticmethod
    def _format_number(value: float, precision: int = 2) -> str:
        """Format number with specified precision."""
        if value is None:
            return "n/a"
        return f"{value:,.{precision}f}"
    
    @staticmethod
    def _format_rate(rate: float) -> str:
        """Format funding rate as APY percentage."""
        if rate is None:
            return "n/a"
        return f"{rate:.4f}%"
    
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
    def format_help() -> str:
        """Format help message."""
        return """🤖 <b>Strategy Control Bot</b>

<b>Commands:</b>
/start - Start bot and show instructions
/auth &lt;api_key&gt; - Authenticate with API key
/status - Get strategy status
/positions [account] - List active positions (optional account filter)
/close &lt;position_id&gt; [market|limit] - Close a position (default: market)
/logout - Unlink Telegram account
/help - Show this help message

<b>Example:</b>
<code>/auth perp_8585a9b87b0ebd546c99347979101304</code>
<code>/positions</code>
<code>/close 4e4389de-060a-4aff-bdf2-dd214d3f5727 market</code>"""
    
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
            "Get your API key by running:\n"
            "<code>python database/scripts/create_api_key.py --username &lt;username&gt;</code>"
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

