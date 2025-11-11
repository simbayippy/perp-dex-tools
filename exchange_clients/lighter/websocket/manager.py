"""
Main WebSocket manager for Lighter exchange.

Orchestrates connection, order book, message handling, and market switching.
"""

import asyncio
import json
from typing import Dict, Any, List, Optional, Callable, Awaitable

from exchange_clients.base_websocket import BaseWebSocketManager, BBOData

from .connection import LighterWebSocketConnection
from .order_book import LighterOrderBook
from .message_handler import LighterMessageHandler
from .market_switcher import LighterMarketSwitcher


class LighterWebSocketManager(BaseWebSocketManager):
    """WebSocket manager for Lighter order updates and order book."""

    def __init__(
        self,
        config: Dict[str, Any],
        order_update_callback: Optional[Callable] = None,
        liquidation_callback: Optional[Callable[[List[Dict[str, Any]]], Awaitable[None]]] = None,
        positions_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
        user_stats_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    ):
        """
        Initialize WebSocket manager.
        
        Args:
            config: Configuration object
            order_update_callback: Callback for order updates
            liquidation_callback: Callback for liquidations
            positions_callback: Callback for positions
            user_stats_callback: Callback for user stats
        """
        super().__init__()
        self.config = config
        
        # Initialize components
        self.connection = LighterWebSocketConnection(config)
        self.order_book = LighterOrderBook()
        self.market_switcher = LighterMarketSwitcher(
            config=config,
            ws=None,  # Will be set after connection
            running=False,  # Will be set when connected
        )
        self.message_handler = LighterMessageHandler(
            config=config,
            ws=None,  # Will be set after connection
            market_index=config.contract_id,
            order_book_manager=self.order_book,
            order_update_callback=order_update_callback,
            liquidation_callback=liquidation_callback,
            positions_callback=positions_callback,
            user_stats_callback=user_stats_callback,
            notify_bbo_update_fn=self._notify_bbo_update,
        )
        
        # Track running state
        self.running = False
        self._listener_task: Optional[asyncio.Task] = None
        self._staleness_monitor_task: Optional[asyncio.Task] = None

    def set_logger(self, logger):
        """Set the logger instance for all components."""
        self.logger = logger
        self.connection.set_logger(logger)
        self.order_book.set_logger(logger)
        self.market_switcher.set_logger(logger)
        self.message_handler.set_logger(logger)

    def _log(self, message: str, level: str = "INFO"):
        """Log message using the logger if available."""
        if self.logger:
            self.logger.log(message, level)

    # Delegate order book methods
    def get_order_book(self, levels: Optional[int] = None) -> Optional[Dict[str, List[Dict[str, Any]]]]:
        """Get formatted order book with optional level limiting."""
        return self.order_book.get_order_book(levels)

    def get_best_levels(
        self, min_size_usd: float = 0
    ) -> tuple[tuple[Optional[float], Optional[float]], tuple[Optional[float], Optional[float]]]:
        """Get the best bid and ask levels from order book."""
        return self.order_book.get_best_levels(min_size_usd)

    @property
    def best_bid(self) -> Optional[float]:
        """Get current best bid."""
        return self.order_book.best_bid

    @property
    def best_ask(self) -> Optional[float]:
        """Get current best ask."""
        return self.order_book.best_ask

    @property
    def order_book_ready(self) -> bool:
        """Check if order book is ready."""
        return self.order_book.order_book_ready

    async def prepare_market_feed(self, symbol: Optional[str]) -> None:
        """
        Ensure the order book stream targets the requested symbol.
        
        Implementation follows the recommended pattern from BaseWebSocketManager:
        1. Validate: Check if already on target market
        2. Clear: Reset stale order book data
        3. Switch: Unsubscribe old, subscribe new
        4. Wait: Block until new data arrives
        5. Update: Synchronize config state
        """
        if symbol is None:
            return

        try:
            # Step 1: Lookup target market_id for the symbol
            target_market = await self.market_switcher.lookup_market_id(symbol)
            if target_market is None:
                return
            
            # Step 2: Validate if switch is needed
            if not self.market_switcher.validate_market_switch_needed(target_market):
                return
            
            # Step 3: Perform the market switch
            old_market_id = self.market_switcher.market_index
            await self._perform_market_switch(old_market_id, target_market)
            
            # Step 4: Wait for new data to arrive
            success = await self._wait_for_market_ready(timeout=5.0)
            
            # Step 5: Log result
            order_book_size = {
                'bids': len(self.order_book.order_book['bids']),
                'asks': len(self.order_book.order_book['asks'])
            }
            self.market_switcher.log_market_switch_result(old_market_id, target_market, success, order_book_size)
            
        except Exception as exc:
            self._log(f"Error switching market: {exc}", "ERROR")

    async def _perform_market_switch(self, old_market_id: int, new_market_id: int) -> None:
        """Execute the market switch: unsubscribe old, subscribe new, update config."""
        self._log(
            f"[LIGHTER] 🔄 Switching order book from market {old_market_id} to {new_market_id}",
            "INFO"
        )
        
        # Clear stale order book data
        await self.order_book.reset_order_book()
        
        # Unsubscribe from old market
        await self.market_switcher.unsubscribe_market(old_market_id)
        
        # Update internal state
        self.market_switcher.market_index = new_market_id
        self.message_handler.set_market_index(new_market_id)
        
        # Update config to keep it synchronized (critical for order placement!)
        self.market_switcher.update_market_config(new_market_id)
        
        # Subscribe to new market
        await self.market_switcher.subscribe_market(new_market_id)

    async def _wait_for_market_ready(self, timeout: float = 5.0) -> bool:
        """
        Wait for new market data to arrive after switching.
        
        Args:
            timeout: Maximum time to wait in seconds
            
        Returns:
            True if snapshot loaded, False if timeout
        """
        start_time = asyncio.get_event_loop().time()
        
        while not self.order_book.snapshot_loaded and (asyncio.get_event_loop().time() - start_time) < timeout:
            await asyncio.sleep(0.1)
        
        return self.order_book.snapshot_loaded

    async def request_fresh_snapshot(self):
        """Request a fresh order book snapshot when we detect inconsistencies."""
        try:
            if not self.connection.ws:
                return

            # Unsubscribe and resubscribe to get a fresh snapshot
            unsubscribe_msg = json.dumps({
                "type": "unsubscribe",
                "channel": f"order_book/{self.market_switcher.market_index}"
            })
            await self.connection.ws.send_str(unsubscribe_msg)

            # Wait a moment for the unsubscribe to process
            await asyncio.sleep(1)

            # Resubscribe to get a fresh snapshot
            subscribe_msg = json.dumps({
                "type": "subscribe",
                "channel": f"order_book/{self.market_switcher.market_index}"
            })
            await self.connection.ws.send_str(subscribe_msg)

            self._log("Requested fresh order book snapshot", "INFO")
        except Exception as e:
            self._log(f"Error requesting fresh snapshot: {e}", "ERROR")
            raise
    
    async def force_reconnect(self):
        """
        Force a full websocket reconnect by closing the current connection.
        
        This will cause _consume_messages() to fail, triggering the reconnect mechanism
        in _listen_loop(). Use this when order book is stale for extended period.
        """
        self._log(
            "[LIGHTER] Forcing websocket reconnect due to stale order book",
            "WARNING"
        )
        try:
            # Close the current connection - this will cause _consume_messages() to fail
            # and trigger the reconnect mechanism in _listen_loop()
            await self.connection.cleanup_current_ws()
            self.order_book.order_book_ready = False
            self.order_book.snapshot_loaded = False
        except Exception as e:
            self._log(f"Error forcing reconnect: {e}", "ERROR")

    async def _subscribe_channels(self) -> None:
        """Subscribe to the required Lighter channels."""
        await self.market_switcher.subscribe_channels(
            subscribe_positions=bool(self.message_handler.positions_callback),
            subscribe_liquidations=bool(self.message_handler.liquidation_callback),
            subscribe_user_stats=bool(self.message_handler.user_stats_callback),
        )

    async def _consume_messages(self) -> None:
        """Listen for messages on the WebSocket connection."""
        cleanup_counter = 0
        while self.running and self.connection.ws:
            try:
                msg = await self.connection.ws.receive()
            except (asyncio.CancelledError,):
                raise
            except Exception as exc:
                break

            # Process message
            result = await self.message_handler.process_message(msg)
            
            if result is None:
                continue
            
            if result.get("close") or result.get("error"):
                break

            # Handle cleanup periodically
            cleanup_counter += 1
            if cleanup_counter >= 1000:
                self.order_book.cleanup_old_order_book_levels()
                cleanup_counter = 0

            # Handle snapshot request
            if result.get("request_snapshot"):
                try:
                    await self.request_fresh_snapshot()
                    self.order_book.order_book_sequence_gap = False
                except Exception as exc:
                    self._log(f"Failed to request fresh snapshot: {exc}", "ERROR")
                    break

            # Dispatch callbacks
            if result.get("notifications"):
                await self.message_handler.dispatch_liquidations(result["notifications"])

            if result.get("positions"):
                await self.message_handler.dispatch_positions(result["positions"])

            if result.get("user_stats"):
                await self.message_handler.dispatch_user_stats(result["user_stats"])

    async def _listen_loop(self) -> None:
        """Keep the websocket stream alive and reconnect on failures."""
        try:
            while self.running:
                try:
                    await self._consume_messages()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._log(f"[LIGHTER] Websocket listener error: {exc}", "ERROR")
                finally:
                    await self.connection.cleanup_current_ws()
                    self.order_book.order_book_ready = False
                    self.order_book.snapshot_loaded = False

                if not self.running:
                    break

                # Reconnect
                await self.connection.reconnect(
                    reset_order_book_fn=self.order_book.reset_order_book,
                    subscribe_channels_fn=self._subscribe_channels,
                    running=self.running,
                    update_component_references_fn=self._update_component_references,
                )
                # Note: Component references are now updated INSIDE reconnect() before subscribing
                # This ensures market_switcher and message_handler use the new websocket
        except asyncio.CancelledError:
            pass
        finally:
            await self.connection.cleanup_current_ws()
            await self.connection._close_session()
            self.running = False
            self._log("WebSocket listener stopped", "INFO")

    def _update_component_references(self):
        """Update component references after reconnection."""
        self.market_switcher.set_ws(self.connection.ws)
        self.market_switcher.set_running(self.running)
        self.message_handler.set_ws(self.connection.ws)
        self.message_handler.set_market_index(self.market_switcher.market_index)

    async def connect(self):
        """Connect to the Lighter WebSocket and start the listener task."""
        if self.running:
            return

        await self.order_book.reset_order_book()

        try:
            await self.connection.open_connection()
            # Update component references BEFORE subscribing (components need ws reference)
            self._update_component_references()
            await self._subscribe_channels()
        except Exception:
            await self.connection._close_session()
            raise

        self.running = True
        self.market_switcher.set_running(True)

        self._listener_task = asyncio.create_task(self._listen_loop(), name="lighter-ws-listener")
        self._staleness_monitor_task = asyncio.create_task(
            self._monitor_staleness_loop(), 
            name="lighter-ws-staleness-monitor"
        )

    async def _monitor_staleness_loop(self) -> None:
        """
        Proactively monitor order book staleness and trigger reconnects.
        
        Runs every 30 seconds to detect stale order books even when not actively
        querying mark prices. This ensures websocket stays healthy.
        """
        try:
            while self.running:
                await asyncio.sleep(30)  # Check every 30 seconds
                
                if not self.running:
                    break
                
                if not self.order_book.snapshot_loaded:
                    continue  # Not loaded yet, skip check
                
                if self.order_book.is_stale():
                    staleness_seconds = self.order_book.get_staleness_seconds()
                    if staleness_seconds is None:
                        continue
                    
                    if self.order_book.needs_reconnect():
                        self._log(
                            f"[LIGHTER] Proactive staleness check: Order book stale "
                            f"({staleness_seconds:.1f}s), forcing reconnect",
                            "WARNING"
                        )
                        await self.force_reconnect()
                    else:
                        self._log(
                            f"[LIGHTER] Proactive staleness check: Order book stale "
                            f"({staleness_seconds:.1f}s), requesting snapshot",
                            "DEBUG"
                        )
                        try:
                            await self.request_fresh_snapshot()
                        except Exception as exc:
                            self._log(
                                f"[LIGHTER] Failed to request snapshot in staleness monitor: {exc}",
                                "ERROR"
                            )
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self._log(f"[LIGHTER] Staleness monitor error: {exc}", "ERROR")

    async def disconnect(self):
        """Disconnect from WebSocket."""
        if not self.running and not self._listener_task:
            return

        self.running = False
        self.market_switcher.set_running(False)

        # Cancel staleness monitor
        if self._staleness_monitor_task:
            self._staleness_monitor_task.cancel()
            try:
                await self._staleness_monitor_task
            except asyncio.CancelledError:
                pass
            finally:
                self._staleness_monitor_task = None

        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
            finally:
                self._listener_task = None

        await self.connection.cleanup_current_ws()
        await self.connection._close_session()

        self._log("WebSocket disconnected", "INFO")

