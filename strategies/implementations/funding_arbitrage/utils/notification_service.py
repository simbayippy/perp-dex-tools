"""
Notification Service for Funding Arbitrage Strategy

Allows strategies to send notifications to Telegram users via database queue.
"""

import os
import json
import logging
from typing import Optional, Dict, Any
from decimal import Decimal
from datetime import datetime

logger = logging.getLogger(__name__)

# Try to import database connection
try:
    from database.connection import database
    DATABASE_AVAILABLE = True
except ImportError:
    DATABASE_AVAILABLE = False
    database = None


class StrategyNotificationService:
    """Service for sending strategy notifications to Telegram users."""
    
    def __init__(self, account_name: Optional[str] = None):
        """
        Initialize notification service.
        
        Args:
            account_name: Account name to identify the strategy run
        """
        self.account_name = account_name
        self._run_id_cache: Optional[str] = None
    
    async def _get_strategy_run_id(self) -> Optional[str]:
        """
        Get strategy run ID from database using account_name.
        
        Returns:
            Strategy run UUID as string, or None if not found
        """
        if not DATABASE_AVAILABLE or not database.is_connected:
            logger.warning("Database not available for notifications")
            return None
        
        if not self.account_name:
            logger.warning("Account name not provided, cannot identify strategy run")
            return None
        
        # Use cached run_id if available
        if self._run_id_cache:
            return self._run_id_cache
        
        try:
            # Find the most recent running strategy for this account
            query = """
                SELECT id, user_id
                FROM strategy_runs
                WHERE account_id IN (
                    SELECT id FROM accounts WHERE account_name = :account_name
                )
                AND status IN ('starting', 'running', 'paused')
                ORDER BY started_at DESC
                LIMIT 1
            """
            row = await database.fetch_one(query, {"account_name": self.account_name})
            
            if row:
                self._run_id_cache = str(row["id"])
                return self._run_id_cache
            else:
                logger.warning(f"No running strategy found for account: {self.account_name}")
                return None
        except Exception as e:
            logger.error(f"Error getting strategy run ID: {e}")
            return None
    
    async def notify_position_opened(
        self,
        symbol: str,
        long_dex: str,
        short_dex: str,
        size_usd: Decimal,
        entry_divergence: Decimal,
        long_price: Optional[Decimal] = None,
        short_price: Optional[Decimal] = None,
        long_exposure: Optional[Decimal] = None,
        short_exposure: Optional[Decimal] = None,
        long_quantity: Optional[Decimal] = None,
        short_quantity: Optional[Decimal] = None,
        normalized_leverage: Optional[int] = None,
        margin_used: Optional[Decimal] = None,
    ) -> bool:
        """
        Send notification when a position is opened.
        
        Args:
            symbol: Trading symbol (e.g., "BTC")
            long_dex: DEX name for long leg
            short_dex: DEX name for short leg
            size_usd: Delta-neutral exposure (minimum of both legs' exposures)
            entry_divergence: Entry funding rate divergence
            long_price: Optional entry price for long leg
            short_price: Optional entry price for short leg
            long_exposure: Optional exposure for long leg
            short_exposure: Optional exposure for short leg
            long_quantity: Optional quantity for long leg
            short_quantity: Optional quantity for short leg
            normalized_leverage: Optional normalized leverage used
            margin_used: Optional margin used (size / leverage)
            
        Returns:
            True if notification queued successfully, False otherwise
        """
        run_id = await self._get_strategy_run_id()
        if not run_id:
            return False
        
        try:
            # Get user_id from strategy_run
            if not DATABASE_AVAILABLE or not database.is_connected:
                return False
            
            run_row = await database.fetch_one(
                "SELECT user_id FROM strategy_runs WHERE id = :run_id",
                {"run_id": run_id}
            )
            if not run_row:
                return False
            
            user_id = str(run_row["user_id"])
            
            # Format message with improved clarity
            divergence_pct = entry_divergence * Decimal("100")
            
            # Build message with clearer information
            message = (
                f"✅ <b>Position Opened</b>\n\n"
                f"Symbol: <b>{symbol}</b>\n"
                f"Position Size: <b>${size_usd:.2f}</b>\n"
            )
            
            # Add margin and leverage info if available
            if margin_used is not None:
                message += f"Margin Used: <b>${margin_used:.2f}</b>"
                if normalized_leverage:
                    message += f" | Leverage: <b>{normalized_leverage}x</b>"
                message += "\n\n"
            elif normalized_leverage:
                message += f"Leverage: <b>{normalized_leverage}x</b>\n\n"
            else:
                message += "\n"
            
            # Long leg details
            message += f"<b>Long:</b> {long_dex.upper()}"
            if long_price:
                message += f" @ ${long_price:.6f}"
            if long_quantity:
                message += f"\n  Qty: {long_quantity:.4f}"
            if long_exposure:
                message += f" | Size: ${long_exposure:.2f}"
            
            message += "\n"
            
            # Short leg details
            message += f"<b>Short:</b> {short_dex.upper()}"
            if short_price:
                message += f" @ ${short_price:.6f}"
            if short_quantity:
                message += f"\n  Qty: {short_quantity:.4f}"
            if short_exposure:
                message += f" | Size: ${short_exposure:.2f}"
            
            message += f"\n\nDivergence: {divergence_pct:.4f}%"
            
            # Prepare details
            details: Dict[str, Any] = {
                "symbol": symbol,
                "long_dex": long_dex,
                "short_dex": short_dex,
                "size_usd": float(size_usd),
                "entry_divergence": float(entry_divergence),
            }
            if long_price:
                details["long_price"] = float(long_price)
            if short_price:
                details["short_price"] = float(short_price)
            if long_exposure:
                details["long_exposure"] = float(long_exposure)
            if short_exposure:
                details["short_exposure"] = float(short_exposure)
            if long_quantity:
                details["long_quantity"] = float(long_quantity)
            if short_quantity:
                details["short_quantity"] = float(short_quantity)
            if normalized_leverage:
                details["normalized_leverage"] = normalized_leverage
            if margin_used:
                details["margin_used"] = float(margin_used)
            
            # Insert notification
            await database.execute(
                """
                INSERT INTO strategy_notifications (
                    strategy_run_id, user_id, notification_type,
                    symbol, message, details
                )
                VALUES (
                    :run_id, :user_id, 'position_opened',
                    :symbol, :message, CAST(:details AS jsonb)
                )
                """,
                {
                    "run_id": run_id,
                    "user_id": user_id,
                    "symbol": symbol,
                    "message": message,
                    "details": json.dumps(details)
                }
            )
            
            return True
        except Exception as e:
            logger.error(f"Error sending position opened notification: {e}")
            return False
    
    async def notify_position_closed(
        self,
        symbol: str,
        reason: str,
        pnl_usd: Optional[Decimal] = None,
        pnl_pct: Optional[Decimal] = None,
        age_hours: Optional[float] = None,
        size_usd: Optional[Decimal] = None,
    ) -> bool:
        """
        Send notification when a position is closed.
        
        Args:
            symbol: Trading symbol (e.g., "BTC")
            reason: Reason for closing (e.g., "PROFIT_EROSION", "DIVERGENCE_FLIPPED", "TIME_LIMIT")
            pnl_usd: Optional PnL in USD
            pnl_pct: Optional PnL percentage
            age_hours: Optional position age in hours
            size_usd: Optional position size in USD
            
        Returns:
            True if notification queued successfully, False otherwise
        """
        run_id = await self._get_strategy_run_id()
        if not run_id:
            return False
        
        try:
            # Get user_id from strategy_run
            if not DATABASE_AVAILABLE or not database.is_connected:
                return False
            
            run_row = await database.fetch_one(
                "SELECT user_id FROM strategy_runs WHERE id = :run_id",
                {"run_id": run_id}
            )
            if not run_row:
                return False
            
            user_id = str(run_row["user_id"])
            
            # Format reason for display
            reason_display = reason.replace("_", " ").title()
            
            # Format message
            message = (
                f"🔒 <b>Position Closed</b>\n\n"
                f"Symbol: <b>{symbol}</b>\n"
                f"Reason: {reason_display}"
            )
            
            if size_usd:
                message += f"\nSize: ${size_usd:.2f}"
            
            if pnl_usd is not None:
                pnl_emoji = "💰" if pnl_usd >= 0 else "📉"
                message += f"\nPnL: {pnl_emoji} ${pnl_usd:.2f}"
                if pnl_pct is not None:
                    message += f" ({pnl_pct*100:+.2f}%)"
            
            if age_hours is not None:
                hours = int(age_hours)
                minutes = int((age_hours - hours) * 60)
                message += f"\nAge: {hours}h {minutes}m"
            
            # Prepare details
            details: Dict[str, Any] = {
                "symbol": symbol,
                "reason": reason,
            }
            if pnl_usd is not None:
                details["pnl_usd"] = float(pnl_usd)
            if pnl_pct is not None:
                details["pnl_pct"] = float(pnl_pct)
            if age_hours is not None:
                details["age_hours"] = age_hours
            if size_usd:
                details["size_usd"] = float(size_usd)
            
            # Insert notification
            await database.execute(
                """
                INSERT INTO strategy_notifications (
                    strategy_run_id, user_id, notification_type,
                    symbol, message, details
                )
                VALUES (
                    :run_id, :user_id, 'position_closed',
                    :symbol, :message, CAST(:details AS jsonb)
                )
                """,
                {
                    "run_id": run_id,
                    "user_id": user_id,
                    "symbol": symbol,
                    "message": message,
                    "details": json.dumps(details)
                }
            )
            
            return True
        except Exception as e:
            logger.error(f"Error sending position closed notification: {e}")
            return False

    async def notify_insufficient_margin(
        self,
        symbol: str,
        exchange_name: str,
        available_balance: Decimal,
        required_margin: Decimal,
        leverage_info: Optional[str] = None,
    ) -> bool:
        """
        Send notification when there's insufficient margin for a trade.
        
        Args:
            symbol: Trading symbol (e.g., "PROVE", "BTC")
            exchange_name: Exchange name with insufficient margin
            available_balance: Available balance on the exchange
            required_margin: Required margin amount
            leverage_info: Optional leverage information (e.g., "3x", "50x")
            
        Returns:
            True if notification queued successfully, False otherwise
        """
        run_id = await self._get_strategy_run_id()
        if not run_id:
            return False
        
        try:
            # Get user_id from strategy_run
            if not DATABASE_AVAILABLE or not database.is_connected:
                return False
            
            run_row = await database.fetch_one(
                "SELECT user_id FROM strategy_runs WHERE id = :run_id",
                {"run_id": run_id}
            )
            if not run_row:
                return False
            
            user_id = str(run_row["user_id"])
            
            # Format message
            message = (
                f"⚠️ <b>Insufficient Margin</b>\n\n"
                f"Symbol: <b>{symbol}</b>\n"
                f"Exchange: {exchange_name.upper()}\n"
                f"Available: ${available_balance:.2f}\n"
                f"Required: ${required_margin:.2f}"
            )
            
            if leverage_info:
                message += f"\nLeverage: {leverage_info}"
            
            message += f"\n\nPlease top up margin on {exchange_name.upper()}"
            
            # Prepare details
            details: Dict[str, Any] = {
                "symbol": symbol,
                "exchange_name": exchange_name,
                "available_balance": float(available_balance),
                "required_margin": float(required_margin),
            }
            if leverage_info:
                details["leverage_info"] = leverage_info
            
            # Insert notification
            await database.execute(
                """
                INSERT INTO strategy_notifications (
                    strategy_run_id, user_id, notification_type,
                    symbol, message, details
                )
                VALUES (
                    :run_id, :user_id, 'insufficient_margin',
                    :symbol, :message, CAST(:details AS jsonb)
                )
                """,
                {
                    "run_id": run_id,
                    "user_id": user_id,
                    "symbol": symbol,
                    "message": message,
                    "details": json.dumps(details)
                }
            )
            
            return True
        except Exception as e:
            logger.error(f"Error sending insufficient margin notification: {e}")
            return False

