"""
PATTERN 1: Event-Driven Control Loop
=====================================

Extracted from: Hummingbot ExecutorBase
Source: docs/hummingbot_reference/position_executor/NOTES.md

Purpose:
--------
This pattern provides a robust event-driven execution framework with:
- Status lifecycle management (NOT_STARTED → RUNNING → SHUTTING_DOWN → TERMINATED)
- Event listener registration/unregistration
- Async control loop that runs continuously

Why This Pattern?
-----------------
✅ More efficient than pure polling
✅ Clean lifecycle management
✅ Easy to test with event mocking
✅ Prevents resource leaks (auto cleanup)

Key Concepts:
-------------
1. RunnableStatus enum tracks executor state
2. Event listeners registered on start, unregistered on stop
3. Control loop runs while status == RUNNING
4. Graceful shutdown with SHUTTING_DOWN → TERMINATED transition

"""

from enum import Enum
from abc import ABC, abstractmethod
import asyncio
from typing import Optional, Dict, Callable


# ============================================================================
# CORE PATTERN: Status Enum
# ============================================================================

class RunnableStatus(Enum):
    """
    Executor lifecycle states.
    
    Transitions:
    NOT_STARTED → RUNNING → SHUTTING_DOWN → TERMINATED
    """
    NOT_STARTED = 1
    RUNNING = 2
    SHUTTING_DOWN = 3
    TERMINATED = 4


# ============================================================================
# CORE PATTERN: Event-Driven Base Class
# ============================================================================

class ExecutorBase(ABC):
    """
    Base class for any trading executor/strategy.
    
    Pattern from Hummingbot ExecutorBase:
    - Manages event listeners
    - Provides control loop
    - Handles lifecycle
    
    Usage in your code:
    -------------------
    class MyStrategy(ExecutorBase):
        def __init__(self, ...):
            super().__init__()
            self.update_interval = 1.0  # 1 second
        
        async def control_logic(self):
            # Your strategy logic here
            await self._monitor_positions()
            await self._check_opportunities()
        
        def register_events(self):
            # Register order fill listeners, etc.
            self.add_listener('order_filled', self._on_order_filled)
    """
    
    def __init__(self):
        self.status = RunnableStatus.NOT_STARTED
        self._event_listeners: Dict[str, Callable] = {}
        self._control_task: Optional[asyncio.Task] = None
        self.update_interval = 1.0  # Default: check every 1 second
    
    # ========================================================================
    # Lifecycle Management
    # ========================================================================
    
    def start(self):
        """
        Start the executor.
        
        Pattern:
        1. Register event listeners
        2. Set status to RUNNING
        3. Start control loop task
        """
        if self.status != RunnableStatus.NOT_STARTED:
            raise RuntimeError(f"Cannot start executor in status {self.status}")
        
        # Register event listeners (child implements this)
        self.register_events()
        
        # Start running
        self.status = RunnableStatus.RUNNING
        
        # Start async control loop
        self._control_task = asyncio.create_task(self.control_task())
        
        print(f"[{self.__class__.__name__}] Started")
    
    def stop(self):
        """
        Stop the executor gracefully.
        
        Pattern:
        1. Set status to SHUTTING_DOWN
        2. Wait for control loop to finish
        3. Unregister event listeners
        4. Set status to TERMINATED
        """
        if self.status == RunnableStatus.RUNNING:
            self.status = RunnableStatus.SHUTTING_DOWN
            print(f"[{self.__class__.__name__}] Shutting down...")
    
    async def wait_till_terminated(self):
        """Wait for executor to fully terminate"""
        if self._control_task:
            await self._control_task
        
        # Cleanup
        self.unregister_events()
        self.status = RunnableStatus.TERMINATED
        print(f"[{self.__class__.__name__}] Terminated")
    
    # ========================================================================
    # Event System
    # ========================================================================
    
    def register_events(self):
        """
        Register event listeners.
        
        Override this in child class to add listeners:
        
        def register_events(self):
            self.add_listener('order_filled', self._on_order_filled)
            self.add_listener('funding_payment', self._on_funding_payment)
        """
        pass
    
    def unregister_events(self):
        """Cleanup all event listeners"""
        self._event_listeners.clear()
    
    def add_listener(self, event_name: str, callback: Callable):
        """Add an event listener"""
        self._event_listeners[event_name] = callback
    
    def remove_listener(self, event_name: str):
        """Remove an event listener"""
        if event_name in self._event_listeners:
            del self._event_listeners[event_name]
    
    # ========================================================================
    # Control Loop (Async)
    # ========================================================================
    
    async def control_task(self):
        """
        Main control loop - runs continuously while RUNNING.
        
        Pattern from Hummingbot:
        - Check status at start of each iteration
        - Execute control logic
        - Sleep for update_interval
        - Exit gracefully when SHUTTING_DOWN
        """
        while self.status == RunnableStatus.RUNNING:
            try:
                # Execute strategy logic (child implements this)
                await self.control_logic()
                
            except Exception as e:
                print(f"[{self.__class__.__name__}] Error in control loop: {e}")
                # Don't crash - log and continue
            
            # Wait before next iteration
            await asyncio.sleep(self.update_interval)
        
        print(f"[{self.__class__.__name__}] Control loop exited")
    
    @abstractmethod
    async def control_logic(self):
        """
        Child implements the actual strategy logic here.
        
        This is called every `update_interval` seconds.
        
        Example:
        --------
        async def control_logic(self):
            # Monitor positions
            await self._check_positions()
            
            # Check for opportunities
            await self._scan_markets()
            
            # Execute trades if needed
            await self._execute_signals()
        """
        raise NotImplementedError


# ============================================================================
# USAGE EXAMPLE
# ============================================================================

class ExampleStrategy(ExecutorBase):
    """Example showing how to use the pattern"""
    
    def __init__(self):
        super().__init__()
        self.update_interval = 5.0  # Check every 5 seconds
        self.position_count = 0
    
    async def control_logic(self):
        """Our strategy logic"""
        print(f"[Strategy] Checking markets... (positions: {self.position_count})")
        
        # Simulate some work
        await asyncio.sleep(0.1)
        
        # Example: Stop after 10 iterations
        self.position_count += 1
        if self.position_count >= 10:
            self.stop()
    
    def register_events(self):
        """Register our event listeners"""
        print("[Strategy] Registering event listeners...")
        self.add_listener('order_filled', self._on_order_filled)
    
    def _on_order_filled(self, event):
        """Handle order fill event"""
        print(f"[Strategy] Order filled: {event}")


# ============================================================================
# HOW TO USE IN YOUR CODE
# ============================================================================

"""
Integration with your base_strategy.py:
---------------------------------------

from enum import Enum

class RunnableStatus(Enum):
    NOT_STARTED = 1
    RUNNING = 2
    SHUTTING_DOWN = 3
    TERMINATED = 4

class BaseStrategy(ABC):
    def __init__(self, config, exchange_client=None):
        self.config = config
        self.exchange_client = exchange_client
        self.status = RunnableStatus.NOT_STARTED  # ADD THIS
        self._event_listeners = {}  # ADD THIS
    
    def start(self):
        '''Start the strategy'''
        if self.status == RunnableStatus.NOT_STARTED:
            self.register_events()
            self.status = RunnableStatus.RUNNING
    
    def stop(self):
        '''Stop the strategy'''
        self.status = RunnableStatus.SHUTTING_DOWN
        self.unregister_events()
        self.status = RunnableStatus.TERMINATED
    
    def register_events(self):
        '''Override to add event listeners'''
        pass
    
    # ... rest of your BaseStrategy
"""

# ============================================================================
# KEY TAKEAWAYS
# ============================================================================

"""
1. ✅ Status enum provides clear lifecycle states
2. ✅ Event registration/unregistration prevents memory leaks
3. ✅ Control loop pattern is more efficient than manual polling
4. ✅ Async-friendly for modern Python trading systems
5. ✅ Easy to test with mocked events

Extract for your code:
----------------------
- RunnableStatus enum → base_strategy.py
- start()/stop() pattern → base_strategy.py
- Event listener dict → base_strategy.py (optional)
- Control loop pattern → Can skip if you use trading_bot.py's main loop
"""

