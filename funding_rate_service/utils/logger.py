"""
Logging configuration for Funding Rate Service

Migrated to UnifiedLogger for consistent logging across the application.
Maintains backward compatibility with existing code.
"""

import logging
import os
from pathlib import Path

# Use UnifiedLogger for consistent logging
from helpers.unified_logger import get_service_logger

from funding_rate_service.config import settings


def _configure_external_loggers() -> None:
    """Limit noisy third-party loggers such as the async databases client."""
    db_level = getattr(logging, settings.database_log_level.upper(), logging.WARNING)
    databases_logger = logging.getLogger("databases")
    databases_logger.setLevel(db_level)
    if db_level >= logging.WARNING:
        databases_logger.propagate = False
    
    http_level = getattr(logging, settings.http_log_level.upper(), logging.WARNING)
    for name in ("urllib3", "urllib3.connectionpool", "pysdk.grvt_ccxt_base"):
        logger_obj = logging.getLogger(name)
        logger_obj.setLevel(http_level)
        if http_level >= logging.WARNING:
            logger_obj.propagate = False
    
    root_logger = logging.getLogger()
    root_logger.setLevel(http_level)
    if root_logger.handlers:
        for handler in root_logger.handlers:
            handler.setLevel(http_level)


# Configure external loggers (for third-party libraries)
_configure_external_loggers()

# Create UnifiedLogger instance for funding_rate_service
# This will use UnifiedLogger's console handler with fixed-width formatting
# and will write to unified_history.log and session logs
logger = get_service_logger("funding_rate_service", log_level=settings.log_level)

# Add service-specific file handlers for standalone mode (optional)
# These will coexist with UnifiedLogger's handlers
from loguru import logger as _loguru_logger

# Only add file handlers if running standalone (not imported by trading bot)
# Check if UnifiedLogger's console handler is already set up
if hasattr(_loguru_logger, '_perp_dex_console_setup'):
    # Running in trading bot context - UnifiedLogger handles everything
    pass
else:
    # Running standalone - add service-specific file handlers
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)
    
    # Add error.log handler (for ERROR level and above)
    _loguru_logger.add(
        str(logs_dir / "error.log"),
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[component_id]:<35} | {message}",
        level="ERROR",
        rotation="10 MB",
        retention="30 days",
        compression="zip",
        filter=lambda record: "component_id" in record["extra"],
        enqueue=True,
        catch=True
    )
    
    # Add app.log handler (for all levels)
    _loguru_logger.add(
        str(logs_dir / "app.log"),
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[component_id]:<35} | {message}",
        level=settings.log_level,
        rotation="100 MB",
        retention="7 days",
        compression="zip",
        filter=lambda record: "component_id" in record["extra"],
        enqueue=True,
        catch=True
    )


def clamp_external_logger_levels() -> None:
    """Reapply external logger configuration when third-party SDKs tweak logging."""
    _configure_external_loggers()
