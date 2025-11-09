#!/usr/bin/env python3
"""
Telegram Bot Service - Entry Point

Standalone script for running the Telegram bot service.
Can be run in a screen session on the VPS.

Usage:
    python telegram_bot_service/main.py
"""

import asyncio
import logging
import sys
import signal
from typing import Optional
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from telegram_bot_service.config import TelegramBotConfig
from telegram_bot_service.bot import StrategyControlBot

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Global bot instance for signal handling
bot_instance: Optional[StrategyControlBot] = None
_shutdown_event: Optional[asyncio.Event] = None


def signal_handler(signum, frame):
    """Handle shutdown signals."""
    logger.info(f"Received signal {signum}, initiating shutdown...")
    if _shutdown_event:
        _shutdown_event.set()


async def main():
    """Main entry point."""
    global bot_instance, _shutdown_event
    
    # Create shutdown event in the event loop
    _shutdown_event = asyncio.Event()
    
    try:
        # Load configuration
        config = TelegramBotConfig()
        if not config.validate():
            logger.error("Invalid configuration")
            sys.exit(1)
        
        logger.info("="*70)
        logger.info("Telegram Bot Service Starting")
        logger.info("="*70)
        logger.info(f"Control API: {config.control_api_base_url}")
        logger.info(f"Database: {config.database_url.split('@')[-1] if '@' in config.database_url else 'configured'}")
        logger.info("="*70)
        
        # Create bot instance
        bot_instance = StrategyControlBot(config)
        
        # Setup signal handlers
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        # Start bot
        await bot_instance.start()
        
        # Keep running until stopped
        logger.info("Bot is running. Press Ctrl+C to stop.")
        try:
            # Wait for shutdown event instead of infinite loop
            await _shutdown_event.wait()
            logger.info("Shutdown signal received")
        except KeyboardInterrupt:
            logger.info("Received keyboard interrupt")
            
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
    finally:
        if bot_instance:
            try:
                await bot_instance.stop()
            except Exception as e:
                logger.error(f"Error during shutdown: {e}")
        logger.info("Telegram bot service stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutdown complete")

