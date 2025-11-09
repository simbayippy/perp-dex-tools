"""
Message formatters for Telegram bot responses
"""

from typing import Dict, Any, List
from decimal import Decimal


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
        """Format positions response."""
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
            
            lines = [
                f"📊 <b>{account_name}</b>",
                f"Positions: {len(positions)}",
                ""
            ]
            
            for pos in positions:
                symbol = pos.get('symbol', 'N/A')
                long_dex = pos.get('long_dex', 'N/A').upper()
                short_dex = pos.get('short_dex', 'N/A').upper()
                size_usd = pos.get('size_usd', 0)
                age_hours = pos.get('age_hours', 0)
                
                # Format age
                age_str = TelegramFormatter._format_hours(age_hours)
                
                # PnL
                net_pnl = pos.get('net_pnl_usd', 0)
                net_pnl_pct = pos.get('net_pnl_pct', 0) * 100
                pnl_icon = "🟢" if net_pnl > 0 else "🔴" if net_pnl < 0 else "⚪"
                
                # Yield
                entry_apy = pos.get('entry_divergence_apy')
                current_apy = pos.get('current_divergence_apy')
                erosion_pct = pos.get('profit_erosion_pct', 0)
                
                # Per-leg PnL
                long_pnl = pos.get('long_unrealized_pnl')
                short_pnl = pos.get('short_unrealized_pnl')
                
                lines.append(f"<b>{symbol}</b> ({long_dex}/{short_dex})")
                lines.append(f"  Size: ${size_usd:,.2f} | Age: {age_str}")
                lines.append(f"  PnL: {pnl_icon} ${net_pnl:+.2f} ({net_pnl_pct:+.2f}%)")
                
                if long_pnl is not None and short_pnl is not None:
                    lines.append(f"  Legs: Long ${long_pnl:+.2f} | Short ${short_pnl:+.2f}")
                
                if entry_apy is not None:
                    lines.append(f"  Entry APY: {entry_apy:.2f}%")
                if current_apy is not None:
                    lines.append(f"  Current APY: {current_apy:.2f}%")
                if erosion_pct > 0:
                    lines.append(f"  Erosion: {erosion_pct:.1f}%")
                
                # Position ID for closing
                pos_id = pos.get('id', '')
                lines.append(f"  ID: <code>{pos_id[:8]}...</code>")
                lines.append("")
            
            message = "\n".join(lines)
            if len(message) > TelegramFormatter.MAX_MESSAGE_LENGTH:
                # Split into multiple messages
                messages.extend(TelegramFormatter._split_message(message))
            else:
                messages.append(message)
        
        return "\n\n".join(messages)
    
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

