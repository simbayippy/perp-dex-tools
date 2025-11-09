"""
Position manager module for Lighter client.

Handles position tracking, snapshots, funding calculations, and enrichment.
"""

import asyncio
import time
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

from exchange_clients.base_models import ExchangePositionSnapshot
from exchange_clients.lighter.client.utils.converters import build_snapshot_from_raw
from exchange_clients.lighter.client.utils.helpers import decimal_or_none


class LighterPositionManager:
    """
    Position manager for Lighter exchange.
    
    Handles:
    - Position snapshot fetching
    - Position caching and enrichment
    - Funding calculations
    - Position refresh and tracking
    """
    
    def __init__(
        self,
        account_api: Any,
        order_api: Any,
        lighter_client: Any,
        config: Any,
        logger: Any,
        account_index: int,
        raw_positions: Dict[str, Dict[str, Any]],
        positions_lock: asyncio.Lock,
        positions_ready: asyncio.Event,
        ws_manager: Optional[Any] = None,
        normalize_symbol_fn: Optional[Any] = None,
        market_data: Optional[Any] = None,
    ):
        """
        Initialize position manager.
        
        Args:
            account_api: Lighter AccountApi instance
            order_api: Lighter OrderApi instance (for trade history)
            lighter_client: Lighter SignerClient instance (for auth tokens)
            config: Trading configuration object
            logger: Logger instance
            account_index: Account index
            raw_positions: Dictionary to cache raw position data (client._raw_positions)
            positions_lock: Lock for thread-safe position access
            positions_ready: Event signaling positions are ready
            ws_manager: Optional WebSocket manager (for live mark prices)
            normalize_symbol_fn: Function to normalize symbols
            market_data: Optional MarketData manager (for REST fallback when websocket unavailable)
        """
        self.account_api = account_api
        self.order_api = order_api
        self.lighter_client = lighter_client
        self.config = config
        self.logger = logger
        self.account_index = account_index
        self.raw_positions = raw_positions
        self.positions_lock = positions_lock
        self.positions_ready = positions_ready
        self.ws_manager = ws_manager
        self.market_data = market_data
        self.normalize_symbol = normalize_symbol_fn or (lambda s: s.upper())
    
    def _trigger_async_reconnect(self, coro) -> None:
        """Helper to trigger async reconnect task without blocking."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(coro)
        except RuntimeError:
            pass  # No event loop, skip (will use REST fallback)

    def _get_websocket_mark_price(self, normalized_symbol: str) -> Optional[Decimal]:
        """
        Get mark price from websocket order book.
        
        Returns None if websocket unavailable, stale, or symbol mismatch.
        Automatically triggers reconnect if order book is stale.
        """
        if self.ws_manager is None:
            return None

        # Check if this is the active symbol
        config_symbol = getattr(self.config, "ticker", None)
        if config_symbol:
            active_symbol = self.normalize_symbol(str(config_symbol)).upper()
            if normalized_symbol != active_symbol:
                return None

        # Check staleness and trigger reconnect if needed
        if self.ws_manager.order_book.is_stale():
            staleness_seconds = self.ws_manager.order_book.get_staleness_seconds()
            
            if self.ws_manager.order_book.needs_reconnect():
                self.logger.warning(
                    f"[LIGHTER] Order book stale ({staleness_seconds:.1f}s), forcing reconnect"
                )
                self._trigger_async_reconnect(self.ws_manager.force_reconnect())
            else:
                self.logger.debug(
                    f"[LIGHTER] Order book stale ({staleness_seconds:.1f}s), requesting snapshot"
                )
                self._trigger_async_reconnect(self.ws_manager.request_fresh_snapshot())
            
            return None  # Stale, use REST fallback

        # Extract best bid/ask and calculate midpoint
        best_bid = self.ws_manager.best_bid
        best_ask = self.ws_manager.best_ask
        
        if best_bid is None and best_ask is None:
            return None
        
        try:
            bid_decimal = Decimal(str(best_bid)) if best_bid is not None else None
            ask_decimal = Decimal(str(best_ask)) if best_ask is not None else None
            
            if bid_decimal is not None and ask_decimal is not None:
                return (bid_decimal + ask_decimal) / Decimal("2")
            elif bid_decimal is not None:
                return bid_decimal
            elif ask_decimal is not None:
                return ask_decimal
        except (TypeError, ValueError):
            pass
        
        return None

    async def _get_rest_mark_price(self, normalized_symbol: str) -> Optional[Decimal]:
        """
        Get mark price from REST API BBO endpoint only (bypasses WebSocket check).
        
        This is called after WebSocket check fails, so we use REST-only method
        to avoid redundant WebSocket checks.
        """
        if self.market_data is None:
            return None
        
        try:
            contract_id = getattr(self.config, "contract_id", None)
            if not contract_id:
                return None
            
            # Use REST-only method since we already checked WebSocket
            bid, ask = await self.market_data.fetch_bbo_prices_rest_only(str(contract_id))
            if bid is not None and ask is not None:
                mark_price = (bid + ask) / Decimal("2")
                self.logger.debug(
                    f"[LIGHTER] Using REST mark_price for {normalized_symbol}: {mark_price}"
                )
                return mark_price
        except Exception as exc:
            self.logger.debug(
                f"[LIGHTER] Failed to fetch REST mark_price for {normalized_symbol}: {exc}"
            )
        
        return None

    async def _get_mark_price(self, normalized_symbol: str, raw: Dict[str, Any]) -> Optional[Decimal]:
        """
        Get mark price with fallback hierarchy:
        1. Websocket order book (real-time)
        2. REST API BBO (fresh but costs weight)
        3. Cached mark_price from raw position data (may be stale)
        
        Returns None if all sources fail.
        """
        # Try websocket first (free, real-time)
        mark_price = self._get_websocket_mark_price(normalized_symbol)
        if mark_price is not None:
            return mark_price
        
        # Fallback to REST API
        mark_price = await self._get_rest_mark_price(normalized_symbol)
        if mark_price is not None:
            return mark_price
        
        # Last resort: cached mark_price from position data
        cached_mark = decimal_or_none(raw.get("mark_price"))
        if cached_mark is not None:
            self.logger.debug(
                f"[LIGHTER] Using cached mark_price for {normalized_symbol} "
                "(websocket and REST unavailable)"
            )
            return cached_mark
        
        return None

    def _calculate_unrealized_pnl(
        self, 
        mark_price: Decimal, 
        entry_price: Optional[Decimal], 
        quantity: Decimal
    ) -> Optional[Decimal]:
        """Calculate unrealized PnL from mark price, entry price, and quantity."""
        if entry_price is None or quantity == 0:
            return None
        
        try:
            return (mark_price - entry_price) * quantity
        except Exception:
            return None
    
    async def enrich_snapshot_with_market_data(
        self,
        normalized_symbol: str,
        raw: Dict[str, Any],
        snapshot: ExchangePositionSnapshot,
    ) -> None:
        """
        Enrich snapshot with mark price, exposure, and unrealized PnL.
        
        Strategy:
        - Always calculate uPnL from mark price when available (websocket or REST)
        - Only fall back to exchange-provided uPnL when mark price is completely unavailable
        - This ensures real-time accuracy even when websocket order book is stale
        """
        # Get mark price with fallback hierarchy (websocket -> REST -> cached)
        mark_price = await self._get_mark_price(normalized_symbol, raw)
        
        quantity = snapshot.quantity or Decimal("0")
        quantity_abs = quantity.copy_abs()
        
        if mark_price is not None:
            # Update snapshot with mark price
            snapshot.mark_price = mark_price
            
            # Calculate exposure
            if quantity_abs != 0:
                snapshot.exposure_usd = quantity_abs * mark_price
            
            # Always calculate uPnL from mark price when available
            # This ensures accuracy even if exchange-provided uPnL is stale
            calculated_pnl = self._calculate_unrealized_pnl(
                mark_price, 
                snapshot.entry_price, 
                quantity
            )
            
            if calculated_pnl is not None:
                snapshot.unrealized_pnl = calculated_pnl
            else:
                # Fallback to exchange-provided uPnL if calculation fails
                raw_unrealized = decimal_or_none(raw.get("unrealized_pnl"))
                if raw_unrealized is not None:
                    snapshot.unrealized_pnl = raw_unrealized
                    self.logger.debug(
                        f"[LIGHTER] Using exchange unrealized_pnl for {normalized_symbol} "
                        f"(calculation unavailable): {raw_unrealized}"
                    )
            
            # Update cache with fresh values
            async with self.positions_lock:
                cached = self.raw_positions.get(normalized_symbol)
                if cached is not None:
                    cached["mark_price"] = str(mark_price)
                    if snapshot.exposure_usd is not None:
                        cached["position_value"] = str(snapshot.exposure_usd)
                    if snapshot.unrealized_pnl is not None:
                        cached["unrealized_pnl"] = str(snapshot.unrealized_pnl)
        else:
            # No mark price available - use exchange-provided uPnL as last resort
            raw_unrealized = decimal_or_none(raw.get("unrealized_pnl"))
            if raw_unrealized is not None:
                snapshot.unrealized_pnl = raw_unrealized
                self.logger.debug(
                    f"[LIGHTER] Using exchange unrealized_pnl for {normalized_symbol} "
                    "(no mark price available): {raw_unrealized}"
                )
    
    async def _get_funding_for_snapshot(
        self,
        normalized_symbol: str,
        raw: Dict[str, Any],
        snapshot: ExchangePositionSnapshot,
        position_opened_at: Optional[float] = None,
    ) -> Optional[Decimal]:
        """
        Get funding accrued for a position snapshot.
        
        Checks websocket data first, then cached funding, then REST API if needed.
        """
        async with self.positions_lock:
            funding_in_cache = raw.get("funding_accrued")
            total_funding_ws = raw.get("total_funding_paid_out")
            
            # Websocket is source of truth - update cache if websocket has funding
            if total_funding_ws is not None:
                ws_funding = decimal_or_none(total_funding_ws)
                if ws_funding is not None:
                    raw["funding_accrued"] = ws_funding
                    funding_in_cache = ws_funding
                    self.logger.debug(
                        f"[LIGHTER] Updated funding from websocket for {normalized_symbol}: {ws_funding}"
                    )
            elif funding_in_cache is None and total_funding_ws is not None:
                raw["funding_accrued"] = total_funding_ws
                funding_in_cache = total_funding_ws
        
        # Determine if we need to query REST for funding
        needs_funding = (
            funding_in_cache is None  # Never checked
            or (
                funding_in_cache == Decimal("0")  # Cached as 0
                and total_funding_ws is None  # But websocket doesn't have it
            )
        )
        
        if not needs_funding:
            return decimal_or_none(funding_in_cache)
        
        # Check if we have fresh cached funding (< 20 minutes old)
        async with self.positions_lock:
            cached_raw = self.raw_positions.get(normalized_symbol)
            funding_cache_timestamp = cached_raw.get("funding_cache_timestamp") if cached_raw else None
            cached_funding_value = cached_raw.get("funding_accrued") if cached_raw else None
            
            cache_age_seconds = None
            if funding_cache_timestamp:
                cache_age_seconds = time.time() - funding_cache_timestamp
                if cache_age_seconds < 1200 and cached_funding_value is not None:  # 20 minutes
                    return decimal_or_none(cached_funding_value)
        
        # Query REST API for funding
        if not snapshot.side or snapshot.quantity == 0 or raw.get("market_id") is None:
            return decimal_or_none(funding_in_cache)
        
        try:
            self.logger.info(
                f"[LIGHTER] Querying position_funding() API for {normalized_symbol} "
                f"({'cache expired' if cache_age_seconds else 'no cache'})"
            )
            funding = await self.get_cumulative_funding(
                raw.get("market_id"),
                snapshot.side,
                quantity=snapshot.quantity,
                position_opened_at=position_opened_at,
            )
            
            # Convert None → Decimal("0") to mark as "checked"
            funding = funding if funding is not None else Decimal("0")
            
            # Cache the funding value with timestamp
            async with self.positions_lock:
                cached = self.raw_positions.get(normalized_symbol)
                if cached is not None:
                    cached["funding_accrued"] = funding
                    cached["funding_cache_timestamp"] = time.time()
                    self.logger.debug(
                        f"[LIGHTER] Cached funding for {normalized_symbol}: {funding}"
                    )
            
            return funding
        except Exception as exc:
            self.logger.debug(f"[LIGHTER] Funding lookup failed for {normalized_symbol}: {exc}")
            return decimal_or_none(funding_in_cache)

    async def snapshot_from_cache(
        self, 
        normalized_symbol: str,
        position_opened_at: Optional[float] = None,
    ) -> Optional[ExchangePositionSnapshot]:
        """Retrieve a cached snapshot if available, enriching with market data and funding."""
        async with self.positions_lock:
            raw = self.raw_positions.get(normalized_symbol)

        if raw is None:
            return None

        snapshot = build_snapshot_from_raw(normalized_symbol, raw)
        if snapshot is None:
            return None

        # Enrich with mark price and calculate uPnL
        await self.enrich_snapshot_with_market_data(normalized_symbol, raw, snapshot)

        # Get funding (from cache or REST API)
        snapshot.funding_accrued = await self._get_funding_for_snapshot(
            normalized_symbol, raw, snapshot, position_opened_at
        )

        return snapshot
    
    async def refresh_positions_via_rest(self) -> None:
        """Refresh cached positions via REST as a fallback."""
        try:
            self.logger.debug("[LIGHTER] Refreshing positions via REST fallback")
            positions = await self.get_detailed_positions()
        except Exception as exc:
            self.logger.warning(f"[LIGHTER] Failed to refresh positions via REST: {exc}")
            return

        updates: Dict[str, Dict[str, Any]] = {}
        for pos in positions:
            if pos is None:
                continue
            position_dict = dict(pos)
            symbol_raw = position_dict.get("symbol") or getattr(self.config, "ticker", None)
            if not symbol_raw and position_dict.get("market_id") is not None:
                symbol_raw = str(position_dict["market_id"])
            if not symbol_raw:
                continue
            normalized_symbol = self.normalize_symbol(str(symbol_raw)).upper()
            updates[normalized_symbol] = position_dict

        async with self.positions_lock:
            self.raw_positions.update(updates)
            self.positions_ready.set()
    
    async def get_detailed_positions(self) -> List[Dict[str, Any]]:
        """Get detailed position info using Lighter SDK."""
        try:
            if not self.account_api:
                return []
                
            account_data = await self.account_api.account(by="index", value=str(self.account_index))
            if account_data and account_data.accounts:
                positions = []
                for pos in account_data.accounts[0].positions:
                    # Log ALL positions being processed (especially those with non-zero position)
                    position_qty = Decimal(pos.position)
                    has_position = abs(position_qty) > Decimal("0.0001")
                    is_toshi = pos.symbol and ('TOSHI' in pos.symbol.upper() or '1000TOSHI' in pos.symbol.upper())
                    
                    if has_position:
                        self.logger.debug(
                            f"[LIGHTER] Processing position: symbol={pos.symbol}, "
                            f"qty={position_qty}, market_id={pos.market_id}"
                        )
                    
                    pos_dict = {
                        'market_id': pos.market_id,
                        'symbol': pos.symbol,
                        'position': position_qty,
                        'avg_entry_price': Decimal(pos.avg_entry_price),
                        'position_value': Decimal(pos.position_value),
                        'unrealized_pnl': Decimal(pos.unrealized_pnl),
                        'realized_pnl': Decimal(pos.realized_pnl),
                        'liquidation_price': Decimal(pos.liquidation_price),
                        'allocated_margin': Decimal(pos.allocated_margin),
                        'sign': pos.sign  # 1 for Long, -1 for Short
                    }
                    
                    # Note: We don't parse total_funding_paid_out from account() API anymore
                    # because it's optional and often None. We use position_funding() API instead.
                    
                    positions.append(pos_dict)
                return positions
            return []
        except Exception as e:
            self.logger.error(f"Error getting detailed positions: {e}")
            return []
    
    async def get_position_open_time(self, market_id: int, current_quantity: Decimal) -> Optional[int]:
        """
        Estimate when the current position was opened by analyzing recent trade history.
        
        Args:
            market_id: The market ID for the position
            current_quantity: Current position quantity (to correlate with trades)
            
        Returns:
            Timestamp (seconds) when position was likely opened, or None if unknown
        """
        try:
            # Generate auth token for API call
            if not self.lighter_client:
                return None
            
            auth_token, error = self.lighter_client.create_auth_token_with_expiry()
            if error:
                self.logger.debug(f"[LIGHTER] Error creating auth token for trade history: {error}")
                return None
            
            account_index = getattr(self.config, "account_index", None)
            if account_index is None:
                return None
            
            # Fetch recent trades for this account and market using OrderApi
            if not self.order_api:
                self.logger.debug("[LIGHTER] OrderApi not available for trade history")
                return None
            
            trades_response = await self.order_api.trades(
                account_index=account_index,
                market_id=market_id,
                sort_by='timestamp',  # Sort by time
                sort_dir='desc',  # Descending (newest first) 
                limit=100,  # Last 100 trades should cover most position opens
                auth=auth_token,
                authorization=auth_token,
                _request_timeout=10,
            )
            
            if not trades_response or not hasattr(trades_response, 'trades'):
                return None
            
            trades = trades_response.trades
            if not trades:
                return None
            
            # Reverse to get chronological order (oldest first)
            trades = list(reversed(trades))
            
            # Track running position to find when current position started
            position_start_time = None
            
            for trade in trades:
                try:
                    # Get timestamp (Lighter uses seconds for timestamp)
                    trade_timestamp = getattr(trade, 'timestamp', None)
                    
                    # Check if position sign changed, which marks a new position
                    taker_sign_changed = getattr(trade, 'taker_position_sign_changed', False)
                    maker_sign_changed = getattr(trade, 'maker_position_sign_changed', False)
                    
                    # If position sign changed, this marks a new position
                    if taker_sign_changed or maker_sign_changed:
                        position_start_time = trade_timestamp
                    
                except Exception as exc:
                    self.logger.debug(f"[LIGHTER] Error processing trade: {exc}")
                    continue
            
            return position_start_time
            
        except Exception as exc:
            self.logger.debug(
                f"[LIGHTER] Failed to determine position open time for market_id={market_id}: {exc}"
            )
            return None
    
    async def get_cumulative_funding(
        self,
        market_id: int,
        side: Optional[str] = None,
        quantity: Optional[Decimal] = None,
        position_opened_at: Optional[float] = None,
    ) -> Optional[Decimal]:
        """
        Fetch cumulative funding fees for the CURRENT position only (not historical positions).
        
        Args:
            market_id: The market ID for the position
            side: Position side ('long' or 'short'), optional filter
            quantity: Current position quantity (used to determine when position was opened if position_opened_at not provided)
            position_opened_at: Optional Unix timestamp (seconds) when position was opened. 
                              If provided, skips expensive trades() API call (300 weight).
                              Should be from FundingArbPosition.opened_at.timestamp().
            
        Returns:
            Cumulative funding fees as Decimal, None if unavailable
        """
        if not self.account_api:
            return None
        
        account_index = getattr(self.config, "account_index", None)
        if account_index is None:
            self.logger.debug("[LIGHTER] No account_index configured, cannot fetch funding")
            return None
        
        # Generate auth token for API call
        if not self.lighter_client:
            self.logger.debug("[LIGHTER] No lighter_client available for auth")
            return None
        
        try:
            auth_token, error = self.lighter_client.create_auth_token_with_expiry()
            if error:
                self.logger.debug(f"[LIGHTER] Error creating auth token for funding: {error}")
                return None
        except Exception as exc:
            self.logger.debug(f"[LIGHTER] Failed to create auth token: {exc}")
            return None
        
        # Try to determine when the current position was opened
        # CRITICAL: Without position_start_time, we cannot filter historical funding correctly
        # and would incorrectly sum ALL funding history (including previous positions)
        position_start_time = None
        
        # If position_opened_at is provided (e.g., from our database), use it directly
        # This avoids expensive trades() API call (300 weight)
        if position_opened_at is not None:
            position_start_time = position_opened_at
            self.logger.debug(
                f"[LIGHTER] Using provided position_opened_at timestamp {position_start_time} "
                "(skipping trades API call)"
            )
        elif quantity is not None and quantity != Decimal("0"):
            # Fallback: Query trades API to find when position sign changed
            # This is expensive (300 weight) but necessary if we don't have opened_at from database
            position_start_time = await self.get_position_open_time(market_id, quantity)
            if position_start_time:
                self.logger.debug(
                    f"[LIGHTER] Filtering funding to only after position opened at timestamp {position_start_time} "
                    "(from trades API)"
                )
            else:
                # Position start time detection failed - cannot safely filter funding
                # Return None rather than summing all history (which would be incorrect)
                self.logger.warning(
                    f"[LIGHTER] Cannot determine position start time for market_id={market_id}. "
                    "Cannot safely filter funding history. Returning None to avoid incorrect data."
                )
                return None
        else:
            # No quantity provided - cannot determine position start time
            # Return None rather than summing all history (which would be incorrect)
            self.logger.warning(
                f"[LIGHTER] No quantity provided for market_id={market_id}. "
                "Cannot determine position start time. Cannot safely filter funding history. "
                "Returning None to avoid incorrect data."
            )
            return None
        
        try:
            # Fetch position funding history with authentication
            # Lighter requires BOTH auth (query param) and authorization (header) for main accounts
            response = await self.account_api.position_funding(
                account_index=account_index,
                market_id=market_id,
                limit=50,  # Reduced from 100: funding settles every 1h on Lighter, so 50 records = ~50 hours
                side=side if side else 'all',
                auth=auth_token,  # Query parameter
                authorization=auth_token,  # Header parameter - required for main accounts
                _request_timeout=10,
            )
            
            if not response or not hasattr(response, 'position_fundings'):
                return None
            
            fundings = response.position_fundings
            if not fundings:
                return Decimal("0")  # No funding yet for this position
            
            # We normalize ALL timestamps to seconds for accurate comparison.
            # The check `> 10**12` detects milliseconds (Unix timestamp > 1 trillion = milliseconds)
            
            def normalize_to_seconds(timestamp: float) -> float:
                """Normalize timestamp to seconds (Unix timestamp format)."""
                if timestamp > 10**12:  # If > 1 trillion, it's milliseconds
                    return timestamp / 1000.0
                return timestamp
            
            position_start_seconds = normalize_to_seconds(position_start_time)
            
            # Log conversion only if it was actually converted (helps debug timestamp format issues)
            if position_start_time != position_start_seconds:
                self.logger.debug(
                    f"[LIGHTER] Converted position_start_time from milliseconds ({position_start_time}) "
                    f"to seconds ({position_start_seconds})"
                )
            
            cumulative = Decimal("0")
            filtered_count = 0
            for funding in fundings:
                try:
                    # Only count funding after position opened (position_start_time is guaranteed to be set)
                    funding_timestamp = getattr(funding, 'timestamp', None)
                    
                    if funding_timestamp is None:
                        continue
                    
                    # Normalize funding timestamp to seconds (might be milliseconds or seconds)
                    # Use same normalization function for consistency
                    funding_seconds = normalize_to_seconds(funding_timestamp)
                    
                    if funding_seconds < position_start_seconds:
                        filtered_count += 1
                        continue
                    
                    change = Decimal(str(funding.change))
                    cumulative += change
                except Exception as exc:
                    self.logger.debug(f"[LIGHTER] Failed to parse funding change: {exc}")
                    continue
            
            self.logger.info(
                f"[LIGHTER] Funding for current position (market_id={market_id}): ${cumulative:.4f} "
                f"(from {len(fundings)} records, filtered {filtered_count} before position opened at {position_start_time})"
            )
            
            return cumulative if cumulative != Decimal("0") else None
            
        except Exception as exc:
            self.logger.debug(
                f"[LIGHTER] Error fetching funding for market_id={market_id}: {exc}"
            )
            return None
    
    async def get_position_snapshot(
        self, 
        symbol: str,
        position_opened_at: Optional[float] = None,
    ) -> Optional[ExchangePositionSnapshot]:
        """Return the latest cached position snapshot for a symbol, falling back to REST if required."""
        normalized_symbol = self.normalize_symbol(symbol).upper()

        snapshot = await self.snapshot_from_cache(normalized_symbol, position_opened_at=position_opened_at)
        if snapshot:
            return snapshot

        try:
            await asyncio.wait_for(self.positions_ready.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            pass

        snapshot = await self.snapshot_from_cache(normalized_symbol, position_opened_at=position_opened_at)
        if snapshot:
            return snapshot

        await self.refresh_positions_via_rest()
        return await self.snapshot_from_cache(normalized_symbol, position_opened_at=position_opened_at)
    
    async def get_account_pnl(self) -> Optional[Decimal]:
        """Get account P&L using Lighter SDK."""
        async with self.positions_lock:
            raw_positions = list(self.raw_positions.values())

        if not raw_positions:
            await self.refresh_positions_via_rest()
            async with self.positions_lock:
                raw_positions = list(self.raw_positions.values())

        total_pnl = Decimal("0")
        for raw in raw_positions:
            unrealized = raw.get("unrealized_pnl")
            value = decimal_or_none(unrealized)
            if value is not None:
                total_pnl += value

        return total_pnl

