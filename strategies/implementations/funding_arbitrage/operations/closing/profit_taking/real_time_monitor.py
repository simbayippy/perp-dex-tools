"""
Real-time profit monitoring using WebSocket BBO streams.

Monitors Best Bid/Offer updates from exchange WebSocket streams to detect
immediate profit opportunities on open funding arbitrage positions.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Callable, Dict, Optional, Set
from datetime import datetime, timezone

from exchange_clients.base_websocket import BBOData

if TYPE_CHECKING:
    from ...strategy import FundingArbitrageStrategy
    from ...models import FundingArbPosition
    from exchange_clients.base_models import ExchangePositionSnapshot


class RealTimeProfitMonitor:
    """
    WebSocket-driven profit opportunity monitor for funding arbitrage positions.

    Registers BBO listeners for open positions and triggers profit evaluation
    when favorable price movements are detected, capturing cross-exchange
    basis spread opportunities in real-time.

    Features:
    - Zero additional API calls (uses existing WebSocket streams)
    - Throttled checks (configurable interval, default 1s)
    - Symbol filtering and concurrency protection
    - Automatic cleanup on position close
    """

    def __init__(self, strategy: "FundingArbitrageStrategy") -> None:
        """
        Initialize real-time profit monitor.

        Args:
            strategy: Parent funding arbitrage strategy instance
        """
        self._strategy = strategy
        self._logger = strategy.logger

        # Listener registry: position_id -> (listener_func, exchanges)
        self._listeners: Dict[str, tuple[Callable, list[str]]] = {}

        # Throttle tracking: position_id -> last_check_timestamp
        self._last_check: Dict[str, float] = {}

        # Concurrency protection: Use position_closer's lock to prevent duplicate closes
        # This ensures coordination between profit-taking and risk-based exits
        self._positions_being_evaluated: Set[str] = set()

        # Configuration
        config = strategy.config
        self._check_interval = float(getattr(config, 'realtime_profit_check_interval', 1.0))

        self._logger.info(
            f"✅ Real-time profit monitor initialized: "
            f"check_interval={self._check_interval}s, "
            f"smart_caching=enabled"
        )

    async def register_position(self, position: "FundingArbPosition") -> None:
        """
        Register BBO listeners for both legs of a position.

        Args:
            position: Position to register listeners for
        """
        position_id = position.id

        # Skip if already registered
        if position_id in self._listeners:
            self._logger.debug(
                f"Position {position.symbol} (id={position_id}) already has BBO listeners registered"
            )
            return

        try:
            # Get exchange clients for both legs
            long_client = self._strategy.exchange_clients.get(position.long_dex)
            short_client = self._strategy.exchange_clients.get(position.short_dex)

            if not long_client or not short_client:
                self._logger.warning(
                    f"Cannot register BBO listeners for {position.symbol}: "
                    f"missing exchange client (long={long_client is not None}, short={short_client is not None})"
                )
                return

            # Create throttled listener for this position
            listener = self._create_listener(position)
            registered_exchanges = []

            # Register with long leg exchange
            long_ws = getattr(long_client, 'ws_manager', None)
            if long_ws and hasattr(long_ws, 'register_bbo_listener'):
                long_ws.register_bbo_listener(listener)
                registered_exchanges.append(position.long_dex)
                self._logger.debug(
                    f"[PROFIT_MONITOR] Registered BBO listener for {position.symbol} on {position.long_dex.upper()}"
                )

            # Register with short leg exchange
            short_ws = getattr(short_client, 'ws_manager', None)
            if short_ws and hasattr(short_ws, 'register_bbo_listener'):
                short_ws.register_bbo_listener(listener)
                registered_exchanges.append(position.short_dex)
                self._logger.debug(
                    f"[PROFIT_MONITOR] Registered BBO listener for {position.symbol} on {position.short_dex.upper()}"
                )

            if registered_exchanges:
                # Store listener and exchanges for cleanup
                self._listeners[position_id] = (listener, registered_exchanges)
                self._logger.info(
                    f"💹 Real-time profit monitor active for {position.symbol} "
                    f"(exchanges: {', '.join([ex.upper() for ex in registered_exchanges])})"
                )
            else:
                self._logger.warning(
                    f"No WebSocket managers available for {position.symbol} real-time profit monitoring"
                )

        except Exception as exc:
            self._logger.error(
                f"Failed to register BBO listeners for {position.symbol} (id={position_id}): {exc}",
                exc_info=True
            )

    async def unregister_position(self, position: "FundingArbPosition") -> None:
        """
        Unregister BBO listeners for a position.

        Args:
            position: Position to unregister listeners for
        """
        position_id = position.id

        if position_id not in self._listeners:
            return

        try:
            listener, registered_exchanges = self._listeners.pop(position_id)

            # Unregister from each exchange
            for dex in registered_exchanges:
                client = self._strategy.exchange_clients.get(dex)
                if client:
                    ws_manager = getattr(client, 'ws_manager', None)
                    if ws_manager and hasattr(ws_manager, 'unregister_bbo_listener'):
                        ws_manager.unregister_bbo_listener(listener)
                        self._logger.debug(
                            f"[PROFIT_MONITOR] Unregistered BBO listener for {position.symbol} on {dex.upper()}"
                        )

            # Cleanup throttle tracking
            self._last_check.pop(position_id, None)
            self._positions_being_evaluated.discard(position_id)

            self._logger.info(
                f"🔕 Real-time profit monitor disabled for {position.symbol}"
            )

        except Exception as exc:
            self._logger.error(
                f"Error unregistering BBO listeners for {position.symbol} (id={position_id}): {exc}",
                exc_info=True
            )

    async def cleanup_all(self) -> None:
        """Cleanup all registered listeners on strategy shutdown."""
        self._logger.info("Cleaning up all real-time profit monitor listeners...")

        # Get all position IDs to cleanup (snapshot to avoid modification during iteration)
        position_ids = list(self._listeners.keys())

        for position_id in position_ids:
            try:
                # We don't have the position object, but we can still cleanup
                listener, registered_exchanges = self._listeners.pop(position_id)

                for dex in registered_exchanges:
                    client = self._strategy.exchange_clients.get(dex)
                    if client:
                        ws_manager = getattr(client, 'ws_manager', None)
                        if ws_manager and hasattr(ws_manager, 'unregister_bbo_listener'):
                            try:
                                ws_manager.unregister_bbo_listener(listener)
                            except Exception:
                                pass  # Best effort cleanup

            except Exception as exc:
                self._logger.debug(f"Error during cleanup for position {position_id}: {exc}")

        # Clear all tracking
        self._last_check.clear()
        self._positions_being_evaluated.clear()

        self._logger.info("✅ Real-time profit monitor cleanup complete")

    def _create_listener(self, position: "FundingArbPosition") -> Callable:
        """
        Create a throttled BBO listener for a position.

        Args:
            position: Position to create listener for

        Returns:
            Async callback function for BBO updates
        """
        position_id = position.id
        position_symbol = position.symbol.upper()

        async def listener(bbo: BBOData) -> None:
            """Handle BBO update and trigger profit evaluation."""
            try:
                # 1. Symbol filtering - only process relevant BBO updates
                if not self._symbol_matches(bbo.symbol, position_symbol):
                    return

                # 2. Throttling - limit check frequency
                now = time.time()
                last_check = self._last_check.get(position_id, 0)
                if now - last_check < self._check_interval:
                    return  # Too soon since last check

                # 3. Concurrency protection - prevent duplicate evaluations AND closes
                # Check both evaluation lock and position_closer's closing lock
                if position_id in self._positions_being_evaluated:
                    return  # Already being evaluated

                # Check if position is being closed by position_closer (risk exit or manual close)
                position_closer = self._strategy.position_closer
                if position_id in position_closer._positions_closing:
                    return  # Already being closed elsewhere

                # Update throttle timestamp
                self._last_check[position_id] = now

                # 4. Trigger profit evaluation
                try:
                    self._positions_being_evaluated.add(position_id)
                    await self._trigger_profit_evaluation(position, bbo)
                finally:
                    self._positions_being_evaluated.discard(position_id)

            except Exception as exc:
                self._logger.error(
                    f"[PROFIT_MONITOR] Error in BBO listener for {position.symbol}: {exc}",
                    exc_info=True
                )

        return listener

    async def _trigger_profit_evaluation(
        self,
        position: "FundingArbPosition",
        bbo: BBOData
    ) -> None:
        """
        Trigger profit evaluation for a position.

        Delegates to ProfitTaker for evaluation and execution.

        Args:
            position: Position to evaluate
            bbo: BBO update that triggered the evaluation
        """
        try:
            # Fetch position snapshots (cached or fresh)
            snapshots = await self._fetch_snapshots(position)

            if not snapshots:
                self._logger.debug(
                    f"[PROFIT_MONITOR] No snapshots available for {position.symbol}, skipping profit check"
                )
                return

            # Collect fresh BBO prices from both legs for accurate profit calculation
            bbo_prices = await self._collect_bbo_prices(position)

            # Delegate to profit_taker for evaluation and execution
            profit_taker = getattr(self._strategy, 'profit_taker', None)
            if not profit_taker:
                self._logger.warning(
                    f"[PROFIT_MONITOR] ProfitTaker not initialized, cannot evaluate {position.symbol}"
                )
                return

            await profit_taker.evaluate_and_execute(
                position,
                snapshots,
                bbo_prices=bbo_prices,
                trigger_source="websocket"
            )

        except Exception as exc:
            self._logger.error(
                f"[PROFIT_MONITOR] Error triggering profit evaluation for {position.symbol}: {exc}",
                exc_info=True
            )

    async def _collect_bbo_prices(
        self,
        position: "FundingArbPosition"
    ) -> Dict[str, BBOData]:
        """
        Collect current BBO prices from both exchange legs.

        Args:
            position: Position to collect BBO prices for

        Returns:
            Dictionary of exchange -> BBOData
        """
        bbo_prices: Dict[str, BBOData] = {}

        try:
            # Collect BBO from long leg exchange
            long_client = self._strategy.exchange_clients.get(position.long_dex)
            if long_client:
                long_ws = getattr(long_client, 'ws_manager', None)
                if long_ws and hasattr(long_ws, 'get_latest_bbo'):
                    long_bbo = long_ws.get_latest_bbo()
                    if long_bbo:
                        bbo_prices[position.long_dex] = long_bbo

            # Collect BBO from short leg exchange
            short_client = self._strategy.exchange_clients.get(position.short_dex)
            if short_client:
                short_ws = getattr(short_client, 'ws_manager', None)
                if short_ws and hasattr(short_ws, 'get_latest_bbo'):
                    short_bbo = short_ws.get_latest_bbo()
                    if short_bbo:
                        bbo_prices[position.short_dex] = short_bbo

            if not bbo_prices:
                self._logger.debug(
                    f"[PROFIT_MONITOR] No BBO data available for {position.symbol}, "
                    f"will fallback to snapshot pricing"
                )

        except Exception as exc:
            self._logger.warning(
                f"[PROFIT_MONITOR] Error collecting BBO prices for {position.symbol}: {exc}"
            )

        return bbo_prices

    async def _fetch_snapshots(
        self,
        position: "FundingArbPosition"
    ) -> Dict[str, Optional["ExchangePositionSnapshot"]]:
        """
        Fetch position snapshots using smart caching.

        Always tries cached snapshots first (zero API calls), automatically
        falls back to fresh REST API fetch if cache is stale or unavailable.

        Args:
            position: Position to fetch snapshots for

        Returns:
            Dictionary of exchange -> snapshot
        """
        # Smart caching: Try cache first (fast path, zero API calls)
        cached = self._get_cached_snapshots(position)
        if cached is not None:
            return cached

        # Fallback: Fetch fresh snapshots via REST API (slow path, ~1-2% of calls)
        return await self._strategy.position_closer._fetch_leg_snapshots(position)

    def _get_cached_snapshots(
        self,
        position: "FundingArbPosition"
    ) -> Optional[Dict[str, Optional["ExchangePositionSnapshot"]]]:
        """
        Get cached snapshots from position metadata if available and fresh.

        Args:
            position: Position to get cached snapshots for

        Returns:
            Cached snapshots if available and fresh, None otherwise
        """
        try:
            snapshot_cache = position.metadata.get("snapshot_cache")
            if not snapshot_cache:
                return None

            # Check staleness (30 seconds max age)
            timestamp_str = snapshot_cache.get("timestamp")
            if not timestamp_str:
                return None

            cache_time = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
            if cache_time.tzinfo is None:
                cache_time = cache_time.replace(tzinfo=timezone.utc)

            age_seconds = (datetime.now(timezone.utc) - cache_time).total_seconds()

            if age_seconds > 30:  # Cache too old
                self._logger.debug(
                    f"[PROFIT_MONITOR] Cached snapshots stale for {position.symbol} "
                    f"(age={age_seconds:.1f}s), fetching fresh"
                )
                return None

            # Return cached snapshots
            snapshots = snapshot_cache.get("snapshots")
            if snapshots:
                self._logger.debug(
                    f"[PROFIT_MONITOR] Using cached snapshots for {position.symbol} "
                    f"(age={age_seconds:.1f}s)"
                )
                return snapshots

            return None

        except Exception as exc:
            self._logger.debug(
                f"Error retrieving cached snapshots for {position.symbol}: {exc}"
            )
            return None

    @staticmethod
    def _symbol_matches(bbo_symbol: Optional[str], position_symbol: Optional[str]) -> bool:
        """
        Check if BBO symbol matches position symbol.

        Handles different symbol formats across exchanges:
        - Aster: "BTCUSDT"
        - Lighter: "BTC" or market_id (numeric)

        Args:
            bbo_symbol: Symbol from BBO update
            position_symbol: Position symbol (normalized)

        Returns:
            True if symbols match, False otherwise
        """
        if not bbo_symbol or not position_symbol:
            return False

        bbo_upper = str(bbo_symbol).upper()
        pos_upper = str(position_symbol).upper()

        # Exact match
        if bbo_upper == pos_upper:
            return True

        # Match with USDT suffix (Aster format)
        if bbo_upper == f"{pos_upper}USDT":
            return True

        # Match without USDT suffix
        if bbo_upper.endswith("USDT") and bbo_upper[:-4] == pos_upper:
            return True

        # Partial match (contains)
        if pos_upper in bbo_upper or bbo_upper in pos_upper:
            return True

        return False
