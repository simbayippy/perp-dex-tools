"""Cooldown management for symbols with wide spreads or validation failures."""

import time
from typing import Dict


class CooldownManager:
    """Manages cooldown periods for symbols to avoid repeated failures."""
    
    def __init__(self):
        """Initialize cooldown manager with empty cooldown dictionary."""
        self._cooldowns: Dict[str, float] = {}  # symbol -> timestamp
    
    def is_in_cooldown(self, symbol: str, cooldown_minutes: int) -> bool:
        """
        Check if a symbol is currently in cooldown.
        
        Args:
            symbol: Symbol to check
            cooldown_minutes: Cooldown duration in minutes
            
        Returns:
            True if symbol is in cooldown, False otherwise
        """
        if symbol not in self._cooldowns:
            return False
        
        timestamp = self._cooldowns[symbol]
        cooldown_seconds = cooldown_minutes * 60
        elapsed = time.time() - timestamp
        
        if elapsed >= cooldown_seconds:
            # Cooldown expired, remove it
            del self._cooldowns[symbol]
            return False
        
        return True
    
    def mark_cooldown(self, symbol: str) -> None:
        """
        Mark a symbol as being in cooldown.
        
        Args:
            symbol: Symbol to mark
        """
        self._cooldowns[symbol] = time.time()
    
    def cleanup_expired(self, cooldown_minutes: int) -> None:
        """
        Remove expired cooldown entries (optional optimization).
        
        Args:
            cooldown_minutes: Cooldown duration in minutes
        """
        current_time = time.time()
        cooldown_seconds = cooldown_minutes * 60
        expired_symbols = [
            symbol
            for symbol, timestamp in self._cooldowns.items()
            if (current_time - timestamp) >= cooldown_seconds
        ]
        
        for symbol in expired_symbols:
            del self._cooldowns[symbol]
    
    def clear_cooldown(self, symbol: str) -> None:
        """
        Manually clear cooldown for a symbol (for testing/debugging).
        
        Args:
            symbol: Symbol to clear
        """
        if symbol in self._cooldowns:
            del self._cooldowns[symbol]
    
    def get_cooldown_status(self, symbol: str, cooldown_minutes: int) -> Dict[str, any]:
        """
        Get cooldown status for a symbol (for debugging/logging).
        
        Args:
            symbol: Symbol to check
            cooldown_minutes: Cooldown duration in minutes
            
        Returns:
            Dictionary with cooldown status information
        """
        if symbol not in self._cooldowns:
            return {
                "in_cooldown": False,
                "remaining_seconds": 0,
            }
        
        timestamp = self._cooldowns[symbol]
        cooldown_seconds = cooldown_minutes * 60
        elapsed = time.time() - timestamp
        remaining = max(0, cooldown_seconds - elapsed)
        
        return {
            "in_cooldown": remaining > 0,
            "remaining_seconds": remaining,
            "elapsed_seconds": elapsed,
        }

