"""
Notification Handler for Telegram Bot

Polls database for strategy notifications and sends them to Telegram users.
"""

import asyncio
import logging
from typing import Optional
from databases import Database
from telegram import Bot

logger = logging.getLogger(__name__)


class NotificationHandler:
    """Handles polling and sending strategy notifications to Telegram users."""
    
    def __init__(self, database: Database, bot: Bot):
        """
        Initialize notification handler.
        
        Args:
            database: Database connection
            bot: Telegram bot instance
        """
        self.database = database
        self.bot = bot
        self._task: Optional[asyncio.Task] = None
        self._running = False
    
    async def start(self):
        """Start the notification polling loop."""
        if self._running:
            logger.warning("Notification handler already running")
            return
        
        self._running = True
        self._task = asyncio.create_task(self._polling_loop())
        logger.info("Notification handler started")
    
    async def stop(self):
        """Stop the notification polling loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Notification handler stopped")
    
    async def _polling_loop(self):
        """Background task to poll for strategy notifications and send to Telegram users."""
        while self._running:
            try:
                await asyncio.sleep(5)  # Poll every 5 seconds
                
                # Get unsent notifications
                notifications = await self.database.fetch_all(
                    """
                    SELECT 
                        sn.id, sn.user_id, sn.message, sn.notification_type, sn.symbol,
                        u.telegram_user_id
                    FROM strategy_notifications sn
                    JOIN users u ON sn.user_id = u.id
                    WHERE sn.sent = FALSE
                    AND u.telegram_user_id IS NOT NULL
                    ORDER BY sn.created_at ASC
                    LIMIT 10
                    """
                )
                
                if not notifications:
                    continue
                
                # Send each notification
                for notif in notifications:
                    notification_id = notif["id"]
                    telegram_user_id = notif["telegram_user_id"]
                    message = notif["message"]
                    
                    try:
                        # Send message to Telegram user
                        await self.bot.send_message(
                            chat_id=telegram_user_id,
                            text=message,
                            parse_mode='HTML'
                        )
                        
                        # Mark as sent
                        await self.database.execute(
                            """
                            UPDATE strategy_notifications
                            SET sent = TRUE, sent_at = NOW()
                            WHERE id = :notification_id
                            """,
                            {"notification_id": notification_id}
                        )
                        
                        logger.debug(f"Sent notification {notification_id} to user {telegram_user_id}")
                        
                    except Exception as e:
                        # Log error but continue processing other notifications
                        logger.error(f"Failed to send notification {notification_id} to user {telegram_user_id}: {e}")
                        # Don't mark as sent if there was an error - will retry next poll
                        
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Notification polling error: {e}")

