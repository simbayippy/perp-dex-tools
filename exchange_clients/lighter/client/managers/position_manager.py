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
        self.normalize_symbol = normalize_symbol_fn or (lambda s: s.upper())
    
    def get_live_mark_price(self, normalized_symbol: str) -> Optional[Decimal]:
        """
        Return a real-time mark price using the active order-book feed.

        Lighter's account positions stream is event-driven, so the cached mark price only
        changes when the exchange pushes a fresh position update. We reuse the best bid/ask
        tracked by the WebSocket to keep the mark current without hitting the heavy REST
        endpoint.
        """
        if self.ws_manager is None:
            self.logger.warning("[LIGHTER] Skipping live mark enrichment – websocket manager unavailable")
            return None

        config_symbol = getattr(self.config, "ticker", None)
        if config_symbol:
            active_symbol = self.normalize_symbol(str(config_symbol)).upper()
            if normalized_symbol != active_symbol:
                return None

        midpoint_candidates: List[Decimal] = []
        for price in (self.ws_manager.best_bid, self.ws_manager.best_ask):
            if price is None:
                continue
            try:
                midpoint_candidates.append(Decimal(str(price)))
            except (TypeError, ValueError):
                continue

        if not midpoint_candidates:
            return None

        if len(midpoint_candidates) == 2:
            return (midpoint_candidates[0] + midpoint_candidates[1]) / Decimal("2")

        return midpoint_candidates[0]
    
    async def enrich_snapshot_with_live_market_data(
        self,
        normalized_symbol: str,
        raw: Dict[str, Any],
        snapshot: ExchangePositionSnapshot,
    ) -> None:
        """
        Refresh mark price, exposure, and unrealized PnL using the latest order-book data.
        """
        live_mark = self.get_live_mark_price(normalized_symbol)
        if live_mark is None:
            return

        snapshot.mark_price = live_mark

        quantity = snapshot.quantity or Decimal("0")
        quantity_abs = quantity.copy_abs()
        if quantity_abs != 0:
            snapshot.exposure_usd = quantity_abs * live_mark

        entry_price = snapshot.entry_price
        if entry_price is not None and quantity != 0:
            try:
                snapshot.unrealized_pnl = (live_mark - entry_price) * quantity
            except Exception:
                pass

        async with self.positions_lock:
            cached = self.raw_positions.get(normalized_symbol)
            if cached is None:
                return

            cached["mark_price"] = str(live_mark)
            if snapshot.exposure_usd is not None:
                cached["position_value"] = str(snapshot.exposure_usd)
            if snapshot.unrealized_pnl is not None:
                cached["unrealized_pnl"] = str(snapshot.unrealized_pnl)
    
    async def snapshot_from_cache(self, normalized_symbol: str) -> Optional[ExchangePositionSnapshot]:
        """Retrieve a cached snapshot if available, optionally enriching with funding data."""
        async with self.positions_lock:
            raw = self.raw_positions.get(normalized_symbol)
            funding_in_cache = None
            total_funding_ws = None
            if raw is not None:
                # raw from websocket data
                funding_in_cache = raw.get("funding_accrued")
                total_funding_ws = raw.get("total_funding_paid_out")
                
                # CRITICAL: Always check websocket total_funding_paid_out, even if cached funding exists
                # Websocket is the source of truth for real-time funding updates
                if total_funding_ws is not None:
                    ws_funding = decimal_or_none(total_funding_ws)
                    # Update cache if websocket has funding (even if cached was 0)
                    if ws_funding is not None:
                        raw["funding_accrued"] = ws_funding
                        funding_in_cache = ws_funding
                        self.logger.debug(
                            f"[LIGHTER] Updated funding from websocket for {normalized_symbol}: {ws_funding}"
                        )
                
                # If websocket has total_funding_paid_out but it wasn't mapped to funding_accrued yet, map it now
                elif funding_in_cache is None and total_funding_ws is not None:
                    raw["funding_accrued"] = total_funding_ws
                    funding_in_cache = total_funding_ws
                    self.logger.debug(
                        f"[LIGHTER] Mapped total_funding_paid_out → funding_accrued for {normalized_symbol}: {funding_in_cache}"
                    )
                
                # Determine if we need to query REST for funding:
                # - If funding is None (never checked) → query REST
                # - If funding is 0 but websocket doesn't have total_funding_paid_out → query REST
                #   (websocket might not have sent update yet, REST account() API should have latest)
                needs_funding = (
                    funding_in_cache is None  # Never checked
                    or (
                        funding_in_cache == Decimal("0")  # Cached as 0
                        and total_funding_ws is None  # But websocket doesn't have it
                    )
                )
                
                if needs_funding:
                    self.logger.debug(
                        f"[LIGHTER] Funding missing for {normalized_symbol}: "
                        f"funding_accrued={funding_in_cache}, "
                        f"total_funding_paid_out={total_funding_ws}"
                    )

        if raw is None:
            return None

        snapshot = build_snapshot_from_raw(normalized_symbol, raw)
        if snapshot is None:
            return None

        await self.enrich_snapshot_with_live_market_data(normalized_symbol, raw, snapshot)

        if (
            needs_funding  # True if funding is None OR cached as 0 but websocket doesn't have it
            and snapshot.side
            and snapshot.quantity != 0
            and raw.get("market_id") is not None
        ):
            # Skip REST account() API - it returns ALL symbols and total_funding_paid_out is often None
            # Go straight to position_funding() API which is reliable
            funding = None
            
            # Check if we have cached funding that's still fresh (< 1 hour old)
            # Funding settles every 1 hour on Lighter, so 1 hour cache is safe
            async with self.positions_lock:
                cached_raw = self.raw_positions.get(normalized_symbol)
                funding_cache_timestamp = cached_raw.get("funding_cache_timestamp") if cached_raw else None
                cached_funding_value = cached_raw.get("funding_accrued") if cached_raw else None
                
                cache_age_seconds = None
                if funding_cache_timestamp:
                    cache_age_seconds = time.time() - funding_cache_timestamp
                    cache_age_hours = cache_age_seconds / 3600
                    
                    # Use cached funding if less than 1 hour old
                    if cache_age_seconds < 3600 and cached_funding_value is not None:
                        funding = decimal_or_none(cached_funding_value)
                        self.logger.debug(
                            f"[LIGHTER] Using cached funding for {normalized_symbol}: {funding} "
                            f"(cached {cache_age_hours:.2f} hours ago)"
                        )
            
            # Query position_funding() API if no fresh cache
            if funding is None:
                try:
                    self.logger.info(
                        f"[LIGHTER] Querying position_funding() API for {normalized_symbol} "
                        f"({'cache expired' if cache_age_seconds else 'no cache'}, "
                        f"{'cache age: ' + f'{cache_age_seconds/3600:.2f}h' if cache_age_seconds else ''})"
                    )
                    funding = await self.get_cumulative_funding(
                        raw.get("market_id"),
                        snapshot.side,
                        quantity=snapshot.quantity,
                    )
                    # get_cumulative_funding() returns None when cumulative is 0
                    # Convert None → Decimal("0") to mark as "checked"
                    if funding is None:
                        funding = Decimal("0")
                    
                    # Cache the funding value with timestamp
                    async with self.positions_lock:
                        cached = self.raw_positions.get(normalized_symbol)
                        if cached is not None:
                            cached["funding_accrued"] = funding
                            cached["funding_cache_timestamp"] = time.time()
                            self.logger.debug(
                                f"[LIGHTER] Cached funding for {normalized_symbol}: {funding} "
                                "(will refresh after 1 hour or on websocket update)"
                            )
                except Exception as exc:
                    self.logger.debug(f"[LIGHTER] Funding lookup failed for {normalized_symbol}: {exc}")
                    # Don't cache on error - might be transient, allow retry next cycle
                    funding = None

            # Set funding on snapshot (may be from cache or fresh query)
            snapshot.funding_accrued = funding
        else:
            snapshot.funding_accrued = snapshot.funding_accrued or decimal_or_none(raw.get("funding_accrued"))

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
            self.logger.info(f"[LIGHTER] Account data: {account_data}")
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
                    
                    # Map funding field from REST account() API (position-specific, filtered for current position)
                    # total_funding_paid_out is always present in account() response (may be "0" string)
                    if has_position and is_toshi:
                        # Log ALL attributes of the position object to see what's available (only for TOSHI)
                        all_attrs = [a for a in dir(pos) if not a.startswith('_')]
                        funding_related = [a for a in all_attrs if 'funding' in a.lower() or 'paid' in a.lower()]
                        self.logger.info(
                            f"[LIGHTER] Position object attributes for {pos.symbol}: "
                            f"funding-related={funding_related}, "
                            f"all_attrs_count={len(all_attrs)}"
                        )
                        # Try to get funding value using different possible attribute names
                        for attr in funding_related:
                            try:
                                value = getattr(pos, attr, None)
                                self.logger.info(
                                    f"[LIGHTER]   {attr} = {repr(value)} (type={type(value)})"
                                )
                            except Exception:
                                pass
                    
                    if hasattr(pos, 'total_funding_paid_out'):
                        raw_funding_value = pos.total_funding_paid_out
                        funding_str = str(raw_funding_value).strip() if raw_funding_value is not None else ""
                        
                        # Debug: Log raw value for ALL positions (especially those with positions)
                        # But only show detailed logs for TOSHI to reduce noise
                        if has_position and is_toshi:
                            self.logger.info(
                                f"[LIGHTER] REST account() API raw total_funding_paid_out for {pos.symbol} "
                                f"(qty={position_qty}): type={type(raw_funding_value)}, "
                                f"value={repr(raw_funding_value)}, str={repr(funding_str)}"
                            )
                        elif has_position:
                            # Other positions: minimal log
                            self.logger.debug(
                                f"[LIGHTER] REST account() API total_funding_paid_out for {pos.symbol}: {funding_str or 'None'}"
                            )
                        else:
                            # No position: skip logging to reduce noise
                            pass
                        
                        # Handle the optional field:
                        # - If None/empty: API didn't include the field (optional) → leave as None to trigger fallback
                        # - If "0": API included field with 0 value → use Decimal("0")
                        # - If non-zero string: Parse and use
                        if funding_str and funding_str.lower() not in ('none', 'null', ''):
                            try:
                                parsed_funding = Decimal(funding_str)
                                pos_dict['funding_accrued'] = parsed_funding
                                if has_position and is_toshi:
                                    self.logger.info(
                                        f"[LIGHTER] Parsed funding for {pos.symbol} (qty={position_qty}): {parsed_funding}"
                                    )
                                # Skip logging for other positions
                            except (ValueError, InvalidOperation) as exc:
                                # If parsing fails, treat as missing (None) to trigger fallback
                                if is_toshi:
                                    self.logger.warning(
                                        f"[LIGHTER] Failed to parse total_funding_paid_out '{funding_str}' "
                                        f"for {pos.symbol}: {exc}. Will fall back to position_funding() API."
                                    )
                                pos_dict['funding_accrued'] = None  # Leave as None to trigger fallback
                        else:
                            # Empty/None means API didn't include the optional field
                            # Leave as None (don't set to 0) so we can fall back to position_funding()
                            if has_position and is_toshi:
                                self.logger.info(
                                    f"[LIGHTER] total_funding_paid_out is None/empty for {pos.symbol} "
                                    f"(qty={position_qty}) - API didn't include optional field. "
                                    "Will fall back to position_funding() API."
                                )
                            # Don't set funding_accrued - leave it missing so fallback triggers
                            pos_dict['funding_accrued'] = None
                    else:
                        # Attribute doesn't exist - this shouldn't happen per API docs
                        if has_position and is_toshi:
                            self.logger.warning(
                                f"[LIGHTER] Position object for {pos.symbol} missing total_funding_paid_out attribute. "
                                f"Available attributes: {', '.join([a for a in dir(pos) if not a.startswith('_')])}"
                            )
                        # Skip logging for other positions
                        pos_dict['funding_accrued'] = Decimal("0")
                    
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
    ) -> Optional[Decimal]:
        """
        Fetch cumulative funding fees for the CURRENT position only (not historical positions).
        
        ⚠️ WARNING: This uses position_funding() API which returns ALL funding history for the account/market.
        It attempts to filter by position_start_time, but this is fragile and may include historical funding
        if position_start_time detection fails.
        
        PREFERRED: Use REST account() API's total_funding_paid_out field instead, which is already
        filtered for the current position. This method should only be used as a last resort.
        
        Args:
            market_id: The market ID for the position
            side: Position side ('long' or 'short'), optional filter
            quantity: Current position quantity (used to determine when position was opened)
            
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
        if quantity is not None and quantity != Decimal("0"):
            position_start_time = await self.get_position_open_time(market_id, quantity)
            if position_start_time:
                self.logger.debug(
                    f"[LIGHTER] Filtering funding to only after position opened at timestamp {position_start_time}"
                )
            else:
                # Position start time detection failed - cannot safely filter funding
                # Return None rather than summing all history (which would be incorrect)
                self.logger.warning(
                    f"[LIGHTER] Cannot determine position start time for market_id={market_id}. "
                    "Cannot safely filter funding history. Returning None to avoid incorrect data. "
                    "REST account() API should have provided funding."
                )
                return None
        else:
            # No quantity provided - cannot determine position start time
            # Return None rather than summing all history (which would be incorrect)
            self.logger.warning(
                f"[LIGHTER] No quantity provided for market_id={market_id}. "
                "Cannot determine position start time. Cannot safely filter funding history. "
                "Returning None to avoid incorrect data. REST account() API should have provided funding."
            )
            return None
        
        try:
            # Fetch position funding history with authentication
            # Lighter requires BOTH auth (query param) and authorization (header) for main accounts
            response = await self.account_api.position_funding(
                account_index=account_index,
                market_id=market_id,
                limit=30,  # Reduced from 100: funding settles every 1h on Lighter, so 30 records = ~30 hours
                # Since we filter by position_start_time, we don't need all historical records
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
            
            # Sum up funding 'change' values for this position only
            # NOTE: position_start_time is guaranteed to be set at this point (we return None if it's missing)
            # Normalize timestamps to seconds (Unix timestamp) for comparison
            # position_start_time might be in milliseconds (13 digits) or seconds (10 digits)
            position_start_seconds = position_start_time
            if position_start_time > 10**12:  # If > 1 trillion, it's milliseconds
                position_start_seconds = position_start_time // 1000
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
                    funding_seconds = funding_timestamp
                    if funding_timestamp > 10**12:  # If > 1 trillion, it's milliseconds
                        funding_seconds = funding_timestamp // 1000
                    
                    # Debug: Log first few funding records to understand timestamp format
                    if filtered_count < 3:
                        self.logger.info(
                            f"[LIGHTER] Funding record {filtered_count + 1}: "
                            f"timestamp={funding_timestamp} ({'ms' if funding_timestamp > 10**12 else 's'}) → {funding_seconds}s, "
                            f"change={getattr(funding, 'change', None)}, "
                            f"position_start={position_start_time} ({'ms' if position_start_time > 10**12 else 's'}) → {position_start_seconds}s, "
                            f"comparison: {funding_seconds} < {position_start_seconds} = {funding_seconds < position_start_seconds}"
                        )
                    
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
    
    async def get_position_snapshot(self, symbol: str) -> Optional[ExchangePositionSnapshot]:
        """Return the latest cached position snapshot for a symbol, falling back to REST if required."""
        normalized_symbol = self.normalize_symbol(symbol).upper()

        snapshot = await self.snapshot_from_cache(normalized_symbol)
        if snapshot:
            return snapshot

        try:
            await asyncio.wait_for(self.positions_ready.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            pass

        snapshot = await self.snapshot_from_cache(normalized_symbol)
        if snapshot:
            return snapshot

        await self.refresh_positions_via_rest()
        return await self.snapshot_from_cache(normalized_symbol)
    
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

