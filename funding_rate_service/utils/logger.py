"""
Logging configuration using loguru
"""

import logging
import sys

from loguru import logger as _logger

from funding_rate_service.config import settings


def _configure_loguru() -> None:
    """Configure loguru sinks."""
    _logger.remove()
    _logger.add(
        sys.stdout,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        level=settings.log_level,
        colorize=True,
    )
    _logger.add(
        "logs/error.log",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
        level="ERROR",
        rotation="10 MB",
        retention="30 days",
        compression="zip",
    )
    _logger.add(
        "logs/app.log",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
        level=settings.log_level,
        rotation="100 MB",
        retention="7 days",
        compression="zip",
    )


def _configure_external_loggers() -> None:
    """Limit noisy third-party loggers such as the async databases client."""
    db_level = getattr(logging, settings.database_log_level.upper(), logging.WARNING)
    databases_logger = logging.getLogger("databases")
    databases_logger.setLevel(db_level)
    if db_level >= logging.WARNING:
        databases_logger.propagate = False


_configure_loguru()
_configure_external_loggers()

# Export logger
logger = _logger
