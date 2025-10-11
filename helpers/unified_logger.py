"""
Unified logging system for perp-dex-tools

Provides consistent, colored, and informative logging across all components:
- Exchange clients
- Trading strategies  
- Funding rate service
- Core utilities

Based on loguru with enhanced formatting and component-specific context.
"""

import os
import sys
from typing import Optional, Dict, Any
from loguru import logger as _logger
from pathlib import Path


class UnifiedLogger:
    """
    Unified logger that provides consistent formatting across all components.
    
    Features:
    - Colored console output with source location (file:function:line)
    - Component-specific context (exchange, strategy, etc.)
    - File logging with rotation and compression
    - Backward compatibility with existing .log() method calls
    - Structured logging support
    """
    
    def __init__(
        self,
        component_type: str,  # "exchange", "strategy", "service", "core"
        component_name: str,  # "aster", "funding_arbitrage", "funding_rate_service", etc.
        context: Optional[Dict[str, Any]] = None,  # Additional context like ticker, account, etc.
        log_to_console: bool = True,
        log_level: str = "INFO"
    ):
        """
        Initialize unified logger.
        
        Args:
            component_type: Type of component (exchange, strategy, service, core)
            component_name: Name of specific component
            context: Additional context (ticker, account, etc.)
            log_to_console: Whether to log to console
            log_level: Minimum log level
        """
        self.component_type = component_type.upper()
        self.component_name = component_name.upper()
        self.context = context or {}
        self.log_level = log_level.upper()
        
        # Create component identifier for logs
        self.component_id = f"{self.component_type}:{self.component_name}"
        if self.context:
            context_str = ":".join([f"{k}={v}" for k, v in self.context.items()])
            self.component_id = f"{self.component_id}:{context_str}"
        
        # Setup logger instance
        self.logger_name = f"perp_dex_{component_type}_{component_name}"
        self._setup_logger(log_to_console)
    
    def _setup_logger(self, log_to_console: bool):
        """Setup loguru logger with unified formatting."""
        
        # Remove any existing handlers for this logger to avoid duplicates
        _logger.remove()
        
        # Create logs directory
        project_root = Path(__file__).parent.parent
        logs_dir = project_root / "logs"
        logs_dir.mkdir(exist_ok=True)
        
        # Console handler with colors and source location
        if log_to_console:
            def format_record(record):
                # Truncate the file path to last 2 segments
                name_parts = record["name"].split(".")
                if len(name_parts) >= 2:
                    short_name = f"{name_parts[-2]}.{name_parts[-1]}"
                else:
                    short_name = record["name"]
                
                # Pad the source location to a fixed width for alignment
                source_location = f"{short_name}:{record['function']}:{record['line']}"
                record["extra"]["short_name"] = f"{source_location:<60}"  # Increase to 60 char width for better alignment
                return True
                
            console_format = (
                "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
                "<level>{level: <8}</level> | "
                "<cyan>{extra[short_name]}</cyan> | "
                "<level>{message}</level>"
            )
            
            _logger.add(
                sys.stdout,
                format=console_format,
                level=self.log_level,
                colorize=True,
                filter=lambda record: record["extra"].get("component_id") == self.component_id and format_record(record),
                backtrace=True,
                diagnose=True
            )
        
        # File handler for all logs (no colors, includes extra context)
        log_file = logs_dir / f"{self.component_type.lower()}_{self.component_name.lower()}_activity.log"
        
        def format_record_file(record):
            # Truncate the file path to last 2 segments for file logs too
            name_parts = record["name"].split(".")
            if len(name_parts) >= 2:
                short_name = f"{name_parts[-2]}.{name_parts[-1]}"
            else:
                short_name = record["name"]
            
            # Pad the source location to a fixed width for alignment in files too
            source_location = f"{short_name}:{record['function']}:{record['line']}"
            record["extra"]["short_name"] = f"{source_location:<60}"  # Increase to 60 char width for better alignment
            return True
            
        file_format = (
            "{time:YYYY-MM-DD HH:mm:ss} | "
            "{level: <8} | "
            "{extra[short_name]} | "
            "{message}"
        )
        
        _logger.add(
            str(log_file),
            format=file_format,
            level=self.log_level,
            rotation="100 MB",
            retention="7 days",
            compression="zip",
            filter=lambda record: record["extra"].get("component_id") == self.component_id and format_record_file(record),
            backtrace=True,
            diagnose=True
        )
        
        # Error-specific file handler
        error_log_file = logs_dir / f"{self.component_type.lower()}_{self.component_name.lower()}_errors.log"
        
        def format_record_error(record):
            # Truncate the file path to last 2 segments for error logs too
            name_parts = record["name"].split(".")
            if len(name_parts) >= 2:
                short_name = f"{name_parts[-2]}.{name_parts[-1]}"
            else:
                short_name = record["name"]
            
            # Pad the source location to a fixed width for alignment in error logs too
            source_location = f"{short_name}:{record['function']}:{record['line']}"
            record["extra"]["short_name"] = f"{source_location:<60}"  # Increase to 60 char width for better alignment
            return True
            
        _logger.add(
            str(error_log_file),
            format=file_format,
            level="ERROR",
            rotation="10 MB",
            retention="30 days",
            compression="zip",
            filter=lambda record: record["extra"].get("component_id") == self.component_id and format_record_error(record),
            backtrace=True,
            diagnose=True
        )
        
        # Bind component context to all log records
        self._logger = _logger.bind(component_id=self.component_id)
    
    def debug(self, message: str, **kwargs):
        """Log debug message."""
        self._logger.opt(depth=1).debug(message, **kwargs)
    
    def info(self, message: str, **kwargs):
        """Log info message."""
        self._logger.opt(depth=1).info(message, **kwargs)
    
    def warning(self, message: str, **kwargs):
        """Log warning message."""
        self._logger.opt(depth=1).warning(message, **kwargs)
    
    def error(self, message: str, **kwargs):
        """Log error message."""
        self._logger.opt(depth=1).error(message, **kwargs)
    
    def critical(self, message: str, **kwargs):
        """Log critical message."""
        self._logger.opt(depth=1).critical(message, **kwargs)
    
    def log(self, message: str, level: str = "INFO", **kwargs):
        """
        Backward compatibility method for existing .log() calls.
        
        Args:
            message: Log message
            level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
            **kwargs: Additional context
        """
        level = level.upper()
        # Use depth=1 to skip this wrapper method and show the real caller
        if level == "DEBUG":
            self._logger.opt(depth=1).debug(message, **kwargs)
        elif level == "INFO":
            self._logger.opt(depth=1).info(message, **kwargs)
        elif level == "WARNING":
            self._logger.opt(depth=1).warning(message, **kwargs)
        elif level == "ERROR":
            self._logger.opt(depth=1).error(message, **kwargs)
        elif level == "CRITICAL":
            self._logger.opt(depth=1).critical(message, **kwargs)
        else:
            self._logger.opt(depth=1).info(message, **kwargs)
    
    def log_transaction(self, order_id: str, side: str, quantity: Any, price: Any, status: str):
        """
        Log trading transaction with structured data.
        
        Maintains compatibility with existing TradingLogger.log_transaction() calls.
        """
        transaction_data = {
            "order_id": order_id,
            "side": side,
            "quantity": str(quantity),
            "price": str(price),
            "status": status,
            "transaction": True  # Flag for filtering transaction logs
        }
        
        self._logger.opt(depth=1).info(
            f"TRANSACTION: {side.upper()} {quantity} @ {price} | Order: {order_id} | Status: {status}",
            **transaction_data
        )
    
    def with_context(self, **context) -> 'UnifiedLogger':
        """
        Create a new logger instance with additional context.
        
        Useful for adding temporary context like order_id, position_id, etc.
        """
        new_context = {**self.context, **context}
        return UnifiedLogger(
            component_type=self.component_type.lower(),
            component_name=self.component_name.lower(),
            context=new_context,
            log_to_console=True,  # Inherit from current setup
            log_level=self.log_level
        )


def get_logger(
    component_type: str,
    component_name: str,
    context: Optional[Dict[str, Any]] = None,
    log_to_console: bool = True,
    log_level: Optional[str] = None
) -> UnifiedLogger:
    """
    Factory function to create unified loggers.
    
    Args:
        component_type: Type of component (exchange, strategy, service, core)
        component_name: Name of specific component
        context: Additional context (ticker, account, etc.)
        log_to_console: Whether to log to console
        log_level: Log level (defaults to env LOG_LEVEL or INFO)
    
    Returns:
        UnifiedLogger instance
    
    Examples:
        # Exchange client logger
        logger = get_logger("exchange", "aster", {"ticker": "BTC"})
        
        # Strategy logger  
        logger = get_logger("strategy", "funding_arbitrage")
        
        # Service logger
        logger = get_logger("service", "funding_rate_service")
    """
    if log_level is None:
        log_level = os.getenv('LOG_LEVEL', 'INFO')
    
    return UnifiedLogger(
        component_type=component_type,
        component_name=component_name,
        context=context,
        log_to_console=log_to_console,
        log_level=log_level
    )


# Convenience functions for common component types
def get_exchange_logger(exchange_name: str, ticker: str = None, **context) -> UnifiedLogger:
    """Get logger for exchange clients."""
    ctx = {"ticker": ticker} if ticker else {}
    ctx.update(context)
    return get_logger("exchange", exchange_name, ctx)


def get_strategy_logger(strategy_name: str, **context) -> UnifiedLogger:
    """Get logger for trading strategies."""
    return get_logger("strategy", strategy_name, context)


def get_service_logger(service_name: str, **context) -> UnifiedLogger:
    """Get logger for services."""
    return get_logger("service", service_name, context)


def get_core_logger(module_name: str, **context) -> UnifiedLogger:
    """Get logger for core utilities."""
    return get_logger("core", module_name, context)
