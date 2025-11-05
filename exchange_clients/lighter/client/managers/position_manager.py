"""
Position manager module for Lighter client.

Handles position tracking, snapshots, funding calculations, and enrichment.
"""

import asyncio
from decimal import Decimal
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
            needs_funding = False
            if raw is not None:
                # raw from websocket data
                needs_funding = raw.get("funding_accrued") is None

        if raw is None:
            return None

        snapshot = build_snapshot_from_raw(normalized_symbol, raw)
        if snapshot is None:
            return None

        await self.enrich_snapshot_with_live_market_data(normalized_symbol, raw, snapshot)

        if (
            needs_funding  # ⚠️ Only True if funding_accrued is None
            and snapshot.side
            and snapshot.quantity != 0
            and raw.get("market_id") is not None
        ):
            try:
                funding = await self.get_cumulative_funding(
                    raw.get("market_id"),
                    snapshot.side,
                    quantity=snapshot.quantity,
                )
            except Exception as exc:
                self.logger.debug(f"[LIGHTER] Funding lookup failed for {normalized_symbol}: {exc}")
                funding = None

            snapshot.funding_accrued = funding
            if funding is not None:
                async with self.positions_lock:
                    cached = self.raw_positions.get(normalized_symbol)
                    if cached is not None:
                        cached["funding_accrued"] = funding
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
            if account_data and account_data.accounts:
                positions = []
                for pos in account_data.accounts[0].positions:
                    pos_dict = {
                        'market_id': pos.market_id,
                        'symbol': pos.symbol,
                        'position': Decimal(pos.position),
                        'avg_entry_price': Decimal(pos.avg_entry_price),
                        'position_value': Decimal(pos.position_value),
                        'unrealized_pnl': Decimal(pos.unrealized_pnl),
                        'realized_pnl': Decimal(pos.realized_pnl),
                        'liquidation_price': Decimal(pos.liquidation_price),
                        'allocated_margin': Decimal(pos.allocated_margin),
                        'sign': pos.sign  # 1 for Long, -1 for Short
                    }
                    
                    # Map funding field (same as WebSocket) to avoid REST funding lookup
                    # Note: total_funding_paid_out is optional (omitted when empty/None)
                    if hasattr(pos, 'total_funding_paid_out') and pos.total_funding_paid_out is not None:
                        pos_dict['funding_accrued'] = Decimal(pos.total_funding_paid_out)
                    
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
        position_start_time = None
        if quantity is not None and quantity != Decimal("0"):
            position_start_time = await self.get_position_open_time(market_id, quantity)
            if position_start_time:
                self.logger.debug(
                    f"[LIGHTER] Filtering funding to only after position opened at timestamp {position_start_time}"
                )
        
        try:
            # Fetch position funding history with authentication
            # Lighter requires BOTH auth (query param) and authorization (header) for main accounts
            response = await self.account_api.position_funding(
                account_index=account_index,
                market_id=market_id,
                limit=100,  # Get recent funding payments
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
            cumulative = Decimal("0")
            for funding in fundings:
                try:
                    # If we have position start time, only count funding after position opened
                    if position_start_time:
                        funding_timestamp = getattr(funding, 'timestamp', None)
                        if funding_timestamp and funding_timestamp < position_start_time:
                            continue
                    
                    change = Decimal(str(funding.change))
                    cumulative += change
                except Exception as exc:
                    self.logger.debug(f"[LIGHTER] Failed to parse funding change: {exc}")
                    continue
            
            self.logger.debug(
                f"[LIGHTER] Funding for current position (market_id={market_id}): ${cumulative:.4f} "
                f"(from {len(fundings)} records{' after position opened' if position_start_time else ' (all history)'})"
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

