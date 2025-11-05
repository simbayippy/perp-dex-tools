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
from datetime import datetime
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
        
        # 🔧 FIX: Use a global flag to ensure console handler is only set up once
        if not hasattr(_logger, '_perp_dex_console_setup'):
            # First time setup - remove default handler and set up shared console handler
            _logger.remove()
            
            # Create logs directory
            project_root = Path(__file__).parent.parent
            logs_dir = project_root / "logs"
            logs_dir.mkdir(exist_ok=True)
            
            # Console handler with colors and source location (SHARED by all components)
            if log_to_console:
                def _truncate_module_path(module: str, max_width: int) -> str:
                    if len(module) <= max_width:
                        return module

                    parts = module.split(".")
                    # Always keep at least the final segment
                    kept = parts[-1]
                    idx = len(parts) - 2
                    while idx >= 0:
                        candidate = ".".join(parts[idx:])
                        if len(candidate) + 3 <= max_width:  # account for ellipsis
                            return f"...{candidate}"
                        idx -= 1

                    # Fall back to cropping the final segment if everything else fails
                    return f"...{kept[-(max_width-3):]}" if len(kept) + 3 > max_width else f"...{kept}"

                def format_record(record):
                    module_name = record.get("module") or record.get("name", "")
                    function_name = record.get("function", "")
                    line_number = record.get("line", 0)
                    
                    max_width = 55
                    
                    # Build the immutable suffix: function:line (NEVER truncate this)
                    if function_name:
                        suffix = f":{function_name}:{line_number}"
                    else:
                        suffix = f":{line_number}"
                    
                    suffix_len = len(suffix)
                    
                    # Calculate available space for module path
                    available_for_module = max_width - suffix_len
                    
                    # Truncate module path to fit (function:line is sacred)
                    if available_for_module <= 3:
                        # Very long function name - use minimal module indicator
                        module_display = "..."
                    else:
                        # Truncate module path intelligently to fit available space
                        module_display = _truncate_module_path(module_name, available_for_module)
                    
                    # Build final source location: module + function:line
                    source_location = f"{module_display}{suffix}"
                    
                    # Right-align to EXACTLY max_width characters
                    # This ensures ALL log messages start at the same column
                    record["extra"]["short_name"] = f"{source_location:>{max_width}}"
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
                    filter=lambda record: record["extra"].get("component_id") and format_record(record),
                    backtrace=True,
                    diagnose=True
                )
            
            # Mark console handler as set up
            _logger._perp_dex_console_setup = True
        
        # Create logs directory (in case it wasn't created above)
        project_root = Path(__file__).parent.parent
        logs_dir = project_root / "logs"
        logs_dir.mkdir(exist_ok=True)

        # Global history file (shared across all components)
        if not hasattr(_logger, "_perp_dex_history_setup"):
            history_file = logs_dir / "unified_history.log"

            def ensure_component(record):
                if "component_id" not in record["extra"]:
                    record["extra"]["component_id"] = "UNKNOWN"
                return True

            history_format = (
                "{time:YYYY-MM-DD HH:mm:ss} | "
                "{level:<8} | "
                "{extra[component_id]:<35} | "
                "{message}"
            )

            _logger.add(
                str(history_file),
                format=history_format,
                level="DEBUG",
                filter=ensure_component,
                backtrace=False,
                diagnose=False,
                enqueue=True,  # Thread-safe async writes (handles buffering internally)
                catch=True     # Catch handler errors to prevent silent failures
            )
            _logger._perp_dex_history_setup = True

        # Per-session log file
        if not hasattr(_logger, "_perp_dex_session_setup"):
            session_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            session_file = logs_dir / f"session_{session_ts}.log"

            def ensure_component_session(record):
                if "component_id" not in record["extra"]:
                    record["extra"]["component_id"] = "UNKNOWN"
                return True

            session_format = (
                "{time:YYYY-MM-DD HH:mm:ss} | "
                "{level:<8} | "
                "{extra[component_id]:<35} | "
                "{message}"
            )

            _logger.add(
                str(session_file),
                format=session_format,
                level="DEBUG",
                filter=ensure_component_session,
                backtrace=False,
                diagnose=False,
                enqueue=True,  # Thread-safe async writes (handles buffering internally)
                catch=True     # Catch handler errors to prevent silent failures
            )
            _logger._perp_dex_session_setup = True
        
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
    
    def flush(self):
        """
        Explicitly flush all log handlers to disk.
        
        This is critical for ensuring buffered/enqueued logs are written
        before the process exits, especially in async contexts.
        """
        try:
            # Force a log message through to trigger queue processing
            self._logger.opt(depth=1).debug("LOG_FLUSH_MARKER")
            
            # Small delay to allow enqueued messages to be processed
            import time
            time.sleep(0.05)  # 50ms should be enough for queue flush
            
            # Flush stdout/stderr
            import sys
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:
            # Silent failure - don't break on flush errors
            pass
    
    @staticmethod
    def flush_all_handlers():
        """
        Global flush of all loguru handlers.
        
        Call this before process exit to ensure all buffered/enqueued logs are written.
        
        When using enqueue=True, loguru uses background threads/processes to write logs.
        This method sends a flush marker and waits for the enqueue queue to drain.
        """
        try:
            import sys
            import time
            
            # Send a flush marker through the logger to trigger queue processing
            # This ensures any pending logs in the enqueue queue are processed
            _logger.opt(depth=2).debug("LOG_FLUSH_MARKER")
            
            # Wait for enqueued logs to be processed
            # With enqueue=True, loguru uses background threads, so we need to wait
            # for the queue to drain. 200ms should be sufficient for most cases.
            time.sleep(0.2)
            
            # Flush system streams (for console output)
            sys.stdout.flush()
            sys.stderr.flush()
            
            # Additional wait to ensure file writes complete
            # File I/O operations may take longer, especially on slower systems
            time.sleep(0.1)
        except Exception:
            # Silent failure - don't break shutdown if flush fails
            pass


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


def log_stage(
    logger_obj: Any,
    title: str,
    *,
    icon: Optional[str] = None,
    stage_id: Optional[str] = None,
    border: str = "=",
    width: int = 55,
    level: str = "INFO"
) -> None:
    """
    Log a formatted stage separator to highlight execution phases.
    
    Works with both UnifiedLogger instances and raw loguru logger objects.
    
    Args:
        logger_obj: Logger to emit messages on.
        title: Stage title to display.
        icon: Optional emoji/icon prefix.
        stage_id: Optional hierarchical identifier (e.g., "1", "2.1").
        border: Character used for separator line.
        width: Width of separator line.
        level: Log level to use (INFO by default).
    """
    def _emit(message: str) -> None:
        normalized_level = level.upper()
        lower_level = normalized_level.lower()
        
        if hasattr(logger_obj, "log"):
            logger_obj.log(message, level=normalized_level)
        elif hasattr(logger_obj, lower_level):
            getattr(logger_obj, lower_level)(message)
        else:
            # Fallback to loguru/global style
            try:
                logger_obj.log(normalized_level, message)
            except AttributeError:
                # Last resort printing
                logger_obj.info(message)
    
    border_line = border * width
    label_parts = []
    if stage_id:
        label_parts.append(f"{stage_id}.")
    if icon:
        label_parts.append(icon)
    label_parts.append(title)
    label = " ".join(label_parts)
    
    _emit(border_line)
    _emit(label)
    _emit(border_line)
