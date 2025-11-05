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
                def _ellipsize_module_path(module: str, max_module_width: int) -> str:
                    """
                    Ellipsize module path while preserving root and final module names.
                    
                    Strategy:
                    1. Always keep the root module (first part) - full or truncated
                    2. Always keep the final module (last part) - full or truncated  
                    3. Show abbreviated middle parts (first few + last few chars of each)
                    4. Use "..." between abbreviated middle parts
                    5. Ensure total length <= max_module_width
                    
                    Examples:
                        funding_rate_service.core.submodule.opportunity_finder
                        -> funding_rate_service.core...opportunity_finder
                        
                        funding_rate_service.somepath.somechildpath.someotherpath.opportunity_finder
                        -> funding_rate_service.some...somechi...some..opportunity_finder
                    """
                    if len(module) <= max_module_width:
                        return module

                    parts = module.split(".")
                    if len(parts) <= 1:
                        # Single part or empty, just truncate if needed
                        return module[:max_module_width] if len(module) > max_module_width else module
                    
                    # Always keep first (root) and last (file) parts
                    root = parts[0]
                    final = parts[-1]
                    
                    if len(parts) == 2:
                        # Only root and final
                        min_needed = len(root) + 3 + len(final)
                        if min_needed <= max_module_width:
                            return f"{root}...{final}"
                        else:
                            # Need to truncate one or both
                            if len(final) <= max_module_width - len(root) - 3:
                                # Truncate root
                                available = max_module_width - 3 - len(final)
                                return f"{root[:available]}...{final}"
                            else:
                                # Truncate final (keep root as much as possible)
                                available = max_module_width - len(root) - 3
                                return f"{root}...{final[-available:]}" if available > 0 else f"{root[:max_module_width-3]}..."
                    
                    # Multiple parts - process middle parts
                    middle_parts = parts[1:-1]
                    
                    # Calculate space available for middle parts
                    # Reserve space for root, final, and separators
                    # Each middle part gets "..." separator (3 chars)
                    separators_needed = len(middle_parts) + 1  # +1 for root->middle, +N for middle->middle, +1 for middle->final
                    min_separator_space = separators_needed * 3
                    available_for_content = max_module_width - len(root) - len(final) - min_separator_space
                    
                    if available_for_content < 0:
                        # Even minimal separators don't fit, truncate root/final
                        if len(final) <= max_module_width - 3:
                            available = max_module_width - 3 - len(final)
                            return f"{root[:available]}...{final}"
                        else:
                            available = max_module_width - len(root) - 3
                            return f"{root}...{final[-available:]}" if available > 0 else f"{root[:max_module_width-3]}..."
                    
                    # Process middle parts - abbreviate each
                    # Use shorter separators: "..." after root, ".." between middles, "..." before final
                    abbreviated_middles = []
                    
                    # Allocate space more evenly - each middle part gets some chars
                    if middle_parts:
                        # Reserve separators: "..." (3) after root, ".." (2) between each middle, "..." (3) before final
                        separator_chars = 3 + (len(middle_parts) - 1) * 2 + 3
                        available_for_middles = max_module_width - len(root) - len(final) - separator_chars
                        chars_per_middle = max(3, available_for_middles // len(middle_parts)) if available_for_middles > 0 else 3
                    else:
                        chars_per_middle = 0
                    
                    for middle_part in middle_parts:
                        if len(middle_part) <= chars_per_middle:
                            abbreviated_middles.append(middle_part)
                        else:
                            # Show first few chars + ".." + last few chars for abbreviation
                            # Prefer showing more at the start (as in user's example)
                            start_chars = max(3, chars_per_middle - 2)  # Reserve 2 for ".."
                            end_chars = 0  # Don't show end chars in middle parts to save space
                            abbreviated = f"{middle_part[:start_chars]}.."
                            abbreviated_middles.append(abbreviated)
                    
                    # Build the full path with appropriate separators
                    result = root
                    if abbreviated_middles:
                        result += "..."  # Separator after root
                        for i, abbrev in enumerate(abbreviated_middles):
                            if i > 0:
                                result += ".."  # Shorter separator between middles
                            result += abbrev
                        result += "..."  # Separator before final
                    else:
                        result += "..."  # Just root and final
                    result += final
                    
                    # Final check - if still too long, truncate root or final
                    if len(result) > max_module_width:
                        excess = len(result) - max_module_width
                        # Try truncating root first
                        if len(root) > excess + 3:
                            root = root[:len(root) - excess]
                            result = root
                            for abbrev in abbreviated_middles:
                                result += f"...{abbrev}"
                            result += f"...{final}"
                        else:
                            # Truncate final instead
                            available = max_module_width - len(root) - (len(abbreviated_middles) + 1) * 3 - sum(len(a) for a in abbreviated_middles)
                            if available > 0:
                                result = root
                                for abbrev in abbreviated_middles:
                                    result += f"...{abbrev}"
                                result += f"...{final[-available:]}"
                            else:
                                # Last resort - minimal display
                                return f"{root[:10]}...{final[-20:]}" if len(final) > 20 else f"{root[:max_module_width-3-len(final)]}...{final}"
                    
                    return result

                def format_record(record):
                    """
                    Format log record with fixed-width origin column.
                    
                    Ensures all log messages align at the same column by:
                    1. Ellipsizing module path (preserving root and final module)
                    2. Always showing function:line
                    3. Right-aligning to fixed width
                    """
                    module_name = record.get("module") or record.get("name", "")
                    function_name = record.get("function", "")
                    line_number = record.get("line", 0)
                    
                    # Fixed width for entire origin column
                    max_width = 70
                    
                    # Build the immutable suffix: function:line (NEVER truncate this)
                    if function_name:
                        suffix = f":{function_name}:{line_number}"
                    else:
                        suffix = f":{line_number}"
                    
                    suffix_len = len(suffix)
                    
                    # Calculate available space for module path
                    available_for_module = max_width - suffix_len
                    
                    # Minimum space needed (root + "..." + final + suffix)
                    if available_for_module < 10:
                        # Very little space, use minimal display
                        module_display = "..."
                    else:
                        # Ellipsize module path intelligently
                        module_display = _ellipsize_module_path(module_name, available_for_module)
                    
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
        # Check if handler was removed (e.g., by funding_rate_service logger) and re-add if needed
        history_file = logs_dir / "unified_history.log"
        handler_exists = False
        
        # Check if our history handler still exists by looking for the file path in active handlers
        if hasattr(_logger, "_perp_dex_history_handler_id"):
            try:
                # Try to access the handler - if it doesn't exist, loguru will raise an error
                _logger._core.handlers[_logger._perp_dex_history_handler_id]
                handler_exists = True
            except (KeyError, AttributeError, TypeError):
                # Handler was removed, need to re-add
                handler_exists = False
        
        if not hasattr(_logger, "_perp_dex_history_setup") or not handler_exists:
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

            handler_id = _logger.add(
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
            _logger._perp_dex_history_handler_id = handler_id

        # Per-session log file
        # Check if handler was removed and re-add if needed
        session_handler_exists = False
        
        if hasattr(_logger, "_perp_dex_session_handler_id"):
            try:
                _logger._core.handlers[_logger._perp_dex_session_handler_id]
                session_handler_exists = True
            except (KeyError, AttributeError, TypeError):
                session_handler_exists = False
        
        if not hasattr(_logger, "_perp_dex_session_setup") or not session_handler_exists:
            session_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            session_file = logs_dir / f"session_{session_ts}.log"
            
            # Store session file path for later reference
            if not hasattr(_logger, "_perp_dex_session_file"):
                _logger._perp_dex_session_file = str(session_file)
            else:
                # Use existing session file if handler was removed but file still exists
                session_file = Path(_logger._perp_dex_session_file)

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

            handler_id = _logger.add(
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
            _logger._perp_dex_session_handler_id = handler_id
        
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
    
    def exception(self, message: str, **kwargs):
        """
        Log exception message with traceback.
        
        Equivalent to error(message, exc_info=True).
        This is a convenience method for logging exceptions.
        """
        self._logger.opt(depth=1).error(message, exc_info=True, **kwargs)
    
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
