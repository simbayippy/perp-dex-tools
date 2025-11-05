"""
Market switching management for Backpack WebSocket.

Handles symbol switching via disconnect/reconnect cycle.
"""

from typing import Any, Callable, Dict, Optional


class BackpackMarketSwitcher:
    """Manages market switching via disconnect/reconnect."""

    def __init__(
        self,
        symbol: Optional[str],
        symbol_formatter: Optional[Callable[[str], str]] = None,
        update_symbol_fn: Optional[Callable[[Optional[str]], None]] = None,
        update_market_config_fn: Optional[Callable[[str], None]] = None,
        logger: Optional[Any] = None,
    ):
        """
        Initialize market switcher.
        
        Args:
            symbol: Current symbol
            symbol_formatter: Function to format symbols
            update_symbol_fn: Function to update symbol
            update_market_config_fn: Function to update market config
            logger: Logger instance
        """
        self.symbol = symbol
        self.symbol_formatter = symbol_formatter
        self.update_symbol = update_symbol_fn
        self.update_market_config = update_market_config_fn
        self.logger = logger

    def set_logger(self, logger):
        """Set the logger instance."""
        self.logger = logger

    def _log(self, message: str, level: str = "INFO"):
        """Log message using the logger if available."""
        if self.logger:
            if hasattr(self.logger, 'log'):
                self.logger.log(message, level)
            elif level == "ERROR" and hasattr(self.logger, 'error'):
                self.logger.error(message)
            elif level == "WARNING" and hasattr(self.logger, 'warning'):
                self.logger.warning(message)
            elif level == "DEBUG" and hasattr(self.logger, 'debug'):
                self.logger.debug(message)
            elif hasattr(self.logger, 'info'):
                self.logger.info(message)

    def format_symbol(self, symbol: str) -> str:
        """
        Format symbol for Backpack streams.
        
        Args:
            symbol: Normalized symbol
            
        Returns:
            Backpack-formatted symbol
        """
        if self.symbol_formatter:
            try:
                return self.symbol_formatter(symbol)
            except Exception:
                return symbol
        return symbol

    def should_switch_symbol(self, target_symbol: str) -> bool:
        """
        Check if symbol switch is needed.
        
        Args:
            target_symbol: Target symbol to switch to
            
        Returns:
            True if switch needed, False if already on target
        """
        if target_symbol == self.symbol:
            return False
        return True

    def perform_symbol_switch(self, new_symbol: str) -> None:
        """
        Execute symbol switch (clears state, updates symbol).
        
        Note: Actual reconnection is handled by manager via disconnect/reconnect.
        
        Args:
            new_symbol: New symbol to switch to
        """
        if self.logger:
            self.logger.info(f"[BACKPACK] 🔄 Switching websocket streams to {new_symbol}")

        # Update symbol
        if self.update_symbol:
            self.update_symbol(new_symbol)
        else:
            self.symbol = new_symbol
        
        # Update config to keep it synchronized
        if self.update_market_config:
            self.update_market_config(new_symbol)

