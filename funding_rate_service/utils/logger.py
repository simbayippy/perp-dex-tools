"""
Logging configuration using loguru
"""

import sys
from loguru import logger as _logger
from funding_rate_service.config import settings


# Remove default handler
_logger.remove()

# Add console handler with format
_logger.add(
    sys.stdout,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
    level=settings.log_level,
    colorize=True
)

# Add file handler for errors
_logger.add(
    "logs/error.log",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
    level="ERROR",
    rotation="10 MB",
    retention="30 days",
    compression="zip"
)

# Add file handler for all logs
_logger.add(
    "logs/app.log",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
    level=settings.log_level,
    rotation="100 MB",
    retention="7 days",
    compression="zip"
)

# Export logger
logger = _logger

